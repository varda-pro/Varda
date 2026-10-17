"""Isolated CRISM geometry (DDR backplane) support for column-locked ROI placement.

CRISM reflectance products ship a separate geometry "DDR" file whose bands give,
per pixel, the detector column ("IR Sample") and the source strip identity
("Target ID" + "Segment ID"). This module resolves that companion file and
computes the horizontal shift needed to place a copied ROI on the same detector
column as its template. It is deliberately CRISM-specific and self-contained: no
general multi-instrument abstraction until a second instrument needs one.
"""

from __future__ import annotations

import logging
import math
import os
import re

import attrs
import numpy as np
import rasterio
from rasterio.errors import RasterioError

logger = logging.getLogger(__name__)


@attrs.define
class ColumnGeometry:
    """Per-pixel CRISM geometry arrays, all shape (height, width).

    ``ir_sample`` is the detector column index; ``target_id`` and ``segment_id``
    identify the source observation strip. Missing values are NaN.
    """

    ir_sample: np.ndarray
    target_id: np.ndarray | None = None
    segment_id: np.ndarray | None = None


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


_geometry_cache: dict[str, ColumnGeometry | None] = {}


def loadColumnGeometry(sourceFilename: str) -> ColumnGeometry | None:
    """Resolve and read the DDR geometry for a CRISM source file, or None.

    Results (including None) are cached by source path so repeated placements do
    not re-read the (large) DDR file.
    """
    import rasterio as rio  # local import: keep module import-light

    if sourceFilename in _geometry_cache:
        return _geometry_cache[sourceFilename]

    geomPath = resolveGeometryFile(sourceFilename)
    if geomPath is None:
        _geometry_cache[sourceFilename] = None
        return None
    try:
        with rio.open(geomPath) as geom:
            descs = geom.descriptions or ()
            irIdx = findBandIndex(descs, "ir_sample")
            if irIdx is None:
                logger.warning(
                    "DDR %s has no IR Sample band; column-lock unavailable", geomPath
                )
                _geometry_cache[sourceFilename] = None
                return None
            tgtIdx = findBandIndex(descs, "target_id")
            segIdx = findBandIndex(descs, "segment_id")
            nodata = geom.nodata

            def _readBand(arr: np.ndarray) -> np.ndarray:
                arr = arr.astype(np.float64)
                if nodata is not None:
                    arr = np.where(arr == nodata, np.nan, arr)
                return arr

            def _readOptional(idx: int | None) -> np.ndarray | None:
                if idx is None:
                    return None
                return _readBand(geom.read(idx))

            result = ColumnGeometry(
                ir_sample=_readBand(geom.read(irIdx)),
                target_id=_readOptional(tgtIdx),
                segment_id=_readOptional(segIdx),
            )
            _geometry_cache[sourceFilename] = result
            return result
    except Exception as e:  # pragma: no cover - I/O error path
        logger.warning("Failed to read DDR geometry %s: %s", geomPath, e)
        _geometry_cache[sourceFilename] = None
        return None


# (pattern, replacement) applied to the filename stem to find the DDR companion.
_GEOMETRY_NAME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"_mrr(al|if|ir|sr|su|ra)_", re.IGNORECASE), "_mrrde_"),
    (re.compile(r"_vrr(al|if|ir|sr|su|ra)_", re.IGNORECASE), "_vrrde_"),
    (re.compile(r"_(if|sr|su)(\d{3}[a-z])", re.IGNORECASE), r"_in\2"),
]


def resolveGeometryFile(sourceFilename: str) -> str | None:
    """Map a CRISM source ``.img``/``.hdr`` path to its geometry companion path.

    Returns the companion ``.img`` path if it exists on disk, else None.
    """
    directory = os.path.dirname(sourceFilename)
    filename = os.path.basename(sourceFilename)
    stem, _ext = os.path.splitext(filename)

    for pattern, replacement in _GEOMETRY_NAME_PATTERNS:
        newStem, nSubs = pattern.subn(replacement, stem)
        if nSubs == 0 or newStem == stem:
            continue
        candidate = os.path.join(directory, newStem + ".img")
        if os.path.exists(candidate):
            logger.debug(f"CRISM GEOMETRY: geometry file path: {candidate}")
            return candidate
    logger.debug("CRISM GEOMETRY: NO geometry file path")

    return None


@attrs.frozen
class Incidence:
    """The solar incidence angle at the areoid over a CRISM scene, in degrees."""

    mean: float
    low: float
    high: float
    path: str  # the DDR it was read from


def sceneIncidence(sourceFilename: str) -> Incidence | None:
    """The solar incidence angle at the areoid (the DDR's "INA at areoid"
    band) over the scene of a CRISM product, or None without a DDR that has
    it. The DDR gives no solar azimuth."""
    geomPath = resolveGeometryFile(sourceFilename)
    if geomPath is None:
        return None
    try:
        with rasterio.open(geomPath) as geom:
            index = findBandIndex(geom.descriptions or (), "incidence")
            if index is None:
                return None
            values = geom.read(index).astype(np.float64)
            if geom.nodata is not None:
                values[values == geom.nodata] = np.nan
    except RasterioError as e:
        logger.warning("Failed to read DDR geometry %s: %s", geomPath, e)
        return None
    # CRISM marks missing values with 65535
    values[(values < 0) | (values > 180)] = np.nan
    if not np.isfinite(values).any():
        return None
    return Incidence(
        float(np.nanmean(values)),
        float(np.nanmin(values)),
        float(np.nanmax(values)),
        geomPath,
    )
