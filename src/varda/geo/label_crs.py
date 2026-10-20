"""A CRS and pixel-to-map transform built from a label's map projection
group (spec §4.8): PDS3's ``IMAGE_MAP_PROJECTION``, ISIS's ``Mapping`` or
PDS4's ``cart:`` elements, as ``labels.labelHints`` keeps them.

GDAL builds a CRS from the same labels, but sometimes builds none (a
projection or keyword it doesn't handle, no radii) or the wrong one (GDAL
3.9.3's PDS3 driver reads a west-positive label as east-positive, and a
planetographic label on an ellipsoid as a sphere). The health check compares
GDAL's CRS with this one.

**Keys and units.** Each value is looked up under its PDS3, ISIS and PDS4
names. Lengths are in the unit the label gives beside them (``{KEY}_UNIT``,
km or m); without one, PDS3 radii and ``MAP_SCALE`` are km (PDS3 Standards
Reference) and ISIS and PDS4 lengths are m. A value that isn't a number
("N/A", "UNK") counts as missing.

**Surface.** a = the equatorial radius, c = the polar radius. a = c (to
1 m): the sphere R = a. A planetographic label with a ≠ c: the ellipsoid
(a, c), whose geodetic latitude is planetographic. A planetocentric label
with a ≠ c has no exact PROJ projection string, so Varda uses a sphere and
says so in ``assumed``: Equirectangular with center latitude φ ≠ 0, the local
radius at φ, r = a·c / √((c cos φ)² + (a sin φ)²) (ISIS's convention, which
gives CRISM MTRDRs their scene-specific spheres); Polar Stereographic, c; any
other projection, a (both as GDAL 3.9.3 does). ISIS's Equirectangular uses r
whatever the latitude type (x = r·cos φ₀·Δλ, y = r·φ; ``SetGround`` in ISIS's
Equirectangular.cpp), so a planetographic Equirectangular label with φ ≠ 0
gets the body's ellipsoid scaled to a = r (PROJ's eqc on an ellipsoid is
x = a·cos φ₀·Δλ, y = a·φ), also in ``assumed``. No radii: the label target's
IAU 2015 sphere (Archinal et al., 2018), also in ``assumed``.

**West-positive labels, and the direction of map x.** For a label whose
longitude is positive west (PDS3 ``POSITIVE_LONGITUDE_DIRECTION = WEST``,
ISIS ``PositiveWest``, PDS4 "Positive West"), the CRS's ``lon_0`` is the
east-positive equivalent of the label's center longitude, and its geodetic
CRS's longitude axis points west, so readouts show °W as the label does.
**Varda assumes map x still increases eastward** (and so sample number, to
the east), as in an east-positive label: the PDS3 equirectangular relation
for west longitudes, lon = CENTER_LONGITUDE − x / (R cos φ₁) with x =
(sample − SAMPLE_PROJECTION_OFFSET − 1)·MAP_SCALE; ISIS's projections, which
negate the longitude difference for PositiveWest before computing x; and
GDAL, whose transform for a west label has the same positive pixel width as
for an east one. A product that instead numbers samples westward would
appear mirrored about its center meridian; compare its corners with the
label's ``WESTERNMOST_LONGITUDE`` and ``EASTERNMOST_LONGITUDE`` to check.
This is not listed in ``assumed``: every convention Varda knows agrees.

**Transform.** PDS3: s = MAP_SCALE in m, upper-left corner
x₀ = −(SAMPLE_PROJECTION_OFFSET + 0.5)·s and y₀ = (LINE_PROJECTION_OFFSET +
0.5)·s, pixels s × −s (the PDS3 standard's 1-based pixel centers; GDAL builds
the same). ISIS and PDS4: the resolution and upper-left corner keys.

Everything here is Qt-free and never raises.
"""

from __future__ import annotations

import math
import re

from attrs import frozen
from pyproj import CRS

from varda.geo.bodies import bodyNamed
from varda.geo.labels import LabelHints
from varda.utilities.proj_lock import PROJ_LOCK

# Radii closer than this (m) are one sphere
SPHERE_TOLERANCE_M = 1.0

type Transform = tuple[float, float, float, float, float, float]


