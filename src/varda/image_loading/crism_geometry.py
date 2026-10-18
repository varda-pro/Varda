"""Isolated CRISM geometry (DDR backplane) support for column-locked ROI placement.

CRISM reflectance products ship a separate geometry "DDR" file whose bands give,
per pixel, the detector column ("IR Sample") and the source strip identity
("Target ID" + "Segment ID"), or the solar incidence angle. This module finds
that companion file, computes the horizontal shift needed to place a copied ROI
on the same detector column as its template, and reads the scene's incidence.

The file is looked for in order (``geometryCandidates``): the image's own
override (``crismGeometryFile`` in its metadata, chosen by hand), the
companions named like the image's file beside it, then those of the files its
Varda provenance names, following crops back to their source. A file serves
only if it has the needed band and covers the image's pixels. It is deliberately CRISM-specific and self-contained: no
general multi-instrument abstraction until a second instrument needs one.
"""

from __future__ import annotations

import logging
import math
import os
import re

from typing import TYPE_CHECKING

import attrs
import numpy as np
import rasterio
from affine import Affine
from pyproj import CRS
from rasterio.errors import RasterioError

from varda.common.provenance import CRISM_GEOMETRY_FILE_KEY, PROVENANCE_KEY

if TYPE_CHECKING:
    from varda.common.entities import VardaRaster

logger = logging.getLogger(__name__)


# How ``ColumnGeometry.source`` names geometry taken from an unprojected
# cube's own pixel grid rather than from a backplane file
IMAGE_COLUMNS = "image columns (unprojected)"
# Image widths of unprojected CRISM cubes and their pixel binning (640
# detector columns at full resolution)
CRISM_SENSOR_WIDTHS = {640: 1, 320: 2, 128: 5, 64: 10}


@attrs.define
class ColumnGeometry:
    """Per-pixel CRISM geometry arrays, all shape (height, width).

    ``ir_sample`` is the detector column index and ``ir_line`` (when the
    backplane has it) the detector line, i.e. the sensor frame; ``target_id``
    and ``segment_id`` identify the source observation strip. Missing values
    are NaN. ``source`` is the backplane file read, or :data:`IMAGE_COLUMNS`;
    ``sampleBand`` the backplane band the columns came from ("" for image
    columns); ``binning`` how many detector columns one image column spans;
    ``route`` how the file was found ("override", "beside the file", "from
    source <path>", or "image columns").
    """

    ir_sample: np.ndarray
    target_id: np.ndarray | None = None
    segment_id: np.ndarray | None = None
    ir_line: np.ndarray | None = None
    source: str = ""
    sampleBand: str = ""
    binning: int = 1
    route: str = ""


def _modeUnderMask(arr: np.ndarray | None, mask: np.ndarray) -> int | None:
    """Most common (non-NaN) integer value of ``arr`` under a boolean mask."""
    if arr is None:
        return None
    vals = arr[mask]
    vals = vals[~np.isnan(vals)]
    if vals.size == 0:
        return None
    uniq, counts = np.unique(vals.astype(np.int64), return_counts=True)
    return int(uniq[int(np.argmax(counts))])


