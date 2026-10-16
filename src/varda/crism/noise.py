"""The noise model σ0 (spec S1), estimated from the data itself.

σ0 is the instrument noise: the part of the along-track variation that is
different in every band. Along-track second differences compare one detector
element with itself on adjacent lines, so fixed stripes cancel, but they also
contain surface texture, which scales all bands of a pixel together
(achromatic) and is far larger than the noise on real scenes. The differences
are therefore taken on brightness-normalized spectra,

    d = R(t) - L(t) · ½[R(t-1)/L(t-1) + R(t+1)/L(t+1)],

with L the mean of the pixel's other bands within ±10 bands (never across a
filter boundary when ``wavelengths`` are given), so any factor that scales a
whole spectrum cancels and per-band noise stays. L excludes the band itself, so
for independent noise the second difference of one band has variance 1.5 σ²
plus that of L's noise, 1.5 σ²/m for m bands in L (m ≤ 20): the robust spread
``1.4826 · MAD / √(1.5 (1 + 1/m))`` is σ (checked on white noise in the
tests). ``achromaticFraction`` reports how much of the plain second-difference
variance the normalization removed. Only pixels whose local gradient (of L) is
in the quietest ``quietFraction`` count, which limits leakage from texture.
Per band, ``σ² = a(λ, x) + b(λ) · R`` is fitted to binned (R, σ²) values by
weighted least squares: b is shared by all columns and, because one band spans
too little reflectance to fix a slope, pooled over neighboring bands; a is
shared within each column group. R is the pixel's brightness from the other
bands (L, which excludes the band itself) in this band's units, so it does not
share the noise of the difference.
"""

import numpy as np
from attrs import frozen

from varda.crism.despike import FILTER_BOUNDARIES_NM

_MAD = 1.4826
_SMOOTH_HALF_WIDTH = 10  # bands each side in the brightness level L
_BINS = 8
_MIN_BIN_SAMPLES = 10
_BAND_BLOCK = 32
_SLOPE_HALF_WIDTH = 20  # bands: b(λ) is smooth, one band's slope is noisy


@frozen
class NoiseModel:
    """σ² = floor(λ, x) + shot(λ) · R, with R the reflectance (I/F)."""

    floor: np.ndarray  # (s, b)
    shot: np.ndarray  # (b,)
    # (b,) the share of the plain along-track second-difference variance that
    # the brightness normalization removed: near 1 where texture dominates
    achromaticFraction: np.ndarray

    def sigma(self, reflectance: np.ndarray) -> np.ndarray:
        """σ with the shape of ``reflectance`` ``(..., s, b)``."""
        return np.sqrt(np.maximum(self.floor + self.shot * reflectance, 0.0))


def _quietMask(
    center: np.ndarray, quietFraction: float, groups: np.ndarray
) -> np.ndarray:
    """Pixels in the lowest ``quietFraction`` of the gradient, per (group, band).

    ``center`` is ``(l - 2, s, b)``, the lines the differences belong to.
    """
    gLine = np.gradient(center, axis=0)
    gSample = np.gradient(center, axis=1)
    gradient = np.hypot(gLine, gSample)
    gradient[~np.isfinite(gradient)] = np.inf
    quiet = np.zeros(center.shape, dtype=bool)
    for g in np.unique(groups):
        cols = groups == g
        block = gradient[:, cols, :].reshape(-1, center.shape[2])
        ordered = np.sort(block, axis=0)
        count = np.isfinite(ordered).sum(axis=0)
        index = np.maximum(np.floor(quietFraction * (count - 1)).astype(int), 0)
        threshold = np.take_along_axis(ordered, index[None, :], axis=0)[0]
        quiet[:, cols, :] = gradient[:, cols, :] <= threshold
    return quiet & np.isfinite(gradient)


