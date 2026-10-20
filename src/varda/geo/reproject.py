"""Reprojecting rasters between map projections of one body (spec §4.3): the
output grid, the warp, its quality measures and its record. No Qt.

The output grid (``gridFor``):

- Extent: the source's footprint in the target CRS, from its edges
  densified to 21 points each and moved with ``transformPoints``; when the
  source contains a pole, the footprint includes it.
- Pixel size: ``resolution`` ground meters at the footprint's center become
  map units through the target's areal scale there (PROJ's scale factors;
  Snyder, 1987, ch. 4): map pixel = resolution × √(areal scale) / (meters
  per map unit). For a geographic target, square degrees: Δφ = resolution /
  M, M the meridional radius of curvature at the center (Snyder, 1987,
  eq. 4-18). The default resolution is the source's ground pixel size at
  its center, √(dx·dy) (``sourceResolution``), which keeps a pixel's area.
- Alignment: the bounds snap outward to whole multiples of the pixel size,
  as ``gdalwarp -tap`` does, so products made at one resolution share pixel
  edges.
- Antimeridian: a footprint that crosses a geographic or (pseudo)cylindrical
  target's cut (180° from its central meridian) is refused, naming a
  ``lon_0`` at the scene's center, never made a world-wide grid.
- Size: more than ``MAX_OUTPUT_PIXELS`` output pixels is refused before
  anything is allocated, naming a coarser resolution that fits.

The warp (``reprojectBand``) is GDAL's warper through
``rasterio.warp.reproject``, with ``geo.transform.gdalWarp``: between
surfaces of one body positions keep their planetocentric longitude and
latitude (§4.3.1), and registered datum shifts keep PROJ's own operation.
GDAL runs outside ``PROJ_LOCK``; the pyproj work holds it."""

from __future__ import annotations

import math
import re

import numpy as np
import pyproj
import rasterio
from affine import Affine
from attrs import frozen
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.warp import reproject

from varda.geo.bodies import latitudeType
from varda.geo.describe import describeCrs
from varda.geo.distortion import arealScaleAtPixels
from varda.geo.equivalence import canTransform
from varda.geo.measure import groundSpacing
from varda.geo.recommend import Footprint, footprintLonLat
from varda.geo.transform import (
    eastLonLat,
    gdalWarp,
    isGeographic,
    longitudePeriod,
    ographicLatitude,
    transformBounds,
    transformPoints,
)
from varda.utilities.proj_lock import PROJ_LOCK

MAX_OUTPUT_PIXELS = 400_000_000
# points per edge of an image's footprint
_EDGE_POINTS = 21
# output pixels moved at once by sourcePixelDistance (bounds its temporaries)
_CHUNK_PIXELS = 262_144
# samples per side of the output for scaleChange
_SCALE_GRID = 9
# longitudes this close (degrees) to a target's cut don't cross it
_CUT_TOLERANCE = 1e-9
# how close to the cut the extent of a polar footprint reaches, degrees
_CUT_INSET = 1e-7
_LON_WRAP = re.compile(r"\+lon_wrap=([-+0-9.eE]+)")
# (pseudo)cylindrical projections, cut 180° from their central meridian
_WRAPPING_METHODS = (
    "cylindrical",
    "equirectangular",
    "mercator",
    "sinusoidal",
    "mollweide",
    "robinson",
    "eckert",
    "miller",
    "plate carr",
    "equal earth",
    "gall",
)


@frozen
class TargetGrid:
    """An output pixel grid: its CRS (WKT, or PROJJSON when WKT would lose
    an axis direction), its north-up transform (a, b, c, d, e, f) and its
    size in pixels."""

    crs: str
    transform: tuple[float, float, float, float, float, float]
    width: int
    height: int

    @property
    def affine(self) -> Affine:
        return Affine(*self.transform)


@frozen
class _Target:
    """What the output grid needs to know about a target CRS."""

    geographic: bool
    wraps: bool  # geographic or (pseudo)cylindrical: cut at lon0 + 180°
    lon0: float  # central meridian, degrees east (lon_wrap for geographic)
    # meters per map unit (projected), degrees per unit (geographic)
    unit: float
    west: bool  # geographic with longitude counting west


# --- the output grid ------------------------------------------------------------


