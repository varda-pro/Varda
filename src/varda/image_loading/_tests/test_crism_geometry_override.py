"""Choosing a CRISM geometry file by hand, and following provenance to one."""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import CRS

from varda.analysis.geometry import crop
from varda.common.entities import VardaRaster
from varda.common.provenance import inputRecord, makeProvenance, setProvenance
from varda.image_loading import crism_geometry
from varda.image_loading.crism_geometry import (
    COLUMNS,
    GEOMETRY_FILE_KEY,
    INCIDENCE,
    checkGeometryFile,
    columnGeometryFor,
    findGeometryFile,
    geometryDataFile,
    geometryOverride,
    geometryStartFolder,
    sceneIncidenceFor,
    setGeometryOverride,
    sourceRoute,
)
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

ROWS, COLS = 6, 9
# real MTRDR names (PDS Geosciences Node)
IF_NAME = "frt000107a7_07_if166j_mtr3.img"
IN_NAME = "frt000107a7_07_in166j_mtr3.img"
DE_NAME = "frt000107a7_07_de166l_mtr1.img"
# what Varda names an IOVF result of the I/F cube
IOVF_NAME = "FRT000107A7_07_IF166J_MTR3.HDR Iterative Outlier Voting Filter (IOVF).img"
UTM = CRS.from_epsg(32612)


@pytest.fixture(autouse=True)
def _freshCache():
    crism_geometry._geometry_cache.clear()
    yield
    crism_geometry._geometry_cache.clear()


def _sample(rows: int = ROWS, cols: int = COLS) -> np.ndarray:
    """Detector samples that differ in every pixel (row r adds 1000 r), so a
    window shows which pixels it took."""
    rowOffsets = np.arange(rows, dtype=np.float64)[:, None] * 1000
    return np.tile(np.arange(cols, dtype=np.float64) + 100.0, (rows, 1)) + rowOffsets


def _envi(path: Path, bands: dict[str, np.ndarray], transform=None, crs=None) -> Path:
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
        transform=transform,
        crs=crs,
    ) as out:
        for index, (name, values) in enumerate(bands.items(), start=1):
            out.write(values.astype(np.float32), index)
            out.set_band_description(index, name)
    return path


def _inFile(path: Path, rows: int = ROWS, cols: int = COLS, **kwargs) -> Path:
    sample = _sample(rows, cols)
    return _envi(
        path,
        {"VNIR Sample": sample + 7, "IR Sample": sample, "IR Line": sample * 0},
        **kwargs,
    )


def _deFile(path: Path, rows: int = ROWS, cols: int = COLS) -> Path:
    return _envi(
        path,
        {
            "INA at areoid; deg": np.full((rows, cols), 40.0),
            "EMA at areoid; deg": np.zeros((rows, cols)),
        },
    )


def _image(
    path: Path | None, rows: int = ROWS, cols: int = COLS, **kwargs
) -> VardaRaster:
    source = ArrayDataSource(
        np.ones((rows, cols, 2), np.float32),
        filePath=None if path is None else str(path),
        **kwargs,
    )
    return VardaRaster(source, name=path.name if path else "result")


# --- the override -----------------------------------------------------------------


def test_the_override_is_used_first(tmp_path):
    image = _image(tmp_path / "renamed cube.img")
    assert columnGeometryFor(image) is None  # no PDS name: nothing found
    chosen = _inFile(tmp_path / "anything.img")
    setGeometryOverride(image, str(chosen))
    assert image.extraMetadata[GEOMETRY_FILE_KEY] == str(chosen)
    geometry = columnGeometryFor(image)
    assert geometry is not None
    assert geometry.route == "override" and geometry.source == str(chosen)
    np.testing.assert_array_equal(geometry.ir_sample, _sample())
    setGeometryOverride(image, None)
    assert geometryOverride(image) == "" and columnGeometryFor(image) is None


