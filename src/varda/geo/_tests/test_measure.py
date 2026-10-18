"""Geodesic lengths, areas and pixel spacing on the body, whatever the
projection, with the difference from the body's IAU 2015 reference
ellipsoid."""

import math

import numpy as np
import pytest
import shapely
from affine import Affine
from pyproj import Geod

from varda.geo._tests.fixtures import (
    HIRISE_POLAR_PROJ,
    LRO_WAC_PROJ,
    makeImage,
)
from varda.geo.distortion import distortionAt
from varda.geo.measure import (
    cumulativeDistance,
    groundSpacing,
    lineLength,
    polygonArea,
    toLonLat,
)
from varda.geo.transform import transformPoints

R_MARS = 3396190.0
A, C = 3396190.0, 3376200.0  # Mars IAU 2015 ellipsoid
MARS_EQC = "+proj=eqc +lat_ts=0 +lon_0=0 +R=3396190 +units=m +no_defs"
DEGREE = math.pi * R_MARS / 180  # 59,274.70 m


def test_a_degree_of_latitude_on_the_mars_sphere_geographic():
    measured = lineLength("IAU_2015:49900", np.array([[0.0, 0.0], [0.0, 1.0]]))
    assert measured.value == pytest.approx(59274.70, abs=0.01)
    assert measured.value == pytest.approx(DEGREE, abs=0.01)
    assert measured.unit == "m"
    assert measured.method == "geodesic (Karney 2013) on sphere R = 3396.19 km"


def test_a_degree_of_latitude_on_the_mars_sphere_equirectangular():
    xy = np.array([[0.0, 0.0], [0.0, DEGREE]])
    assert lineLength("IAU_2015:49910", xy).value == pytest.approx(59274.70, abs=0.01)


def test_an_east_west_line_at_60n_is_half_its_map_length():
    y = math.radians(60) * R_MARS
    xy = np.array([[-10000.0, y], [10000.0, y]])
    assert lineLength(MARS_EQC, xy).value == pytest.approx(10000.0, abs=1.0)


def test_cumulative_distance_starts_at_zero_and_adds_segments():
    xy = np.array([[0.0, 0.0], [0.0, DEGREE], [0.0, 2 * DEGREE]])
    distances = cumulativeDistance("IAU_2015:49910", xy)
    np.testing.assert_allclose(distances, [0.0, DEGREE, 2 * DEGREE], atol=1e-3)


def _groundSquare(crs: str, lon: float, lat: float, side: float) -> shapely.Polygon:
    """A square of ``side`` m on the ground centered on ``lon``, ``lat``: its
    map side is ``side`` · k, k the CRS's (conformal) scale there."""
    (x,), (y,) = transformPoints("+proj=longlat +R=3376200", crs, [lon], [lat])
    half = side * distortionAt(crs, lon, lat).parallelScale / 2
    return shapely.box(x - half, y - half, x + half, y + half)


def test_a_one_km_square_at_85n_in_polar_stereographic():
    square = _groundSquare(HIRISE_POLAR_PROJ, 0.0, 85.0, 1000.0)
    area, perimeter = polygonArea(HIRISE_POLAR_PROJ, square, densifyStep=1.0)
    assert area.value == pytest.approx(1.0e6, rel=1e-3)
    assert area.unit == "m²"
    assert perimeter.value == pytest.approx(4000.0, rel=1e-3)
    assert perimeter.unit == "m"


def test_the_moon_sphere_is_its_own_reference():
    measured = lineLength(LRO_WAC_PROJ, np.array([[0.0, 0.0], [1000.0, 0.0]]))
    assert measured.reference is None
    assert measured.bodyModelDifference == 0


def _northSouthLine(crs: str, lat: float) -> np.ndarray:
    (x0, x1), (y0, y1) = transformPoints(
        "+proj=longlat +R=3376200", crs, [0.0, 0.0], [lat, lat + 0.01]
    )
    return np.array([[x0, y0], [x1, y1]])


def test_the_hirise_polar_sphere_near_the_pole_matches_the_ellipsoid():
    """Decision (task 8 report): the polar sphere's radius is Mars's polar
    radius c, and near the pole the ellipsoid is that sphere: the same
    planetocentric positions are within 0.005% of each other at 85°N. The
    brief's 1.1–1.3% is a 1 km line with its latitude number kept (not
    converted), i.e. a different place on the ellipsoid."""
    measured = lineLength(HIRISE_POLAR_PROJ, _northSouthLine(HIRISE_POLAR_PROJ, 85.0))
    assert measured.reference is not None
    assert 1e-5 < measured.bodyModelDifference < 1e-4
    assert measured.bodyModelDifference == pytest.approx(
        abs(measured.value - measured.reference) / measured.reference
    )


