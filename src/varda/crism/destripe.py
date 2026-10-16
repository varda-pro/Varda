"""Residual column destriping after the atmospheric correction (spec S5).

TRR3 I/F is already RSC-filtered, so this removes what is left: patterns of
single detector elements (a column at a band) that persist along the track.
The model is multiplicative, R = S · f(x, λ), and the frame f is estimated
only from bland pixels (``blandMask``), so geology does not enter it.

**Cross-track model.** Per band and per line, ln R across the track is fitted
by a Legendre polynomial of degree 5 under least absolute deviations (IRLS,
L1, residuals in units of σ_lnR = σ/R) over the line's bland pixels: the TER
RSC model R̃. ln f(x, λ) is the median over bland lines of ln R − ln R̃
(``_bandResidual``: refitted to the destriped data, outliers set aside).

**Column scale.** Of that median only the part narrower than
``COLUMN_SCALE`` columns is kept (``_crossTrackSmooth``), the scale M11
measures striping at. A degree-5 polynomial cannot follow a real scene
across 640 columns: on FRS00031442 the rest of the median, scene that
persists along the 190 lines, passed every test below and, removed, raised
the M11 scatter from 0.51% to 0.72%. A detector element's factor is
independent of its neighbors', so the price is only the share of the
stripes that varies over more than ``COLUMN_SCALE`` columns.

**Protecting geology (S2).** ln f is split per column into a smooth part s, a
Legendre polynomial of degree 3 in λ within each filter segment
(``FILTER_BOUNDARIES_NM``; never across 1656 or 2730 nm, weighted by the
instrument σ of the median), and the rest, h, independent band to band as a
detector element's response is. Each part is removed only where it is
present and the two halves of the lines agree on it:

- s, per column: RMS over the bands of s/σ_s at least
  ``SIGNIFICANCE_SIGMAS``, and of (s₁ − s₂)/σ(s₁ − s₂) at most
  ``SPLIT_HALF_SIGMAS``;
- h, per element: |h| > ``SIGNIFICANCE_SIGMAS`` σ_h and
  |h₁ − h₂| ≤ ``ELEMENT_SPLIT_HALF_SIGMAS`` σ(h₁ − h₂).

The spec removes h always. On FRS00031442 the column-scale band-to-band
pattern of the column medians does not persist from one half of the lines
to the other (correlation −0.35 to 0.11 per wavelength range): what an
element shows over half the strip is mostly scene and wandering. Removing h
everywhere applied factors of 0.17–0.22% RMS over 1.0–2.6 µm and raised the
cross-validated M11 scatter (frame from lines 0–94, applied to 95–189) from
0.69% to 0.75%; with the test, 0.1% and 0.72%. A column offset that
a unit or a slope puts into part of the strip stays. A column with fewer
than ``MIN_LINES`` bland lines (a unit aligned with the columns over the
whole strip has none) gets no factor: its elements keep their stripes rather
than risk its geology.

**Not used: the plan's ±1-column ratio accumulation** (the median ratio of
each column to its neighbor, accumulated across the track in +x and −x from
both neighbors, 4 profiles averaged). With medians the 4 profiles are one
up to constants (the median of ln R(x)/R(x+1) is exactly minus that of
ln R(x+1)/R(x); the two directions differ by the total, which the
cross-track fit absorbs), and the accumulation is a random walk. On the
synthetic cube its error was twice the median residual's (0.46% against
0.28% at 64 lines and SNR 100); on FRS00031442 (a prototype without the
tests above) it raised the cross-validated M11 scatter from 0.69% to 0.82%.

**σ.** The frame's σ is a moving-block bootstrap over lines
(``BOOTSTRAP_RESAMPLES`` resamples of ``BOOTSTRAP_BLOCK_LINES``-line blocks,
within each half, the same lines for every band), so it sees what is common
to the bands, such as texture, and what is correlated along the track; the
same resamples give σ of the halves' difference. ``applyStripes`` adds the
frame's σ in quadrature, σ′² = (σ/f)² + (R·σ_f/f²)².

``cube`` and ``sigma`` may be ``(l, s, b)`` memmaps: they are read in blocks
of ``_BAND_BLOCK`` bands (``estimateStripes``) or ``_LINE_CHUNK`` lines
(``blandMask``), never copied whole.
"""

