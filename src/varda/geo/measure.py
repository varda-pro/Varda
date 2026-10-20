"""Lengths, areas and pixel spacing measured on the body, whatever the
image's projection (spec §4.5).

Map coordinates become longitude and planetocentric latitude on the CRS's
own surface (``transform.planetocentricLonLat``), then geodetic latitude on
that surface, and the geodesics between them are measured on its ellipsoid
or sphere (Karney, 2013, through ``pyproj.Geod``): the surface the data were
projected on, as ISIS, ArcGIS and QGIS measure.

The body-model term: when the body's IAU 2015 reference ellipsoid
(``Body.referenceCrs``) differs from the CRS's surface (a or c by more than
1 m), the same points (the same planetocentric longitude and latitude) are
measured on it too, with tan φg = (a/c)² tan φc (Snyder, 1987, eq. 3-28),
and the relative difference is reported.

Pixel areas (``pixelAreas``): where a pixel's area depends on its row only
(no rotation, and a geographic CRS or an equidistant cylindrical, Mercator
or cylindrical equal-area projection), each row's area is the geodesic
polygon area of one pixel's corners, each edge densified to 16 points, so
its only error is that the geodesic chords between the 16 points cut
inside the parallels: relative error ≈ δλ² (cos² φ − 2 sin² φ) / 12 with
δλ = Δλ / 16 in radians (Δλ the pixel's width in longitude), at most about
2.1e-7 × (Δλ / 1°)² (and Karney's algorithm, nanometers). Otherwise it's the map pixel area
over PROJ's areal scale at the pixel center (relative error of order
(pixel / R)²).

Not included: topography (measurements are horizontal, on the reference
surface) and the image's georeferencing error."""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import numpy.typing as npt
import shapely
from affine import Affine
from attrs import frozen
from pyproj import CRS, Geod
from pyproj.exceptions import CRSError

from varda.geo.bodies import identifyBody, latitudeType, surfaceText
from varda.geo.distortion import (
    DistortionSummary,
    arealScaleAtPixels,
    distortionOver,
)
from varda.geo.transform import (
    isGeographic,
    ographicLatitude,
    planetocentricLonLat,
    transformPoints,
)
from varda.utilities.proj_lock import PROJ_LOCK

# radii closer than this to the reference ellipsoid's are the same surface
SAME_SURFACE_M = 1.0

AREA_METHOD_ROWS = "exact per row (geodesic polygon, edges densified to 16 points)"
AREA_METHOD_SCALE = (
    "map pixel area over the projection's areal scale at the pixel center"
)
# points per pixel edge for the exact row areas
_EDGE_POINTS = 16
# imagePixelAreas evaluates the areas of larger images outside the row-only
# case on a grid of every n-th pixel and interpolates (spec §4.5)
_MAX_EVALUATED_PIXELS = 4_000_000
# grid points evaluated at once by imagePixelAreas (bounds its temporaries)
_BLOCK_POINTS = 65_536
# projections whose scale depends on latitude only: north-up, a pixel's
# area depends on its row only
_ROW_ONLY_METHODS = frozenset(
    {
        "Equidistant Cylindrical",
        "Equidistant Cylindrical (Spherical)",
        "Mercator (variant A)",
        "Mercator (variant B)",
        "Popular Visualisation Pseudo Mercator",
        "Lambert Cylindrical Equal Area",
        "Lambert Cylindrical Equal Area (Spherical)",
    }
)


@frozen
class Measurement:
    """A length or area on the body, how it was measured, and how much the
    body's reference ellipsoid would change it."""

    value: float  # meters or square meters
    unit: str  # "m" | "m²"
    method: str  # "geodesic (Karney 2013) on sphere R = 3396.19 km"
    # the same on the body's IAU 2015 reference ellipsoid, or None when that
    # is the CRS's own surface (or the body is unknown)
    reference: float | None
    bodyModelDifference: float  # |value − reference| / reference; 0 without one
    # the image projection's distortion over the measured geometry (tips
    # only: the value never uses it)
    distortion: DistortionSummary | None


