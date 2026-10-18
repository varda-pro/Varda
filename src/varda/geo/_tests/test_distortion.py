"""Distortion at points and over images, against Snyder (1987), *Map
Projections: A Working Manual*, USGS Professional Paper 1395."""

from __future__ import annotations

import math

import numpy as np
import pytest
from affine import Affine
from pyproj import Proj, Transformer

from varda.geo._tests.fixtures import (
    CRISM_MTRDR_WKT,
    CTX_DEM_PROJ,
    HIRISE_POLAR_PROJ,
    MOLA_MEGDR_PROJ,
    makeImage,
)
from varda.geo.distortion import (
    ANGLE_STRONG_DEG,
    ANGLE_TIP_DEG,
    SCALE_STRONG,
    SCALE_TIP,
    Distortion,
    distortionAt,
    distortionAtPixels,
    distortionOver,
)

MARS_R = 3396190.0
CRISM_R = 3394839.81331631


def test_thresholds_are_the_specs():
    assert (SCALE_TIP, ANGLE_TIP_DEG, SCALE_STRONG, ANGLE_STRONG_DEG) == (
        0.01,
        1.0,
        0.10,
        10.0,
    )


def test_equirectangular_on_the_equator_doubles_east_west_at_60():
    # Snyder p. 91: h = 1, k = cos φ1 / cos φ
    found = distortionAt(f"+proj=eqc +lat_ts=0 +R={MARS_R}", 30.0, 60.0)
    assert found.parallelScale == pytest.approx(2.0, abs=1e-9)
    assert found.meridionalScale == pytest.approx(1.0, abs=1e-9)
    assert found.level == "strong"


def test_polar_stereographic_at_65():
    # Snyder eq. 21-4: k = 2 k0 / (1 + sin φ), conformal
    found = distortionAt(f"+proj=stere +lat_0=90 +lon_0=0 +k=1 +R={MARS_R}", 40, 65)
    expected = 2 / (1 + math.sin(math.radians(65)))
    assert expected == pytest.approx(1.04915, abs=1e-5)
    assert found.parallelScale == pytest.approx(expected, abs=1e-5)
    assert found.meridionalScale == pytest.approx(expected, abs=1e-5)
    assert found.angularDistortionDeg == pytest.approx(0.0, abs=1e-6)


def test_transverse_mercator_300_km_from_the_central_meridian():
    # Snyder eq. 8-7 at the equator: k = 1 / √(1 − sin²(x/R))
    lon = math.degrees(300_000 / MARS_R)
    found = distortionAt(f"+proj=tmerc +lon_0=0 +R={MARS_R}", lon, 0.0)
    expected = 1 / math.sqrt(1 - math.sin(300_000 / MARS_R) ** 2)
    assert expected == pytest.approx(1.00391, abs=1e-5)
    assert found.scaleError == pytest.approx(expected - 1, abs=1e-5)


def test_crism_sphere_at_its_own_standard_parallel_and_north_of_it():
    # the real MTRDR sphere, lat_ts = 15°: true at 15°N
    assert distortionAt(CRISM_MTRDR_WKT, 0, 15).scaleError == pytest.approx(0, abs=1e-9)
    found = distortionAt(CRISM_MTRDR_WKT, 0, 18.4)
    expected = math.cos(math.radians(15)) / math.cos(math.radians(18.4))
    assert found.parallelScale == pytest.approx(expected, abs=1e-9)
    assert expected == pytest.approx(1.01797, abs=1e-5)


def test_crism_at_18_4_north_is_a_tip_east_west():
    image = makeImage(CRISM_MTRDR_WKT, centerLonLat=(77.5, 18.4), pixelSize=18)
    summary = distortionOver(image.crs, image.transform, image.width, image.height)
    assert summary is not None
    assert summary.worst.level == "tip"
    assert "east–west" in summary.text
    assert "18.4°N" in summary.text
    assert summary.text.startswith("up to 1.8% scale error (east–west) at 18.4°N")
    assert summary.where[1] == pytest.approx(18.4, abs=0.05)


