"""Finding where one image's features sit in another, to sub-pixel accuracy.

Shifts are (dx, dy) in pixels: a feature at p in the reference is at p + d in
the moving image.

- ``tileShift``: phase correlation of windowed tiles, upsampled 100×
  (scikit-image); about 0.1 px.
- ``refineShift``: the phase-plane fit of Leprince et al. (2007, IEEE TGRS
  45(6), "Automatic and precise orthorectification, coregistration, and
  subpixel correlation of satellite images"), as COSI-Corr does it,
  re-implemented from the paper: both patches are weighted by a raised-cosine
  window; the normalized cross-power spectrum Q is then, for a pure shift,
  the plane wave exp(j ω·d); d is fitted to Q by weighted least squares over
  the frequencies with enough energy (a mask that drops the noisy ones), and
  the fit's agreement gives an SNR between 0 and 1. A window fixed on both
  patches biases the fit toward zero shift, so the moving patch's window is
  moved with the estimate and the fit repeated until it settles: then the
  windowed patches are exact shifted copies. About 1/50 px.
"""

from __future__ import annotations

import math
import tempfile
import warnings
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
from affine import Affine
from attrs import field, frozen
from scipy import ndimage, optimize
from skimage.feature import ORB, match_descriptors
from skimage.registration import phase_cross_correlation

from varda.common.parameter import LabeledEnum
from varda.mosaicking.backends.asp import AspError, AspInstall, imageAlign
from varda.mosaicking.bands import DEFAULT_BAND_COUNT, bandSnr, chooseBands
from varda.mosaicking.grid import TargetGrid, overlapGrid
from varda.mosaicking.model import MosaicInput, MosaicProject
from varda.mosaicking.render import readCube, sampleOnGrid
from varda.mosaicking.transforms import TransformModel

UPSAMPLE = 100
DEFAULT_TILE = 64
MAX_TILES_PER_BAND = 400  # the tile step grows to stay under this
MIN_QUALITY = 0.6  # tiles that correlate less once aligned are dropped
MIN_TILE_SNR = 2.0  # tiles with less texture than this over noise are skipped
COARSE_SIZE = 512  # the coarse offset is found on images this size at most
FEATURE_SIZE = 2000  # ORB runs on images this size at most
MIN_PHASE_POINTS = 6  # fewer than this and AUTO falls back to features
SNR_SAMPLE = 256  # band SNRs are estimated on about this many rows and columns
WINDOW_BETA = 0.5  # the raised-cosine window's roll-off
MASK_FACTOR = (
    0.5  # keep frequencies whose log energy (from the peak) is within this × the mean
)


def tileShift(reference: np.ndarray, moving: np.ndarray) -> tuple[np.ndarray, float]:
    """The shift (dx, dy) of ``moving`` relative to ``reference`` (same-sized,
    finite tiles) by upsampled phase correlation, and its quality: the
    correlation of the tiles once aligned (1 is a perfect match)."""
    window = np.outer(
        raisedCosine(reference.shape[0]), raisedCosine(reference.shape[1])
    )
    registration, _, _ = phase_cross_correlation(
        (reference - reference.mean()) * window,
        (moving - moving.mean()) * window,
        upsample_factor=UPSAMPLE,
        normalization=None,
    )
    dy, dx = -registration  # scikit-image returns the shift that undoes it
    return np.array([dx, dy]), alignedCorrelation(reference, moving, (dx, dy))


def alignedCorrelation(
    reference: np.ndarray, moving: np.ndarray, shift: tuple[float, float]
) -> float:
    """The correlation coefficient of the tiles after moving ``moving`` back
    by ``shift``, over the part both still cover."""
    dx, dy = shift
    back = ndimage.shift(moving, (-dy, -dx), order=1, mode="nearest")
    margin = int(np.ceil(max(abs(dx), abs(dy)))) + 1
    if 2 * margin >= min(reference.shape):
        return 0.0
    inner = (slice(margin, -margin), slice(margin, -margin))
    a, b = reference[inner].ravel(), back[inner].ravel()
    a, b = a - a.mean(), b - b.mean()
    denominator = np.sqrt((a @ a) * (b @ b))
    return float(a @ b / denominator) if denominator > 0 else 0.0


