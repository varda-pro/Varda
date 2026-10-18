"""Tests for the isolated CRISM geometry module."""

from pathlib import Path

import numpy as np
import pytest
import rasterio

from varda.image_loading import crism_geometry
from varda.image_loading.crism_geometry import (
    IMAGE_COLUMNS,
    ColumnGeometry,
    columnGeometryFor,
    computeColumnLockedTranslation,
    findBandIndex,
    geometryFileCandidates,
    loadColumnGeometry,
    resolveGeometryFile,
    sceneIncidence,
)


@pytest.fixture(autouse=True)
def _freshCache():
    crism_geometry._geometry_cache.clear()
    yield
    crism_geometry._geometry_cache.clear()


def _touch(p: Path) -> None:
    p.write_bytes(b"")


def test_resolves_per_strip_mtrdr(tmp_path):
    src = tmp_path / "frt00013000_07_if166j_mtr3.img"
    _touch(src)
    ddr = tmp_path / "frt00013000_07_in166j_mtr3.img"
    _touch(ddr)
    assert resolveGeometryFile(str(src)) == str(ddr)


def test_resolves_mosaic_tile(tmp_path):
    src = tmp_path / "t0886_mrral_05s058_0327_4.img"
    _touch(src)
    ddr = tmp_path / "t0886_mrrde_05s058_0327_4.img"
    _touch(ddr)
    assert resolveGeometryFile(str(src)) == str(ddr)


def test_returns_none_when_companion_missing(tmp_path):
    src = tmp_path / "frt00013000_07_if166j_mtr3.img"
    _touch(src)
    assert resolveGeometryFile(str(src)) is None


def test_returns_none_for_non_crism_name(tmp_path):
    src = tmp_path / "some_geotiff.tif"
    _touch(src)
    assert resolveGeometryFile(str(src)) is None


def _geometry():
    # 10 rows x 8 cols. IR Sample = detector column index = the column number,
    # constant down each column. One strip (target 1, segment 1) everywhere.
    h, w = 10, 8
    ir = np.tile(np.arange(w, dtype=np.float64), (h, 1))
    target = np.ones((h, w), dtype=np.float64)
    segment = np.ones((h, w), dtype=np.float64)
    return ColumnGeometry(ir_sample=ir, target_id=target, segment_id=segment)


def test_translation_keeps_same_column():
    geom = _geometry()
    # 2x2 footprint over columns 2..3, rows 1..2 -> mean IR sample = 2.5.
    template_px = np.array([[2, 1], [4, 1], [4, 3], [2, 3]], dtype=np.float64)
    # Click far away at row 7, column 6. Column-lock must place the copy back on
    # the template's detector column (2 or 3), NOT at the clicked column 6.
    dxdy = computeColumnLockedTranslation(
        template_px, clickRow=7, clickCol=6, geometry=geom
    )
    assert dxdy is not None
    dx, dy = dxdy
    src_cx = float(template_px[:, 0].mean())  # 3.0 (vertex centroid)
    src_cy = float(template_px[:, 1].mean())  # 2.0
    # The template's mean IR sample (2.5) lies between columns 2 and 3, i.e. at
    # continuous x = 3.0 — exactly where the template already is. Locked to the
    # template's columns, not the click (6).
    assert abs((src_cx + dx) - 3.0) < 1e-6
    # The shift is a whole number of pixels, so this 2-row template can't be
    # centered exactly on row 7's center (7.5); it extends down to cover rows
    # 7..8 (centroid 8.0), the same convention as boxPolygonPixels.
    assert abs((src_cy + dy) - 8.0) < 1e-6


def _pixelsCovered(polygon: np.ndarray, shape: tuple[int, int]) -> tuple[set, set]:
    """(rows, cols) of the pixels a (col,row) corner polygon rasterizes to."""
    import rasterio.features
    from shapely.geometry import Polygon, mapping

    mask = rasterio.features.rasterize(
        [(mapping(Polygon([tuple(p) for p in polygon])), 1)],
        out_shape=shape,
        fill=0,
        dtype=np.uint8,
    ).astype(bool)
    rows, cols = np.nonzero(mask)
    return set(rows.tolist()), set(cols.tolist())


def _box(left: int, top: int, width: int, height: int) -> np.ndarray:
    return np.array(
        [
            [left, top],
            [left + width, top],
            [left + width, top + height],
            [left, top + height],
        ],
        dtype=np.float64,
    )


