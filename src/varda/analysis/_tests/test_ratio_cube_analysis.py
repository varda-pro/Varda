import tracemalloc

import numpy as np
import pytest

from varda.analysis import ratio_cube_analysis
from varda.analysis.ratio_cube_analysis import (
    BLANDNESS_GENERIC,
    RatioCubeAnalysis,
    RatioCubeMethod,
)
from varda.common.entities import VardaRaster
from varda.image_loading.crism_geometry import ColumnGeometry
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.data_sources.rasterio_data_source import RasterioDataSource
from varda.image_loading.raster_writer import writeRaster

QUIET = lambda percent, message: None  # noqa: E731
ROWS, COLS, BANDS = 40, 6, 30
PATH = "/data/frt0000_07_if165j_trr3.img"


def _geometry(rows: int = ROWS, cols: int = COLS) -> ColumnGeometry:
    sample = np.tile(np.arange(cols, dtype=np.float64), (rows, 1))
    return ColumnGeometry(ir_sample=sample)


def _scene(rows: int = ROWS, cols: int = COLS, gain=None) -> VardaRaster:
    """A flat spectrum with a gain per detector column."""
    rng = np.random.default_rng(0)
    wavelengths = np.linspace(1100, 2500, BANDS)
    gain = np.linspace(0.8, 1.2, cols) if gain is None else gain
    cube = np.full((rows, cols, BANDS), 0.3) * gain[None, :, None]
    cube = cube * (1 + 0.001 * rng.normal(size=cube.shape))
    source = ArrayDataSource(
        cube.astype(np.float32),
        wavelengths=wavelengths,
        wavelengthUnits="nm",
        filePath=PATH,
    )
    return VardaRaster(source, name="scene")


@pytest.fixture
def geometry(monkeypatch):
    geometry = _geometry()
    monkeypatch.setattr(
        ratio_cube_analysis, "loadColumnGeometry", lambda path: geometry
    )
    monkeypatch.setattr(
        ratio_cube_analysis,
        "resolveGeometryFile",
        lambda path: "/data/frt0000_07_de165j_trr3.img",
    )
    return geometry


def _run(image: VardaRaster, **settings) -> list[VardaRaster]:
    analysis = RatioCubeAnalysis()
    analysis.prepareFor(image)
    for name, value in settings.items():
        getattr(analysis, name).set(value)
    return analysis.runAll(image, QUIET)


def test_the_ratio_cube_keeps_the_inputs_bands_and_wavelengths(geometry):
    image = _scene()
    ratio, companion = _run(image)
    assert ratio.getData().shape == image.getData().shape
    assert np.allclose(ratio.wavelengths, image.wavelengths)
    # a per-column gain divides out: every pixel is ~1
    assert np.nanmax(np.abs(ratio.getData() - 1)) < 0.02
    assert companion.bandNames == ["Used as denominator", "Column denominator count"]


def test_the_companion_counts_match_the_record(geometry):
    ratio, companion = _run(_scene())
    record = ratio.extraMetadata["ratioCube"]
    data = companion.getData()
    assert len(record["columns"]) == COLS
    for entry in record["columns"]:
        column = entry["column"]
        assert np.all(data[:, column, 1] == entry["count"])
        assert data[:, column, 0].sum() <= entry["count"] or entry["fallback"]


def test_small_columns_fall_back_and_are_reported(geometry):
    ratio, _ = _run(_scene(), minimumPixels=ROWS + 1, neighborRadius=0)
    record = ratio.extraMetadata["ratioCube"]
    assert {c["fallback"] for c in record["columns"]} == {"whole column"}
    assert f"{COLS} of {COLS} columns" in ratio.description
    assert "whole column" in ratio.description


def test_a_short_column_borrows_from_its_neighbors_when_allowed(geometry):
    ratio, _ = _run(_scene(), minimumPixels=ROWS + 1, neighborRadius=1)
    record = ratio.extraMetadata["ratioCube"]
    assert {c["fallback"] for c in record["columns"]} == {"neighbors"}
    assert f"{COLS} of {COLS} columns borrowed" in ratio.description


def test_the_record_holds_the_settings_and_each_columns_denominator(geometry):
    ratio, _ = _run(_scene())
    record = ratio.extraMetadata["ratioCube"]
    assert record["method"] == "Bland pixels"
    assert record["blandness"] == BLANDNESS_GENERIC
    assert record["thresholds"]["maxDepth"] == 0.02
    assert record["runLength"] == 9 and record["minimumPixels"] == 20
    assert record["geometryFile"].endswith("de165j_trr3.img")
    first = record["columns"][0]
    assert len(first["spectrum"]) == BANDS == len(first["std"])
    assert first["spectrum"][0] == pytest.approx(0.3 * 0.8, rel=0.01)


def test_vnir_bands_are_flagged_as_approximately_column_matched(geometry):
    assert _run(_scene())[0].extraMetadata["ratioCube"]["vnirApproximate"] is False
    source = ArrayDataSource(
        np.full((ROWS, COLS, 4), 0.3, np.float32),
        wavelengths=np.array([500.0, 700.0, 1200.0, 1500.0]),
        wavelengthUnits="nm",
        filePath=PATH,
    )
    ratio, _ = _run(VardaRaster(source, name="vnir"))
    assert ratio.extraMetadata["ratioCube"]["vnirApproximate"] is True
    assert "VNIR" in ratio.description


