"""Choose Geometry File…: pointing Varda to a CRISM IN file by hand enables
column lock in the CRISM Ratio workspace and the ROI manager (General Image
Analysis, Dual)."""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.image_loading import crism_geometry, crism_geometry_chooser
from varda.image_loading.crism_geometry import COLUMNS, geometryOverride
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.plotting.plot import VardaPlotWidget
from varda.rois.roi_collection import ROICollection
from varda.rois.roi_manager_widget import ROIManagerWidget
from varda.workspaces.crism_ratio.crism_ratio_workspace import CrismRatioWorkspace
from varda.workspaces.linked_images.linking import Link

MARS = CRS.from_user_input("+proj=eqc +R=3396190 +units=m +no_defs")
ROWS, COLS = 30, 40
IOVF_NAME = "FRT000107A7_07_IF166J_MTR3.HDR Iterative Outlier Voting Filter (IOVF).img"


@pytest.fixture(autouse=True)
def _freshCache():
    crism_geometry._geometry_cache.clear()
    yield
    crism_geometry._geometry_cache.clear()


def _cube(name: str, filePath: str | None = None, bands: int = 6) -> VardaRaster:
    values = np.random.default_rng(0).uniform(0.2, 0.4, (ROWS, COLS, bands))
    return VardaRaster(
        ArrayDataSource(
            values.astype(np.float32),
            wavelengths=np.linspace(1000, 2500, bands),
            wavelengthUnits="nanometers",
            transform=Affine(100.0, 0, 0, 0, -100.0, 0),
            crs=MARS,
            filePath=filePath,
        ),
        name=name,
    )


def _backplane(path: Path, bands: dict[str, np.ndarray]) -> Path:
    with rasterio.open(
        path, "w", driver="ENVI", width=COLS, height=ROWS, count=len(bands),
        dtype="float32",
    ) as out:  # fmt: skip
        for index, (name, values) in enumerate(bands.items(), start=1):
            out.write(values.astype(np.float32), index)
            out.set_band_description(index, name)
    return path


def _inFile(folder: Path) -> Path:
    sample = np.tile(np.arange(COLS, dtype=np.float32) + 10, (ROWS, 1))
    return _backplane(folder / "frt000107a7_07_in166j_mtr3.img", {"IR Sample": sample})


def _deFile(folder: Path) -> Path:
    return _backplane(
        folder / "frt000107a7_07_de166l_mtr1.img",
        {"INA at areoid; deg": np.full((ROWS, COLS), 40.0)},
    )


@pytest.fixture
def dialogs(monkeypatch):
    """The file dialog answers with ``answer["path"]``; warnings are kept."""
    answer: dict = {"path": "", "starts": [], "warnings": []}

    def getOpenFileName(_parent, _title, start, _filter):
        answer["starts"].append(start)
        return answer["path"], ""

    monkeypatch.setattr(
        crism_geometry_chooser.QFileDialog, "getOpenFileName", getOpenFileName
    )
    monkeypatch.setattr(
        crism_geometry_chooser.QMessageBox,
        "warning",
        lambda _parent, _title, text: answer["warnings"].append(text),
    )
    return answer


def _workspace(qtbot, data: VardaRaster) -> CrismRatioWorkspace:
    browse = _cube("browse", bands=1)
    workspace = CrismRatioWorkspace(browse, data, [browse, data], Link.PIXEL)
    qtbot.addWidget(workspace)
    return workspace


def test_choosing_the_in_file_enables_column_lock(qtbot, tmp_path, dialogs):
    data = _cube("iovf", str(tmp_path / IOVF_NAME))
    workspace = _workspace(qtbot, data)
    assert not workspace.lockCheck.isEnabled()
    assert "choose its geometry file" in workspace.lockStatus.text()
    assert workspace.chooseGeometryButton.text() == "Choose Geometry File…"

    dialogs["path"] = str(_inFile(tmp_path))
    workspace.chooseGeometryButton.click()

    assert dialogs["starts"] == [str(tmp_path)]  # the image's folder
    assert geometryOverride(data, COLUMNS) == dialogs["path"]
    assert workspace.lockCheck.isEnabled() and workspace.lockCheck.isChecked()
    assert workspace.lockStatus.text() == "Column lock on"
    assert "(override)" in workspace.lockCheck.toolTip()
    assert workspace.chooseGeometryButton.text() == "Change Geometry File…"
    assert dialogs["warnings"] == []


