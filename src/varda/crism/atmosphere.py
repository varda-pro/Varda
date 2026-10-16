"""Atmospheric correction (spec S4). Part A: the per-column fit and the
objective choice of transmission. Part B (``fitPixels``,
``fitElevationModel``, ``correct``): the per-pixel scale β with T and δ
fixed, its elevation prior ln β ≈ a − z/H, and the correction with its σ.

Over the fit window W = 1850–2200 nm, in log space,

    ln R(λ) = Σ_k c_k P_k(u) + β · ln T_x(λ + δ_x),

with P_k the Legendre polynomials of u (W mapped onto [−1, 1]), T_x the
candidate transmission of column x at the column's own wavelengths (an AT CDR
is defined on detector elements, so no resampling), β the path scale and δ_x
the column's wavelength shift. ln T_x(λ + δ) is ln T interpolated linearly
along the column's wavelengths. The fit is weighted least squares with
σ_lnR = σ_R / R.

δ enters through its Taylor expansion, ln T(λ + δ) ≈ ln T(λ) + δ ∂ln T/∂λ
(∂ln T/∂λ the central difference along the sorted wavelengths), so each solve
is linear in (c, β, η = β·δ) and δ = η/β. One step leaves a bias that grows
with δ (on the synthetic cube, residuals twice the noise and β off by about
12σ at 0.8 nm), so the step is repeated about the shift found so far,
re-interpolating ln T, until it changes by less than 1e-4 nm (Gauss–Newton;
a few steps).

Bands inside the hydration-band core 1880–1945 nm are left out of the fit
(weight 0): hydrated minerals and adsorbed water put a band there that the
cubic continuum cannot follow and that would otherwise bias β and δ. The
continuum still spans the whole window.

A candidate's score is the median over valid columns of the σ-weighted RMS
residual inside the score window 1947–2066 nm (the TER window): the fit's own
objective restricted to that window (ledger A3). Candidates are scored after
the shift fit (M10).
"""

import math
from collections.abc import Sequence

import numpy as np
from attrs import frozen
from numpy.polynomial import legendre
from scipy import ndimage

from varda.crism.calibration import Transmission

FIT_WINDOW_NM = (1850.0, 2200.0)
SCORE_WINDOW_NM = (1947.0, 2066.0)
HYDRATION_MASK_NM = (1880.0, 1945.0)
CONTINUUM_DEGREE = 3

_MIN_FIT_BANDS = 24  # of the ~43 fitted bands (53 in the window, 10 masked)
_MIN_COMPLETE_LINES = 8
_IMPUTE_RANK = 2
_IMPUTE_ITERATIONS = 10
_MIN_SCORE_BANDS = 8  # of the ~18 bands in the score window
_MAX_SHIFT_NM = 5.0  # beyond this a column's fit is not trusted (bands are 6.6 nm)
_SHIFT_TOLERANCE_NM = 1e-4
_MAX_SHIFT_STEPS = 10
_TUKEY_C = 4.685
_MAD_TO_SIGMA = 1.4826
_IRLS_ITERATIONS = 50


@frozen(eq=False)
class ColumnFit:
    """The fit of each spectrum (one per column, or any ``(n, b)`` stack).

    ``residualRms`` is the σ-weighted RMS of ``residuals`` over the score
    window. ``betaSigma`` and ``shiftSigma`` are the formal (noise-only)
    σ; where the model does not reach the noise (``reducedChiSquare`` > 1,
    e.g. surface structure in the window), σ · √max(1, χ²_ν) is the honest
    uncertainty. ``residuals`` is ln R minus the model, NaN outside the fit window
    and where a band was not used (the hydration mask among them). Invalid
    fits (too few bands, β ≤ 0, a shift beyond 5 nm, or a last Gauss–Newton
    step above the tolerance) are NaN everywhere but in a fixed ``shiftNm``."""

    beta: np.ndarray  # (n,)
    betaSigma: np.ndarray  # (n,)
    shiftNm: np.ndarray  # (n,) the fitted δ, else the fixed one as given (0 by default)
    shiftSigma: np.ndarray  # (n,) 0 when δ was not fitted
    residualRms: np.ndarray  # (n,)
    reducedChiSquare: np.ndarray  # (n,) over the fitted bands
    residuals: np.ndarray  # (n, b)
    valid: np.ndarray  # (n,) bool


@frozen(eq=False)
class SelectionResult:
    """The chosen transmission and every candidate's score, keyed by
    ``Transmission.key``. ``margin`` is the runner-up's score minus the best,
    ``relativeMargin`` that over the best score; both are ``math.inf`` with
    one candidate, so a writer must serialize inf (e.g. as null or "inf").
    ``winFraction`` is the fraction of columns valid for both where the
    best's own column score is below the runner-up's (1.0 with one
    candidate); a margin near 0 or a fraction near 0.5 flags a tie. ``fit``
    is the chosen candidate refitted with the smoothed shift held fixed."""

    chosen: Transmission
    scores: dict[str, float]
    margin: float
    relativeMargin: float
    winFraction: float
    fit: ColumnFit


def _inside(wavelengths: np.ndarray, window: tuple[float, float]) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return (wavelengths >= window[0]) & (wavelengths <= window[1])


