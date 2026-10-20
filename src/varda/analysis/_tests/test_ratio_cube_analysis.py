import csv
import tracemalloc

import attrs
import numpy as np
import pytest
import rasterio

from varda.analysis import ratio_cube_analysis
from varda.analysis.blandness import CHECKS, RATIO_CUBE_LIMITS
from varda.analysis.ratio_cube_analysis import (
    BLANDNESS_GENERIC,
    RatioCubeAnalysis,
    RatioCubeMethod,
)
from varda.common.entities import VardaRaster
from varda.common.provenance import makeProvenance, setProvenance
from varda.image_loading.crism_geometry import ColumnGeometry
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.data_sources.rasterio_data_source import RasterioDataSource
from varda.image_loading.raster_writer import writeRaster

WHOLE = RatioCubeMethod.WHOLE_COLUMN  # for cubes too small for a blandness check
QUIET = lambda percent, message: None  # noqa: E731
ROWS, COLS, BANDS = 40, 6, 120
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
    cube[:, column, :] *= 1 - 0.5 * np.exp(-0.5 * ((wavelengths - 2300) / 60) ** 2)
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
    thresholds = record["thresholds"]
    assert thresholds["depthFloor"] == RATIO_CUBE_LIMITS.floor
    assert thresholds["noiseMultiple"] == RATIO_CUBE_LIMITS.noiseMultiple
    assert thresholds["maxSlope"] == RATIO_CUBE_LIMITS.maxSlope
    assert thresholds["excludedNm"] == [[1000, 1050], [1950, 2100], [2650, 4000]]
    assert [w["name"] for w in thresholds["windows"]] == [
        w.name for w in CHECKS["Generic"].windows
    ]
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
    ratio, _ = _run(_cube([500.0, 700.0, 1200.0, 1500.0]), method=WHOLE)
    assert ratio.extraMetadata["ratioCube"]["vnirApproximate"] is True
    assert "VNIR" in ratio.description


def test_a_vnir_only_cube_is_not_flagged(geometry):
    ratio, _ = _run(_cube([450.0, 600.0, 750.0, 900.0]), method=WHOLE)
    assert ratio.extraMetadata["ratioCube"]["vnirApproximate"] is False
    assert "VNIR" not in ratio.description


def test_an_unprojected_crism_cube_without_a_backplane_uses_its_image_columns():
    ratio, _ = _run(
        _cube(np.linspace(1100, 2500, 6), cols=320, path=None), method=WHOLE
    )
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


def test_too_few_bands_in_a_window_are_refused_clearly(geometry):
    source = ArrayDataSource(
        np.ones((ROWS, COLS, 2), np.float32),
        wavelengths=np.array([1200.0, 1500.0]),
        wavelengthUnits="nm",
        filePath=PATH,
    )
    reason = RatioCubeAnalysis().unavailableReason(VardaRaster(source, name="two"))
    assert "at least 5 good bands in each window" in reason
    assert "1.05–1.7 µm (2)" in reason


@pytest.mark.parametrize("name", list(CHECKS))
def test_every_check_runs_without_hypyrameter(geometry, name):
    analysis = RatioCubeAnalysis()
    analysis.blandness.set(
        next(b for b in analysis.blandness.enumType if b.value == name)
    )
    assert analysis.unavailableReason(_scene()) == ""
    ratio, _ = _run_with(analysis, _scene())
    assert ratio.extraMetadata["ratioCube"]["blandness"] == name


def _run_with(analysis: RatioCubeAnalysis, image: VardaRaster) -> list[VardaRaster]:
    analysis.prepareFor(image)
    return analysis.runAll(image, QUIET)


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
    assert "Generic check (windows 1.05–1.7 µm 1050–1700 nm" in description
    assert "excluded 1000–1050, 1950–2100, 2650–4000 nm" in description
    assert "max(0.03, 3 × its per-band noise)" in description
    assert "Neighbor Radius 2" in description
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
    assert column2["blandness"] == "Generic"
    assert "max(0.03, 3 × its per-band noise)" in column2["blandnessSettings"]


def test_a_geotiff_ratio_cube_gets_the_column_table_too(geometry, tmp_path):
    ratio, _ = _run(_scene())
    writeRaster(ratio, tmp_path / "ratio.tif")
    assert (tmp_path / "ratio.columns.csv").exists()