def raisedCosine(
    size: int, beta: float = WINDOW_BETA, offset: float = 0.0
) -> np.ndarray:
    """A 1-D raised-cosine window: 1 over the middle ``1 - beta`` of its
    length, rolling off to 0 at the ends; centered ``offset`` pixels off the
    middle."""
    u = np.abs(np.arange(size) - (size - 1) / 2 - offset) / (size / 2)
    flat = 1.0 - beta
    window = np.where(u >= 1.0, 0.0, 1.0)
    rolling = (u > flat) & (u < 1.0)
    window[rolling] = 0.5 * (1 + np.cos(np.pi * (u[rolling] - flat) / beta))
    return window


MAX_ITERATIONS = 10
CONVERGED = 1e-3  # px


def refineShift(
    reference: np.ndarray, moving: np.ndarray, initial: tuple[float, float]
) -> tuple[np.ndarray, float]:
    """The shift (dx, dy) of ``moving`` relative to ``reference`` by the
    phase-plane fit, starting from ``initial`` (within about half a pixel of
    the answer, e.g. from ``tileShift``), and its SNR (0-1)."""
    shift = np.asarray(initial, dtype=np.float64).copy()
    snr = 0.0
    for _ in range(MAX_ITERATIONS):
        previous = shift
        shift, snr = _phasePlane(reference, moving, shift)
        if np.hypot(*(shift - previous)) < CONVERGED:
            break
    return shift, snr


def _phasePlane(
    reference: np.ndarray, moving: np.ndarray, shift: np.ndarray
) -> tuple[np.ndarray, float]:
    """One phase-plane fit, with the moving patch's window moved by
    ``shift`` (the current estimate, also the starting point), and its SNR."""
    rows, cols = reference.shape
    window = np.outer(raisedCosine(rows), raisedCosine(cols))
    movingWindow = np.outer(
        raisedCosine(rows, offset=shift[1]), raisedCosine(cols, offset=shift[0])
    )
    f1 = np.fft.fft2((reference - reference.mean()) * window)
    f2 = np.fft.fft2((moving - moving.mean()) * movingWindow)
    cross = f1 * np.conj(f2)
    magnitude = np.abs(cross)
    q = np.where(magnitude > 0, cross / np.where(magnitude > 0, magnitude, 1), 0)
    # Frequency mask: the frequencies whose energy (log, relative to the
    # strongest) is above MASK_FACTOR × its mean carry the signal
    logEnergy = np.log10(magnitude + 1e-30)
    logEnergy -= logEnergy.max()
    weight = (logEnergy > MASK_FACTOR * logEnergy.mean()).astype(np.float64)
    weight[0, 0] = 0.0  # the mean was removed
    wy, wx = np.meshgrid(
        2 * np.pi * np.fft.fftfreq(rows),
        2 * np.pi * np.fft.fftfreq(cols),
        indexing="ij",
    )
    keep = weight > 0
    if not keep.any():  # a featureless patch: nothing to fit
        return np.asarray(shift, dtype=np.float64), 0.0
    qk, wxk, wyk, root = q[keep], wx[keep], wy[keep], np.sqrt(weight[keep])

    def residual(d: np.ndarray) -> np.ndarray:
        difference = qk - np.exp(1j * (wxk * d[0] + wyk * d[1]))
        return np.concatenate([root * difference.real, root * difference.imag])

    fit = optimize.least_squares(residual, shift, method="lm")
    misfit = float(np.sum(fit.fun**2))
    snr = 1.0 - misfit / (4.0 * float(weight.sum())) if weight.sum() else 0.0
    return fit.x, float(np.clip(snr, 0.0, 1.0))


# --- aligning images ---------------------------------------------------------------


class MatchMethod(LabeledEnum):
    AUTO = "Automatic (phase correlation, features if that fails)"
    PHASE = "Phase correlation (shifts, small warps)"
    FEATURES = "Features (ORB; rotation and scale)"
    ASP = "Ames Stereo Pipeline (image_align)"
    LOFTR = "Learned features (LoFTR; needs PyTorch)"
    SIFT = "SIFT features (OpenCV; needs PyTorch)"


