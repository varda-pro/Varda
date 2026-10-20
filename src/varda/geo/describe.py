"""A coordinate system in words a scientist recognizes, e.g. "Mars, sphere
R = 3394.84 km, Equirectangular, center lat 15°, 18 m/px" (one CRISM MTRDR:
the local radius under its scene; every CRISM product has its own, read
from its file), and the fields behind it, read from the CRS's PROJ
definition.

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
from pyproj.database import get_codes
from pyproj.enums import PJType
from pyproj.exceptions import CRSError

from varda.geo.bodies import Body, identifyBody, latitudeType, surfaceText
from varda.geo.equivalence import sameCrs
from varda.geo.measure import cumulativeDistance
from varda.utilities.proj_lock import PROJ_LOCK

NO_CRS_TEXT = "No coordinate system (pixel coordinates)"
# a pixel's two ground sides are both shown when they differ by more than this
PIXEL_SIZES_DIFFER = 0.01
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
    # "geographic" | "projected" | "none"; also "local" (an engineering
    # CRS: map coordinates on no body) and "unreadable"
    kind: str
    projection: str  # "Equirectangular", ...; the error when unreadable
    surface: str  # "sphere R = 3396.19 km" | "ellipsoid a = 3396.19, c = 3376.20 km"
    latitudeType: str  # "planetocentric" | "planetographic" ("" without a body)
    longitudeDirection: str  # "east" | "west" ("" without a body)
    longitudeDomain: str  # "-180..180" | "0..360"
    centerLatitude: float | None  # lat_ts or latitude of origin, degrees
    centerLongitude: float | None  # lon_0, degrees
    units: str  # "m", "km", "°"
    # the larger side of a pixel on the ground at the image center, m
    # (geodesic on the CRS's own surface)
    pixelSizeM: float | None
    authority: str | None  # "IAU_2015:49910", "EPSG:4326", or None
    # a pixel's sides on the ground across the image center, m: along its
    # row (east–west in a north-up image) and along its column
    pixelSizeEastWestM: float | None = None
    pixelSizeNorthSouthM: float | None = None

    @property
    def text(self) -> str:
        if self.kind == "none":
            return NO_CRS_TEXT
        if self.kind == "unreadable":
            return f"Unreadable coordinate system ({self.projection})"
        if self.kind == "local":
            first = ["Local coordinates (no body)"]
        elif self.body is None:
            first = [f"Unknown body ({self.surface})"]
        else:
            first = [self.body, self.surface]
        parts = [*first, self.projection]
        if self.centerLatitude is not None and not self.projection.startswith("Polar"):
            parts.append(f"center lat {_degrees(self.centerLatitude)}°")
        if self.centerLongitude:
            parts.append(f"lon_0 {_degrees(self.centerLongitude)}°")
        parts.extend(self._pixelSizeText())
        return ", ".join(parts)

    def _pixelSizeText(self) -> list[str]:
        """ "18 m/px", or both sides when they differ by more than 1%."""
        eastWest, northSouth = self.pixelSizeEastWestM, self.pixelSizeNorthSouthM
        if self.pixelSizeM is None:
            return []
        if (
            eastWest is not None
            and northSouth is not None
            and abs(eastWest - northSouth) > PIXEL_SIZES_DIFFER * self.pixelSizeM
        ):
            return [
                f"{formatMeters(eastWest)} m/px (east–west)",
                f"{formatMeters(northSouth)} m/px (north–south)",
            ]
        return [f"{formatMeters(self.pixelSizeM)} m/px"]


def _degrees(value: float) -> str:
    return f"{value + 0.0:g}"  # + 0.0: never "-0"


def formatMeters(value: float) -> str:
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
    pixel size on the ground and the longitude domain of its extent. Never
    raises: text that isn't a CRS is described as unreadable."""
    if crs is None:
        return _bare("none", "none", "px")
    with PROJ_LOCK:
        try:
            # as written (a WKT round trip can lose the names PROJ identifies by)
            key = crs if isinstance(crs, str) else CRS.from_user_input(crs).srs
            description = _describe(key)
        except CRSError as error:
            return _bare("unreadable", str(error), "map units")
        if transform is None:
            return description
        parsed = CRS.from_user_input(key)
        eastWest, northSouth = _pixelSides(parsed, key, transform, width, height)
        known = [size for size in (eastWest, northSouth) if size is not None]
        return attrs.evolve(
            description,
            pixelSizeM=max(known) if known else None,
            pixelSizeEastWestM=eastWest,
            pixelSizeNorthSouthM=northSouth,
            longitudeDomain=_longitudeDomain(description, transform, width, height),
        )


def crsText(crs: CRS | str) -> str:
    """The CRS as one line of WKT, or as PROJJSON when WKT would lose an
    axis direction (a west-positive base CRS): what an output records so the
    CRS can be read back exactly. Raises CRSError for text that isn't a CRS.
    Holds PROJ_LOCK."""
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


