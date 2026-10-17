"""Shadow masks from brightness and from the terrain and the sun."""

import time
import tracemalloc

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio import CRS

from varda.analysis.analysis import AnalysisContext
from varda.analysis.shadow_mask import (
    ShadowMaskAnalysis,
    ShadowMethod,
    ThresholdKind,
    brightnessBands,
    castShadow,
    growMask,
    solarGeometry,
)
from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

QUIET = lambda percent, message: None  # noqa: E731
PIXEL = 10.0  # meters
UTM = CRS.from_epsg(32612)
NORTH_UP = Affine(PIXEL, 0.0, 500000.0, 0.0, -PIXEL, 4000000.0)


def _raster(values: np.ndarray, name: str = "image", **kwargs) -> VardaRaster:
    if values.ndim == 2:
        values = values[:, :, None]
    return VardaRaster(ArrayDataSource(values.astype(np.float32), **kwargs), name=name)


def _onGrid(values: np.ndarray, name: str, transform=NORTH_UP) -> VardaRaster:
    return _raster(values, name, transform=transform, crs=UTM)


# --- brute-force references ------------------------------------------------------


def _bruteOnTheRay(z: np.ndarray, azimuth: float, sunElevation: float) -> np.ndarray:
    """Cast shadow from first principles, for square pixels and a sun along a
    row, a column or a diagonal (the ray then passes through cell centers):
    a pixel is shadowed when any cell exactly on its ray toward the sun rises
    above the ray."""
    rows, cols = z.shape
    sunEast, sunNorth = np.sin(np.radians(azimuth)), np.cos(np.radians(azimuth))
    tan = np.tan(np.radians(sunElevation))
    out = np.zeros(z.shape, bool)
    for r in range(rows):
        for c in range(cols):
            for rq in range(rows):
                for cq in range(cols):
                    east, north = (cq - c) * PIXEL, (r - rq) * PIXEL
                    along = east * sunEast + north * sunNorth
                    across = abs(east * sunNorth - north * sunEast)
                    if (
                        along > 0
                        and across < 1e-6
                        and z[rq, cq] > z[r, c] + along * tan
                    ):
                        out[r, c] = True
    return out


def _bilinear(z: np.ndarray, y: float, x: float) -> float:
    r0, c0 = int(np.floor(y)), int(np.floor(x))
    r1, c1 = min(r0 + 1, z.shape[0] - 1), min(c0 + 1, z.shape[1] - 1)
    fy, fx = y - r0, x - c0
    return (
        z[r0, c0] * (1 - fy) * (1 - fx)
        + z[r0, c1] * (1 - fy) * fx
        + z[r1, c0] * fy * (1 - fx)
        + z[r1, c1] * fy * fx
    )


def _bruteFineMarch(z: np.ndarray, azimuth: float, sunElevation: float) -> np.ndarray:
    """Cast shadow by marching in steps of 1/20 pixel along any azimuth."""
    rows, cols = z.shape
    dCol, dRow = np.sin(np.radians(azimuth)) / 20, -np.cos(np.radians(azimuth)) / 20
    rise = PIXEL / 20 * np.tan(np.radians(sunElevation))
    out = np.zeros(z.shape, bool)
    for r in range(rows):
        for c in range(cols):
            k = 1
            while True:
                y, x = r + k * dRow, c + k * dCol
                if not (0 <= y <= rows - 1 and 0 <= x <= cols - 1):
                    break
                if _bilinear(z, y, x) > z[r, c] + k * rise:
                    out[r, c] = True
                    break
                k += 1
    return out


