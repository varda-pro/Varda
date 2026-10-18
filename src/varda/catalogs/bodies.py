"""Planetary bodies for the find-data map: a body chosen by hand, longitude
conventions, and footprints as longitude and latitude on their body (which
body, and its size, come from ``varda.geo.bodies``)."""

from __future__ import annotations

import geopandas as gpd

from varda.common.parameter import LabeledEnum
from varda.geo.bodies import identifyBody
from varda.utilities.proj_lock import PROJ_LOCK


def normalizeLongitude(longitude: float) -> float:
    """-180 to 180, east positive (0-360 longitudes are converted)."""
    return ((longitude + 180.0) % 360.0) - 180.0


class BodyChoice(LabeledEnum):
    """A body chosen by hand, or found from a file's coordinate system
    (GeoJSON always says Earth's, even for Mars coordinates)."""

    AUTO = "From the file"
    MERCURY = "Mercury"
    VENUS = "Venus"
    EARTH = "Earth"
    MOON = "Moon"
    MARS = "Mars"
    CERES = "Ceres"
    VESTA = "Vesta"
    IO = "Io"
    EUROPA = "Europa"
    GANYMEDE = "Ganymede"
    CALLISTO = "Callisto"
    ENCELADUS = "Enceladus"
    TITAN = "Titan"


def toLongitudeLatitude(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """A GeoDataFrame in longitude and latitude: geographic coordinates are
    taken as they are (degrees on whatever body; GeoJSON always declares
    Earth's), projected ones are converted to degrees on their body's IAU
    2015 sphere. A projection on no known body is a ``ValueError``."""
    crs = frame.crs
    if crs is None or crs.is_geographic:
        return frame
    match = identifyBody(crs)
    if match.body is None:
        raise ValueError(
            f"The footprints' projection matches no known body ({match.evidence})."
        )
    with PROJ_LOCK:
        return frame.to_crs(match.body.sphereCrs())
