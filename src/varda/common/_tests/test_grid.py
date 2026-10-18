"""Image choices (same grid, or placeable by map location, with why the rest
aren't offered) and ``onGridOf``, which resamples one image onto another's
grid."""

import numpy as np
import pytest
from affine import Affine
from rasterio import CRS
from rasterio.enums import Resampling
from rasterio.warp import transform as warpTransform

from varda.common.entities import VardaRaster
from varda.common.grid import (
    demResampling,
    onGridOf,
    pixelSizeText,
    placeableChoices,
    placementProblem,
    resamplingRecord,
    resamplingText,
    sameGrid,
    sameGridChoices,
)
from varda.geo._tests.fixtures import (
    CRISM_MTRDR_SOUTH_WKT,
    CRISM_MTRDR_WKT,
    MARS_EQC_PDS3_WKT,
    WGS84,
    makeImage,
)
from varda.geo.transform import transformPoints, warpOptions
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

UTM = CRS.from_epsg(32612)
OTHER_UTM = CRS.from_epsg(32613)  # the next zone east: a different projection
X0, Y0 = 500000.0, 4000000.0
COARSE = Affine(18.0, 0.0, X0, 0.0, -18.0, Y0)
FINE = Affine(6.0, 0.0, X0, 0.0, -6.0, Y0)


def _raster(values, name, transform=COARSE, crs=UTM, nodata=None) -> VardaRaster:
    values = np.asarray(values, np.float32)
    if values.ndim == 2:
        values = values[:, :, None]
    return VardaRaster(
        ArrayDataSource(values, transform=transform, crs=crs, nodata=nodata),
        name=name,
    )


def _scene():
    """A 30-band CRISM-like cube, a 2-band shadow mask on its grid, a DEM on
    a 3× finer grid over the same ground, the same DEM in the next UTM zone,
    an unreferenced image and one far away."""
    crism = _raster(np.ones((10, 12, 30)), "crism")
    shadow = _raster(np.zeros((10, 12, 2)), "shadow mask")
    dem = _raster(np.zeros((30, 36)), "ctx_dem", transform=FINE)
    # the CTX DEM's corner, in the next zone: same ground, another projection
    (x,), (y,) = warpTransform(UTM, OTHER_UTM, [X0], [Y0])
    otherDem = _raster(
        np.zeros((30, 36)),
        "dem_zone13",
        transform=Affine(6.0, 0.0, x, 0.0, -6.0, y),
        crs=OTHER_UTM,
    )
    plain = VardaRaster(ArrayDataSource(np.zeros((5, 5, 1), np.float32)), "photo")
    far = _raster(
        np.zeros((10, 12)), "far", transform=Affine(18.0, 0, X0 + 1e5, 0, -18.0, Y0)
    )
    return crism, shadow, dem, otherDem, plain, far


def test_same_grid_choices_explain_what_is_left_out():
    crism, shadow, dem, otherDem, plain, far = _scene()
    images = [crism, shadow, dem, otherDem, plain, far]
    choices = sameGridChoices(images, crism, bandCounts=(1, 30))
    assert choices.images == []
    reasons = {e.image.name: e.reason for e in choices.excluded}
    assert reasons["shadow mask"] == "2 bands; this needs 1 or 30"
    assert reasons["ctx_dem"] == (
        "different pixel grid: 6.0 m vs 18.0 m pixels; this needs the same grid"
    )
    assert "different map projection" in reasons["dem_zone13"]
    assert "not georeferenced" in reasons["photo"]
    assert "10 × 12" not in reasons["far"] and "shifted" in reasons["far"]
    assert choices.note.startswith("Not listed: ")
    assert "ctx_dem (different pixel grid: 6.0 m vs 18.0 m" in choices.note


