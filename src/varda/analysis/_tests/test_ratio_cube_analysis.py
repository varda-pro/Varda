import csv
import tracemalloc

import attrs
import numpy as np
import pytest

from varda.analysis import ratio_cube_analysis
from varda.analysis.blandness import PRESETS
from varda.analysis.ratio_cube_analysis import (
    BLANDNESS_GENERIC,
    RatioCubeAnalysis,
    RatioCubeMethod,
    formatPresetLimits,
    parsePresetLimits,
)
from varda.common.entities import VardaRaster
from varda.image_loading.crism_geometry import ColumnGeometry
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.data_sources.rasterio_data_source import RasterioDataSource
from varda.image_loading.raster_writer import writeRaster

QUIET = lambda percent, message: None  # noqa: E731
ROWS, COLS, BANDS = 40, 6, 30
PATH = "/data/frt0000_07_if165j_mtr3.img"
BACKPLANE = "/data/frt0000_07_in165j_mtr3.img"


def _geometry(rows: int = ROWS, cols: int = COLS) -> ColumnGeometry:
    sample = np.tile(np.arange(cols, dtype=np.float64), (rows, 1))
    return ColumnGeometry(ir_sample=sample, source=BACKPLANE, sampleBand="IR Sample")


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
    monkeypatch.setattr(ratio_cube_analysis, "columnGeometryFor", lambda *_: geometry)
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
        assert entry["fallback"] is None
        assert np.all(data[:, column, 1] == entry["count"])
        # a column with enough bland pixels pools exactly its bland pixels
        assert data[:, column, 0].sum() == entry["count"]


def _absorbing(column: int) -> VardaRaster:
    """The scene with a deep absorption in one column (never bland)."""
    image = _scene()
    cube = image.getData().astype(np.float64)
    wavelengths = np.asarray(image.wavelengths)
    cube[:, column, :] *= 1 - 0.5 * np.exp(-0.5 * ((wavelengths - 2000) / 60) ** 2)
    source = ArrayDataSource(
        cube.astype(np.float32),
        wavelengths=wavelengths,
        wavelengthUnits="nm",
        filePath=PATH,
    )
    return VardaRaster(source, name="absorbing")


def test_the_companion_mask_shows_the_pixels_borrowed_from_neighbors(geometry):
    ratio, companion = _run(_absorbing(2))
    data = companion.getData()
    entry = next(
        c for c in ratio.extraMetadata["ratioCube"]["columns"] if c["column"] == 2
    )
    assert entry["fallback"] == "neighbors" and entry["count"] >= 20
    assert not data[:, 2, 0].any()  # none of column 2's own pixels were pooled
    assert data[:, 1, 0].all()  # its neighbor's were, for column 2 and itself
    assert np.all(data[:, 2, 1] == entry["count"])


def test_the_companion_mask_shows_the_whole_column_fallback(geometry):
    ratio, companion = _run(_absorbing(2), neighborRadius=0)
    data = companion.getData()
    entry = next(
        c for c in ratio.extraMetadata["ratioCube"]["columns"] if c["column"] == 2
    )
    assert entry["fallback"] == "whole column" and entry["count"] == ROWS
    assert data[:, 2, 0].all()  # every pixel of the column was pooled
    assert data[:, :, 0].sum() == ROWS * COLS
    assert np.all(data[:, 2, 1] == ROWS)


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
    assert record["geometryFile"] == BACKPLANE
    assert record["geometry"] == "backplane band 'IR Sample'"
    first = record["columns"][0]
    assert len(first["spectrum"]) == BANDS == len(first["std"])
    assert first["spectrum"][0] == pytest.approx(0.3 * 0.8, rel=0.01)


def _cube(wavelengths, cols: int = COLS, path: str | None = PATH) -> VardaRaster:
    source = ArrayDataSource(
        np.full((ROWS, cols, len(wavelengths)), 0.3, np.float32),
        wavelengths=np.asarray(wavelengths, dtype=np.float64),
        wavelengthUnits="nm",
        filePath=path,
    )
    return VardaRaster(source, name="cube")


def test_vnir_bands_are_flagged_as_approximately_column_matched(geometry):
    assert _run(_scene())[0].extraMetadata["ratioCube"]["vnirApproximate"] is False
    ratio, _ = _run(_cube([500.0, 700.0, 1200.0, 1500.0]))
    assert ratio.extraMetadata["ratioCube"]["vnirApproximate"] is True
    assert "VNIR" in ratio.description


def test_a_vnir_only_cube_is_not_flagged(geometry):
    ratio, _ = _run(_cube([450.0, 600.0, 750.0, 900.0]))
    assert ratio.extraMetadata["ratioCube"]["vnirApproximate"] is False
    assert "VNIR" not in ratio.description


