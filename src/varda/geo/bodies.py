"""The bodies Varda knows, with their sizes from PROJ's ``IAU_2015``
authority (Archinal et al., 2018; Hare et al., 2018), and which body a
coordinate system is on.

A body's ``IAU_2015`` geographic sphere is code ``naif × 100``, its
planetographic ellipsoid ``naif × 100 + 1`` and its planetocentric ellipsoid
``naif × 100 + 2``, where PROJ has them. An unknown body is ``None``, never
Earth.
"""

from __future__ import annotations

import re

from attrs import frozen
from pyproj import CRS, Geod
from pyproj.crs import Ellipsoid
from pyproj.exceptions import CRSError

from varda.utilities.proj_lock import PROJ_LOCK

# Within this fraction of a body's radii, a CRS's semi-major axis is that body's
RADIUS_TOLERANCE = 0.01


@frozen
class Body:
    """A planetary body: its NAIF id, its IAU 2015 radii (m) and its
    planetographic longitude convention."""

    name: str
    naifId: int
    equatorialRadius: float  # a, m
    polarRadius: float  # c, m (= a for bodies PROJ models as spheres)
    meanRadius: float  # IAU mean radius, m
    westPositiveOgraphic: bool  # planetographic longitude increases westward

    def sphereCrs(self) -> CRS:
        """The body's IAU 2015 geographic sphere (a fresh object)."""
        with PROJ_LOCK:
            return CRS.from_user_input(f"IAU_2015:{self.naifId * 100}")

    def referenceCrs(self) -> CRS:
        """The body's IAU 2015 planetocentric ellipsoid, or its sphere when
        PROJ has no ellipsoid for it (a fresh object)."""
        with PROJ_LOCK:
            try:
                return CRS.from_user_input(f"IAU_2015:{self.naifId * 100 + 2}")
            except CRSError:
                return self.sphereCrs()

    def geod(self) -> Geod:
        """Geodesics on the body's reference ellipsoid (a fresh object)."""
        with PROJ_LOCK:
            return Geod(a=self.equatorialRadius, b=self.polarRadius)


# NAIF id and IAU mean radius (m) of each body
_KNOWN: dict[str, tuple[int, float]] = {
    "Mercury": (199, 2_439_400.0),
    "Venus": (299, 6_051_800.0),
    "Earth": (399, 6_371_008.8),
    "Moon": (301, 1_737_400.0),
    "Mars": (499, 3_389_500.0),
    "Phobos": (401, 11_080.0),
    "Ceres": (2000001, 469_700.0),
    "Vesta": (2000004, 262_700.0),
    "Io": (501, 1_821_490.0),
    "Europa": (502, 1_560_800.0),
    "Ganymede": (503, 2_631_200.0),
    "Callisto": (504, 2_410_300.0),
    "Enceladus": (602, 252_100.0),
    "Titan": (606, 2_574_730.0),
}
# Planetographic longitude is east positive for these when PROJ has no
# planetographic CRS to say (Archinal et al., 2018): Earth and the Moon by
# tradition, Venus as a retrograde rotator, and dwarf planets and asteroids
# such as Vesta
_EAST_POSITIVE_OGRAPHIC = {"Earth", "Moon", "Venus", "Vesta"}


def _ographic(naifId: int) -> CRS | None:
    try:
        return CRS.from_user_input(f"IAU_2015:{naifId * 100 + 1}")
    except CRSError:
        return None


def _body(name: str, naifId: int, meanRadius: float) -> Body:
    """The body, with a and c from its planetographic ellipsoid (or its
    sphere) and its longitude direction from the planetographic CRS's axis."""
    ographic = _ographic(naifId)
    ellipsoid = (ographic or CRS.from_user_input(f"IAU_2015:{naifId * 100}")).ellipsoid
    if ellipsoid is None:
        raise ValueError(f"PROJ's IAU_2015 has no ellipsoid for {name}.")
    if ographic is not None:
        westPositive = any(axis.direction == "west" for axis in ographic.axis_info)
    else:
        westPositive = name not in _EAST_POSITIVE_OGRAPHIC
    return Body(
        name=name,
        naifId=naifId,
        equatorialRadius=ellipsoid.semi_major_metre,
        polarRadius=ellipsoid.semi_minor_metre,
        meanRadius=meanRadius,
        westPositiveOgraphic=westPositive,
    )


with PROJ_LOCK:
    BODIES: dict[str, Body] = {
        name: _body(name, naifId, meanRadius)
        for name, (naifId, meanRadius) in _KNOWN.items()
    }

_BY_NAIF = {body.naifId: body for body in BODIES.values()}
_ALIASES = {"luna": "Moon", "d_mars": "Mars", "mars_2000": "Mars", "moon_2000": "Moon"}
_DECORATIONS = re.compile(r"\(2015\)|\s-\s*sphere", re.IGNORECASE)
_PREFIXES = re.compile(r"^(?:d_|gcs_)+", re.IGNORECASE)
_WORD_SEPARATORS = re.compile(r"[\s/,()_\-]+")


