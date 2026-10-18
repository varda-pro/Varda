"""Profiles: densified lines, bilinear samples, and distances in meters."""

import numpy as np
import pytest
from affine import Affine

from varda.geo.bodies import BODIES
from varda.lines.profile import (
    densify,
    lineLength,
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
    assert result.distanceUnits == "m" and result.distance[-1] == pytest.approx(600)
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


def test_without_georeferencing_distances_are_pixels():
    length, units = lineLength(np.array([[0, 0], [3, 4]]), None, None)
    assert (length, units) == (5.0, "px")


def test_a_freehand_line_is_thinned_but_keeps_its_ends():
    x = np.linspace(0, 50, 500)
    points = np.column_stack([x, 0.01 * np.sin(x)])
    thinned = simplify(points, tolerance=0.5)
    assert len(thinned) < 10
    assert np.allclose(thinned[0], points[0]) and np.allclose(thinned[-1], points[-1])
