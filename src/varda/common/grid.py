"""Which images share a pixel grid (so masks, overlays and statistics line
up), which can be placed on another image's grid by their map location, and
the resampling that puts them there. No Qt.

A dropdown that offers only some images uses an ``ImageChoices``: the images
offered and, for the others, why not (``ImageChoices.note``)."""

from __future__ import annotations

from collections.abc import Collection, Sequence

import attrs
import numpy as np
from affine import Affine
from rasterio import CRS
from rasterio.enums import Resampling
from rasterio.errors import RasterioError
from rasterio.transform import array_bounds
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import from_bounds

from varda.common.entities import VardaRaster
from varda.geo.describe import describeCrs

# source pixels read beyond the target's footprint, so the resampling
# kernels at its edge have their neighbors
_MARGIN = 2

SAME_GRID = "this needs the same grid"


def sameGrid(a: VardaRaster, b: VardaRaster) -> bool:
    if (a.height, a.width) != (b.height, b.width):
        return False
    if a.hasGeospatialData and b.hasGeospatialData:
        return a.crs == b.crs and a.transform.almost_equals(b.transform)
    return a.hasGeospatialData == b.hasGeospatialData


def compatibleImages(
    images: Sequence[VardaRaster], base: VardaRaster
) -> list[VardaRaster]:
    """The other images on ``base``'s pixel grid."""
    return [image for image in images if image is not base and sameGrid(image, base)]


# --- choices and why images are left out ----------------------------------------


@attrs.frozen
class Excluded:
    image: VardaRaster
    reason: str


@attrs.frozen
class ImageChoices:
    """The images a dropdown offers, and the ones it doesn't with why."""

    images: list[VardaRaster] = attrs.field(factory=list)
    excluded: list[Excluded] = attrs.field(factory=list)

    @property
    def note(self) -> str:
        """ "Not listed: name (reason)", one line per image; "" when every
        image is offered."""
        if not self.excluded:
            return ""
        lines = [f"{e.image.name} ({e.reason})" for e in self.excluded]
        return "Not listed: " + "\n".join(lines)


def pixelSizeText(image: VardaRaster) -> str:
    """A pixel's size, e.g. "6.0 m" or "0.00231°" ("1 px" without
    georeferencing; the larger side for rectangular pixels)."""
    if not image.hasGeospatialData:
        return "1 px"
    t = image.transform
    size = max(float(np.hypot(t.a, t.d)), float(np.hypot(t.b, t.e)))
    units = describeCrs(image.crs).units
    return f"{size:.3g}°" if units == "°" else f"{size:.1f} {units}"


def gridDifference(image: VardaRaster, base: VardaRaster) -> str:
    """How ``image``'s pixel grid differs from ``base``'s, in a few words."""
    if image.hasGeospatialData != base.hasGeospatialData:
        return (
            "not georeferenced"
            if base.hasGeospatialData
            else "georeferenced, and this image isn't"
        )
    if image.hasGeospatialData and image.crs != base.crs:
        return "a different map projection"
    ours, theirs = pixelSizeText(image), pixelSizeText(base)
    if ours != theirs:
        return f"{ours} vs {theirs} pixels"
    if (image.width, image.height) != (base.width, base.height):
        return f"{image.width} × {image.height} vs {base.width} × {base.height} pixels"
    return "shifted from this image's pixels"


def _bandsReason(image: VardaRaster, bandCounts: Collection[int]) -> str | None:
    if image.bandCount in bandCounts:
        return None
    counts = sorted(set(bandCounts))
    allowed = " or ".join(str(n) for n in counts)
    have = f"{image.bandCount} band{'s' if image.bandCount != 1 else ''}"
    return f"{have}; this needs {allowed}"


def sameGridChoices(
    images: Sequence[VardaRaster],
    base: VardaRaster,
    *,
    bandCounts: Collection[int] | None = None,
) -> ImageChoices:
    """The other images on ``base``'s pixel grid (with one of
    ``bandCounts`` bands, when given), and why the rest aren't."""
    choices = ImageChoices()
    for image in images:
        if image is base:
            continue
        if not sameGrid(image, base):
            reason = f"different pixel grid: {gridDifference(image, base)}; {SAME_GRID}"
        else:
            reason = _bandsReason(image, bandCounts) if bandCounts else None
        if reason is None:
            choices.images.append(image)
        else:
            choices.excluded.append(Excluded(image, reason))
    return choices