def computeColumnLockedTranslation(
    templatePolygonPixels: np.ndarray,
    clickRow: int,
    clickCol: int,
    geometry: ColumnGeometry,
) -> tuple[float, float] | None:
    """Compute the (dx, dy) pixel shift for a column-locked template paste.

    ``templatePolygonPixels`` is an (N, 2) array of (col, row) pixel *corner*
    coordinates (pixel ``c`` spans ``c..c+1``), as ROI geometries are stored.
    ``clickCol`` is intentionally ignored: the horizontal placement is determined
    by the detector-column match, not the clicked column.
    Returns (dx, dy) so that the template, shifted by it, is centered on the
    clicked row and sits on the same detector column within the same strip. The
    destination column is interpolated to sub-pixel precision, so a template
    whose mean IR sample falls between two columns (any even-width box) keeps
    exactly its columns rather than snapping a whole pixel sideways. Returns
    None if the lock cannot be satisfied (destination row out of bounds, the
    template's strip does not reach the destination row, or no geometry under
    the template).
    """
    import rasterio.features
    from shapely.geometry import Polygon
    from shapely.geometry import mapping as shapely_mapping

    ir = geometry.ir_sample
    nrows, ncols = ir.shape

    # Rasterize the template polygon footprint to a boolean mask. (Same approach
    # as ROICollection.getMask, so no extra dependency is needed.)
    poly = Polygon([(float(c), float(r)) for c, r in templatePolygonPixels])
    maskBool = rasterio.features.rasterize(
        [(shapely_mapping(poly), 1)],
        out_shape=(nrows, ncols),
        fill=0,
        dtype=np.uint8,
    ).astype(bool)

    srcIr = ir[maskBool]
    srcIr = srcIr[~np.isnan(srcIr)]
    if srcIr.size == 0:
        return None
    srcIrMean = float(np.mean(srcIr))
    srcTarget = _modeUnderMask(geometry.target_id, maskBool)
    srcSeg = _modeUnderMask(geometry.segment_id, maskBool)

    destY = int(clickRow)
    if not (0 <= destY < nrows):
        return None

    rowIr = ir[destY, :]
    valid = ~np.isnan(rowIr)
    if srcTarget is not None and geometry.target_id is not None:
        valid &= geometry.target_id[destY, :] == srcTarget
    if srcSeg is not None and geometry.segment_id is not None:
        valid &= geometry.segment_id[destY, :] == srcSeg
    validXs = np.where(valid)[0]
    if validXs.size == 0:
        return None

    destCol = _interpolateColumn(validXs, rowIr[validXs], srcIrMean)

    # Polygon coordinates are pixel corners, so pixel column c is centered on
    # c + 0.5; the same holds for the clicked row. Use the true centroid: a
    # vertex mean is biased when the ring repeats its first vertex to close.
    # The shift is rounded to whole pixels so the copy is an exact pixel-set
    # translate of the template (a fractional shift would leave an even-sized
    # footprint's edges on pixel centers, where rasterization is ambiguous).
    centroid = poly.centroid
    dx = wholePixels((destCol + 0.5) - centroid.x)
    dy = wholePixels((float(clickRow) + 0.5) - centroid.y)
    return (dx, dy)


def wholePixels(shift: float) -> float:
    """Round a pixel shift half-up, so an even-sized footprint centered between two
    pixels extends right/down — the same convention as ``boxPolygonPixels``."""
    return float(math.floor(shift + 0.5))


def _interpolateColumn(cols: np.ndarray, irSamples: np.ndarray, target: float) -> float:
    """Sub-pixel column where the row's IR sample equals ``target``.

    Starts from the column whose IR sample is nearest and interpolates linearly
    towards the neighbor on the far side of ``target`` (IR sample varies
    smoothly along a row). Falls back to the nearest column at the row's ends
    or where the IR sample is flat.
    """
    i = int(np.argmin(np.abs(irSamples - target)))
    col0, ir0 = float(cols[i]), float(irSamples[i])
    if target > ir0 and i + 1 < len(cols):
        j = i + 1
    elif target < ir0 and i > 0:
        j = i - 1
    else:
        return col0
    col1, ir1 = float(cols[j]), float(irSamples[j])
    if ir1 == ir0:
        return col0
    return col0 + (target - ir0) / (ir1 - ir0) * (col1 - col0)


_BAND_ALIASES: dict[str, tuple[str, ...]] = {
    "ir_sample": (
        "ir (l-detector) sample",
        "ir sample",
        "l-detector sample",
        "sample",
    ),
    # never plain "line": "VNIR Line" would match it
    "ir_line": ("ir (l-detector) line", "ir line", "l-detector line"),
    "target_id": ("target id",),
    "segment_id": ("segment id (counter)", "segment id", "segment"),
    # the solar incidence angle to the reference surface, not the MOLA-local one
    "incidence": ("ina at areoid",),
}