@frozen
class LabelCrs:
    """The CRS and transform a label's map projection group describes."""

    # The CRS as WKT, or as PROJJSON for a west-positive label (WKT has no
    # place for a projected CRS's base longitude axis, only an authority
    # code); any text PROJ reads. None when the group can't make one
    wkt: str | None
    # Affine's a, b, c, d, e, f from the label, or None when it has no
    # complete pixel-to-map keys
    transform: Transform | None
    projection: str  # the label's name for it, e.g. "EQUIRECTANGULAR"
    assumed: tuple[str, ...]  # conventions Varda had to assume, in words
    missing: tuple[str, ...]  # keys needed and absent (or unreadable)
    source: str  # hints.source

    @property
    def complete(self) -> bool:
        """Whether there's a CRS and Varda assumed nothing to build it."""
        return self.wkt is not None and not self.assumed


# Each value's keys: PDS3's, ISIS's, then PDS4's names. Lookup tries all;
# messages name the label's own.
_PDS3, _ISIS, _PDS4 = 0, 1, 2
type _Keys = tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]

PROJECTION: _Keys = (
    ("MAP_PROJECTION_TYPE",),
    ("PROJECTIONNAME",),
    ("MAP_PROJECTION_NAME",),
)
A_RADIUS: _Keys = (("A_AXIS_RADIUS",), ("EQUATORIALRADIUS",), ("A_AXIS_RADIUS",))
B_RADIUS: _Keys = (("B_AXIS_RADIUS",), (), ("B_AXIS_RADIUS",))
C_RADIUS: _Keys = (("C_AXIS_RADIUS",), ("POLARRADIUS",), ("C_AXIS_RADIUS",))
# The projection's origin latitude (lat_0)
ORIGIN_LATITUDE: _Keys = (
    ("CENTER_LATITUDE",),
    ("CENTERLATITUDE",),
    ("LATITUDE_OF_PROJECTION_ORIGIN",),
)
# The latitude of true scale (Equirectangular, Mercator, Polar Stereographic)
TRUE_SCALE_LATITUDE: _Keys = (
    ("CENTER_LATITUDE",),
    ("CENTERLATITUDE",),
    ("STANDARD_PARALLEL_1", "LATITUDE_OF_PROJECTION_ORIGIN"),
)
CENTER_LONGITUDE: _Keys = (
    ("CENTER_LONGITUDE",),
    ("CENTERLONGITUDE",),
    ("LONGITUDE_OF_CENTRAL_MERIDIAN", "STRAIGHT_VERTICAL_LONGITUDE_FROM_POLE"),
)
SCALE_FACTOR: _Keys = (
    ("SCALE_FACTOR",),
    ("SCALEFACTOR",),
    ("SCALE_FACTOR_AT_CENTRAL_MERIDIAN", "SCALE_FACTOR_AT_PROJECTION_ORIGIN"),
)
FIRST_PARALLEL: _Keys = (
    ("FIRST_STANDARD_PARALLEL",),
    ("FIRSTSTANDARDPARALLEL",),
    ("STANDARD_PARALLEL_1",),
)
SECOND_PARALLEL: _Keys = (
    ("SECOND_STANDARD_PARALLEL",),
    ("SECONDSTANDARDPARALLEL",),
    ("STANDARD_PARALLEL_2",),
)
PIXEL_SIZE_X: _Keys = (("MAP_SCALE",), ("PIXELRESOLUTION",), ("PIXEL_RESOLUTION_X",))
PIXEL_SIZE_Y: _Keys = (("MAP_SCALE",), ("PIXELRESOLUTION",), ("PIXEL_RESOLUTION_Y",))
LINE_OFFSET: _Keys = (("LINE_PROJECTION_OFFSET",), (), ())
SAMPLE_OFFSET: _Keys = (("SAMPLE_PROJECTION_OFFSET",), (), ())
# ISIS writes the local radius its Equirectangular used (m)
LOCAL_RADIUS: _Keys = ((), ("CENTERLATITUDERADIUS",), ())
CORNER_X: _Keys = ((), ("UPPERLEFTCORNERX",), ("UPPERLEFT_CORNER_X",))
CORNER_Y: _Keys = ((), ("UPPERLEFTCORNERY",), ("UPPERLEFT_CORNER_Y",))