def placementProblem(image: VardaRaster, base: VardaRaster) -> str | None:
    """Why ``image`` can't be resampled onto ``base``'s grid by map location,
    or None: on the same grid, or georeferenced (like ``base``) and
    overlapping it."""
    if sameGrid(image, base):
        return None
    if not base.hasGeospatialData:
        return (
            "a different size, and this image isn't georeferenced, so only an "
            "image of its size can be used"
        )
    if not image.hasGeospatialData:
        return "not georeferenced, can't be placed on this image"
    try:
        west, south, east, north = _footprintIn(base, image.crs)
    except (RasterioError, ValueError):
        return "can't be reprojected to this image's map projection"
    left, bottom, right, top = _bounds(image)
    if west >= right or east <= left or south >= top or north <= bottom:
        return "doesn't overlap this image"
    return None


def placeableChoices(
    images: Sequence[VardaRaster],
    base: VardaRaster,
    *,
    bandCounts: Collection[int] | None = None,
    includeBase: bool = False,
) -> ImageChoices:
    """The images that can go on ``base``'s grid: on it already, or
    georeferenced and overlapping it in map space (any map projection; they
    are resampled with ``onGridOf``), with one of ``bandCounts`` bands when
    given; and why the rest can't. ``base`` itself is a choice only with
    ``includeBase`` (and never listed as left out)."""
    choices = ImageChoices()
    for image in images:
        reason = placementProblem(image, base)
        if reason is None and bandCounts:
            reason = _bandsReason(image, bandCounts)
        if reason is None and (image is not base or includeBase):
            choices.images.append(image)
        elif image is not base:
            choices.excluded.append(Excluded(image, reason or ""))
    return choices


# --- resampling ----------------------------------------------------------------


def _bounds(image: VardaRaster) -> tuple[float, float, float, float]:
    return array_bounds(image.height, image.width, image.transform)


def _footprintIn(
    image: VardaRaster, crs: CRS | None
) -> tuple[float, float, float, float]:
    """``image``'s bounds (west, south, east, north) in ``crs``."""
    bounds = _bounds(image)
    if crs is None or crs == image.crs:
        return bounds
    return transform_bounds(image.crs, crs, *bounds, densify_pts=21)


def sourcePixelSize(source: VardaRaster, target: VardaRaster) -> float:
    """The mean size of ``source``'s pixels in ``target``'s map units (its
    footprint there over its pixel count)."""
    west, south, east, north = _footprintIn(source, target.crs)
    return float(
        np.sqrt((east - west) * (north - south) / (source.width * source.height))
    )


def targetPixelSize(target: VardaRaster) -> float:
    t = target.transform
    return float(np.sqrt(abs(t.a * t.e - t.b * t.d)))


def demResampling(source: VardaRaster, target: VardaRaster) -> Resampling:
    """Average when ``source``'s pixels are finer than ``target``'s (every
    source pixel counts), bilinear otherwise."""
    if sameGrid(source, target):
        return Resampling.nearest  # nothing is resampled
    finer = sourcePixelSize(source, target) < targetPixelSize(target)
    return Resampling.average if finer else Resampling.bilinear


# a part of an image: (row offset, column offset, height, width) in pixels,
# as ``VardaRaster.getData`` takes it
type PixelWindow = tuple[int, int, int, int]


def _readFloat(
    image: VardaRaster, bands: list[int] | None, window: PixelWindow | None = None
) -> np.ndarray:
    """Bands as float64 (rows, cols, bands), NaN where there's no data."""
    data = np.ma.filled(
        np.ma.asarray(image.getData(bands, window=window), dtype=np.float64), np.nan
    )
    nodata = image.nodata
    if nodata is not None and np.isfinite(nodata):
        data[data == nodata] = np.nan
    return data


