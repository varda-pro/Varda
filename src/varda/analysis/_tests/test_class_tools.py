"""Density slice and the class map clean-up tools."""

import logging

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS

from varda.analysis.class_tools import (
    ClumpAnalysis,
    Connectivity,
    DensitySliceAnalysis,
    MajorityFilterAnalysis,
    MajorityRule,
    SieveAnalysis,
    SliceMode,
    classAreas,
    classAreasCsv,
    classRaster,
    isClassMap,
)
from varda.analysis import class_tools
from varda.common.entities import VardaRaster
from varda.geo._tests.fixtures import LRO_WAC_SOUTH_POLAR_PROJ, makeImage
from varda.geo.measure import pixelAreaMethod, pixelAreas
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

QUIET = lambda percent, message: None  # noqa: E731
NAMES, COLORS = ["water", "rock", "dust"], ["#0000ff", "#888888", "#ffaa00"]


def _scene(classes: np.ndarray) -> VardaRaster:
    base = VardaRaster(
        ArrayDataSource(np.zeros(classes.shape + (1,), np.float32)), name="scene"
    )
    return classRaster(base, classes, NAMES, COLORS, name="classes", description="test")


def _classes(result: VardaRaster) -> np.ndarray:
    return np.asarray(result.getData(), float)[:, :, 0]


def test_class_maps_are_recognized_and_other_images_refused():
    plain = VardaRaster(ArrayDataSource(np.zeros((4, 4, 3), np.float32)), name="plain")
    assert isClassMap(_scene(np.zeros((4, 4))))
    assert "class map" in SieveAnalysis().unavailableReason(plain)


def test_majority_removes_speckle_and_keeps_names_and_colors():
    classes = np.zeros((9, 9))
    classes[4, 4] = 1  # one speck of rock in water
    result = MajorityFilterAnalysis().run(_scene(classes), QUIET)
    assert np.all(_classes(result) == 0)
    assert result.extraMetadata["classes"] == NAMES
    assert result.extraMetadata["classColors"] == COLORS


def test_minority_brings_out_the_rare_class():
    classes = np.zeros((9, 9))
    classes[4, 4] = 1
    analysis = MajorityFilterAnalysis()
    analysis.rule.set(MajorityRule.MINORITY)
    out = _classes(analysis.run(_scene(classes), QUIET))
    assert np.all(out[3:6, 3:6] == 1)


def test_a_heavy_center_weight_keeps_the_original():
    classes = np.zeros((9, 9))
    classes[4, 4] = 1
    analysis = MajorityFilterAnalysis()
    analysis.centerWeight.set(9)
    assert _classes(analysis.run(_scene(classes), QUIET))[4, 4] == 1


def test_unclassified_pixels_stay_unclassified():
    classes = np.zeros((6, 6))
    classes[2, 2] = np.nan
    assert np.isnan(
        _classes(MajorityFilterAnalysis().run(_scene(classes), QUIET))[2, 2]
    )


def test_sieve_removes_small_groups_or_fills_them():
    classes = np.zeros((10, 10))
    classes[1, 1] = 1  # a group of 1
    classes[5:8, 5:8] = 2  # a group of 9
    analysis = SieveAnalysis()
    analysis.minimum.set(4)
    out = _classes(analysis.run(_scene(classes), QUIET))
    assert np.isnan(out[1, 1]) and np.all(out[5:8, 5:8] == 2)
    analysis.fill.set(True)
    assert _classes(analysis.run(_scene(classes), QUIET))[1, 1] == 0


def test_sieve_connectivity_decides_whether_diagonal_pixels_join():
    classes = np.zeros((6, 6))
    classes[1, 1] = classes[2, 2] = 1  # touching only at a corner
    analysis = SieveAnalysis()
    analysis.minimum.set(2)
    analysis.connectivity.set(Connectivity.FOUR)
    assert np.isnan(_classes(analysis.run(_scene(classes), QUIET))[1, 1])
    analysis.connectivity.set(Connectivity.EIGHT)
    assert _classes(analysis.run(_scene(classes), QUIET))[1, 1] == 1


def test_clump_fills_holes_but_keeps_solid_regions_of_other_classes():
    classes = np.zeros((12, 12))
    classes[:, 6:] = 1  # a solid rock region
    classes[3, 2] = np.nan  # a hole in the water
    classes[8, 3] = 2  # a speck of dust in the water
    out = _classes(ClumpAnalysis().run(_scene(classes), QUIET))
    assert out[3, 2] == 0 and out[8, 3] == 0
    assert np.all(out[:, 7:] == 1)


def _band(values: np.ndarray) -> VardaRaster:
    return VardaRaster(
        ArrayDataSource(values.astype(np.float32)[:, :, None]), name="band"
    )