def _bare(kind: str, projection: str, units: str) -> CrsDescription:
    """No CRS, or one Varda can't read: no body, surface or conventions."""
    return CrsDescription(
        body=None,
        kind=kind,
        projection=projection,
        surface="",
        latitudeType="",
        longitudeDirection="",
        longitudeDomain="",
        centerLatitude=None,
        centerLongitude=None,
        units=units,
        pixelSizeM=None,
        authority=None,
    )


def _isGeographic(crs: CRS) -> bool:
    """Angles on a body: a geographic CRS, or another geodetic one such as
    PROJ's planetocentric ellipsoids (``IAU_2015:49902``, a spherical
    coordinate system), but not geocentric x, y, z."""
    return (
        not crs.is_projected and crs.geodetic_crs is not None and not crs.is_geocentric
    )


@lru_cache(maxsize=256)
def _describe(key: str) -> CrsDescription:
    """The description of the CRS ``key`` (as the user or file wrote it),
    without the image; cached, since identifying the body takes
    milliseconds. The caller holds PROJ_LOCK."""
    crs = CRS.from_user_input(key)
    ellipsoid = crs.ellipsoid
    if ellipsoid is None:  # e.g. GDAL's LOCAL_CS
        return attrs.evolve(
            _bare("local", crs.name, _units(crs)), surface="no ellipsoid"
        )
    body = identifyBody(crs).body
    projected = not _isGeographic(crs)
    centerLatitude = _parameter(crs, _CENTER_LATITUDE) if projected else None
    centerLongitude = _parameter(crs, _CENTER_LONGITUDE) if projected else None
    return CrsDescription(
        body=body.name if body is not None else None,
        kind="projected" if projected else "geographic",
        projection=_projection(crs) if projected else "Geographic (lon/lat)",
        surface=surfaceText(ellipsoid),
        latitudeType=latitudeType(crs),
        longitudeDirection=_longitudeDirection(crs),
        longitudeDomain="0..360"
        if centerLongitude is not None and centerLongitude >= 180
        else "-180..180",
        centerLatitude=centerLatitude,
        centerLongitude=centerLongitude,
        units=_units(crs),
        pixelSizeM=None,
        authority=_authority(crs, body),
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
    if _isGeographic(crs):
        return "°"
    name = crs.axis_info[0].unit_name if crs.axis_info else ""
    return _UNITS.get(name, name or "map units")


def _authority(crs: CRS, body: Body | None) -> str | None:
    """The CRS's own authority code, else the first ``IAU_2015`` or EPSG code
    that is the same CRS (``sameCrs``, ignoring axis order): among those
    PROJ suggests, then among the body's ``IAU_2015`` codes."""
    found = crs.to_authority(min_confidence=100)
    if found is not None:
        return ":".join(found)
    for candidate in _candidates(crs, body):
        if sameCrs(crs, candidate, ignoreAxisOrder=True):
            return candidate
    return None


def _candidates(crs: CRS, body: Body | None) -> list[str]:
    suggested = [
        f"{match.auth_name}:{match.code}"
        for match in crs.list_authority(min_confidence=0)
        if match.auth_name in _AUTHORITIES
    ]
    own = list(_iauCodes(body.naifId)) if body is not None else []
    return list(dict.fromkeys(suggested + own))


@lru_cache(maxsize=32)
def _iauCodes(naifId: int) -> tuple[str, ...]:
    """The ``IAU_2015`` CRS codes PROJ lists for the body: ``naif × 100``
    plus 0, 1, 2, 10, 11, 12, 15, … The caller holds PROJ_LOCK."""
    codes = sorted(
        int(code)
        for code in get_codes("IAU_2015", PJType.CRS)
        if int(code) // 100 == naifId
    )
    return tuple(f"IAU_2015:{code}" for code in codes)


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


def _pixelSides(
    crs: CRS, key: str, transform: Affine, width: int, height: int
) -> tuple[float | None, float | None]:
    """A pixel's sides on the ground across the image center: the geodesics
    from the middle of one edge to the middle of the opposite one, on the
    CRS's own surface (``measure.cumulativeDistance``), along its row and
    along its column; None where PROJ can't invert. A CRS on no body: map
    units times the unit's size. The caller holds PROJ_LOCK."""
    if crs.ellipsoid is None:
        factor = crs.axis_info[0].unit_conversion_factor if crs.axis_info else 1.0
        return (
            math.hypot(transform.a, transform.d) * factor,
            math.hypot(transform.b, transform.e) * factor,
        )
    col, row = width / 2, height / 2
    sides = []
    for start, end in (
        ((col - 0.5, row), (col + 0.5, row)),
        ((col, row - 0.5), (col, row + 0.5)),
    ):
        xy = np.array([transform * start, transform * end])
        try:
            size = float(cumulativeDistance(key, xy)[-1])
        except ValueError:
            size = math.nan
        sides.append(size if math.isfinite(size) else None)
    return sides[0], sides[1]
