"""Profiles: a band sampled along a line, against distance along it. No Qt.

Lines are in image pixel coordinates (x = column, y = row, with pixel
corners at whole numbers, as the viewports and ROIs use), so a pixel's value
sits at its center, (col + 0.5, row + 0.5). Distances are in meters when the
image is georeferenced, in any coordinate system: the sum of the geodesics
(Karney, 2013) between samples about 1 px apart, on the CRS's own ellipsoid
or sphere (``varda.geo.measure``), so the projection's scale error doesn't
enter them. On a CRS with no body ellipsoid (an engineering or local CRS)
but known linear units, they are planar, in those units converted to
meters; in pixels otherwise. Samples PROJ can't convert to longitude and
latitude have NaN distances, and so do all after them.
"""

from __future__ import annotations

import logging

import numpy as np
import shapely
from affine import Affine
from attrs import frozen
from pyproj import CRS
from pyproj.exceptions import CRSError

from varda.geo import measure
from varda.geo.measure import Measurement
from varda.utilities.proj_lock import PROJ_LOCK

logger = logging.getLogger(__name__)

# how a length on a CRS with no body ellipsoid is measured
PLANAR_METHOD = "planar, in the coordinate system's units (no body ellipsoid)"

Points = np.ndarray  # (n, 2): x (column), y (row)


@frozen
class Profile:
    """Sample positions (pixel coordinates), distance along the line from
    its start, and the band's values there (NaN off the image or in no-data)."""

    positions: np.ndarray  # (n, 2)
    distance: np.ndarray  # (n,)
    values: np.ndarray  # (n,)
    distanceUnits: str  # "m" or "px"


def densify(vertices: Points, step: float = 1.0) -> tuple[Points, np.ndarray]:
    """Positions along the polyline through ``vertices``, about ``step``
    pixels apart and including every vertex, and each one's distance along
    the line in pixels."""
    vertices = np.asarray(vertices, dtype=float)
    if len(vertices) < 2:
        return vertices.copy(), np.zeros(len(vertices))
    positions, distances, travelled = [vertices[:1]], [np.zeros(1)], 0.0
    for start, end in zip(vertices[:-1], vertices[1:]):
        length = float(np.hypot(*(end - start)))
        count = max(1, int(np.ceil(length / step)))
        t = np.arange(1, count + 1) / count
        positions.append(start + t[:, None] * (end - start))
        distances.append(travelled + t * length)
        travelled += length
    return np.concatenate(positions), np.concatenate(distances)


def sampleBilinear(band: np.ndarray, positions: Points) -> np.ndarray:
    """``band`` (rows, cols) at pixel-coordinate positions, interpolated
    between the four nearest pixel centers; NaN off the image, and wherever a
    neighbor with any weight is NaN."""
    band = np.asarray(band, dtype=float)
    rows, cols = band.shape
    u = positions[:, 0] - 0.5  # in pixel-center units
    v = positions[:, 1] - 0.5
    inside = (positions[:, 0] >= 0) & (positions[:, 0] <= cols)
    inside &= (positions[:, 1] >= 0) & (positions[:, 1] <= rows)
    # Edge halves of the border pixels take the border pixel's value
    u = np.clip(u, 0, cols - 1)
    v = np.clip(v, 0, rows - 1)
    c0 = np.minimum(np.floor(u).astype(int), cols - 1)
    r0 = np.minimum(np.floor(v).astype(int), rows - 1)
    c1 = np.minimum(c0 + 1, cols - 1)
    r1 = np.minimum(r0 + 1, rows - 1)
    fu, fv = u - c0, v - r0
    corners = (
        (band[r0, c0], (1 - fu) * (1 - fv)),
        (band[r0, c1], fu * (1 - fv)),
        (band[r1, c0], (1 - fu) * fv),
        (band[r1, c1], fu * fv),
    )
    total = np.zeros(len(positions))
    bad = ~inside
    for value, weight in corners:
        used = weight > 1e-12
        bad |= used & ~np.isfinite(value)
        total += np.where(used, np.nan_to_num(value), 0.0) * weight
    total[bad] = np.nan
    return total


