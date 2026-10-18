"""Which images the analyses' dropdowns offer, why the others aren't, and
inputs on another pixel grid resampled onto the image's (the reported bug:
a 2-band Shadow Mask missing from Apply Mask, a co-registered CTX DEM missing
from the Shadow Mask's DEM dropdown)."""

import numpy as np
from affine import Affine
from rasterio import CRS
from rasterio.enums import Resampling
from rasterio.warp import transform as warpTransform

from varda.analysis.analysis import AnalysisContext
from varda.analysis.fatt_map import FattMapAnalysis
from varda.analysis.image_math import (
    ApplyMaskAnalysis,
    ImageArithmeticAnalysis,
    MaskMode,
)
from varda.analysis.shadow_mask import ShadowMaskAnalysis, ShadowMethod
from varda.analysis.spectral_library import LibrarySpectrum
from varda.analysis.transforms import PcaAnalysis
from varda.common.entities import VardaRaster
from varda.common.grid import onGridOf
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

QUIET = lambda percent, message: None  # noqa: E731
UTM = CRS.from_epsg(32612)
OTHER_UTM = CRS.from_epsg(32613)
X0, Y0 = 500000.0, 4000000.0
CRISM_GRID = Affine(18.0, 0.0, X0, 0.0, -18.0, Y0)
CTX_GRID = Affine(6.0, 0.0, X0, 0.0, -6.0, Y0)
ROWS, COLS = 12, 16
WAVELENGTHS = np.linspace(1000.0, 2600.0, 30)


def _raster(values, name, transform=CRISM_GRID, crs=UTM, **kwargs) -> VardaRaster:
    values = np.asarray(values, np.float32)
    if values.ndim == 2:
        values = values[:, :, None]
    return VardaRaster(
        ArrayDataSource(values, transform=transform, crs=crs, **kwargs), name=name
    )


def _crism() -> VardaRaster:
    rng = np.random.default_rng(0)
    cube = 0.2 + 0.01 * rng.normal(size=(ROWS, COLS, 30))
    return _raster(cube, "crism_iovf", wavelengths=WAVELENGTHS, wavelengthUnits="nm")


def _shadowMask() -> VardaRaster:
    """Like the Shadow Mask's output: the mask, then the brightness."""
    mask = np.zeros((ROWS, COLS))
    mask[:, :4] = 1.0
    brightness = np.full((ROWS, COLS), 0.3)
    return _raster(np.stack([mask, brightness], axis=2), "crism shadow mask")


def _ridge(rows: int, cols: int, pixel: float) -> np.ndarray:
    """A north-south ridge 60 m high, its crest 120 m east of the left edge."""
    x = (np.arange(cols) + 0.5) * pixel
    return np.tile(60.0 * np.exp(-(((x - 120.0) / 40.0) ** 2)), (rows, 1))


def _ctxDem() -> VardaRaster:
    return _raster(_ridge(ROWS * 3, COLS * 3, 6.0), "ctx_dem", transform=CTX_GRID)


def _zone13Dem() -> VardaRaster:
    (x,), (y,) = warpTransform(UTM, OTHER_UTM, [X0 - 60], [Y0 + 60])
    return _raster(
        np.zeros((60, 70)),
        "dem_zone13",
        transform=Affine(6.0, 0.0, x, 0.0, -6.0, y),
        crs=OTHER_UTM,
    )


def _prepared(analysis, image, *others):
    analysis.setContext(AnalysisContext(images=[image, *others]))
    analysis.prepareFor(image)
    return analysis


# --- the reported bug ------------------------------------------------------------


def test_apply_mask_offers_a_two_band_shadow_mask():
    crism, shadow = _crism(), _shadowMask()
    analysis = _prepared(ApplyMaskAnalysis(), crism, shadow, _ctxDem())
    assert shadow in analysis.mask.imageProvider()
    assert analysis.unavailableReason(crism) == ""


def test_the_shadow_mask_offers_co_registered_dems_but_not_a_mask():
    crism, shadow, ctx, zone13 = _crism(), _shadowMask(), _ctxDem(), _zone13Dem()
    photo = VardaRaster(ArrayDataSource(np.zeros((5, 5, 1), np.float32)), "photo")
    analysis = ShadowMaskAnalysis()
    analysis.method.set(ShadowMethod.TOPOGRAPHIC)
    _prepared(analysis, crism, shadow, ctx, zone13, photo)
    offered = analysis.dem.imageProvider()
    assert ctx in offered and zone13 in offered
    assert shadow not in offered and crism not in offered
    note = analysis.dem.explain()
    assert "crism shadow mask (2 bands; this needs 1)" in note
    assert "photo (not georeferenced, can't be placed on this image)" in note
    assert "crism_iovf" not in note  # the image itself isn't listed


# --- masks: any band count, the first band, any overlapping grid ----------------


def test_apply_mask_uses_the_first_band_of_a_two_band_mask():
    crism, shadow = _crism(), _shadowMask()
    analysis = _prepared(ApplyMaskAnalysis(), crism, shadow)
    analysis.mask.set(shadow)
    analysis.mode.set(MaskMode.KEEP_UNSET)
    out = analysis.run(crism, QUIET).getData()
    assert np.isnan(out[:, :4]).all()  # band 1 is set there
    np.testing.assert_allclose(out[:, 4:], crism.getData()[:, 4:])


