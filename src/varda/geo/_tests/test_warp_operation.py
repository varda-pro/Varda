"""The PROJ pipeline GDAL's warper gets between surfaces of one body does
what ``transformPoints`` does (planetocentric positions kept); none on one
surface; other bodies are refused."""

import numpy as np
import pytest
from pyproj import CRS, Transformer

from varda.geo._tests.fixtures import (
    CRISM_MTRDR_WKT,
    CTX_DEM_PROJ,
    HIRISE_POLAR_PROJ,
    WGS84,
)
from varda.geo.transform import _latitudeFirst as _crsLatitudeFirst
from varda.geo.transform import (
    gdalWarp,
    isGeographic,
    transformPoints,
    warpOperation,
    warpOptions,
)

MARS_SPHERE = "+proj=longlat +R=3396190 +no_defs"


def _gridOn(crs: str, south: float, north: float) -> tuple[np.ndarray, np.ndarray]:
    """A 10 × 10 grid of Mars longitudes and latitudes, as points on ``crs``."""
    lons, lats = np.meshgrid(np.linspace(-170, 170, 10), np.linspace(south, north, 10))
    return transformPoints(MARS_SPHERE, crs, lons.ravel(), lats.ravel())


def _latitudeFirst(crs: str) -> bool:
    return _crsLatitudeFirst(CRS.from_user_input(crs))


@pytest.mark.parametrize(
    ("src", "dst", "south", "north"),
    [
        ("IAU_2015:49910", "IAU_2015:49912", -80, 80),
        ("IAU_2015:49911", "IAU_2015:49910", -80, 80),
        (CRISM_MTRDR_WKT, "IAU_2015:49912", -80, 80),
        (HIRISE_POLAR_PROJ, "IAU_2015:49901", 60, 89),  # west, ographic, lat first
        # one surface, but GDAL's warper reads 49902's planetocentric
        # latitude as geodetic, so it gets the pipeline too
        ("IAU_2015:49902", "IAU_2015:49912", -80, 80),
        ("IAU_2015:49901", "IAU_2015:49902", -80, 80),
        # PROJ's "Transformation from unknown to unknown", not "ballpark"
        (CRISM_MTRDR_WKT, "+proj=eqc +a=3396190 +b=3376200 +over +units=m", -80, 80),
    ],
)
def test_pipeline_matches_transformPoints(src, dst, south, north):
    xs, ys = _gridOn(src, south, north)
    operation = warpOperation(src, dst)
    assert operation is not None and "geoc" in operation
    expected = transformPoints(src, dst, xs, ys)
    # the pipeline works in the CRSs' own axis order
    native = (ys, xs) if _latitudeFirst(src) else (xs, ys)
    first, second = Transformer.from_pipeline(operation).transform(*native)
    got = (second, first) if _latitudeFirst(dst) else (first, second)
    # 1e-6 m, in degrees on a geographic target (~59 km per degree of Mars)
    tolerance = 1e-6 / 59000 if CRS.from_user_input(dst).is_geographic else 1e-6
    np.testing.assert_allclose(got, expected, rtol=0, atol=tolerance)
    assert warpOptions(src, dst) == {"COORDINATE_OPERATION": operation}


def test_no_pipeline_on_one_surface():
    assert warpOperation(CTX_DEM_PROJ, "IAU_2015:49910") is None
    assert warpOperation("EPSG:4326", "EPSG:3857") is None
    assert warpOptions(CTX_DEM_PROJ, "IAU_2015:49910") == {}


def test_other_body_raises():
    with pytest.raises(ValueError, match="Earth.*Mars|Mars.*Earth"):
        warpOperation("IAU_2015:49910", WGS84)


def test_gdal_gets_x_first_crss():
    warp = gdalWarp("+proj=longlat +R=3396190 +no_defs", "IAU_2015:49901")
    axes = CRS.from_wkt(warp.dstCrs).axis_info
    assert [a.direction for a in axes] == ["west", "north"]
    assert warp.operation.endswith("+step +proj=axisswap +order=2,1")
    same = gdalWarp(CTX_DEM_PROJ, "IAU_2015:49910")
    assert same.options == {} and same.operation == "PROJ"


def test_planetocentric_ellipsoid_is_geographic():
    assert not CRS.from_user_input("IAU_2015:49902").is_geographic
    assert isGeographic("IAU_2015:49902") and isGeographic("IAU_2015:49901")
    assert not isGeographic("IAU_2015:49912")
