"""Two CRISM MTRDRs, each on the sphere of Mars's local radius under its own
scene: the same body on different surfaces, so different grids, with
planetocentric places kept between them."""

import numpy as np
import pytest
from pyproj import CRS, Transformer

from varda.geo._tests.fixtures import CRISM_MTRDR_SOUTH_WKT, CRISM_MTRDR_WKT
from varda.geo.bodies import identifyBody
from varda.geo.describe import describeCrs
from varda.geo.equivalence import Compatibility, canTransform, compatibility, sameCrs
from varda.geo.transform import transformPoints
from varda.utilities.proj_lock import PROJ_LOCK

NORTH, SOUTH = CRISM_MTRDR_WKT, CRISM_MTRDR_SOUTH_WKT


@pytest.mark.parametrize("crs", [NORTH, SOUTH])
def test_each_is_mars(crs):
    match = identifyBody(crs)
    assert match.body is not None and match.body.name == "Mars"


def test_they_are_one_body_on_different_surfaces():
    assert compatibility(NORTH, SOUTH) == Compatibility.SAME_BODY
    assert not sameCrs(NORTH, SOUTH)
    assert canTransform(NORTH, SOUTH) is None


def _project(crs: str, lons: np.ndarray, lats: np.ndarray) -> tuple:
    with PROJ_LOCK:
        projected = CRS.from_wkt(crs)
        toMap = Transformer.from_crs(projected.geodetic_crs, projected, always_xy=True)
        return toMap.transform(lons, lats)


def _unproject(crs: str, xs: np.ndarray, ys: np.ndarray) -> tuple:
    with PROJ_LOCK:
        projected = CRS.from_wkt(crs)
        toLonLat = Transformer.from_crs(
            projected, projected.geodetic_crs, always_xy=True
        )
        return toLonLat.transform(xs, ys)


def test_planetocentric_places_are_kept_between_them():
    # on a sphere the geodetic latitude is the planetocentric one
    lons = np.array([77.5, -120.0, 10.0, 0.0])
    lats = np.array([18.4, -40.0, 0.0, -75.0])
    xs, ys = _project(NORTH, lons, lats)
    moved = transformPoints(NORTH, SOUTH, xs, ys)
    assert not np.allclose(moved, (xs, ys))  # different grids
    np.testing.assert_allclose(_unproject(SOUTH, *moved), [lons, lats], atol=1e-9)
    back = transformPoints(SOUTH, NORTH, *moved)
    np.testing.assert_allclose(back, [xs, ys], atol=1e-6)


def test_each_is_described_with_its_own_radius():
    assert describeCrs(NORTH).surface == "sphere R = 3394.84 km"
    assert describeCrs(SOUTH).surface == "sphere R = 3392.50 km"
    assert describeCrs(SOUTH).text == (
        "Mars, sphere R = 3392.50 km, Equirectangular, center lat -40°"
    )