def findBandIndex(descriptions: tuple[str, ...], kind: str) -> int | None:
    """Return the 1-indexed band whose description matches an alias of ``kind``.

    Aliases are matched on a word boundary so that, e.g., the ``ir_sample`` alias
    ``"ir sample"`` matches ``"IR Sample"`` but not ``"VNIR Sample"`` (where
    ``"ir sample"`` only appears mid-word). More specific aliases are tried first.
    """
    aliases = _BAND_ALIASES.get(kind, ())
    lowered = [
        (idx, desc.strip().lower())
        for idx, desc in enumerate(descriptions, start=1)
        if desc
    ]
    for alias in aliases:
        pattern = re.compile(r"\b" + re.escape(alias))
        for idx, desc in lowered:
            if pattern.search(desc):
                return idx
    return None


# --- which geometry file, and how it was found -----------------------------------

# An image's hand-chosen geometry file (a path), in its metadata
GEOMETRY_FILE_KEY = CRISM_GEOMETRY_FILE_KEY
# How a geometry file was found (``route``)
ROUTE_OVERRIDE = "override"
ROUTE_BESIDE = "beside the file"
ROUTE_IMAGE_COLUMNS = "image columns"
SOURCE_ROUTE_PREFIX = "from source "
# How many provenance steps back the lookup follows (e.g. crop → IOVF → I/F)
PROVENANCE_DEPTH = 8
# What a feature needs from the geometry file: a band kind of ``_BAND_ALIASES``
COLUMNS = "ir_sample"
INCIDENCE = "incidence"
# Processes whose output is a window of their input: the parameters giving the
# first row and column, and whether those are 1-based
_CROPS = {"crop": ("row", "column", 0), "subset": ("First Row", "First Column", 1)}
# Processes whose output is on another grid (or an unrecorded window of it):
# the provenance chain stops there
_REGRIDS = frozenset({"resample", "rotate_flip", "crop_to_roi"})
# How close to whole pixels a georeferenced offset must be
_OFFSET_TOLERANCE = 0.01


def sourceRoute(path: str) -> str:
    return f"{SOURCE_ROUTE_PREFIX}{path}"


@attrs.frozen
class GeometryCandidate:
    """A geometry file that may serve an image: ``offset`` is the (row,
    column) of the image's first pixel in the file's grid when crops along the
    provenance chain say so; (0, 0) means the same grid, or one placed by
    georeferencing."""

    path: str
    route: str
    offset: tuple[int, int] = (0, 0)


@attrs.frozen
class GeometryMatch:
    """The geometry file found for an image and need: the image's pixels are
    the file's rows ``window[0]``.. and columns ``window[1]``.."""

    path: str
    route: str
    window: tuple[int, int]


@attrs.frozen
class _Header:
    descriptions: tuple[str, ...]
    height: int
    width: int
    transform: Affine
    crs: str  # WKT, "" without one


def _header(path: str) -> _Header:
    with rasterio.open(path) as geom:
        return _Header(
            tuple(d or "" for d in geom.descriptions or ()),
            geom.height,
            geom.width,
            geom.transform,
            geom.crs.to_wkt() if geom.crs else "",
        )


def geometryDataFile(path: str) -> str:
    """The file to open for a chosen geometry file: an ENVI header's data
    file (``.img`` beside the ``.hdr``), else ``path`` itself."""
    stem, suffix = os.path.splitext(path)
    if suffix.lower() == ".hdr":
        for data in (stem + ".img", stem + ".IMG"):
            if os.path.isfile(data):
                return data
    return path


def geometryStartFolder(image: VardaRaster) -> str:
    """Where to start looking for ``image``'s geometry file: its own file's
    folder, else that of the first file its provenance names ("" if none)."""
    path = image.filePath or ""
    record = image.extraMetadata.get(PROVENANCE_KEY)
    for _ in range(PROVENANCE_DEPTH):
        if path or not isinstance(record, dict):
            break
        inputs = [i for i in record.get("inputs") or [] if isinstance(i, dict)]
        if not inputs:
            break
        path = str(inputs[0].get("path") or "")
        record = inputs[0].get(PROVENANCE_KEY)
    return os.path.dirname(path)


