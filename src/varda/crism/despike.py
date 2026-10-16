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
strict local extremum in λ (Parente 2008) that also stands more than k errors
above the mean residual of its band neighbors. The extremum is taken on the
residual, not on R, so a spike on a steep ramp counts, and the contrast
separates a spike from a prediction that is wrong across the whole spectrum.
Real features at least three bands wide, shared by the neighbors, leave no
residual. Flagged samples are replaced by R̂ with σ set to σ_p, and every
replacement is reported, including those in the Leask window (1850–2150 nm).

NaN samples are never flagged and are never evidence for their neighbors.
"""

from __future__ import annotations

from itertools import product

import numpy as np
from attrs import frozen

_MAD_TO_SIGMA = 1.4826
_LEASK_WINDOW_NM = (1850.0, 2150.0)
_BAND_BLOCK = 48
_DISAGREEMENT = 3.0  # σ of the predictors' gap that is still noise
_MIN_NEIGHBORS = 3  # fewer valid spatial neighbors: only the spectral median
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


def _spectralMedian(block: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The median of the 4 other bands of the 5-band window along the last
    axis (the center is left out: a spike on a ramp would be its own median),
    and 1.4826 · MAD of those 4 (NaN unless all 4 exist)."""
    padded = np.pad(block, ((0, 0), (0, 0), (2, 2)), constant_values=np.nan)
    bands = block.shape[2]
    window = np.stack([padded[:, :, k : k + bands] for k in (0, 1, 3, 4)])
    middle = _nanMedian(window)
    scatter = _MAD_TO_SIGMA * _nanMedian(np.abs(window - middle))
    # a one-sided window is biased by any slope, which its scatter does not show
    whole = np.isfinite(window).all(axis=0)
    return np.where(whole, middle, np.nan), scatter


def _levelRatio(block: np.ndarray, neighborMedian: np.ndarray) -> np.ndarray:
    """Per pixel ratio of the medians over the whole spectrum, ``(l, s, 1)``."""
    both = np.isfinite(block) & np.isfinite(neighborMedian)
    own = np.where(both, block, np.nan)
    ref = np.where(both, neighborMedian, np.nan)
    own = _nanMedian(np.moveaxis(own, 2, 0))
    ref = _nanMedian(np.moveaxis(ref, 2, 0))
    with np.errstate(all="ignore"):
        ratio = own / ref
    return np.where(np.isfinite(ratio) & (ref > 0), ratio, 1.0)[..., None]


def _strictExtremum(block: np.ndarray) -> np.ndarray:
    """A sample above, or below, both band neighbors. A missing (edge or NaN)
    neighbor does not count against it, but one valid neighbor is required.
    Applied to the residual ``R - R̂``, not to R: a spike on a steep ramp is
    not an extremum of R, but it is of the residual, while a real feature the
    prediction misses is a plateau or a smooth lobe there only if it is at
    least 3 bands wide."""
    before = np.pad(block, ((0, 0), (0, 0), (1, 0)), constant_values=np.nan)[:, :, :-1]
    after = np.pad(block, ((0, 0), (0, 0), (0, 1)), constant_values=np.nan)[:, :, 1:]
    haveBefore = np.isfinite(before)
    haveAfter = np.isfinite(after)
    with np.errstate(invalid="ignore"):
        peak = (~haveBefore | (block > before)) & (~haveAfter | (block > after))
        trough = (~haveBefore | (block < before)) & (~haveAfter | (block < after))
    return (peak | trough) & (haveBefore | haveAfter)


def _contrast(residual: np.ndarray) -> np.ndarray:
    """The residual minus the mean of its valid band neighbors. A spike stands
    out from them; a prediction that is wrong across the whole spectrum (a
    pixel on a unit boundary) does not."""
    nan = np.full(residual.shape[:2] + (1,), np.nan)
    before = np.concatenate([nan, residual[:, :, :-1]], axis=2)
    after = np.concatenate([residual[:, :, 1:], nan], axis=2)
    both = np.stack([before, after])
    count = np.isfinite(both).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return residual - np.where(count > 0, np.nansum(both, axis=0) / count, np.nan)