def _crater(size: int = 21, radius: float = 7.0, depth: float = 40.0) -> np.ndarray:
    rows, cols = np.mgrid[0:size, 0:size].astype(float)
    r = np.hypot(rows - size // 2, cols - size // 2)
    rim = 10.0 * np.exp(-(((r - radius) / 1.5) ** 2))
    return np.where(r < radius, depth * ((r / radius) ** 2 - 1), 0.0) + rim


def _wall(height: float = 45.0) -> np.ndarray:
    z = np.zeros((9, 20))
    z[:, 12] = height  # a one-pixel north-south wall
    return z


# --- cast shadow -----------------------------------------------------------------


def test_a_wall_casts_a_shadow_exactly_as_long_as_it_should():
    # 45 m high, sun 45° up in the east: the shadow reaches 45 m west
    shadow = castShadow(_wall(), 90.0, 45.0, PIXEL, PIXEL)
    expected = np.zeros_like(shadow)
    expected[:, 8:12] = True  # 10-40 m from the wall; 50 m is lit
    np.testing.assert_array_equal(shadow, expected)
    # a lower sun casts a longer shadow: 45 m / tan 30° = 77.9 m
    longer = castShadow(_wall(), 90.0, 30.0, PIXEL, PIXEL)
    assert longer[0].nonzero()[0].tolist() == list(range(5, 12))  # 10-70 m
    # and from the west, the shadow falls east
    west = castShadow(_wall(), 270.0, 45.0, PIXEL, PIXEL)
    assert west[0].nonzero()[0].tolist() == [13, 14, 15, 16]


@pytest.mark.parametrize(
    "azimuth", [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0]
)
@pytest.mark.parametrize("elevation", [15.0, 35.0])
def test_cast_shadow_matches_the_brute_force_reference(azimuth, elevation):
    z = _crater()
    np.testing.assert_array_equal(
        castShadow(z, azimuth, elevation, PIXEL, PIXEL),
        _bruteOnTheRay(z, azimuth, elevation),
    )


@pytest.mark.parametrize("azimuth", [20.0, 110.0, 250.0, 333.0])
def test_cast_shadow_at_any_azimuth_differs_only_on_shadow_edges(azimuth):
    """Off the grid's axes and diagonals the ray falls between cell centers:
    the one-pixel steps and a fine march may disagree only at the edges."""
    z = _crater(31, 10.0, 60.0)
    shadow = castShadow(z, azimuth, 25.0, PIXEL, PIXEL)
    reference = _bruteFineMarch(z, azimuth, 25.0)
    differ = shadow != reference
    assert differ.mean() < 0.02
    edges = growMask(reference, 1) & growMask(~reference, 1)
    assert (differ <= edges).all()
    assert reference.sum() > 20  # the test sees real shadows


def test_the_grid_orientation_sets_where_the_sun_is():
    # a south-up grid: columns run west, so the eastern sun is on the left
    flipped = castShadow(
        _wall(), 90.0, 45.0, PIXEL, PIXEL, eastSign=-1.0, northSign=-1.0
    )
    assert flipped[0].nonzero()[0].tolist() == [13, 14, 15, 16]


def test_a_1000_pixel_dem_takes_seconds():
    rng = np.random.default_rng(0)
    z = np.cumsum(rng.normal(size=(1000, 1000)), axis=1) * 5
    castShadow(z[:50, :50], 120.0, 10.0, PIXEL, PIXEL)  # compile
    start = time.perf_counter()
    castShadow(z, 120.0, 10.0, PIXEL, PIXEL)
    assert time.perf_counter() - start < 10.0


def test_the_buffer_grows_a_square():
    mask = np.zeros((7, 7), bool)
    mask[3, 3] = True
    grown = growMask(mask, 1)
    assert grown.sum() == 9 and grown[2:5, 2:5].all()
    assert growMask(mask, 0) is mask


# --- brightness ------------------------------------------------------------------


def _ramp() -> VardaRaster:
    return _raster(np.arange(100.0).reshape(10, 10), "ramp")


def _run(analysis: ShadowMaskAnalysis, image: VardaRaster, images=()) -> VardaRaster:
    analysis.setContext(AnalysisContext(images=[image, *images]))
    analysis.prepareFor(image)
    return analysis.run(image, QUIET)


def test_a_percentile_threshold_marks_the_darkest_pixels():
    result = _run(ShadowMaskAnalysis(), _ramp())
    mask = result.getData()[:, :, 0]
    assert mask.sum() == 5 and mask.flat[:5].all()  # values 0-4
    record = result.extraMetadata["shadowMask"]["brightness"]
    assert record["percentile"] == 5.0
    assert record["threshold"] == pytest.approx(4.95)  # what it resolved to
    assert result.bandNames == ["Shadow mask", "Brightness"]
    np.testing.assert_array_equal(result.getData()[:, :, 1], _ramp().getData()[..., 0])


def test_an_absolute_threshold_is_in_data_units():
    analysis = ShadowMaskAnalysis()
    analysis.thresholdKind.set(ThresholdKind.ABSOLUTE)
    analysis.absolute.set(10.0)
    result = _run(analysis, _ramp())
    assert result.getData()[:, :, 0].sum() == 10  # 0-9 are below 10
    assert result.extraMetadata["shadowMask"]["brightness"]["threshold"] == 10.0


def test_brightness_is_the_mean_over_the_wavelength_range():
    cube = np.zeros((4, 4, 4))
    cube[..., 0], cube[..., 1], cube[..., 2], cube[..., 3] = 1.0, 2.0, 4.0, 9.0
    wavelengths = np.array([1000.0, 1300.0, 1350.0, 2000.0])
    image = _raster(cube, wavelengths=wavelengths, wavelengthUnits="nm")
    assert brightnessBands(image, 1280, 1380).tolist() == [1, 2]
    result = _run(ShadowMaskAnalysis(), image)  # the default, 1280-1380 nm
    assert np.allclose(result.getData()[:, :, 1], 3.0)
    assert result.extraMetadata["shadowMask"]["brightness"]["bands"] == [2, 3]
    # one band: a range around it with no band inside picks the nearest
    assert brightnessBands(image, 1990, 1995).tolist() == [3]


def test_without_the_default_window_every_good_band_is_used():
    cube = np.dstack([np.full((4, 4), v) for v in (1.0, 2.0, 6.0)])
    image = _raster(
        cube, wavelengths=np.array([500.0, 600.0, 700.0]), wavelengthUnits="nm"
    )
    analysis = ShadowMaskAnalysis()
    result = _run(analysis, image)
    assert (analysis.minWavelength.get(), analysis.maxWavelength.get()) == (
        500.0,
        700.0,
    )
    assert np.allclose(result.getData()[:, :, 1], 3.0)


def test_no_data_stays_no_data():
    values = np.arange(100.0).reshape(10, 10)
    values[9, 9] = -1.0
    image = _raster(values, nodata=-1.0)
    mask = _run(ShadowMaskAnalysis(), image).getData()[:, :, 0]
    assert np.isnan(mask[9, 9]) and np.isfinite(mask[:9]).all()


# --- topographic -----------------------------------------------------------------


def _topographic(dem: VardaRaster, azimuth=90.0, elevation=45.0, **values):
    analysis = ShadowMaskAnalysis()
    analysis.method.set(ShadowMethod.TOPOGRAPHIC)
    image = _onGrid(np.ones(dem.getData().shape[:2]), "scene")
    analysis.setContext(AnalysisContext(images=[image, dem]))
    analysis.prepareFor(image)
    analysis.dem.set(dem)
    analysis.sunAzimuth.set(azimuth)
    analysis.sunElevation.set(elevation)
    for name, value in values.items():
        getattr(analysis, name).set(value)
    return analysis, image


def test_topographic_shadow_is_self_and_cast_shadow():
    dem = _onGrid(_wall(), "dem")
    analysis, image = _topographic(dem)
    result = analysis.run(image, QUIET)
    mask = result.getData()[:, :, 0]
    cosine = result.getData()[:, :, 1]
    assert result.bandNames == ["Shadow mask", "cos(i)"]
    assert mask[:, 8:12].all()  # cast (and the wall's west face)
    assert not mask[:, :7].any() and not mask[:, 13:].any()
    # flat ground: cos(i) = sin(elevation)
    assert np.allclose(cosine[:, :6], np.sin(np.radians(45.0)), atol=1e-6)
    assert (cosine[:, 11] <= 0).all()  # the face turned away from the sun
    record = result.extraMetadata["shadowMask"]["topographic"]
    assert record["castShadowPixels"] == 36
    assert record["selfShadowPixels"] == 9
    assert record["sunAzimuthSource"] == "entered"


def test_the_z_factor_puts_heights_in_the_pixel_units():
    dem = _onGrid(_wall(0.045), "dem")  # 45 m written in kilometers
    analysis, image = _topographic(dem, zFactor=1000.0)
    record = analysis.run(image, QUIET).extraMetadata["shadowMask"]["topographic"]
    assert record["castShadowPixels"] == 36


def test_both_is_the_union_and_the_buffer_grows_it():
    dem = _onGrid(_wall(), "dem")
    analysis, image = _topographic(dem, buffer=1)
    analysis.method.set(ShadowMethod.BOTH)
    analysis.thresholdKind.set(ThresholdKind.ABSOLUTE)
    analysis.absolute.set(0.5)  # nothing is that dark
    result = analysis.run(image, QUIET)
    mask = result.getData()[:, :, 0]
    assert mask[:, 7:13].all() and mask.sum() == 9 * 6  # 8-11, grown by one
    assert result.bandNames == ["Shadow mask", "Brightness", "cos(i)"]
    assert result.extraMetadata["shadowMask"]["bufferAddedPixels"] == 18


def test_the_topographic_method_needs_a_dem_and_the_sun():
    image = _onGrid(np.ones((9, 20)), "scene")
    dem = _onGrid(_wall(), "dem")
    analysis = ShadowMaskAnalysis()
    analysis.method.set(ShadowMethod.TOPOGRAPHIC)
    analysis.setContext(AnalysisContext(images=[image]))
    analysis.prepareFor(image)
    assert "DEM" in analysis.unavailableReason(image)
    analysis.setContext(AnalysisContext(images=[image, dem]))
    analysis.prepareFor(image)
    analysis.dem.set(dem)
    assert "azimuth or elevation" in analysis.unavailableReason(image)
    with pytest.raises(ValueError):
        analysis.run(image, QUIET)
    analysis.sunAzimuth.set(90.0)
    analysis.sunElevation.set(45.0)
    assert analysis.unavailableReason(image) == ""


def test_sun_angles_come_from_the_metadata():
    image = _raster(
        np.ones((4, 4)),
        extraMetadata={"Sun Azimuth": "135.5 <deg>", "INCIDENCE_ANGLE": 60},
    )
    angles = solarGeometry(image)
    assert angles.azimuth == 135.5 and angles.elevation == pytest.approx(30.0)
    assert "Sun Azimuth" in angles.azimuthSource
    assert "incidence" in angles.elevationSource
    analysis = ShadowMaskAnalysis()
    analysis.method.set(ShadowMethod.TOPOGRAPHIC)
    analysis.prepareFor(image)
    assert analysis.sunAzimuth.get() == 135.5
    assert analysis.sunSources["azimuth"].startswith("image metadata")


def test_the_sun_elevation_comes_from_a_crism_ddr(tmp_path):
    ddr = tmp_path / "frt00003e12_07_in166j_trr3.img"
    ina = np.full((4, 4), 60.0, np.float32)
    ina[0, 0] = 58.0
    ina[3, 3] = 65535.0  # CRISM's missing value
    with rasterio.open(
        ddr, "w", driver="GTiff", height=4, width=4, count=2, dtype="float32"
    ) as out:
        out.write(ina, 1)
        out.write(np.zeros((4, 4), np.float32), 2)
        out.set_band_description(1, "INA at areoid, deg")
        out.set_band_description(2, "EMA at areoid, deg")
    image = _raster(
        np.ones((4, 4)), filePath=str(tmp_path / "frt00003e12_07_if166j_trr3.img")
    )
    angles = solarGeometry(image)
    assert angles.azimuth is None
    assert angles.elevation == pytest.approx(90.0 - (58.0 + 14 * 60.0) / 15)
    assert "DDR" in angles.elevationSource and "58.00–60.00°" in angles.elevationSource


def test_geographic_dems_are_refused():
    dem = _raster(
        _wall(),
        "dem",
        transform=Affine(0.01, 0, 0, 0, -0.01, 0),
        crs=CRS.from_epsg(4326),
    )
    image = _raster(np.ones((9, 20)), transform=dem.transform, crs=dem.crs)
    analysis = ShadowMaskAnalysis()
    analysis.method.set(ShadowMethod.TOPOGRAPHIC)
    analysis.setContext(AnalysisContext(images=[image, dem]))
    analysis.prepareFor(image)
    analysis.dem.set(dem)
    assert "degrees" in analysis.unavailableReason(image)


def test_the_memory_estimate_bounds_the_topographic_method():
    """The registry-wide memory test runs the default (brightness) method; the
    DEM's planes are counted here, on a one-band image that is its own DEM."""
    rng = np.random.default_rng(1)
    dem = _onGrid(np.cumsum(rng.normal(size=(120, 160)), axis=1) * 5, "dem")
    analysis = ShadowMaskAnalysis()
    analysis.method.set(ShadowMethod.BOTH)
    analysis.buffer.set(3)
    analysis.setContext(AnalysisContext(images=[dem]))
    analysis.prepareFor(dem)
    analysis.dem.set(dem)
    analysis.sunAzimuth.set(120.0)
    analysis.sunElevation.set(20.0)
    analysis.run(dem, QUIET)  # warm up (compiles the ray march)
    tracemalloc.start()
    try:
        analysis.run(dem, QUIET)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= analysis.estimateMemory(dem)