def test_ctx_dem_at_its_standard_parallel_is_ok():
    image = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 18.4), pixelSize=20)
    summary = distortionOver(image.crs, image.transform, image.width, image.height)
    assert summary is not None
    assert summary.worst.level == "ok"


def test_polar_image_far_from_the_pole_is_north_south_and_east_west_alike():
    image = makeImage(HIRISE_POLAR_PROJ, centerLonLat=(0, 60), pixelSize=1000)
    summary = distortionOver(image.crs, image.transform, image.width, image.height)
    assert summary is not None
    assert summary.worst.level == "tip"
    assert "angular" not in summary.text  # conformal


def test_geographic_at_60_relative_to_square_degrees():
    # square-degree pixels are an equirectangular map true at the equator
    found = distortionAt(MOLA_MEGDR_PROJ, 10.0, 60.0)
    assert found.parallelScale == pytest.approx(2.0, abs=1e-9)
    assert found.meridionalScale == 1.0
    image = makeImage(MOLA_MEGDR_PROJ, centerLonLat=(10, 60), pixelSize=1 / 128)
    summary = distortionOver(image.crs, image.transform, image.width, image.height)
    assert summary is not None
    assert summary.worst.parallelScale == pytest.approx(2.0, rel=0.01)
    assert summary.worst.level == "strong"
    assert "east–west" in summary.text
    assert "angular" in summary.text


def test_geographic_pixels_square_on_the_ground_at_60_are_ok_there():
    transform = Affine(2 / 128, 0, 0, 0, -1 / 128, 61)
    found = distortionAtPixels(
        MOLA_MEGDR_PROJ, transform, np.array([0.0]), np.array([128.0])
    )
    assert found[0].parallelScale == pytest.approx(1.0, abs=1e-9)


def test_geographic_on_an_ellipsoid_uses_its_radii_of_curvature():
    # Mars IAU 2015 ellipsoid, planetographic: at the equator a degree of
    # latitude (M) is shorter than one of longitude (N) by 1 − e²
    e2 = 1 - (3376200 / 3396190) ** 2
    found = distortionAt("IAU_2015:49901", 0.0, 0.0)
    assert found.parallelScale == pytest.approx(1 - e2, abs=1e-9)


def _ocentricGroundK(lat: float) -> float:
    """Square-degree k on Mars's IAU ellipsoid at planetocentric ``lat``,
    from the ground lengths themselves: east–west r cos φc, north–south
    √(r² + (dr/dφc)²), with r(φc) = ab / √((b cos φc)² + (a sin φc)²)."""
    a, b = 3396190.0, 3376200.0
    phi = math.radians(lat)
    root = (b * math.cos(phi)) ** 2 + (a * math.sin(phi)) ** 2
    r = a * b / math.sqrt(root)
    dr = -a * b * (a**2 - b**2) * math.sin(phi) * math.cos(phi) / root**1.5
    return math.hypot(r, dr) / (r * math.cos(phi))


@pytest.mark.parametrize("lat", [0.0, 45.0, -70.0])
def test_geographic_planetocentric_ellipsoid(lat: float):
    found = distortionAt("IAU_2015:49902", 0.0, lat)
    assert found.parallelScale == pytest.approx(_ocentricGroundK(lat), abs=1e-9)
    if lat == 0:
        assert found.parallelScale == pytest.approx(1.0, abs=1e-12)
        assert found.level == "ok"
    if lat == 45:
        assert found.parallelScale == pytest.approx(1.41424, abs=1e-5)


