"""Points moved between coordinate systems of one body keep their
planetocentric longitude and latitude, whatever each surface and latitude
type; equivalent CRSs are the identity; other bodies are refused."""

import math

import numpy as np
import pytest
from pyproj import Transformer

from varda.geo._tests.fixtures import (
    CRISM_MTRDR_WKT,
    CTX_DEM_PROJ,
    MARS_EQC_PDS3_WKT,
    WGS84,
)
from varda.geo.transform import eastLonLat, geodeticLonLat, transformPoints
from varda.utilities.proj_lock import PROJ_LOCK

MARS_SPHERE = "+proj=longlat +R=3396190 +no_defs"
OGRAPHIC = "IAU_2015:49901"  # Mars ellipsoid, planetographic, west-positive
OCENTRIC = "IAU_2015:49902"  # Mars ellipsoid, planetocentric
A, C = 3396190.0, 3376200.0  # Mars IAU 2015 ellipsoid


def _ographic(centric: float) -> float:
    """Snyder (1987) eq. 3-28: tan φg = (a/c)² tan φc."""
    return math.degrees(math.atan((A / C) ** 2 * math.tan(math.radians(centric))))


def test_sphere_to_planetocentric_ellipsoid_keeps_the_latitude():
    lon, lat = transformPoints(MARS_SPHERE, OCENTRIC, [77.5], [45.0])
    np.testing.assert_allclose([lon[0], lat[0]], [77.5, 45.0], atol=1e-9)


def test_sphere_to_planetographic_west_positive_converts_the_latitude():
    lon, lat = transformPoints(MARS_SPHERE, OGRAPHIC, [77.5], [45.0])
    assert lat[0] == pytest.approx(_ographic(45.0), abs=1e-9)
    assert lat[0] == pytest.approx(45.34, abs=0.01)
    assert (-lon[0]) % 360 == pytest.approx(77.5, abs=1e-9)  # west-positive


def test_the_round_trip_is_exact():
    lons, lats = np.array([77.5, -120.0, 10.0]), np.array([45.0, -60.0, 0.0])
    there = transformPoints(MARS_SPHERE, OGRAPHIC, lons, lats)
    back = transformPoints(OGRAPHIC, MARS_SPHERE, *there)
    np.testing.assert_allclose(back, [lons, lats], atol=1e-9)
    projected = transformPoints(MARS_SPHERE, CTX_DEM_PROJ, lons, lats)
    viaCrism = transformPoints(CTX_DEM_PROJ, CRISM_MTRDR_WKT, *projected)
    np.testing.assert_allclose(
        transformPoints(CRISM_MTRDR_WKT, MARS_SPHERE, *viaCrism),
        [lons, lats],
        atol=1e-9,
    )


def test_a_projected_sphere_to_a_planetographic_ellipsoid():
    x, y = transformPoints(MARS_SPHERE, CTX_DEM_PROJ, [77.5], [30.0])
    lon, lat = transformPoints(CTX_DEM_PROJ, OGRAPHIC, x, y)
    assert lat[0] == pytest.approx(_ographic(30.0), abs=1e-9)
    assert (-lon[0]) % 360 == pytest.approx(77.5, abs=1e-9)


def test_equivalent_crss_are_the_identity():
    xs, ys = np.array([1234.5]), np.array([-678.25])
    x, y = transformPoints(MARS_EQC_PDS3_WKT, "IAU_2015:49910", xs, ys)
    assert (x[0], y[0]) == (1234.5, -678.25)


def test_other_bodies_and_missing_crss_are_refused():
    with pytest.raises(ValueError, match="Earth and Mars are different bodies"):
        transformPoints(WGS84, MARS_SPHERE, [0.0], [0.0])
    with pytest.raises(ValueError, match="no coordinate system"):
        transformPoints(None, MARS_SPHERE, [0.0], [0.0])