def geometryOverride(image: VardaRaster) -> str:
    """The geometry file chosen by hand for ``image`` ("" when none)."""
    return str(image.extraMetadata.get(GEOMETRY_FILE_KEY) or "")


def setGeometryOverride(image: VardaRaster, path: str | None) -> None:
    """Use ``path`` as ``image``'s geometry file before any found by name
    (None or "" goes back to automatic lookup); everything showing the image
    is told."""
    metadata = image.dataSource.extraMetadata
    if path:
        metadata[GEOMETRY_FILE_KEY] = str(path)
    else:
        metadata.pop(GEOMETRY_FILE_KEY, None)
    image.sigMetadataChanged.emit()


def geometryCandidates(image: VardaRaster) -> list[GeometryCandidate]:
    """Every geometry file that may serve ``image``, in the order they are
    tried: its override, the companions beside its file, then those of the
    files its provenance names (up to :data:`PROVENANCE_DEPTH` steps back)."""
    found: list[GeometryCandidate] = []
    override = geometryOverride(image)
    if override:
        found.append(GeometryCandidate(override, ROUTE_OVERRIDE))
    if image.filePath:
        found += [
            GeometryCandidate(path, ROUTE_BESIDE)
            for path in geometryFileCandidates(image.filePath)
        ]
    found += _provenanceCandidates(image.extraMetadata.get(PROVENANCE_KEY), (0, 0), 1)
    unique: dict[tuple[str, tuple[int, int]], GeometryCandidate] = {}
    for candidate in found:
        unique.setdefault((candidate.path, candidate.offset), candidate)
    return list(unique.values())


def _provenanceCandidates(
    record: dict | None, offset: tuple[int, int], depth: int
) -> list[GeometryCandidate]:
    """The geometry files of the inputs a provenance record names (their
    override, then their companions), then of their own inputs."""
    if not isinstance(record, dict) or depth > PROVENANCE_DEPTH:
        return []
    process = record.get("processId", "")
    if process in _REGRIDS:
        return []
    if process in _CROPS:
        rowKey, columnKey, base = _CROPS[process]
        parameters = record.get("parameters") or {}
        try:
            offset = (
                offset[0] + int(parameters[rowKey]) - base,
                offset[1] + int(parameters[columnKey]) - base,
            )
        except (KeyError, TypeError, ValueError):
            return []
    found: list[GeometryCandidate] = []
    for entry in record.get("inputs") or []:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "")
        route = sourceRoute(path or str(entry.get("name", "?")))
        chosen = entry.get(GEOMETRY_FILE_KEY)
        if chosen:
            found.append(GeometryCandidate(str(chosen), route, offset))
        if path:
            found += [
                GeometryCandidate(companion, route, offset)
                for companion in geometryFileCandidates(path)
            ]
        found += _provenanceCandidates(entry.get(PROVENANCE_KEY), offset, depth + 1)
    return found


def _georeferencedOffset(header: _Header, image: VardaRaster) -> tuple[int, int] | None:
    """The image's first pixel in the file's grid, when both are on one map
    grid (the same projection and pixel size, no rotation, whole-pixel offset)."""
    if not header.crs or not image.hasGeospatialData or image.crs is None:
        return None
    if not CRS.from_wkt(header.crs).equals(image.crs, ignore_axis_order=True):
        return None
    file, own = header.transform, image.transform
    if file.b or file.d or own.b or own.d or not file.a or not file.e:
        return None
    if not (
        math.isclose(file.a, own.a, rel_tol=1e-6)
        and math.isclose(file.e, own.e, rel_tol=1e-6)
    ):
        return None
    column = (own.c - file.c) / file.a
    row = (own.f - file.f) / file.e
    if (
        abs(column - round(column)) > _OFFSET_TOLERANCE
        or abs(row - round(row)) > _OFFSET_TOLERANCE
    ):
        return None
    return int(round(row)), int(round(column))


