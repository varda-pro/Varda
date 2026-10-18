"""Lines from GIS files (a rover traverse, mapped contacts, Varda's own
exported lines) or from another workspace placed on a 3D terrain: as
positions over the DEM's extent, for the viewer to drape. No Qt.

Coordinates in longitude and latitude are taken as degrees on the DEM's body
(planetary GeoJSON says Earth's CRS84 even for Mars): longitude east first and
the DEM's own latitude type, whatever its axes; projected ones are converted
to the DEM's map projection (``geo.transform.transformPoints``), and refused
on another body. GeoJSON can't record a planetary map projection at all, so
"degrees" that can't be degrees (beyond ±360 and ±90) are taken to be in the
DEM's projection already."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from attrs import frozen
from pyproj import CRS

from varda.geo.equivalence import sameCrs
from varda.geo.transform import eastLonLat, transformPoints
from varda.utilities.proj_lock import PROJ_LOCK

Bounds = tuple[float, float, float, float]  # west, south, east, north


class OffDemError(ValueError):
    """No line crosses the DEM."""


@frozen
class PageLine:
    """A layer of lines on the terrain: its paths as (u, v) fractions of the
    DEM's extent (u east, v north), with a color and a width (relative)."""

    name: str
    color: str
    paths: tuple[tuple[tuple[float, float], ...], ...]
    width: float = 1.0
    visible: bool = True


def _paths(geometry: shapely.Geometry) -> list[np.ndarray]:
    """Every line and polygon outline in a geometry, as (n, 2) arrays."""
    found = []
    for part in getattr(geometry, "geoms", [geometry]):
        if isinstance(part, shapely.LineString):
            found.append(np.asarray(part.coords)[:, :2])
        elif isinstance(part, shapely.Polygon):
            found.append(np.asarray(part.exterior.coords)[:, :2])
            found += [np.asarray(ring.coords)[:, :2] for ring in part.interiors]
        elif hasattr(part, "geoms"):
            found += _paths(part)
    return found


def toDemCoordinates(frame: gpd.GeoDataFrame, demCrs: CRS) -> list[np.ndarray]:
    """The frame's lines in the DEM's map coordinates."""
    paths = [path for geometry in frame.geometry.dropna() for path in _paths(geometry)]
    crs = frame.crs
    with PROJ_LOCK:
        if crs is None or crs.is_geographic:
            everything = np.concatenate(paths) if paths else np.zeros((0, 2))
            if everything.size and (
                np.abs(everything[:, 0]).max() > 360
                or np.abs(everything[:, 1]).max() > 90
            ):
                return paths  # projected coordinates whose projection was lost
            # degrees on the DEM's body, whatever body the file claims
            source = eastLonLat(demCrs) if demCrs.geodetic_crs else demCrs
        else:
            source = CRS.from_user_input(crs)
        if sameCrs(source, demCrs):
            return paths
        return [
            np.column_stack(transformPoints(source, demCrs, p[:, 0], p[:, 1]))
            for p in paths
        ]


def readLines(
    path: str | Path,
    demCrs: CRS,
    bounds: Bounds,
    color: str,
    name: str | None = None,
) -> PageLine:
    """A vector file's lines over a DEM (``bounds`` in its map units), as
    fractions of its extent. Parts entirely off the DEM are dropped."""
    frame = gpd.read_file(path)
    try:
        return placeLines(frame, demCrs, bounds, color, name or Path(path).stem)
    except OffDemError as error:
        raise OffDemError(f"No line in {Path(path).name} crosses the DEM.") from error


def placeLines(
    frame: gpd.GeoDataFrame, demCrs: CRS, bounds: Bounds, color: str, name: str
) -> PageLine:
    """A frame's lines over a DEM, as ``readLines`` places a file's."""
    west, south, east, north = bounds
    kept = []
    for points in toDemCoordinates(frame, demCrs):
        u = (points[:, 0] - west) / (east - west)
        v = (points[:, 1] - south) / (north - south)
        inside = (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
        if len(points) >= 2 and inside.any():
            kept.append(tuple((float(a), float(b)) for a, b in zip(u, v)))
    if not kept:
        raise OffDemError(f"{name} doesn't cross the DEM.")
    return PageLine(name, color, tuple(kept))
