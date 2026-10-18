"""sameCrs, compatibility and canTransform: CRSs that are the same under
other names are the same; different bodies are never combined."""

import pytest
from pyproj import CRS

from varda.geo._tests.fixtures import (
    CRISM_MTRDR_ENVI_WKT,
    CRISM_MTRDR_WKT,
    CTX_DEM_PROJ,
    MARS_EQC_PDS3_WKT,
    WGS84,
)
from varda.geo.describe import describeCrs
from varda.geo.equivalence import (
    Compatibility,
    canTransform,
    compatibility,
    sameCrs,
)

# CRISM_MTRDR_WKT as a PROJ string, as the Ames Stereo Pipeline writes it
CRISM_MTRDR_PROJ = (
    "+proj=eqc +lat_ts=15 +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 "
    "+R=3394839.81331631 +units=m +no_defs"
)


def test_gdal_wkt_equals_iau_code():
    # PROJ alone says these differ: only the names do
    assert not CRS(MARS_EQC_PDS3_WKT).equals(CRS("IAU_2015:49910"))
    assert sameCrs(MARS_EQC_PDS3_WKT, "IAU_2015:49910")
    assert compatibility(MARS_EQC_PDS3_WKT, "IAU_2015:49910") == (
        Compatibility.EQUIVALENT
    )
    assert canTransform(MARS_EQC_PDS3_WKT, "IAU_2015:49910") is None


def test_gdal_pds3_and_envi_readings_of_one_product_are_the_same():
    assert not CRS(CRISM_MTRDR_WKT).equals(CRS(CRISM_MTRDR_ENVI_WKT))
    assert compatibility(CRISM_MTRDR_WKT, CRISM_MTRDR_ENVI_WKT) == (
        Compatibility.EQUIVALENT
    )


def test_proj_string_and_wkt():
    assert sameCrs(CRISM_MTRDR_WKT, CRISM_MTRDR_PROJ)
    assert sameCrs(MARS_EQC_PDS3_WKT, "+proj=eqc +R=3396190 +units=m")
    assert compatibility(CRISM_MTRDR_WKT, CRISM_MTRDR_PROJ) == Compatibility.SAME


def test_same_text_is_same():
    assert compatibility(CTX_DEM_PROJ, CTX_DEM_PROJ) == Compatibility.SAME
    assert compatibility(CRS(CTX_DEM_PROJ), CTX_DEM_PROJ) == Compatibility.SAME


def test_lengths_are_compared_to_a_millimeter():
    # the CRISM radius rounded to 1 cm differs by 3.3 mm
    rounded = "+proj=eqc +lat_ts=15 +R=3394839.81 +units=m +no_defs"
    assert compatibility(CRISM_MTRDR_WKT, rounded) == Compatibility.SAME_BODY


def test_different_standard_parallel_is_not_same():
    other = CTX_DEM_PROJ.replace("+lat_ts=18.4", "+lat_ts=0")
    assert not sameCrs(CTX_DEM_PROJ, other)
    assert compatibility(CTX_DEM_PROJ, other) == Compatibility.SAME_BODY
    assert canTransform(CTX_DEM_PROJ, other) is None


def test_different_radius_is_not_same():
    a = "+proj=eqc +R=3396190 +units=m +no_defs"
    b = "+proj=eqc +R=3389500 +units=m +no_defs"
    assert not sameCrs(a, b)
    assert compatibility(a, b) == Compatibility.SAME_BODY


def test_a_proj_string_matches_its_iau_code():
    degrees = "+proj=eqc +lon_0=180 +R=3396190 +units=m"
    assert sameCrs(degrees, "IAU_2015:49915")


def test_axis_order_matters():
    # IAU_2015:49900 is latitude, longitude; the PROJ string longitude, latitude
    assert not sameCrs("+proj=longlat +R=3396190 +no_defs", "IAU_2015:49900")
    assert (
        compatibility("+proj=longlat +R=3396190 +no_defs", "IAU_2015:49900")
        == Compatibility.SAME_BODY
    )


def test_west_positive_is_not_east_positive():
    assert compatibility("IAU_2015:49901", "IAU_2015:49900") == (
        Compatibility.SAME_BODY
    )


def test_mars_vs_wgs84_is_different_body():
    assert compatibility(CRISM_MTRDR_WKT, WGS84) == Compatibility.DIFFERENT_BODY
    assert canTransform(CRISM_MTRDR_WKT, WGS84) == (
        "Mars and Earth are different bodies"
    )


def test_missing_crs_is_unknown():
    assert sameCrs(None, None)
    assert not sameCrs(None, WGS84) and not sameCrs(WGS84, None)
    assert compatibility(None, WGS84) == Compatibility.UNKNOWN
    assert compatibility(WGS84, None) == Compatibility.UNKNOWN
    assert compatibility(None, None) == Compatibility.UNKNOWN
    assert canTransform(None, WGS84) == "The first image has no coordinate system"
    assert canTransform(WGS84, None) == "The second image has no coordinate system"


def test_unreadable_crs_is_unknown():
    assert compatibility("not a CRS", WGS84) == Compatibility.UNKNOWN
    assert not sameCrs("not a CRS", WGS84)
    assert canTransform("not a CRS", WGS84) == (
        "The first image's coordinate system is unreadable"
    )


def test_unknown_bodies_compare_radii():
    a = "+proj=longlat +R=3000000 +no_defs"
    b = "+proj=eqc +R=3000000 +units=m +no_defs"
    c = "+proj=eqc +R=3000500 +units=m +no_defs"
    assert compatibility(a, b) == Compatibility.SAME_BODY
    assert compatibility(a, c) == Compatibility.DIFFERENT_BODY
    assert canTransform(a, c) == (
        "An unknown body (sphere R = 3000.00 km) and "
        "an unknown body (sphere R = 3000.50 km) are different bodies"
    )


def test_an_unknown_body_never_matches_a_known_one_by_name():
    # 3000 km is no body Varda knows; Mars is 3396.19 km
    assert compatibility("+proj=longlat +R=3000000", CRISM_MTRDR_WKT) == (
        Compatibility.DIFFERENT_BODY
    )


def test_a_local_crs_has_no_body():
    local = 'LOCAL_CS["arbitrary",UNIT["metre",1],AXIS["x",EAST],AXIS["y",NORTH]]'
    assert compatibility(local, local) == Compatibility.SAME
    assert compatibility(local, WGS84) == Compatibility.UNKNOWN
    assert canTransform(local, WGS84) == (
        "The first image's coordinate system is on no body"
    )


@pytest.mark.parametrize(
    ("crs", "code"),
    [
        (MARS_EQC_PDS3_WKT, "IAU_2015:49910"),
        ("+proj=eqc +lon_0=180 +R=3396190 +units=m", "IAU_2015:49915"),
    ],
)
def test_describe_finds_iau_code(crs, code):
    assert describeCrs(crs).authority == code


def test_describe_finds_no_code_for_a_local_radius():
    assert describeCrs(CRISM_MTRDR_WKT).authority is None
