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
    resamplingRecord,
    resamplingText,
    sameGridChoices,
)
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