def _bandStatistics(
    r: np.ndarray,
    d: np.ndarray,
    groupIndex: np.ndarray,
    groupCount: int,
    spread: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per (group, bin): count, mean R, σ² for one band's quiet samples; ``spread``
    is the standard deviation of ``d`` for unit noise."""
    count = np.zeros((groupCount, _BINS))
    meanR = np.zeros((groupCount, _BINS))
    var = np.zeros((groupCount, _BINS))
    edges = np.quantile(r, np.linspace(0, 1, _BINS + 1)[1:-1])
    binIndex = np.digitize(r, edges)
    for g in range(groupCount):
        inGroup = groupIndex == g
        for k in range(_BINS):
            sel = inGroup & (binIndex == k)
            n = int(sel.sum())
            if n < _MIN_BIN_SAMPLES:
                continue
            dk = d[sel]
            mad = np.median(np.abs(dk - np.median(dk)))
            count[g, k] = n
            meanR[g, k] = r[sel].mean()
            var[g, k] = (_MAD * mad / spread) ** 2
    return count, meanR, var


def _shotSlopes(count: np.ndarray, meanR: np.ndarray, var: np.ndarray) -> np.ndarray:
    """b(λ): the slope of σ² against R, pooled over neighboring bands.

    ``count``, ``meanR`` and ``var`` are ``(band, group, bin)``. One band over
    a scene spans little reflectance, so its own slope is too noisy; b is
    smooth in wavelength, so the within-(band, group) covariance of σ² and R
    (which removes each floor) is summed over a window of bands. NaN where the
    window holds no data.
    """
    valid = (count > 0) & (var > 0)
    vbar = np.where(valid, count * var, 0).sum(-1, keepdims=True) / np.maximum(
        count.sum(-1, keepdims=True), 1
    )
    weight = np.where(valid, count, 0) / np.where(vbar > 0, vbar, np.inf) ** 2
    total = np.maximum(weight.sum(-1, keepdims=True), np.finfo(float).tiny)
    x = np.where(valid, meanR, 0)
    y = np.where(valid, var, 0)
    xc = np.where(valid, x - (weight * x).sum(-1, keepdims=True) / total, 0)
    yc = np.where(valid, y - (weight * y).sum(-1, keepdims=True) / total, 0)
    kernel = np.ones(2 * _SLOPE_HALF_WIDTH + 1)
    cross = np.convolve((weight * xc * yc).sum(axis=(1, 2)), kernel, mode="same")
    spread = np.convolve(
        (weight * xc * xc).sum(axis=(1, 2)),
        kernel,
        mode="same",
    )
    own = valid.any(axis=(1, 2))
    with np.errstate(invalid="ignore", divide="ignore"):
        slope = np.where(spread > 0, cross / spread, np.nan)
    return np.where(own, slope, np.nan)


def _fitFloor(
    count: np.ndarray, meanR: np.ndarray, var: np.ndarray, b: float
) -> np.ndarray:
    """The floor of each group with the shot term fixed."""
    # Weights from the group's mean variance: weights from each noisy estimate
    # would favor the low ones and bias the floor down.
    valid = (count > 0) & (var > 0)
    vbar = np.where(valid, count * var, 0).sum(-1, keepdims=True) / np.maximum(
        count.sum(-1, keepdims=True), 1
    )
    weight = np.where(valid, count, 0) / np.where(vbar > 0, vbar, np.inf) ** 2
    resid = np.where(valid, var - b * meanR, 0)
    total = weight.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        floor = (weight * resid).sum(axis=1) / total
    return np.where(total > 0, np.maximum(floor, 0.0), np.nan)


def _fillNearest(values: np.ndarray, axis: int) -> np.ndarray:
    """NaN entries take the value of the nearest finite entry along ``axis``
    (unchanged where a lane has none)."""
    moved = np.moveaxis(values, axis, -1).copy()
    index = np.arange(moved.shape[-1])
    for lane in moved.reshape(-1, moved.shape[-1]):  # views into ``moved``
        known = np.isfinite(lane)
        if known.any() and not known.all():
            nearest = np.abs(index[:, None] - index[known][None, :]).argmin(axis=1)
            lane[:] = lane[known][nearest]
    return np.moveaxis(moved, -1, axis)


def _bandWindows(
    wavelengths: np.ndarray | None, bands: int
) -> tuple[np.ndarray, np.ndarray]:
    """First and last band (inclusive) of each band's smoothing window, kept
    inside its filter segment."""
    if wavelengths is None:
        segment = np.zeros(bands, dtype=int)
    else:
        segment = np.searchsorted(
            FILTER_BOUNDARIES_NM, np.nan_to_num(wavelengths, nan=np.inf)
        )
    index = np.arange(bands)
    first = np.empty(bands, dtype=int)
    last = np.empty(bands, dtype=int)
    for s in np.unique(segment):
        members = index[segment == s]
        first[members] = np.maximum(members - _SMOOTH_HALF_WIDTH, members[0])
        last[members] = np.minimum(members + _SMOOTH_HALF_WIDTH, members[-1])
    return first, last


def _smoothBands(
    block: np.ndarray, first: np.ndarray, last: np.ndarray, own: np.ndarray
) -> np.ndarray:
    """Mean of the finite values over each band's window ``[first, last]``
    (indices into ``block``'s band axis) without the band itself, ``own`` being
    that band's values ``(l, s, b)``; NaN where a window has none."""
    finite = np.isfinite(block)
    padding = [(0, 0), (0, 0), (1, 0)]
    total = np.pad(np.cumsum(np.where(finite, block, 0.0), axis=2), padding)
    number = np.pad(np.cumsum(finite, axis=2), padding)
    ownFinite = np.isfinite(own)
    n = number[:, :, last + 1] - number[:, :, first] - ownFinite
    summed = total[:, :, last + 1] - total[:, :, first] - np.where(ownFinite, own, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return summed / np.where(n > 0, n, np.nan)


def _robustVariance(values: np.ndarray) -> float:
    return float((_MAD * np.median(np.abs(values - np.median(values)))) ** 2)


def _blockStatistics(
    cube: np.ndarray,
    bandRange: slice,
    windows: tuple[np.ndarray, np.ndarray],
    columnGroups: np.ndarray,
    groupCount: int,
    quietFraction: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(count, mean R, σ², achromatic fraction) for a block of bands: arrays
    ``(band, group, bin)`` and ``(band,)``."""
    first, last = windows
    # The smoothing needs the bands around the block, within each window.
    lo = int(first[bandRange].min())
    hi = int(last[bandRange].max()) + 1
    halo = cube[:, :, lo:hi].astype(np.float64)
    inside = slice(bandRange.start - lo, bandRange.stop - lo)
    bandCount = bandRange.stop - bandRange.start
    block = halo[:, :, inside]
    level = _smoothBands(halo, first[bandRange] - lo, last[bandRange] - lo, block)
    ratio = block / level
    center = block[1:-1]
    # Texture and anything else that scales a whole spectrum cancels here.
    d = center - level[1:-1] * 0.5 * (ratio[:-2] + ratio[2:])
    plain = center - 0.5 * (block[:-2] + block[2:])
    # The reflectance each difference is regressed against must not share its
    # noise, so it is the pixel's brightness from the other bands (L) in this
    # band's units: the band's typical ratio to L times L.
    typical = np.array(
        [
            np.median(v[np.isfinite(v)]) if np.isfinite(v).any() else np.nan
            for v in np.moveaxis(ratio, 2, 0)
        ]
    )
    mean = level[1:-1] * typical
    quiet = (
        _quietMask(mean, quietFraction, columnGroups)
        & np.isfinite(d)
        & np.isfinite(plain)
        & np.isfinite(mean)
    )
    groupIndex = np.broadcast_to(columnGroups[None, :], center.shape[:2])
    count = np.zeros((bandCount, groupCount, _BINS))
    meanR = np.zeros_like(count)
    var = np.zeros_like(count)
    achromatic = np.full(bandCount, np.nan)
    others = (last - first)[bandRange]  # bands in L besides the band itself
    for band in range(bandCount):
        mask = quiet[:, :, band]
        if mask.sum() < _BINS * _MIN_BIN_SAMPLES:
            continue
        spread = np.sqrt(1.5 * (1.0 + 1.0 / max(others[band], 1)))
        count[band], meanR[band], var[band] = _bandStatistics(
            mean[:, :, band][mask],
            d[:, :, band][mask],
            groupIndex[mask],
            groupCount,
            spread,
        )
        # Plain differences carry the noise at 1.5 σ²; normalized ones at spread².
        removed = 1.0 - _robustVariance(d[:, :, band][mask]) / (
            spread**2 / 1.5 * _robustVariance(plain[:, :, band][mask])
        )
        achromatic[band] = np.clip(removed, 0.0, 1.0)
    return count, meanR, var, achromatic


def estimateNoise(
    cube: np.ndarray,
    columnGroup: int = 16,
    quietFraction: float = 0.5,
    wavelengths: np.ndarray | None = None,
) -> NoiseModel:
    """Estimate the noise model of a ``(line, sample, band)`` reflectance cube.

    ``wavelengths`` ``(b,)`` (nm, ascending) keeps the brightness smoothing
    inside the detector's filter segments; without it the cube is one segment.
    NaN samples are ignored; a band with no data at all gets NaN. Elsewhere σ is
    finite: a band too sparse for a shot slope takes the nearest band's, a column
    group with no quiet pixels the nearest group's floor. At least 3 lines are needed (``ValueError``).
    Bands are processed in blocks, so memory stays near the size of one block
    in float64 beyond the input.
    """
    lines, samples, bands = cube.shape
    if lines < 3:
        raise ValueError(
            f"The noise model needs at least 3 lines for second differences, got {lines}."
        )
    columnGroups = np.arange(samples) // columnGroup
    groupCount = int(columnGroups[-1]) + 1
    count = np.zeros((bands, groupCount, _BINS))
    meanR = np.zeros_like(count)
    var = np.zeros_like(count)
    achromatic = np.full(bands, np.nan)
    windows = _bandWindows(wavelengths, bands)
    for start in range(0, bands, _BAND_BLOCK):
        sl = slice(start, min(start + _BAND_BLOCK, bands))
        count[sl], meanR[sl], var[sl], achromatic[sl] = _blockStatistics(
            cube, sl, windows, columnGroups, groupCount, quietFraction
        )
    # Bands too sparse for a slope take the nearest band's, a column group with
    # no quiet pixels (invalid edge columns) the nearest group's floor, and
    # what is still missing the nearest band's: σ is finite wherever the data
    # are, in every band that has any.
    shot = np.maximum(_fillNearest(_shotSlopes(count, meanR, var), 0), 0.0)
    floorGroups = np.stack(
        [_fitFloor(count[i], meanR[i], var[i], shot[i]) for i in range(bands)], axis=1
    )
    floorGroups = _fillNearest(_fillNearest(floorGroups, 0), 1)
    empty = ~np.isfinite(cube).any(axis=(0, 1))  # a band with no data stays NaN
    shot[empty] = np.nan
    floorGroups[:, empty] = np.nan
    return NoiseModel(
        floor=floorGroups[columnGroups], shot=shot, achromaticFraction=achromatic
    )