def test_placeable_choices_take_any_overlapping_georeferenced_image():
    crism, shadow, dem, otherDem, plain, far = _scene()
    choices = placeableChoices([crism, shadow, dem, otherDem, plain, far], crism)
    assert choices.images == [shadow, dem, otherDem]
    reasons = {e.image.name: e.reason for e in choices.excluded}
    assert reasons == {
        "photo": "not georeferenced, can't be placed on this image",
        "far": "doesn't overlap this image",
    }
    dems = placeableChoices([crism, shadow, dem], crism, bandCounts=(1,))
    assert dems.images == [dem]
    assert dems.excluded[0].reason == "2 bands; this needs 1"


def test_same_grid_is_read_as_it_is():
    crism, *_ = _scene()
    values = np.random.default_rng(0).normal(size=(10, 12, 2)).astype(np.float32)
    mask = _raster(values, "mask")
    np.testing.assert_array_equal(onGridOf(mask, crism), values.astype(np.float64))
    assert resamplingText(mask, crism, Resampling.nearest) == ""
    assert resamplingRecord(mask, crism, Resampling.nearest) is None


def test_nearest_keeps_a_mask_zero_or_one():
    crism, *_ = _scene()
    rng = np.random.default_rng(1)
    fine = _raster(rng.integers(0, 2, (30, 36)), "mask", transform=FINE)
    out = onGridOf(fine, crism, Resampling.nearest)[..., 0]
    assert set(np.unique(out)) <= {0.0, 1.0}
    # nearest takes the fine pixel under each coarse pixel's center
    np.testing.assert_array_equal(out, fine.getData()[1::3, 1::3, 0])


def test_average_of_a_3x_finer_dem_is_the_block_means():
    crism, *_ = _scene()
    heights = np.random.default_rng(2).normal(size=(30, 36)) * 100
    dem = _raster(heights, "ctx_dem", transform=FINE)
    assert demResampling(dem, crism) is Resampling.average
    out = onGridOf(dem, crism, Resampling.average)[..., 0]
    blocks = heights.astype(np.float32).reshape(10, 3, 12, 3).mean(axis=(1, 3))
    np.testing.assert_allclose(out, blocks, rtol=1e-5, atol=1e-3)
    assert resamplingText(dem, crism, Resampling.average) == (
        "ctx_dem resampled from 6.0 m to 18.0 m pixels (average)"
    )


def test_bilinear_when_upsampling():
    fine = _raster(np.zeros((30, 36)), "target", transform=FINE)
    # a plane rising 1 per meter eastward, on the coarse grid
    cols = np.arange(12) * 18.0 + 9.0
    coarse = _raster(np.tile(cols, (10, 1)), "dem")
    assert demResampling(coarse, fine) is Resampling.bilinear
    out = onGridOf(coarse, fine, Resampling.bilinear)[..., 0]
    expected = np.arange(36) * 6.0 + 3.0
    # inside the coarse centers a plane is reproduced exactly
    np.testing.assert_allclose(out[3:-3, 3:-3], np.tile(expected, (30, 1))[3:-3, 3:-3])


def test_a_different_projection_lands_in_the_right_place():
    crism, *_ = _scene()
    # a DEM in the next zone, with one hot pixel at a known ground point
    groundX, groundY = X0 + 18.0 * 5 + 9.0, Y0 - 18.0 * 4 - 9.0  # pixel (4, 5)
    (x,), (y,) = warpTransform(UTM, OTHER_UTM, [groundX], [groundY])
    transform = Affine(3.0, 0.0, x - 300.0, 0.0, -3.0, y + 300.0)
    heights = np.zeros((200, 200))
    heights[95:105, 95:105] = 1.0  # 30 m square around the point
    dem = _raster(heights, "dem_zone13", transform=transform, crs=OTHER_UTM)
    out = onGridOf(dem, crism, Resampling.nearest)[..., 0]
    assert out[4, 5] == 1.0
    assert np.nansum(out) <= 4  # only around that point
    assert "reprojected and resampled" in resamplingText(dem, crism, Resampling.nearest)
    record = resamplingRecord(dem, crism, Resampling.nearest)
    assert record is not None and record["fromCrs"] == "EPSG:32613"