def _sourceWindow(source: VardaRaster, target: VardaRaster) -> PixelWindow | None:
    """The part of ``source`` under ``target`` (plus a margin), or None when
    they don't overlap."""
    west, south, east, north = _footprintIn(target, source.crs)
    window = from_bounds(west, south, east, north, source.transform)
    col0 = max(int(np.floor(min(window.col_off, window.col_off + window.width))), 0)
    row0 = max(int(np.floor(min(window.row_off, window.row_off + window.height))), 0)
    col1 = int(np.ceil(max(window.col_off, window.col_off + window.width)))
    row1 = int(np.ceil(max(window.row_off, window.row_off + window.height)))
    col0, row0 = max(col0 - _MARGIN, 0), max(row0 - _MARGIN, 0)
    col1 = min(col1 + _MARGIN, source.width)
    row1 = min(row1 + _MARGIN, source.height)
    if col1 <= col0 or row1 <= row0:
        return None
    return row0, col0, row1 - row0, col1 - col0


def readPixels(source: VardaRaster, target: VardaRaster) -> int:
    """How many of ``source``'s pixels (per band) ``onGridOf`` reads to
    resample it onto ``target``: its part under ``target``; 0 on the same
    grid or when it can't be placed."""
    if sameGrid(source, target) or placementProblem(source, target) is not None:
        return 0
    window = _sourceWindow(source, target)
    return 0 if window is None else window[2] * window[3]


def onGridOf(
    source: VardaRaster,
    target: VardaRaster,
    resampling: Resampling = Resampling.nearest,
    bands: list[int] | None = None,
) -> np.ndarray:
    """``source``'s ``bands`` (all by default) on ``target``'s pixel grid,
    as float64 (rows, cols, bands): read as they are when the grids are the
    same, otherwise reprojected and resampled by map location with
    ``resampling`` (GDAL's warper). No data in the source (its no-data value
    or masked pixels) is left out of the resampling; target pixels outside
    the source, or with no source data under them, are NaN."""
    if sameGrid(source, target):
        return _readFloat(source, bands)
    reason = placementProblem(source, target)
    if reason is not None:
        raise ValueError(
            f"{source.name} can't be resampled onto {target.name}: {reason}"
        )
    count = source.bandCount if bands is None else len(bands)
    out = np.full((count, target.height, target.width), np.nan)
    window = _sourceWindow(source, target)
    if window is None:
        return out.transpose(1, 2, 0)
    data = _readFloat(source, bands, window).transpose(2, 0, 1)
    reproject(
        source=np.ascontiguousarray(data),
        destination=out,
        src_transform=source.transform * Affine.translation(window[1], window[0]),
        src_crs=source.crs,
        src_nodata=np.nan,
        dst_transform=target.transform,
        dst_crs=target.crs,
        dst_nodata=np.nan,
        resampling=resampling,
    )
    return out.transpose(1, 2, 0)


def resamplingText(
    source: VardaRaster, target: VardaRaster, resampling: Resampling
) -> str:
    """How ``onGridOf`` places ``source`` on ``target``, for descriptions,
    e.g. "ctx_dem resampled from 6.0 m to 18.0 m pixels (average)"; "" on
    the same grid."""
    if sameGrid(source, target):
        return ""
    how = "reprojected and resampled" if source.crs != target.crs else "resampled"
    return (
        f"{source.name} {how} from {pixelSizeText(source)} to "
        f"{pixelSizeText(target)} pixels ({resampling.name})"
    )


def resamplingRecord(
    source: VardaRaster, target: VardaRaster, resampling: Resampling
) -> dict | None:
    """The resampling, for a result's provenance metadata (None on the same
    grid)."""
    if sameGrid(source, target):
        return None
    assert source.crs is not None and target.crs is not None
    return {
        "source": source.name,
        "description": resamplingText(source, target, resampling),
        "method": resampling.name,
        "library": "GDAL warper (rasterio.warp.reproject)",
        "fromPixelSize": pixelSizeText(source),
        "toPixelSize": pixelSizeText(target),
        "fromCrs": source.crs.to_string(),
        "toCrs": target.crs.to_string(),
    }
