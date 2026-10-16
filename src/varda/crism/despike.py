"""Despiking against the explicit noise model (spec S3).

Each sample is predicted from its neighbors by two estimators: the median of
the 8 spatial neighbors' spectra (scaled to the pixel's level by the ratio of
medians over the whole spectrum, and moved to the pixel's column wavelengths),
and a spectral median of the 4 other bands of the 5-band window. Each one's
error is its 1.4826 · MAD scatter, floored at the sample's own σ, and R̂ is
their inverse-variance combination, so a pixel on a unit boundary (large
spatial scatter) is judged by its spectrum and a cliff in the spectrum by the
neighbors. σ_p is that combined error, widened by whatever part of the gap
between the two predictors exceeds 3 combined errors (when they disagree, one
is wrong: a pixel on a unit boundary, a cliff in the spectrum). A sample is a
spike when ``|R - R̂| / √(σ² + σ_p²) > k`` and its residual ``R - R̂`` is a
strict local extremum in λ (Parente 2008) that is isolated: each band
neighbor's residual is under ``_ISOLATION`` of its own. The extremum is taken
on the residual, not on R, so a spike on a steep ramp counts, and isolation
separates a spike from a prediction that is wrong across the whole spectrum
or a feature three bands wide. Real features shared by the neighbors leave no
residual. Flagged samples are replaced by R̂ with σ set to σ_p, and every
replacement is reported, including those in the Leask window (1850–2150 nm).

The IR detector's order-sorting filters join at ``FILTER_BOUNDARIES_NM``, where
spectra step per column. Bands on either side are separate segments: the
spectral median never reaches across a boundary (so within two bands of one
only the spatial predictor is used) and the extremum and isolation tests do not
compare across it.

NaN samples are never flagged and are never evidence for their neighbors.
"""

from __future__ import annotations

from itertools import product

import numpy as np
from attrs import frozen

_MAD_TO_SIGMA = 1.4826
_LEASK_WINDOW_NM = (1850.0, 2150.0)
# Where the IR detector's order-sorting filters join: spectra step there, per
# column, between the bands either side. 1656 nm falls between 1653 and 1660 nm
# (the 1.65 µm join); 2730 nm is mid-gap of the ≈2700 nm cliff, where the
# detector rows 173–193 are missing (2701 → 2761 nm).
FILTER_BOUNDARIES_NM = (1656.0, 2730.0)
_BAND_BLOCK = 16
_DISAGREEMENT = 3.0  # σ of the predictors' gap that is still noise
_ISOLATION = 0.4  # a spike's band neighbors keep under this share of its residual
_TRANSIENT = (
    0.5  # a spike differs from its own line neighbors by this share of k errors
)
_MIN_NEIGHBORS = 3  # fewer valid spatial neighbors: only the spectral median
_LEVEL_LINES = 8  # lines per step of the level ratio, to bound its temporaries
_NEIGHBOR_OFFSETS = [o for o in product(range(3), repeat=2) if o != (1, 1)]


@frozen
class SpikeReport:
    """What despiking replaced. ``positions`` rows are (line, sample, band)
    with absolute line numbers."""

    perPixel: np.ndarray  # (l, s) uint16, flags per pixel
    positions: np.ndarray  # (n, 3) int32
    leaskCount: np.ndarray  # (l, s) uint16, flags inside 1850–2150 nm

    def merged(self, other: SpikeReport) -> SpikeReport:
        """This report followed by ``other``, the next chunk of lines."""
        return SpikeReport(
            perPixel=np.vstack([self.perPixel, other.perPixel]),
            positions=np.vstack([self.positions, other.positions]),
            leaskCount=np.vstack([self.leaskCount, other.leaskCount]),
        )


