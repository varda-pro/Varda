"""The bodies Varda knows, with their sizes from PROJ's ``IAU_2015``
authority (Archinal et al., 2018; Hare et al., 2018), and which body a
coordinate system is on.

A body's ``IAU_2015`` geographic sphere is code ``naif × 100``, its
planetographic ellipsoid ``naif × 100 + 1`` and its planetocentric ellipsoid
``naif × 100 + 2``, where PROJ has them. An unknown body is ``None``, never
Earth.

``small_bodies.toml`` adds the bodies PROJ lacks (Bennu, Ryugu, Didymos,
Dimorphos, Arrokoth) and the density and rotation period of small bodies,
each value with its citation. A body's ``shape`` says whether a sphere or
ellipsoid stands for it: ``irregular`` when the table says so or when its
IAU 2015 radii differ by more than ``IRREGULAR_FLATTENING`` of its mean
radius ((a − c)/R; Archinal et al. 2018, Sect. 8).
"""

from __future__ import annotations

import re
import tomllib
from enum import StrEnum
from pathlib import Path

from attrs import field, frozen
from pyproj import CRS, Geod
from pyproj.crs import Ellipsoid
from pyproj.exceptions import CRSError

from varda.utilities.proj_lock import PROJ_LOCK

# Within this fraction of a body's radii, a CRS's semi-major axis is that body's
RADIUS_TOLERANCE = 0.01
# A body whose IAU radii differ by more than this fraction of its mean radius,
# (a − c)/R, is irregular: Eros (1.36) is; Mars (0.006) and Ceres (0.09) aren't
IRREGULAR_FLATTENING = 0.1
SMALL_BODIES_FILE = Path(__file__).with_name("small_bodies.toml")


class BodyShape(StrEnum):
    """Whether a sphere, an ellipsoid or neither stands for a body's shape."""

    SPHERE_LIKE = "sphere-like"
    ELLIPSOID_LIKE = "ellipsoid-like"
    IRREGULAR = "irregular"


@frozen
class Body:
    """A planetary body: its NAIF id, its IAU 2015 radii (m), its
    planetographic longitude convention, its shape class, and for small
    bodies its rotation period and density, each value cited in ``sources``
    (field name → citation)."""

    name: str
    naifId: int
    equatorialRadius: float  # a, m
    polarRadius: float  # c, m (= a for bodies PROJ models as spheres)
    meanRadius: float  # IAU mean radius, m
    westPositiveOgraphic: bool  # planetographic longitude increases westward
    shape: BodyShape = BodyShape.SPHERE_LIKE
    rotationPeriodH: float | None = None  # sidereal, hours
    density: tuple[float, float] | None = None  # kg/m³, value and 1σ
    inProj: bool = True  # PROJ's IAU_2015 has the body's CRSs
    sources: dict[str, str] = field(factory=dict, eq=False)

    def sphereCrs(self) -> CRS:
        """The body's IAU 2015 geographic sphere, or for a body PROJ lacks a
        sphere of its mean radius named for it (a fresh object)."""
        with PROJ_LOCK:
            if not self.inProj:
                return CRS.from_wkt(_sphereWkt(self.name, self.meanRadius))
            return CRS.from_user_input(f"IAU_2015:{self.naifId * 100}")

    def referenceCrs(self) -> CRS:
        """The body's IAU 2015 planetocentric ellipsoid, or its sphere when
        PROJ has no ellipsoid for it (a fresh object)."""
        if not self.inProj:
            return self.sphereCrs()
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
    "Eros": (2000433, 8_450.0),
    "Itokawa": (2025143, 173.0),
    "Churyumov-Gerasimenko": (1000012, 1_650.0),
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


def _sphereWkt(name: str, radius: float) -> str:
    """A planetocentric sphere CRS for a body PROJ lacks, named like PROJ's
    IAU_2015 ones so ``identifyBody`` finds the body by its datum."""
    degree = 'ANGLEUNIT["degree",0.0174532925199433]'
    return (
        f'GEOGCRS["{name} - Sphere / Ocentric",'
        f'DATUM["{name}",ELLIPSOID["{name}",{radius!r},0,LENGTHUNIT["metre",1]]],'
        f'PRIMEM["Reference Meridian",0,{degree}],CS[ellipsoidal,2],'
        f'AXIS["geodetic latitude (Lat)",north,ORDER[1],{degree}],'
        f'AXIS["geodetic longitude (Lon)",east,ORDER[2],{degree}]]'
    )


def _shapeFromRadii(a: float, c: float, meanRadius: float) -> BodyShape:
    if (a - c) / meanRadius > IRREGULAR_FLATTENING:
        return BodyShape.IRREGULAR
    return BodyShape.SPHERE_LIKE if a == c else BodyShape.ELLIPSOID_LIKE


def _smallBodyValues(entry: dict) -> dict:
    """The Body fields a ``small_bodies.toml`` entry sets."""
    values: dict = {"sources": dict(entry.get("sources", {}))}
    if "rotationPeriodH" in entry:
        values["rotationPeriodH"] = float(entry["rotationPeriodH"])
    if "density" in entry:
        value, sigma = entry["density"]
        values["density"] = (float(value), float(sigma))
    if "shape" in entry:
        values["shape"] = BodyShape(entry["shape"])
    return values