@frozen(eq=False)
class _Surface:
    """An ellipsoid (or sphere) to measure on."""

    geod: Geod
    a: float
    c: float

    def latitudes(self, centric: np.ndarray) -> np.ndarray:
        """Planetocentric latitudes as this surface's geodetic latitudes."""
        return ographicLatitude(centric, self.a, self.c)


@frozen(eq=False)
class _Surfaces:
    own: _Surface
    reference: _Surface | None
    method: str
    surface: str  # "sphere R = 3396.19 km"


@frozen
class _Layout:
    """What pixel areas need to know about a CRS."""

    rowOnly: bool  # geographic, or a projection in _ROW_ONLY_METHODS
    geographic: bool
    unit: float  # the map unit in meters, or radians when geographic
    # geographic, with planetocentric latitude numbers
    centricNumbers: bool


def toLonLat(crs: CRS | str, xy: npt.ArrayLike) -> np.ndarray:
    """(n, 2) map points on ``crs`` as (n, 2) longitude, latitude in
    ``crs.geodetic_crs``'s own units, directions and latitude type, longitude
    first whatever its axis order (``transformPoints``). Holds PROJ_LOCK."""
    points = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    with PROJ_LOCK:
        geodetic = CRS.from_user_input(crs).geodetic_crs
        if geodetic is None:
            raise ValueError(f"No longitude and latitude on this CRS: {crs}")
        lon, lat = transformPoints(crs, geodetic, points[:, 0], points[:, 1])
    return np.column_stack([lon, lat])


def cumulativeDistance(crs: CRS | str, xy: npt.ArrayLike) -> np.ndarray:
    """Meters along (n, 2) map points on ``crs`` from the first: the sum of
    the geodesics between consecutive points on the CRS's own surface.

    A point PROJ can't invert (off the projection) makes the segments
    touching it NaN, so every distance from it on is NaN; NumPy may warn
    ("invalid value"). Raises ValueError for a CRS on no body or one PROJ
    can't read (``planetocentricLonLat``)."""
    points = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    surfaces = _surfacesFor(crs)
    lon, centric = planetocentricLonLat(crs, points[:, 0], points[:, 1])
    segments = _segments(surfaces.own, lon, centric)
    return np.concatenate([[0.0], np.cumsum(segments)])


def lineLength(
    crs: CRS | str, xy: npt.ArrayLike, *, transform: Affine | None = None
) -> Measurement:
    """The length of the line through (n, 2) map points on ``crs``, as
    drawn: the caller densifies it (about 1 px) to follow the image. With
    the image's ``transform``, also the distortion over its bounding box.

    The value is NaN when PROJ can't invert any of the points (see
    ``cumulativeDistance``); raises ValueError as it does."""
    points = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    surfaces = _surfacesFor(crs)
    lon, centric = planetocentricLonLat(crs, points[:, 0], points[:, 1])
    value = float(np.sum(_segments(surfaces.own, lon, centric)))
    reference = (
        float(np.sum(_segments(surfaces.reference, lon, centric)))
        if surfaces.reference is not None
        else None
    )
    return _measurement(
        value, reference, "m", surfaces.method, _distortion(crs, transform, points)
    )


