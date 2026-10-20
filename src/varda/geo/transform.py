"""Moving coordinates between two coordinate systems of one body, keeping
each position the same direction from the body's center.

PROJ has no transformation between two models of one body (two spheres, a
sphere and an ellipsoid), so it uses a "ballpark geographic offset" that
keeps the number of the geodetic latitude. Between a sphere and a flattened
ellipsoid that misreads the latitude: a planetocentric 45° on the Mars
sphere becomes 44.66° in ``IAU_2015:49902`` (planetocentric) and stays 45°
in ``IAU_2015:49901`` (planetographic, where it should be 45.34°), about
20 km off. ``transformPoints`` keeps the planetocentric longitude and
latitude instead (Archinal et al., 2018: planetocentric coordinates are
the direction from the body's center)."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

import numpy as np
import numpy.typing as npt
from affine import Affine
from attrs import frozen
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError

from varda.geo.bodies import latitudeType
from varda.geo.equivalence import canTransform, sameCrs
from varda.utilities.proj_lock import PROJ_LOCK

# points per image edge for transformBounds
_EDGE_POINTS = 21


def transformPoints(
    src: CRS | str | None,
    dst: CRS | str | None,
    xs: npt.ArrayLike,
    ys: npt.ArrayLike,
) -> tuple[np.ndarray, np.ndarray]:
    """Points (``xs``, ``ys``; x = easting or longitude, y = northing or
    latitude, in the CRS's units and axis directions, whatever its axis
    order; a west-positive longitude stays west-positive) on ``src`` as
    points on ``dst``, the same direction from the body's center (the same
    planetocentric longitude and latitude).

    When both share one surface (or PROJ knows a real transformation
    between registered datums, such as Earth's datum shifts) this is PROJ's
    own operation. Between other surfaces of one body, where PROJ only keeps
    the latitude's number (its ballpark, or what it calls "Transformation
    from unknown to unknown" for ``+lon_wrap`` or ``+over``):

    1. ``src`` → longitude (east) and geodetic latitude on ``src``'s own
       datum, with PROJ (an exact conversion: projection, axes, west-positive
       longitude, PROJ's planetocentric CRSs).
    2. Geodetic → planetocentric on ``src``'s ellipsoid, tan φc = (c/a)²
       tan φg (Snyder, 1987, eq. 3-28); on a sphere they're equal.
    3. Planetocentric → geodetic on ``dst``'s ellipsoid, tan φg = (a/c)²
       tan φc.
    4. → ``dst`` with PROJ.

    The latitude type is read from the CRS as ``describeCrs`` reads it: a
    flattened ellipsoid whose latitude axis is named planetocentric but
    whose coordinate system PROJ treats as geodetic holds planetocentric
    numbers, so steps 2 or 3 are skipped for it.

    Equivalent CRSs (``sameCrs``) return the points unchanged. Different
    bodies, a body Varda can't tell is the same, or a missing CRS raise
    ValueError with ``canTransform``'s reason, naming both. Holds PROJ_LOCK;
    no Qt."""
    x = np.asarray(xs, dtype=np.float64).copy()
    y = np.asarray(ys, dtype=np.float64).copy()
    reason = canTransform(src, dst)
    if reason is not None:
        raise ValueError(reason)
    if sameCrs(src, dst):
        return x, y
    assert src is not None and dst is not None  # canTransform said why not
    with PROJ_LOCK:
        route = _route(_key(src), _key(dst))
        native = (y, x) if route.src.latitudeFirst else (x, y)
        # lists, not arrays: pyproj takes a one-element array for a scalar,
        # which NumPy deprecates
        if route.direct is not None:
            out = route.direct.transform(*(v.tolist() for v in native))
        else:
            lon, lat = (
                np.asarray(v, dtype=np.float64)
                for v in route.src.plain.transform(*(v.tolist() for v in native))
            )
            if not route.src.centricNumbers:
                lat = _tanScaled(lat, route.src.ratio)  # → planetocentric
            if not route.dst.centricNumbers:
                lat = _tanScaled(lat, 1 / route.dst.ratio)  # → geodetic
            out = route.dst.plain.transform(lon.tolist(), lat.tolist())
    first, second = (np.asarray(v, dtype=np.float64) for v in out)
    return (second, first) if route.dst.latitudeFirst else (first, second)


def warpOperation(src: CRS | str, dst: CRS | str) -> str | None:
    """The PROJ pipeline that moves rasters from ``src`` to ``dst`` as
    ``transformPoints`` moves points, for GDAL's warper (its
    ``COORDINATE_OPERATION`` option), in the CRSs' own axis order; None when
    PROJ's own operation is exact (equivalent CRSs, one surface, or a real
    transformation such as Earth's datum shifts) and neither CRS has a
    planetocentric latitude on a flattened ellipsoid (e.g.
    ``IAU_2015:49902``: GDAL's warper reads its numbers as geodetic, about
    20 km off at 45° on Mars, though PROJ's point operation is exact).
    Between surfaces of one body, where PROJ has only its ballpark, and with
    such a CRS on either side, its steps are:

    1. ``src`` → longitude east and geodetic latitude in degrees (PROJ's own
       steps, ``transformPoints``' step 1);
    2. degrees → radians;
    3. ``+proj=geoc`` on ``src``'s ellipsoid (geodetic → planetocentric),
       left out when its latitude numbers are already planetocentric;
    4. ``+inv +proj=geoc`` on ``dst``'s ellipsoid, left out likewise;
    5. radians → degrees;
    6. → ``dst`` (PROJ's own steps).

    On a sphere the geoc steps change nothing. GDAL itself needs the CRSs
    x first: use ``gdalWarp``, which adds the axis swaps. Raises ValueError with
    ``canTransform``'s reason (e.g. different bodies). Holds PROJ_LOCK; no
    Qt."""
    reason = canTransform(src, dst)
    if reason is not None:
        raise ValueError(reason)
    if sameCrs(src, dst):
        return None
    with PROJ_LOCK:
        steps = _warpSteps(_key(src), _key(dst))
    return None if steps is None else _pipeline(steps)


def warpOptions(src: CRS | str, dst: CRS | str) -> dict[str, str]:
    """GDAL warper options that place a raster from ``src`` on ``dst`` as
    ``transformPoints`` places points (``warpOperation``): pass them to
    ``rasterio.warp.reproject``. Empty when PROJ's own operation is exact.
    Raises ValueError as ``warpOperation`` does."""
    operation = warpOperation(src, dst)
    return {} if operation is None else {"COORDINATE_OPERATION": operation}


@frozen(eq=False)
class GdalWarp:
    """What GDAL's warper gets to place a raster from one CRS on another as
    ``transformPoints`` places points (``gdalWarp``)."""

    srcCrs: str  # WKT, x axis (easting or longitude) first
    dstCrs: str  # WKT, x axis first
    # GDAL warper options: the COORDINATE_OPERATION pipeline when PROJ's own
    # operation isn't exact; empty otherwise
    options: dict[str, str]
    # the source is a west-positive geographic CRS, given to GDAL as its
    # east-positive twin: its x (longitude) is negated (``sourceTransform``)
    srcNegated: bool = False

    @property
    def operation(self) -> str:
        """The pipeline GDAL runs (in x-first axis order), or "PROJ"."""
        return self.options.get("COORDINATE_OPERATION", "PROJ")

    def sourceTransform(self, transform: Affine) -> Affine:
        """The source's pixel-to-map transform for GDAL, in ``srcCrs``'s
        units: x negated for a west-positive geographic source."""
        return Affine.scale(-1.0, 1.0) * transform if self.srcNegated else transform


def gdalWarp(src: CRS | str, dst: CRS | str) -> GdalWarp:
    """The CRSs and options for ``rasterio.warp.reproject`` (or
    ``transform_bounds``) from ``src`` to ``dst``. Varda's x is always the
    easting or longitude, but GDAL's traditional GIS axis order leaves some
    CRSs latitude first (the IAU_2015 ellipsoids, e.g. ``IAU_2015:49901``
    and ``IAU_2015:49902``), so such a CRS is given to GDAL with its axes
    swapped (and no authority code, from which GDAL would restore the
    order), and the pipeline (``warpOperation``) gets axis swaps at its
    ends.

    GDAL's warper loses a west-positive geographic source (e.g.
    ``IAU_2015:49901``) wherever its west longitude plus its latitude passes
    about 180° (east 135–180° at 45°N, and across ±180°), so such a source
    is given to GDAL as its east-positive twin, with x negated
    (``GdalWarp.sourceTransform``) and the pipeline starting with the
    negation's inverse. Raises ValueError as ``warpOperation`` does. Holds
    PROJ_LOCK."""
    reason = canTransform(src, dst)
    if reason is not None:
        raise ValueError(reason)
    with PROJ_LOCK:
        srcKey, dstKey = _key(src), _key(dst)
        srcWkt, srcSwapped, srcNegated = _xFirst(srcKey, eastward=True)
        dstWkt, dstSwapped, _ = _xFirst(dstKey)
        steps = None if sameCrs(src, dst) else _warpSteps(srcKey, dstKey)
    if steps is None:
        return GdalWarp(srcWkt, dstWkt, {}, srcNegated)
    swap = ["proj=axisswap order=2,1"]
    negate = ["proj=axisswap order=-1,2"]  # GDAL's east x → the CRS's west x
    steps = [
        *(negate if srcNegated else []),
        *(swap if srcSwapped else []),
        *steps,
        *(swap if dstSwapped else []),
    ]
    return GdalWarp(
        srcWkt, dstWkt, {"COORDINATE_OPERATION": _pipeline(steps)}, srcNegated
    )


@lru_cache(maxsize=64)
def _xFirst(key: str, eastward: bool = False) -> tuple[str, bool, bool]:
    """The CRS as WKT with its x axis first, whether its axes were swapped,
    and (with ``eastward``) whether a west-positive geographic CRS's
    longitude axis was turned east (so x must be negated). The caller holds
    PROJ_LOCK."""
    crs = CRS.from_user_input(key)
    swapped = _latitudeFirst(crs)
    negated = (
        eastward
        and not crs.is_projected
        and any(axis.direction == "west" for axis in crs.axis_info)
    )
    if not swapped and not negated:
        return crs.to_wkt(), False, False
    definition = crs.to_json_dict()
    definition.pop("id", None)
    definition.pop("ids", None)
    axes = definition["coordinate_system"]["axis"]
    if swapped:
        axes.reverse()
    if negated:
        for axis in axes:
            if axis["direction"] == "west":
                axis["direction"] = "east"
    return CRS.from_json_dict(definition).to_wkt(), swapped, negated


def _pipeline(steps: Sequence[str]) -> str:
    return " ".join(
        ["+proj=pipeline"]
        + [" ".join(["+step", *(f"+{word}" for word in s.split())]) for s in steps]
    )


@lru_cache(maxsize=64)
def _warpSteps(srcKey: str, dstKey: str) -> tuple[str, ...] | None:
    """The steps of ``warpOperation``'s pipeline, or None. The caller holds
    PROJ_LOCK."""
    route = _route(srcKey, dstKey)
    if route.direct is not None:
        return None
    steps = [*_steps(route.src.plain), "proj=unitconvert xy_in=deg xy_out=rad"]
    for side, inverse in ((route.src, ""), (route.dst, "inv ")):
        if not side.centricNumbers:
            steps.append(
                f"{inverse}proj=geoc a={side.semiMajor!r} b={side.semiMinor!r}"
            )
    steps += ["proj=unitconvert xy_in=rad xy_out=deg", *_steps(route.dst.plain)]
    return tuple(steps)


def isGeographic(crs: CRS | str) -> bool:
    """Angles on a body: a geographic CRS, or another geodetic one such as
    PROJ's planetocentric ellipsoids (``IAU_2015:49902``, a spherical
    coordinate system, which pyproj's ``is_geographic`` calls not
    geographic), but not geocentric x, y, z. Holds PROJ_LOCK."""
    with PROJ_LOCK:
        parsed = crs if isinstance(crs, CRS) else CRS.from_user_input(crs)
        return (
            not parsed.is_projected
            and parsed.geodetic_crs is not None
            and not parsed.is_geocentric
        )


def transformBounds(
    src: CRS | str, dst: CRS | str, transform: Affine, width: int, height: int
) -> tuple[float, float, float, float]:
    """The (west, south, east, north) bounds, in ``dst``'s x and y, of an
    image of ``width`` × ``height`` pixels with ``transform`` on ``src``:
    its edges, 21 points each, moved with ``transformPoints`` (so x is
    always the easting or longitude). On a geographic ``dst`` the longitudes
    are unwrapped along the edge, so a footprint across ±180° stays
    contiguous (east may pass 180°). Raises ValueError across bodies or
    when no edge point can be moved."""
    t = np.linspace(0.0, 1.0, _EDGE_POINTS)
    cols = np.concatenate(
        [t * width, np.full_like(t, width), (1 - t) * width, np.zeros_like(t)]
    )
    rows = np.concatenate(
        [np.zeros_like(t), t * height, np.full_like(t, height), (1 - t) * height]
    )
    xs, ys = transformPoints(src, dst, *(transform * (cols, rows)))
    finite = np.isfinite(xs) & np.isfinite(ys)
    if not finite.any():
        raise ValueError("No point of the image's edge can be moved.")
    xs, ys = xs[finite], ys[finite]
    period = longitudePeriod(dst)
    if period is not None:
        xs = np.unwrap(xs, period=period)
    return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())


def longitudePeriod(crs: CRS | str) -> float | None:
    """A whole turn of longitude in a geographic CRS's (``isGeographic``)
    angle unit; None for a projected CRS. Holds PROJ_LOCK."""
    if not isGeographic(crs):
        return None
    with PROJ_LOCK:
        parsed = crs if isinstance(crs, CRS) else CRS.from_user_input(crs)
        return 360.0 / np.degrees(parsed.axis_info[0].unit_conversion_factor)


def _steps(transformer: Transformer) -> list[str]:
    """The steps of ``transformer``'s PROJ definition, without the
    ``proj=pipeline`` head (one step when it isn't a pipeline)."""
    parts = transformer.definition.split(" step ")
    if parts[0].strip() == "proj=pipeline":
        return parts[1:]
    if parts[0].startswith("proj=pipeline "):
        raise ValueError(f"Unexpected PROJ pipeline options: {parts[0]}")
    return parts


def planetocentricLonLat(
    crs: CRS | str, xs: npt.ArrayLike, ys: npt.ArrayLike
) -> tuple[np.ndarray, np.ndarray]:
    """Points (``xs``, ``ys`` as in ``transformPoints``) on ``crs`` as
    longitude east and planetocentric latitude, degrees, on the CRS's own
    surface (steps 1 and 2 of ``transformPoints``). NaN or inf where PROJ
    can't invert. Raises ValueError for a CRS on no body surface (a local
    CRS) or one PROJ can't read. Holds PROJ_LOCK; no Qt."""
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    with PROJ_LOCK:
        key = _key(crs)
        try:
            parsed = CRS.from_user_input(key)
        except CRSError as error:
            raise ValueError(f"Unreadable coordinate system: {error}") from error
        if parsed.ellipsoid is None or parsed.geodetic_crs is None:
            raise ValueError(f"{parsed.name} is on no body (no ellipsoid)")
        side = _source(key)
        first, second = (y, x) if side.latitudeFirst else (x, y)
        out = side.plain.transform(first.ravel().tolist(), second.ravel().tolist())
    lon, lat = (np.asarray(v, dtype=np.float64).reshape(x.shape) for v in out)
    if not side.centricNumbers:
        lat = _tanScaled(lat, side.ratio)  # → planetocentric
    return lon, lat


def ographicLatitude(centric: npt.ArrayLike, a: float, c: float) -> np.ndarray:
    """Planetocentric latitude (degrees) as geodetic (planetographic)
    latitude on an ellipsoid of radii ``a``, ``c``: tan φg = (a/c)² tan φc
    (Snyder, 1987, eq. 3-28); unchanged on a sphere."""
    return _tanScaled(np.asarray(centric, dtype=np.float64), (a / c) ** 2)


def eastLonLat(crs: CRS | str) -> CRS:
    """``crs``'s own geographic CRS (its datum, latitude type and units)
    with longitude first and east-positive: how GeoJSON and most planetary
    tools write degrees on a body. ``transformPoints`` from it reads such
    degrees on ``crs``. Raises ValueError for a CRS on no body. Holds
    PROJ_LOCK."""
    with PROJ_LOCK:
        geodetic = CRS.from_user_input(crs).geodetic_crs
        if geodetic is None:
            raise ValueError(f"No longitude and latitude on this CRS: {crs}")
        definition = geodetic.to_json_dict()
        definition.pop("id", None)
        definition.pop("ids", None)
        axes = definition["coordinate_system"]["axis"]
        longitude = next(a for a in axes if a["direction"] in ("east", "west"))
        latitude = next(a for a in axes if a["direction"] in ("north", "south"))
        definition["coordinate_system"]["axis"] = [
            {**longitude, "direction": "east"},
            latitude,
        ]
        definition["name"] = f"{geodetic.name} (longitude east first)"
        return CRS.from_json_dict(definition)


def geodeticLonLat(crs: CRS | str) -> CRS:
    """Longitude east and geodetic (planetographic) latitude, in degrees, on
    ``crs``'s own datum and prime meridian: what a PROJ projection string
    takes. Holds PROJ_LOCK."""
    with PROJ_LOCK:
        return _plain(CRS.from_user_input(crs))


@frozen
class _Side:
    """One CRS's part of the route."""

    # between the CRS and longitude east, geodetic latitude on its datum
    # (from the CRS for the source, to it for the target), in the CRS's own
    # axis order: PROJ's always_xy leaves a west-positive or spherical
    # (planetocentric) CRS latitude first
    plain: Transformer
    ratio: float  # (c/a)² of its ellipsoid; 1 on a sphere
    semiMajor: float  # a, meters
    semiMinor: float  # c, meters
    # the numbers PROJ reads as geodetic latitude are planetocentric (an
    # ellipsoidal coordinate system whose latitude is named planetocentric)
    centricNumbers: bool
    latitudeFirst: bool  # its own axis order is latitude, longitude


@frozen
class _Route:
    src: _Side
    dst: _Side
    # PROJ's own operation, when it is exact or a real transformation (the
    # same surface, or Earth's datum shifts between registered datums); None
    # between different surfaces otherwise, which go through the sides
    direct: Transformer | None


def _key(crs: CRS | str) -> str:
    """The caller holds PROJ_LOCK."""
    return crs if isinstance(crs, str) else CRS.from_user_input(crs).srs


@lru_cache(maxsize=64)
def _route(srcKey: str, dstKey: str) -> _Route:
    """The transformers for ``transformPoints`` (they take milliseconds to
    build), used only inside PROJ_LOCK; the caller holds it."""
    src, dst = CRS.from_user_input(srcKey), CRS.from_user_input(dstKey)
    try:
        direct = Transformer.from_crs(src, dst)
    except ProjError:
        # PROJ can't build one between a spherical planetocentric CRS
        # (IAU_2015:49902) and a PROJ string with +lon_wrap or +over
        # ("exportToPROJString() not supported"); each side builds alone
        direct = None
    if direct is not None and not _sameSurface(src, dst):
        if not _datumShift(src, dst, direct):
            direct = None
    elif _centricOnEllipsoid(src) or _centricOnEllipsoid(dst):
        # PROJ's own operation is exact for points, but GDAL's warper reads
        # a planetocentric latitude's numbers as geodetic (0.34° off at 45°
        # on Mars), so the latitude always goes through the explicit steps
        direct = None
    return _Route(
        _source(srcKey), _side(dst, Transformer.from_crs(_plain(dst), dst)), direct
    )


@lru_cache(maxsize=64)
def _source(key: str) -> _Side:
    """The side reading points on the CRS ``key``; the caller holds
    PROJ_LOCK."""
    crs = CRS.from_user_input(key)
    return _side(crs, Transformer.from_crs(crs, _plain(crs)))


def _side(crs: CRS, plain: Transformer) -> _Side:
    """The latitude type as ``describeCrs`` reads it (``latitudeType``); PROJ converts its
    spherical (planetocentric) coordinate systems to geodetic itself."""
    ellipsoid = crs.ellipsoid
    assert ellipsoid is not None  # canTransform found the body
    centric = latitudeType(crs) == "planetocentric"
    return _Side(
        plain,
        (ellipsoid.semi_minor_metre / ellipsoid.semi_major_metre) ** 2,
        ellipsoid.semi_major_metre,
        ellipsoid.semi_minor_metre,
        centric and _subtype(crs) != "spherical",
        _latitudeFirst(crs),
    )


def _centricOnEllipsoid(crs: CRS) -> bool:
    """Planetocentric latitude on a flattened ellipsoid (where it differs
    from geodetic latitude), e.g. ``IAU_2015:49902``."""
    ellipsoid = crs.ellipsoid
    return (
        ellipsoid is not None
        and abs(ellipsoid.semi_major_metre - ellipsoid.semi_minor_metre) > 1e-3
        and latitudeType(crs) == "planetocentric"
    )


def _sameSurface(a: CRS, b: CRS) -> bool:
    """The same ellipsoid (or sphere), to 1 mm in each radius. Decided from
    the CRSs, never from PROJ's description of its operation: PROJ names the
    latitude-keeping operation between PROJ strings with ``+lon_wrap`` or
    ``+over`` "Transformation from unknown to unknown", not a ballpark."""
    ellipsoidA, ellipsoidB = a.ellipsoid, b.ellipsoid
    assert ellipsoidA is not None and ellipsoidB is not None
    return (
        abs(ellipsoidA.semi_major_metre - ellipsoidB.semi_major_metre) <= 1e-3
        and abs(ellipsoidA.semi_minor_metre - ellipsoidB.semi_minor_metre) <= 1e-3
    )


def _datumShift(a: CRS, b: CRS, operation: Transformer) -> bool:
    """Whether ``operation`` is a real transformation between two different
    surfaces (Earth's datum shifts, e.g. NAD27 → WGS 84): both datums are
    registered with an authority (EPSG, IAU) and PROJ found more than a
    ballpark between them. Otherwise PROJ only keeps the latitude's number,
    whatever it calls the operation."""
    registered = all(_registeredDatum(crs) for crs in (a, b))
    return registered and "ballpark" not in operation.description.lower()


def _registeredDatum(crs: CRS) -> bool:
    """Whether the CRS's datum is an authority's: the datum carries an id,
    or its geodetic CRS does. WKT drops a datum ensemble's id (WGS 84's,
    so ``CRS.from_wkt(CRS("EPSG:4326").to_wkt())``'s datum has none) and,
    under a projected or bound CRS, the datum's, but keeps the geodetic
    CRS's."""
    if crs.datum is None:
        return False
    if crs.datum.to_json_dict().get("id") is not None:
        return True
    geodetic = crs.geodetic_crs
    return geodetic is not None and geodetic.to_json_dict().get("id") is not None


def _subtype(crs: CRS) -> str | None:
    geodetic = crs.geodetic_crs
    system = geodetic.coordinate_system if geodetic is not None else None
    return system.to_json_dict().get("subtype") if system is not None else None


def _latitudeFirst(crs: CRS) -> bool:
    """Whether the CRS's first axis is north–south and its second east–west
    (Varda's x is always the east–west one, in the axis's own direction)."""
    axes = crs.axis_info
    return (
        len(axes) >= 2
        and axes[0].direction in ("north", "south")
        and axes[1].direction in ("east", "west")
    )


def _plain(crs: CRS) -> CRS:
    """Longitude east and geodetic latitude, in degrees, on ``crs``'s own
    datum and prime meridian."""
    geodetic = crs.geodetic_crs
    assert geodetic is not None  # canTransform found the body
    definition = geodetic.to_json_dict()
    definition.pop("id", None)
    definition.pop("ids", None)
    definition["type"] = "GeographicCRS"
    definition["name"] = f"{geodetic.name} (longitude east, geodetic latitude)"
    definition["coordinate_system"] = {
        "subtype": "ellipsoidal",
        "axis": [
            {
                "name": "Longitude",
                "abbreviation": "lon",
                "direction": "east",
                "unit": "degree",
            },
            {
                "name": "Latitude",
                "abbreviation": "lat",
                "direction": "north",
                "unit": "degree",
            },
        ],
    }
    return CRS.from_json_dict(definition)


def _tanScaled(latitude: np.ndarray, ratio: float) -> np.ndarray:
    """tan φ' = ratio · tan φ, keeping the quadrant (±90° stay ±90°):
    geodetic → planetocentric with ratio (c/a)², the inverse with (a/c)²."""
    radians = np.radians(latitude)
    return np.degrees(np.arctan2(ratio * np.sin(radians), np.cos(radians)))
