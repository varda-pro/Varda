"""Whether two coordinate systems are the same, and whether images on them
can be combined.

PROJ's ``equals`` compares names, so GDAL's ``D_MARS`` WKT and
``IAU_2015:49910`` differ to PROJ though they describe one projection on
one sphere. Varda also calls two CRSs the same (EQUIVALENT) when their
geometry matches with every name ignored: the conversion method and its
parameters (angles to 1e-9°, lengths to 1e-6 m), the ellipsoid's a and b
(to 1e-3 m), the prime meridian, and the axes' directions and units.

CRSs that aren't the same are compared by body (``identifyBody``): on one
body they can be reprojected (SAME_BODY), on different bodies never. When
a body is unknown its radii decide: equal radii (to 1e-3 m) are SAME_BODY,
otherwise DIFFERENT_BODY; an unknown body is never Earth."""

from __future__ import annotations

import math
from enum import Enum
from functools import lru_cache

from pyproj import CRS
from pyproj.crs import CoordinateOperation, Ellipsoid
from pyproj.exceptions import CRSError

from varda.geo.bodies import Body, identifyBody, surfaceText
from varda.utilities.proj_lock import PROJ_LOCK

ANGLE_TOLERANCE_DEG = 1e-9
LENGTH_TOLERANCE_M = 1e-6
RADIUS_TOLERANCE_M = 1e-3
# a conversion parameter a CRS leaves out has PROJ's default for its kind
_DEFAULTS = {"angular": 0.0, "linear": 0.0, "scale": 1.0}
_TOLERANCES = {"angular": ANGLE_TOLERANCE_DEG, "linear": LENGTH_TOLERANCE_M}
# the CRS types compared by geometry; others (bound, compound, ...) only by PROJ
_COMPARABLE = {
    "Projected CRS",
    "Geographic 2D CRS",
    "Geographic 3D CRS",
    "Geodetic CRS",
}


class Compatibility(Enum):
    """How two coordinate systems relate, from most to least alike."""

    SAME = "same"  # PROJ-equivalent (names may differ)
    EQUIVALENT = "equivalent"  # same body, surface, method and parameters; names differ
    SAME_BODY = "same body"  # same body, different projection or surface: reproject
    DIFFERENT_BODY = "different body"  # never combine
    UNKNOWN = "unknown"  # a CRS is missing, unreadable or on no body


def sameCrs(
    a: CRS | str | None, b: CRS | str | None, *, ignoreAxisOrder: bool = False
) -> bool:
    """Whether ``a`` and ``b`` are the same CRS, by PROJ or with names
    ignored (SAME or EQUIVALENT). Two missing CRSs are the same; one is not."""
    if a is None or b is None:
        return a is None and b is None
    with PROJ_LOCK:
        return _sameness(_key(a), _key(b), ignoreAxisOrder) is not None


def compatibility(a: CRS | str | None, b: CRS | str | None) -> Compatibility:
    """How ``a`` and ``b`` relate; UNKNOWN when either is missing,
    unreadable, or on no body (a local CRS)."""
    if a is None or b is None:
        return Compatibility.UNKNOWN
    with PROJ_LOCK:
        return _compatibility(_key(a), _key(b))


def canTransform(a: CRS | str | None, b: CRS | str | None) -> str | None:
    """None when coordinates on ``a`` can be put on ``b`` (the same CRS, or
    the same body), else why not."""
    match = compatibility(a, b)
    if match == Compatibility.DIFFERENT_BODY:
        reason = f"{_bodyLabel(a)} and {_bodyLabel(b)} are different bodies"
        return reason[0].upper() + reason[1:]
    if match != Compatibility.UNKNOWN:
        return None
    for crs, which in ((a, "first"), (b, "second")):
        problem = _problem(crs)
        if problem is not None:
            return f"The {which} image{problem}"
    return "The coordinate systems can't be compared"


def _key(crs: CRS | str) -> str:
    """The CRS as text, for caching; the caller holds PROJ_LOCK."""
    return crs if isinstance(crs, str) else CRS.from_user_input(crs).srs


@lru_cache(maxsize=256)
def _parsed(key: str) -> CRS | None:
    """The CRS ``key`` names, parsed once (a long WKT takes milliseconds);
    used only inside PROJ_LOCK and never returned to callers."""
    try:
        return CRS.from_user_input(key)
    except CRSError:
        return None


@lru_cache(maxsize=1024)
def _sameness(keyA: str, keyB: str, ignoreAxisOrder: bool) -> Compatibility | None:
    """SAME, EQUIVALENT or None; the caller holds PROJ_LOCK."""
    a, b = _parsed(keyA), _parsed(keyB)
    if a is None or b is None:
        return None
    if a.equals(b, ignore_axis_order=ignoreAxisOrder):
        return Compatibility.SAME
    if _equivalent(a, b, ignoreAxisOrder):
        return Compatibility.EQUIVALENT
    return None


@lru_cache(maxsize=1024)
def _compatibility(keyA: str, keyB: str) -> Compatibility:
    """The caller holds PROJ_LOCK."""
    same = _sameness(keyA, keyB, False)
    if same is not None:
        return same
    a, b = _parsed(keyA), _parsed(keyB)
    if a is None or b is None or a.ellipsoid is None or b.ellipsoid is None:
        return Compatibility.UNKNOWN
    bodyA, bodyB = _body(keyA), _body(keyB)
    if bodyA is not None and bodyB is not None:
        if bodyA.name == bodyB.name:
            return Compatibility.SAME_BODY
        return Compatibility.DIFFERENT_BODY
    if _sameRadii(a.ellipsoid, b.ellipsoid):
        return Compatibility.SAME_BODY
    return Compatibility.DIFFERENT_BODY