# PDS3 keys whose lengths are km when the label gives no unit
_PDS3_KM_KEYS = frozenset(
    {"A_AXIS_RADIUS", "B_AXIS_RADIUS", "C_AXIS_RADIUS", "MAP_SCALE"}
)
_LENGTH_UNITS = {
    "m": 1.0,
    "meter": 1.0,
    "meters": 1.0,
    "metre": 1.0,
    "metres": 1.0,
    "km": 1000.0,
    "kilometer": 1000.0,
    "kilometers": 1000.0,
    "kilometre": 1000.0,
    "kilometres": 1000.0,
}
_PER_PIXEL = re.compile(r"\s*(?:/|per)\s*(?:pixel|pix|px)s?\s*$")
_NAME_NOISE = re.compile(r"[\s_]+")


class _Group:
    """A projection group's numbers, by any of each value's names."""

    def __init__(self, hints: LabelHints) -> None:
        self.values = hints.projectionGroup
        self.dialect = self._dialect(hints)

    def _dialect(self, hints: LabelHints) -> int:
        for dialect in (_PDS3, _ISIS, _PDS4):
            if any(key in self.values for key in PROJECTION[dialect]):
                return dialect
        if hints.source.startswith("ISIS"):
            return _ISIS
        return _PDS4 if hints.source.startswith("PDS4") else _PDS3

    def name(self, keys: _Keys) -> str:
        """The value's key as this label spells it (another's when it has none)."""
        own = keys[self.dialect] or next(k for k in keys if k)
        return " or ".join(own)

    def text(self, keys: _Keys) -> str | None:
        for key in (key for names in keys for key in names):
            if self.values.get(key, "").strip():
                return self.values[key].strip()
        return None

    def number(self, keys: _Keys) -> float | None:
        """The first readable number under any of ``keys``."""
        for key in (key for names in keys for key in names):
            value = _float(self.values.get(key))
            if value is not None:
                return value
        return None

    def length(self, keys: _Keys) -> float | None:
        """The first readable length under any of ``keys``, in meters."""
        for key in (key for names in keys for key in names):
            value = _float(self.values.get(key))
            if value is None:
                continue
            unit = self.values.get(f"{key}_UNIT")
            if unit is None:
                kilometers = key in _PDS3_KM_KEYS and self.dialect != _PDS4
                return value * (1000.0 if kilometers else 1.0)
            factor = _LENGTH_UNITS.get(_PER_PIXEL.sub("", unit.strip().lower()))
            if factor is not None:
                return value * factor
        return None


def labelCrs(hints: LabelHints) -> LabelCrs | None:
    """The CRS and transform ``hints``' map projection group describes; None
    when there's no group. Never raises: what it can't build is in
    ``missing``."""
    if not hints.projectionGroup:
        return None
    group = _Group(hints)
    projection = group.text(PROJECTION) or ""
    try:
        return _build(group, hints, projection)
    except Exception as error:  # never raise to the health check
        return LabelCrs(
            None,
            None,
            projection,
            (),
            (f"a projection group Varda can read ({error})",),
            hints.source,
        )


def _build(group: _Group, hints: LabelHints, projection: str) -> LabelCrs:
    missing: list[str] = []
    assumed: list[str] = []
    method = _method(group, hints, projection, missing)
    surface = _surface(group, hints, method, assumed, missing)
    wkt = None
    if method is not None and surface is not None and not missing:
        wkt = _wkt(
            f"{method[1]} {surface} +x_0=0 +y_0=0 +units=m +no_defs",
            west=hints.longitudeDirection == "west",
            name=f"{hints.target or 'Unknown body'} {projection} (from its label)",
        )
    transform = _transform(group, missing)
    return LabelCrs(
        wkt, transform, projection, tuple(assumed), tuple(missing), hints.source
    )


# --- Projection ---


