"""How much a projection distorts scale and angles at a point and, at worst,
over an image, for the tip-off that planar shapes, slopes and map-unit
measurements are off there.

Projected CRSs: PROJ's scale factors (``Proj.get_factors``; PROJ docs,
*Factors*; Snyder, 1987, ch. 4): the meridional scale h, the parallel scale
k, the areal scale and the maximum angular distortion ω.

Geographic CRSs: Varda shows the pixels square, so the image is an
equirectangular map whose shape is true where the pixel is square on the
ground. With h = 1, k is the ground length of a degree north–south over that
of a degree east–west, times the pixel aspect: k = (Δφ_px/Δλ_px) · M / (N cos φ)
(M and N the meridional and prime-vertical radii of curvature, equal on a
sphere; Snyder, 1987, eqs. 4-18, 4-20). For square-degree pixels on a sphere
this is Snyder's equirectangular k = cos φ1 / cos φ with φ1 = 0 (p. 91), and
for pixels of Δλ = Δφ / cos φ1 it is the same with standard parallel φ1. The
meridians and parallels stay perpendicular, so the areal scale is h·k and
sin(ω/2) = |h − k| / (h + k) (Snyder, 1987, eq. 4-9 with a', b' = h, k).

φ above is geodetic (planetographic) latitude. A planetocentric latitude φc
on a flattened ellipsoid (e.g. ``IAU_2015:49902``) is converted with
tan φg = (a/b)² tan φc, and a degree of φc spans dφg/dφc degrees of φg, so
M is multiplied by dφg/dφc = (a/b)² / (cos² φc + (a/b)⁴ sin² φc). Projected
CRSs take and report latitude in their geodetic CRS's own convention too;
PROJ's factors are evaluated at the converted planetographic latitude."""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from affine import Affine
from attrs import frozen
from pyproj import CRS, Proj
from pyproj.exceptions import CRSError

from varda.utilities.proj_lock import PROJ_LOCK

SCALE_TIP: float = 0.01
ANGLE_TIP_DEG: float = 1.0
SCALE_STRONG: float = 0.10
ANGLE_STRONG_DEG: float = 10.0
# samples per side of the grid over an image (spec §4.5)
_GRID = 9


@frozen
class Distortion:
    """A projection's scale factors and angular distortion at one point."""

    meridionalScale: float  # h
    parallelScale: float  # k
    arealScale: float
    angularDistortionDeg: float  # ω

    @property
    def scaleError(self) -> float:
        return max(abs(self.meridionalScale - 1), abs(self.parallelScale - 1))

    @property
    def level(self) -> str:
        """ "ok", "tip" (> 1% or > 1°) or "strong" (> 10% or > 10°)."""
        if (
            self.scaleError > SCALE_STRONG
            or self.angularDistortionDeg > ANGLE_STRONG_DEG
        ):
            return "strong"
        if self.scaleError > SCALE_TIP or self.angularDistortionDeg > ANGLE_TIP_DEG:
            return "tip"
        return "ok"

    @property
    def _severity(self) -> float:
        return max(
            self.scaleError / SCALE_TIP, self.angularDistortionDeg / ANGLE_TIP_DEG
        )


@frozen
class DistortionSummary:
    """The worst distortion over an image (or part of it), and where."""

    worst: Distortion
    where: tuple[float, float]  # lon, lat of the worst sample
    text: str  # "up to 5.3% scale error (east–west) at 18.4°N"


@frozen(eq=False)
class _Model:
    """How to compute the distortion of one CRS; used only in PROJ_LOCK."""

    proj: Proj | None  # projected; None for geographic
    degreesPerUnit: float  # geographic: the angle unit in degrees
    eccentricitySquared: float  # the ellipsoid's e²
    west: bool  # the geodetic CRS's longitude is positive west
    westing: bool  # projected, its x counts west (Proj takes an easting)
    # the geodetic CRS's latitude is planetocentric (on a flattened
    # ellipsoid; PROJ's own latitude is always geodetic, i.e. planetographic)
    planetocentric: bool


def distortionAt(crs: CRS | str, lon: float, lat: float) -> Distortion:
    """The distortion at ``lon``, ``lat`` (degrees in ``crs.geodetic_crs``);
    for a geographic CRS, of square-degree pixels. Raises ValueError for a
    CRS on no body or one PROJ can't read."""
    with PROJ_LOCK:
        model = _model(_key(crs))
        if model is None:
            raise ValueError(f"No distortion for this coordinate system: {crs}")
        lons = np.array([-lon if model.west else lon], dtype=float)
        found = _factors(model, lons, np.array([lat], dtype=float), 1.0)
    return found[0]