def _window(
    header: _Header, image: VardaRaster, offset: tuple[int, int]
) -> tuple[int, int] | None:
    """Where the image's pixels are in the file: the same grid, a window that
    crops along the provenance chain give, or one placed by georeferencing.
    None when the file doesn't cover the image that way."""
    height, width = image.height, image.width
    if offset == (0, 0) and (header.height, header.width) == (height, width):
        return (0, 0)
    if offset == (0, 0):
        placed = _georeferencedOffset(header, image)
        if placed is None:
            return None
        offset = placed
    row, column = offset
    if (
        row < 0
        or column < 0
        or row + height > header.height
        or column + width > header.width
    ):
        return None
    return offset


def _firstMatch(
    candidates: list[GeometryCandidate], image: VardaRaster, need: str
) -> GeometryMatch | None:
    for candidate in candidates:
        try:
            header = _header(candidate.path)
        except (RasterioError, OSError):
            logger.info("Can't open the geometry file %s", candidate.path)
            continue
        if findBandIndex(header.descriptions, need) is None:
            continue
        window = _window(header, image, candidate.offset)
        if window is None:
            logger.info("%s doesn't cover %s's pixels", candidate.path, image.name)
            continue
        return GeometryMatch(candidate.path, candidate.route, window)
    return None


def findGeometryFile(image: VardaRaster, need: str) -> GeometryMatch | None:
    """The first of :func:`geometryCandidates` that has the band ``need``
    (:data:`COLUMNS` or :data:`INCIDENCE`) and covers the image's pixels, or
    None. Reads only the files' headers."""
    return _firstMatch(geometryCandidates(image), image, need)


_NEEDED_BY = {
    COLUMNS: "IR detector sample band ('IR Sample'), which column lock and "
    "the Ratio Cube need",
    INCIDENCE: "solar incidence band ('INA at areoid'), which the Shadow Mask reads",
}


def checkGeometryFile(path: str, image: VardaRaster, need: str) -> str:
    """Why ``path`` can't serve as ``image``'s geometry file for ``need``
    ("" when it can): it must open, have the band, and cover the image's
    pixels (the same rows × columns, or one map grid that covers the image)."""
    name = os.path.basename(path)
    if not os.path.isfile(path):
        return f"{path} doesn't exist."
    try:
        header = _header(path)
    except (RasterioError, OSError) as error:
        return f"Varda can't open {name} as an image: {error}"
    if findBandIndex(header.descriptions, need) is None:
        bands = ", ".join(d for d in header.descriptions if d) or "none named"
        if (
            need == COLUMNS
            and findBandIndex(header.descriptions, INCIDENCE) is not None
        ):
            return (
                f"{name} looks like a DDR/DE file: it has the incidence angle "
                f"but no {_NEEDED_BY[COLUMNS]}. Choose the product's IN file "
                "(…_in…, e.g. frt000107a7_07_in166j_mtr3.img), which holds "
                "the detector columns."
            )
        companion = "IN file (…_in…)" if need == COLUMNS else "DDR/DE file (…_de…)"
        return (
            f"{name} has no {_NEEDED_BY[need]}. Its bands: {bands}. Choose the "
            f"product's {companion}."
        )
    if _window(header, image, (0, 0)) is None:
        return (
            f"{name} is {header.width} × {header.height} pixels but {image.name} "
            f"is {image.width} × {image.height}, and they aren't on one map grid "
            "(the same projection and pixel size) that covers the image, so its "
            "pixels can't be matched to the image's. Choose the companion of the "
            "product this image was made from."
        )
    return ""


# --- reading ---------------------------------------------------------------------