def test_an_unprojected_crism_cube_without_a_backplane_uses_its_image_columns():
    ratio, _ = _run(_cube(np.linspace(1100, 2500, 6), cols=320, path=None))
    record = ratio.extraMetadata["ratioCube"]
    assert record["geometry"] == "image columns (unprojected)"
    assert record["binning"] == 2 and record["geometryFile"] == ""
    assert len(record["columns"]) == 320
    assert "image columns" in ratio.description


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


def _withMissing(nodata: float | None = None) -> VardaRaster:
    """The scene with three bands of pixel (5, 2) missing (NaN, or ``nodata``)."""
    image = _scene()
    cube = image.getData().copy()
    cube[5, 2, 3:6] = np.nan if nodata is None else nodata
    source = ArrayDataSource(
        cube,
        wavelengths=np.asarray(image.wavelengths),
        wavelengthUnits="nm",
        filePath=PATH,
        nodata=nodata,
    )
    return VardaRaster(source, name="missing")


@pytest.mark.parametrize("nodata", [None, 65535.0], ids=["nan", "nodata"])
@pytest.mark.parametrize("method", list(RatioCubeMethod), ids=lambda m: m.name)
def test_missing_values_stay_missing_and_are_not_pooled(geometry, nodata, method):
    ratio, companion = _run(_withMissing(nodata), method=method)
    values = ratio.getData()
    assert np.isnan(values[5, 2, 3:6]).all()  # not interpolated
    assert np.isfinite(np.delete(values[5, 2], [3, 4, 5])).all()
    assert companion.getData()[5, 2, 0] == 0  # not in the denominator
    entry = next(
        c for c in ratio.extraMetadata["ratioCube"]["columns"] if c["column"] == 2
    )
    assert entry["count"] == ROWS - 1 and entry["fallback"] is None
    assert entry["validCount"] == [ROWS - 1] * BANDS


def test_the_record_gives_each_columns_support(geometry):
    ratio, _ = _run(_absorbing(2))
    record = ratio.extraMetadata["ratioCube"]
    columns = {c["column"]: c for c in record["columns"]}
    assert columns[2]["fallback"] == "neighbors"
    assert columns[2]["borrowed"] == [[1, ROWS]]
    assert columns[0]["borrowed"] == []
    # no backplane lines: the oversampling is estimated from the backplane
    assert record["oversamplingFrom"] == "estimate: (pixels per row)²"
    for entry in record["columns"]:
        assert entry["oversampling"] == 1.0
        assert entry["sensorPixels"] is None
        assert entry["effectiveCount"] == entry["count"]
        assert len(entry["validCount"]) == BANDS