def distortionAtPixels(
    crs: CRS | str, transform: Affine, cols: np.ndarray, rows: np.ndarray
) -> list[Distortion]:
    """The distortion at the pixel positions ``cols``, ``rows`` (fractional;
    pixel centers are at .5) of an image with ``transform``. Points PROJ
    can't invert have NaN factors."""
    with PROJ_LOCK:
        model = _model(_key(crs))
        if model is None:
            raise ValueError(f"No distortion for this coordinate system: {crs}")
        lons, lats = _lonLat(model, transform, cols, rows)
        return _factors(model, lons, lats, _aspect(transform))


def distortionOver(
    crs: CRS | str | None,
    transform: Affine,
    width: int,
    height: int,
    window: tuple[int, int, int, int] | None = None,
) -> DistortionSummary | None:
    """The worst distortion over a 9 × 9 grid of pixel centers spanning the
    image, or ``window`` (row_off, col_off, height, width, as
    ``VardaRaster.getData``). None without a CRS, for a CRS on no body or
    that PROJ can't read, or when PROJ can invert none of the samples."""
    if crs is None:
        return None
    rowOff, colOff, rowCount, colCount = window or (0, 0, height, width)
    if rowCount <= 0 or colCount <= 0:
        return None
    cols, rows = np.meshgrid(
        colOff + _centers(colCount), rowOff + _centers(rowCount), indexing="xy"
    )
    with PROJ_LOCK:
        model = _model(_key(crs))
        if model is None:
            return None
        lons, lats = _lonLat(model, transform, cols.ravel(), rows.ravel())
        samples = _factors(model, lons, lats, _aspect(transform))
    candidates = [
        (sample, lon, lat)
        for sample, lon, lat in zip(samples, lons, lats, strict=True)
        if math.isfinite(sample._severity)
    ]
    if not candidates:
        return None
    worst, lon, lat = max(candidates, key=lambda found: found[0]._severity)
    where = (-float(lon) if model.west else float(lon), float(lat))
    return DistortionSummary(worst=worst, where=where, text=_text(worst, where[1]))


def _centers(count: int) -> np.ndarray:
    """_GRID pixel centers spread from the first pixel to the last."""
    return np.unique(np.linspace(0, count - 1, _GRID).round()) + 0.5


def _text(worst: Distortion, lat: float) -> str:
    h, k = worst.meridionalScale, worst.parallelScale
    direction = "east–west" if abs(k - 1) > abs(h - 1) else "north–south"
    hemisphere = "N" if lat >= 0 else "S"
    text = (
        f"up to {worst.scaleError * 100:.1f}% scale error ({direction}) "
        f"at {abs(lat):.1f}°{hemisphere}"
    )
    if worst.angularDistortionDeg > ANGLE_TIP_DEG:
        text += f", {worst.angularDistortionDeg:.1f}° angular distortion"
    return text


def _key(crs: CRS | str) -> str:
    """The CRS as text, for caching; the caller holds PROJ_LOCK."""
    return crs if isinstance(crs, str) else CRS.from_user_input(crs).srs


@lru_cache(maxsize=256)
def _model(key: str) -> _Model | None:
    """The distortion model of the CRS ``key``, built once; None for a CRS
    on no body (or geocentric) or one PROJ can't read. The caller holds
    PROJ_LOCK."""
    try:
        crs = CRS.from_user_input(key)
    except CRSError:
        return None
    geodetic = crs.geodetic_crs
    if geodetic is None or crs.ellipsoid is None or crs.is_geocentric:
        return None
    west = any(
        axis.direction == "west"
        for axis in geodetic.axis_info
        if "longitude" in axis.name.lower() or axis.abbrev.lower() == "lon"
    )
    inverse = crs.ellipsoid.inverse_flattening
    flattening = 1 / inverse if inverse else 0.0
    e2 = flattening * (2 - flattening)
    centric = e2 > 0 and _planetocentric(geodetic)
    if crs.is_projected:
        westing = bool(crs.axis_info) and crs.axis_info[0].direction == "west"
        return _Model(Proj(crs), 1.0, e2, west, westing, centric)
    unit = crs.axis_info[0].unit_conversion_factor if crs.axis_info else 1.0
    return _Model(None, math.degrees(unit), e2, west, False, centric)