def _lowRankMean(logR: np.ndarray, usable: np.ndarray) -> np.ndarray:
    """The mean over lines ``(s, b)`` of ``logR`` ``(l, s, b)`` with its
    missing samples filled by an EM low-rank reconstruction per column: each
    line differs from the column mean by about an offset (albedo) plus a
    multiple of the CO2 shape (its own β), which a rank-2 model captures, so
    every band then averages the same lines."""
    observed = np.moveaxis(usable, 1, 0)  # (s, l, b)
    values = np.moveaxis(np.where(usable, logR, 0.0), 1, 0)
    count = observed.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(count > 0, values.sum(axis=1, keepdims=True) / count, 0.0)
    filled = np.where(observed, values, mean)
    for _ in range(_IMPUTE_ITERATIONS):
        mean = filled.mean(axis=1, keepdims=True)
        u, singular, vt = np.linalg.svd(filled - mean, full_matrices=False)
        rank = slice(0, _IMPUTE_RANK)
        low = (u[..., rank] * singular[:, None, rank]) @ vt[:, rank]
        filled = np.where(observed, values, mean + low)
    return filled.mean(axis=1)


def columnSpectra(
    cube: np.ndarray, sigma: np.ndarray, wavelengths: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """The per-column geometric mean over lines of ``cube`` ``(l, s, b)``,
    exp(mean ln R), and its σ, R̄ · √(Σ σ_lnR²) / n over the n finite
    samples (NaN where a band has none); ``wavelengths`` is ``(s, b)``.

    The mean of ln R keeps the column spectrum on the model: each line is
    ln S_t + β_t · ln T(λ + δ), so the mean of a fixed set of lines is the
    same model with their mean β. A median is not linear: it takes each band
    from a different line, and on the synthetic cube (β ±14 % down a column)
    that leaves residuals 1.4–1.8 times the noise and column β off by a
    median 5–11σ (up to 37σ).

    So that every band averages the same lines, a column uses only its
    complete lines: those finite in every fit-window band that at least half
    of the column's lines have. When fewer than 8 lines are complete (e.g.
    scattered invalid samples), every finite sample counts instead, and the
    missing ones in those fit-window bands are filled by a rank-2 model of
    the column (``_lowRankMean``) before the mean: a plain per-band mean
    over different lines inflates the scatter of β by 1.5–1.6× at 10%
    invalid samples. Bands outside the fit window are plain per-band
    means."""
    with np.errstate(invalid="ignore", divide="ignore"):
        logR = np.log(cube)
        relative = sigma / cube
    usable = np.isfinite(logR) & np.isfinite(relative)
    live = _inside(wavelengths, FIT_WINDOW_NM) & (
        usable.sum(axis=0) >= 0.5 * cube.shape[0]
    )
    complete = (usable | ~live[None]).all(axis=2)  # (l, s)
    enough = complete.sum(axis=0) >= _MIN_COMPLETE_LINES
    usable &= complete[..., None] | ~enough[None, :, None]
    count = usable.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        logMean = np.where(usable, logR, 0.0).sum(axis=0) / count
        spread = np.sqrt((np.where(usable, relative, 0.0) ** 2).sum(axis=0)) / count
    sparse = ~enough & live.any(axis=1)
    if sparse.any():
        bands = live[sparse].any(axis=0)
        block = np.ix_(sparse, bands)
        filled = _lowRankMean(
            logR[:, sparse][..., bands], usable[:, sparse][..., bands]
        )
        logMean[block] = np.where(live[block], filled, logMean[block])
    spectra = np.exp(logMean)
    empty = count == 0
    return np.where(empty, np.nan, spectra), np.where(empty, np.nan, spectra * spread)


# --- ln T along each row's own wavelengths -----------------------------------------


@frozen(eq=False)
class _Packed:
    """Each row's valid (λ, ln T) samples sorted by λ and packed to the front
    (NaN after ``count``); ``order`` maps them back to the band order."""

    order: np.ndarray  # (n, b)
    wavelengths: np.ndarray  # (n, b)
    logT: np.ndarray  # (n, b)
    count: np.ndarray  # (n, 1)

    @classmethod
    def of(cls, wavelengths: np.ndarray, transmission: np.ndarray) -> "_Packed":
        wavelengths = np.asarray(wavelengths, dtype=np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            logT = np.log(np.asarray(transmission, dtype=np.float64))
        usable = np.isfinite(wavelengths) & np.isfinite(logT)
        order = np.argsort(np.where(usable, wavelengths, np.inf), axis=1, kind="stable")
        return cls(
            order,
            np.take_along_axis(np.where(usable, wavelengths, np.nan), order, 1),
            np.take_along_axis(np.where(usable, logT, np.nan), order, 1),
            usable.sum(axis=1, keepdims=True),
        )

    def _gather(self, values: np.ndarray, index: np.ndarray) -> np.ndarray:
        return np.take_along_axis(values, index, 1)

    def shifted(self, shiftNm: np.ndarray) -> np.ndarray:
        """ln T(λ + δ) at the packed wavelengths: linear between samples and
        held at the end values beyond them (as ``np.interp``). Rows are made
        disjoint by an offset so one ``searchsorted`` serves them all."""
        rows, bands = self.wavelengths.shape
        position = np.arange(bands)[None, :]
        inside = position < self.count
        last = self._gather(self.wavelengths, np.maximum(self.count - 1, 0))
        first = np.nan_to_num(self.wavelengths[:, :1])
        # past ``count``, a 1 nm staircase keeps each row strictly ascending
        key = np.nan_to_num(
            np.where(inside, self.wavelengths, last + 1 + position) - first
        )
        query = np.nan_to_num(self.wavelengths + shiftNm[:, None] - first)
        stride = key.max() + 2 * np.abs(query).max() + 1.0
        offset = stride * np.arange(rows)[:, None]
        found = np.searchsorted(
            (key + offset).ravel(), (query + offset).ravel(), "right"
        )
        index = found.reshape(rows, bands) - 1 - bands * np.arange(rows)[:, None]
        index = np.clip(index, 0, np.maximum(self.count - 2, 0))
        x0, x1 = self._gather(key, index), self._gather(key, index + 1)
        y0, y1 = self._gather(self.logT, index), self._gather(self.logT, index + 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            t = np.clip((query - x0) / (x1 - x0), 0.0, 1.0)
        return np.where(inside & (self.count >= 2), y0 + t * (y1 - y0), np.nan)

    def slope(self, logT: np.ndarray) -> np.ndarray:
        """The central difference of packed ``logT`` along λ, one-sided at the
        ends."""
        position = np.arange(logT.shape[1])[None, :]
        last = np.maximum(self.count - 1, 0)
        below = np.clip(position - 1, 0, last)
        above = np.clip(position + 1, 0, last)
        w = self.wavelengths
        with np.errstate(invalid="ignore", divide="ignore"):
            slope = (self._gather(logT, above) - self._gather(logT, below)) / (
                self._gather(w, above) - self._gather(w, below)
            )
        return np.where((position < self.count) & (self.count >= 2), slope, np.nan)

    def unpack(self, packed: np.ndarray) -> np.ndarray:
        result = np.full(packed.shape, np.nan)
        np.put_along_axis(result, self.order, packed, 1)
        return result


def shiftedLogTransmission(
    wavelengths: np.ndarray, transmission: np.ndarray, shiftNm: np.ndarray
) -> np.ndarray:
    """ln T_x(λ + δ_x) ``(n, b)``: ln T interpolated linearly along each row's
    own wavelengths (held at the end values), NaN where T or λ is invalid.
    This is the atmosphere the fit models; a correction divides by
    exp(β · this)."""
    packed = _Packed.of(wavelengths, transmission)
    return packed.unpack(packed.shifted(np.asarray(shiftNm, dtype=np.float64)))


# --- the fit ------------------------------------------------------------------------


@frozen(eq=False)
class _Normal:
    """The weighted least-squares solution operator of each row."""

    covariance: np.ndarray  # (n, p, p) (AᵀWA)⁻¹
    gain: np.ndarray  # (n, p, b) (AᵀWA)⁻¹AᵀW: coefficients = gain @ target
    valid: np.ndarray  # (n,)


def _normal(design: np.ndarray, weight: np.ndarray) -> _Normal:
    """``design`` ``(n, b, p)`` and ``weight`` ``(n, b)`` (1/σ, a zero weight
    dropping a band). Normal equations, equilibrated by the diagonal; rows
    whose matrix is singular are invalid."""
    used = weight > 0
    weighted = np.where(used[..., None], design, 0.0) * weight[..., None]
    gram = np.einsum("nbj,nbk->njk", weighted, weighted)
    diagonal = np.einsum("njj->nj", gram)
    valid = (diagonal > 0).all(axis=1)
    scale = np.sqrt(np.where(diagonal > 0, diagonal, 1.0))
    outer = scale[:, :, None] * scale[:, None, :]
    equilibrated = np.where(valid[:, None, None], gram / outer, np.eye(gram.shape[1]))
    valid &= np.linalg.cond(equilibrated) < 1e12
    equilibrated[~valid] = np.eye(gram.shape[1])
    covariance = np.linalg.inv(equilibrated) / outer
    gain = covariance @ (weighted * weight[..., None]).swapaxes(1, 2)
    return _Normal(covariance, gain, valid)


@frozen(eq=False)
class _Solution:
    coefficients: np.ndarray  # (n, p)
    covariance: np.ndarray  # (n, p, p)
    residuals: np.ndarray  # (n, b), NaN where not used
    valid: np.ndarray  # (n,)


def _solve(design: np.ndarray, target: np.ndarray, weight: np.ndarray) -> _Solution:
    """Weighted least squares per row: ``design`` ``(n, b, p)``, ``target`` and
    ``weight`` ``(n, b)``, a zero weight dropping a band (``_normal``)."""
    used = weight > 0
    normal = _normal(design, weight)
    covariance, valid = normal.covariance, normal.valid
    coefficients = np.einsum("njb,nb->nj", normal.gain, np.where(used, target, 0.0))
    model = np.einsum(
        "nbj,nj->nb", np.where(used[..., None], design, 0.0), coefficients
    )
    residuals = np.where(used & valid[:, None], target - model, np.nan)
    return _Solution(coefficients, covariance, residuals, valid)


def fitColumns(
    spectra: np.ndarray,
    sigma: np.ndarray,
    wavelengths: np.ndarray,
    transmission: np.ndarray,
    *,
    fitShift: bool = True,
    degree: int = CONTINUUM_DEGREE,
    shiftNm: np.ndarray | None = None,
) -> ColumnFit:
    """Fit every spectrum of ``spectra`` ``(n, b)`` (I/F, with ``sigma``,
    ``wavelengths`` in nm and ``transmission`` of the same shape) over the
    fit window: Legendre P0..P``degree`` and β · ln T(λ + δ). With
    ``fitShift`` δ is fitted per spectrum; otherwise it is ``shiftNm``
    ``(n,)`` (0 by default), held fixed. Batched over spectra."""
    if fitShift and shiftNm is not None:
        raise ValueError("a fixed shiftNm needs fitShift=False")
    spectra = np.asarray(spectra, dtype=np.float64)
    wavelengths = np.asarray(wavelengths, dtype=np.float64)
    rows = spectra.shape[0]
    with np.errstate(invalid="ignore", divide="ignore"):
        logR = np.log(spectra)
        weight = spectra / np.asarray(sigma, dtype=np.float64)  # 1 / σ_lnR
    data = (
        _inside(wavelengths, FIT_WINDOW_NM)
        & ~_inside(wavelengths, HYDRATION_MASK_NM)
        & np.isfinite(logR)
        & np.isfinite(weight)
        & (weight > 0)
    )
    u = np.where(
        data,
        2 * (wavelengths - FIT_WINDOW_NM[0]) / (FIT_WINDOW_NM[1] - FIT_WINDOW_NM[0])
        - 1,
        0,
    )
    continuum = legendre.legvander(u, degree)
    packed = _Packed.of(wavelengths, transmission)
    b = degree + 1  # the index of β
    parameters = b + (2 if fitShift else 1)

    def solveAt(expansion: np.ndarray) -> tuple[_Solution, np.ndarray]:
        logT = packed.shifted(expansion)
        terms = [logT, packed.slope(logT)] if fitShift else [logT]
        terms = [packed.unpack(term) for term in terms]
        used = data & np.isfinite(terms[0]) & np.isfinite(terms[-1])
        design = np.concatenate(
            [continuum] + [np.nan_to_num(term)[..., None] for term in terms], axis=-1
        )
        return _solve(design, logR, np.where(used, weight, 0.0)), used

    def step(solution: _Solution) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            eta = solution.coefficients[:, b + 1] / solution.coefficients[:, b]
        return np.where(solution.valid & np.isfinite(eta), eta, 0.0)

    if fitShift:
        expansion = np.zeros(rows)
        solution, used = solveAt(expansion)
        for _ in range(_MAX_SHIFT_STEPS):
            delta = step(solution)
            if np.abs(delta).max(initial=0.0) < _SHIFT_TOLERANCE_NM:
                break
            expansion = np.clip(expansion + delta, -_MAX_SHIFT_NM, _MAX_SHIFT_NM)
            solution, used = solveAt(expansion)
        last = step(solution)
        shift = expansion + last
        converged = np.abs(last) < _SHIFT_TOLERANCE_NM
    else:
        shift = np.zeros(rows) if shiftNm is None else np.asarray(shiftNm, np.float64)
        solution, used = solveAt(shift)
        converged = np.ones(rows, dtype=bool)

    covariance = solution.covariance
    beta = solution.coefficients[:, b]
    scored = used & _inside(wavelengths, SCORE_WINDOW_NM)
    valid = (
        solution.valid
        & (used.sum(axis=1) >= _MIN_FIT_BANDS)
        & (scored.sum(axis=1) >= _MIN_SCORE_BANDS)
        & (beta > 0)
        & (np.abs(shift) < _MAX_SHIFT_NM)
        & converged
    )
    w2 = np.where(scored, weight, 0.0) ** 2
    with np.errstate(invalid="ignore", divide="ignore"):
        rms = np.sqrt(np.nansum(w2 * solution.residuals**2, axis=1) / w2.sum(axis=1))
        weighted = np.where(used, weight * solution.residuals, 0.0)
        chiSquare = (weighted**2).sum(axis=1) / (used.sum(axis=1) - parameters)
        if fitShift:
            # δ = η / β about the last expansion point; its variance by propagation
            eta = solution.coefficients[:, b + 1]
            delta = eta / beta
            shiftSigma = np.sqrt(
                covariance[:, b + 1, b + 1]
                - 2 * delta * covariance[:, b, b + 1]
                + delta**2 * covariance[:, b, b]
            ) / np.abs(beta)
        else:
            shiftSigma = np.zeros(rows)

    def masked(values: np.ndarray) -> np.ndarray:
        return np.where(valid, values, np.nan)

    return ColumnFit(
        beta=masked(beta),
        betaSigma=masked(np.sqrt(covariance[:, b, b])),
        shiftNm=masked(shift) if fitShift else shift,
        shiftSigma=masked(shiftSigma),
        residualRms=masked(rms),
        reducedChiSquare=masked(chiSquare),
        residuals=np.where(valid[:, None], solution.residuals, np.nan),
        valid=valid,
    )


def smoothShift(shiftNm: np.ndarray, valid: np.ndarray, degree: int = 3) -> np.ndarray:
    """A robust polynomial of ``degree`` in the column index through the valid
    shifts (iteratively reweighted least squares, Tukey biweight with
    c = 4.685 on a MAD scale), evaluated at every column. Zeros when no
    column is valid; the degree drops when fewer than degree + 1 are. Invalid
    columns get the polynomial's value too: between valid columns that is an
    interpolation, but at an invalid edge (e.g. the FRS's empty first and
    last columns) it is an extrapolation of the cubic."""
    columns = shiftNm.shape[0]
    x = np.linspace(-1.0, 1.0, columns)
    keep = valid & np.isfinite(shiftNm)
    count = int(keep.sum())
    if count == 0:
        return np.zeros(columns)
    degree = min(degree, count - 1)
    basis = legendre.legvander(x[keep], degree)
    y = shiftNm[keep]
    weights = np.ones(count)
    coefficients = np.zeros(degree + 1)
    for _ in range(_IRLS_ITERATIONS):
        root = np.sqrt(weights)
        coefficients = np.linalg.lstsq(basis * root[:, None], y * root, rcond=None)[0]
        residual = y - basis @ coefficients
        spread = _MAD_TO_SIGMA * np.median(np.abs(residual))
        if spread == 0:
            break
        z = residual / (_TUKEY_C * spread)
        updated = np.where(np.abs(z) < 1, (1 - z**2) ** 2, 0.0)
        if np.allclose(updated, weights, atol=1e-6):
            break
        weights = updated
    return legendre.legval(x, coefficients)


def selectTransmission(
    spectra: np.ndarray,
    sigma: np.ndarray,
    wavelengths: np.ndarray,
    candidates: Sequence[Transmission],
    *,
    fitShift: bool = True,
) -> SelectionResult:
    """Fit ``spectra`` ``(s, b)`` (column spectra, see ``columnSpectra``) with
    every candidate, score each (the median over valid columns of the
    σ-weighted RMS residual over the score window) and choose the lowest.
    Candidates are told apart by ``Transmission.key``; a repeated key raises.
    The chosen candidate's shifts are smoothed (``smoothShift``) and it is
    refitted with them held fixed."""
    keys = [candidate.key for candidate in candidates]
    if not keys:
        raise ValueError("no transmission candidates")
    if len(set(keys)) != len(keys):
        raise ValueError(f"candidates share a key: {keys}")
    fits = {
        c.key: fitColumns(spectra, sigma, wavelengths, c.values, fitShift=fitShift)
        for c in candidates
    }
    scores = {
        key: float(np.median(fit.residualRms[fit.valid]))
        if fit.valid.any()
        else math.inf
        for key, fit in fits.items()
    }
    ranked = sorted(candidates, key=lambda c: scores[c.key])
    chosen = ranked[0]
    best = scores[chosen.key]
    if not math.isfinite(best):
        raise ValueError("no column could be fitted with any candidate")
    first = fits[chosen.key]
    if len(ranked) > 1:
        rival = fits[ranked[1].key]
        margin = scores[ranked[1].key] - best
        both = first.valid & rival.valid
        winFraction = (
            float(np.mean(first.residualRms[both] < rival.residualRms[both]))
            if both.any()
            else 1.0
        )
    else:
        margin, winFraction = math.inf, 1.0
    relativeMargin = margin / best if best > 0 else math.inf
    shift = (
        smoothShift(first.shiftNm, first.valid)
        if fitShift
        else np.zeros(len(first.valid))
    )
    fit = fitColumns(
        spectra, sigma, wavelengths, chosen.values, fitShift=False, shiftNm=shift
    )
    return SelectionResult(chosen, scores, margin, relativeMargin, winFraction, fit)


# --- part B: the per-pixel scale, the elevation prior and the correction -----------

SCALE_HEIGHT_RANGE_KM = (5.0, 20.0)
_ELEVATION_SIGNIFICANCE = 3.0  # |slope| / σ_slope for H to count as determined
_TUKEY_MEAN_SQUARE = 0.8280730033893929  # E[w z²] / E[w], z ~ N(0, 1), c = 4.685
_BISECTIONS = 50
_COHERENT_BOX = 7  # pixels: the window of the spatially coherent part of ln β
_MIN_TRIPLETS = 10


@frozen(eq=False)
class ElevationModel:
    """ln β = ``lnBeta0`` − (z − ``referenceElevationM``) / H, H =
    ``scaleHeightKm`` (S4.C, ledger A5): β is the ratio of the pixel's CO2
    column to the scan's, which scales with surface pressure.

    ``sigmaRatio`` is the pixels' empirical σ_β (from along-track second
    differences of β) over the fit's formal σ_β; every σ_β the model uses,
    and the free fit's σ_β when ``fitPixels`` shrinks toward the model, is
    the formal one times it. On TRR3 data the noise is spectrally
    correlated (the IKF filter) and the formal σ_β is about twice too large.

    ``scatter`` is the robust std of ln β about the line (1.4826 · MAD);
    ``intrinsicScatter`` τ is what is left of it beyond the pixels'
    (empirical) σ_β/β, the part topography does not explain, and at least
    its spatially coherent part (``_coherentVariance``). The reference
    elevation is the fit's weighted mean, so ``lnBeta0`` and the slope are
    uncorrelated and ``lnBeta0Sigma`` and ``scaleHeightSigmaKm`` are their
    formal σ (spatially correlated residuals make them optimistic).
    ``accepted``: H lies in 5–20 km and the slope differs from 0 by at least
    3σ."""

    lnBeta0: float
    scaleHeightKm: float
    referenceElevationM: float
    scatter: float
    accepted: bool
    lnBeta0Sigma: float
    scaleHeightSigmaKm: float
    intrinsicScatter: float
    sigmaRatio: float

    def prior(self, elevationM: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The prior mean of β at ``elevationM`` and its σ: τ and the line's
        own uncertainty at that elevation, in ln β, times β."""
        offset = np.asarray(elevationM, dtype=np.float64) - self.referenceElevationM
        heightM = 1000.0 * self.scaleHeightKm
        slopeSigma = 1000.0 * self.scaleHeightSigmaKm / heightM**2
        logMean = self.lnBeta0 - offset / heightM
        logVariance = (
            self.intrinsicScatter**2 + self.lnBeta0Sigma**2 + (slopeSigma * offset) ** 2
        )
        mean = np.exp(logMean)
        return mean, mean * np.sqrt(logVariance)


@frozen(eq=False)
class AtmosphereModel:
    """Everything the per-pixel fit holds fixed: the chosen transmission
    (``Transmission.values`` ``(s, b)``), the smoothed per-column shift
    ``(s,)``, an optional artifact spectrum per column ``(s, b)`` in ln R
    (its amplitude α is fitted per pixel; Task 14) and the elevation model,
    which is the prior on β when accepted."""

    transmission: Transmission
    shiftNm: np.ndarray
    artifact: np.ndarray | None = None
    elevation: ElevationModel | None = None


@frozen(eq=False)
class PixelFit:
    """The per-pixel fit, ``(l, s)`` per field but the last three.

    ``artifactAmplitude`` is α (0 without an artifact). ``residualRms`` is
    the σ-weighted RMS residual over the score window. Invalid pixels (too
    few bands, a singular fit, β ≤ 0) are NaN. For the σ of the correction:
    ``gain`` ``(l, s, q, k)`` is ∂(β[, α])/∂ln R at the bands ``fitBands``
    ``(k,)`` (0 where a band was not used), and ``covariance`` ``(l, s, q,
    q)`` is the covariance of (β[, α]), prior included."""

    beta: np.ndarray
    betaSigma: np.ndarray
    artifactAmplitude: np.ndarray
    residualRms: np.ndarray
    valid: np.ndarray
    fitBands: np.ndarray
    gain: np.ndarray
    covariance: np.ndarray


def _modelTerms(wavelengths: np.ndarray, model: AtmosphereModel) -> list[np.ndarray]:
    """The atmosphere's columns of the design, ``(s, b)`` each: ln T_x(λ + δ_x)
    and the artifact, if any."""
    logT = shiftedLogTransmission(wavelengths, model.transmission.values, model.shiftNm)
    if model.artifact is None:
        return [logT]
    return [logT, np.asarray(model.artifact, dtype=np.float64)]


def fitPixels(
    cube: np.ndarray,
    sigma: np.ndarray,
    wavelengths: np.ndarray,
    model: AtmosphereModel,
    elevationM: np.ndarray | None = None,
) -> PixelFit:
    """Fit every pixel of ``cube`` ``(l, s, b)`` (I/F, with ``sigma``;
    ``wavelengths`` ``(s, b)``) over the fit window, with T and δ fixed
    (S4.B): ln R = Σ c_k P_k + β ln T_x(λ + δ_x) [+ α a_x], the same
    continuum and hydration mask as ``fitColumns``.

    The model is linear, so each column's solution operator (AᵀWA)⁻¹AᵀW is
    computed once, with the column's mean σ_lnR² as weights, and applied to
    all its complete lines; a pixel missing some of the column's fitted
    bands is solved on its own bands with its own weights. Either way the
    covariance is propagated from the pixel's own σ.

    With an accepted elevation model (it then needs ``elevationM`` ``(l,
    s)``), β has the Gaussian prior ``model.elevation.prior(z)``. The
    augmented least squares (the data plus one row for the prior) is solved
    in closed form, as a scalar update of the free fit: β moves toward the
    prior mean by V / (V + s²), V the free fit's variance of β and s² the
    prior's, and every coefficient with it by its covariance with β. V and
    the covariance are the formal ones times ``sigmaRatio``² (the empirical
    σ_β), here and in the result. A pixel whose elevation is invalid keeps
    the free fit (its covariance is still rescaled)."""
    elevation = model.elevation
    if elevation is not None and not elevation.accepted:
        elevation = None  # out of range or undetermined: per-pixel β (A5)
    if elevation is not None and elevationM is None:
        raise ValueError("an accepted elevation model needs elevationM")
    cube = np.asarray(cube, dtype=np.float64)
    sigma = np.asarray(sigma, dtype=np.float64)
    wavelengths = np.asarray(wavelengths, dtype=np.float64)
    terms = _modelTerms(wavelengths, model)
    fitted = _inside(wavelengths, FIT_WINDOW_NM) & ~_inside(
        wavelengths, HYDRATION_MASK_NM
    )
    for term in terms:
        fitted &= np.isfinite(term)
    bands = np.nonzero(fitted.any(axis=0))[0]
    fitted = fitted[:, bands]
    window = wavelengths[:, bands]
    u = np.where(
        fitted,
        2 * (window - FIT_WINDOW_NM[0]) / (FIT_WINDOW_NM[1] - FIT_WINDOW_NM[0]) - 1,
        0.0,
    )
    design = np.concatenate(
        [legendre.legvander(u, CONTINUUM_DEGREE)]
        + [np.nan_to_num(term[:, bands])[..., None] for term in terms],
        axis=-1,
    )
    design = np.where(fitted[..., None], design, 0.0)  # (s, k, p)
    with np.errstate(invalid="ignore", divide="ignore"):
        logR = np.log(cube[..., bands])
        weight = cube[..., bands] / sigma[..., bands]  # 1 / σ_lnR
    usable = fitted[None] & np.isfinite(logR) & np.isfinite(weight) & (weight > 0)
    weight = np.where(usable, weight, 0.0)
    logR = np.where(usable, logR, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        variance = np.where(usable, 1.0 / weight**2, 0.0)  # σ_lnR²

    # one solution per column for its complete lines
    complete = (usable == fitted[None]).all(axis=2) & fitted.any(axis=1)[None]
    count = complete.sum(axis=0)[:, None]
    with np.errstate(invalid="ignore", divide="ignore"):
        meanVariance = np.where(complete[..., None], variance, 0.0).sum(axis=0) / count
        columnWeight = np.where(fitted & (count > 0), 1.0 / np.sqrt(meanVariance), 0.0)
    columns = _normal(design, np.nan_to_num(columnWeight))
    gain = np.broadcast_to(columns.gain, (cube.shape[0], *columns.gain.shape)).copy()
    solvable = complete & columns.valid[None]

    # pixels missing some of the column's bands: solved on their own
    partial = ~complete & usable.any(axis=2)
    if partial.any():
        t, x = np.nonzero(partial)
        own = _normal(design[x], weight[t, x])
        gain[t, x] = own.gain
        solvable[t, x] = own.valid
    gain[~solvable] = 0.0

    coefficients = np.einsum("lspk,lsk->lsp", gain, logR)
    covariance = (gain * variance[:, :, None, :]) @ gain.swapaxes(-1, -2)
    b = CONTINUUM_DEGREE + 1  # the index of β
    if elevation is not None and elevationM is not None:
        # the free fit's σ is the empirical one when it meets the prior
        covariance = covariance * elevation.sigmaRatio**2
        mean, spread = elevation.prior(elevationM)
        informed = np.isfinite(mean) & np.isfinite(spread) & (spread > 0)
        denominator = covariance[..., b, b] + np.where(informed, spread**2, np.inf)
        withBeta = covariance[..., :, b]  # (l, s, p)
        step = (np.where(informed, mean, 0.0) - coefficients[..., b]) / denominator
        coefficients = coefficients + withBeta * step[..., None]
        update = withBeta / denominator[..., None]
        gain = gain - update[..., :, None] * gain[..., b : b + 1, :]
        covariance = covariance - update[..., :, None] * withBeta[..., None, :]

    residuals = logR - np.einsum("skp,lsp->lsk", design, coefficients)
    scored = usable & _inside(window, SCORE_WINDOW_NM)[None]
    w2 = np.where(scored, weight, 0.0) ** 2
    beta = coefficients[..., b]
    valid = (
        solvable
        & (usable.sum(axis=2) >= _MIN_FIT_BANDS)
        & (scored.sum(axis=2) >= _MIN_SCORE_BANDS)
        & (beta > 0)
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        rms = np.sqrt((w2 * residuals**2).sum(axis=2) / w2.sum(axis=2))
    atmospheric = list(range(b, design.shape[-1]))

    def masked(values: np.ndarray) -> np.ndarray:
        return np.where(valid, values, np.nan)

    return PixelFit(
        beta=masked(beta),
        betaSigma=masked(np.sqrt(covariance[..., b, b])),
        artifactAmplitude=masked(
            coefficients[..., b + 1]
            if model.artifact is not None
            else np.zeros_like(beta)
        ),
        residualRms=masked(rms),
        valid=valid,
        fitBands=bands,
        gain=np.where(valid[..., None, None], gain[..., atmospheric, :], 0.0),
        covariance=np.where(
            valid[..., None, None],
            covariance[..., atmospheric, :][..., :, atmospheric],
            np.nan,
        ),
    )


def _biweight(standardized: np.ndarray) -> np.ndarray:
    """Tukey's biweight (c = 4.685) of residuals already on a unit scale."""
    z = standardized / _TUKEY_C
    return np.where(np.abs(z) < 1, (1 - z**2) ** 2, 0.0)


def _excessVariance(
    residuals: np.ndarray, variance: np.ndarray, weights: np.ndarray
) -> float:
    """τ² ≥ 0 such that the biweight-weighted mean of r² / (σ² + τ²) is what it
    is for unit Gaussian residuals (0.828 at c = 4.685): the scatter beyond
    each value's own σ, with the same weights as the line (bisection; the
    mean is decreasing in τ²)."""
    squared = residuals**2
    total = float(weights.sum())

    def meanSquare(tau2: float) -> float:
        return float(np.sum(weights * squared / (variance + tau2))) / total

    if total <= 0 or meanSquare(0.0) <= _TUKEY_MEAN_SQUARE:
        return 0.0
    low, high = 0.0, float(squared.max()) / _TUKEY_MEAN_SQUARE
    for _ in range(_BISECTIONS):
        middle = 0.5 * (low + high)
        if meanSquare(middle) > _TUKEY_MEAN_SQUARE:
            low = middle
        else:
            high = middle
    return 0.5 * (low + high)


def _coherentVariance(deviation: np.ndarray, variance: np.ndarray) -> float:
    """The variance of the spatially coherent part of ``deviation`` ``(l, s)``
    (NaN where unused): the robust variance (1.4826 · MAD)² of its 7 × 7
    means, less what the pixels' own ``variance`` puts into them. Departures
    from the elevation law that persist over many pixels are real (aerosol,
    weather, or surface structure the fit reads as β), not noise, so τ is at
    least this; 0 when too few windows are half full."""
    used = np.isfinite(deviation)
    area = _COHERENT_BOX**2

    def windowSum(values: np.ndarray) -> np.ndarray:
        filtered = ndimage.uniform_filter(
            np.where(used, values, 0.0), _COHERENT_BOX, mode="constant"
        )
        return filtered * area

    count = windowSum(np.ones_like(deviation))
    full = used & (count >= area / 2)
    if full.sum() < 3:
        return 0.0
    means = windowSum(deviation)[full] / count[full]
    noise = float(np.mean(windowSum(variance)[full] / count[full] ** 2))
    spread = _MAD_TO_SIGMA * float(np.median(np.abs(means - np.median(means))))
    return max(0.0, spread**2 - noise)


def _alongTrackRatio(logBeta: np.ndarray, variance: np.ndarray) -> float:
    """The empirical σ of ln β over its formal σ: 1.4826 · MAD of the
    along-track second differences ln β_t − ½(ln β_t−1 + ln β_t+1) of each
    column, each over its formal σ (``variance`` ``(l, s)``). Neighboring
    lines see the same atmosphere and nearly the same ground, so the
    difference is the noise; 1 with fewer than 10 complete triplets."""
    if logBeta.shape[0] < 3:
        return 1.0
    difference = logBeta[1:-1] - 0.5 * (logBeta[:-2] + logBeta[2:])
    expected = variance[1:-1] + 0.25 * (variance[:-2] + variance[2:])
    with np.errstate(invalid="ignore", divide="ignore"):
        z = difference / np.sqrt(expected)
    z = z[np.isfinite(z)]
    if z.size < _MIN_TRIPLETS:
        return 1.0
    return _MAD_TO_SIGMA * float(np.median(np.abs(z - np.median(z))))


@frozen(eq=False)
class _Line:
    reference: float
    intercept: float
    slope: float
    residuals: np.ndarray


def _weightedLine(y: np.ndarray, z: np.ndarray, weights: np.ndarray) -> _Line:
    """Weighted least squares y = intercept + slope (z − reference), the
    reference the weighted mean of z."""
    total = float(np.sum(weights))
    reference = float(np.sum(weights * z)) / total
    offset = z - reference
    intercept = float(np.sum(weights * y)) / total
    slope = float(np.sum(weights * offset * y) / np.sum(weights * offset**2))
    return _Line(reference, intercept, slope, y - intercept - slope * offset)


def fitElevationModel(
    beta: np.ndarray, betaSigma: np.ndarray, elevationM: np.ndarray
) -> ElevationModel:
    """The robust line of ln β against z (S4.C, A5). ``beta`` and
    ``betaSigma`` are a free ``fitPixels``'s, NaN where invalid, and
    ``elevationM`` is the DDR's; all ``(l, s)`` with the lines in along-track
    order (a subset of lines, e.g. Pass A's, is enough).

    The pixels' formal σ_β is first rescaled to their empirical σ
    (``_alongTrackRatio``). Then, iteratively reweighted least squares: each
    pixel is weighted by 1 / (σ²_lnβ + τ²) times a Tukey biweight (c = 4.685)
    of its residual over √(σ²_lnβ + τ²), so hydrated units and spikes, whose
    β the 1.9 µm band or the spike has biased, lose weight. Each step
    re-estimates τ² with the same biweights (``_excessVariance``), floored by
    the coherent part of the residuals (``_coherentVariance``). The line and
    its σ come from the final weights and τ². With fewer than 3 usable
    pixels the model is NaN and not accepted."""
    beta = np.asarray(beta, dtype=np.float64)
    elevationM = np.asarray(elevationM, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        logBeta = np.log(beta)
        variance = (np.asarray(betaSigma, dtype=np.float64) / beta) ** 2
    keep = (
        np.isfinite(logBeta)
        & np.isfinite(variance)
        & (variance > 0)
        & np.isfinite(elevationM)
    )
    if keep.sum() < 3:
        nan = math.nan
        return ElevationModel(nan, nan, nan, nan, False, nan, nan, nan, nan)
    ratio = _alongTrackRatio(np.where(keep, logBeta, np.nan), variance)
    variance = np.where(keep, ratio**2 * variance, np.nan)
    y, v, z = logBeta[keep], variance[keep], elevationM[keep]
    deviation = np.full(keep.shape, np.nan)
    robust = np.ones_like(y)
    tau2 = 0.0
    for _ in range(_IRLS_ITERATIONS):
        line = _weightedLine(y, z, robust / (v + tau2))
        deviation[keep] = line.residuals
        updatedTau2 = max(
            _excessVariance(line.residuals, v, robust),
            _coherentVariance(deviation, variance),
        )
        updated = _biweight(line.residuals / np.sqrt(v + updatedTau2))
        settled = np.allclose(updated, robust, atol=1e-6) and math.isclose(
            updatedTau2, tau2, rel_tol=1e-6, abs_tol=1e-12
        )
        robust, tau2 = updated, updatedTau2
        if settled:
            break
    weights = robust / (v + tau2)
    line = _weightedLine(y, z, weights)
    offset = z - line.reference
    standardized = line.residuals / np.sqrt(v + tau2)
    meanSquare = float(np.sum(robust * standardized**2) / np.sum(robust))
    scale = max(1.0, math.sqrt(meanSquare / _TUKEY_MEAN_SQUARE))
    slope = line.slope
    slopeSigma = scale / math.sqrt(float(np.sum(weights * offset**2)))
    heightKm = -1.0 / (1000.0 * slope) if slope != 0 else math.inf
    determined = slope < 0 and -slope >= _ELEVATION_SIGNIFICANCE * slopeSigma
    low, high = SCALE_HEIGHT_RANGE_KM
    return ElevationModel(
        lnBeta0=line.intercept,
        scaleHeightKm=heightKm,
        referenceElevationM=line.reference,
        scatter=_MAD_TO_SIGMA * float(np.median(np.abs(line.residuals))),
        accepted=bool(determined and low <= heightKm <= high),
        lnBeta0Sigma=scale / math.sqrt(float(np.sum(weights))),
        scaleHeightSigmaKm=slopeSigma / (1000.0 * slope**2) if slope != 0 else math.inf,
        intrinsicScatter=math.sqrt(tau2),
        sigmaRatio=ratio,
    )


def correct(
    cube: np.ndarray,
    sigma: np.ndarray,
    wavelengths: np.ndarray,
    model: AtmosphereModel,
    fit: PixelFit,
) -> tuple[np.ndarray, np.ndarray]:
    """R′ = R / exp(β ln T_x(λ + δ_x) + α a_x) over all bands (S4.D), and σ′.

    In ln R′ = ln R − t·θ (t the atmosphere's terms, θ = (β[, α])), θ is
    itself linear in the data it was fitted to, θ = G ln R + (prior), so

        σ²_lnR′ = σ²_lnR − 2 σ²_lnR Σ_q t_q G_q + tᵀ C t,

    C the covariance of θ; the middle term (the fitted bands only) is the
    covariance of θ with ln R. Neglecting it, as σ²_lnR + tᵀ C t, overstates
    σ′ in the fitted CO2 bands (``test_correct_sigma_chi2``). Invalid pixels
    are NaN.

    Peak memory is about five float64 copies of ``cube`` beyond its input:
    call it per chunk (64 lines), as the pipeline does."""
    cube = np.asarray(cube, dtype=np.float64)
    sigma = np.asarray(sigma, dtype=np.float64)
    terms = np.stack(_modelTerms(np.asarray(wavelengths, np.float64), model), axis=-1)
    amplitudes = np.stack(
        [fit.beta] + ([fit.artifactAmplitude] if model.artifact is not None else []),
        axis=-1,
    )
    path = np.einsum("sbq,lsq->lsb", terms, amplitudes)
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        corrected = cube * np.exp(-path)
        relative = (sigma / cube) ** 2
    variance = relative + np.einsum("sbq,lsqr,sbr->lsb", terms, fit.covariance, terms)
    k = fit.fitBands
    withData = np.einsum("skq,lsqk->lsk", terms[:, k], fit.gain) * relative[..., k]
    variance[..., k] -= 2 * withData
    corrected = np.where(fit.valid[..., None], corrected, np.nan)
    with np.errstate(invalid="ignore"):
        return corrected, corrected * np.sqrt(variance)
