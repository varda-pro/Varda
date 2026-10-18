"""A coordinate system in words a scientist recognizes, e.g. "Mars, sphere
R = 3394.84 km, Equirectangular, center lat 15°, 18 m/px", and the fields
behind it, read from the CRS's PROJ definition.

Conventions (Archinal et al., 2018; Seidelmann et al., 2002): latitude on a
sphere is planetocentric, and on a flattened ellipsoid it is PROJ's geodetic
latitude, i.e. planetographic; the longitude direction is the geodetic
CRS's longitude axis."""

from __future__ import annotations

import math
from functools import lru_cache

import attrs
import numpy as np
from affine import Affine
from attrs import frozen
from pyproj import CRS
from pyproj.exceptions import CRSError

from varda.geo.bodies import identifyBody
from varda.utilities.proj_lock import PROJ_LOCK

NO_CRS_TEXT = "No coordinate system (pixel coordinates)"
# map units PROJ names, as short labels
_UNITS = {
    "metre": "m",
    "meter": "m",
    "kilometre": "km",
    "kilometer": "km",
    "foot": "ft",
    "US survey foot": "ft",
}
_EQUIRECTANGULAR = {"Equidistant Cylindrical", "Equirectangular"}
_STEREOGRAPHIC = {"Stereographic", "Oblique Stereographic"}
# conversion parameters, by PROJ's names, in order of preference
_CENTER_LATITUDE = (
    "Latitude of 1st standard parallel",
    "Latitude of standard parallel",
    "Latitude of natural origin",
    "Latitude of false origin",
    "Latitude of projection centre",
)
_CENTER_LONGITUDE = (
    "Longitude of natural origin",
    "Longitude of origin",
    "Longitude of false origin",
    "Longitude of projection centre",
)
_AUTHORITIES = ("IAU_2015", "EPSG")


@frozen
class CrsDescription:
    """What a coordinate system is: its body, surface, projection, latitude
    and longitude conventions, units and, for an image, its pixel size."""

    body: str | None  # "Mars"; None when unknown
    kind: str  # "geographic" | "projected" | "none"
    projection: str  # "Equirectangular", "Polar Stereographic (north)", ...
    surface: str  # "sphere R = 3396.19 km" | "ellipsoid a = 3396.19, c = 3376.20 km"
    latitudeType: str  # "planetocentric" | "planetographic"
    longitudeDirection: str  # "east" | "west"
    longitudeDomain: str  # "-180..180" | "0..360"
    centerLatitude: float | None  # lat_ts or latitude of origin, degrees
    centerLongitude: float | None  # lon_0, degrees
    units: str  # "m", "km", "°"
    pixelSizeM: float | None  # ground size of a pixel at the image center, m
    authority: str | None  # "IAU_2015:49910", "EPSG:4326", or None

    @property
    def text(self) -> str:
        if self.kind == "none":
            return NO_CRS_TEXT
        parts = [
            self.body if self.body is not None else f"Unknown body ({self.surface})",
            *([self.surface] if self.body is not None else []),
            self.projection,
        ]
        if self.centerLatitude is not None and not self.projection.startswith("Polar"):
            parts.append(f"center lat {_degrees(self.centerLatitude)}°")
        if self.centerLongitude:
            parts.append(f"lon_0 {_degrees(self.centerLongitude)}°")
        if self.pixelSizeM is not None:
            parts.append(f"{meters(self.pixelSizeM)} m/px")
        return ", ".join(parts)


def _degrees(value: float) -> str:
    return f"{value + 0.0:g}"  # + 0.0: never "-0"


def meters(value: float) -> str:
    """A length in meters for reading: whole meters from 100 m, otherwise
    three significant figures ("463", "18", "0.25")."""
    if value >= 100:
        return f"{value:.0f}"
    return f"{float(f'{value:.3g}'):g}"


def describeCrs(
    crs: CRS | str | None,
    transform: Affine | None = None,
    width: int = 0,
    height: int = 0,
) -> CrsDescription:
    """``crs`` described; with an image's ``transform`` and size, also its
    pixel size on the ground and the longitude domain of its extent."""
    if crs is None:
        return CrsDescription(
            body=None,
            kind="none",
            projection="none",
            surface="",
            latitudeType="",
            longitudeDirection="",
            longitudeDomain="",
            centerLatitude=None,
            centerLongitude=None,
            units="px",
            pixelSizeM=None,
            authority=None,
        )
    with PROJ_LOCK:
        # as written (a WKT round trip can lose the names PROJ identifies by)
        key = crs if isinstance(crs, str) else CRS.from_user_input(crs).srs
        description = _describe(key)
        if transform is None:
            return description
        parsed = CRS.from_user_input(key)
        return attrs.evolve(
            description,
            pixelSizeM=_pixelSizeM(parsed, transform, width, height),
            longitudeDomain=_longitudeDomain(description, transform, width, height),
        )