import numpy as np
from attrs import frozen
from numpy.polynomial import legendre
from scipy import ndimage

from varda.crism.brightness import FILTER_BOUNDARIES_NM, bandWindows, smoothBands

BLAND_WINDOW_NM = (1000.0, 2600.0)
BLAND_FRACTION = 0.5  # the quietest share of the continuum-removed variance
# (low continuum, band center, high continuum) of the band depths that make a
# pixel not bland: 1.9 µm (hydration, VB2014 BD1900), 2.3 µm (Fe/Mg-OH,
# carbonate; the prototype's BD2310) and 2.5 µm (carbonate, VB2014 BD2500_2)
BAND_DEPTHS_NM = (
    (1850.0, 1930.0, 2067.0),
    (2160.0, 2310.0, 2400.0),
    (2364.0, 2480.0, 2570.0),
)
DEPTH_SIGMAS = 3.0
SPECTRAL_DEGREE = 3
SPLIT_HALF_SIGMAS = 2.0
BOOTSTRAP_RESAMPLES = 50
BOOTSTRAP_BLOCK_LINES = 16  # the scene is correlated along the track
SIGNIFICANCE_SIGMAS = 3.0  # a part of the frame is removed only beyond this
ELEMENT_SPLIT_HALF_SIGMAS = 3.0  # h, tested per element: many tests
COLUMN_SCALE = 15  # columns; the frame keeps narrower structure (as M11 does)
MIN_LINES = 8  # bland lines a column needs for a factor
MIN_HALF_LINES = 4  # and in each half for the split-half test

_MAD_TO_SIGMA = 1.4826
_WINDOW_SHARE = 0.9  # of the column's bands in the window a bland pixel needs
_MIN_WINDOW_BANDS = 20
_DEPTH_KERNEL = 5  # bands in each band-depth median
_LINE_CHUNK = 64
_BAND_BLOCK = 8
_IRLS_ITERATIONS = 12  # half as many in each refit; outliers set aside twice
_REFITS = 3  # cross-track fits per band, each to the data destriped so far
_CLIP = 5.0  # robust spreads beyond which a pixel leaves its line's fit
_IRLS_FLOOR = 0.01  # |r|/σ below this weighs as this, so IRLS stays finite
_FIT_PIXELS_PER_TERM = 3  # bland pixels a line needs per Legendre term
_MIN_SEGMENT_BANDS = 8  # weighted bands a segment needs for its cubic
_MEDIAN_EFFICIENCY = np.sqrt(np.pi / 2)  # the σ of a median over that of a mean
_SEED = 0
_MEDIAN_BATCH = 2_000_000  # line × column × resample counts per median batch


@frozen(eq=False)
class StripeFrame:
    """The column factor frame, ``(s, b)`` per column and band.

    ``factors`` are multiplicative and applied by division (1 where nothing
    was estimated); ``sigma`` is their bootstrap σ (0 there).
    ``highFrequency`` and ``smooth`` are the two parts of ln f (h and s, 0
    where nothing was estimated); the factors hold h where
    ``highFrequencyKept`` and s where ``smoothAccepted`` ``(s,)``."""

    factors: np.ndarray
    sigma: np.ndarray
    highFrequency: np.ndarray
    highFrequencyKept: np.ndarray  # (s, b) bool: the elements whose h is removed
    smooth: np.ndarray
    smoothAccepted: np.ndarray


def _nominal(wavelengths: np.ndarray, samples: int, bands: int) -> np.ndarray:
    """``(s, b)`` per-column wavelengths from ``(s, b)`` or ``(b,)``."""
    return np.broadcast_to(np.asarray(wavelengths, dtype=np.float64), (samples, bands))


def _bandCenters(columnWavelengths: np.ndarray) -> np.ndarray:
    """``(b,)``: each band's median wavelength over the columns (NaN if none)."""
    finite = np.isfinite(columnWavelengths)
    ordered = np.sort(np.where(finite, columnWavelengths, np.inf), axis=0)
    count = finite.sum(axis=0)
    index = np.maximum(count - 1, 0) // 2
    middle = ordered[index, np.arange(ordered.shape[1])]
    return np.where(count > 0, middle, np.nan)