def test_outside_the_source_and_its_no_data_are_nan():
    crism, *_ = _scene()
    # a DEM covering only the left half, with a no-data value in it
    heights = np.full((30, 18), 5.0)
    heights[:3, :3] = -9999.0
    dem = _raster(heights, "half", transform=FINE, nodata=-9999.0)
    out = onGridOf(dem, crism, Resampling.average)[..., 0]
    assert np.isnan(out[:, 6:]).all()
    assert np.isnan(out[0, 0])  # all of its source pixels are no data
    np.testing.assert_allclose(out[1:, :6], 5.0)


def test_images_that_cant_be_placed_raise():
    crism, shadow, dem, otherDem, plain, far = _scene()
    with pytest.raises(ValueError, match="doesn't overlap"):
        onGridOf(far, crism)


def test_pixel_size_text_names_the_crs_units():
    assert pixelSizeText(_raster(np.zeros((2, 2)), "utm")) == "18.0 m"
    km = CRS.from_user_input("+proj=eqc +R=3396190 +units=km +no_defs")
    image = _raster(np.zeros((2, 2)), "km", Affine(0.5, 0, 0, 0, -0.5, 0), km)
    assert pixelSizeText(image) == "0.5 km"
    degrees = _raster(
        np.zeros((2, 2)), "deg", Affine(0.01, 0, 0, 0, -0.01, 0), CRS.from_epsg(4326)
    )
    assert pixelSizeText(degrees) == "0.01°"


def test_pixel_size_text_of_a_local_crs():
    local = CRS.from_user_input(
        'LOCAL_CS["arbitrary",UNIT["metre",1],AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
    )
    image = _raster(np.zeros((2, 2)), "local", Affine(2.0, 0, 0, 0, -2.0, 0), local)
    assert pixelSizeText(image) == "2.0 m"


# GDAL's PDS3 form and the IAU code of one projection: names differ, geometry not
PDS3_EQC = CRS.from_wkt(MARS_EQC_PDS3_WKT)
IAU_EQC = CRS.from_user_input("IAU_2015:49910")
MARS_DEGREES = CRS.from_user_input("+proj=longlat +R=3396190 +no_defs")


def test_equivalent_projections_are_one_grid():
    data = np.zeros((4, 4))
    assert sameGrid(
        _raster(data, "gdal", crs=PDS3_EQC), _raster(data, "asp", crs=IAU_EQC)
    )


def test_images_on_different_bodies_are_never_placed():
    earth = _raster(
        np.zeros((4, 4)),
        "earth",
        Affine(0.01, 0, 10, 0, -0.01, 5),
        CRS.from_user_input(WGS84),
    )
    mars = _raster(
        np.zeros((8, 8)), "mars", Affine(0.005, 0, 10, 0, -0.005, 5), MARS_DEGREES
    )
    assert placementProblem(earth, mars) == "Earth and Mars are different bodies"


def test_longitudes_0_to_360_and_minus_180_to_180_overlap():
    values = np.arange(100, dtype=np.float32).reshape(10, 10)
    east = _raster(values, "0-360", Affine(0.1, 0, 257.0, 0, -0.1, 5.0), MARS_DEGREES)
    west = _raster(
        np.zeros((10, 10)),
        "-180-180",
        Affine(0.1, 0, -103.0, 0, -0.1, 5.0),
        MARS_DEGREES,
    )
    assert placementProblem(east, west) is None
    assert placementProblem(west, east) is None
    placed = onGridOf(east, west)[..., 0]
    assert np.nansum(placed) == pytest.approx(values.sum())
    np.testing.assert_array_equal(placed, values)


