"""The noise model σ0 (spec S1), estimated from the data itself.

Along-track second differences ``d = R(t) - ½[R(t-1) + R(t+1)]`` compare one
detector element with itself on adjacent lines, so fixed stripes cancel.
Their robust spread, ``1.4826 · MAD / √1.5``, is the noise σ (the second
difference has variance 1.5 σ²). Only pixels whose local spatial gradient is
in the quietest ``quietFraction`` count, which limits leakage from texture.
Per band, ``σ² = a(λ, x) + b(λ) · R`` is fitted to binned (R, σ²) values by
weighted least squares: b is shared by all columns and, because one band spans
too little reflectance to fix a slope, pooled over neighboring bands; a is
shared within each column group. R is the mean of the neighboring detector
elements over the same lines, so it does not share the noise of the difference.
"""

import numpy as np
from attrs import frozen

_MAD_TO_SIGMA = 1.4826 / np.sqrt(1.5)
_BINS = 8
_MIN_BIN_SAMPLES = 10
_SLOPE_HALF_WIDTH = 20  # bands: b(λ) is smooth, one band's slope is noisy


@frozen
class NoiseModel:
    """σ² = floor(λ, x) + shot(λ) · R, with R the reflectance (I/F)."""

    floor: np.ndarray  # (s, b)
    shot: np.ndarray  # (b,)

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
    r: np.ndarray, d: np.ndarray, groupIndex: np.ndarray, groupCount: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per (group, bin): count, mean R, σ² for one band's quiet samples."""
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
            var[g, k] = (_MAD_TO_SIGMA * mad) ** 2
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


def estimateNoise(
    cube: np.ndarray, columnGroup: int = 16, quietFraction: float = 0.5
) -> NoiseModel:
    """Estimate the noise model of a ``(line, sample, band)`` reflectance cube.

    NaN samples are ignored; a band with no usable data gets NaN.
    """
    _, samples, bands = cube.shape
    cube = cube.astype(np.float64, copy=False)
    center = cube[1:-1]
    d = center - 0.5 * (cube[:-2] + cube[2:])
    # The reflectance each difference is regressed against must not share its
    # noise, so it is the mean of the two neighboring detector elements over
    # the same three lines (the edge columns have only one and are skipped).
    level = np.full(center.shape, np.nan)
    level[:, 1:-1] = (
        cube[:-2, :-2]
        + cube[:-2, 2:]
        + center[:, :-2]
        + center[:, 2:]
        + cube[2:, :-2]
        + cube[2:, 2:]
    ) / 6
    columnGroups = np.arange(samples) // columnGroup
    groupCount = int(columnGroups[-1]) + 1
    quiet = (
        _quietMask(level, quietFraction, columnGroups)
        & np.isfinite(d)
        & np.isfinite(level)
    )
    groupIndex = np.broadcast_to(columnGroups[None, :], center.shape[:2])

    count = np.zeros((bands, groupCount, _BINS))
    meanR = np.zeros_like(count)
    var = np.zeros_like(count)
    for band in range(bands):
        mask = quiet[:, :, band]
        if mask.sum() < _BINS * _MIN_BIN_SAMPLES:
            continue
        count[band], meanR[band], var[band] = _bandStatistics(
            level[:, :, band][mask],
            d[:, :, band][mask],
            groupIndex[mask],
            groupCount,
        )
    shot = np.maximum(_shotSlopes(count, meanR, var), 0.0)
    floorGroups = np.stack(
        [_fitFloor(count[i], meanR[i], var[i], shot[i]) for i in range(bands)], axis=1
    )
    return NoiseModel(floor=floorGroups[columnGroups], shot=shot)
