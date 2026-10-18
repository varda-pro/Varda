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

from functools import lru_cache

import numpy as np
import numpy.typing as npt
from attrs import frozen
from pyproj import CRS, Transformer

from varda.geo.describe import describeCrs
from varda.geo.equivalence import canTransform, sameCrs
from varda.utilities.proj_lock import PROJ_LOCK


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

    When both share one surface (or PROJ knows a real transformation, such
    as Earth's datum shifts) this is PROJ's own operation. Between different
    surfaces of one body, where PROJ has only a ballpark:

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


@frozen
class _Side:
    """One CRS's part of the route."""

    # between the CRS and longitude east, geodetic latitude on its datum
    # (from the CRS for the source, to it for the target), in the CRS's own
    # axis order: PROJ's always_xy leaves a west-positive or spherical
    # (planetocentric) CRS latitude first
    plain: Transformer
    ratio: float  # (c/a)² of its ellipsoid; 1 on a sphere
    # the numbers PROJ reads as geodetic latitude are planetocentric (an
    # ellipsoidal coordinate system whose latitude is named planetocentric)
    centricNumbers: bool
    latitudeFirst: bool  # its own axis order is latitude, longitude


@frozen
class _Route:
    src: _Side
    dst: _Side
    # PROJ's own operation, when it is exact or a real transformation (the
    # same surface, or Earth's datum shifts); None when PROJ has only a
    # ballpark between different surfaces, which goes through the sides
    direct: Transformer | None


def _key(crs: CRS | str) -> str:
    """The caller holds PROJ_LOCK."""
    return crs if isinstance(crs, str) else CRS.from_user_input(crs).srs


@lru_cache(maxsize=64)
def _route(srcKey: str, dstKey: str) -> _Route:
    """The transformers for ``transformPoints`` (they take milliseconds to
    build), used only inside PROJ_LOCK; the caller holds it."""
    src, dst = CRS.from_user_input(srcKey), CRS.from_user_input(dstKey)
    direct = Transformer.from_crs(src, dst)
    if not _sameSurface(src, dst) and "ballpark" in direct.description.lower():
        direct = None
    return _Route(
        _side(srcKey, src, Transformer.from_crs(src, _plain(src))),
        _side(dstKey, dst, Transformer.from_crs(_plain(dst), dst)),
        direct,
    )


def _side(key: str, crs: CRS, plain: Transformer) -> _Side:
    """The latitude type as ``describeCrs`` reads it; PROJ converts its
    spherical (planetocentric) coordinate systems to geodetic itself."""
    ellipsoid = crs.ellipsoid
    assert ellipsoid is not None  # canTransform found the body
    centric = describeCrs(key).latitudeType == "planetocentric"
    return _Side(
        plain,
        (ellipsoid.semi_minor_metre / ellipsoid.semi_major_metre) ** 2,
        centric and _subtype(crs) != "spherical",
        _latitudeFirst(crs),
    )


def _sameSurface(a: CRS, b: CRS) -> bool:
    ellipsoidA, ellipsoidB = a.ellipsoid, b.ellipsoid
    assert ellipsoidA is not None and ellipsoidB is not None
    return (
        ellipsoidA.semi_major_metre == ellipsoidB.semi_major_metre
        and ellipsoidA.semi_minor_metre == ellipsoidB.semi_minor_metre
    )


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