def polygonArea(
    crs: CRS | str,
    polygon: shapely.Polygon | shapely.MultiPolygon,
    densifyStep: float,
    *,
    transform: Affine | None = None,
) -> tuple[Measurement, Measurement]:
    """The area and perimeter on the body of a polygon in ``crs``'s map
    coordinates. Edges are densified to ``densifyStep`` map units (the
    caller passes the pixel size) before conversion, so they follow the
    straight map edges; holes are subtracted from the area (and their rings
    added to the perimeter); a MultiPolygon's parts are summed. With the
    image's ``transform``, also the distortion over its bounding box."""
    surfaces = _surfacesFor(crs)
    dense = shapely.segmentize(polygon, densifyStep)
    parts = list(dense.geoms) if isinstance(dense, shapely.MultiPolygon) else [dense]
    rings = [
        (np.asarray(ring.coords, dtype=np.float64), hole)
        for part in parts
        for ring, hole in [(part.exterior, False)]
        + [(interior, True) for interior in part.interiors]
    ]
    converted = [
        (planetocentricLonLat(crs, coords[:, 0], coords[:, 1]), hole)
        for coords, hole in rings
    ]

    def measured(surface: _Surface) -> tuple[float, float]:
        area = perimeter = 0.0
        for (lon, centric), hole in converted:
            with PROJ_LOCK:
                ringArea, ringPerimeter = surface.geod.polygon_area_perimeter(
                    lon, surface.latitudes(centric)
                )
            area += -abs(ringArea) if hole else abs(ringArea)
            perimeter += ringPerimeter
        return area, perimeter

    area, perimeter = measured(surfaces.own)
    reference = measured(surfaces.reference) if surfaces.reference else None
    bounds = np.array(polygon.bounds, dtype=np.float64).reshape(2, 2)
    distortion = _distortion(crs, transform, bounds)
    return (
        _measurement(
            area,
            reference[0] if reference else None,
            "m²",
            surfaces.method,
            distortion,
        ),
        _measurement(
            perimeter,
            reference[1] if reference else None,
            "m",
            surfaces.method,
            distortion,
        ),
    )