def _readWindow(
    window: tuple[int, int], shape: tuple[int, int]
) -> tuple[tuple[int, int], tuple[int, int]]:
    """A rasterio read window: ``shape`` (rows, cols) pixels from ``window``
    (row, column), as ((row start, stop), (column start, stop))."""
    (row, column), (height, width) = window, shape
    return (row, row + height), (column, column + width)


# (path, window, shape) -> the detector columns read there
_geometry_cache: dict[
    tuple[str, tuple[int, int], tuple[int, int]], ColumnGeometry | None
] = {}


def _readColumnGeometry(
    geomPath: str, window: tuple[int, int], shape: tuple[int, int]
) -> ColumnGeometry | None:
    """The backplane bands of ``geomPath`` under the image (``shape`` pixels
    from ``window``), or None when it has no detector sample band (a TRR3's
    DDR, for example) or can't be read. Cached."""
    key = (geomPath, window, shape)
    if key in _geometry_cache:
        return _geometry_cache[key]
    result = None
    try:
        with rasterio.open(geomPath) as geom:
            descs = geom.descriptions or ()
            irIdx = findBandIndex(descs, "ir_sample")
            if irIdx is None:
                logger.info("%s has no IR Sample band", geomPath)
            else:
                nodata = geom.nodata
                pixels = _readWindow(window, shape)

                def _read(idx: int | None) -> np.ndarray | None:
                    if idx is None:
                        return None
                    arr = geom.read(idx, window=pixels).astype(np.float64)
                    if nodata is not None:
                        arr = np.where(arr == nodata, np.nan, arr)
                    return arr

                sample = _read(irIdx)
                assert sample is not None
                result = ColumnGeometry(
                    ir_sample=sample,
                    target_id=_read(findBandIndex(descs, "target_id")),
                    segment_id=_read(findBandIndex(descs, "segment_id")),
                    ir_line=_read(findBandIndex(descs, "ir_line")),
                    source=geomPath,
                    sampleBand=descs[irIdx - 1] or "",
                )
    except RasterioError as e:
        logger.warning("Failed to read DDR geometry %s: %s", geomPath, e)
    _geometry_cache[key] = result
    return result


def imageColumnGeometry(height: int, width: int) -> ColumnGeometry:
    """An unprojected cube's own grid as its sensor geometry: image column c
    is detector column c (in binned columns) and image row r is frame r."""
    return ColumnGeometry(
        ir_sample=np.tile(np.arange(width, dtype=np.float64), (height, 1)),
        ir_line=np.repeat(np.arange(height, dtype=np.float64)[:, None], width, 1),
        source=IMAGE_COLUMNS,
        binning=CRISM_SENSOR_WIDTHS.get(width, 1),
        route=ROUTE_IMAGE_COLUMNS,
    )


def columnGeometryFor(image: VardaRaster, chosen: str = "") -> ColumnGeometry | None:
    """The detector columns of a CRISM cube, on its pixels: from ``chosen``
    when given (a file picked for this use, route "override"; None if it
    can't serve), else from the first geometry file with an IR Sample band
    that covers the image (:func:`findGeometryFile`), else, for a cube that
    is confidently unprojected (no map projection and a CRISM width, 640,
    320, 128 or 64 columns), its own image columns. None otherwise.
    ``route`` says how the file was found."""
    match = (
        _firstMatch([GeometryCandidate(chosen, ROUTE_OVERRIDE)], image, COLUMNS)
        if chosen
        else findGeometryFile(image, COLUMNS)
    )
    if match is not None:
        geometry = _readColumnGeometry(
            match.path, match.window, (image.height, image.width)
        )
        if geometry is not None:
            return attrs.evolve(geometry, route=match.route)
    if not chosen and image.crs is None and image.width in CRISM_SENSOR_WIDTHS:
        return imageColumnGeometry(image.height, image.width)
    return None