def sourceResolution(
    srcCrs: CRS | str, srcTransform: Affine, width: int, height: int
) -> float:
    """The source's ground pixel size at its center pixel, √(dx·dy) in
    meters on its own surface (``groundSpacing``): a square pixel of this
    size has the center pixel's area. Raises ValueError where it can't be
    measured."""
    dx, dy = groundSpacing(
        srcCrs, srcTransform, np.array([height // 2]), np.array([width // 2])
    )
    size = float(np.sqrt(dx[0] * dy[0]))
    if not (math.isfinite(size) and size > 0):
        raise ValueError("The image's pixel size can't be measured at its center.")
    return size


def gridFor(
    srcCrs: CRS | str,
    srcTransform: Affine,
    width: int,
    height: int,
    dstCrs: CRS | str,
    *,
    resolution: float | None = None,
    extent: tuple[float, float, float, float] | None = None,
) -> TargetGrid:
    """The output grid of an image of ``width`` × ``height`` pixels with
    ``srcTransform`` on ``srcCrs`` reprojected to ``dstCrs`` (the module's
    rules): ``resolution`` ground meters per pixel at the output's center
    (None or 0: ``sourceResolution``), over ``extent`` (west, south, east,
    north in ``dstCrs``'s map units) or the source's footprint. Raises
    ValueError with a user-facing message: another body, an extent across
    the antimeridian, or too many pixels."""
    reason = canTransform(srcCrs, dstCrs)
    if reason is not None:
        raise ValueError(reason)
    if resolution is None or resolution == 0:
        resolution = sourceResolution(srcCrs, srcTransform, width, height)
    elif not (math.isfinite(resolution) and resolution > 0):
        raise ValueError(
            f"The resolution must be a positive number of meters, not {resolution}."
        )
    target = _target(dstCrs)
    if extent is None:
        footprint = footprintLonLat(srcCrs, srcTransform, width, height)
        _refuseAntimeridian(footprint, target)
        west, south, east, north = _extentIn(
            srcCrs, srcTransform, width, height, dstCrs, footprint, target
        )
    else:
        west, south, east, north = (float(v) for v in extent)
        if not (west < east and south < north):
            raise ValueError(
                f"The extent {extent} is empty (west, south, east, north)."
            )
    size = _pixelSize(
        dstCrs, target, resolution, (west + east) / 2, (south + north) / 2
    )
    west, east = math.floor(west / size) * size, math.ceil(east / size) * size
    south, north = math.floor(south / size) * size, math.ceil(north / size) * size
    cols, rows = (
        max(1, round((east - west) / size)),
        max(1, round((north - south) / size)),
    )
    if cols * rows > MAX_OUTPUT_PIXELS:
        coarser = _roundUp(
            resolution * math.sqrt(cols * rows / MAX_OUTPUT_PIXELS) * 1.01
        )
        raise ValueError(
            f"The output would be {cols:,} × {rows:,} = {cols * rows:,} pixels at "
            f"{resolution:,.12g} m, more than {MAX_OUTPUT_PIXELS:,}. Use a resolution "
            f"of at least {coarser:,.12g} m."
        )
    return TargetGrid(
        _crsText(dstCrs), (size, 0.0, west, 0.0, -size, north), cols, rows
    )


def _target(crs: CRS | str) -> _Target:
    with PROJ_LOCK:
        parsed = CRS.from_user_input(crs)
        geographic = isGeographic(parsed)
        unit = parsed.axis_info[0].unit_conversion_factor if parsed.axis_info else 1.0
        operation = parsed.coordinate_operation
        method = operation.method_name.lower() if operation is not None else ""
        remarks = str(parsed.to_json_dict().get("remarks", ""))
    description = describeCrs(crs)
    if geographic:
        # PROJ applies +lon_wrap to its own east-positive longitudes, before
        # any axis direction, so lon0 is east here too
        wrap = _LON_WRAP.search(remarks)
        lon0 = float(wrap.group(1)) if wrap else 0.0
    else:
        lon0 = description.centerLongitude or 0.0
    wraps = geographic or (
        "transverse" not in method and any(m in method for m in _WRAPPING_METHODS)
    )
    return _Target(
        geographic=geographic,
        wraps=wraps,
        lon0=lon0,
        unit=math.degrees(unit) if geographic else unit,
        west=geographic and description.longitudeDirection == "west",
    )


def _refuseAntimeridian(footprint: Footprint, target: _Target) -> None:
    """Raises when the footprint crosses the target's cut, 180° from its
    central meridian (a footprint around a pole needs every longitude)."""
    if not target.wraps or footprint.pole is not None:
        return
    cut = target.lon0 + 180.0
    west, east = footprint.west + _CUT_TOLERANCE, footprint.east - _CUT_TOLERANCE
    first = math.ceil((west - cut) / 360.0)
    if cut + 360.0 * first >= east:
        return
    center = round((footprint.center[0] + 180.0) % 360.0 - 180.0) or 0.0
    suggestion = 180.0 if center == -180.0 else center
    option = "+lon_wrap" if target.geographic else "+lon_0"
    raise ValueError(
        f"The image crosses the target's antimeridian (180° from its central "
        f"meridian, lon_0 = {target.lon0:g}°), so its grid would wrap around the "
        f"body. Use a coordinate system centered on the scene, e.g. lon_0 = "
        f"{suggestion:g}° ({option}={suggestion:g})."
    )


def _edgePixels(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixel positions (cols, rows) along an image's edges, 21 per edge."""
    t = np.linspace(0.0, 1.0, _EDGE_POINTS)
    cols = np.concatenate(
        [t * width, np.full_like(t, width), (1 - t) * width, np.zeros_like(t)]
    )
    rows = np.concatenate(
        [np.zeros_like(t), t * height, np.full_like(t, height), (1 - t) * height]
    )
    return cols, rows


def _extentIn(
    srcCrs: CRS | str,
    srcTransform: Affine,
    width: int,
    height: int,
    dstCrs: CRS | str,
    footprint: Footprint,
    target: _Target,
) -> tuple[float, float, float, float]:
    """The source's footprint (west, south, east, north) in ``dstCrs``."""
    xs, ys = transformPoints(
        srcCrs, dstCrs, *(srcTransform * _edgePixels(width, height))
    )
    finite = np.isfinite(xs) & np.isfinite(ys)
    xs, ys = xs[finite], ys[finite]
    if target.geographic and xs.size:
        xs = _intoDomain(xs, target, around=footprint.pole is not None)
    poleXs, poleYs = _polePoints(dstCrs, footprint, target)
    xs, ys = np.concatenate([xs, poleXs]), np.concatenate([ys, poleYs])
    finite = np.isfinite(xs) & np.isfinite(ys)
    if not finite.any():
        raise ValueError("The image can't be placed on this coordinate system.")
    xs, ys = xs[finite], ys[finite]
    return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())


def _intoDomain(xs: np.ndarray, target: _Target, *, around: bool) -> np.ndarray:
    """Geographic longitudes along an image's edge (in the target's units)
    moved by whole turns into the target's domain, the 360° up to its cut:
    together, unwrapped along the edge, or each on its own for an edge
    ``around`` a pole (which spans the whole domain)."""
    period = 360.0 / target.unit
    # x counts west: x = −longitude
    low = (-(target.lon0 + 180.0) if target.west else target.lon0 - 180.0) / target.unit
    if around:
        return low + np.mod(xs - low, period)
    xs = np.unwrap(xs, period=period)
    turns = math.floor((float(xs.min()) - low) / period + _CUT_TOLERANCE)
    return xs - turns * period


def _polePoints(
    dstCrs: CRS | str, footprint: Footprint, target: _Target
) -> tuple[np.ndarray, np.ndarray]:
    """The target's map points the footprint reaches because it contains a
    pole: the pole, and on a wrapping target every longitude up to its cut
    (at the pole, the footprint's far latitude and the equator if inside
    it). Longitudes and latitudes are on the target's own datum; near the
    cut only x matters, and the grid snaps outward."""
    if footprint.pole is None:
        return np.empty(0), np.empty(0)
    poles = {"north": [90.0], "south": [-90.0], "both": [90.0, -90.0]}[footprint.pole]
    lats = [*poles, footprint.south, footprint.north]
    if footprint.south < 0 < footprint.north:
        lats.append(0.0)
    if target.wraps:
        lons = [
            target.lon0 - 180.0 + _CUT_INSET,
            target.lon0,
            target.lon0 + 180.0 - _CUT_INSET,
        ]
    else:
        lons, lats = [target.lon0], poles
    grid = np.meshgrid(np.array(lons), np.array(lats))
    return transformPoints(eastLonLat(dstCrs), dstCrs, grid[0].ravel(), grid[1].ravel())


def _pixelSize(
    dstCrs: CRS | str, target: _Target, resolution: float, x: float, y: float
) -> float:
    """``resolution`` meters on the ground, at the map point (x, y), in the
    target's map units."""
    if target.geographic:
        with PROJ_LOCK:
            parsed = CRS.from_user_input(dstCrs)
            ellipsoid = parsed.ellipsoid
            assert ellipsoid is not None  # canTransform found the body
            a, c = ellipsoid.semi_major_metre, ellipsoid.semi_minor_metre
            centric = latitudeType(parsed) == "planetocentric"
        latitude = y * target.unit
        if centric:
            latitude = float(ographicLatitude(latitude, a, c))
        e2 = 1 - (c / a) ** 2
        sine = math.sin(math.radians(latitude))
        meridional = a * (1 - e2) / (1 - e2 * sine * sine) ** 1.5
        return math.degrees(resolution / meridional) / target.unit
    areal = float(
        arealScaleAtPixels(dstCrs, Affine.identity(), np.array([x]), np.array([y]))[0]
    )
    if not math.isfinite(areal):
        raise ValueError(
            "The projection's scale can't be computed at the output's center."
        )
    return resolution * math.sqrt(areal) / target.unit


def _roundUp(value: float) -> float:
    """``value`` rounded up to two significant figures."""
    step = 10.0 ** (math.floor(math.log10(value)) - 1)
    return math.ceil(value / step) * step


def _crsText(crs: CRS | str) -> str:
    """The CRS as WKT, or as PROJJSON when WKT would lose an axis direction
    (a west-positive base CRS). Holds PROJ_LOCK."""
    with PROJ_LOCK:
        parsed = CRS.from_user_input(crs)
        wkt = parsed.to_wkt()
        if wkt and _directions(CRS.from_wkt(wkt)) == _directions(parsed):
            return wkt
        return parsed.to_json()


def _directions(crs: CRS) -> list[str]:
    geodetic = crs.geodetic_crs
    base = geodetic.axis_info if geodetic is not None else []
    return [axis.direction for axis in (*crs.axis_info, *base)]


# --- the warp ---------------------------------------------------------------------


def reprojectBand(
    band: np.ndarray,
    srcCrs: CRS | str,
    srcTransform: Affine,
    grid: TargetGrid,
    method: Resampling,
) -> np.ndarray:
    """One band (float64, rows × cols, NaN for no data) on ``grid``, as
    float32 (grid.height × grid.width), NaN outside the source or with no
    source data under it: GDAL's warper with ``method`` and
    ``gdalWarp`` (planetocentric positions kept between surfaces, x-first
    axes for GDAL), run outside PROJ_LOCK. A geographic source's longitudes are moved by a whole
    turn when that puts it over the grid (0–360 sources on −180..180
    grids). Raises ValueError across bodies."""
    warp = gdalWarp(srcCrs, grid.crs)
    height, width = band.shape
    shift = _longitudeShift(srcCrs, srcTransform, width, height, grid)
    out = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    reproject(
        source=np.ascontiguousarray(band, dtype=np.float64),
        destination=out,
        src_transform=Affine.translation(-shift, 0.0) * srcTransform,
        src_crs=warp.srcCrs,
        src_nodata=np.nan,
        dst_transform=grid.affine,
        dst_crs=warp.dstCrs,
        dst_nodata=np.nan,
        resampling=method,
        **warp.options,
    )
    return out


def _longitudeShift(
    srcCrs: CRS | str, srcTransform: Affine, width: int, height: int, grid: TargetGrid
) -> float:
    """The whole turn (0, +360° or −360°, in the source's units) to take
    from a geographic source's longitudes so it lies over ``grid`` (as
    ``common.grid.onGridOf`` does); 0 for a projected source or when no
    turn makes them overlap."""
    period = longitudePeriod(srcCrs)
    if period is None:
        return 0.0
    try:
        west, _, east, _ = transformBounds(
            grid.crs, srcCrs, grid.affine, grid.width, grid.height
        )
    except ValueError:
        return 0.0
    corners = srcTransform * (np.array([0.0, width]), np.array([0.0, height]))
    left, right = float(min(corners[0])), float(max(corners[0]))
    for shift in (0.0, period, -period):
        if west + shift < right and east + shift > left:
            return shift
    return 0.0


def _intoSource(
    srcCrs: CRS | str, srcTransform: Affine, width: int, xs: np.ndarray
) -> np.ndarray:
    """Longitudes on a geographic source moved by whole turns to the
    representation nearest the source's center (x unchanged when
    projected)."""
    period = longitudePeriod(srcCrs)
    if period is None:
        return xs
    center = (srcTransform * (width / 2, 0.0))[0]
    return xs + period * np.round((center - xs) / period)


# --- quality ----------------------------------------------------------------------


def sourcePixelDistance(
    srcCrs: CRS | str,
    srcTransform: Affine,
    srcWidth: int,
    srcHeight: int,
    grid: TargetGrid,
    chunkRows: int = 256,
) -> np.ndarray:
    """For each output pixel (float32, grid.height × grid.width), the
    distance in source pixels from its center to the nearest source pixel
    center: the resampling's interpolation distance (0 on the source's own
    grid, up to √2/2). Each center is moved into the source with
    ``transformPoints``, ``chunkRows`` rows at a time (fewer for a wide
    grid: at most 262,144 pixels per chunk); NaN outside the
    source."""
    out = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    inverse = ~srcTransform
    cols = np.arange(grid.width, dtype=np.float64) + 0.5
    perChunk = max(1, min(chunkRows, _CHUNK_PIXELS // max(1, grid.width)))
    for first in range(0, grid.height, perChunk):
        rows = np.arange(first, min(first + perChunk, grid.height)) + 0.5
        c, r = np.meshgrid(cols, rows)
        xs, ys = transformPoints(
            grid.crs, srcCrs, *(grid.affine * (c.ravel(), r.ravel()))
        )
        xs = _intoSource(srcCrs, srcTransform, srcWidth, xs)
        col, row = inverse * (xs, ys)
        with np.errstate(invalid="ignore"):
            inside = (col >= 0) & (col < srcWidth) & (row >= 0) & (row < srcHeight)
            distance = np.hypot(
                col - (np.floor(col) + 0.5), row - (np.floor(row) + 0.5)
            )
        out[first : first + len(rows)] = np.where(inside, distance, np.nan).reshape(
            c.shape
        )
    return out


def scaleChange(
    srcCrs: CRS | str,
    srcTransform: Affine,
    srcWidth: int,
    srcHeight: int,
    grid: TargetGrid,
) -> tuple[float, float]:
    """(max, mean) of the output's ground pixel size √(dx·dy) over the
    source's at the same place (``groundSpacing``), on 9 × 9 output pixels
    whose centers fall inside the source; (NaN, NaN) when none does."""
    rows = ((np.arange(_SCALE_GRID) + 0.5) * grid.height / _SCALE_GRID).astype(int)
    cols = ((np.arange(_SCALE_GRID) + 0.5) * grid.width / _SCALE_GRID).astype(int)
    r, c = (v.ravel() for v in np.meshgrid(rows, cols, indexing="ij"))
    dx, dy = groundSpacing(grid.crs, grid.affine, r, c)
    xs, ys = transformPoints(grid.crs, srcCrs, *(grid.affine * (c + 0.5, r + 0.5)))
    xs = _intoSource(srcCrs, srcTransform, srcWidth, xs)
    col, row = ~srcTransform * (xs, ys)
    with np.errstate(invalid="ignore"):
        inside = (col >= 0) & (col < srcWidth) & (row >= 0) & (row < srcHeight)
    if not inside.any():
        return math.nan, math.nan
    sdx, sdy = groundSpacing(
        srcCrs, srcTransform, np.floor(row[inside]), np.floor(col[inside])
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.sqrt(dx[inside] * dy[inside]) / np.sqrt(sdx * sdy)
    ratio = ratio[np.isfinite(ratio)]
    if ratio.size == 0:
        return math.nan, math.nan
    return float(ratio.max()), float(ratio.mean())


# --- the record -------------------------------------------------------------------


def reprojectionRecord(
    srcCrs: CRS | str,
    grid: TargetGrid,
    method: Resampling,
    resolutionM: float,
    *,
    scale: tuple[float, float],
) -> dict:
    """The ``reprojection`` metadata of a result (plain JSON values; NaN
    becomes None): ``from`` and ``to`` (each ``crs`` text and
    ``description``), ``method``, ``resolutionM``, ``grid`` (transform,
    width, height), ``coordinateOperation`` (the PROJ pipeline GDAL's
    warper ran, with GDAL's x-first axis order, or "PROJ" for PROJ's own), the ``gdal`` and ``proj`` versions, and
    ``scaleChange`` ``{max, mean}`` (``scale``, from ``scaleChange``).
    Raises ValueError across bodies."""
    # the pipeline as GDAL ran it (x-first axes), or "PROJ"
    operation = gdalWarp(srcCrs, grid.crs).operation
    gdalProj = getattr(rasterio, "__proj_version__", None)
    proj = pyproj.__proj_version__
    if gdalProj and gdalProj != proj:
        proj = f"{gdalProj} (GDAL warper); {proj} (pyproj)"
    return {
        "from": {"crs": _crsText(srcCrs), "description": describeCrs(srcCrs).text},
        "to": {"crs": grid.crs, "description": describeCrs(grid.crs).text},
        "method": method.name,
        "resolutionM": _plain(resolutionM),
        "grid": {
            "transform": [float(v) for v in grid.transform],
            "width": int(grid.width),
            "height": int(grid.height),
        },
        "coordinateOperation": operation,
        "gdal": rasterio.__gdal_version__,
        "proj": proj,
        "scaleChange": {"max": _plain(scale[0]), "mean": _plain(scale[1])},
    }


def _plain(value: float) -> float | None:
    value = float(value)
    return value if math.isfinite(value) else None