def test_equal_interval_slices_between_low_and_high():
    values = np.tile(np.linspace(0.0, 1.0, 10), (3, 1))
    analysis = DensitySliceAnalysis()
    analysis.slices.set(5)
    analysis.low.set(0.0)
    analysis.high.set(1.0)
    result = analysis.run(_band(values), QUIET)
    out = _classes(result)
    assert out[0, 0] == 0 and out[0, -1] == 4
    assert result.extraMetadata["classes"][0] == "0 – 0.2"
    assert len(result.extraMetadata["classColors"]) == 5


def test_quantile_slices_have_equal_counts():
    values = np.random.default_rng(0).exponential(size=(40, 40))
    analysis = DensitySliceAnalysis()
    analysis.slices.set(4)
    analysis.mode.set(SliceMode.QUANTILE)
    counts = np.bincount(
        _classes(analysis.run(_band(values), QUIET)).astype(int).ravel()
    )
    assert counts.tolist() == pytest.approx([400] * 4, abs=2)


def _mapped() -> VardaRaster:
    """A 3-class map on Mars's equirectangular sphere at 0°N, 1 km pixels."""
    base = makeImage(
        "IAU_2015:49910",
        centerLonLat=(10.0, 0.0),
        pixelSize=1000.0,
        width=12,
        height=10,
    )
    classes = np.full((10, 12), np.nan)
    classes[:4] = 0
    classes[4:7, :8] = 1
    classes[7:, 3:] = 2
    return classRaster(base, classes, NAMES, COLORS, name="classes", description="test")


def _pixelAreas(image: VardaRaster) -> np.ndarray:
    assert image.crs is not None
    rows, cols = np.indices((image.height, image.width))
    return pixelAreas(image.crs, image.transform, rows, cols)


def test_class_areas_sum_the_pixel_areas_of_each_class():
    image = _mapped()
    classes = _classes(image)
    areas = _pixelAreas(image)
    result = classAreas(image)
    assert [row.name for row in result.rows] == NAMES
    for k, row in enumerate(result.rows):
        mask = classes == k
        assert row.index == k
        assert row.pixels == mask.sum()
        assert row.areaKm2 == pytest.approx(areas[mask].sum() / 1e6, rel=1e-12)
        # ~1 km² pixels at the equator
        assert row.areaKm2 == pytest.approx(row.pixels, rel=1e-3)
    assert sum(row.fraction for row in result.rows) == pytest.approx(1.0)
    assert result.rows[0].fraction == pytest.approx(48 / 99)
    assert result.totalKm2 == pytest.approx(sum(r.areaKm2 for r in result.rows))
    assert image.crs is not None
    assert result.method == pixelAreaMethod(image.crs, image.transform)


def test_an_unreferenced_class_map_has_pixel_counts_only():
    classes = np.array([[0, 0, 1], [2, np.nan, 1]])
    result = classAreas(_scene(classes))
    assert [row.pixels for row in result.rows] == [2, 2, 1]
    assert all(np.isnan(row.areaKm2) for row in result.rows)
    assert np.isnan(result.totalKm2)
    assert [row.fraction for row in result.rows] == pytest.approx([0.4, 0.4, 0.2])
    assert result.method == "pixel counts only: the image has no map coordinates"


def test_class_areas_csv_has_header_lines_and_a_row_per_class():
    text = classAreasCsv(classAreas(_mapped()), "scene.tif")
    lines = text.splitlines()
    header = [line for line in lines if line.startswith("#")]
    assert header[0] == "# image: scene.tif"
    assert header[1].startswith("# method: exact per row")
    assert header[2].startswith("# Varda version: ")
    table = lines[len(header) :]
    assert table[0] == "class,name,pixels,area_km2,fraction"
    assert [line.split(",")[:3] for line in table[1:]] == [
        ["0", "water", "48"],
        ["1", "rock", "24"],
        ["2", "dust", "27"],
    ]


def test_class_areas_in_blocks_match_off_the_row_only_path(monkeypatch):
    base = makeImage(
        LRO_WAC_SOUTH_POLAR_PROJ,
        centerLonLat=(0.0, -85.0),
        pixelSize=500.0,
        width=9,
        height=7,
    )
    classes = np.arange(63, dtype=float).reshape(7, 9) % 3
    image = classRaster(base, classes, NAMES, COLORS, name="c", description="t")
    areas = _pixelAreas(image)
    monkeypatch.setattr(class_tools, "_BLOCK_PIXELS", 10)  # one row at a time
    monkeypatch.setattr(class_tools, "_AREA_BLOCK_PIXELS", 20)  # two rows of areas
    result = classAreas(image)
    assert result.method.startswith("map pixel area over")
    for k, row in enumerate(result.rows):
        assert row.areaKm2 == pytest.approx(areas[classes == k].sum() / 1e6)