def _median(values: np.ndarray, axis: int) -> np.ndarray:
    """NaN-ignoring median; NaN (not a warning) where every value is NaN."""
    finite = np.isfinite(values)
    ordered = np.sort(np.where(finite, values, np.inf), axis=axis)
    count = finite.sum(axis=axis, keepdims=True)
    lower = np.take_along_axis(ordered, np.maximum(count - 1, 0) // 2, axis)
    upper = np.take_along_axis(ordered, np.maximum(count // 2, 0), axis)
    upper = np.where(count > 1, upper, lower)
    return np.squeeze(np.where(count > 0, 0.5 * (lower + upper), np.nan), axis)


def _bandDepth(
    block: np.ndarray, centers: np.ndarray, low: float, center: float, high: float
) -> np.ndarray:
    """1 − R_center / (straight continuum from R_low to R_high), each R the
    median of the ``_DEPTH_KERNEL`` bands centered on the band nearest it."""
    known = np.isfinite(centers)

    def nearest(target: float) -> tuple[int, float]:
        index = int(np.argmin(np.where(known, np.abs(centers - target), np.inf)))
        return index, float(centers[index])

    def value(index: int) -> np.ndarray:
        half = _DEPTH_KERNEL // 2
        return _median(block[..., max(index - half, 0) : index + half + 1], -1)

    (iLow, wLow), (iCenter, wCenter), (iHigh, wHigh) = (
        nearest(low),
        nearest(center),
        nearest(high),
    )
    weight = (wCenter - wLow) / (wHigh - wLow)
    continuum = (1 - weight) * value(iLow) + weight * value(iHigh)
    with np.errstate(invalid="ignore", divide="ignore"):
        return 1 - value(iCenter) / continuum


def _robustSpread(values: np.ndarray) -> float:
    """1.4826 · MAD of the finite values (NaN if there are none)."""
    values = values[np.isfinite(values)]
    if values.size == 0:
        return float("nan")
    return float(_MAD_TO_SIGMA * np.median(np.abs(values - np.median(values))))


def _depthSigma(depth: np.ndarray, quiet: np.ndarray) -> float:
    """σ of a band-depth map ``(l, s)`` around the scene's bland level: its
    noise (the robust spread of its along-track differences over √2, where
    geology, coherent along the track, mostly cancels) and, in quadrature,
    the spread between columns of the quiet pixels' median depth (the
    detector's own column pattern; a column-aligned unit is an outlier
    there, which the robust spread ignores)."""
    noise = _robustSpread(depth[1:] - depth[:-1]) / np.sqrt(2.0)
    columns = _median(np.where(quiet, depth, np.nan), 0)
    return float(np.hypot(noise, _robustSpread(columns)))


def blandMask(cube: np.ndarray, wavelengths: np.ndarray) -> np.ndarray:
    """``(l, s)``: the pixels a stripe frame may be estimated from.

    Bland pixels are in the lowest ``BLAND_FRACTION`` of their column's
    continuum-removed spectral variance over ``BLAND_WINDOW_NM`` and have no
    band depth at 1.9, 2.3 or 2.5 µm (``BAND_DEPTHS_NM``) more than
    ``DEPTH_SIGMAS`` σ above the median depth of the quiet pixels.

    - The continuum-removed spectrum is c = R/L, L the pixel's level from its
      other bands within ±10 bands, inside the filter segment
      (``brightness.py``). Its variance is taken on c divided by the column's
      mean c over the lines: the column's own stripes, which every pixel of
      the column carries, would otherwise rank its pixels by how well their
      noise cancels the stripes, and bias the frame toward 1. Ranking within
      the column keeps a noisy or stripy column from losing all its pixels.
    - σ of a depth comes from the depths themselves (``_depthSigma``: noise
      and the detector's column pattern), so the cube's own σ is not needed.

    ``wavelengths`` is ``(s, b)`` or ``(b,)`` in nm, bands ascending. A pixel
    missing more than a tenth of the window's bands that its column has data
    in is not bland. The cube is read
    twice, ``_LINE_CHUNK`` lines at a time."""
    lines, samples, bands = cube.shape
    centers = _bandCenters(_nominal(wavelengths, samples, bands))
    first, last = bandWindows(centers, bands)
    window = (centers >= BLAND_WINDOW_NM[0]) & (centers <= BLAND_WINDOW_NM[1])
    chunks = [
        slice(start, min(start + _LINE_CHUNK, lines))
        for start in range(0, lines, _LINE_CHUNK)
    ]

    def continuumRemoved(block: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            return (block / smoothBands(block, first, last, block))[..., window]

    total = np.zeros((samples, int(window.sum())))
    number = np.zeros_like(total)
    for rows in chunks:
        removed = continuumRemoved(np.asarray(cube[rows], dtype=np.float64))
        finite = np.isfinite(removed)
        total += np.where(finite, removed, 0.0).sum(axis=0)
        number += finite.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        columnMean = total / number
    columnBands = np.isfinite(columnMean).sum(axis=-1)  # (s,) bands with data

    variance = np.full((lines, samples), np.nan)
    depths = np.full((len(BAND_DEPTHS_NM), lines, samples), np.nan)
    for rows in chunks:
        block = np.asarray(cube[rows], dtype=np.float64)
        removed = continuumRemoved(block) / columnMean
        finite = np.isfinite(removed)
        count = finite.sum(axis=-1)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = np.where(finite, removed, 0.0).sum(axis=-1) / count
            spread = (np.where(finite, removed - mean[..., None], 0.0) ** 2).sum(
                axis=-1
            ) / count
        enough = (count >= _WINDOW_SHARE * columnBands) & (count >= _MIN_WINDOW_BANDS)
        variance[rows] = np.where(enough, spread, np.nan)
        for i, (low, center, high) in enumerate(BAND_DEPTHS_NM):
            depths[i, rows] = _bandDepth(block, centers, low, center, high)
    usable = np.isfinite(variance) & np.isfinite(depths).all(axis=0)
    ranked = np.where(usable, variance, np.inf)
    count = usable.sum(axis=0)
    limit = np.take_along_axis(
        np.sort(ranked, axis=0),
        np.maximum(np.ceil(BLAND_FRACTION * count).astype(int) - 1, 0)[None, :],
        axis=0,
    )
    quiet = usable & (ranked <= limit) & (count > 0)[None, :]
    bland = quiet.copy()
    for depth in depths:
        excess = depth - np.median(depth[quiet]) if quiet.any() else depth
        with np.errstate(invalid="ignore"):
            bland &= excess <= DEPTH_SIGMAS * _depthSigma(depth, quiet)
    return bland


def _bandResidual(
    reflectance: np.ndarray,
    sigma: np.ndarray,
    bland: np.ndarray,
    design: np.ndarray,
) -> np.ndarray:
    """``(..., l, s)``: ln R − ln R̃ at bland pixels (NaN elsewhere), R̃ each
    line's L1 Legendre fit across the track (IRLS on residuals in units of
    σ_lnR); leading axes (bands) are independent.

    - **Refits.** A line's fit sees only its own bland pixels, a different
      subset of the columns on every line, so it absorbs a share of each
      column's factor (about its leverage, terms / pixels: 0.06 of the
      stripes with half of 120 columns bland, on the synthetic cube). R̃ is
      therefore fitted ``_REFITS`` times, each time to ln R less the column
      factor found so far, the IRLS weights carried over (half as many
      iterations after the first).
    - **Outliers.** A pixel more than ``_CLIP`` robust spreads of its line's
      residuals from the fit (a bright boulder, a column far off) gets no
      weight, decided at the start and the middle of each fit: under L1 alone
      it would still pull the fit by a constant amount, most at the edge
      columns, and the neighbors would carry that into their factors."""
    rows = reflectance.shape[:-1]
    terms = design.shape[1]
    with np.errstate(invalid="ignore", divide="ignore"):
        logR = np.log(reflectance)
        precision = reflectance / sigma  # 1 / σ_lnR
    usable = bland & np.isfinite(logR) & np.isfinite(precision) & (precision > 0)
    enough = usable.sum(axis=-1) >= _FIT_PIXELS_PER_TERM * terms
    y = np.where(usable, logR, 0.0)
    precision = np.where(usable, precision, 0.0)
    outer = (design[:, :, None] * design[:, None, :]).reshape(design.shape[0], -1)
    identity = np.eye(terms)
    weight = precision**2
    inlier = usable
    fit = np.zeros_like(y)
    offset = np.zeros(reflectance.shape[:-2] + reflectance.shape[-1:])
    residual = np.full(reflectance.shape, np.nan)
    for refit in range(_REFITS):
        target = y - offset[..., None, :]
        iterations = _IRLS_ITERATIONS if refit == 0 else _IRLS_ITERATIONS // 2
        for iteration in range(iterations):
            normal = (weight @ outer).reshape(*rows, terms, terms)
            normal[~enough] = identity
            rhs = ((weight * target) @ design)[..., None]
            fit = np.linalg.solve(normal, rhs)[..., 0] @ design.T
            deviation = np.abs(target - fit)
            if iteration in (0, iterations // 2):
                spread = _MAD_TO_SIGMA * _median(
                    np.where(usable, deviation, np.nan), -1
                )
                inlier = usable & (deviation <= _CLIP * spread[..., None])
                enough &= inlier.sum(axis=-1) >= _FIT_PIXELS_PER_TERM * terms
            weight = np.where(
                inlier,
                precision**2 / np.maximum(deviation * precision, _IRLS_FLOOR),
                0.0,
            )
        residual = np.where(usable & enough[..., None], y - fit, np.nan)
        counted = np.isfinite(residual).sum(axis=-2) >= MIN_LINES
        offset = np.where(counted, _median(residual, -2), 0.0)
    return residual


def _medianNoise(
    reflectance: np.ndarray, sigma: np.ndarray, residual: np.ndarray
) -> np.ndarray:
    """``(..., s)``: the instrument σ of each column's median of ``residual``
    ``(..., l, s)``, √(π/2) · rms(σ/R) / √n over its n values."""
    used = np.isfinite(residual)
    with np.errstate(invalid="ignore", divide="ignore"):
        variance = np.where(used, (sigma / reflectance) ** 2, 0.0).sum(axis=-2)
        count = used.sum(axis=-2)
        return _MEDIAN_EFFICIENCY * np.sqrt(variance / count) / np.sqrt(count)


def _weightedMedian(
    ordered: np.ndarray, counts: np.ndarray, minimum: int
) -> np.ndarray:
    """``(..., s)``: the median of each column of ``ordered`` ``(l, s)``
    (sorted along lines, NaN last) with each value repeated ``counts``
    ``(..., l, s)`` times; NaN where fewer than ``minimum`` values count."""
    cumulative = np.cumsum(counts, axis=-2, dtype=np.int32)
    total = cumulative[..., -1, :]
    columns = np.arange(ordered.shape[1])

    def rank(k: np.ndarray) -> np.ndarray:  # the value at 0-based rank k
        index = (cumulative <= k[..., None, :]).sum(axis=-2)
        return ordered[np.minimum(index, len(ordered) - 1), columns]

    middle = 0.5 * (rank(np.maximum(total - 1, 0) // 2) + rank(total // 2))
    return np.where(total >= max(minimum, 1), middle, np.nan)


def _columnMedians(
    residual: np.ndarray, firstHalf: np.ndarray, resamples: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per column ``(s,)``: the median over lines of ``residual`` ``(l, s)``,
    the same over the first and the second half of the lines, and, over the
    bootstrap resamples (``resamples`` ``(n, l)``: how often each line is
    drawn), ``(n, s)`` the medians and the differences between the halves'
    medians (0 where a half has none)."""
    order = np.argsort(np.where(np.isfinite(residual), residual, np.inf), axis=0)
    rows = max(int(np.isfinite(residual).sum(axis=0).max()), 1)  # NaN sort last
    order = order[:rows]
    ordered = np.take_along_axis(residual, order, axis=0)
    finite = np.isfinite(ordered).astype(np.int32)
    inFirst = firstHalf[order].astype(np.int32)
    whole = _weightedMedian(ordered, finite, MIN_LINES)
    first = _weightedMedian(ordered, finite * inFirst, MIN_HALF_LINES)
    second = _weightedMedian(ordered, finite * (1 - inFirst), MIN_HALF_LINES)
    boot = np.empty((3, len(resamples), ordered.shape[1]))
    step = max(1, _MEDIAN_BATCH // ordered.size)  # resamples per batch
    for i in range(0, len(resamples), step):
        drawn = finite * resamples[i : i + step][:, order]  # (n, l, s)
        both = np.stack([drawn, drawn * inFirst, drawn * (1 - inFirst)])
        boot[:, i : i + step] = _weightedMedian(ordered, both, 1)
    # Only the column scale is kept: wider structure is scene to this model.
    # The same smooth part (the whole scene's) is taken from the halves and
    # the resamples, so geology in one half cannot move it there alone.
    estimated = np.isfinite(whole)
    smooth = _crossTrackSmooth(whole, estimated)
    whole, first, second, boot = (v - smooth for v in (whole, first, second, boot))
    difference = boot[1] - boot[2]
    return (
        whole,
        first,
        second,
        np.where(estimated, boot[0], np.nan),
        np.where(estimated & np.isfinite(difference), difference, 0.0),
    )


def _crossTrackSmooth(values: np.ndarray, estimated: np.ndarray) -> np.ndarray:
    """``(s,)``: the smooth cross-track part of a frame row over the
    ``estimated`` columns (NaN at the others, missing values counted as 0):
    the running median of ``COLUMN_SCALE`` columns (robust: one far-off
    column does not leak into its neighbors) smoothed by a Gaussian of
    ``COLUMN_SCALE`` / 3 columns (σ), since a running median alone steps
    whenever its window moves and would put stripes of its own back."""
    result = np.full(values.shape, np.nan)
    if estimated.sum() == 0:
        return result
    running = ndimage.median_filter(
        np.nan_to_num(values[estimated]), size=COLUMN_SCALE, mode="nearest"
    )
    result[estimated] = ndimage.gaussian_filter1d(
        running, COLUMN_SCALE / 3, mode="nearest"
    )
    return result


def _std(values: np.ndarray, axis: int) -> np.ndarray:
    """NaN-ignoring standard deviation; NaN (not a warning) where < 2 values."""
    finite = np.isfinite(values)
    count = finite.sum(axis=axis)
    filled = np.where(finite, values, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = filled.sum(axis=axis) / count
        deviation = np.where(finite, values - np.expand_dims(mean, axis), 0.0)
        return np.where(
            count > 1, np.sqrt((deviation**2).sum(axis=axis) / (count - 1)), np.nan
        )


def _smoothParts(
    targets: np.ndarray, wavelengths: np.ndarray, weight: np.ndarray
) -> np.ndarray:
    """``(m, b)``: each target spectrum's weighted Legendre polynomial of degree
    ``SPECTRAL_DEGREE`` in λ, fitted per filter segment of one column; 0 in a
    segment with fewer than ``_MIN_SEGMENT_BANDS`` weighted bands."""
    smooth = np.zeros_like(targets)
    known = np.isfinite(wavelengths) & (weight > 0)
    segment = np.searchsorted(
        FILTER_BOUNDARIES_NM, np.nan_to_num(wavelengths, nan=np.inf)
    )
    for s in np.unique(segment[known]):
        bands = known & (segment == s)
        if bands.sum() < _MIN_SEGMENT_BANDS:
            continue
        w = wavelengths[bands]
        u = 2 * (w - w.min()) / (w.max() - w.min()) - 1
        design = legendre.legvander(u, SPECTRAL_DEGREE)
        root = np.sqrt(weight[bands])
        coefficients, *_ = np.linalg.lstsq(
            design * root[:, None], (targets[:, bands] * root).T, rcond=None
        )
        smooth[:, bands] = (design @ coefficients).T
    return smooth


def _blockResamples(lines: int) -> np.ndarray:
    """``(BOOTSTRAP_RESAMPLES, l)``: how often each line is drawn when blocks
    of ``BOOTSTRAP_BLOCK_LINES`` consecutive lines are resampled with
    replacement (a moving-block bootstrap), within each half of the lines
    separately, so each resample also gives both halves; seeded."""
    rng = np.random.default_rng(_SEED)
    counts = np.zeros((BOOTSTRAP_RESAMPLES, lines), dtype=np.int32)
    middle = lines // 2
    for low, high in ((0, middle), (middle, lines)):
        size = high - low
        if size == 0:
            continue
        block = min(BOOTSTRAP_BLOCK_LINES, size)
        draws = -(-size // block)
        for i in range(BOOTSTRAP_RESAMPLES):
            for first in low + rng.integers(0, size - block + 1, draws):
                counts[i, first : first + block] += 1
    return counts


def _highFrequencyKept(
    high: np.ndarray,
    difference: np.ndarray,
    sigma: np.ndarray,
    sigmaDifference: np.ndarray,
    compared: np.ndarray,
    splitHalf: bool,
) -> np.ndarray:
    """``(b,)``: the bands of one column whose h is removed: significant,
    |h| > ``SIGNIFICANCE_SIGMAS`` σ_h, and (``splitHalf``) the same in both
    halves, |h₁ − h₂| ≤ ``ELEMENT_SPLIT_HALF_SIGMAS`` σ(h₁ − h₂). That test
    runs once per element, hundreds of thousands of times, hence 3σ."""
    with np.errstate(invalid="ignore"):
        kept = compared & (np.abs(high) > SIGNIFICANCE_SIGMAS * sigma)
        if splitHalf:
            kept &= np.abs(difference) <= ELEMENT_SPLIT_HALF_SIGMAS * sigmaDifference
    return kept


def _smoothAccepted(
    whole: np.ndarray,
    difference: np.ndarray,
    sigma: np.ndarray,
    sigmaDifference: np.ndarray,
    compared: np.ndarray,
    splitHalf: bool,
) -> bool:
    """Whether a column's smooth part s is removed. s must be present, its RMS
    over the bands at least ``SIGNIFICANCE_SIGMAS`` σ_s, and (``splitHalf``)
    the same in both halves, the RMS of (s₁ − s₂) / σ(s₁ − s₂) at most
    ``SPLIT_HALF_SIGMAS``."""
    usable = compared & (sigma > 0)
    if splitHalf:
        usable &= sigmaDifference > 0
    if usable.sum() < _MIN_SEGMENT_BANDS:
        return False
    if np.sqrt(np.mean((whole[usable] / sigma[usable]) ** 2)) < SIGNIFICANCE_SIGMAS:
        return False
    if not splitHalf:
        return True
    z = difference[usable] / sigmaDifference[usable]
    return bool(np.sqrt(np.mean(z**2)) <= SPLIT_HALF_SIGMAS)


def estimateStripes(
    cube: np.ndarray,
    sigma: np.ndarray,
    wavelengths: np.ndarray,
    bland: np.ndarray,
    *,
    legendreDegree: int = 5,
    splitHalf: bool = True,
) -> StripeFrame:
    """The stripe frame of a ``(l, s, b)`` cube (I/F after the atmospheric
    correction) and its σ, from the ``(l, s)`` bland pixels; the module
    docstring gives the method. ``wavelengths`` is ``(s, b)`` or ``(b,)`` in
    nm. ``splitHalf`` False removes the smooth part wherever it was
    estimated (where present). Bands are read ``_BAND_BLOCK`` at a time, so
    ``cube`` and ``sigma`` may be memmaps; memory beyond them is a few
    ``(l, s)`` planes per band plus two bootstrap frames
    ``(BOOTSTRAP_RESAMPLES, s, b)`` in float32 (110 MB at 640 columns). The
    bootstrap is seeded, so the same input gives the same frame. About 30 s
    for 190 × 640 × 438."""
    lines, samples, bands = cube.shape
    columnWavelengths = _nominal(wavelengths, samples, bands)
    resamples = _blockResamples(lines)
    firstHalf = np.arange(lines) < lines // 2
    design = legendre.legvander(np.linspace(-1.0, 1.0, samples), legendreDegree)
    bland = np.asarray(bland, dtype=bool)

    logF = np.full((samples, bands), np.nan)
    halves = np.full((2, samples, bands), np.nan)
    boot = np.full((BOOTSTRAP_RESAMPLES, samples, bands), np.nan, dtype=np.float32)
    bootDifference = np.zeros_like(boot)  # first half − second half
    noise = np.full((samples, bands), np.nan)  # the instrument σ of ln f
    for start in range(0, bands, _BAND_BLOCK):
        stop = min(start + _BAND_BLOCK, bands)
        # (band, l, s): the bands of a block are fitted together
        block = np.moveaxis(np.asarray(cube[:, :, start:stop], np.float64), 2, 0)
        blockSigma = np.moveaxis(np.asarray(sigma[:, :, start:stop], np.float64), 2, 0)
        residuals = _bandResidual(block, blockSigma, bland, design)
        noise[:, start:stop] = _medianNoise(block, blockSigma, residuals).T
        for k, residual in enumerate(residuals, start):
            (
                logF[:, k],
                halves[0, :, k],
                halves[1, :, k],
                boot[:, :, k],
                bootDifference[:, :, k],
            ) = _columnMedians(residual, firstHalf, resamples)

    sigmaLogF = _std(boot.astype(np.float64), axis=0)  # (s, b)
    highFrequency = np.zeros((samples, bands))
    smooth = np.zeros((samples, bands))
    sigmaApplied = np.zeros((samples, bands))
    accepted = np.zeros(samples, dtype=bool)
    kept = np.zeros((samples, bands), dtype=bool)
    for x in range(samples):
        known = np.isfinite(logF[x]) & np.isfinite(sigmaLogF[x]) & (noise[x] > 0)
        if not known.any():
            continue
        # The instrument σ weighs the bands: the bootstrap σ of one band's
        # median is itself too noisy, and a few bands would carry the fit.
        weight = np.where(known, 1.0 / np.where(known, noise[x], 1.0) ** 2, 0.0)
        # a resample or half missing a band takes the whole-scene value there
        targets = np.vstack([logF[x], halves[:, x], boot[:, x]])
        targets = np.where(np.isfinite(targets), targets, logF[x])
        targets = np.where(known, np.vstack([targets, bootDifference[:, x]]), 0.0)
        parts = _smoothParts(targets, columnWavelengths[x], weight)
        high = targets - parts
        resampled = slice(3, 3 + BOOTSTRAP_RESAMPLES)
        differences = slice(3 + BOOTSTRAP_RESAMPLES, None)
        inBoth = known & np.isfinite(halves[:, x]).all(axis=0)
        compared = inBoth if splitHalf else known
        accepted[x] = _smoothAccepted(
            parts[0],
            parts[1] - parts[2],
            _std(parts[resampled], 0),
            _std(parts[differences], 0),
            compared,
            splitHalf,
        )
        kept[x] = _highFrequencyKept(
            high[0],
            high[1] - high[2],
            _std(high[resampled], 0),
            _std(high[differences], 0),
            compared,
            splitHalf,
        )
        smooth[x] = parts[0]
        highFrequency[x] = high[0]
        applied = (
            np.where(kept[x], high[resampled], 0.0) + accepted[x] * parts[resampled]
        )
        sigmaApplied[x] = np.where(
            kept[x] | (known & accepted[x]), _std(applied, 0), 0.0
        )

    logApplied = np.where(kept, highFrequency, 0.0) + accepted[:, None] * smooth
    factors = np.exp(logApplied)
    return StripeFrame(
        factors=factors,
        sigma=factors * np.nan_to_num(sigmaApplied),
        highFrequency=highFrequency,
        highFrequencyKept=kept,
        smooth=smooth,
        smoothAccepted=accepted,
    )


def applyStripes(
    cube: np.ndarray, sigma: np.ndarray, frame: StripeFrame
) -> tuple[np.ndarray, np.ndarray]:
    """``(R / f, σ′)`` for a ``(l, s, b)`` chunk, with
    σ′² = (σ/f)² + (R·σ_f/f²)², σ_f the frame's σ. NaN stays NaN."""
    cube = np.asarray(cube, dtype=np.float64)
    sigma = np.asarray(sigma, dtype=np.float64)
    f, sigmaF = frame.factors, frame.sigma
    return cube / f, np.sqrt((sigma / f) ** 2 + (cube * sigmaF / f**2) ** 2)