def test_the_reference_converts_planetocentric_to_planetographic():
    """On the Mars 2000 sphere (R = a) at 85°N the ellipsoid's surface is
    near c, so the difference is about (a − c)/c = 0.59%; computed here with
    Snyder (1987) eq. 3-28, tan φg = (a/c)² tan φc."""
    xy = _northSouthLine("IAU_2015:49910", 85.0)
    measured = lineLength("IAU_2015:49910", xy)
    lon, lat = transformPoints("IAU_2015:49910", "+proj=longlat +R=3396190", *xy.T)
    graphic = np.degrees(np.arctan((A / C) ** 2 * np.tan(np.radians(lat))))
    expected = Geod(a=A, b=C).line_length(lon, graphic)
    assert measured.reference == pytest.approx(expected, rel=1e-9)
    assert measured.bodyModelDifference == pytest.approx(0.0059, abs=0.0003)


def test_a_hole_is_subtracted():
    outer = shapely.box(-20000.0, 100000.0, 20000.0, 140000.0)
    hole = shapely.box(-5000.0, 110000.0, 5000.0, 120000.0)
    holed = shapely.Polygon(outer.exterior.coords, [hole.exterior.coords])
    area = polygonArea("IAU_2015:49910", holed, 1000.0)[0].value
    outerArea = polygonArea("IAU_2015:49910", outer, 1000.0)[0].value
    holeArea = polygonArea("IAU_2015:49910", hole, 1000.0)[0].value
    assert area == pytest.approx(outerArea - holeArea, rel=1e-4)


def test_a_multipolygon_is_summed():
    first = shapely.box(0.0, 0.0, 10000.0, 10000.0)
    second = shapely.box(50000.0, 0.0, 60000.0, 10000.0)
    both = shapely.MultiPolygon([first, second])
    area, perimeter = polygonArea("IAU_2015:49910", both, 1000.0)
    single = [polygonArea("IAU_2015:49910", p, 1000.0) for p in (first, second)]
    assert area.value == pytest.approx(sum(s[0].value for s in single), rel=1e-9)
    assert perimeter.value == pytest.approx(sum(s[1].value for s in single), rel=1e-9)


def test_ground_spacing_at_60n_in_equirectangular():
    y = math.radians(60) * R_MARS
    transform = Affine(100.0, 0.0, 0.0, 0.0, -100.0, y + 50.0)
    acrossColumns, acrossRows = groundSpacing(
        MARS_EQC, transform, np.array([0]), np.array([0])
    )
    assert acrossColumns[0] == pytest.approx(50.0, rel=1e-6)
    assert acrossRows[0] == pytest.approx(100.0, rel=1e-6)


@pytest.mark.parametrize("code", ["IAU_2015:49901", "IAU_2015:49912"])
def test_to_lon_lat_puts_longitude_first(code):
    """PROJ's always_xy leaves these latitude first; toLonLat doesn't."""
    lon, lat = 77.5, 45.0  # east, planetocentric
    x, y = transformPoints("+proj=longlat +R=3396190", code, [lon], [lat])
    found = toLonLat(code, np.array([[x[0], y[0]]]))
    if code == "IAU_2015:49901":  # planetographic, west-positive, on its own
        graphic = math.degrees(math.atan((A / C) ** 2 * math.tan(math.radians(lat))))
        assert (-found[0, 0]) % 360 == pytest.approx(lon, abs=1e-9)
        assert found[0, 1] == pytest.approx(graphic, abs=1e-9)
    else:  # planetocentric
        np.testing.assert_allclose(found, [[lon, lat]], atol=1e-9)
        assert x[0] == pytest.approx(math.radians(lon) * A, rel=1e-12)


def test_make_image_centers_on_the_crs_own_longitude_and_latitude():
    image = makeImage("IAU_2015:49912", centerLonLat=(77.5, 45.0), pixelSize=100.0)
    center = np.array([image.transform * (image.width / 2, image.height / 2)])
    assert image.crs is not None
    np.testing.assert_allclose(toLonLat(image.crs, center), [[77.5, 45.0]], atol=1e-9)


def test_distortion_comes_with_a_transform_and_never_changes_the_value():
    y = math.radians(60) * R_MARS
    transform = Affine(100.0, 0.0, -20000.0, 0.0, -100.0, y + 5000.0)
    xy = np.array([[-10000.0, y], [10000.0, y]])
    plain = lineLength(MARS_EQC, xy)
    withTips = lineLength(MARS_EQC, xy, transform=transform)
    assert plain.distortion is None
    assert withTips.distortion is not None
    assert withTips.distortion.worst.parallelScale == pytest.approx(2.0, rel=1e-3)
    assert withTips.value == plain.value