# A matcher run outside Varda's process (the PyTorch runtime's worker): two
# images in, points in each (pixels) and each match's confidence out
ExternalMatcher = Callable[
    [np.ndarray, np.ndarray, str], tuple[np.ndarray, np.ndarray, np.ndarray]
]
EXTERNAL = {MatchMethod.LOFTR: "loftr", MatchMethod.SIFT: "sift"}


@frozen
class MatchSettings:
    bands: tuple[int, ...] | None = None  # None: choose by SNR
    bandCount: int = DEFAULT_BAND_COUNT
    tileSize: int = DEFAULT_TILE
    refine: bool = True  # the phase-plane fit after phase correlation
    method: MatchMethod = MatchMethod.AUTO
    asp: AspInstall | None = None  # for MatchMethod.ASP
    externalMatcher: ExternalMatcher | None = None  # for LOFTR and SIFT


@frozen
class DisplacementField:
    """Where the moving image's features sit relative to the reference's,
    tile by tile over their overlap (as COSI-Corr's output): east and north
    displacement in map units, each match's quality (0-1: the tiles'
    correlation once aligned) and its phase-plane SNR (0-1; NaN without the
    refinement). NaN where a tile gave no confident match."""

    referenceKey: int
    movingKey: int
    dx: np.ndarray
    dy: np.ndarray
    quality: np.ndarray
    transform: Affine  # field pixel (col, row) → map; pixels sit on the tile centers
    phaseSnr: np.ndarray


# (reference pixel, moving pixel, quality, band, phase-plane SNR or NaN)
MatchedPoint = tuple[tuple[float, float], tuple[float, float], float, int, float]
# The image's transform, as image_align names it (warps are screened with a
# homography; Varda fits them)
ASP_MODELS = {
    TransformModel.TRANSLATION: "translation",
    TransformModel.RIGID: "rigid",
    TransformModel.SIMILARITY: "similarity",
    TransformModel.AFFINE: "affine",
    TransformModel.PROJECTIVE: "projective",
}


@frozen
class AutoAlignResult:
    points: tuple[tuple[int, MatchedPoint], ...]  # (reference key, match)
    bands: tuple[int, ...]
    snr: np.ndarray  # every band's SNR (moving image)
    method: MatchMethod  # what was used in the end
    fields: tuple[DisplacementField, ...] = field(factory=tuple)
    message: str = ""

    @property
    def added(self) -> int:
        return len(self.points)


def autoAlign(
    project: MosaicProject,
    movingKey: int,
    referenceKeys: Sequence[int],
    settings: MatchSettings = MatchSettings(),
    progress: Callable[[float, str], None] = lambda fraction, message: None,
) -> AutoAlignResult:
    """Find tie points between an input and each reference and put them in
    the project in place of its earlier automatic points. Fit them
    afterwards (``MosaicProject.fit``, robustly)."""
    result = findTiePoints(project, movingKey, referenceKeys, settings, progress)
    applyTiePoints(project, movingKey, result)
    return result


def applyTiePoints(
    project: MosaicProject, movingKey: int, result: AutoAlignResult
) -> None:
    """Replace an input's automatic tie points with ``result``'s."""
    project.removeAutoTiePoints(movingKey)
    for referenceKey, (reference, moving, quality, band, snr) in result.points:
        project.addTiePoint(
            movingKey,
            moving,
            referenceKey,
            reference,
            auto=True,
            quality=quality,
            band=band,
            phaseSnr=snr,
        )


