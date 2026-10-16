"""Atmospheric correction (spec S4), part A: the per-column fit and the
objective choice of transmission.

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
class _Solution:
    coefficients: np.ndarray  # (n, p)
    covariance: np.ndarray  # (n, p, p)
    residuals: np.ndarray  # (n, b), NaN where not used
    valid: np.ndarray  # (n,)


def _solve(design: np.ndarray, target: np.ndarray, weight: np.ndarray) -> _Solution:
    """Weighted least squares per row: ``design`` ``(n, b, p)``, ``target`` and
    ``weight`` ``(n, b)``, a zero weight dropping a band. Normal equations,
    equilibrated by the diagonal; rows whose matrix is singular are invalid."""
    used = weight > 0
    weighted = np.where(used[..., None], design, 0.0) * weight[..., None]
    gram = np.einsum("nbj,nbk->njk", weighted, weighted)
    moments = np.einsum("nbj,nb->nj", weighted, np.where(used, target, 0.0) * weight)
    diagonal = np.einsum("njj->nj", gram)
    valid = (diagonal > 0).all(axis=1)
    scale = np.sqrt(np.where(diagonal > 0, diagonal, 1.0))
    outer = scale[:, :, None] * scale[:, None, :]
    equilibrated = np.where(valid[:, None, None], gram / outer, np.eye(gram.shape[1]))
    valid &= np.linalg.cond(equilibrated) < 1e12
    equilibrated[~valid] = np.eye(gram.shape[1])
    covariance = np.linalg.inv(equilibrated) / outer
    coefficients = np.einsum("njk,nk->nj", covariance, moments)
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