def groundSpacing(
    crs: CRS | str, transform: Affine, rows: np.ndarray, cols: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """The ground size, in meters on the CRS's own surface, of the pixels at
    (``rows``, ``cols``) (integer indices; broadcast together): across
    columns (the geodesic from the middle of its left edge to the middle of
    its right edge) and across rows (top edge to bottom edge). NaN where
    PROJ can't invert. Terrain analyses' per-pixel dx and dy (Phase 3)."""
    rows, cols = np.broadcast_arrays(
        np.asarray(rows, dtype=np.float64), np.asarray(cols, dtype=np.float64)
    )
    surfaces = _surfacesFor(crs)
    centerCol, centerRow = cols + 0.5, rows + 0.5

    def spacing(
        c0: np.ndarray, r0: np.ndarray, c1: np.ndarray, r1: np.ndarray
    ) -> np.ndarray:
        start = planetocentricLonLat(crs, *transform * (c0, r0))
        end = planetocentricLonLat(crs, *transform * (c1, r1))
        own = surfaces.own
        distances = _inverse(
            own, start[0], own.latitudes(start[1]), end[0], own.latitudes(end[1])
        )
        return distances.reshape(rows.shape)

    acrossColumns = spacing(centerCol - 0.5, centerRow, centerCol + 0.5, centerRow)
    acrossRows = spacing(centerCol, centerRow - 0.5, centerCol, centerRow + 0.5)
    return acrossColumns, acrossRows


def pixelAreas(
    crs: CRS | str, transform: Affine, rows: npt.ArrayLike, cols: npt.ArrayLike
) -> np.ndarray:
    """The ground areas, in m² on the CRS's own surface, of the pixels at
    (``rows``, ``cols``) (integer indices; broadcast together), in their
    shape: exact per row for row-only layouts (``AREA_METHOD_ROWS``),
    otherwise the map pixel area over the areal scale at the pixel center
    (``AREA_METHOD_SCALE``). NaN where PROJ can't invert. Raises ValueError
    only for a CRS on no body or one PROJ can't read."""
    rows, cols = np.broadcast_arrays(
        np.asarray(rows, dtype=np.float64), np.asarray(cols, dtype=np.float64)
    )
    surface = _surfacesFor(crs).own
    layout = _layoutFor(crs)
    if not _isRowOnly(layout, transform):
        return _scaledAreas(crs, layout, surface, transform, rows, cols)
    unique, first, inverse = np.unique(
        rows.ravel(), return_index=True, return_inverse=True
    )
    areas = _rowAreas(crs, surface, transform, unique, cols.ravel()[first])
    return areas[inverse].reshape(rows.shape)


def imagePixelAreas(
    crs: CRS | str, transform: Affine, width: int, height: int
) -> np.ndarray:
    """The (``height``, ``width``) ground areas (m²) of every pixel of an
    image, as ``pixelAreas``.

    Row-only layouts compute each row once and return a **read-only**
    broadcast view of the row areas (no copy; ``.ravel()`` or ``.copy()``
    makes a full array). Otherwise images of more than 4,000,000 pixels
    evaluate the areas on a grid of every n-th pixel
    (n = ⌈√(pixels / 4,000,000)⌉, plus the last row and column) and
    interpolate bilinearly between them (scale factors vary smoothly); a NaN
    grid value spreads to the pixels between it and its neighbors. The work
    goes in blocks of rows, so the peak memory is about the result's size.
    Raises as ``pixelAreas``."""
    surface = _surfacesFor(crs).own
    layout = _layoutFor(crs)
    if width <= 0 or height <= 0:
        return np.zeros((max(height, 0), max(width, 0)))
    if _isRowOnly(layout, transform):
        rows = np.arange(height, dtype=np.float64)
        # any column has the row's area; the middle one is inside the image
        # (a column past the projection's edge would be NaN)
        areas = _rowAreas(crs, surface, transform, rows, np.full(height, width // 2))
        return np.broadcast_to(areas[:, None], (height, width))
    step = max(1, math.ceil(math.sqrt(width * height / _MAX_EVALUATED_PIXELS)))
    gridRows, gridCols = _gridIndices(height, step), _gridIndices(width, step)
    lowerC, upperC, tC = _weights(gridCols, width)
    movingC = tC > 0

    def acrossColumns(blockRows: np.ndarray) -> np.ndarray:
        """The grid rows ``blockRows``, interpolated to every column."""
        values = _scaledAreas(
            crs, layout, surface, transform, blockRows[:, None], gridCols[None, :]
        )
        across = values[:, lowerC] * (1 - tC)
        # only where the weight isn't 0, so a NaN neighbor stays out of
        # pixels on the grid
        across[:, movingC] += values[:, upperC[movingC]] * tC[movingC]
        return across

    out = np.empty((height, width))
    if len(gridRows) == 1:
        out[:] = acrossColumns(gridRows)
        return out
    # grid rows per block, sharing the last with the next block
    perBlock = max(2, _BLOCK_POINTS // len(gridCols))
    for first in range(0, len(gridRows) - 1, perBlock - 1):
        block = gridRows[first : first + perBlock]
        across = acrossColumns(block)
        top = int(block[0])
        bottom = height if first + len(block) == len(gridRows) else int(block[-1])
        lower, upper, t = _weights(block, bottom - top, top)
        rows = out[top:bottom]
        np.multiply(across[lower], (1 - t)[:, None], out=rows)
        moving = t > 0
        rows[moving] += across[upper[moving]] * t[moving, None]
    return out


def pixelAreaMethod(crs: CRS | str, transform: Affine) -> str:
    """How ``pixelAreas`` measures this image's pixels, and on what:
    "{AREA_METHOD_ROWS or AREA_METHOD_SCALE} on sphere R = 3394.84 km".
    Raises as ``pixelAreas``."""
    surfaces = _surfacesFor(crs)
    rowOnly = _isRowOnly(_layoutFor(crs), transform)
    method = AREA_METHOD_ROWS if rowOnly else AREA_METHOD_SCALE
    return f"{method} on {surfaces.surface}"


def _isRowOnly(layout: _Layout, transform: Affine) -> bool:
    return layout.rowOnly and transform.b == 0 and transform.d == 0


def _rowAreas(
    crs: CRS | str,
    surface: _Surface,
    transform: Affine,
    rows: np.ndarray,
    cols: np.ndarray,
) -> np.ndarray:
    """The geodesic polygon area (m²) of the pixel at each of ``rows``,
    ``cols`` (1-D), its edges densified to _EDGE_POINTS points so the top
    and bottom follow their parallels; NaN where a corner doesn't invert."""
    t = np.arange(_EDGE_POINTS) / _EDGE_POINTS
    zeros, ones = np.zeros_like(t), np.ones_like(t)
    # clockwise on the image from the top left: top, right, bottom, left
    ringCols = np.concatenate([t, ones, 1 - t, zeros])
    ringRows = np.concatenate([zeros, t, ones, 1 - t])
    pixelCols = np.asarray(cols, dtype=np.float64)[:, None] + ringCols
    pixelRows = np.asarray(rows, dtype=np.float64)[:, None] + ringRows
    xs, ys = transform * (pixelCols, pixelRows)
    lon, centric = planetocentricLonLat(crs, xs, ys)
    lat = surface.latitudes(centric)
    finite = np.isfinite(lon).all(axis=1) & np.isfinite(lat).all(axis=1)
    areas = np.full(len(rows), np.nan)
    with PROJ_LOCK:
        for index in np.flatnonzero(finite):
            area, _ = surface.geod.polygon_area_perimeter(
                lon[index].tolist(), lat[index].tolist()
            )
            areas[index] = abs(area)
    return areas


def _scaledAreas(
    crs: CRS | str,
    layout: _Layout,
    surface: _Surface,
    transform: Affine,
    rows: np.ndarray,
    cols: np.ndarray,
) -> np.ndarray:
    """The map pixel area over the areal scale at each pixel center (rows
    and cols broadcast together). For a geographic CRS (rotated: otherwise
    it's row-only), the ground area of the pixel's angular area at its
    center: M·N·cos φg per square radian of φg and λ (Snyder, 1987,
    eqs. 4-18, 4-20), times dφg/dφc for planetocentric latitudes."""
    rows, cols = np.broadcast_arrays(rows, cols)
    mapArea = abs(transform.determinant) * layout.unit**2
    if not layout.geographic:
        scale = arealScaleAtPixels(crs, transform, cols + 0.5, rows + 0.5)
        return mapArea / scale
    xs, ys = transform * (cols + 0.5, rows + 0.5)
    _, centric = planetocentricLonLat(crs, xs, ys)
    graphic = np.radians(surface.latitudes(centric))
    a, c = surface.a, surface.c
    e2 = 1 - (c / a) ** 2
    w = 1 - e2 * np.sin(graphic) ** 2
    meridional = a * (1 - e2) / w**1.5
    primeVertical = a / np.sqrt(w)
    perRadian = meridional * primeVertical * np.cos(graphic)
    if layout.centricNumbers:
        ratio = (a / c) ** 2
        phi = np.radians(centric)
        perRadian = perRadian * ratio / (np.cos(phi) ** 2 + ratio**2 * np.sin(phi) ** 2)
    return mapArea * perRadian


def _gridIndices(count: int, step: int) -> np.ndarray:
    """Every ``step``-th index from 0, and the last."""
    return np.unique(np.append(np.arange(0, count, step), count - 1)).astype(np.float64)


def _weights(
    grid: np.ndarray, count: int, start: int = 0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each of ``count`` indices from ``start``, its grid neighbors
    (positions in ``grid``) and the linear weight of the upper one."""
    positions = np.arange(start, start + count, dtype=np.float64)
    if len(grid) == 1:
        zeros = np.zeros(count, dtype=np.intp)
        return zeros, zeros, np.zeros(count)
    upper = np.clip(np.searchsorted(grid, positions, side="right"), 1, len(grid) - 1)
    lower = upper - 1
    weight = (positions - grid[lower]) / (grid[upper] - grid[lower])
    # the last grid point takes its own value only (weight 0), as the others
    # do, so a NaN neighbor can't reach it
    last = positions >= grid[-1]
    lower[last] = upper[last] = len(grid) - 1
    weight[last] = 0.0
    return lower, upper, weight


def _measurement(
    value: float,
    reference: float | None,
    unit: str,
    method: str,
    distortion: DistortionSummary | None,
) -> Measurement:
    difference = abs(value - reference) / reference if reference else 0.0
    return Measurement(value, unit, method, reference, difference, distortion)


def _segments(surface: _Surface, lon: np.ndarray, centric: np.ndarray) -> np.ndarray:
    """Geodesic lengths between consecutive points (none for one point)."""
    if lon.size < 2:
        return np.zeros(0)
    latitudes = surface.latitudes(centric)
    return _inverse(surface, lon[:-1], latitudes[:-1], lon[1:], latitudes[1:])


def _inverse(
    surface: _Surface,
    lon1: np.ndarray,
    lat1: np.ndarray,
    lon2: np.ndarray,
    lat2: np.ndarray,
) -> np.ndarray:
    """Geodesic distances (m) between lon1, lat1 and lon2, lat2 (geodetic);
    lists, not arrays: pyproj takes a one-element array for a scalar, which
    NumPy deprecates."""
    with PROJ_LOCK:
        _, _, distances = surface.geod.inv(
            lon1.ravel().tolist(),
            lat1.ravel().tolist(),
            lon2.ravel().tolist(),
            lat2.ravel().tolist(),
        )
    return np.asarray(distances, dtype=np.float64)


def _distortion(
    crs: CRS | str, transform: Affine | None, points: np.ndarray
) -> DistortionSummary | None:
    """The distortion over the pixels covering ``points``' bounding box."""
    if transform is None or not np.isfinite(points).all() or len(points) == 0:
        return None
    cols, rows = ~transform * (points[:, 0], points[:, 1])
    col0, row0 = math.floor(np.min(cols)), math.floor(np.min(rows))
    col1, row1 = math.ceil(np.max(cols)), math.ceil(np.max(rows))
    width, height = max(col1 - col0, 1), max(row1 - row0, 1)
    return distortionOver(crs, transform, width, height, (row0, col0, height, width))


def _key(crs: CRS | str) -> str:
    """The CRS as text, for caching; the caller holds PROJ_LOCK."""
    return crs if isinstance(crs, str) else CRS.from_user_input(crs).srs


def _surfacesFor(crs: CRS | str) -> _Surfaces:
    with PROJ_LOCK:
        return _surfaces(_key(crs))


@lru_cache(maxsize=64)
def _surfaces(key: str) -> _Surfaces:
    """The CRS's own surface and, when it differs, the body's reference
    ellipsoid; the caller holds PROJ_LOCK. Raises ValueError for a CRS on
    no body or one PROJ can't read."""
    try:
        crs = CRS.from_user_input(key)
    except CRSError as error:
        raise ValueError(f"Unreadable coordinate system: {error}") from error
    ellipsoid = crs.ellipsoid
    if ellipsoid is None:
        raise ValueError(f"{crs.name} is on no body (no ellipsoid)")
    a, c = ellipsoid.semi_major_metre, ellipsoid.semi_minor_metre
    own = _Surface(Geod(a=a, b=c), a, c)
    body = identifyBody(crs).body
    reference = None
    if body is not None:
        referenceEllipsoid = body.referenceCrs().ellipsoid
        assert referenceEllipsoid is not None  # IAU_2015 CRSs have one
        refA = referenceEllipsoid.semi_major_metre
        refC = referenceEllipsoid.semi_minor_metre
        if abs(refA - a) > SAME_SURFACE_M or abs(refC - c) > SAME_SURFACE_M:
            reference = _Surface(Geod(a=refA, b=refC), refA, refC)
    surface = surfaceText(ellipsoid)
    return _Surfaces(own, reference, f"geodesic (Karney 2013) on {surface}", surface)


def _layoutFor(crs: CRS | str) -> _Layout:
    with PROJ_LOCK:
        return _layout(_key(crs))


@lru_cache(maxsize=64)
def _layout(key: str) -> _Layout:
    """The caller holds PROJ_LOCK. Raises ValueError for a CRS PROJ can't
    read. The latitude type is the shared reading (``latitudeType``), as
    ``planetocentricLonLat`` and ``distortion`` read it."""
    try:
        crs = CRS.from_user_input(key)
    except CRSError as error:
        raise ValueError(f"Unreadable coordinate system: {error}") from error
    unit = crs.axis_info[0].unit_conversion_factor if crs.axis_info else 1.0
    if isGeographic(crs):  # also PROJ's planetocentric ellipsoids
        centric = latitudeType(crs) == "planetocentric"
        return _Layout(True, True, unit, centric)
    operation = crs.coordinate_operation
    method = operation.method_name if operation is not None else ""
    return _Layout(method in _ROW_ONLY_METHODS, False, unit, False)