def test_a_column_with_no_finite_pixels_is_flagged_not_silently_nan(geometry):
    image = _scene()
    cube = image.getData().copy()
    cube[:, 2, :] = np.nan
    source = ArrayDataSource(
        cube,
        wavelengths=np.asarray(image.wavelengths),
        wavelengthUnits="nm",
        filePath=PATH,
    )
    ratio, companion = _run(VardaRaster(source, name="gap"), neighborRadius=0)
    record = ratio.extraMetadata["ratioCube"]
    entry = next(c for c in record["columns"] if c["column"] == 2)
    assert entry["fallback"] == "no data"
    assert entry["count"] == 0
    assert np.all(np.isnan(ratio.getData()[:, 2, :]))
    assert "no data" in ratio.description
    assert np.all(companion.getData()[:, 2, 1] == 0)


def test_the_whole_column_method_uses_every_finite_pixel(geometry):
    ratio, companion = _run(_scene(), method=RatioCubeMethod.WHOLE_COLUMN)
    record = ratio.extraMetadata["ratioCube"]
    assert record["method"] == "Whole column"
    assert all(c["count"] == ROWS and c["fallback"] is None for c in record["columns"])
    assert np.all(companion.getData()[:, :, 0] == 1)


def test_without_a_backplane_the_reason_is_shown(monkeypatch):
    monkeypatch.setattr(ratio_cube_analysis, "loadColumnGeometry", lambda path: None)
    analysis = RatioCubeAnalysis()
    image = _scene()
    assert "backplane" in analysis.unavailableReason(image)
    with pytest.raises(ValueError, match="backplane"):
        analysis.runAll(image, QUIET)


def test_a_backplane_of_another_size_is_refused(monkeypatch):
    monkeypatch.setattr(
        ratio_cube_analysis, "loadColumnGeometry", lambda path: _geometry(ROWS + 1)
    )
    assert "size" in RatioCubeAnalysis().unavailableReason(_scene())


def test_an_image_without_a_file_has_no_backplane():
    source = ArrayDataSource(
        np.ones((4, 4, 5), np.float32), wavelengths=np.arange(5) * 100.0 + 1000
    )
    assert "file" in RatioCubeAnalysis().unavailableReason(
        VardaRaster(source, name="x")
    )


def test_fewer_than_three_bands_are_refused_clearly(geometry):
    source = ArrayDataSource(
        np.ones((ROWS, COLS, 2), np.float32),
        wavelengths=np.array([1200.0, 1500.0]),
        wavelengthUnits="nm",
        filePath=PATH,
    )
    reason = RatioCubeAnalysis().unavailableReason(VardaRaster(source, name="two"))
    assert "three good bands" in reason


def test_a_preset_without_hypyrameter_names_it(geometry, monkeypatch):
    monkeypatch.setattr(ratio_cube_analysis, "HYPYRAMETER_AVAILABLE", False)
    analysis = RatioCubeAnalysis()
    analysis.blandness.set(
        next(b for b in analysis.blandness.enumType if b.value == "Olivine")
    )
    reason = analysis.unavailableReason(_scene())
    assert "HyPyRameter" in reason
    with pytest.raises(ValueError, match="HyPyRameter"):
        analysis.runAll(_scene(), QUIET)
    # the Generic check works without it
    analysis.blandness.resetToDefault()
    assert analysis.unavailableReason(_scene()) == ""


def test_parameter_ranges_guard_the_pooling_settings():
    analysis = RatioCubeAnalysis()
    assert analysis.minimumPixels.range == (1, 10000)
    assert analysis.neighborRadius.range == (0, 10)
    assert analysis.runLength.range == (3, 51) and analysis.runLength.step == 2


def test_out_of_range_settings_are_refused(geometry):
    analysis = RatioCubeAnalysis()
    analysis.runLength.value = 4  # bypassing the widget's clamp
    assert "odd" in analysis.unavailableReason(_scene())
    analysis.runLength.value = 9
    analysis.minimumPixels.value = 0
    assert "Minimum" in analysis.unavailableReason(_scene())


def test_the_record_survives_a_geotiff_round_trip(geometry, tmp_path):
    ratio, _ = _run(_scene())
    path = writeRaster(ratio, tmp_path / "ratio.tif")
    reread = VardaRaster(RasterioDataSource(str(path)), name="ratio")
    record = reread.extraMetadata["ratioCube"]
    assert record == ratio.extraMetadata["ratioCube"]
    assert len(record["columns"]) == COLS


def test_the_memory_estimate_bounds_a_run_with_a_backplane(monkeypatch):
    """The registry-wide memory test has no backplane to read; this one gives
    the run a synthetic one, on a scene big enough for the per-pixel cost to
    dominate the per-column records."""
    rows, cols = 120, 100
    geometry = _geometry(rows, cols)
    monkeypatch.setattr(
        ratio_cube_analysis, "loadColumnGeometry", lambda path: geometry
    )
    monkeypatch.setattr(ratio_cube_analysis, "resolveGeometryFile", lambda path: None)
    analysis = RatioCubeAnalysis()
    image = _scene(rows, cols)
    analysis.prepareFor(image)
    analysis.runAll(image, QUIET)  # warm up
    tracemalloc.start()
    try:
        analysis.runAll(image, QUIET)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= analysis.estimateMemory(image)