# --- the blandness settings ---------------------------------------------------


def _blandColumns(ratio: VardaRaster) -> set[int]:
    return {
        c["column"]
        for c in ratio.extraMetadata["ratioCube"]["columns"]
        if c["fallback"] is None
    }


def _withBand(depths: list[float], center: float = 2310) -> VardaRaster:
    """The scene with an absorption of the given depth in each column."""
    image = _scene()
    cube = image.getData().astype(np.float64)
    wavelengths = np.asarray(image.wavelengths)
    shape = np.exp(-0.5 * ((wavelengths - center) / 30) ** 2)
    for column, depth in enumerate(depths):
        cube[:, column, :] *= 1 - depth * shape
    source = ArrayDataSource(
        cube.astype(np.float32),
        wavelengths=wavelengths,
        wavelengthUnits="nm",
        filePath=PATH,
    )
    return VardaRaster(source, name="bands")


def _settings(analysis: RatioCubeAnalysis, check: str, **values) -> None:
    analysis.blandness.set(
        next(b for b in analysis.blandness.enumType if b.value == check)
    )
    analysis.minimumPixels.set(1)
    analysis.neighborRadius.set(0)
    for name, value in values.items():
        getattr(analysis, name).set(value)


def test_the_depth_floor_decides_which_columns_are_bland(geometry):
    image = _withBand([0.0, 0.02, 0.05, 0.08, 0.0, 0.0])
    analysis = RatioCubeAnalysis()
    analysis.prepareFor(image)
    _settings(analysis, "Fe/Mg phyllosilicates")
    ratio, _ = analysis.runAll(image, QUIET)
    assert _blandColumns(ratio) == {0, 1, 4, 5}  # the 5 and 8 % bands fail
    analysis.depthFloor.set(0.06)
    ratio, _ = analysis.runAll(image, QUIET)
    assert _blandColumns(ratio) == {0, 1, 2, 4, 5}
    assert ratio.extraMetadata["ratioCube"]["thresholds"]["depthFloor"] == 0.06
    assert "max(0.06, 3 × its per-band noise)" in ratio.description


