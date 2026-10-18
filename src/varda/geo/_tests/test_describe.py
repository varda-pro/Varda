"""describeCrs: each fixture's coordinate system in words a scientist
recognizes, and the fields behind them."""

import math

import pytest
from affine import Affine
from pyproj import CRS

from varda.geo._tests.fixtures import (
    CRISM_MTRDR_ENVI_WKT,
    CRISM_MTRDR_WKT,
    CTX_DEM_PROJ,
    HIRISE_POLAR_PROJ,
    LRO_WAC_PROJ,
    LRO_WAC_SOUTH_POLAR_PROJ,
    MOLA_MEGDR_PROJ,
    VESTA_IAU,
    WGS84,
    makeImage,
)
from varda.geo.describe import describeCrs


def _described(crs: str, centerLonLat: tuple[float, float], pixelSize: float):
    image = makeImage(crs, centerLonLat=centerLonLat, pixelSize=pixelSize)
    return describeCrs(image.crs, image.transform, image.width, image.height)


@pytest.mark.parametrize("crs", [CRISM_MTRDR_WKT, CRISM_MTRDR_ENVI_WKT])
def test_crism_mtrdr(crs):
    description = _described(crs, (77.0, 15.0), 18.0)
    assert description.text == (
        "Mars, sphere R = 3394.84 km, Equirectangular, center lat 15°, 18 m/px"
    )
    assert description.kind == "projected" and description.units == "m"
    assert description.latitudeType == "planetocentric"
    assert description.longitudeDirection == "east"
    assert description.longitudeDomain == "-180..180"
    assert description.centerLatitude == 15 and description.centerLongitude == 0
    assert description.pixelSizeM == pytest.approx(18.0)
    assert description.authority is None


def test_hirise_polar():
    description = _described(HIRISE_POLAR_PROJ, (0.0, 85.0), 1.0)
    assert description.text == (
        "Mars, sphere R = 3376.20 km, Polar Stereographic (north), 1 m/px"
    )
    assert description.centerLatitude == 90


def test_south_polar_stereographic():
    description = describeCrs(LRO_WAC_SOUTH_POLAR_PROJ)
    assert (
        description.text == "Moon, sphere R = 1737.40 km, Polar Stereographic (south)"
    )


def test_lro_wac():
    description = _described(LRO_WAC_PROJ, (10.0, 5.0), 100.0)
    assert description.text == (
        "Moon, sphere R = 1737.40 km, Equirectangular, center lat 0°, 100 m/px"
    )


def test_ctx_dem_names_its_central_meridian():
    description = _described(CTX_DEM_PROJ, (77.5, 18.4), 20.0)
    assert description.text == (
        "Mars, sphere R = 3396.19 km, Equirectangular, center lat 18.4°, "
        "lon_0 77.5°, 20 m/px"
    )


def test_wgs84_is_earth_on_its_ellipsoid():
    description = describeCrs(WGS84)
    assert description.text == (
        "Earth, ellipsoid a = 6378.14, c = 6356.75 km, Geographic (lon/lat)"
    )
    assert description.kind == "geographic" and description.units == "°"
    assert description.latitudeType == "planetographic"
    assert description.authority == "EPSG:4326"
    assert description.centerLatitude is None


def test_mola_megdr_in_0_to_360():
    transform = Affine(1 / 128, 0.0, 0.0, 0.0, -1 / 128, 90.0)
    description = describeCrs(MOLA_MEGDR_PROJ, transform, 46080, 23040)
    assert description.longitudeDomain == "0..360"
    assert description.pixelSizeM == pytest.approx(math.pi * 3396000 / 180 / 128)
    assert description.text == (
        "Mars, sphere R = 3396.00 km, Geographic (lon/lat), 463 m/px"
    )


def test_a_geographic_image_west_of_180_is_minus_180_to_180():
    transform = Affine(0.01, 0.0, -10.0, 0.0, -0.01, 10.0)
    assert describeCrs(MOLA_MEGDR_PROJ, transform, 100, 100).longitudeDomain == (
        "-180..180"
    )


def test_a_projection_centered_on_180_is_0_to_360():
    assert describeCrs("IAU_2015:49915").longitudeDomain == "0..360"


def test_mars_planetographic_is_west_positive():
    description = describeCrs("IAU_2015:49901")
    assert description.longitudeDirection == "west"
    assert description.latitudeType == "planetographic"
    assert description.surface == "ellipsoid a = 3396.19, c = 3376.20 km"
    assert description.body == "Mars"
    assert description.authority == "IAU_2015:49901"


def test_vesta_is_vesta():
    assert describeCrs(VESTA_IAU).text.startswith("Vesta, sphere R = 255.00 km")


def test_no_crs():
    description = describeCrs(None)
    assert description.text == "No coordinate system (pixel coordinates)"
    assert description.kind == "none" and description.body is None


def test_an_unknown_body_is_named_as_unknown():
    description = describeCrs("+proj=longlat +R=3000000 +no_defs")
    assert description.body is None
    assert description.text == (
        "Unknown body (sphere R = 3000.00 km), Geographic (lon/lat)"
    )


def test_an_equivalent_authority_code_is_found():
    assert describeCrs("+proj=longlat +R=3396190 +no_defs").authority == (
        "IAU_2015:49900"
    )
    assert describeCrs("IAU_2015:49910").authority == "IAU_2015:49910"


def test_kilometre_units_scale_the_ground_size():
    crs = "+proj=eqc +lat_ts=0 +lon_0=0 +R=3396190 +units=km +no_defs"
    description = describeCrs(crs, Affine(0.5, 0, 0, 0, -0.5, 0), 10, 10)
    assert description.units == "km"
    assert description.pixelSizeM == pytest.approx(500.0)


def test_strings_and_crs_objects_agree():
    assert describeCrs(CRS.from_user_input(LRO_WAC_PROJ)) == describeCrs(LRO_WAC_PROJ)