def test_apply_mask_resamples_a_mask_on_another_grid_by_nearest_neighbor():
    crism = _crism()
    fine = np.zeros((ROWS * 3, COLS * 3))
    fine[:, : 4 * 3] = 1.0
    mask = _raster(fine, "ctx mask", transform=CTX_GRID)
    analysis = _prepared(ApplyMaskAnalysis(), crism, mask)
    assert mask in analysis.mask.imageProvider()
    analysis.mask.set(mask)
    result = analysis.run(crism, QUIET)
    out = result.getData()
    assert np.isnan(out[:, 4:]).all()
    np.testing.assert_allclose(out[:, :4], crism.getData()[:, :4])
    assert "ctx mask resampled from 6.0 m to 18.0 m pixels (nearest)" in (
        result.description
    )


def test_fatt_exclude_takes_a_resampled_mask():
    crism = _crism()
    fine = np.zeros((ROWS * 3, COLS * 3))
    fine[:9, :9] = 1.0  # 3 × 3 CRISM pixels
    mask = _raster(fine, "ctx shadow", transform=CTX_GRID)
    analysis = FattMapAnalysis([LibrarySpectrum("flat", WAVELENGTHS, np.ones(30))])
    analysis.minWavelength.set(1000.0)
    analysis.maxWavelength.set(2600.0)
    analysis.factors.set(2)
    _prepared(analysis, crism, mask)
    assert mask in analysis.exclude.imageProvider()
    analysis.exclude.set(mask)
    excluded, counts = analysis._exclusions(
        crism, np.ones((ROWS, COLS)), np.ones((ROWS, COLS), bool)
    )
    np.testing.assert_array_equal(
        excluded, onGridOf(mask, crism, Resampling.nearest, [0])[..., 0] != 0
    )
    assert counts["excludedByMask"] == 9
    assert counts["excludeResampling"]["method"] == "nearest"


# --- the DEM, resampled ----------------------------------------------------------


def _topographic(image, dem, *others):
    analysis = ShadowMaskAnalysis()
    analysis.method.set(ShadowMethod.TOPOGRAPHIC)
    _prepared(analysis, image, dem, *others)
    analysis.dem.set(dem)
    analysis.sunAzimuth.set(90.0)
    analysis.sunElevation.set(20.0)
    return analysis


def test_a_finer_dem_gives_what_the_dem_averaged_onto_the_grid_gives():
    crism, ctx = _crism(), _ctxDem()
    result = _topographic(crism, ctx).run(crism, QUIET)
    averaged = _raster(onGridOf(ctx, crism, Resampling.average), "averaged")
    expected = _topographic(crism, averaged).run(crism, QUIET)
    # the same shadow; cos(i) to float32 (the pre-resampled DEM is stored so)
    np.testing.assert_array_equal(result.getData()[..., 0], expected.getData()[..., 0])
    np.testing.assert_allclose(result.getData(), expected.getData(), atol=1e-6)
    assert result.getData()[:, :, 0].any()  # the ridge casts a shadow
    record = result.extraMetadata["shadowMask"]["topographic"]
    assert record["demResampling"]["method"] == "average"
    assert record["demResampling"]["fromPixelSize"] == "6.0 m"
    assert record["pixelSize"] == [18.0, 18.0]
    assert "ctx_dem resampled from 6.0 m to 18.0 m pixels (average)" in (
        result.description
    )
    assert expected.extraMetadata["shadowMask"]["topographic"]["demResampling"] is None


def test_a_coarser_dem_is_resampled_bilinearly():
    fine = _raster(np.ones((ROWS * 3, COLS * 3)), "hirise", transform=CTX_GRID)
    coarse = _raster(_ridge(ROWS, COLS, 18.0), "coarse_dem")
    record = (_topographic(fine, coarse).run(fine, QUIET).extraMetadata["shadowMask"])[
        "topographic"
    ]
    assert record["demResampling"]["method"] == "bilinear"


# --- exact-grid dropdowns explain what they leave out --------------------------


def test_image_arithmetic_still_needs_the_same_grid_and_says_so():
    crism, shadow, ctx = _crism(), _shadowMask(), _ctxDem()
    analysis = _prepared(ImageArithmeticAnalysis(), crism, shadow, ctx)
    assert analysis.second.imageProvider() == []
    note = analysis.second.explain()
    assert "crism shadow mask (2 bands; this needs 1 or 30)" in note
    assert (
        "ctx_dem (different pixel grid: 6.0 m vs 18.0 m pixels; this needs the "
        "same grid)"
    ) in note


def test_pca_weights_still_need_the_same_grid_and_say_so():
    crism, ctx = _crism(), _ctxDem()
    analysis = _prepared(PcaAnalysis(), crism, ctx)
    assert analysis.weights.imageProvider() == []
    assert "ctx_dem (different pixel grid" in analysis.weights.explain()