def findTiePoints(
    project: MosaicProject,
    movingKey: int,
    referenceKeys: Sequence[int],
    settings: MatchSettings = MatchSettings(),
    progress: Callable[[float, str], None] = lambda fraction, message: None,
) -> AutoAlignResult:
    """Tie points between an input and each reference (where they overlap),
    matched on the best bands. Reads the project without changing it, so it
    can run in the background."""
    moving = project.input(movingKey)
    snr = estimateSnr(moving)
    bands = (
        tuple(settings.bands)
        if settings.bands is not None
        else tuple(
            chooseBands(
                snr,
                moving.image.bandModel.centers,
                settings.bandCount,
                moving.image.bandModel.badBands,
            )
        )
    )
    if not bands:
        return AutoAlignResult(
            (), bands, snr, settings.method, message="No band has usable contrast."
        )
    found: list[tuple[int, MatchedPoint]] = []
    fields, methods, missing = [], set(), []
    references = [k for k in referenceKeys if k != movingKey]
    for i, referenceKey in enumerate(references):
        grid = overlapGrid(project, referenceKey, movingKey)
        if grid is None or min(grid.width, grid.height) < settings.tileSize:
            missing.append(project.input(referenceKey).image.name)
            continue

        def report(fraction: float, message: str, i: int = i) -> None:
            progress((i + fraction) / len(references), message)

        points, displacement, method = matchPair(
            project.input(referenceKey), moving, grid, bands, settings, report
        )
        found += [(referenceKey, point) for point in points]
        methods.add(method)
        if displacement is not None:
            fields.append(displacement)
    method = methods.pop() if len(methods) == 1 else settings.method
    message = (
        f"No overlap with {', '.join(missing)} (at least one tile, "
        f"{settings.tileSize} px, is needed)."
        if missing
        else ""
    )
    return AutoAlignResult(tuple(found), bands, snr, method, tuple(fields), message)


def estimateSnr(item: MosaicInput) -> np.ndarray:
    """Every band's SNR, on a subsample of the image for speed."""
    image = item.image
    step = max(1, int(math.ceil(max(image.height, image.width) / SNR_SAMPLE)))
    cube = np.ma.filled(
        np.ma.asarray(image.getData()[::step, ::step], dtype=np.float32), np.nan
    )
    if image.nodata is not None:
        cube[cube == image.nodata] = np.nan
    return bandSnr(cube)


def matchPair(
    reference: MosaicInput,
    moving: MosaicInput,
    grid: TargetGrid,
    bands: Sequence[int],
    settings: MatchSettings,
    progress: Callable[[float, str], None],
) -> tuple[list[MatchedPoint], DisplacementField | None, MatchMethod]:
    """Tie points (reference pixel, moving pixel, quality, band, phase SNR)
    between two inputs over ``grid`` (their overlap), the displacement field, and the
    method used."""
    referenceCube = readCube(reference, list(bands))
    movingCube = readCube(moving, list(bands))
    overlaps = []
    for i in range(len(bands)):
        a = sampleOnGrid(reference, referenceCube[:, :, [i]], grid)[:, :, 0]
        b = sampleOnGrid(moving, movingCube[:, :, [i]], grid)[:, :, 0]
        overlaps.append((a, b))
    points: list[MatchedPoint] = []
    displacement = None
    method = settings.method
    if settings.method is MatchMethod.ASP:
        if settings.asp is None:
            raise AspError("The Ames Stereo Pipeline isn't set up (Optional Features).")
        transform = ASP_MODELS.get(moving.model, "projective")
        with tempfile.TemporaryDirectory(prefix="varda-asp-") as folder:
            for i, (band, (a, b)) in enumerate(zip(bands, overlaps, strict=True)):
                progress(i / len(bands), f"image_align on band {band + 1}")
                first, second = imageAlign(
                    settings.asp, a, b, transform, Path(folder) / f"band{band}"
                )
                points += [
                    _tiePoint(reference, moving, grid, p, q, float("nan"), band)
                    for p, q in zip(first, second, strict=True)
                ]
        return points, None, MatchMethod.ASP
    if settings.method in EXTERNAL:
        if settings.externalMatcher is None:
            raise RuntimeError("These matchers need PyTorch (Optional Features).")
        for i, (band, (a, b)) in enumerate(zip(bands, overlaps, strict=True)):
            progress(i / len(bands), f"matching band {band + 1}")
            first, second, confidence = settings.externalMatcher(
                a, b, EXTERNAL[settings.method]
            )
            points += [
                _tiePoint(reference, moving, grid, p, q, float(c), band)
                for p, q, c in zip(first, second, confidence, strict=True)
            ]
        return points, None, settings.method
    if settings.method is not MatchMethod.FEATURES:
        shifts = []
        for i, (band, (a, b)) in enumerate(zip(bands, overlaps, strict=True)):
            progress(i / len(bands), f"matching band {band + 1}")
            found, centers, shift = _tileMatches(a, b, settings)
            points += [
                _tiePoint(reference, moving, grid, p, q, quality, band, snr)
                for p, q, quality, snr in found
            ]
            shifts.append(shift)
        displacement = _field(
            reference, moving, grid, centers, shifts, settings.tileSize
        )
        method = MatchMethod.PHASE
        if settings.method is MatchMethod.AUTO and len(points) < MIN_PHASE_POINTS:
            points, displacement = [], None
    if not points and settings.method is not MatchMethod.PHASE:
        progress(0.5, "matching features")
        a, b = overlaps[0]
        found = _featureMatches(a, b)
        points = [
            _tiePoint(reference, moving, grid, p, q, quality, bands[0])
            for p, q, quality in found
        ]
        method = MatchMethod.FEATURES
    return points, displacement, method


