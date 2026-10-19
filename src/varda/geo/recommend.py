"""A map projection for a scene, chosen for what it's for (spec §4.6).

1. Polar data (|center latitude| ≥ 65°, the HiRISE rule: McEwen et al.,
   2010; or a footprint that contains a pole): polar stereographic, true
   scale at the pole for a mosaic (the HiRISE/PDS convention) or at the
   scene's latitude otherwise.
2. Mosaic: equirectangular with ``lat_ts`` and ``lon_0`` the scene's center
   rounded to 1° (the CTX/HiRISE/ISIS ``cam2map`` convention), or the
   inputs' own CRS when they share one that is good enough over the scene.
3. Terrain and measurement: the image's own projected CRS when it's good
   enough over the scene (≤ 1% scale error, ≤ 1° angular distortion);
   otherwise transverse Mercator centered on the scene when every point is
   within 0.14 R of its central meridian (scale error ≤ 0.99%, Snyder, 1987,
   eq. 8-1), or Lambert azimuthal equal-area for wider scenes.
4. The surface: the body's IAU 2015 sphere, unless every input is on the
   body's IAU reference ellipsoid; for an unknown body, the image's own.

A global scene (both poles, or ≥ 180° of latitude or ≥ 300° of longitude)
gets equirectangular with ``lat_ts`` = 0 centered on its longitude (or keeps
its own CRS when it already is that): no single projection has low
distortion over it, so measurements stay geodesic on the body.

Longitudes and latitudes here are planetocentric, east, in degrees. Rounding
is half away from zero (77.5 → 78), not Python's banker's rounding. No Qt."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from affine import Affine
from attrs import frozen
from pyproj import CRS
from pyproj.crs import Ellipsoid

from varda.common.parameter import LabeledEnum
from varda.geo.bodies import Body, identifyBody, latitudeType
from varda.geo.describe import CrsDescription, describeCrs
from varda.geo.distortion import ANGLE_TIP_DEG, SCALE_TIP, Distortion, distortionAt
from varda.geo.equivalence import (
    RADIUS_TOLERANCE_M,
    Compatibility,
    compatibility,
    sameCrs,
)
from varda.geo.transform import (
    eastLonLat,
    ographicLatitude,
    planetocentricLonLat,
    transformPoints,
)
from varda.utilities.proj_lock import PROJ_LOCK

# |center latitude| from which a scene is polar (HiRISE's rule; McEwen et al., 2010)
POLAR_LATITUDE = 65.0
# transverse Mercator while every point is within this angle (radians) of the
# central meridian: k − 1 = 1/cos B − 1 ≤ 0.99% (Snyder, 1987, eq. 8-1)
TM_HALF_WIDTH = 0.14
# a scene this tall (degrees of latitude) or wide (of longitude) is global
GLOBAL_LATITUDE_SPAN = 180.0
GLOBAL_LONGITUDE_SPAN = 300.0
_SAME_BODY = (Compatibility.SAME, Compatibility.EQUIVALENT, Compatibility.SAME_BODY)
# points per footprint edge
_EDGE_POINTS = 21
# samples per side of the lon/lat grid that isGoodEnough checks
_GRID = 9


class Purpose(LabeledEnum):
    MEASUREMENT = "Measurement"
    MOSAIC = "Mosaic"
    TERRAIN = "Terrain"


@frozen
class Footprint:
    """A scene's extent in planetocentric longitude (east) and latitude,
    degrees; ``west < east``, unwrapped around the first edge point."""

    west: float
    south: float
    east: float
    north: float
    center: tuple[float, float]  # lon, lat
    # "north" | "south" when the image contains that pole, "both" when it
    # contains both (a global scene)
    pole: str | None


@frozen
class Recommendation:
    wkt: str
    description: CrsDescription
    reason: str  # "Polar stereographic: the scene reaches 82.3°N"
    keptExisting: bool


def footprintLonLat(
    crs: CRS | str, transform: Affine, width: int, height: int
) -> Footprint:
    """The planetocentric extent of an image of ``width`` × ``height`` pixels
    with ``transform`` on ``crs``, from 21 points along each edge. Raises
    ValueError for a CRS on no body or when PROJ can invert no edge point."""
    t = np.linspace(0.0, 1.0, _EDGE_POINTS)
    cols = np.concatenate(
        [t * width, np.full_like(t, width), (1 - t) * width, np.zeros_like(t)]
    )
    rows = np.concatenate(
        [np.zeros_like(t), t * height, np.full_like(t, height), (1 - t) * height]
    )
    xs, ys = transform * (cols, rows)
    lons, lats = planetocentricLonLat(crs, xs, ys)
    finite = np.isfinite(lons) & np.isfinite(lats)
    if not finite.any():
        raise ValueError("No point of the image's edge has a longitude and latitude.")
    lons = np.unwrap(lons[finite], period=360.0)
    lats = lats[finite]
    west, east = float(lons.min()), float(lons.max())
    south, north = float(lats.min()), float(lats.max())
    poles = _poles(crs, transform, width, height)
    if poles:
        west, east = -180.0, 180.0
    if "north" in poles:
        north = 90.0
    if "south" in poles:
        south = -90.0
    pole = "both" if len(poles) == 2 else (poles[0] if poles else None)
    return Footprint(
        west=west,
        south=south,
        east=east,
        north=north,
        center=((west + east) / 2, (south + north) / 2),
        pole=pole,
    )


def isGoodEnough(crs: CRS | str, footprint: Footprint) -> tuple[bool, str]:
    """Whether ``crs`` distorts at most ``SCALE_TIP`` (1%) in scale and
    ``ANGLE_TIP_DEG`` (1°) in angles over a 9 × 9 lon/lat grid on
    ``footprint`` (``distortionAt``), and why. Never raises."""
    try:
        samples = _distortions(crs, footprint)
    except ValueError as error:
        return False, f"its distortion can't be computed ({error})"
    if not samples:
        return False, "its distortion can't be computed over the scene"
    scale = max(sample.scaleError for sample in samples)
    angle = max(sample.angularDistortionDeg for sample in samples)
    if scale <= SCALE_TIP and angle <= ANGLE_TIP_DEG:
        return True, "under 1% scale error over the scene"
    projection = describeCrs(crs).projection.lower()
    if scale > SCALE_TIP:
        return False, f"its {projection} map has {scale * 100:.1f}% scale error here"
    return False, f"its {projection} map has {angle:.1f}° angular distortion here"


def recommendProjection(
    crs: CRS | str,
    footprint: Footprint,
    purpose: Purpose,
    existing: Sequence[CRS | str] = (),
) -> Recommendation:
    """The projection for ``footprint`` (of an image on ``crs``) and
    ``purpose``, following spec §4.6's rules; ``existing`` are the CRSs of
    all the inputs (a mosaic's). Raises ValueError for a CRS on no body."""
    surface, ellipsoidal = _surface(crs, existing)
    lon, lat = footprint.center
    if isGlobal(footprint):
        return _global(crs, footprint, purpose, surface)
    if footprint.pole is not None or abs(lat) >= POLAR_LATITUDE:
        south = footprint.pole == "south" or (footprint.pole is None and lat < 0)
        if purpose is Purpose.MOSAIC:
            scale = "+k=1"
        else:
            trueScale = _latitude(abs(lat), surface, ellipsoidal)
            scale = (
                f"+lat_ts={_number(_round(-trueScale if south else trueScale, 0.1))}"
            )
        where = (
            f"the scene contains the {footprint.pole} pole"
            if footprint.pole is not None
            else f"the scene reaches {abs(footprint.south if south else footprint.north):.1f}°{'S' if south else 'N'}"
        )
        return _made(
            f"+proj=stere +lat_0={-90 if south else 90} +lon_0=0 {scale}",
            surface,
            f"Polar stereographic: {where}",
        )
    if purpose is Purpose.MOSAIC:
        kept = _keptInputs(crs, footprint, existing)
        if kept is not None:
            return kept
        latTs = _round(_latitude(lat, surface, ellipsoidal), 1.0)
        return _made(
            f"+proj=eqc +lat_ts={_number(latTs)} +lat_0=0 +lon_0={_number(_round(_wrap(lon), 1.0))}",
            surface,
            "Equirectangular centered on the scene, rounded to 1° (the CTX, "
            "HiRISE and ISIS cam2map convention)",
        )
    with PROJ_LOCK:
        projected = CRS.from_user_input(crs).is_projected
    good, why = isGoodEnough(crs, footprint)
    if projected and good:
        return Recommendation(
            wkt=_wkt(crs),
            description=describeCrs(crs),
            reason=f"Kept its own projection: {why}",
            keptExisting=True,
        )
    if not projected:
        why = "its longitude–latitude pixels aren't square on the ground"
    center = (
        f"+lat_0={_number(_round(_latitude(lat, surface, ellipsoidal), 0.01))} "
        f"+lon_0={_number(_round(_wrap(lon), 0.01))}"
    )
    if _fitsTransverseMercator(footprint):
        return _made(
            f"+proj=tmerc {center} +k=1",
            surface,
            f"Transverse Mercator centered on the scene: {why}",
        )
    definition = f"+proj=laea {center}"
    samples = _distortions(_proj(definition, surface), footprint)
    shape = max((s.angularDistortionDeg for s in samples), default=math.nan)
    return _made(
        definition,
        surface,
        "Lambert azimuthal equal-area centered on the scene (too wide for "
        f"transverse Mercator): areas are exact, shapes are off by up to {shape:.1f}°",
    )


def isGlobal(footprint: Footprint) -> bool:
    """Whether the scene is global: it contains both poles, or spans at
    least 180° of latitude or (without a pole) 300° of longitude. No one
    projection has low distortion over it."""
    return (
        footprint.pole == "both"
        or footprint.north - footprint.south >= GLOBAL_LATITUDE_SPAN
        # a contained pole spans all longitudes without being global
        or (
            footprint.pole is None
            and footprint.east - footprint.west >= GLOBAL_LONGITUDE_SPAN
        )
    )


def _global(
    crs: CRS | str, footprint: Footprint, purpose: Purpose, surface: str
) -> Recommendation:
    """Equirectangular with ``lat_ts`` = 0 centered on the scene's longitude,
    or ``crs`` itself when it already is that."""
    definition = (
        f"+proj=eqc +lat_ts=0 +lat_0=0 "
        f"+lon_0={_number(_round(_wrap(footprint.center[0]), 1.0))}"
    )
    why = "the scene is global"
    if purpose is not Purpose.MOSAIC:
        why += (
            ", and no single projection has low distortion over it: measurements "
            "stay geodesic on the body; crop to the area of interest for a local "
            "projection"
        )
    if sameCrs(crs, _proj(definition, _ownSurface(crs)[0])):
        return Recommendation(
            wkt=_wkt(crs),
            description=describeCrs(crs),
            reason=f"Kept its own equirectangular projection: {why}",
            keptExisting=True,
        )
    return _made(definition, surface, f"Equirectangular centered on the scene: {why}")


def _round(value: float, step: float) -> float:
    """``value`` to a multiple of ``step``, halves away from zero; first to
    1e-9 steps, so a center computed as 77.49999999999 is 77.5."""
    steps = round(abs(value) / step, 9)
    return math.copysign(math.floor(steps + 0.5) * step, value)


def _number(value: float) -> str:
    return f"{value + 0.0:.12g}"  # + 0.0: never "-0"


def _wrap(lon: float) -> float:
    """A longitude in [−180, 180)."""
    return (lon + 180.0) % 360.0 - 180.0


def _poles(crs: CRS | str, transform: Affine, width: int, height: int) -> list[str]:
    """ "north" and "south", when that pole's map point is inside the image."""
    found: list[str] = []
    lonLat = eastLonLat(crs)
    for name, lat in (("north", 90.0), ("south", -90.0)):
        try:
            xs, ys = transformPoints(lonLat, crs, [0.0], [lat])
        except ValueError:
            continue
        col, row = ~transform * (float(xs[0]), float(ys[0]))
        if math.isfinite(col) and math.isfinite(row):
            if 0 <= col <= width and 0 <= row <= height:
                found.append(name)
    return found


def _surface(crs: CRS | str, existing: Sequence[CRS | str]) -> tuple[str, bool]:
    """The PROJ surface parameters (rule 4), and whether it's an ellipsoid."""
    body = identifyBody(crs).body
    if body is None:
        return _ownSurface(crs)
    if existing and all(_onReferenceEllipsoid(other, body) for other in existing):
        reference = body.referenceCrs().ellipsoid
        assert reference is not None
        return _parameters(reference.semi_major_metre, reference.semi_minor_metre)
    sphere = body.sphereCrs().ellipsoid
    assert sphere is not None
    return _parameters(sphere.semi_major_metre, sphere.semi_major_metre)


def _ownSurface(crs: CRS | str) -> tuple[str, bool]:
    """``crs``'s own surface as PROJ parameters, and whether it's an
    ellipsoid."""
    with PROJ_LOCK:
        ellipsoid = CRS.from_user_input(crs).ellipsoid
    if ellipsoid is None:
        raise ValueError(f"No surface to project on: {crs}")
    return _parameters(ellipsoid.semi_major_metre, ellipsoid.semi_minor_metre)


def _parameters(a: float, b: float) -> tuple[str, bool]:
    if abs(a - b) <= RADIUS_TOLERANCE_M:
        return f"+R={_number(a)}", False
    return f"+a={_number(a)} +b={_number(b)}", True


def _onReferenceEllipsoid(crs: CRS | str, body: Body) -> bool:
    if identifyBody(crs).body != body:
        return False
    reference = body.referenceCrs().ellipsoid
    with PROJ_LOCK:
        ellipsoid = CRS.from_user_input(crs).ellipsoid
    return _sameEllipsoid(ellipsoid, reference)


def _sameEllipsoid(a: Ellipsoid | None, b: Ellipsoid | None) -> bool:
    if a is None or b is None:
        return False
    return (
        abs(a.semi_major_metre - b.semi_major_metre) <= RADIUS_TOLERANCE_M
        and abs(a.semi_minor_metre - b.semi_minor_metre) <= RADIUS_TOLERANCE_M
    )


def _latitude(centric: float, surface: str, ellipsoidal: bool) -> float:
    """A planetocentric latitude as PROJ's string reads it: geodetic
    (planetographic) on an ellipsoid, the same on a sphere."""
    if not ellipsoidal:
        return centric
    a, b = (float(part.split("=")[1]) for part in surface.split())
    return float(ographicLatitude(centric, a, b))


def _keptInputs(
    crs: CRS | str, footprint: Footprint, existing: Sequence[CRS | str]
) -> Recommendation | None:
    """The inputs' one CRS, when they all share it, it's on ``crs``'s body
    and it's good enough over the scene (rule 2)."""
    if not existing:
        return None
    first = existing[0]
    if not all(sameCrs(first, other) for other in existing[1:]):
        return None
    # the same body; an unknown body only with the same radii (Phase 1's rule)
    if compatibility(first, crs) not in _SAME_BODY:
        return None
    good, why = isGoodEnough(first, footprint)
    if not good:
        return None
    return Recommendation(
        wkt=_wkt(first),
        description=describeCrs(first),
        reason=f"Kept the inputs' projection: {why}",
        keptExisting=True,
    )


def _fitsTransverseMercator(footprint: Footprint) -> bool:
    """Every point of the footprint within ``TM_HALF_WIDTH`` of the central
    meridian: B = arcsin(cos φ |sin Δλ|) (Snyder, 1987, eq. 8-1), on a
    21 × 21 grid over the box plus the equator when the box spans it (where
    cos φ is largest)."""
    if footprint.east - footprint.west >= 180.0:
        return False
    latitudes = np.linspace(footprint.south, footprint.north, _EDGE_POINTS)
    if footprint.south < 0 < footprint.north:
        latitudes = np.append(latitudes, 0.0)
    lons, lats = np.meshgrid(
        np.linspace(footprint.west, footprint.east, _EDGE_POINTS), latitudes
    )
    deltas = np.radians(lons - footprint.center[0])
    angles = np.arcsin(np.cos(np.radians(lats)) * np.abs(np.sin(deltas)))
    return bool(angles.max() <= TM_HALF_WIDTH)


def _distortions(crs: CRS | str, footprint: Footprint) -> list[Distortion]:
    """``distortionAt`` on a 9 × 9 lon/lat grid over ``footprint``, converted
    to ``crs``'s own longitude direction and latitude type; points PROJ can't
    invert are left out."""
    lons, lats = np.meshgrid(
        np.linspace(footprint.west, footprint.east, _GRID),
        np.linspace(footprint.south, footprint.north, _GRID),
    )
    lons, lats = lons.ravel(), lats.ravel()
    with PROJ_LOCK:
        parsed = CRS.from_user_input(crs)
        geodetic = parsed.geodetic_crs
        if geodetic is None or parsed.ellipsoid is None:
            raise ValueError(f"{parsed.name} is on no body")
        # planetocentric → the CRS's own numbers (planetocentric or not)
        centricLonLat = eastLonLat(parsed)
        ownLats = _ownLatitudes(parsed, lats)
        xs, ys = transformPoints(centricLonLat, geodetic, lons, ownLats)
    samples = [
        distortionAt(parsed, float(x), float(y)) for x, y in zip(xs, ys, strict=True)
    ]
    return [
        s
        for s in samples
        if math.isfinite(s.scaleError) and math.isfinite(s.angularDistortionDeg)
    ]


def _ownLatitudes(crs: CRS, centric: np.ndarray) -> np.ndarray:
    """Planetocentric latitudes in ``crs``'s latitude type."""
    ellipsoid = crs.ellipsoid
    assert ellipsoid is not None
    if latitudeType(crs) == "planetocentric":
        return centric
    return ographicLatitude(
        centric, ellipsoid.semi_major_metre, ellipsoid.semi_minor_metre
    )


def _proj(definition: str, surface: str) -> str:
    return f"{definition} {surface} +units=m +no_defs"


def _wkt(crs: CRS | str) -> str:
    with PROJ_LOCK:
        return CRS.from_user_input(crs).to_wkt()


def _made(definition: str, surface: str, reason: str) -> Recommendation:
    wkt = _wkt(_proj(definition, surface))
    return Recommendation(
        wkt=wkt, description=describeCrs(wkt), reason=reason, keptExisting=False
    )