@lru_cache(maxsize=256)
def _body(key: str) -> Body | None:
    """The CRS's body (identifying it takes milliseconds); the caller holds
    PROJ_LOCK."""
    return identifyBody(key).body


def _bodyLabel(crs: CRS | str | None) -> str:
    """ "Mars", or "an unknown body (sphere R = 3000.00 km)"."""
    if crs is None:
        return "no body"
    with PROJ_LOCK:
        key = _key(crs)
        body = _body(key)
        if body is not None:
            return body.name
        parsed = _parsed(key)
        if parsed is None or parsed.ellipsoid is None:
            return "no body"
        return f"an unknown body ({surfaceText(parsed.ellipsoid)})"


def _problem(crs: CRS | str | None) -> str | None:
    """What keeps ``crs`` from being compared, completing "The first image…"."""
    if crs is None:
        return " has no coordinate system"
    with PROJ_LOCK:
        parsed = _parsed(_key(crs))
        if parsed is None:
            return "'s coordinate system is unreadable"
        if parsed.ellipsoid is None:
            return "'s coordinate system is on no body"
    return None


def _close(x: float, y: float, tolerance: float) -> bool:
    return abs(x - y) <= tolerance


def _sameRadii(a: Ellipsoid, b: Ellipsoid) -> bool:
    return _close(
        a.semi_major_metre, b.semi_major_metre, RADIUS_TOLERANCE_M
    ) and _close(a.semi_minor_metre, b.semi_minor_metre, RADIUS_TOLERANCE_M)


def _equivalent(a: CRS, b: CRS, ignoreAxisOrder: bool) -> bool:
    """The same geometry with every name ignored: CRS type, ellipsoid,
    prime meridian, axes, conversion method and parameters."""
    if a.type_name != b.type_name or a.type_name not in _COMPARABLE:
        return False
    if a.ellipsoid is None or b.ellipsoid is None:
        return False
    if not _sameRadii(a.ellipsoid, b.ellipsoid):
        return False
    if not _samePrimeMeridian(a, b) or not _sameAxes(a, b, ignoreAxisOrder):
        return False
    conversionA, conversionB = a.coordinate_operation, b.coordinate_operation
    if conversionA is None or conversionB is None:
        return conversionA is None and conversionB is None
    spheres = a.ellipsoid.inverse_flattening == 0 == b.ellipsoid.inverse_flattening
    if _method(conversionA.method_name, spheres) != _method(
        conversionB.method_name, spheres
    ):
        return False
    return _sameParameters(_parameters(conversionA), _parameters(conversionB))


def _method(name: str, sphere: bool) -> str:
    """The method's name in lower case; on a sphere a method's "(Spherical)"
    form is the same formula."""
    name = name.lower()
    return name.removesuffix(" (spherical)") if sphere else name


def _samePrimeMeridian(a: CRS, b: CRS) -> bool:
    meridianA, meridianB = a.prime_meridian, b.prime_meridian
    if meridianA is None or meridianB is None:
        return meridianA is None and meridianB is None
    return _close(
        math.degrees(meridianA.longitude * float(meridianA.unit_conversion_factor)),
        math.degrees(meridianB.longitude * float(meridianB.unit_conversion_factor)),
        ANGLE_TOLERANCE_DEG,
    )


def _sameAxes(a: CRS, b: CRS, ignoreAxisOrder: bool) -> bool:
    """The same coordinate system kind and axes (direction and unit)."""
    if _subtype(a) != _subtype(b):
        return False
    axesA, axesB = _axes(a), _axes(b)
    if ignoreAxisOrder:
        axesA, axesB = sorted(axesA), sorted(axesB)
    return len(axesA) == len(axesB) and all(
        directionA == directionB and math.isclose(unitA, unitB, rel_tol=1e-12)
        for (directionA, unitA), (directionB, unitB) in zip(axesA, axesB)
    )


def _subtype(crs: CRS) -> str | None:
    system = crs.coordinate_system
    return system.to_json_dict().get("subtype") if system is not None else None


def _axes(crs: CRS) -> list[tuple[str, float]]:
    return [(axis.direction, axis.unit_conversion_factor) for axis in crs.axis_info]


def _parameters(conversion: CoordinateOperation) -> dict[str, tuple[str, float | str]]:
    """Each parameter by name: its kind and its value in degrees (angles),
    meters (lengths) or as a factor (scales)."""
    values: dict[str, tuple[str, float | str]] = {}
    for param in conversion.params:
        value = param.value
        if isinstance(value, (int, float)):
            value = value * param.unit_conversion_factor
            if param.unit_category == "angular":
                value = math.degrees(value)
        values[param.name] = (param.unit_category, value)
    return values


def _sameParameters(
    a: dict[str, tuple[str, float | str]], b: dict[str, tuple[str, float | str]]
) -> bool:
    """Every parameter of either matches; one left out has its default
    (0 for angles and lengths, 1 for scales)."""
    for name in a.keys() | b.keys():
        category, _ = a.get(name) or b[name]
        if category not in _DEFAULTS and (name not in a or name not in b):
            return False
        default = (category, _DEFAULTS.get(category, 0.0))
        valueA, valueB = a.get(name, default)[1], b.get(name, default)[1]
        if isinstance(valueA, str) or isinstance(valueB, str):
            if valueA != valueB:
                return False
        elif not _close(valueA, valueB, _TOLERANCES.get(category, 1e-12)):
            return False
    return True
