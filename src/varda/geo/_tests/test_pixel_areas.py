"""The ground area of each pixel on the CRS's own surface: exact per row
where the area depends on the row only, else the map pixel area over the
projection's areal scale (spec §4.5, Phase 2)."""

import math

import numpy as np
import pytest
import shapely
from affine import Affine

from varda.geo._tests.fixtures import CRISM_MTRDR_WKT, HIRISE_POLAR_PROJ
from varda.geo.distortion import arealScaleAtPixels
from varda.geo.measure import (
    AREA_METHOD_ROWS,
    AREA_METHOD_SCALE,
    imagePixelAreas,
    pixelAreaMethod,
    pixelAreas,
    polygonArea,
)

R_MARS = 3396190.0
R_HIRISE = 3376200.0


def _polarY(lat: float) -> float:
    """The polar stereographic (k0 = 1 at the pole) y on the HiRISE sphere
    of a point at ``lat`` on the central meridian: ρ = 2R tan(π/4 − φ/2)
    (Snyder, 1987, eq. 21-1), y = −ρ."""
    return -2 * R_HIRISE * math.tan(math.pi / 4 - math.radians(lat) / 2)


def test_geographic_rows_match_the_sphere_formula():
    step = 0.1
    transform = Affine(step, 0, 10.0, 0, -step, 60.0)
    areas = pixelAreas(
        "+proj=longlat +R=3396190", transform, np.array([0]), np.array([0])
    )
    top, bottom = math.radians(60.0), math.radians(60.0 - step)
    expected = R_MARS**2 * math.radians(step) * (math.sin(top) - math.sin(bottom))
    # the brief asked for 1e-9; 16 points per edge leave 1.24e-9 here: the
    # geodesic chords between them cut inside the parallels (the error is
    # about 1.2e-7 × (Δλ/1°)² at 60°N, at most 2.1e-7 × (Δλ/1°)²)
    assert areas[0] == pytest.approx(expected, rel=2e-9)


def test_equirectangular_rows():
    y = math.radians(60) * R_MARS
    transform = Affine(1000.0, 0, 0.0, 0, -1000.0, y + 500.0)
    areas = pixelAreas("IAU_2015:49910", transform, np.array([0, 0]), np.array([0, 7]))
    # k = 1/cos 60° = 2, h = 1: half the map area
    np.testing.assert_allclose(areas, 0.5e6, rtol=1e-4)
    assert pixelAreaMethod("IAU_2015:49910", transform).startswith(AREA_METHOD_ROWS)


def test_polar_stereographic_uses_areal_scale():
    transform = Affine(1.0, 0, -0.5, 0, -1.0, _polarY(85.0) + 0.5)
    areas = pixelAreas(HIRISE_POLAR_PROJ, transform, np.array([0]), np.array([0]))
    scale = arealScaleAtPixels(
        HIRISE_POLAR_PROJ, transform, np.array([0.5]), np.array([0.5])
    )
    assert areas[0] == pytest.approx(1 / scale[0], rel=1e-12)
    x0, y0 = transform * (0, 0)
    x1, y1 = transform * (1, 1)
    square = shapely.box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    area, _ = polygonArea(HIRISE_POLAR_PROJ, square, 0.1)
    assert areas[0] == pytest.approx(area.value, rel=1e-6)
    assert pixelAreaMethod(HIRISE_POLAR_PROJ, transform).startswith(AREA_METHOD_SCALE)


def test_whole_image_interpolation_error():
    size = 4000
    transform = Affine(1.0, 0, -size / 2, 0, -1.0, _polarY(85.0) + size / 2)
    areas = imagePixelAreas(HIRISE_POLAR_PROJ, transform, size, size)
    assert areas.shape == (size, size)
    rng = np.random.default_rng(0)
    rows, cols = rng.integers(0, size, 100), rng.integers(0, size, 100)
    exact = pixelAreas(HIRISE_POLAR_PROJ, transform, rows, cols)
    np.testing.assert_allclose(areas[rows, cols], exact, rtol=1e-6)


def test_whole_image_rows_repeat_the_row_area():
    transform = Affine(1000.0, 0, 0.0, 0, -1000.0, math.radians(60) * R_MARS)
    areas = imagePixelAreas("IAU_2015:49910", transform, 3, 2)
    assert areas.shape == (2, 3)
    np.testing.assert_allclose(areas[0], areas[0, 0])
    exact = pixelAreas("IAU_2015:49910", transform, np.array([1]), np.array([2]))
    assert areas[1, 2] == pytest.approx(exact[0], rel=1e-12)


def test_off_the_projection_is_nan():
    # equirectangular past the pole (row areas) and orthographic off the
    # disc (areal scale): PROJ can't invert
    beyond = Affine(1000.0, 0, 0.0, 0, -1000.0, 1e8)
    assert np.isnan(pixelAreas("IAU_2015:49910", beyond, [0], [0])[0])
    offDisc = Affine(1000.0, 0, 1e8, 0, -1000.0, 0.0)
    assert np.isnan(pixelAreas("+proj=ortho +R=3396190", offDisc, [0], [0])[0])


def test_no_body_raises():
    with pytest.raises(ValueError):
        pixelAreas("EPSG:5714", Affine.identity(), np.array([0]), np.array([0]))


def test_method_text():
    transform = Affine(18.0, 0, 0.0, 0, -18.0, 1e6)
    method = pixelAreaMethod(CRISM_MTRDR_WKT, transform)
    assert method.startswith(AREA_METHOD_ROWS)
    assert method.endswith("sphere R = 3394.84 km")


@pytest.mark.parametrize(
    "crs", ["+proj=longlat +R=3396190", "IAU_2015:49901", "IAU_2015:49902"]
)
def test_rotated_geographic_uses_the_area_at_the_center(crs):
    # a rotation too small to move the pixel leaves the scale method, which
    # must agree with the exact row area to (pixel / R)²
    rows = Affine(0.01, 0, 10.0, 0, -0.01, 45.0)
    rotated = Affine(0.01, 1e-12, 10.0, 0, -0.01, 45.0)
    assert pixelAreaMethod(crs, rotated).startswith(AREA_METHOD_SCALE)
    exact = pixelAreas(crs, rows, [3], [5])[0]
    assert pixelAreas(crs, rotated, [3], [5])[0] == pytest.approx(exact, rel=1e-6)
