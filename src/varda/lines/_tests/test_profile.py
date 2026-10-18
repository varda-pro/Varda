"""Profiles: densified lines, bilinear samples, and distances in meters."""

import warnings

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS

from varda.geo.bodies import BODIES
from varda.geo.measure import Measurement
from varda.lines.profile import (
    PLANAR_METHOD,
    densify,
    lineLength,
    lineMeasurement,
    mapDistances,
    profile,
    sampleBilinear,
    simplify,
)


def test_densify_steps_about_a_pixel_and_keeps_every_vertex():
    positions, distance = densify(np.array([[0, 0], [10, 0], [10, 5]]))
    assert len(positions) == 16 and distance[-1] == pytest.approx(15)
    assert np.allclose(np.diff(distance), 1.0)
    assert any(np.allclose(p, [10, 0]) for p in positions)


def test_samples_sit_at_pixel_centers_and_interpolate_between_them():
    band = np.array([[0.0, 10.0], [20.0, 30.0]])
    centers = np.array([[0.5, 0.5], [1.5, 0.5], [1.0, 0.5], [1.0, 1.0]])
    assert sampleBilinear(band, centers) == pytest.approx([0, 10, 5, 15])


def test_off_the_image_and_no_data_are_nan():
    band = np.array([[0.0, np.nan], [20.0, 30.0]])
    values = sampleBilinear(band, np.array([[0.5, 0.5], [1.0, 0.5], [5.0, 5.0]]))
    assert values[0] == 0 and np.isnan(values[1]) and np.isnan(values[2])


def test_a_dem_profile_runs_up_a_slope_in_meters():
    rows, cols = 20, 40
    dem = np.tile(np.arange(cols, dtype=float) * 2.0, (rows, 1))  # 2 m per pixel east
    transform = Affine(20.0, 0, 100000.0, 0, -20.0, 50000.0)  # 20 m pixels
    crs = "+proj=eqc +R=3396190 +units=m +no_defs"
    result = profile(dem, np.array([[0.5, 10.0], [30.5, 10.0]]), transform, crs)
    # geodesic: 600 m on the map is 599.93 m on the body at 0.84°N
    assert result.distanceUnits == "m"
    assert result.distance[-1] == pytest.approx(600, rel=2e-4)
    assert result.values[0] == pytest.approx(0) and result.values[-1] == pytest.approx(
        60
    )


def test_geographic_distances_are_geodesic_on_the_body():
    # one degree of longitude at the equator of Mars's IAU 2015 sphere
    transform = Affine(0.1, 0, 0.0, 0, -0.1, 0.0)
    distance, units = mapDistances(
        np.array([[0.0, 0.0], [10.0, 0.0]]), transform, BODIES["Mars"].sphereCrs()
    )
    assert units == "m"
    assert distance[-1] == pytest.approx(
        2 * np.pi * BODIES["Mars"].equatorialRadius / 360, rel=1e-6
    )


def _oldGeographicDistances(positions, transform, crs) -> np.ndarray:
    """The distances before Task 9: geodesics on the geographic CRS's own
    ellipsoid, between its (geodetic) longitudes and latitudes."""
    xs, ys = transform * (positions[:, 0], positions[:, 1])
    geod = CRS.from_user_input(crs).get_geod()
    assert geod is not None
    _, _, lengths = geod.inv(list(xs[:-1]), list(ys[:-1]), list(xs[1:]), list(ys[1:]))
    return np.concatenate([[0.0], np.cumsum(np.abs(lengths))])


@pytest.mark.parametrize(
    "crs",
    [
        "+proj=longlat +R=3396000 +no_defs",  # MOLA MEGDR
        "+proj=longlat +a=3396190 +b=3376200 +no_defs",  # planetographic
    ],
)
def test_geographic_profiles_are_unchanged(crs):
    transform = Affine(0.05, 0, 30.0, 0, -0.05, 45.0)
    positions, _ = densify(np.array([[3.0, 2.0], [80.0, 61.0], [20.0, 90.0]]))
    distance, units = mapDistances(positions, transform, crs)
    assert units == "m"
    np.testing.assert_allclose(
        distance, _oldGeographicDistances(positions, transform, crs), rtol=1e-6
    )


def test_projected_profiles_are_geodesic_not_planar():
    # 0°-centered equirectangular stretches east-west 2x at 60°N (Snyder 1987)
    crs = "+proj=eqc +lat_ts=0 +lon_0=0 +R=3396190 +units=m +no_defs"
    y60 = 3396190 * np.pi / 3
    transform = Affine(100.0, 0, 0.0, 0, -100.0, y60)
    band = np.zeros((10, 300))
    result = profile(band, np.array([[0.0, 0.0], [200.0, 0.0]]), transform, crs)
    assert result.distanceUnits == "m"
    assert result.distance[-1] == pytest.approx(10_000, abs=1)  # not 20,000
    length, units = lineLength(np.array([[0.0, 0.0], [200.0, 0.0]]), transform, crs)
    assert units == "m" and length == pytest.approx(result.distance[-1], rel=1e-9)


