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

from varda.geo.bodies import identifyBody, surfaceText
from varda.geo.distortion import DistortionSummary, distortionOver
from varda.geo.transform import ographicLatitude, planetocentricLonLat, transformPoints
from varda.utilities.proj_lock import PROJ_LOCK

# radii closer than this to the reference ellipsoid's are the same surface
SAME_SURFACE_M = 1.0


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
    the geodesics between consecutive points on the CRS's own surface."""
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
    the image's ``transform``, also the distortion over its bounding box."""
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


def _surfacesFor(crs: CRS | str) -> _Surfaces:
    with PROJ_LOCK:
        return _surfaces(crs if isinstance(crs, str) else CRS.from_user_input(crs).srs)


@lru_cache(maxsize=64)
def _surfaces(key: str) -> _Surfaces:
    """The CRS's own surface and, when it differs, the body's reference
    ellipsoid; the caller holds PROJ_LOCK."""
    crs = CRS.from_user_input(key)
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
    return _Surfaces(
        own, reference, f"geodesic (Karney 2013) on {surfaceText(ellipsoid)}"
    )