def test_locked_copy_of_odd_box_keeps_columns_and_centres_on_clicked_row():
    geom = _geometry()
    template = _box(left=2, top=1, width=5, height=5)  # pixels cols 2..6, rows 1..5
    dx, dy = computeColumnLockedTranslation(
        template, clickRow=7, clickCol=0, geometry=geom
    )

    rows, cols = _pixelsCovered(template + np.array([dx, dy]), geom.ir_sample.shape)
    assert cols == {2, 3, 4, 5, 6}
    assert rows == {5, 6, 7, 8, 9}  # centered on the clicked row 7


def test_locked_copy_of_even_box_keeps_columns():
    geom = _geometry()
    template = _box(left=2, top=1, width=2, height=2)  # pixels cols 2..3, rows 1..2
    dx, dy = computeColumnLockedTranslation(
        template, clickRow=7, clickCol=6, geometry=geom
    )

    rows, cols = _pixelsCovered(template + np.array([dx, dy]), geom.ir_sample.shape)
    assert cols == {2, 3}
    assert rows == {7, 8}  # even height: clicked row and the one below


def test_translation_none_when_strip_absent_at_dest_row():
    geom = _geometry()
    # Make destination row 7 a different strip than the template's.
    geom.target_id[7, :] = 99
    template_px = np.array([[2, 1], [3, 1], [3, 2], [2, 2]], dtype=np.float64)
    assert (
        computeColumnLockedTranslation(
            template_px, clickRow=7, clickCol=6, geometry=geom
        )
        is None
    )


def test_translation_none_when_dest_row_out_of_bounds():
    geom = _geometry()
    template_px = np.array([[2, 1], [3, 1], [3, 2], [2, 2]], dtype=np.float64)
    assert (
        computeColumnLockedTranslation(
            template_px, clickRow=999, clickCol=6, geometry=geom
        )
        is None
    )


def test_find_band_index_matches_aliases():
    descriptions = ("IR (L-detector) Sample", "Target ID", "Segment ID (counter)")
    assert findBandIndex(descriptions, "ir_sample") == 1  # 1-indexed for rasterio
    assert findBandIndex(descriptions, "target_id") == 2
    assert findBandIndex(descriptions, "segment_id") == 3


def test_find_band_index_returns_none_when_absent():
    assert findBandIndex(("Latitude", "Longitude"), "ir_sample") is None


def test_find_band_index_prefers_ir_over_vnir_sample():
    # Real CRISM MTRDR _in geometry band order. "VNIR Sample" contains the
    # substring "ir sample", so a naive substring match wrongly picks it; the
    # IR detector sample (band 6) is the correct match for an IR cube.
    descriptions = (
        "VNIR/IR Spectral Continuity Residual",
        "VNIR/IR Spatial Gradient Residual",
        "ATM Correction Spectral Shift Artifact",
        "VNIR Sample",
        "VNIR Line",
        "IR Sample",
        "IR Line",
        "VNIR/IR Ground Sampling Offset",
        "VNIR/IR Mask",
    )
    assert findBandIndex(descriptions, "ir_sample") == 6


# --- real CRISM product names (PDS Geosciences Node, checked 2026-10-17) ------
#
# TRR3 (sensor space): frt000107a7_07_if166l_trr3.img, its DDR (in the DDR
# volume) frt000107a7_07_de166l_ddr1.img, 14 bands, no detector sample band.
# MTRDR (map projected): frt000107a7_07_if166j_mtr3.img with
# frt000107a7_07_in166j_mtr3.img (9 bands, "IR Sample" and "IR Line") and
# frt000107a7_07_de166l_mtr1.img (14 DDR bands). TER3 (sensor space):
# frt000107a7_07_if166j_ter3.img with frt000107a7_07_in166j_ter3.img.

_IN_BANDS = (
    "VNIR/IR Spectral Continuity Residual",
    "VNIR/IR Spatial Gradient Residual",
    "ATM Correction Spectral Shift Artifact",
    "VNIR Sample",
    "VNIR Line",
    "IR Sample",
    "IR Line",
    "VNIR/IR Ground Sampling Offset",
    "VNIR/IR Mask",
)
_DDR_BANDS = ("INA at areoid; deg", "EMA at areoid; deg", "Phase angle; deg")


