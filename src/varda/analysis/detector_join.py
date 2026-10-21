"""Scaling CRISM's VNIR bands to its IR bands at their join near 1 µm.

CRISM measures 362-1053 nm on its VNIR (S) detector and 1001-3920 nm on its
IR (L) detector (Murchie et al. 2007); a joined (J) product such as an MTRDR
splices them, keeping the S bands up to about 1010 nm and the L bands from
about 1047 nm. The two detectors' calibrations differ slightly, so a
spectrum often steps at the join: in FRT00005850's MTRDR the IR side sits a
median 1.9 percent below the VNIR side. A ratio divides out a step only as
far as numerator and denominator share it.

The correction is Varda's own, empirical step: a straight line is fitted
to each side of the join (the good bands within ``FIT_NM`` of the gap), both
lines are evaluated at the gap's middle, and the VNIR bands are multiplied
by IR / VNIR there, so the spectrum is continuous; the IR bands, and so
every band depth beyond 1 µm, are unchanged. The factor's standard error
comes from the two fits' standard errors at that wavelength. A spectrum
with real curvature across the 37 nm gap (the short-wavelength side of a
1 µm band) is made continuous to the fitted lines, not to the truth. No Qt.
"""

from __future__ import annotations

import attrs
import numpy as np

# the join is the widest gap between band centers in this range (nm)
SEARCH_NM = (950.0, 1100.0)
# a gap this many times the cube's median band spacing is a detector join
MIN_GAP_SPACINGS = 3.0
FIT_NM = 80.0  # the bands fitted on each side of the gap
MIN_FIT_BANDS = 4  # per side


@attrs.frozen
class DetectorJoin:
    """Where a cube's VNIR bands end and its IR bands begin."""

    vnirEndNm: float  # the last VNIR band's center
    irStartNm: float  # the first IR band's center
    vnir: np.ndarray  # bool per band: on the VNIR detector (below the gap)
    vnirFit: np.ndarray  # bool per band: fitted on the VNIR side
    irFit: np.ndarray  # bool per band: fitted on the IR side

    @property
    def atNm(self) -> float:
        """Where the two sides are matched: the middle of the gap."""
        return (self.vnirEndNm + self.irStartNm) / 2


def findJoin(
    wavelengthsNm: np.ndarray, good: np.ndarray | None = None
) -> DetectorJoin | None:
    """The VNIR/IR join of a cube with bands on both detectors: the widest
    gap between neighboring band centers in ``SEARCH_NM`` that is at least
    ``MIN_GAP_SPACINGS`` times the median spacing, with ``MIN_FIT_BANDS``
    good bands (``good``, default all finite) within ``FIT_NM`` on each
    side. None for a cube on one detector (or without wavelengths there)."""
    centers = np.asarray(wavelengthsNm, np.float64)
    good = (
        np.isfinite(centers)
        if good is None
        else np.asarray(good) & np.isfinite(centers)
    )
    order = np.argsort(np.where(np.isfinite(centers), centers, np.inf))
    sorted_ = centers[order][np.isfinite(centers[order])]
    if sorted_.size < 2 * MIN_FIT_BANDS:
        return None
    gaps = np.diff(sorted_)
    spacing = float(np.median(gaps))
    inRange = (sorted_[:-1] >= SEARCH_NM[0]) & (sorted_[1:] <= SEARCH_NM[1])
    candidates = np.where(inRange & (gaps >= MIN_GAP_SPACINGS * spacing))[0]
    if candidates.size == 0:
        return None
    at = candidates[np.argmax(gaps[candidates])]
    vnirEnd, irStart = float(sorted_[at]), float(sorted_[at + 1])
    with np.errstate(invalid="ignore"):
        vnir = centers <= vnirEnd
        vnirFit = good & vnir & (centers >= vnirEnd - FIT_NM)
        irFit = good & (centers >= irStart) & (centers <= irStart + FIT_NM)
    if vnirFit.sum() < MIN_FIT_BANDS or irFit.sum() < MIN_FIT_BANDS:
        return None
    return DetectorJoin(vnirEnd, irStart, vnir, vnirFit, irFit)


@attrs.frozen
class JoinScale:
    """The factors that bring the VNIR bands to the IR's level, one per
    spectrum (NaN where a side has too few finite bands or a level is not
    positive), and their standard errors."""

    factor: np.ndarray
    sigma: np.ndarray


def _lineAt(x: np.ndarray, y: np.ndarray, at: float) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares lines through each row of ``y`` (NaN ignored), at
    ``at``: their values and standard errors (NaN with fewer than
    ``MIN_FIT_BANDS`` points)."""
    w = np.isfinite(y)
    n = w.sum(axis=1).astype(np.float64)
    xs = np.where(w, x, 0.0)
    ys = np.where(w, y, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        xMean = xs.sum(axis=1) / n
        yMean = ys.sum(axis=1) / n
        dx = np.where(w, x - xMean[:, None], 0.0)
        sxx = (dx**2).sum(axis=1)
        slope = (dx * np.where(w, y - yMean[:, None], 0.0)).sum(axis=1) / sxx
        value = yMean + slope * (at - xMean)
        residuals = np.where(w, y - (yMean[:, None] + slope[:, None] * dx), 0.0)
        variance = (residuals**2).sum(axis=1) / (n - 2)
        se = np.sqrt(variance * (1 / n + (at - xMean) ** 2 / sxx))
    few = n < MIN_FIT_BANDS
    return np.where(few, np.nan, value), np.where(few, np.nan, se)


def joinScale(
    spectra: np.ndarray, wavelengthsNm: np.ndarray, join: DetectorJoin
) -> JoinScale:
    """The VNIR factor for each spectrum (``spectra``: (..., bands))."""
    data = np.asarray(spectra, np.float64)
    flat = data.reshape(-1, data.shape[-1])
    centers = np.asarray(wavelengthsNm, np.float64)
    vnir, vnirSe = _lineAt(centers[join.vnirFit], flat[:, join.vnirFit], join.atNm)
    ir, irSe = _lineAt(centers[join.irFit], flat[:, join.irFit], join.atNm)
    with np.errstate(invalid="ignore", divide="ignore"):
        usable = (vnir > 0) & (ir > 0)
        factor = np.where(usable, ir / vnir, np.nan)
        sigma = factor * np.sqrt((irSe / ir) ** 2 + (vnirSe / vnir) ** 2)
    shape = data.shape[:-1]
    return JoinScale(
        factor.reshape(shape), np.where(usable, sigma, np.nan).reshape(shape)
    )


def scaleVnir(
    spectra: np.ndarray, join: DetectorJoin, factor: np.ndarray | float
) -> np.ndarray:
    """``spectra`` (..., bands) with their VNIR bands multiplied by
    ``factor`` (one per spectrum); a NaN factor leaves its spectrum as it is."""
    out = np.array(spectra, dtype=np.result_type(spectra, np.float32))
    f = np.asarray(factor, np.float64)
    f = np.where(np.isfinite(f), f, 1.0)
    out[..., join.vnir] = out[..., join.vnir] * f[..., None]
    return out