def _planetocentric(geodetic: CRS) -> bool:
    """A planetocentric latitude axis, read as ``describeCrs`` reads its
    latitude type (axis name, or a spherical coordinate system)."""
    names = " ".join(axis.name.lower() for axis in geodetic.axis_info)
    system = geodetic.coordinate_system
    subtype = system.to_json_dict().get("subtype") if system is not None else None
    return "planetocentric" in names or "geocentric" in names or subtype == "spherical"


def _toGraphic(lats: np.ndarray, e2: float) -> np.ndarray:
    """Planetocentric to planetographic latitude, degrees:
    tan φg = tan φc / (1 − e²) = (a/b)² tan φc."""
    phi = np.radians(lats)
    return np.degrees(np.arctan2(np.sin(phi), (1 - e2) * np.cos(phi)))


def _toCentric(lats: np.ndarray, e2: float) -> np.ndarray:
    """Planetographic to planetocentric latitude, degrees."""
    phi = np.radians(lats)
    return np.degrees(np.arctan2((1 - e2) * np.sin(phi), np.cos(phi)))


def _lonLat(
    model: _Model, transform: Affine, cols: np.ndarray, rows: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Pixel positions as lon/lat in degrees, lon east-positive (PROJ's
    convention) and lat in the CRS's own (planetocentric or not); NaN where
    PROJ can't invert. The caller holds PROJ_LOCK."""
    xs, ys = transform * (np.asarray(cols, float), np.asarray(rows, float))
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    if model.proj is None:
        lons, lats = xs * model.degreesPerUnit, ys * model.degreesPerUnit
        return (-lons if model.west else lons), lats
    if model.westing:
        xs = -xs
    # lists: pyproj takes a one-element array for a scalar (and warns)
    lons, lats = model.proj(xs.tolist(), ys.tolist(), inverse=True, errcheck=False)
    lons, lats = np.asarray(lons, float), np.asarray(lats, float)
    invalid = ~(np.isfinite(lons) & np.isfinite(lats) & (np.abs(lats) <= 90))
    lons, lats = np.where(invalid, np.nan, lons), np.where(invalid, np.nan, lats)
    if model.planetocentric:
        lats = _toCentric(lats, model.eccentricitySquared)
    return lons, lats


def _aspect(transform: Affine) -> float:
    """Degrees north–south per degree east–west of a pixel (geographic)."""
    across = math.hypot(transform.a, transform.d)
    down = math.hypot(transform.b, transform.e)
    return down / across if across else 1.0


def _factors(
    model: _Model, lons: np.ndarray, lats: np.ndarray, aspect: float
) -> list[Distortion]:
    """Distortion at east-positive ``lons`` and ``lats`` in the CRS's own
    latitude (degrees); NaN where the factors aren't finite. The caller
    holds PROJ_LOCK."""
    e2 = model.eccentricitySquared
    graphic = _toGraphic(lats, e2) if model.planetocentric else lats
    if model.proj is not None:
        factors = model.proj.get_factors(lons, graphic, errcheck=False)
        columns = (
            factors.meridional_scale,
            factors.parallel_scale,
            factors.areal_scale,
            factors.angular_distortion,
        )
        h, k, areal, omega = (np.atleast_1d(np.asarray(c, float)) for c in columns)
    else:
        phi = np.radians(graphic)
        with np.errstate(divide="ignore", invalid="ignore"):
            # M / N = (1 − e²) / (1 − e² sin² φg)
            curvature = (1 - e2) / (1 - e2 * np.sin(phi) ** 2)
            if model.planetocentric:
                # a degree of φc is dφg/dφc degrees of φg:
                # dφg/dφc = (a/b)² / (cos² φc + (a/b)⁴ sin² φc)
                ratio = 1 / (1 - e2)
                centric = np.radians(lats)
                curvature = (
                    curvature
                    * ratio
                    / (np.cos(centric) ** 2 + ratio**2 * np.sin(centric) ** 2)
                )
            k = aspect * curvature / np.cos(phi)
            k = np.where(np.abs(lats) < 90, k, np.inf)
            h = np.ones_like(k)
            areal = h * k
            omega = np.degrees(2 * np.arcsin(np.abs(h - k) / (h + k)))
    distortions = []
    for values in zip(h, k, areal, omega, strict=True):
        finite = all(math.isfinite(value) for value in values)
        distortions.append(
            Distortion(*(float(value) if finite else math.nan for value in values))
        )
    return distortions