def test_earth_keeps_projs_own_operation_and_x_is_longitude():
    """EPSG:4326 lists latitude first; x is still longitude."""
    lon, lat = transformPoints("EPSG:32612", WGS84, [500000.0], [4000000.0])
    assert lon[0] == pytest.approx(-111.0, abs=1e-9)
    assert lat[0] == pytest.approx(36.14, abs=0.01)


# PROJ-string CRSs whose operation PROJ names "Transformation from unknown
# to unknown" (not "ballpark"), though it keeps the latitude's number too
WRAPPED_ELLIPSOID = "+proj=longlat +a=3396190 +b=3376200 +lon_wrap=180 +no_defs"
OVER_ELLIPSOID = "+proj=eqc +a=3396190 +b=3376200 +over +units=m +no_defs"


def test_sphere_to_a_lon_wrap_ellipsoid_converts_the_latitude():
    lon, lat = transformPoints(MARS_SPHERE, WRAPPED_ELLIPSOID, [200.0], [45.0])
    assert lat[0] == pytest.approx(_ographic(45.0), abs=1e-9)
    assert lat[0] == pytest.approx(45.338, abs=1e-3)
    assert lon[0] % 360 == pytest.approx(200.0, abs=1e-9)


def test_a_lon_wrap_ellipsoid_to_the_sphere_converts_back():
    lon, lat = transformPoints(WRAPPED_ELLIPSOID, MARS_SPHERE, [200.0], [_ographic(45)])
    assert lat[0] == pytest.approx(45.0, abs=1e-9)
    assert lon[0] % 360 == pytest.approx(200.0, abs=1e-9)


def test_sphere_to_a_projected_over_ellipsoid_converts_the_latitude():
    plain = "+proj=eqc +a=3396190 +b=3376200 +units=m +no_defs"
    over = transformPoints(MARS_SPHERE, OVER_ELLIPSOID, [77.5], [45.0])
    np.testing.assert_allclose(
        over, transformPoints(MARS_SPHERE, plain, [77.5], [45.0]), atol=1e-6
    )
    back = transformPoints(OVER_ELLIPSOID, MARS_SPHERE, *over)
    np.testing.assert_allclose(back, [[77.5], [45.0]], atol=1e-9)


def test_earth_datum_shifts_stay_projs():
    """A real transformation between two Earth datums is PROJ's, not the
    planetocentric route (NAD27 → WGS 84 moves points tens of meters)."""
    ours = transformPoints("EPSG:4267", WGS84, [-100.0], [40.0])
    with PROJ_LOCK:
        theirs = Transformer.from_crs("EPSG:4267", WGS84, always_xy=True).transform(
            -100.0, 40.0
        )
    np.testing.assert_allclose(ours, [[theirs[0]], [theirs[1]]], atol=1e-9)


@pytest.mark.parametrize(
    "code, expected",
    [
        ("EPSG:4326", (77.5, 18.4)),  # latitude first
        ("IAU_2015:49901", (-77.5, 18.4)),  # west-positive, planetographic
        ("IAU_2015:49902", (77.5, 18.4)),  # spherical, planetocentric
    ],
)
def test_east_lon_lat_reads_degrees_in_the_crs_own_latitude_type(code, expected):
    x, y = transformPoints(eastLonLat(code), code, [77.5], [18.4])
    assert (x[0], y[0]) == pytest.approx(expected, abs=1e-9)


def test_geodetic_lon_lat_is_east_and_geodetic_on_the_crs_datum():
    # 18.4° planetocentric on the Mars ellipsoid is 18.68° planetographic
    x, y = transformPoints(
        "IAU_2015:49902", geodeticLonLat("IAU_2015:49902"), [77.5], [18.4]
    )
    assert x[0] == pytest.approx(77.5, abs=1e-9)
    a, c = 3396190.0, 3376200.0
    expected = math.degrees(math.atan((a / c) ** 2 * math.tan(math.radians(18.4))))
    assert y[0] == pytest.approx(expected, abs=1e-9)