def mapDistances(
    positions: Points, transform: Affine | None, crs: CRS | str | None
) -> tuple[np.ndarray, str]:
    """Distance along ``positions`` from the first, and its units: meters
    on the body (geodesic) when the image is georeferenced, meters on the
    map (planar) on a CRS with no body but known linear units, pixels
    otherwise. Never raises."""
    mode, metersPerUnit = _distanceMode(transform, crs)
    if mode == "geodesic":
        assert transform is not None and crs is not None
        try:
            # samples PROJ can't convert are NaN, not warnings (np.errstate
            # is per thread)
            with np.errstate(invalid="ignore"):
                points = _mapPoints(positions, transform)
                return measure.cumulativeDistance(crs, points), "m"
        except (ValueError, CRSError):
            logger.exception("Couldn't measure on the body; using pixels")
            mode = "px"
    if mode == "planar":
        assert transform is not None
        steps = np.diff(_mapPoints(positions, transform), axis=0) * metersPerUnit
        return np.concatenate([[0.0], np.cumsum(np.hypot(*steps.T))]), "m"
    steps = np.diff(positions, axis=0)
    return np.concatenate([[0.0], np.cumsum(np.hypot(*steps.T))]), "px"


def profile(
    band: np.ndarray,
    vertices: Points,
    transform: Affine | None = None,
    crs: CRS | str | None = None,
) -> Profile:
    """``band`` sampled along the polyline through ``vertices``, about one
    sample per pixel."""
    positions, _ = densify(vertices)
    distance, units = mapDistances(positions, transform, crs)
    return Profile(positions, distance, sampleBilinear(band, positions), units)


def simplify(points: Points, tolerance: float = 0.5) -> Points:
    """A freehand line's points thinned (Douglas-Peucker, ``tolerance`` in
    pixels), keeping its ends."""
    points = np.asarray(points, dtype=float)
    if len(points) < 3:
        return points
    line = shapely.LineString(points).simplify(tolerance, preserve_topology=False)
    return np.asarray(line.coords, dtype=float)


def lineMeasurement(
    vertices: Points,
    transform: Affine | None,
    crs: CRS | str | None,
    *,
    withDistortion: bool = True,
) -> Measurement | None:
    """The polyline's length on the body, followed about 1 px at a time as
    drawn on the image, with how it was measured, the body-model term and
    (``withDistortion``) the projection's distortion over it; NaN when PROJ
    can't convert part of it. On a CRS with no body but known linear units,
    its planar length in meters (``PLANAR_METHOD``); None in pixels (no
    georeferencing, or units unknown). Never raises."""
    mode, metersPerUnit = _distanceMode(transform, crs)
    if mode == "px":
        return None
    assert transform is not None and crs is not None
    positions, _ = densify(vertices)
    points = _mapPoints(positions, transform)
    if mode == "planar":
        steps = np.diff(points, axis=0) * metersPerUnit
        length = float(np.sum(np.hypot(*steps.T)))
        return Measurement(length, "m", PLANAR_METHOD, None, 0.0, None)
    try:
        with np.errstate(invalid="ignore"):  # NaN where PROJ can't convert
            return measure.lineLength(
                crs, points, transform=transform if withDistortion else None
            )
    except (ValueError, CRSError):
        logger.exception("Couldn't measure on the body; using pixels")
        return None


def lineLength(
    vertices: Points, transform: Affine | None, crs: CRS | str | None
) -> tuple[float, str]:
    """The polyline's length and units: meters on the body when the image
    is georeferenced (``lineMeasurement``), pixels otherwise."""
    measurement = lineMeasurement(vertices, transform, crs, withDistortion=False)
    if measurement is not None:
        return measurement.value, "m"
    _, distance = densify(vertices, step=np.inf)
    return float(distance[-1]) if len(distance) else 0.0, "px"


def _distanceMode(transform: Affine | None, crs: CRS | str | None) -> tuple[str, float]:
    """How to measure on this image: "geodesic" (its CRS is on a body's
    sphere or ellipsoid), "planar" (no ellipsoid, but linear units: the
    meters per unit) or "px" (no georeferencing, or unknown units)."""
    if transform is None or transform.is_identity or crs is None:
        return "px", 1.0
    try:
        with PROJ_LOCK:
            crs = CRS.from_user_input(crs)
            if crs.ellipsoid is not None:
                return "geodesic", 1.0
            axes = crs.axis_info
            unit = axes[0].unit_name.lower() if axes else ""
            factor = axes[0].unit_conversion_factor if axes else 0.0
    except CRSError:
        return "px", 1.0
    angular = any(word in unit for word in ("degree", "radian", "grad", "arc"))
    if unit in ("", "unknown") or angular or not factor > 0:
        return "px", 1.0
    return "planar", factor


def _mapPoints(positions: Points, transform: Affine) -> np.ndarray:
    xs, ys = transform * (positions[:, 0], positions[:, 1])
    return np.column_stack([np.asarray(xs, float), np.asarray(ys, float)])