def test_the_override_wins_over_the_companion_beside_the_file(tmp_path):
    image = _image(tmp_path / IF_NAME)
    beside = _inFile(tmp_path / IN_NAME)
    assert findGeometryFile(image, COLUMNS).route == "beside the file"
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    chosen = _inFile(elsewhere / IN_NAME)
    setGeometryOverride(image, str(chosen))
    match = findGeometryFile(image, COLUMNS)
    assert (match.path, match.route) == (str(chosen), "override")
    assert match.path != str(beside)


def test_an_override_without_the_band_falls_back_to_the_lookup(tmp_path):
    # a DE file chosen for the incidence doesn't stop column lock finding the IN
    image = _image(tmp_path / IF_NAME)
    _inFile(tmp_path / IN_NAME)
    setGeometryOverride(image, str(_deFile(tmp_path / "chosen_de.img")))
    assert columnGeometryFor(image).route == "beside the file"
    incidence = sceneIncidenceFor(image)
    assert incidence is not None and incidence.route == "override"


def test_setting_the_override_tells_everything_showing_the_image(tmp_path):
    image = _image(tmp_path / "cube.img")
    told = []
    image.sigMetadataChanged.connect(lambda: told.append(True))
    setGeometryOverride(image, str(_inFile(tmp_path / "in.img")))
    assert told == [True]


def test_a_header_stands_for_its_data_file(tmp_path):
    data = _inFile(tmp_path / IN_NAME)
    header = data.with_suffix(".hdr")
    assert header.exists()
    assert geometryDataFile(str(header)) == str(data)
    assert geometryDataFile(str(data)) == str(data)


# --- checking a chosen file ---------------------------------------------------------


def test_a_usable_in_file_passes(tmp_path):
    image = _image(tmp_path / "cube.img")
    assert checkGeometryFile(str(_inFile(tmp_path / IN_NAME)), image, COLUMNS) == ""
    assert checkGeometryFile(str(_deFile(tmp_path / DE_NAME)), image, INCIDENCE) == ""


def test_a_file_of_another_size_is_refused(tmp_path):
    image = _image(tmp_path / "cube.img")
    wrong = _inFile(tmp_path / IN_NAME, rows=ROWS + 2, cols=COLS - 1)
    problem = checkGeometryFile(str(wrong), image, COLUMNS)
    assert f"{COLS - 1} × {ROWS + 2} pixels" in problem
    assert f"cube.img is {COLS} × {ROWS}" in problem


def test_a_ddr_chosen_for_column_lock_is_refused_plainly(tmp_path):
    image = _image(tmp_path / "cube.img")
    problem = checkGeometryFile(str(_deFile(tmp_path / DE_NAME)), image, COLUMNS)
    assert "looks like a DDR/DE file" in problem
    assert "no IR detector sample band" in problem and "IN file" in problem


def test_a_file_with_no_sample_band_is_refused(tmp_path):
    image = _image(tmp_path / "cube.img")
    other = _envi(tmp_path / "other.img", {"Albedo": np.ones((ROWS, COLS))})
    problem = checkGeometryFile(str(other), image, COLUMNS)
    assert "has no IR detector sample band" in problem and "Albedo" in problem
    problem = checkGeometryFile(str(other), image, INCIDENCE)
    assert "has no solar incidence band" in problem and "DDR/DE" in problem


def test_a_missing_or_unreadable_file_is_refused(tmp_path):
    image = _image(tmp_path / "cube.img")
    assert "doesn't exist" in checkGeometryFile(str(tmp_path / "x.img"), image, COLUMNS)
    junk = tmp_path / "junk.img"
    junk.write_text("not an image")
    assert "can't open" in checkGeometryFile(str(junk), image, COLUMNS)


def test_a_georeferenced_file_covering_a_crop_of_the_image_serves_it(tmp_path):
    transform = Affine(18.0, 0, 1000.0, 0, -18.0, 5000.0)
    chosen = _inFile(tmp_path / IN_NAME, rows=20, cols=30, transform=transform, crs=UTM)
    image = _image(
        tmp_path / "crop.img",
        transform=transform * Affine.translation(4, 3),  # column 4, row 3
        crs=UTM,
    )
    assert checkGeometryFile(str(chosen), image, COLUMNS) == ""
    setGeometryOverride(image, str(chosen))
    geometry = columnGeometryFor(image)
    np.testing.assert_array_equal(geometry.ir_sample, _sample(20, 30)[3:9, 4:13])
    # half a pixel off the grid: not the same pixels
    shifted = _image(
        tmp_path / "shifted.img",
        transform=transform * Affine.translation(4.5, 3),
        crs=UTM,
    )
    assert "aren't on one map grid" in checkGeometryFile(str(chosen), shifted, COLUMNS)