def _method(
    group: _Group, hints: LabelHints, projection: str, missing: list[str]
) -> tuple[str, str] | None:
    """The projection's kind ("eqc", "stere", …) and its PROJ parameters,
    or None (with what's missing)."""
    if not projection:
        missing.append(f"the projection's name ({group.name(PROJECTION)})")
        return None
    kind = _NAME_NOISE.sub("", projection).lower()
    needed: dict[str, float | None] = {}

    def value(keys: _Keys, default: float | None = None) -> float:
        number = group.number(keys)
        if number is None:
            number = default
        if number is None:
            needed[group.name(keys)] = None
            return math.nan
        return number

    longitude = value(CENTER_LONGITUDE)
    if hints.longitudeDirection == "west":
        # The label's center longitude is west-positive; PROJ's lon_0 is east.
        # Map x still increases eastward (see the module docstring)
        longitude = _eastOf(longitude)
    lon = f"+lon_0={longitude!r}"
    if kind == "equirectangular":
        params = f"+proj=eqc +lat_ts={value(TRUE_SCALE_LATITUDE)!r} +lat_0=0 {lon}"
    elif kind == "simplecylindrical":
        params = f"+proj=eqc +lat_ts=0 +lat_0=0 {lon}"
    elif kind == "polarstereographic":
        trueScale = value(TRUE_SCALE_LATITUDE)
        pole = math.copysign(90.0, trueScale)
        if abs(trueScale) == 90:
            # Variant A, true scale at the pole (as HIRISE_POLAR_PROJ)
            k = value(SCALE_FACTOR, 1.0)
            params = f"+proj=stere +lat_0={pole!r} {lon} +k={k!r}"
        else:
            # Variant B, true scale at the label's latitude
            params = f"+proj=stere +lat_0={pole!r} +lat_ts={trueScale!r} {lon}"
    elif kind == "sinusoidal":
        params = f"+proj=sinu {lon}"
    elif kind == "orthographic":
        params = f"+proj=ortho +lat_0={value(ORIGIN_LATITUDE)!r} {lon}"
    elif kind == "transversemercator":
        params = (
            f"+proj=tmerc +lat_0={value(ORIGIN_LATITUDE)!r} {lon} "
            f"+k={value(SCALE_FACTOR, 1.0)!r}"
        )
    elif kind == "mercator":
        params = f"+proj=merc +lat_ts={value(TRUE_SCALE_LATITUDE)!r} {lon}"
    elif kind in ("lambertconformal", "lambertconformalconic"):
        params = (
            f"+proj=lcc +lat_1={value(FIRST_PARALLEL)!r} "
            f"+lat_2={value(SECOND_PARALLEL)!r} "
            f"+lat_0={value(ORIGIN_LATITUDE)!r} {lon}"
        )
    elif kind == "lambertazimuthalequalarea":
        params = f"+proj=laea +lat_0={value(ORIGIN_LATITUDE)!r} {lon}"
    else:
        missing.append(f"a projection Varda can build: {projection}")
        return None
    if needed:
        missing.extend(needed)
        return None
    return kind, params


def _eastOf(westLongitude: float) -> float:
    """A west-positive longitude as east-positive, in (−180, 180]."""
    east = (-westLongitude + 180.0) % 360.0 - 180.0
    return 180.0 if east == -180.0 else east + 0.0  # no −0.0


# --- Surface ---