def _nanMedian(stack: np.ndarray) -> np.ndarray:
    """Median over axis 0 ignoring NaN (NaN where there is no value), and the
    count of values."""
    ordered = np.sort(stack, axis=0)  # NaN sorts last
    count = np.isfinite(stack).sum(axis=0)
    low = np.maximum((count - 1) // 2, 0)[None]
    high = np.maximum(count // 2, 0)[None]
    middle = 0.5 * (
        np.take_along_axis(ordered, low, axis=0)[0]
        + np.take_along_axis(ordered, high, axis=0)[0]
    )
    return np.where(count > 0, middle, np.nan)


def _slope(padded: np.ndarray, padWavelengths: np.ndarray) -> np.ndarray:
    """dR/dλ along the bands by central differences (0 where undefined)."""
    nan = np.full(padded[:, :, :1].shape, np.nan)
    ahead = np.concatenate([padded[:, :, 1:], nan], axis=2)
    behind = np.concatenate([nan, padded[:, :, :-1]], axis=2)
    wNan = np.full(padWavelengths[:, :1].shape, np.nan)
    wAhead = np.concatenate([padWavelengths[:, 1:], wNan], axis=1)
    wBehind = np.concatenate([wNan, padWavelengths[:, :-1]], axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        slope = (ahead - behind) / (wAhead - wBehind)[None]
    return np.where(np.isfinite(slope), slope, 0.0)


def _spatialStack(
    padded: np.ndarray,
    slope: np.ndarray,
    padWavelengths: np.ndarray,
    wavelengths: np.ndarray,
) -> np.ndarray:
    """The 8 neighbors ``(8, lines, samples, b)``, from a NaN-padded cube, each
    moved to the pixel's own column wavelengths by a first-order step along
    the neighbor's own slope: a sharp line shifts with the column's smile,
    and the neighbor's column is not the pixel's."""
    lines = padded.shape[0] - 2
    samples = wavelengths.shape[0]
    stack = []
    for i, j in _NEIGHBOR_OFFSETS:
        shift = wavelengths - padWavelengths[j : j + samples]
        shift = np.where(np.isfinite(shift), shift, 0.0)
        stack.append(
            padded[i : i + lines, j : j + samples]
            + shift[None] * slope[i : i + lines, j : j + samples]
        )
    return np.stack(stack)


def _segments(wavelengths: np.ndarray) -> np.ndarray:
    """The filter segment of each band ``(b,)``, from the columns' median
    wavelengths (the same for every column: the join is a detector row)."""
    middle = _nanMedian(wavelengths)
    return np.searchsorted(FILTER_BOUNDARIES_NM, np.nan_to_num(middle, nan=np.inf))


def _sameSegment(segment: np.ndarray, offset: int) -> np.ndarray:
    """Whether the band ``offset`` away is in the same segment ``(b,)``;
    False past the ends."""
    other = np.full(segment.shape, -1)
    if offset > 0:
        other[:-offset] = segment[offset:]
    else:
        other[-offset:] = segment[:offset]
    return other == segment


def _spectralMedian(
    block: np.ndarray, segment: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """The median of the 4 other bands of the 5-band window along the last
    axis (the center is left out: a spike on a ramp would be its own median),
    and 1.4826 · MAD of those 4. NaN unless all 4 exist in the band's filter
    segment: a one-sided window is biased by any slope, which its scatter does
    not show."""
    bands = block.shape[2]
    padded = np.pad(block, ((0, 0), (0, 0), (2, 2)), constant_values=np.nan)
    window = []
    for k in (0, 1, 3, 4):
        values = padded[:, :, k : k + bands].copy()
        values[:, :, ~_sameSegment(segment, k - 2)] = np.nan
        window.append(values)
    window = np.stack(window)
    middle = _nanMedian(window)
    scatter = _MAD_TO_SIGMA * _nanMedian(np.abs(window - middle))
    whole = np.isfinite(window).all(axis=0)
    return np.where(whole, middle, np.nan), scatter


def _levelRatio(block: np.ndarray, neighborMedian: np.ndarray) -> np.ndarray:
    """Per pixel ratio of the medians over the whole spectrum, ``(l, s, 1)``."""
    ratio = np.ones(block.shape[:2])
    for i in range(0, block.shape[0], _LEVEL_LINES):
        lines = slice(i, i + _LEVEL_LINES)
        both = np.isfinite(block[lines]) & np.isfinite(neighborMedian[lines])
        own = _nanMedian(np.moveaxis(np.where(both, block[lines], np.nan), 2, 0))
        ref = _nanMedian(
            np.moveaxis(np.where(both, neighborMedian[lines], np.nan), 2, 0)
        )
        with np.errstate(all="ignore"):
            level = own / ref
        ratio[lines] = np.where(np.isfinite(level) & (ref > 0), level, 1.0)
    return ratio[..., None]


def _bandNeighbors(
    residual: np.ndarray, segment: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """The residual of the band before and after, NaN past an end of the
    spectrum or a filter boundary."""
    nan = np.full(residual.shape[:2] + (1,), np.nan)
    before = np.concatenate([nan, residual[:, :, :-1]], axis=2)
    after = np.concatenate([residual[:, :, 1:], nan], axis=2)
    before[:, :, ~_sameSegment(segment, -1)] = np.nan
    after[:, :, ~_sameSegment(segment, 1)] = np.nan
    return before, after


def _isolatedExtremum(
    residual: np.ndarray, before: np.ndarray, after: np.ndarray
) -> np.ndarray:
    """A residual strictly above, or below, both band neighbors, and at least
    ``1 / _ISOLATION`` times larger than each. A missing neighbor does not
    count against it, but one is required. Taken on the residual, not on R: a
    spike on a steep ramp is not an extremum of R, but it is of the residual."""
    haveBefore, haveAfter = np.isfinite(before), np.isfinite(after)
    with np.errstate(invalid="ignore"):
        peak = (~haveBefore | (residual > before)) & (~haveAfter | (residual > after))
        trough = (~haveBefore | (residual < before)) & (~haveAfter | (residual < after))
        small = _ISOLATION * np.abs(residual)
        isolated = (~haveBefore | (np.abs(before) < small)) & (
            ~haveAfter | (np.abs(after) < small)
        )
    return (peak | trough) & isolated & (haveBefore | haveAfter)


def _transient(
    block: np.ndarray,
    above: np.ndarray,
    below: np.ndarray,
    residual: np.ndarray,
    limit: np.ndarray,
) -> np.ndarray:
    """A spike is not a column defect: the sample also stands out, in the
    direction of its residual, from the same detector element on the lines
    before and after (a missing line does not count against it). A defect
    that persists down the lines agrees with them."""
    direction = np.sign(residual)
    with np.errstate(invalid="ignore"):
        return (np.isnan(above) | ((block - above) * direction > limit)) & (
            np.isnan(below) | ((block - below) * direction > limit)
        )


def _predict(
    block: np.ndarray,
    sigma: np.ndarray,
    spatial: np.ndarray,
    scatter: np.ndarray,
    spectral: np.ndarray,
    spectralScatter: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """The combined prediction and its error σ_p for one band block, given both
    predictions and their scatter."""
    # Each predictor's error: its scatter, never below the sample's own noise.
    spatialError = np.fmax(scatter, sigma)
    spectralError = np.fmax(spectralScatter, sigma)
    haveSpatial = np.isfinite(spatial) & np.isfinite(spatialError)
    haveSpectral = np.isfinite(spectral) & np.isfinite(spectralError)
    with np.errstate(invalid="ignore", divide="ignore"):
        weightSpatial = np.where(haveSpatial, 1.0 / spatialError**2, 0.0)
        weightSpectral = np.where(haveSpectral, 1.0 / spectralError**2, 0.0)
        total = weightSpatial + weightSpectral
        prediction = (
            np.where(haveSpatial, spatial, 0.0) * weightSpatial
            + np.where(haveSpectral, spectral, 0.0) * weightSpectral
        ) / total
        # predictors that disagree beyond their errors are not both right
        # (a pixel on a unit boundary, a cliff in the spectrum): their gap
        # joins the error, so only a sample far from both is a spike
        gap = np.fmax(
            np.abs(spatial - spectral)
            - _DISAGREEMENT * np.hypot(spatialError, spectralError),
            0.0,
        )
        sigmaP = np.sqrt(
            1.0 / total + np.where(haveSpatial & haveSpectral, gap, 0.0) ** 2
        )
    return prediction, sigmaP


def despike(
    cube: np.ndarray,
    sigma: np.ndarray,
    wavelengths: np.ndarray,
    threshold: float = 5.0,
    firstLine: int = 0,
    halo: int = 1,
    haloBelow: int | None = None,
) -> tuple[np.ndarray, np.ndarray, SpikeReport]:
    """Despike a chunk: ``(cube, sigma, report)`` without the halo lines.

    ``cube`` and ``sigma`` ``(lines, s, b)`` carry ``halo`` neighbor lines
    above and ``haloBelow`` below (default: the same; pass fewer, down to 0,
    at the first and last chunk of a scene). Only the 8 spatial neighbors are
    used, so ``halo = 1`` makes chunked output identical to a whole-scene run.
    Negative halos, or no line left between them, raise ``ValueError``.
    ``wavelengths`` is the ``(s, b)`` per-column map in nm (bands ascending;
    NaN for unused bands); it places the Leask window and the filter segments.
    ``firstLine`` is the absolute number of the first output line. Peak memory
    is about five copies of the cube beyond its input; the rest is per block.
    """
    haloAfter = halo if haloBelow is None else haloBelow
    if halo < 0 or haloAfter < 0:
        raise ValueError(f"The halos must not be negative, got {halo} and {haloAfter}.")
    stop = cube.shape[0] - haloAfter
    outLines = stop - halo
    if outLines < 1:
        raise ValueError(
            f"{cube.shape[0]} lines leave none between halos of {halo} and {haloAfter}."
        )
    if sigma.shape != cube.shape:
        raise ValueError(f"sigma {sigma.shape} must match the cube {cube.shape}.")
    samples, bands = cube.shape[1:]
    segment = _segments(wavelengths)
    blocks = [slice(i, i + _BAND_BLOCK) for i in range(0, bands, _BAND_BLOCK)]

    outCube = cube[halo:stop].astype(np.float64, copy=True)
    # First pass: the spatial neighbors' median and scatter. One NaN line/column
    # of padding surrounds the output lines, filled from the halo.
    padded = np.full((outLines + 2, samples + 2, bands), np.nan)
    first, last = max(halo - 1, 0), min(stop + 1, cube.shape[0])
    padded[first - halo + 1 : last - halo + 1, 1:-1] = cube[first:last]
    padWavelengths = np.full((samples + 2, bands), np.nan)
    padWavelengths[1:-1] = wavelengths
    slope = _slope(padded, padWavelengths)
    # these two arrays become the prediction and σ_p, block by block, below
    prediction = np.empty(outCube.shape)
    sigmaP = np.empty(outCube.shape)
    for sel in blocks:
        neighbors = _spatialStack(
            padded[:, :, sel],
            slope[:, :, sel],
            padWavelengths[:, sel],
            wavelengths[:, sel],
        )
        middle = _nanMedian(neighbors)
        enough = np.isfinite(neighbors).sum(axis=0) >= _MIN_NEIGHBORS
        prediction[:, :, sel] = np.where(enough, middle, np.nan)
        sigmaP[:, :, sel] = _MAD_TO_SIGMA * _nanMedian(np.abs(neighbors - middle))
    del padded, slope
    ratio = _levelRatio(outCube, prediction)

    # Second pass: both predictions, their combination and the residual.
    residual = np.full(outCube.shape, np.nan)
    for sel in blocks:  # the spectral window reaches 2 bands past the block
        wide = slice(max(sel.start - 2, 0), min(sel.stop + 2, bands))
        skip = sel.start - wide.start
        middle, spread = _spectralMedian(outCube[:, :, wide], segment[wide])
        keep = slice(skip, skip + outCube[:, :, sel].shape[2])
        block = outCube[:, :, sel]
        prediction[:, :, sel], sigmaP[:, :, sel] = _predict(
            block,
            sigma[halo:stop, :, sel],
            prediction[:, :, sel] * ratio,
            sigmaP[:, :, sel] * ratio,
            middle[:, :, keep],
            spread[:, :, keep],
        )
        residual[:, :, sel] = block - prediction[:, :, sel]

    # Third pass: flag, a band block at a time (the neighbor test reaches 1 band).
    flags = np.zeros(outCube.shape, dtype=bool)
    for sel in blocks:
        wide = slice(max(sel.start - 1, 0), min(sel.stop + 1, bands))
        skip = sel.start - wide.start
        before, after = _bandNeighbors(residual[:, :, wide], segment[wide])
        keep = slice(skip, skip + residual[:, :, sel].shape[2])
        error = np.hypot(sigma[halo:stop, :, sel], sigmaP[:, :, sel])
        with np.errstate(invalid="ignore"):
            over = np.abs(residual[:, :, sel]) > threshold * error
        above = np.full(error.shape, np.nan)  # the same element, a line earlier/later
        below = np.full(error.shape, np.nan)
        if halo > 0:
            above[:] = cube[halo - 1 : stop - 1, :, sel]
        else:
            above[1:] = cube[: stop - 1, :, sel]
        if haloAfter > 0:
            below[:] = cube[halo + 1 : stop + 1, :, sel]
        else:
            below[:-1] = cube[halo + 1 : stop, :, sel]
        flags[:, :, sel] = (
            over
            & _isolatedExtremum(
                residual[:, :, sel], before[:, :, keep], after[:, :, keep]
            )
            & _transient(
                outCube[:, :, sel],
                above,
                below,
                residual[:, :, sel],
                _TRANSIENT * threshold * error,
            )
        )
    outSigma = sigma[halo:stop].astype(np.float64, copy=True)
    outCube[flags] = prediction[flags]
    outSigma[flags] = sigmaP[flags]

    low, high = _LEASK_WINDOW_NM
    with np.errstate(invalid="ignore"):
        inWindow = (wavelengths >= low) & (wavelengths <= high)
    lines, columns, bandIndex = np.nonzero(flags)
    positions = np.column_stack([lines + firstLine, columns, bandIndex]).astype(
        np.int32
    )
    return (
        outCube,
        outSigma,
        SpikeReport(
            perPixel=flags.sum(axis=2).astype(np.uint16),
            positions=positions,
            leaskCount=(flags & inWindow[None]).sum(axis=2).astype(np.uint16),
        ),
    )