# --- following provenance -----------------------------------------------------------


def _iovfOf(ifImage: VardaRaster, folder: Path) -> VardaRaster:
    """An IOVF-like result of ``ifImage`` saved under Varda's name for it."""
    result = _image(folder / IOVF_NAME, ifImage.height, ifImage.width)
    setProvenance(
        result,
        makeProvenance("Iterative Outlier Voting Filter (IOVF)", {}, [ifImage]),
    )
    result.dataSource.extraMetadata["provenance"]["processId"] = "iovf"
    return result


def test_an_iovf_result_finds_the_in_file_of_its_source(tmp_path):
    ifPath = tmp_path / IF_NAME
    _inFile(tmp_path / IN_NAME)
    iovf = _iovfOf(_image(ifPath), tmp_path)
    # its own name no longer matches the PDS pattern
    assert crism_geometry.geometryFileCandidates(str(tmp_path / IOVF_NAME)) == []
    geometry = columnGeometryFor(iovf)
    assert geometry is not None
    assert geometry.route == sourceRoute(str(ifPath))
    assert geometry.route == f"from source {ifPath}"
    assert geometry.source == str(tmp_path / IN_NAME)
    np.testing.assert_array_equal(geometry.ir_sample, _sample())


def test_the_chain_follows_crops_and_several_steps(tmp_path):
    ifPath = tmp_path / IF_NAME
    _inFile(tmp_path / IN_NAME)
    iovf = _iovfOf(_image(ifPath), tmp_path)
    cropped = crop(iovf, 2, 3, 3, 4)  # rows 2.., columns 3..
    setProvenance(
        cropped,
        makeProvenance(
            "Crop",
            {"row": 2, "column": 3, "height": 3, "width": 4},
            [iovf],
            processId="crop",
        ),
    )
    again = _iovfOf(cropped, tmp_path / "nowhere")  # an in-memory step after
    for image in (cropped, again):
        geometry = columnGeometryFor(image)
        assert geometry is not None and geometry.route == f"from source {ifPath}"
        np.testing.assert_array_equal(geometry.ir_sample, _sample()[2:5, 3:7])


def test_the_chain_stops_at_a_resampling(tmp_path):
    ifPath = tmp_path / IF_NAME
    _inFile(tmp_path / IN_NAME)
    resampled = _image(None)
    setProvenance(
        resampled,
        makeProvenance("Resample", {}, [_image(ifPath)], processId="resample"),
    )
    assert findGeometryFile(resampled, COLUMNS) is None


def test_a_source_s_override_is_carried_by_the_record(tmp_path):
    ifImage = _image(tmp_path / "renamed.img")
    chosen = _inFile(tmp_path / "chosen.img")
    setGeometryOverride(ifImage, str(chosen))
    assert inputRecord(ifImage)[GEOMETRY_FILE_KEY] == str(chosen)
    iovf = _iovfOf(ifImage, tmp_path)
    match = findGeometryFile(iovf, COLUMNS)
    assert match is not None and match.path == str(chosen)
    assert match.route == f"from source {tmp_path / 'renamed.img'}"


def test_the_file_dialog_starts_in_the_image_s_or_its_source_s_folder(tmp_path):
    assert geometryStartFolder(_image(tmp_path / "cube.img")) == str(tmp_path)
    inMemory = _image(None)
    setProvenance(inMemory, makeProvenance("X", {}, [_image(tmp_path / IF_NAME)]))
    assert geometryStartFolder(inMemory) == str(tmp_path)
    assert geometryStartFolder(_image(None)) == ""