def test_backplane_lines_give_the_effective_count(monkeypatch):
    line = np.repeat(np.arange(ROWS)[:, None] // 2, COLS, axis=1)  # 2x along track
    geometry = attrs.evolve(_geometry(), ir_line=line)
    monkeypatch.setattr(ratio_cube_analysis, "columnGeometryFor", lambda *_: geometry)
    ratio, _ = _run(_scene(), method=RatioCubeMethod.WHOLE_COLUMN)
    record = ratio.extraMetadata["ratioCube"]
    assert record["oversamplingFrom"] == "backplane lines"
    for entry in record["columns"]:
        assert entry["sensorPixels"] == ROWS // 2
        assert entry["oversampling"] == 2.0
        assert entry["effectiveCount"] == ROWS / 2


def test_the_whole_column_method_uses_every_finite_pixel(geometry):
    ratio, companion = _run(_scene(), method=RatioCubeMethod.WHOLE_COLUMN)
    record = ratio.extraMetadata["ratioCube"]
    assert record["method"] == "Whole column"
    assert all(c["count"] == ROWS and c["fallback"] is None for c in record["columns"])
    assert np.all(companion.getData()[:, :, 0] == 1)


def test_without_a_backplane_the_reason_is_shown(monkeypatch):
    monkeypatch.setattr(ratio_cube_analysis, "columnGeometryFor", lambda *_: None)
    analysis = RatioCubeAnalysis()
    image = _scene()
    assert "backplane" in analysis.unavailableReason(image)
    with pytest.raises(ValueError, match="backplane"):
        analysis.runAll(image, QUIET)


def test_a_backplane_of_another_size_is_refused(monkeypatch):
    monkeypatch.setattr(
        ratio_cube_analysis, "columnGeometryFor", lambda *_: _geometry(ROWS + 1)
    )
    assert "size" in RatioCubeAnalysis().unavailableReason(_scene())


def test_an_image_without_a_file_or_a_crism_width_has_no_columns():
    source = ArrayDataSource(
        np.ones((4, 4, 5), np.float32), wavelengths=np.arange(5) * 100.0 + 1000
    )
    reason = RatioCubeAnalysis().unavailableReason(VardaRaster(source, name="x"))
    assert "backplane" in reason and "unprojected" in reason


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


def test_an_envi_ratio_cube_has_a_methods_description_and_a_column_table(
    geometry, tmp_path
):
    ratio, _ = _run(_absorbing(2))
    path = writeRaster(ratio, tmp_path / "ratio.img")
    header = (tmp_path / "ratio.hdr").read_text()
    start = header.index("description = {")
    description = header[start : header.index("}", start)]
    assert "Ratio cube of absorbing: Bland pixels" in description
    assert "maximum depth 0.02" in description and "Neighbor Radius 2" in description
    # the files that must travel together are named
    for name in ("ratio.hdr", "ratio.img.aux.xml", "ratio.columns.csv"):
        assert name in description
    with open(tmp_path / "ratio.columns.csv", newline="") as table:
        rows = list(csv.DictReader(table))
    assert path == tmp_path / "ratio.img"
    assert len(rows) == COLS
    column2 = next(r for r in rows if r["column"] == "2")
    assert column2["fallback"] == "neighbors" and column2["borrowed"] == "1:40"
    entry = next(
        c for c in ratio.extraMetadata["ratioCube"]["columns"] if c["column"] == 2
    )
    assert float(column2["mean_B1"]) == pytest.approx(entry["spectrum"][0])
    assert float(column2["std_B1"]) == pytest.approx(entry["std"][0])
    assert int(column2["n_B1"]) == entry["validCount"][0]
    assert float(column2["effectiveCount"]) == entry["effectiveCount"]


def test_a_geotiff_ratio_cube_gets_the_column_table_too(geometry, tmp_path):
    ratio, _ = _run(_scene())
    writeRaster(ratio, tmp_path / "ratio.tif")
    assert (tmp_path / "ratio.columns.csv").exists()


# --- presets, with HyPyRameter replaced by a stand-in ------------------------


def _olivine(analysis: RatioCubeAnalysis) -> None:
    analysis.blandness.set(
        next(b for b in analysis.blandness.enumType if b.value == "Olivine")
    )


def _fakeHypyrameter(monkeypatch) -> None:
    """Parameter values that grow with the detector column: (c + 0.5) / 100."""

    def parameters(cube, wavelengthsNm, names):
        values = (np.arange(cube.shape[1]) + 0.5) / 100
        return np.broadcast_to(
            values[None, :, None], (*cube.shape[:2], len(names))
        ).copy()

    monkeypatch.setattr(ratio_cube_analysis, "HYPYRAMETER_AVAILABLE", True)
    monkeypatch.setattr(ratio_cube_analysis, "parameterCube", parameters)
    monkeypatch.setattr(
        ratio_cube_analysis, "validParameterNames", lambda wavelengths: list(_ALL_NAMES)
    )


@pytest.fixture
def fakeHypyrameter(monkeypatch):
    _fakeHypyrameter(monkeypatch)


_ALL_NAMES = {limit.name for preset in PRESETS.values() for limit in preset.limits}


def _blandColumns(ratio: VardaRaster) -> set[int]:
    return {
        c["column"]
        for c in ratio.extraMetadata["ratioCube"]["columns"]
        if c["fallback"] is None
    }


def test_choosing_a_preset_fills_its_editable_limits():
    analysis = RatioCubeAnalysis()
    assert analysis.presetLimits.get() == ""
    _olivine(analysis)
    assert analysis.presetLimits.get() == (
        "OLINDEX3 ≤ 0.02, LCPINDEX2 ≤ 0.02, HCPINDEX2 ≤ 0.02; "
        "slope ≤ 0.1 /µm over 1000–1700 nm"
    )
    analysis.blandness.resetToDefault()
    assert analysis.presetLimits.get() == ""


@pytest.mark.parametrize("preset", list(PRESETS.values()), ids=lambda p: p.name)
def test_every_presets_text_parses_back_to_itself(preset):
    assert parsePresetLimits(formatPresetLimits(preset), preset) == preset


def test_a_preset_runs_end_to_end_and_records_its_limits(geometry, fakeHypyrameter):
    analysis = RatioCubeAnalysis()
    image = _scene()
    analysis.prepareFor(image)
    _olivine(analysis)
    analysis.minimumPixels.set(1)
    analysis.neighborRadius.set(0)
    assert analysis.unavailableReason(image) == ""
    ratio, companion = analysis.runAll(image, QUIET)
    record = ratio.extraMetadata["ratioCube"]
    assert record["blandness"] == "Olivine"
    assert record["thresholds"] == {
        "limits": {"OLINDEX3": 0.02, "LCPINDEX2": 0.02, "HCPINDEX2": 0.02},
        "maxSlope": 0.1,
        "slopeWindowNm": [1000.0, 1700.0],
    }
    # values 0.005 and 0.015 are under 0.02; the others fall back to the whole column
    assert _blandColumns(ratio) == {0, 1}
    assert "Olivine" in ratio.description


def test_edited_preset_limits_change_which_pixels_are_bland(geometry, fakeHypyrameter):
    analysis = RatioCubeAnalysis()
    image = _scene()
    analysis.prepareFor(image)
    _olivine(analysis)
    analysis.minimumPixels.set(1)
    analysis.neighborRadius.set(0)
    analysis.presetLimits.set(
        "OLINDEX3 ≤ 0.04, LCPINDEX2 ≤ 0.04, HCPINDEX2 ≤ 0.04; "
        "slope ≤ 0.10 /µm over 1000–1700 nm"
    )
    ratio, _ = analysis.runAll(image, QUIET)
    assert _blandColumns(ratio) == {0, 1, 2, 3}
    assert ratio.extraMetadata["ratioCube"]["thresholds"]["limits"]["OLINDEX3"] == 0.04


def test_the_edited_slope_limit_applies(geometry, fakeHypyrameter):
    image = _scene()
    cube = image.getData().astype(np.float64)
    cube[:, 1, :] *= np.linspace(1.0, 2.0, BANDS)  # a steep slope in column 1
    source = ArrayDataSource(
        cube.astype(np.float32),
        wavelengths=np.asarray(image.wavelengths),
        wavelengthUnits="nm",
        filePath=PATH,
    )
    tilted = VardaRaster(source, name="tilted")
    analysis = RatioCubeAnalysis()
    analysis.prepareFor(tilted)
    _olivine(analysis)
    analysis.minimumPixels.set(1)
    analysis.neighborRadius.set(0)
    ratio, _ = analysis.runAll(tilted, QUIET)
    assert _blandColumns(ratio) == {0}  # column 1 is too steep
    analysis.presetLimits.set(
        "OLINDEX3 ≤ 0.02, LCPINDEX2 ≤ 0.02, HCPINDEX2 ≤ 0.02; "
        "slope ≤ 5 /µm over 1000–1700 nm"
    )
    ratio, _ = analysis.runAll(tilted, QUIET)
    assert _blandColumns(ratio) == {0, 1}


@pytest.mark.parametrize(
    "text, message",
    [
        ("garbage", "slope part"),
        ("OLINDEX3 ≤ 0.02; slope ≤ 0.1 /µm over 1000–1700 nm", "missing"),
        (
            "OLINDEX3 ≤ x, LCPINDEX2 ≤ 0.02, HCPINDEX2 ≤ 0.02; "
            "slope ≤ 0.1 /µm over 1000–1700 nm",
            "not like",
        ),
        (
            "OLINDEX3 ≤ 0.02, BD1900r2 ≤ 0.02, HCPINDEX2 ≤ 0.02; "
            "slope ≤ 0.1 /µm over 1000–1700 nm",
            "not a parameter",
        ),
        (
            "OLINDEX3 ≤ 0.02, LCPINDEX2 ≤ 0.02, HCPINDEX2 ≤ 0.02; "
            "slope ≤ 0.1 /µm over 1700–1000 nm",
            "start < end",
        ),
    ],
)
def test_malformed_preset_limits_are_refused_clearly(
    geometry, fakeHypyrameter, text, message
):
    analysis = RatioCubeAnalysis()
    _olivine(analysis)
    analysis.presetLimits.set(text)
    assert message in analysis.unavailableReason(_scene())
    with pytest.raises(ValueError, match=message):
        analysis.runAll(_scene(), QUIET)


def test_generic_ignores_the_preset_limits_text(geometry):
    analysis = RatioCubeAnalysis()
    analysis.presetLimits.set("garbage")
    assert analysis.unavailableReason(_scene()) == ""


@pytest.mark.parametrize("preset", [False, True], ids=["generic", "preset"])
def test_the_memory_estimate_bounds_a_run_with_a_backplane(monkeypatch, preset):
    """The registry-wide memory test has no backplane to read; this one gives
    the run a synthetic one, on a scene big enough for the per-pixel cost to
    dominate the per-column records."""
    rows, cols = 120, 100
    geometry = _geometry(rows, cols)
    monkeypatch.setattr(ratio_cube_analysis, "columnGeometryFor", lambda *_: geometry)
    analysis = RatioCubeAnalysis()
    image = _scene(rows, cols)
    analysis.prepareFor(image)
    if preset:  # HyPyRameter's own working memory is not measured here
        _fakeHypyrameter(monkeypatch)
        _olivine(analysis)
    analysis.runAll(image, QUIET)  # warm up
    tracemalloc.start()
    try:
        analysis.runAll(image, QUIET)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= analysis.estimateMemory(image)
