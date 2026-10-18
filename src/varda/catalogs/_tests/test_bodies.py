"""Bodies: footprints in longitude and latitude, longitude conventions."""

import geopandas as gpd
import pytest
import shapely

from varda.catalogs.bodies import normalizeLongitude, toLongitudeLatitude
from varda.geo.bodies import BODIES
from varda.geo._tests.fixtures import CTX_DEM_PROJ, VESTA_IAU


def test_projected_footprints_become_degrees_on_the_bodys_iau_sphere():
    frame = gpd.GeoDataFrame(geometry=[shapely.Point(0.0, 0.0)], crs=CTX_DEM_PROJ)
    degrees = toLongitudeLatitude(frame)
    assert degrees.crs.is_geographic
    assert degrees.crs == BODIES["Mars"].sphereCrs()
    assert degrees.geometry[0].x == pytest.approx(77.5)


def test_geographic_footprints_are_taken_as_they_are():
    frame = gpd.GeoDataFrame(geometry=[shapely.Point(10.0, 5.0)], crs=VESTA_IAU)
    assert toLongitudeLatitude(frame) is frame


def test_footprints_on_an_unknown_body_are_an_error_not_earth():
    frame = gpd.GeoDataFrame(
        geometry=[shapely.Point(0.0, 0.0)],
        crs="+proj=eqc +R=100000 +units=m +no_defs",
    )
    with pytest.raises(ValueError, match="no known body"):
        toLongitudeLatitude(frame)


def test_mars_degrees_are_the_iau_2015_sphere():
    ellipsoid = BODIES["Mars"].sphereCrs().ellipsoid
    assert ellipsoid is not None and ellipsoid.semi_major_metre == 3_396_190.0


@pytest.mark.parametrize(
    ("raw", "expected"), [(0, 0), (77.5, 77.5), (282.5, -77.5), (360, 0), (-190, 170)]
)
def test_longitudes_run_from_minus_180_to_180(raw, expected):
    assert normalizeLongitude(raw) == pytest.approx(expected)