def test_two_crism_scenes_need_reprojecting_not_one_grid():
    # each MTRDR is on the sphere of Mars's local radius under its own scene
    north = makeImage(CRISM_MTRDR_WKT, centerLonLat=(10.0, -40.0), pixelSize=18.0)
    south = makeImage(CRISM_MTRDR_SOUTH_WKT, centerLonLat=(10.0, -40.0), pixelSize=18.0)
    southCrs = CRS.from_wkt(CRISM_MTRDR_SOUTH_WKT)
    sameNumbers = _raster(np.zeros((64, 64)), "south", north.transform, southCrs)
    assert not sameGrid(north, sameNumbers)  # same transform, other surface
    assert not sameGrid(north, south)
    assert placementProblem(south, north) is None  # reprojected onto it


# the Mars sphere in degrees: 100 × 100 pixels of 0.01°, the marker pixel's
# center at (77.505°E, 44.995°N), row 50, column 50
SPHERE_DEGREES = CRS.from_user_input("+proj=longlat +R=3396190 +no_defs")
MARKER_LON_LAT = (77.505, 44.995)


def _markerScene() -> tuple[VardaRaster, VardaRaster]:
    """The sphere image with its marker, and a 100 × 100, 500 m grid on the
    Mars ellipsoid (``IAU_2015:49912``, planetocentric) whose pixel (50, 50)
    is centered where ``transformPoints`` puts the marker."""
    values = np.zeros((100, 100), np.float32)
    values[50, 50] = 1.0
    source = _raster(
        values, "sphere", Affine(0.01, 0, 77.0, 0, -0.01, 45.5), SPHERE_DEGREES
    )
    ellipsoid = CRS.from_user_input("IAU_2015:49912")
    (x,), (y,) = transformPoints(
        SPHERE_DEGREES, ellipsoid, [MARKER_LON_LAT[0]], [MARKER_LON_LAT[1]]
    )
    transform = Affine(500.0, 0, x - 50.5 * 500.0, 0, -500.0, y + 50.5 * 500.0)
    target = _raster(np.zeros((100, 100)), "ellipsoid", transform, ellipsoid)
    return source, target


def test_onGridOf_keeps_planetocentric_latitude():
    source, target = _markerScene()
    out = onGridOf(source, target, Resampling.nearest)[..., 0]
    rows, cols = np.nonzero(out == 1.0)
    assert rows.size > 0, "the marker fell off the grid"
    # GDAL's ballpark put it 40 rows (20 km) south
    assert abs(rows.mean() - 50) <= 0.5 and abs(cols.mean() - 50) <= 0.5


def test_resampling_record_names_the_operation():
    source, target = _markerScene()
    record = resamplingRecord(source, target, Resampling.nearest)
    assert record is not None and "geoc" in record["coordinateOperation"]
    crism, _, dem, *_ = _scene()
    same = resamplingRecord(dem, crism, Resampling.average)
    assert same is not None and same["coordinateOperation"] == "PROJ"


def test_onGridOf_onto_a_lon_wrap_ellipsoid_keeps_planetocentric_latitude():
    """PROJ names this pair's operation "Transformation from unknown to
    unknown", not a ballpark, yet it keeps the latitude's number too."""
    source, _ = _markerScene()
    wrapped = CRS.from_user_input(
        "+proj=longlat +a=3396190 +b=3376200 +lon_wrap=180 +no_defs"
    )
    assert warpOptions(SPHERE_DEGREES, wrapped)
    (lon,), (lat,) = transformPoints(
        SPHERE_DEGREES, wrapped, [MARKER_LON_LAT[0]], [MARKER_LON_LAT[1]]
    )
    transform = Affine(0.01, 0, lon - 0.505, 0, -0.01, lat + 0.505)
    target = _raster(np.zeros((100, 100)), "wrapped", transform, wrapped)
    out = onGridOf(source, target, Resampling.nearest)[..., 0]
    rows, cols = np.nonzero(out == 1.0)
    assert rows.size > 0, "the marker fell off the grid"
    # PROJ's latitude-keeping operation put it 34 rows (0.34°) south
    assert abs(rows.mean() - 50) <= 0.5 and abs(cols.mean() - 50) <= 0.5