def _surface(
    group: _Group,
    hints: LabelHints,
    method: tuple[str, str] | None,
    assumed: list[str],
    missing: list[str],
) -> str | None:
    """PROJ's surface parameters (``+R=…`` or ``+a=… +b=…``), with what was
    assumed, or None (with what's missing)."""
    a, c = group.length(A_RADIUS), group.length(C_RADIUS)
    if a is None and c is None:
        body = bodyNamed(hints.target) if hints.target else None
        if body is None:
            radii = f"{group.name(A_RADIUS)} and {group.name(C_RADIUS)}"
            missing.append(f"the body's radii ({radii}), or a target Varda knows")
            return None
        sphere = body.sphereCrs().ellipsoid
        assert sphere is not None
        assumed.append(
            f"radii from the IAU 2015 {body.name} sphere; the label has none"
        )
        return f"+R={sphere.semi_major_metre!r}"
    a = a if a is not None else c
    c = c if c is not None else a
    assert a is not None and c is not None
    b = group.length(B_RADIUS)
    if b is not None and abs(b - a) > SPHERE_TOLERANCE_M:
        assumed.append(
            f"a triaxial body: {group.name(B_RADIUS)} ({b:.1f} m) is ignored, "
            f"and the surface uses only a ({a:.1f} m) and c ({c:.1f} m)"
        )
    if abs(a - c) <= SPHERE_TOLERANCE_M:
        return f"+R={a!r}"
    latitudeType = hints.latitudeType
    if latitudeType is None:
        assumed.append(
            "the label doesn't state its latitude type: planetocentric, "
            "the IAU's default"
        )
    kind = method[0] if method is not None else ""
    latitude = group.number(TRUE_SCALE_LATITUDE)
    if kind == "equirectangular" and latitude is not None and latitude != 0:
        # ISIS's Equirectangular: x = r·cos φ₀·Δλ, y = r·φ, with r the local
        # radius at the center latitude φ₀ and φ the label's own latitude,
        # whatever its type (ISIS Equirectangular.cpp, SetGround)
        phi = math.radians(latitude)
        radius = a * c / math.hypot(c * math.cos(phi), a * math.sin(phi))
        # ISIS's own value when it wrote one (the same formula, its precision)
        radius = group.length(LOCAL_RADIUS) or radius
        if latitudeType == "planetographic":
            # PROJ's eqc on an ellipsoid is x = a·cos φ₀·Δλ, y = a·φ (φ
            # geodetic), so the body's ellipsoid scaled to a = r gives ISIS's
            # numbers and keeps planetographic latitude as geodetic
            assumed.append(
                "ISIS's convention: the local radius at the center latitude "
                f"({radius:.4f} m at {latitude:g}°) as the map's scale, on the "
                f"body's ellipsoid scaled by {radius / a:.6f} (lengths measured "
                "on it are that much short)"
            )
            return f"+a={radius!r} +b={radius * c / a!r}"
        assumed.append(
            "ISIS's convention: the sphere of the local radius at the center "
            f"latitude ({radius:.4f} m at {latitude:g}°), since a planetocentric "
            "label on an ellipsoid has no exact PROJ form"
        )
        return f"+R={radius!r}"
    if latitudeType == "planetographic":
        return f"+a={a!r} +b={c!r}"
    if kind == "polarstereographic":
        assumed.append(
            f"the sphere of the polar radius ({c:.1f} m), as GDAL does, since a "
            "planetocentric label on an ellipsoid has no exact PROJ form"
        )
        return f"+R={c!r}"
    assumed.append(
        f"the sphere of the equatorial radius ({a:.1f} m), as GDAL does, since "
        "a planetocentric label on an ellipsoid has no exact PROJ form"
    )
    return f"+R={a!r}"


def _wkt(proj: str, *, west: bool, name: str) -> str:
    """The CRS of a PROJ string as WKT; when ``west``, with its geodetic
    longitude axis pointing west (as ``IAU_2015:49901``;
    ``transform.eastLonLat`` edits axes the same way), as PROJJSON, since
    WKT drops a base CRS's axes. Holds PROJ_LOCK."""
    with PROJ_LOCK:
        definition = CRS.from_user_input(proj).to_json_dict()
        definition["name"] = name
        if not west:
            return CRS.from_json_dict(definition).to_wkt()
        for axis in definition["base_crs"]["coordinate_system"]["axis"]:
            if axis["direction"] == "east":
                axis["direction"] = "west"
        return CRS.from_json_dict(definition).to_json()


# --- Transform ---


def _transform(group: _Group, missing: list[str]) -> Transform | None:
    """Affine's six numbers from the label's pixel-to-map keys."""
    sizeX = group.length(PIXEL_SIZE_X)
    sizeY = group.length(PIXEL_SIZE_Y) or sizeX
    if group.dialect == _PDS3:
        line, sample = group.number(LINE_OFFSET), group.number(SAMPLE_OFFSET)
        absent = [
            group.name(keys)
            for keys, number in (
                (PIXEL_SIZE_X, sizeX),
                (LINE_OFFSET, line),
                (SAMPLE_OFFSET, sample),
            )
            if number is None
        ]
        if absent or sizeX is None or line is None or sample is None:
            missing.extend(f"{key} (for the pixel grid)" for key in absent)
            return None
        # 1-based pixel centers: sample s is at x = (s − offset − 1)·size
        return (sizeX, 0.0, -(sample + 0.5) * sizeX, 0.0, -sizeX, (line + 0.5) * sizeX)
    x, y = group.length(CORNER_X), group.length(CORNER_Y)
    absent = [
        group.name(keys)
        for keys, number in (
            (PIXEL_SIZE_X, sizeX),
            (CORNER_X, x),
            (CORNER_Y, y),
        )
        if number is None
    ]
    if absent or sizeX is None or sizeY is None or x is None or y is None:
        missing.extend(f"{key} (for the pixel grid)" for key in absent)
        return None
    return (sizeX, 0.0, x, 0.0, -sizeY, y)


def _float(text: str | None) -> float | None:
    """``text`` as a finite number, else None ("N/A", "UNK", "")."""
    try:
        value = float((text or "").strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None