def test_projected_planetocentric_ellipsoid_takes_and_reports_ocentric_lat():
    e2 = 1 - (3376200 / 3396190) ** 2
    graphic = math.degrees(math.atan(math.tan(math.radians(45)) / (1 - e2)))
    centric = distortionAt("IAU_2015:49912", 0.0, 45.0)
    found = distortionAt("IAU_2015:49911", 0.0, graphic)
    assert centric.parallelScale == pytest.approx(found.parallelScale, abs=1e-12)
    assert centric.meridionalScale == pytest.approx(found.meridionalScale, abs=1e-12)
    # the map position from PROJ's own (ographic) eqc: pyproj's always_xy
    # doesn't put the IAU ellipsoidal CRSs' lon first, so makeImage can't
    x, y = Proj("+proj=eqc +a=3396190 +b=3376200")(0.0, graphic)
    transform = Affine(100, 0, x - 3200, 0, -100, y + 3200)
    summary = distortionOver("IAU_2015:49912", transform, 64, 64)
    assert summary is not None
    # the north edge, 3.2 km up; planetographic would read 45.24°
    assert summary.where[1] == pytest.approx(45.05, abs=0.01)


def test_empty_window_gives_none():
    image = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 18.4), pixelSize=20)
    args = (image.crs, image.transform, image.width, image.height)
    assert distortionOver(*args, window=(0, 0, 0, 64)) is None


def test_at_pixels_matches_at_points():
    image = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 40), pixelSize=200)
    cols, rows = np.array([0.5, 32.0, 63.5]), np.array([0.5, 32.0, 63.5])
    found = distortionAtPixels(CTX_DEM_PROJ, image.transform, cols, rows)
    assert len(found) == 3
    single = distortionAtPixels(CTX_DEM_PROJ, image.transform, cols[1:2], rows[1:2])
    assert single == [found[1]]
    assert found[0].parallelScale > found[1].parallelScale > found[2].parallelScale
    x, y = image.transform * (32.0, 32.0)
    toLonLat = Transformer.from_crs(CTX_DEM_PROJ, "+proj=longlat +R=3396190")
    lon, lat = toLonLat.transform(x, y)
    atPoint = distortionAt(CTX_DEM_PROJ, lon, lat)
    assert atPoint.parallelScale == pytest.approx(found[1].parallelScale, abs=1e-12)


def test_window_limits_the_samples():
    image = makeImage(
        CTX_DEM_PROJ, centerLonLat=(77.5, 18.4), pixelSize=2000, height=1000
    )
    whole = distortionOver(image.crs, image.transform, image.width, image.height)
    # rows 495–504 of 1000 (row_off, col_off, height, width): the center
    middle = distortionOver(
        image.crs, image.transform, image.width, image.height, window=(495, 0, 10, 64)
    )
    assert whole is not None and middle is not None
    assert whole.worst.level == "strong"
    assert middle.worst.level == "ok"
    assert middle.where[1] == pytest.approx(18.4, abs=0.2)


def test_no_crs_or_no_invertible_points_gives_none():
    assert distortionOver(None, Affine.identity(), 10, 10) is None
    # past the pole of an equirectangular map: nothing PROJ can invert
    outside = Affine(1e3, 0, 0, 0, -1e3, 1e8)
    assert distortionOver(CTX_DEM_PROJ, outside, 10, 10) is None
    for crs in ("not a crs", "+proj=geocent +R=3396190"):
        assert distortionOver(crs, Affine.identity(), 10, 10) is None
        with pytest.raises(ValueError):
            distortionAt(crs, 0, 0)


def test_levels():
    def made(h: float, k: float, omega: float = 0.0) -> Distortion:
        return Distortion(h, k, h * k, omega)

    assert made(1.005, 0.995).level == "ok"
    assert made(1.0, 1.02).level == "tip"
    assert made(1.0, 1.0, 1.5).level == "tip"
    assert made(0.85, 1.0).level == "strong"
    assert made(1.0, 1.0, 12.0).level == "strong"
    assert made(0.97, 1.02).scaleError == pytest.approx(0.03)
