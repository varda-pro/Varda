"""Restoring a session reloads its images and rebuilds workspaces with their ROIs."""

import json

import numpy as np
from shapely.geometry import box

from varda.common.di_types import ProjectImages
from varda.common.entities import Color, ROIMode, VardaRaster
from varda.image_loading.crism_geometry import geometryOverride, setGeometryOverride
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.rois.roi_collection import ROICollection
from varda.rois.roi_json import roisToJson
from varda.session import (
    SessionState,
    WorkspaceState,
    captureSession,
    readSession,
    writeSession,
)
from varda.session_restore import SessionRestorer
from varda.workspaces.dual_image_workspace.dual_image_workspace import (
    DualImageWorkspace,
)
from varda.workspaces.general_image_analysis import GeneralImageAnalysisWorkflow

RED = Color(1.0, 0.0, 0.0, 0.5)


def _image(name: str, path: str) -> VardaRaster:
    cube = np.random.default_rng(0).random((20, 20, 10))
    source = ArrayDataSource(cube, wavelengths=np.arange(10.0), filePath=path)
    return VardaRaster(source, name=name)


class FakeMainGui:
    def __init__(self) -> None:
        self.tabs = []

    def addTab(self, widget, title=None) -> None:
        self.tabs.append((widget, title))


def _fakeLoader(available: dict[str, VardaRaster]):
    def load(path, onSuccess, onFailure):
        if path in available:
            onSuccess(available[path])
        else:
            onFailure(f"no such file {path}")

    return load


def _roisWithOneBox() -> dict:
    rois = ROICollection()
    rois.addROI(box(1, 1, 4, 4), "r1", RED, ROIMode.RECTANGLE)
    return roisToJson(rois)


def test_restore_loads_images_in_order_and_rebuilds_workspaces(qtbot):
    a, b = _image("a", "/data/a.img"), _image("b", "/data/b.img")
    state = SessionState(
        images=("/data/a.img", "/data/b.img"),
        workspaces=(
            WorkspaceState("general", ("/data/b.img",), _roisWithOneBox()),
            WorkspaceState(
                "dual", ("/data/a.img", "/data/b.img"), roisToJson(ROICollection())
            ),
        ),
    )
    images = ProjectImages()
    mainGui = FakeMainGui()
    restorer = SessionRestorer(
        images, mainGui, loadImage=_fakeLoader({a.filePath: a, b.filePath: b})
    )

    with qtbot.waitSignal(restorer.sigFinished, timeout=10_000) as blocker:
        restorer.restore(state)

    assert list(images) == [a, b]
    (general, _), (dual, _) = mainGui.tabs
    for widget, _ in mainGui.tabs:
        qtbot.addWidget(widget)
    assert isinstance(general, GeneralImageAnalysisWorkflow)
    assert general.config.image.get() is b
    assert [roi.name for roi in general.roiCollection.getAllROIs()] == ["r1"]
    assert isinstance(dual, DualImageWorkspace)
    assert (dual.image1, dual.image2) == (a, b)
    (report,) = blocker.args
    assert report.failed == ()
    assert report.workspaces == 2


def test_already_open_images_are_not_loaded_again(qtbot):
    a = _image("a", "/data/a.img")
    images = ProjectImages([a])
    calls = []

    def load(path, onSuccess, onFailure):
        calls.append(path)
        onSuccess(_image("a again", path))

    restorer = SessionRestorer(images, FakeMainGui(), loadImage=load)
    state = SessionState(images=("/data/a.img",), workspaces=())

    with qtbot.waitSignal(restorer.sigFinished, timeout=10_000):
        restorer.restore(state)

    assert calls == []
    assert list(images) == [a]


def test_missing_images_are_reported_and_their_workspaces_skipped(qtbot):
    a = _image("a", "/data/a.img")
    state = SessionState(
        images=("/data/a.img", "/data/gone.img"),
        workspaces=(
            WorkspaceState("general", ("/data/gone.img",), _roisWithOneBox()),
            WorkspaceState("general", ("/data/a.img",), _roisWithOneBox()),
        ),
    )
    images = ProjectImages()
    mainGui = FakeMainGui()
    restorer = SessionRestorer(images, mainGui, loadImage=_fakeLoader({a.filePath: a}))

    with qtbot.waitSignal(restorer.sigFinished, timeout=10_000) as blocker:
        restorer.restore(state)

    for widget, _ in mainGui.tabs:
        qtbot.addWidget(widget)
    assert list(images) == [a]
    assert len(mainGui.tabs) == 1
    (report,) = blocker.args
    assert report.failed == ("/data/gone.img",)
    assert report.workspaces == 1


def test_metadata_edited_in_varda_comes_back_with_the_session(qtbot, tmp_path):
    from varda.session import captureSession, readSession, writeSession

    a = _image("a", "/data/a.img")
    a.setMetadataOverrides({"nodata": 0.0, "wavelengthUnits": "nm"}, name="scene")
    path = writeSession(captureSession([a], []).state, tmp_path / "s.varda")
    state = readSession(path)
    assert state.metadata["/data/a.img"]["overrides"]["wavelengthUnits"] == "nm"

    fresh = _image("a", "/data/a.img")  # as loaded from the file again
    images = ProjectImages()
    restorer = SessionRestorer(
        images, FakeMainGui(), loadImage=_fakeLoader({"/data/a.img": fresh})
    )
    with qtbot.waitSignal(restorer.sigFinished, timeout=10_000):
        restorer.restore(state)
    assert fresh.name == "scene" and fresh.nodata == 0.0
    assert fresh.wavelengthUnits == "nm"


def test_a_chosen_crism_geometry_file_comes_back_with_the_session(qtbot, tmp_path):
    imagePath = str(tmp_path / "data" / "iovf.img")
    geometryPath = str(tmp_path / "data" / "frt000107a7_07_in166j_mtr3.img")
    a = _image("a", imagePath)
    setGeometryOverride(a, geometryPath)
    # saved relative to the session's folder, so a bundle can move
    path = writeSession(
        captureSession([a], []).state, tmp_path / "s.varda", relativeTo=tmp_path
    )
    stored = json.loads(path.read_text())["metadata"]["data/iovf.img"]
    assert stored["crismGeometryFile"] == "data/frt000107a7_07_in166j_mtr3.img"
    state = readSession(path)
    assert state.metadata[imagePath]["crismGeometryFile"] == geometryPath

    fresh = _image("a", imagePath)  # as loaded from the file again
    restorer = SessionRestorer(
        ProjectImages(), FakeMainGui(), loadImage=_fakeLoader({imagePath: fresh})
    )
    with qtbot.waitSignal(restorer.sigFinished, timeout=10_000):
        restorer.restore(state)
    assert geometryOverride(fresh) == geometryPath
    assert fresh.metadataOverrides == {}  # nothing else was edited