def surfaceText(ellipsoid: Ellipsoid) -> str:
    """The surface in words: "sphere R = 3396.19 km" or
    "ellipsoid a = 3396.19, c = 3376.20 km" (km to 10 m)."""
    a = ellipsoid.semi_major_metre / 1000
    if ellipsoid.inverse_flattening == 0:
        return f"sphere R = {a:.2f} km"
    return f"ellipsoid a = {a:.2f}, c = {ellipsoid.semi_minor_metre / 1000:.2f} km"


def bodyNamed(name: str) -> Body | None:
    """The body called ``name``, ignoring case, IAU and ESRI decorations
    ("Mars (2015) - Sphere", "D_MARS", "GCS_Mars_2000") and aliases ("Luna")."""
    key = name.strip().lower()
    if key in _ALIASES:
        return BODIES[_ALIASES[key]]
    key = _PREFIXES.sub("", _DECORATIONS.sub("", key).strip()).strip()
    key = _ALIASES.get(key, key).lower()
    return next((body for body in BODIES.values() if body.name.lower() == key), None)


@frozen
class BodyMatch:
    """The body a coordinate system is on, and why Varda thinks so;
    ``candidates`` lists the bodies when its radius alone can't decide."""

    body: Body | None
    evidence: str
    candidates: tuple[str, ...] = ()


def identifyBody(crs: CRS | str | None, labelTarget: str | None = None) -> BodyMatch:
    """Which body ``crs`` is on. In order: the label's target name, an
    ``IAU_2015`` code, Earth's datums, the datum, ellipsoid and CRS names,
    then the radius (within 1%)."""
    if labelTarget:
        body = bodyNamed(labelTarget)
        if body is not None:
            return BodyMatch(body, f"label TARGET_NAME = {labelTarget}")
    if crs is None:
        return BodyMatch(None, "no coordinate system")
    with PROJ_LOCK:
        try:
            crs = CRS.from_user_input(crs)
        except CRSError as error:
            return BodyMatch(None, f"unreadable coordinate system ({error})")
        return _fromCrs(crs)


def _fromCrs(crs: CRS) -> BodyMatch:
    """identifyBody's coordinate system steps; the caller holds PROJ_LOCK."""
    code = _iauCode(crs)
    if code is not None and code // 100 in _BY_NAIF:
        return BodyMatch(_BY_NAIF[code // 100], f"IAU_2015:{code}")
    if _isEarth(crs):
        return BodyMatch(BODIES["Earth"], f"Earth datum {_datumName(crs)}")
    for kind, name in _names(crs):
        body = _namedIn(name)
        if body is not None:
            return BodyMatch(body, f"{kind} {name}")
    return _byRadius(crs)


def _iauCode(crs: CRS) -> int | None:
    authority = crs.to_authority(auth_name="IAU_2015")
    return int(authority[1]) if authority is not None else None


_EARTH_NAMES = ("wgs84", "wgs1984", "worldgeodeticsystem1984")


def _isEarth(crs: CRS) -> bool:
    """EPSG:4326, or a datum or ellipsoid from the EPSG registry (all of
    which are Earth's), or one named WGS 84."""
    geodetic = crs.geodetic_crs or crs
    parts = [geodetic, crs.datum, crs.ellipsoid]
    for part in parts:
        if part is None:
            continue
        identifier = part.to_json_dict().get("id") or {}
        if identifier.get("authority") == "EPSG":
            return True
        simple = re.sub(r"[^a-z0-9]", "", part.name.lower())
        if any(earth in simple for earth in _EARTH_NAMES):
            return True
    return False


def _datumName(crs: CRS) -> str:
    return crs.datum.name if crs.datum is not None else crs.name


def _names(crs: CRS) -> list[tuple[str, str]]:
    names = [
        ("datum", crs.datum.name if crs.datum is not None else None),
        ("ellipsoid", crs.ellipsoid.name if crs.ellipsoid is not None else None),
        ("CRS name", crs.name),
    ]
    return [(kind, name) for kind, name in names if name]


# Words shorter than this never name a body on their own ("IO", "D")
_MIN_WORD = 3


def _namedIn(name: str) -> Body | None:
    """The body ``name`` names, as a whole or by one of its words (a whole
    word of at least three letters, so a stray "IO" isn't Io)."""
    body = bodyNamed(name)
    if body is not None:
        return body
    for word in _WORD_SEPARATORS.split(name):
        body = bodyNamed(word) if len(word) >= _MIN_WORD else None
        if body is not None:
            return body
    return None


def _byRadius(crs: CRS) -> BodyMatch:
    ellipsoid = crs.ellipsoid
    if ellipsoid is None:
        return BodyMatch(None, "no ellipsoid")
    radius = ellipsoid.semi_major_metre
    low, high = 1 - RADIUS_TOLERANCE, 1 + RADIUS_TOLERANCE
    names = tuple(
        body.name
        for body in BODIES.values()
        if body.polarRadius * low <= radius <= body.equatorialRadius * high
        or body.meanRadius * low <= radius <= body.meanRadius * high
    )
    km = f"radius {radius / 1000:.10g} km"
    if len(names) == 1:
        return BodyMatch(BODIES[names[0]], km)
    if names:
        return BodyMatch(None, f"{km} matches {', '.join(names)}", names)
    return BodyMatch(None, f"{km} matches no known body")