def test_a_line_measurement_says_how_it_was_measured():
    crs = "+proj=eqc +lat_ts=0 +lon_0=0 +R=3396190 +units=m +no_defs"
    transform = Affine(100.0, 0, 0.0, 0, -100.0, 3396190 * np.pi / 3)
    vertices = np.array([[0.0, 0.0], [200.0, 0.0]])
    measurement = lineMeasurement(vertices, transform, crs)
    assert isinstance(measurement, Measurement)
    assert measurement.value == pytest.approx(10_000, abs=1)
    assert measurement.method == "geodesic (Karney 2013) on sphere R = 3396.19 km"
    # Mars's IAU 2015 ellipsoid differs from the sphere
    assert measurement.reference is not None
    assert measurement.bodyModelDifference > 0
    assert measurement.distortion is not None
    assert measurement.distortion.worst.level == "strong"
    assert lineMeasurement(vertices, None, None) is None


def test_without_georeferencing_distances_are_pixels():
    length, units = lineLength(np.array([[0, 0], [3, 4]]), None, None)
    assert (length, units) == (5.0, "px")


def test_a_freehand_line_is_thinned_but_keeps_its_ends():
    x = np.linspace(0, 50, 500)
    points = np.column_stack([x, 0.01 * np.sin(x)])
    thinned = simplify(points, tolerance=0.5)
    assert len(thinned) < 10
    assert np.allclose(thinned[0], points[0]) and np.allclose(thinned[-1], points[-1])


def test_samples_off_the_body_have_no_distance_and_no_warnings():
    # an orthographic view of Mars: the line runs off the disk
    crs = "+proj=ortho +lat_0=0 +lon_0=0 +R=3396190 +units=m +no_defs"
    transform = Affine(100_000.0, 0, 3_000_000.0, 0, -100_000.0, 0.0)
    vertices = np.array([[0.0, 0.5], [10.0, 0.5]])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = profile(np.zeros((5, 20)), vertices, transform, crs)
        measurement = lineMeasurement(vertices, transform, crs)
    assert np.isfinite(result.distance[:4]).all() and np.isnan(result.distance[-1])
    assert measurement is not None and np.isnan(measurement.value)


# An engineering CRS: a lab or rover frame on no body, in centimeters
LOCAL_CM = (
    'LOCAL_CS["Rover",LOCAL_DATUM["d",0],UNIT["centimetre",0.01],'
    'AXIS["X",EAST],AXIS["Y",NORTH]]'
)
ENGCRS_M = (
    'ENGCRS["Lab",EDATUM["Lab datum"],CS[Cartesian,2],'
    'AXIS["x",east,LENGTHUNIT["metre",1]],AXIS["y",north,LENGTHUNIT["metre",1]]]'
)
LOCAL_UNKNOWN = (
    'LOCAL_CS["Frame",LOCAL_DATUM["d",0],UNIT["unknown",1],'
    'AXIS["X",EAST],AXIS["Y",NORTH]]'
)


@pytest.mark.parametrize(("crs", "meters"), [(ENGCRS_M, 100.0), (LOCAL_CM, 1.0)])
def test_a_crs_on_no_body_measures_planar_in_its_units(crs, meters):
    transform = Affine(20.0, 0, 0.0, 0, -20.0, 0.0)  # 20 units per pixel
    vertices = np.array([[0.0, 0.0], [3.0, 4.0]])  # 5 px = 100 units
    length, units = lineLength(vertices, transform, crs)
    assert units == "m" and length == pytest.approx(meters)
    result = profile(np.zeros((10, 10)), vertices, transform, crs)
    assert result.distanceUnits == "m" and result.distance[-1] == pytest.approx(meters)
    measurement = lineMeasurement(vertices, transform, crs)
    assert measurement is not None
    assert measurement.method == PLANAR_METHOD
    assert measurement.reference is None and measurement.distortion is None


def test_a_crs_on_no_body_in_unknown_units_measures_pixels():
    transform = Affine(20.0, 0, 0.0, 0, -20.0, 0.0)
    vertices = np.array([[0.0, 0.0], [3.0, 4.0]])
    assert lineLength(vertices, transform, LOCAL_UNKNOWN) == (5.0, "px")
    assert (
        profile(np.zeros((10, 10)), vertices, transform, LOCAL_UNKNOWN).distanceUnits
        == "px"
    )
    assert lineMeasurement(vertices, transform, LOCAL_UNKNOWN) is None