def test_a_ddr_chosen_for_column_lock_is_refused(qtbot, tmp_path, dialogs):
    data = _cube("iovf", str(tmp_path / IOVF_NAME))
    workspace = _workspace(qtbot, data)
    dialogs["path"] = str(_deFile(tmp_path))
    workspace.chooseGeometryButton.click()
    assert len(dialogs["warnings"]) == 1 and "DDR/DE" in dialogs["warnings"][0]
    assert geometryOverride(data, COLUMNS) == ""
    assert not workspace.lockCheck.isEnabled()


def test_canceling_the_dialog_changes_nothing(qtbot, tmp_path, dialogs):
    data = _cube("iovf", str(tmp_path / IOVF_NAME))
    workspace = _workspace(qtbot, data)
    workspace.chooseGeometryButton.click()
    assert geometryOverride(data, COLUMNS) == "" and dialogs["warnings"] == []


def test_the_roi_manager_offers_the_choice_and_follows_the_image(
    qtbot, tmp_path, dialogs
):
    data = _cube("iovf", str(tmp_path / IOVF_NAME))
    manager = ROIManagerWidget(ROICollection(), data, VardaPlotWidget())
    qtbot.addWidget(manager)
    assert not manager.chooseGeometryButton.isHidden()
    assert not manager._lockColumnCheck.isEnabled()

    dialogs["path"] = str(_inFile(tmp_path))
    manager.chooseGeometryButton.click()
    assert manager._lockColumnCheck.isEnabled()
    assert "override" in manager._lockColumnCheck.toolTip()

    # chosen in another workspace showing the same image: this one follows
    other = _cube("other", str(tmp_path / "renamed.img"))
    otherManager = ROIManagerWidget(ROICollection(), other, VardaPlotWidget())
    qtbot.addWidget(otherManager)
    assert not otherManager._lockColumnCheck.isEnabled()
    workspace = _workspace(qtbot, other)
    workspace.chooseGeometryButton.click()
    assert otherManager._lockColumnCheck.isEnabled()


def test_a_found_backplane_needs_no_button_in_the_roi_manager(qtbot, tmp_path, dialogs):
    _inFile(tmp_path)
    data = _cube("if", str(tmp_path / "frt000107a7_07_if166j_mtr3.img"))
    manager = ROIManagerWidget(ROICollection(), data, VardaPlotWidget())
    qtbot.addWidget(manager)
    assert manager._lockColumnCheck.isEnabled()
    assert manager.chooseGeometryButton.isHidden()


def test_the_workspace_with_the_button_fits_a_laptop(qtbot, tmp_path):
    workspace = _workspace(qtbot, _cube("iovf", str(tmp_path / IOVF_NAME)))
    workspace.showNormal()
    workspace.resize(1280, 800)
    qtbot.waitExposed(workspace)
    qtbot.wait(50)
    assert workspace.chooseGeometryButton.isVisible()
    assertNothingClipped(workspace)


def test_a_non_crism_image_gets_no_button_in_the_roi_manager(qtbot, tmp_path):
    landsat = _cube("landsat", str(tmp_path / "LC08_L2SP_038037_20230101.tif"))
    manager = ROIManagerWidget(ROICollection(), landsat, VardaPlotWidget())
    qtbot.addWidget(manager)
    assert not manager._lockColumnCheck.isEnabled()
    assert manager.chooseGeometryButton.isHidden()
    assert "No CRISM detector columns" not in manager._lockColumnCheck.toolTip()


def test_the_button_never_invites_a_ddr(qtbot, tmp_path):
    data = _cube("iovf", str(tmp_path / IOVF_NAME))
    workspace = _workspace(qtbot, data)
    tip = workspace.chooseGeometryButton.toolTip()
    assert "IN file" in tip and "DDR" not in tip and "_de" not in tip