def test_a_crs_varda_cannot_measure_on_falls_back_to_pixel_counts(monkeypatch):
    def refuse(*args: object) -> np.ndarray:
        raise ValueError("the CRS is on no body")

    monkeypatch.setattr(class_tools, "imagePixelAreas", refuse)
    source = ArrayDataSource(
        np.zeros((2, 2, 1), np.float32),
        transform=Affine(1, 0, 0, 0, -1, 2),
        crs=CRS("+proj=longlat +R=1000 +no_defs"),
    )
    base = VardaRaster(source, name="odd")
    classes = np.array([[0, 1], [2, 2]], float)
    result = classAreas(
        classRaster(base, classes, NAMES, COLORS, name="c", description="t")
    )
    assert [row.pixels for row in result.rows] == [1, 1, 2]
    assert np.isnan(result.totalKm2)
    assert result.method == "pixel counts only: the CRS is on no body"


def test_the_class_band_is_read_in_row_blocks(monkeypatch):
    image = _mapped()
    windows: list[tuple[int, int, int, int] | None] = []
    getData = image.getData

    def spy(bandIndices=None, window=None, masked=True):
        windows.append(window)
        return getData(bandIndices, window, masked)

    monkeypatch.setattr(image, "getData", spy)
    monkeypatch.setattr(class_tools, "_BLOCK_PIXELS", 36)  # three rows of 12
    monkeypatch.setattr(class_tools, "_AREA_BLOCK_PIXELS", 48)  # four rows
    result = classAreas(image)
    # never the whole band; row blocks inside each block of areas
    assert windows == [(0, 0, 3, 12), (3, 0, 1, 12), (4, 0, 3, 12)] + [
        (7, 0, 1, 12),
        (8, 0, 2, 12),
    ]
    assert [row.pixels for row in result.rows] == [48, 24, 27]


def test_nan_pixel_areas_reach_the_class_and_the_total(monkeypatch):
    image = _mapped()
    real = class_tools.imagePixelAreas

    def withHole(crs, transform, width, height):
        areas = np.array(real(crs, transform, width, height))
        areas[0, 0] = np.nan  # a class 0 pixel PROJ can't invert
        return areas

    monkeypatch.setattr(class_tools, "imagePixelAreas", withHole)
    result = classAreas(image)
    assert np.isnan(result.rows[0].areaKm2)
    assert np.isfinite(result.rows[1].areaKm2) and np.isfinite(result.rows[2].areaKm2)
    assert np.isnan(result.totalKm2)


def test_non_integer_out_of_range_and_no_data_class_values_are_unclassified():
    classes = np.array([[0, 1, 2, 1.5], [-1, 3, 7, 99], [2, np.nan, 0, 0]], np.float32)
    source = ArrayDataSource(
        classes[:, :, None],
        bandNames=["Class"],
        nodata=99.0,
        extraMetadata={"classes": NAMES, "classColors": COLORS},
    )
    result = classAreas(VardaRaster(source, name="classes"))
    assert [row.pixels for row in result.rows] == [3, 1, 2]
    assert result.unclassifiedPixels == 6  # 1.5, -1, 3, 7, no-data and NaN
    assert [row.fraction for row in result.rows] == pytest.approx([0.5, 1 / 6, 1 / 3])
    np.testing.assert_array_equal(source.getData()[1, 3, 0], 99.0)  # not edited


def test_any_proj_failure_falls_back_to_counts_and_is_logged(monkeypatch, caplog):
    def broken(*args: object) -> np.ndarray:
        raise RuntimeError("PROJ context gone")

    monkeypatch.setattr(class_tools, "imagePixelAreas", broken)
    with caplog.at_level(logging.WARNING, logger=class_tools.__name__):
        result = classAreas(_mapped())
    assert [row.pixels for row in result.rows] == [48, 24, 27]
    assert all(np.isnan(row.areaKm2) for row in result.rows)
    assert result.method == "pixel counts only: PROJ context gone"
    assert "PROJ context gone" in caplog.text


def test_class_areas_csv_records_the_fractions_denominator():
    result = classAreas(_mapped())
    lines = classAreasCsv(result, "scene.tif").splitlines()
    assert "# classified pixels: 99 (the fractions' denominator)" in lines
    assert "# unclassified pixels: 21" in lines
    assert f"# classified area_km2: {result.totalKm2!r}" in lines
    unreferenced = classAreasCsv(classAreas(_scene(np.zeros((2, 2)))), "x")
    assert "# classified area_km2: unavailable" in unreferenced.splitlines()
