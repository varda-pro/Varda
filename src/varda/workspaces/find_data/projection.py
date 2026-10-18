"""Map projections for the find-data map: equirectangular (longitude and
latitude as they are) or north or south polar stereographic on the body's
sphere. Footprints stay in longitude and latitude; the map draws them
projected, clipped to the polar view's hemisphere, and turns its view and
search box back into longitude and latitude bounds. No Qt."""

from __future__ import annotations

import math

import numpy as np
import shapely
from pyproj import Transformer

from varda.common.parameter import LabeledEnum
from varda.geo.bodies import BODIES, bodyNamed

Bounds = tuple[float, float, float, float]  # west, south, east, north (or x, y)
WHOLE: Bounds = (-180.0, -90.0, 180.0, 90.0)
SEGMENT_DEG = 0.5  # edges are split this finely, so they curve when projected
GRID_M = 0.001  # snapping that joins the halves of a footprint split at ±180°
LAT_STEPS = (30.0, 10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01)
LON_STEPS = (30.0, 15.0, 10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01)


class MapProjection(LabeledEnum):
    EQUIRECTANGULAR = "Equirectangular"
    NORTH_POLAR = "North Polar Stereographic"
    SOUTH_POLAR = "South Polar Stereographic"


class Projector:
    """Turns longitude and latitude on a body into map coordinates (degrees
    for equirectangular, meters for the polar views) and back. The map is
    drawn on the body's IAU mean-radius sphere (WGS 84 degrees for Earth); a
    body Varda doesn't know keeps its name and is drawn at Earth's size."""

    def __init__(
        self,
        projection: MapProjection = MapProjection.EQUIRECTANGULAR,
        body: str = "Earth",
    ):
        self.projection = projection
        known = bodyNamed(body) if body else BODIES["Earth"]
        self.body = known.name if known is not None else body.strip()
        self.polar = projection is not MapProjection.EQUIRECTANGULAR
        self.north = projection is MapProjection.NORTH_POLAR
        radius = (known or BODIES["Earth"]).meanRadius
        pole = 90 if self.north else -90
        # longitude and latitude on the map's sphere, as a PROJ string
        self.lonLatCrs = (
            "EPSG:4326"
            if self.body == "Earth"
            else f"+proj=longlat +R={radius:g} +no_defs"
        )
        # 0°E points down from the north pole and up from the south (PDS's convention)
        self.crs = (
            f"+proj=stere +lat_0={pole} +lon_0=0 +k=1 +R={radius:g} +units=m +no_defs"
            if self.polar
            else self.lonLatCrs
        )
        if self.polar:
            geographic = f"+proj=longlat +R={radius:g} +no_defs"
            self._forward = Transformer.from_crs(geographic, self.crs, always_xy=True)
            self._inverse = Transformer.from_crs(self.crs, geographic, always_xy=True)

    @property
    def hemisphere(self) -> Bounds:
        """The longitude and latitude the map shows."""
        if not self.polar:
            return WHOLE
        return (-180.0, 0.0, 180.0, 90.0) if self.north else (-180.0, -90.0, 180.0, 0.0)

    def forward(self, longitude, latitude) -> tuple[np.ndarray, np.ndarray]:
        if not self.polar:
            return np.asarray(longitude, float), np.asarray(latitude, float)
        x, y = self._forward.transform(longitude, latitude)
        return np.asarray(x, float), np.asarray(y, float)

    def inverse(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        if not self.polar:
            return np.asarray(x, float), np.asarray(y, float)
        longitude, latitude = self._inverse.transform(x, y)
        return np.asarray(longitude, float), np.asarray(latitude, float)

    def _project(self, coords: np.ndarray) -> np.ndarray:
        return np.column_stack(self.forward(coords[:, 0], coords[:, 1]))

    def geometry(self, geometry: shapely.Geometry) -> shapely.Geometry:
        """A longitude-latitude geometry in map coordinates (clipped to the
        hemisphere in a polar view; empty if it's all on the other side)."""
        if not self.polar:
            return geometry
        # clipping drops in-line points, so the edges are split after it
        clipped = shapely.clip_by_rect(geometry, *self.hemisphere)
        if clipped.is_empty:
            return clipped
        projected = shapely.transform(
            shapely.segmentize(clipped, SEGMENT_DEG), self._project
        )
        if isinstance(projected, shapely.MultiPolygon):
            projected = shapely.union_all(projected.geoms, grid_size=GRID_M)
        return projected

    def mapBounds(self, bounds: Bounds) -> Bounds | None:
        """Longitude-latitude bounds as map bounds (None if they're all off
        the map)."""
        west, south, east, north = bounds
        if not self.polar:
            return (
                max(west, -180.0),
                max(south, -90.0),
                min(east, 180.0),
                min(north, 90.0),
            )
        box = self.geometry(shapely.box(west, south, east, north))
        if box.is_empty:
            return None
        x0, y0, x1, y1 = (float(v) for v in box.bounds)
        return (x0, y0, x1, y1)

    def lonLatBounds(self, mapBounds: Bounds) -> Bounds:
        """The longitude and latitude bounds of a rectangle on the map,
        within the hemisphere a polar view shows. A rectangle around the pole
        takes in every longitude."""
        x0, y0, x1, y1 = mapBounds
        if not self.polar:
            return (max(x0, -180.0), max(y0, -90.0), min(x1, 180.0), min(y1, 90.0))
        # latitude depends only on the distance from the pole, at the origin
        nearest = math.hypot(min(max(0.0, x0), x1), min(max(0.0, y0), y1))
        farthest = max(math.hypot(x, y) for x in (x0, x1) for y in (y0, y1))
        _, latitudes = self.inverse([nearest, farthest], [0.0, 0.0])
        near, far = float(latitudes[0]), float(latitudes[1])
        containsPole = x0 <= 0 <= x1 and y0 <= 0 <= y1
        if self.north:
            south, north = max(far, 0.0), 90.0 if containsPole else near
        else:
            south, north = -90.0 if containsPole else near, min(far, 0.0)
        if containsPole:
            return (-180.0, south, 180.0, north)
        # a rectangle off the pole spans the longitudes of its corners
        longitudes, _ = self.inverse([x0, x0, x1, x1], [y0, y1, y0, y1])
        ordered = np.sort(longitudes)
        gaps = np.diff(np.append(ordered, ordered[0] + 360.0))
        if np.argmax(gaps) != len(ordered) - 1:  # it crosses ±180°
            return (-180.0, south, 180.0, north)
        return (float(ordered[0]), south, float(ordered[-1]), north)


def _step(span: float, steps: tuple[float, ...], lines: int) -> float:
    """The largest step that draws about ``lines`` lines across ``span``."""
    return next((s for s in steps if s <= span / lines), steps[-1])


def _label(value: float, positive: str, negative: str) -> str:
    value = round(value, 6)
    if value == 0:
        return "0°"
    return f"{abs(value):g}°{positive if value > 0 else negative}"


def graticule(
    projector: Projector, view: Bounds
) -> tuple[list[shapely.Geometry], list[tuple[float, float, str]]]:
    """Parallels and meridians for a polar view, spaced for how far in it is,
    in map coordinates; and their labels (x, y, text): the parallels along
    the meridian through the view's center, the meridians along its
    parallel."""
    west, south, east, north = projector.lonLatBounds(view)
    latStep = _step(north - south, LAT_STEPS, 4)
    lonStep = _step(east - west, LON_STEPS, 6)
    x0, y0, x1, y1 = view
    if x0 <= 0 <= x1 and y0 <= 0 <= y1:
        # around the pole, meridians are labeled on a ring partway out, and
        # parallels along a meridian between the lines
        ring = 0.7 * min(x1 - x0, y1 - y0) / 2
        centerLon, centerLat = 45.0, float(projector.inverse(ring, 0.0)[1])
    else:
        centerLon, centerLat = (
            float(v) for v in projector.inverse((x0 + x1) / 2, (y0 + y1) / 2)
        )
    lines, labels = [], []
    first = math.ceil(south / latStep) * latStep
    for latitude in np.arange(first, north + latStep / 2, latStep):
        if abs(latitude) < 90 - 1e-9:
            lines.append(shapely.LineString([(west, latitude), (east, latitude)]))
            labels.append((centerLon, latitude, _label(latitude, "N", "S")))
    first = math.ceil(west / lonStep) * lonStep
    for longitude in np.arange(first, east + lonStep / 2, lonStep):
        if longitude < 180 - 1e-9 or west > -180:
            lines.append(shapely.LineString([(longitude, south), (longitude, north)]))
            text = f"{round(longitude, 6) if longitude > -180 else 180:g}°E"
            labels.append((longitude, centerLat, text))
    placed = []
    for longitude, latitude, text in labels:
        x, y = (float(v) for v in projector.forward(longitude, latitude))
        if x0 <= x <= x1 and y0 <= y <= y1:
            placed.append((x, y, text))
    projected = [projector.geometry(line) for line in lines]
    return [g for g in projected if not g.is_empty], placed