def test_the_noise_multiple_spares_noisy_columns(geometry):
    """A 2 % band in a column with 1 % noise is within 3 × its noise."""
    image = _withBand([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    cube = image.getData().astype(np.float64)
    rng = np.random.default_rng(3)
    cube[:, 2, :] *= 1 + 0.01 * rng.normal(size=cube[:, 2, :].shape)
    image = VardaRaster(
        ArrayDataSource(
            cube.astype(np.float32),
            wavelengths=np.asarray(image.wavelengths),
            wavelengthUnits="nm",
            filePath=PATH,
        ),
        name="noisy",
    )
    analysis = RatioCubeAnalysis()
    analysis.prepareFor(image)
    _settings(analysis, "Fe/Mg phyllosilicates", depthFloor=0.0, runLength=3)
    ratio, _ = analysis.runAll(image, QUIET)
    assert 2 in _blandColumns(ratio)  # noisy, but nothing beyond its noise


def test_the_co2_range_can_be_included(geometry):
    """A band at 2.0 µm is invisible while the CO2 range is excluded."""
    image = _withBand([0.0, 0.1, 0.0, 0.0, 0.0, 0.0], center=2020)
    analysis = RatioCubeAnalysis()
    analysis.prepareFor(image)
    _settings(analysis, "Generic")
    ratio, _ = analysis.runAll(image, QUIET)
    assert 1 in _blandColumns(ratio)
    analysis.excludedRanges.set("1000-1050, 2650-4000")
    ratio, _ = analysis.runAll(image, QUIET)
    assert 1 not in _blandColumns(ratio)
    assert ratio.extraMetadata["ratioCube"]["thresholds"]["excludedNm"] == [
        [1000, 1050],
        [2650, 4000],
    ]


@pytest.mark.parametrize(
    "setting, value, message",
    [
        ("excludedRanges", "garbage", "not like"),
        ("excludedRanges", "2100-1950", "start < end"),
        ("depthFloor", float("nan"), "Depth Floor"),
    ],
)
def test_bad_blandness_settings_are_refused_clearly(geometry, setting, value, message):
    analysis = RatioCubeAnalysis()
    getattr(analysis, setting).value = value  # bypassing the widget
    assert message in analysis.unavailableReason(_scene())
    with pytest.raises(ValueError, match=message):
        analysis.runAll(_scene(), QUIET)


def test_the_whole_column_method_ignores_the_blandness_settings(geometry):
    analysis = RatioCubeAnalysis()
    analysis.method.set(RatioCubeMethod.WHOLE_COLUMN)
    analysis.excludedRanges.value = "garbage"
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
    if preset:
        analysis.blandness.set(
            next(b for b in analysis.blandness.enumType if b.value == "Hydrated (any)")
        )
    analysis.runAll(image, QUIET)  # warm up
    tracemalloc.start()
    try:
        analysis.runAll(image, QUIET)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= analysis.estimateMemory(image)


def test_a_micrometer_cube_finds_the_same_bland_pixels(geometry):
    nm = _withBand([0.0, 0.02, 0.05, 0.08, 0.0, 0.0])
    um = VardaRaster(
        ArrayDataSource(
            nm.getData(),
            wavelengths=np.asarray(nm.wavelengths) / 1000,
            wavelengthUnits="micrometers",
            filePath=PATH,
        ),
        name="um",
    )
    results = []
    for image in (nm, um):
        analysis = RatioCubeAnalysis()
        analysis.prepareFor(image)
        _settings(analysis, "Fe/Mg phyllosilicates")
        ratio, companion = analysis.runAll(image, QUIET)
        results.append((_blandColumns(ratio), companion.getData()[:, :, 0].tolist()))
    assert results[0] == results[1]
    assert results[0][0] == {0, 1, 4, 5}


# --- the geometry file: chosen by hand, and how it was found -------------------------


def _backplaneFile(path, bands: dict[str, np.ndarray]):
    first = next(iter(bands.values()))
    with rasterio.open(
        path, "w", driver="ENVI", width=first.shape[1], height=first.shape[0],
        count=len(bands), dtype="float32",
    ) as out:  # fmt: skip
        for index, (name, values) in enumerate(bands.items(), start=1):
            out.write(values.astype(np.float32), index)
            out.set_band_description(index, name)
    return path


def _inFile(path, rows: int = ROWS):
    return _backplaneFile(path, {"IR Sample": _geometry(rows).ir_sample})


def test_a_chosen_geometry_file_is_used_and_recorded(tmp_path):
    chosen = _inFile(tmp_path / "frt0000_07_in165j_mtr3.img")
    image = _scene()
    analysis = RatioCubeAnalysis()
    analysis.prepareFor(image)
    assert "with Geometry File" in analysis.unavailableReason(image)
    analysis.geometryFile.set(str(chosen))
    # the image now carries it, so every other feature uses it too
    assert image.extraMetadata["crismColumnsFile"] == str(chosen)
    analysis.method.set(WHOLE)
    ratio, _ = analysis.runAll(image, QUIET)
    record = ratio.extraMetadata["ratioCube"]
    assert record["geometryFile"] == str(chosen)
    assert record["geometryRoute"] == "override"
    assert f"of {chosen} (found: override)" in ratio.description
    table = ratio_cube_analysis.columnTableCsv(record).splitlines()
    rows = list(csv.DictReader(table))
    assert {r["geometryFile"] for r in rows} == {str(chosen)}
    assert {r["geometryRoute"] for r in rows} == {"override"}
    # a new dialog for the image shows the file; clearing it goes back to automatic
    again = RatioCubeAnalysis()
    again.prepareFor(image)
    assert again.geometryFile.get() == str(chosen)
    again.geometryFile.set("")
    assert "crismColumnsFile" not in image.extraMetadata


def test_a_wrong_geometry_file_is_refused_with_the_reason(tmp_path):
    image = _scene()
    analysis = RatioCubeAnalysis()
    analysis.prepareFor(image)
    ddr = _backplaneFile(
        tmp_path / "frt0000_07_de165l_mtr1.img",
        {"INA at areoid; deg": np.full((ROWS, COLS), 40.0)},
    )
    analysis.geometryFile.set(str(ddr))
    reason = analysis.unavailableReason(image)
    assert reason.startswith("Geometry File: ") and "DDR/DE" in reason
    assert "crismColumnsFile" not in image.extraMetadata  # not taken
    analysis.geometryFile.set(str(_inFile(tmp_path / "small_in.img", ROWS - 1)))
    assert "pixels but" in analysis.unavailableReason(image)
    with pytest.raises(ValueError, match="Geometry File"):
        analysis.runAll(image, QUIET)


def test_the_route_through_provenance_is_recorded(tmp_path):
    source = tmp_path / "frt0000_07_if165j_mtr3.img"
    _inFile(tmp_path / "frt0000_07_in165j_mtr3.img")
    scene = _scene()

    def at(path, name: str) -> VardaRaster:
        return VardaRaster(
            ArrayDataSource(
                scene.getData(),
                wavelengths=scene.wavelengths,
                wavelengthUnits="nm",
                filePath=str(path),
            ),
            name=name,
        )

    original = at(source, "if")
    filtered = at(tmp_path / "if IOVF.img", "filtered")
    setProvenance(filtered, makeProvenance("IOVF", {}, [original], processId="iovf"))
    ratio, _ = _run(filtered, method=WHOLE)
    record = ratio.extraMetadata["ratioCube"]
    assert record["geometryRoute"] == f"from source {source}"
    assert f"(found: from source {source})" in ratio.description


# --- the 1 µm detector join ----------------------------------------------------------

JOINED_NM = np.concatenate(
    [np.arange(905.5, 1011.0, 6.55), np.arange(1047.2, 1400, 6.55)]
)


def _joined(steps: np.ndarray) -> VardaRaster:
    """A joined cube whose IR side sits ``steps[row]`` times its VNIR level."""
    level = np.where(JOINED_NM > 1020, steps[:, None], 1.0)  # (rows, bands)
    cube = 0.3 * np.broadcast_to(level[:, None, :], (ROWS, COLS, JOINED_NM.size))
    source = ArrayDataSource(
        np.ascontiguousarray(cube, np.float32),
        wavelengths=JOINED_NM,
        wavelengthUnits="nm",
        filePath=PATH,
    )
    return VardaRaster(source, name="joined")


def test_the_join_correction_scales_each_pixels_vnir_to_its_ir(geometry):
    # half the rows step down 3 %, the other half not at all: uncorrected,
    # every ratio steps at the join; corrected, none does
    steps = np.where(np.arange(ROWS) < ROWS // 2, 0.97, 1.0)
    image = _joined(steps)
    vnir = JOINED_NM < 1020

    def joinStep(cube: VardaRaster) -> np.ndarray:
        data = cube.getData()
        return data[:, :, ~vnir].mean(axis=2) / data[:, :, vnir].mean(axis=2)

    plain, _ = _run(image, method=WHOLE)
    assert np.abs(joinStep(plain) - 1).max() > 0.01
    ratio, denominators = _run(image, method=WHOLE, joinCorrection=True)
    assert np.allclose(joinStep(ratio), 1.0, atol=1e-5)

    record = ratio.extraMetadata["ratioCube"]["joinCorrection"]
    assert record["enabled"] is True
    assert record["vnirEndNm"] == pytest.approx(JOINED_NM[vnir][-1])
    assert record["factorP5"] == pytest.approx(0.97, abs=1e-5)
    assert record["factorP95"] == pytest.approx(1.0, abs=1e-5)
    assert record["unscaledPixels"] == 0
    assert "scaled to its IR" in ratio.description
    names = list(denominators.bandNames)
    factor = denominators.getData()[:, :, names.index(ratio_cube_analysis.JOIN_FACTOR)]
    assert np.allclose(factor[: ROWS // 2], 0.97, atol=1e-5)
    assert np.allclose(factor[ROWS // 2 :], 1.0, atol=1e-5)
    assert ratio_cube_analysis.JOIN_SIGMA in names


def test_without_the_correction_the_record_says_so(geometry):
    ratio, denominators = _run(_joined(np.full(ROWS, 0.97)), method=WHOLE)
    assert ratio.extraMetadata["ratioCube"]["joinCorrection"] == {"enabled": False}
    assert ratio_cube_analysis.JOIN_FACTOR not in list(denominators.bandNames)


def test_the_correction_needs_a_joined_cube(geometry):
    analysis = RatioCubeAnalysis()
    analysis.method.set(WHOLE)
    analysis.joinCorrection.set(True)
    reason = analysis.unavailableReason(_scene())
    assert "joined cube" in reason