def _despikeBlock(
    block: np.ndarray,
    sigma: np.ndarray,
    spatial: np.ndarray,
    scatter: np.ndarray,
    spectral: np.ndarray,
    spectralScatter: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """``√(σ² + σ_p²)``, and the prediction and σ_p, of one band block, given
    both predictions and their scatter."""
    # Each predictor's error: its scatter, never below the sample's own noise.
    spatialError = np.fmax(scatter, sigma)
    spectralError = np.fmax(spectralScatter, sigma)
    haveSpatial = np.isfinite(spatial) & np.isfinite(spatialError)
    haveSpectral = np.isfinite(spectral) & np.isfinite(spectralError)
    weightSpatial = np.where(haveSpatial, 1.0 / spatialError**2, 0.0)
    weightSpectral = np.where(haveSpectral, 1.0 / spectralError**2, 0.0)
    total = weightSpatial + weightSpectral
    with np.errstate(invalid="ignore", divide="ignore"):
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
        error = np.sqrt(sigma**2 + sigmaP**2)
    return error, np.stack([prediction, sigmaP])


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
    ``wavelengths`` is the ``(s, b)`` per-column map in nm (bands ascending;
    NaN for unused bands) and places the Leask window. ``firstLine`` is the
    absolute number of the first output line.
    """
    below = halo if haloBelow is None else haloBelow
    stop = cube.shape[0] - below
    outLines = stop - halo
    samples, bands = cube.shape[1:]
    # one NaN line/column of padding around the output lines, filled from the halo
    padded = np.full((outLines + 2, samples + 2, bands), np.nan)
    first, last = max(halo - 1, 0), min(stop + 1, cube.shape[0])
    padded[first - halo + 1 : last - halo + 1, 1:-1] = cube[first:last]
    centerSigma = sigma[halo:stop]
    padWavelengths = np.full((samples + 2, bands), np.nan)
    padWavelengths[1:-1] = wavelengths
    slope = _slope(padded, padWavelengths)

    outCube = cube[halo:stop].astype(np.float64, copy=True)
    outSigma = centerSigma.astype(np.float64, copy=True)

    # the spatial prediction needs the pixel's level over the whole spectrum,
    # so its neighbor statistics come from a first pass over the band blocks
    blocks = [slice(i, i + _BAND_BLOCK) for i in range(0, bands, _BAND_BLOCK)]
    neighborMedian = np.empty(outCube.shape)
    neighborScatter = np.empty(outCube.shape)
    enough = np.empty(outCube.shape, dtype=bool)
    for sel in blocks:
        neighbors = _spatialStack(
            padded[:, :, sel],
            slope[:, :, sel],
            padWavelengths[:, sel],
            wavelengths[:, sel],
        )
        middle = _nanMedian(neighbors)
        neighborMedian[:, :, sel] = middle
        neighborScatter[:, :, sel] = _MAD_TO_SIGMA * _nanMedian(
            np.abs(neighbors - middle)
        )
        enough[:, :, sel] = np.isfinite(neighbors).sum(axis=0) >= _MIN_NEIGHBORS
    ratio = _levelRatio(outCube, neighborMedian)
    spatial = np.where(enough, neighborMedian * ratio, np.nan)
    scatter = neighborScatter * ratio

    spectral = np.empty(outCube.shape)
    spectralScatter = np.empty(outCube.shape)
    for sel in blocks:  # the window reaches 2 bands past the block
        wide = slice(max(sel.start - 2, 0), min(sel.stop + 2, bands))
        skip = sel.start - wide.start
        middle, spread = _spectralMedian(outCube[:, :, wide])
        spectral[:, :, sel] = middle[:, :, skip : skip + _BAND_BLOCK]
        spectralScatter[:, :, sel] = spread[:, :, skip : skip + _BAND_BLOCK]

    error = np.empty(outCube.shape)
    residual = np.full(outCube.shape, np.nan)
    replacement = np.empty((2, *outCube.shape))
    for sel in blocks:
        block = outCube[:, :, sel]
        error[:, :, sel], replacement[:, :, :, sel] = _despikeBlock(
            block,
            centerSigma[:, :, sel],
            spatial[:, :, sel],
            scatter[:, :, sel],
            spectral[:, :, sel],
            spectralScatter[:, :, sel],
        )
        residual[:, :, sel] = block - replacement[0, :, :, sel]
    flags = (
        (np.abs(residual) > threshold * error)
        & _strictExtremum(residual)
        & (np.abs(_contrast(residual)) > threshold * error)
    )
    outCube[flags] = replacement[0][flags]
    outSigma[flags] = replacement[1][flags]

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