@lru_cache(maxsize=256)
def _describe(key: str) -> CrsDescription:
    """The description of the CRS ``key`` (as the user or file wrote it),
    without the image; cached, since identifying the body takes
    milliseconds. The caller holds PROJ_LOCK."""
    crs = CRS.from_user_input(key)
    ellipsoid = crs.ellipsoid
    if ellipsoid is None:
        raise CRSError(f"{crs.name} has no ellipsoid.")
    a = ellipsoid.semi_major_metre / 1000
    if ellipsoid.inverse_flattening == 0:
        surface = f"sphere R = {a:.2f} km"
    else:
        surface = (
            f"ellipsoid a = {a:.2f}, c = {ellipsoid.semi_minor_metre / 1000:.2f} km"
        )
    body = identifyBody(crs).body
    projected = not crs.is_geographic
    centerLatitude = _parameter(crs, _CENTER_LATITUDE) if projected else None
    centerLongitude = _parameter(crs, _CENTER_LONGITUDE) if projected else None
    return CrsDescription(
        body=body.name if body is not None else None,
        kind="projected" if projected else "geographic",
        projection=_projection(crs) if projected else "Geographic (lon/lat)",
        surface=surface,
        latitudeType="planetographic"
        if ellipsoid.inverse_flattening != 0
        else "planetocentric",
        longitudeDirection=_longitudeDirection(crs),
        longitudeDomain="0..360"
        if centerLongitude is not None and centerLongitude >= 180
        else "-180..180",
        centerLatitude=centerLatitude,
        centerLongitude=centerLongitude,
        units=_units(crs),
        pixelSizeM=None,
        authority=_authority(crs),
    )


def _parameter(crs: CRS, names: tuple[str, ...]) -> float | None:
    """The first of the conversion's angular parameters ``names``, degrees."""
    operation = crs.coordinate_operation
    if operation is None:
        return None
    values = {
        param.name: _inDegrees(param.value, param.unit_conversion_factor)
        for param in operation.params
        if isinstance(param.value, (int, float))  # PROJ allows text values
    }
    return next((values[name] for name in names if name in values), None)


def _inDegrees(value: float, radiansPerUnit: float) -> float:
    """An angle in degrees, exactly as written when it is already degrees."""
    if math.isclose(radiansPerUnit, math.pi / 180, rel_tol=1e-12):
        return value
    return math.degrees(value * radiansPerUnit)


def _projection(crs: CRS) -> str:
    operation = crs.coordinate_operation
    if operation is None:
        return crs.name
    method = operation.method_name.removesuffix(" (Spherical)")
    if method in _EQUIRECTANGULAR:
        return "Equirectangular"
    polar = method.startswith("Polar Stereographic")
    origin = _parameter(crs, _CENTER_LATITUDE[1:])
    if polar or (method in _STEREOGRAPHIC and origin is not None and abs(origin) == 90):
        return f"Polar Stereographic ({'south' if (origin or 0) < 0 else 'north'})"
    return method


def _longitudeDirection(crs: CRS) -> str:
    geodetic = crs.geodetic_crs or crs
    for axis in geodetic.axis_info:
        if "longitude" in axis.name.lower() or axis.abbrev.lower() == "lon":
            return "west" if axis.direction == "west" else "east"
    return "east"


def _units(crs: CRS) -> str:
    if crs.is_geographic:
        return "°"
    name = crs.axis_info[0].unit_name if crs.axis_info else ""
    return _UNITS.get(name, name or "map units")


def _authority(crs: CRS) -> str | None:
    """The CRS's authority code, else the first IAU_2015 or EPSG code PROJ
    finds that is the same CRS (ignoring axis order)."""
    found = crs.to_authority()
    if found is not None:
        return ":".join(found)
    for match in crs.list_authority(min_confidence=0):
        if match.auth_name not in _AUTHORITIES:
            continue
        candidate = CRS.from_authority(match.auth_name, match.code)
        if crs.equals(candidate, ignore_axis_order=True):
            return f"{match.auth_name}:{match.code}"
    return None


def _corners(transform: Affine, width: int, height: int) -> np.ndarray:
    cols = np.array([0, width, 0, width], dtype=float)
    rows = np.array([0, 0, height, height], dtype=float)
    return np.array(transform * (cols, rows))


def _longitudeDomain(
    description: CrsDescription, transform: Affine, width: int, height: int
) -> str:
    """A geographic image's longitudes past 180° are 0..360."""
    if description.kind != "geographic":
        return description.longitudeDomain
    longitudes = _corners(transform, width, height)[0]
    return "0..360" if longitudes.max() > 180 + 1e-9 else "-180..180"


def _pixelSizeM(crs: CRS, transform: Affine, width: int, height: int) -> float:
    """A pixel's larger side on the ground at the image center: map units
    times the unit's size (projected; the projection's scale error is not
    corrected), or degrees times π·a/180 (·cos φ east–west; geographic)."""
    across = math.hypot(transform.a, transform.d)
    down = math.hypot(transform.b, transform.e)
    if not crs.is_geographic:
        factor = crs.axis_info[0].unit_conversion_factor if crs.axis_info else 1.0
        return max(across, down) * factor
    ellipsoid = crs.ellipsoid
    assert ellipsoid is not None  # _describe checked
    perDegree = math.pi * ellipsoid.semi_major_metre / 180
    _, latitude = transform * (width / 2, height / 2)
    eastWest = across * perDegree * math.cos(math.radians(latitude))
    return max(eastWest, down * perDegree)