# (pattern, replacement) applied to the lower-case filename stem to find the
# geometry companions, most useful first: the IN file's detector sample and
# line, then the DDR (incidence angle and the like). Names as on the PDS
# Geosciences Node: a TRR3 ``..._if166l_trr3`` has the DDR ``..._de166l_ddr1``
# (no detector sample band: the cube is in sensor space); an MTRDR
# ``..._if166j_mtr3`` has ``..._in166j_mtr3`` and ``..._de166l_mtr1``; a TER3
# ``..._if166j_ter3`` has ``..._in166j_ter3``.
_GEOMETRY_NAME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"_mrr(al|if|ir|sr|su|ra)_"), "_mrrde_"),
    (re.compile(r"_vrr(al|if|ir|sr|su|ra)_"), "_vrrde_"),
    (re.compile(r"_(if|sr|su)(\d{3}[a-z])"), r"_in\2"),
    (re.compile(r"_(if|ra)(\d{3})([ls])_trr3"), r"_de\2\3_ddr1"),
    (re.compile(r"_(if|sr|su)(\d{3})[a-z]_ter3"), r"_de\2l_ddr1"),
    (re.compile(r"_(if|sr|su)(\d{3})[a-z]_mtr3"), r"_de\2l_mtr1"),
]


def geometryFileCandidates(sourceFilename: str) -> list[str]:
    """The geometry companions of a CRISM product found next to it, in the
    order of ``_GEOMETRY_NAME_PATTERNS``. Names match whatever their case
    (PDS names are lower case, ODE downloads upper case); the paths returned
    are the files as named on disk."""
    directory = os.path.dirname(sourceFilename)
    stem = os.path.splitext(os.path.basename(sourceFilename))[0].lower()
    try:
        onDisk = {name.lower(): name for name in os.listdir(directory or ".")}
    except OSError:
        return []
    found: list[str] = []
    for pattern, replacement in _GEOMETRY_NAME_PATTERNS:
        newStem, nSubs = pattern.subn(replacement, stem)
        name = onDisk.get(newStem + ".img") if nSubs and newStem != stem else None
        path = os.path.join(directory, name) if name else None
        if path and path not in found:
            found.append(path)
    return found


def resolveGeometryFile(sourceFilename: str) -> str | None:
    """The first geometry companion of a CRISM ``.img``/``.hdr`` path found on
    disk (see :func:`geometryFileCandidates`), or None."""
    found = geometryFileCandidates(sourceFilename)
    return found[0] if found else None


@attrs.frozen
class Incidence:
    """The solar incidence angle at the areoid over a CRISM scene, in degrees."""

    mean: float
    low: float
    high: float
    path: str  # the DDR it was read from
    route: str = ""  # how that file was found


def sceneIncidenceFor(image: VardaRaster, chosen: str = "") -> Incidence | None:
    """The solar incidence angle at the areoid (the DDR's "INA at areoid"
    band) over ``image``'s pixels, from ``chosen`` when given (a file picked
    for this use, route "override"), else the first geometry file that has it
    (:func:`findGeometryFile`); None without one. The DDR gives no solar
    azimuth."""
    match = (
        _firstMatch([GeometryCandidate(chosen, ROUTE_OVERRIDE)], image, INCIDENCE)
        if chosen
        else findGeometryFile(image, INCIDENCE)
    )
    if match is None:
        return None
    return _readIncidence(match, (image.height, image.width))


def _readIncidence(match: GeometryMatch, shape: tuple[int, int]) -> Incidence | None:
    try:
        with rasterio.open(match.path) as geom:
            index = findBandIndex(geom.descriptions or (), INCIDENCE)
            if index is None:
                return None
            pixels = _readWindow(match.window, shape)
            values = geom.read(index, window=pixels).astype(np.float64)
            if geom.nodata is not None:
                values[values == geom.nodata] = np.nan
    except RasterioError as e:
        logger.warning("Failed to read DDR geometry %s: %s", match.path, e)
        return None
    # CRISM marks missing values with 65535
    values[(values < 0) | (values > 180)] = np.nan
    if not np.isfinite(values).any():
        return None
    return Incidence(
        float(np.nanmean(values)),
        float(np.nanmin(values)),
        float(np.nanmax(values)),
        match.path,
        match.route,
    )