# PROJ's IAU_2015 holds the mean radii in _KNOWN (Archinal et al., 2018)
_IAU_RADIUS_SOURCE = "Archinal et al. (2018), CMDA 130, 22, Tables 4-6 (PROJ IAU_2015)"


def _body(name: str, naifId: int, meanRadius: float, extra: dict) -> Body:
    """The body, with a and c from its planetographic ellipsoid (or its
    sphere) and its longitude direction from the planetographic CRS's axis;
    ``extra`` holds its ``small_bodies.toml`` entry, if any."""
    ographic = _ographic(naifId)
    ellipsoid = (ographic or CRS.from_user_input(f"IAU_2015:{naifId * 100}")).ellipsoid
    if ellipsoid is None:
        raise ValueError(f"PROJ's IAU_2015 has no ellipsoid for {name}.")
    if ographic is not None:
        westPositive = any(axis.direction == "west" for axis in ographic.axis_info)
    else:
        westPositive = name not in _EAST_POSITIVE_OGRAPHIC
    a, c = ellipsoid.semi_major_metre, ellipsoid.semi_minor_metre
    values = _smallBodyValues(extra)
    values["sources"] = {"meanRadius": _IAU_RADIUS_SOURCE} | values["sources"]
    values.setdefault("shape", _shapeFromRadii(a, c, meanRadius))
    return Body(
        name=name,
        naifId=naifId,
        equatorialRadius=a,
        polarRadius=c,
        meanRadius=meanRadius,
        westPositiveOgraphic=westPositive,
        **values,
    )


def _bodyProjLacks(name: str, entry: dict) -> Body:
    """A body from ``small_bodies.toml`` alone: a sphere of its mean radius,
    east-positive like other small bodies (Archinal et al., 2018)."""
    radius = float(entry["meanRadius"])
    return Body(
        name=name,
        naifId=int(entry["naifId"]),
        equatorialRadius=radius,
        polarRadius=radius,
        meanRadius=radius,
        westPositiveOgraphic=False,
        inProj=False,
        **_smallBodyValues(entry),
    )


_SMALL_BODIES: dict[str, dict] = tomllib.loads(SMALL_BODIES_FILE.read_text("utf-8"))

with PROJ_LOCK:
    BODIES: dict[str, Body] = {
        name: _body(name, naifId, meanRadius, _SMALL_BODIES.get(name, {}))
        for name, (naifId, meanRadius) in _KNOWN.items()
    }
BODIES |= {
    name: _bodyProjLacks(name, entry)
    for name, entry in _SMALL_BODIES.items()
    if name not in BODIES
}


def smallBodies() -> list[Body]:
    """The bodies ``small_bodies.toml`` describes, in its order."""
    return [BODIES[name] for name in _SMALL_BODIES]


_BY_NAIF = {body.naifId: body for body in BODIES.values()}
_ALIASES = {
    "luna": "Moon",
    "d_mars": "Mars",
    "mars_2000": "Mars",
    "moon_2000": "Moon",
    "67p": "Churyumov-Gerasimenko",
    "67p/churyumov-gerasimenko": "Churyumov-Gerasimenko",
    "2014 mu69": "Arrokoth",
    "ultima thule": "Arrokoth",
}
# A minor planet's number before its name: "(101955) Bennu", "433 Eros"
_DESIGNATION = re.compile(r"^\(?\d+\)?\s+")
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


def latitudeType(crs: CRS) -> str:
    """The latitude convention of ``crs``'s geodetic CRS: planetocentric when
    it says so (its latitude axis, or a spherical coordinate system) or its
    surface is a sphere; otherwise its geodetic latitude is planetographic
    (Seidelmann et al., 2002). Holds PROJ_LOCK."""
    with PROJ_LOCK:
        geodetic = crs.geodetic_crs or crs
        ellipsoid = geodetic.ellipsoid
        if ellipsoid is None or ellipsoid.inverse_flattening == 0:
            return "planetocentric"
        names = " ".join(axis.name.lower() for axis in geodetic.axis_info)
        system = geodetic.coordinate_system
        subtype = system.to_json_dict().get("subtype") if system is not None else None
    if "planetocentric" in names or "geocentric" in names or subtype == "spherical":
        return "planetocentric"
    return "planetographic"


# Label targets that name no body: calibration and sky observations, and
# placeholders. Compared ignoring case, spaces and underscores
NON_BODY_TARGETS = frozenset(
    {
        "SKY", "N/A", "NA", "NONE", "NULL", "UNK", "UNKNOWN", "CALIBRATION",
        "CAL", "CALIBRATION TARGET", "DARK", "DARK CURRENT", "FLAT", "FLAT FIELD",
        "STAR", "STARS", "SPACE", "DEEP SPACE", "BIAS", "TEST",
    }
)  # fmt: skip


def isNonBodyTarget(name: str) -> bool:
    """Whether a label's target ``name`` names no body (``NON_BODY_TARGETS``),
    e.g. "SKY", "n/a" or "Calibration"."""
    return " ".join(name.replace("_", " ").split()).upper() in NON_BODY_TARGETS


def bodyNamed(name: str) -> Body | None:
    """The body called ``name``, ignoring case, IAU and ESRI decorations
    ("Mars (2015) - Sphere", "D_MARS", "GCS_Mars_2000") and aliases ("Luna")."""
    key = name.strip().lower()
    if key not in _ALIASES:
        key = _DESIGNATION.sub("", key)
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