def _envi(path: Path, bands: dict[str, np.ndarray]) -> None:
    first = next(iter(bands.values()))
    with rasterio.open(
        path,
        "w",
        driver="ENVI",
        width=first.shape[1],
        height=first.shape[0],
        count=len(bands),
        dtype="float32",
        nodata=65535.0,
    ) as out:
        for index, (name, values) in enumerate(bands.items(), start=1):
            out.write(values.astype(np.float32), index)
            out.set_band_description(index, name)


def test_a_trr3_resolves_its_ddr(tmp_path):
    src = tmp_path / "frt000107a7_07_if166l_trr3.img"
    _touch(src)
    ddr = tmp_path / "frt000107a7_07_de166l_ddr1.img"
    _touch(ddr)
    assert resolveGeometryFile(str(src)) == str(ddr)
    vnir = tmp_path / "frt000107a7_07_if166s_trr3.img"
    _touch(vnir)
    _touch(tmp_path / "frt000107a7_07_de166s_ddr1.img")
    assert str(resolveGeometryFile(str(vnir))).endswith("de166s_ddr1.img")


def test_upper_case_names_resolve_to_the_file_as_named(tmp_path):
    # ODE downloads keep PDS's upper-case names, also on case-sensitive disks
    src = tmp_path / "FRT000107A7_07_IF166J_MTR3.IMG"
    _touch(src)
    companion = tmp_path / "FRT000107A7_07_IN166J_MTR3.IMG"
    _touch(companion)
    assert resolveGeometryFile(str(src)) == str(companion)


def test_an_mtrdr_lists_its_in_file_then_its_ddr(tmp_path):
    src = tmp_path / "frt000107a7_07_if166j_mtr3.img"
    _touch(src)
    for name in ("frt000107a7_07_de166l_mtr1.img", "frt000107a7_07_in166j_mtr3.img"):
        _touch(tmp_path / name)
    assert [Path(p).name for p in geometryFileCandidates(str(src))] == [
        "frt000107a7_07_in166j_mtr3.img",
        "frt000107a7_07_de166l_mtr1.img",
    ]


def test_an_mtrdr_in_file_gives_detector_columns_and_lines(tmp_path):
    rows, cols = 6, 9
    sample = np.tile(np.arange(cols) // 2 + 100.0, (rows, 1))  # 2x oversampled
    line = np.repeat(np.arange(rows)[:, None] // 2 + 50.0, cols, axis=1)
    bands = {name: np.zeros((rows, cols)) for name in _IN_BANDS}
    bands["IR Sample"], bands["IR Line"] = sample, line
    bands["VNIR Sample"] = sample + 7  # must not be taken for the IR sample
    src = tmp_path / "frt000107a7_07_if166j_mtr3.img"
    _touch(src)
    _envi(tmp_path / "frt000107a7_07_in166j_mtr3.img", bands)
    geometry = loadColumnGeometry(str(src))
    assert geometry is not None
    np.testing.assert_array_equal(geometry.ir_sample, sample)
    assert geometry.ir_line is not None
    np.testing.assert_array_equal(geometry.ir_line, line)
    assert geometry.sampleBand == "IR Sample"
    assert geometry.source.endswith("in166j_mtr3.img")


def test_an_unprojected_trr3_uses_its_image_columns(tmp_path):
    rows = 4
    src = tmp_path / "frt000107a7_07_if166l_trr3.img"
    _touch(src)
    # the real DDR: incidence and so on, but no detector sample band
    _envi(
        tmp_path / "frt000107a7_07_de166l_ddr1.img",
        {name: np.full((rows, 640), 40.0) for name in _DDR_BANDS},
    )
    assert loadColumnGeometry(str(src)) is None
    geometry = columnGeometryFor(str(src), (rows, 640), projected=False)
    assert geometry is not None
    assert geometry.source == IMAGE_COLUMNS and geometry.binning == 1
    np.testing.assert_array_equal(geometry.ir_sample[0], np.arange(640))
    # the DDR still gives the solar incidence
    incidence = sceneIncidence(str(src))
    assert incidence is not None and incidence.mean == 40.0


def test_image_columns_need_a_confidently_unprojected_crism_cube():
    binned = columnGeometryFor(None, (4, 320), projected=False)
    assert binned is not None and binned.binning == 2
    assert columnGeometryFor(None, (4, 320), projected=True) is None
    assert columnGeometryFor(None, (4, 600), projected=False) is None