def _tiePoint(
    reference: MosaicInput,
    moving: MosaicInput,
    grid: TargetGrid,
    p: np.ndarray,
    q: np.ndarray,
    quality: float,
    band: int,
    phaseSnr: float = float("nan"),
) -> MatchedPoint:
    """A match from overlap-grid pixels (p in the reference, q in the moving
    image) to each input's own pixels."""
    mapP = np.array(grid.transform * (p[0], p[1]))
    mapQ = np.array(grid.transform * (q[0], q[1]))
    r = reference.fromMap(mapP)[0]
    m = moving.fromMap(mapQ)[0]
    return (
        (float(r[0]), float(r[1])),
        (float(m[0]), float(m[1])),
        float(quality),
        int(band),
        float(phaseSnr),
    )


def _coarseOffset(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The whole-pixel shift (dx, dy) of b relative to a, from masked phase
    correlation on downsampled copies."""
    step = max(1, int(math.ceil(max(a.shape) / COARSE_SIZE)))
    small = [
        ndimage.uniform_filter(np.nan_to_num(x), step)[::step, ::step] for x in (a, b)
    ]
    masks = [np.isfinite(x)[::step, ::step] for x in (a, b)]
    if min(m.sum() for m in masks) < 16:
        return np.zeros(2)
    registration = phase_cross_correlation(
        small[0], small[1], reference_mask=masks[0], moving_mask=masks[1]
    )[0]
    dy, dx = -np.asarray(registration) * step
    return np.round(np.array([dx, dy]))


def _tileMatches(
    a: np.ndarray, b: np.ndarray, settings: MatchSettings
) -> tuple[
    list[tuple[np.ndarray, np.ndarray, float, float]],
    list[tuple[int, int]],
    np.ndarray,
]:
    """Phase-correlated tiles over one band of the overlap: the matches
    (reference point, moving point, quality, phase-plane SNR or NaN), the
    tile grid's top-left corners, and each tile's shift (tiles × (dx, dy,
    quality, SNR), NaN when rejected)."""
    size = settings.tileSize
    rows, cols = a.shape
    step = max(size // 4, 1)  # overlapping tiles, as COSI-Corr steps its windows
    while ((rows - size) // step + 1) * (
        (cols - size) // step + 1
    ) > MAX_TILES_PER_BAND:
        step += size // 4
    corners = [
        (y, x)
        for y in range(0, rows - size + 1, step)
        for x in range(0, cols - size + 1, step)
    ]
    coarse = _coarseOffset(a, b)
    found, shifts = [], np.full((len(corners), 4), np.nan)
    for t, (y, x) in enumerate(corners):
        tile = a[y : y + size, x : x + size]
        if not np.isfinite(tile).all() or bandSnr(tile[:, :, None])[0] < MIN_TILE_SNR:
            continue
        other = _tile(b, x + coarse[0], y + coarse[1], size)
        # both tiles need texture: the moving one may be a scene's fill
        if other is None or bandSnr(other[:, :, None])[0] < MIN_TILE_SNR:
            continue
        shift, quality = tileShift(tile, other)
        if np.abs(shift).max() > size / 4:  # beyond what phase correlation can trust
            continue
        whole = np.round(shift)
        snr = float("nan")
        if settings.refine:
            aligned = _tile(b, x + coarse[0] + whole[0], y + coarse[1] + whole[1], size)
            if aligned is None:
                continue
            fraction, snr = refineShift(tile, aligned, shift - whole)
            shift = whole + fraction
            quality = alignedCorrelation(tile, aligned, tuple(fraction))
        if quality < MIN_QUALITY:
            continue
        total = coarse + shift
        center = np.array([x + size / 2, y + size / 2])
        found.append((center, center + total, quality, snr))
        shifts[t] = (*total, quality, snr)
    return found, corners, shifts


def _tile(image: np.ndarray, x: float, y: float, size: int) -> np.ndarray | None:
    x, y = int(x), int(y)
    if x < 0 or y < 0 or y + size > image.shape[0] or x + size > image.shape[1]:
        return None
    tile = image[y : y + size, x : x + size]
    return tile if np.isfinite(tile).all() else None


def _field(
    reference: MosaicInput,
    moving: MosaicInput,
    grid: TargetGrid,
    corners: list[tuple[int, int]],
    shifts: list[np.ndarray],
    size: int,
) -> DisplacementField | None:
    """The tiles' shifts (median over the bands) as rasters in map units."""
    if not corners:
        return None
    ys = sorted({y for y, _ in corners})
    xs = sorted({x for _, x in corners})
    step = (
        (xs[1] - xs[0]) if len(xs) > 1 else ((ys[1] - ys[0]) if len(ys) > 1 else size)
    )
    stacked = np.stack(shifts)  # (bands, tiles, 4)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN tiles stay NaN
        median = np.nanmedian(stacked, axis=0)
    shape = (len(ys), len(xs))
    linear = np.array(
        [[grid.transform.a, grid.transform.b], [grid.transform.d, grid.transform.e]]
    )
    mapShift = median[:, :2] @ linear.T  # pixel shifts to map units
    offset = size / 2 - step / 2
    transform = grid.transform * Affine.translation(offset, offset) * Affine.scale(step)
    return DisplacementField(
        reference.key,
        moving.key,
        mapShift[:, 0].reshape(shape),
        mapShift[:, 1].reshape(shape),
        median[:, 2].reshape(shape),
        transform,
        median[:, 3].reshape(shape),
    )


def _normalized(image: np.ndarray) -> np.ndarray:
    finite = image[np.isfinite(image)]
    if not finite.size:
        return np.zeros_like(image)
    low, high = np.percentile(finite, (1, 99))
    scaled = (np.nan_to_num(image, nan=low) - low) / ((high - low) or 1.0)
    return np.clip(scaled, 0.0, 1.0)


def _featureMatches(
    a: np.ndarray, b: np.ndarray
) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """ORB keypoints matched between the overlaps (robust to rotation and
    scale): (reference point, moving point, quality)."""
    step = max(1, int(math.ceil(max(a.shape) / FEATURE_SIZE)))
    images = [_normalized(x)[::step, ::step] for x in (a, b)]
    keypoints, descriptors = [], []
    for image in images:
        orb = ORB(n_keypoints=800, fast_threshold=0.05)
        try:
            orb.detect_and_extract(image)
        except RuntimeError:  # no keypoints
            return []
        keypoints.append(orb.keypoints)
        descriptors.append(orb.descriptors)
    if min(len(k) for k in keypoints) < 3:
        return []
    matches = match_descriptors(
        descriptors[0], descriptors[1], cross_check=True, max_ratio=0.8
    )
    found = []
    for i, j in matches:
        # keypoints are (row, col) at pixel centers
        p = (keypoints[0][i][::-1] + 0.5) * step
        q = (keypoints[1][j][::-1] + 0.5) * step
        found.append((p, q, float("nan")))
    return found
