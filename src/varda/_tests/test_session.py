"""Sessions: capture the open images, workspaces and ROIs; save and read them back."""

import json

import numpy as np
from affine import Affine
import pytest

from varda import session
from shapely.geometry import box

from varda.analysis.band_math import BandMathAnalysis
from varda.analysis.provenance import recordProvenance
from varda.common.di_types import ProjectImages
from varda.common.entities import Color, ROIMode, VardaRaster
from varda.geo._tests.fixtures import writeWgs84
from varda.geo.equivalence import sameCrs
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.common.provenance import PROVENANCE_KEY
from varda.image_loading.crs_check import chooseCrs, undoCrsFix
from varda.image_loading.image_loading_service import loadRaster, openDataSource
from varda.image_loading.raster_writer import writeRaster
from varda.rois.roi_collection import ROICollection
from varda.rois.roi_json import roisFromJson, roisToJson
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
    DualImageWorkspaceConfig,
)
from varda.workspaces.general_image_analysis import (
    GeneralImageAnalysisConfig,
    GeneralImageAnalysisWorkflow,
)

RED = Color(1.0, 0.0, 0.0, 0.5)


def _image(name: str, path: str | None) -> VardaRaster:
    cube = np.random.default_rng(0).random((20, 20, 10))
    source = ArrayDataSource(cube, wavelengths=np.arange(10.0), filePath=path)
    return VardaRaster(source, name=name)


def test_roi_json_round_trip():
    source = ROICollection()
    source.addColumn("note")
    source.addROI(box(2, 2, 6, 6), "square", RED, ROIMode.RECTANGLE, note="hi")

    data = roisToJson(source)
    restored = ROICollection()
    roisFromJson(data, restored)

    (roi,) = restored.getAllROIs()
    assert roi.name == "square"
    assert roi.roiType == ROIMode.RECTANGLE
    assert roi.color.toHexString() == RED.toHexString()
    assert roi.geometry.equals(box(2, 2, 6, 6))
    assert roi.properties["note"] == "hi"
    json.dumps(data)  # must be plain JSON


def test_capture_records_images_workspaces_and_rois(qtbot):
    a, b = _image("a", "/data/a.img"), _image("b", "/data/b.img")
    general = GeneralImageAnalysisConfig([a, b])
    general.image.set(b)
    generalWorkspace = GeneralImageAnalysisWorkflow(general)
    qtbot.addWidget(generalWorkspace)
    generalWorkspace.roiCollection.addROI(box(1, 1, 4, 4), "r1", RED, ROIMode.RECTANGLE)
    dual = DualImageWorkspaceConfig([a, b])
    dual.image1Param.set(a)
    dual.image2Param.set(b)
    dualWorkspace = DualImageWorkspace(dual)
    qtbot.addWidget(dualWorkspace)

    capture = captureSession([a, b], [generalWorkspace, dualWorkspace])

    assert capture.state.images == ("/data/a.img", "/data/b.img")
    assert capture.unsavedImages == ()
    first, second = capture.state.workspaces
    assert (first.kind, first.images) == ("general", ("/data/b.img",))
    assert len(first.rois["features"]) == 1
    assert (second.kind, second.images) == ("dual", ("/data/a.img", "/data/b.img"))
    assert second.rois["features"] == []


def test_images_without_a_file_are_reported_and_their_workspaces_skipped(qtbot):
    saved, unsaved = _image("saved", "/data/saved.img"), _image("result", None)
    config = GeneralImageAnalysisConfig([saved, unsaved])
    config.image.set(unsaved)
    workspace = GeneralImageAnalysisWorkflow(config)
    qtbot.addWidget(workspace)

    capture = captureSession([saved, unsaved], [workspace])

    assert capture.state.images == ("/data/saved.img",)
    assert capture.unsavedImages == ("result",)
    assert capture.state.workspaces == ()


def test_write_and_read_round_trip(tmp_path):
    state = SessionState(
        images=("/data/a.img", "/data/b.img"),
        workspaces=(
            WorkspaceState(
                "dual", ("/data/a.img", "/data/b.img"), roisToJson(ROICollection())
            ),
        ),
    )

    path = writeSession(state, tmp_path / "work")

    assert path == tmp_path / "work.varda"
    assert readSession(path) == state


def test_reading_a_non_session_file_is_a_clear_error(tmp_path):
    bogus = tmp_path / "x.varda"
    bogus.write_text('{"hello": 1}')
    with pytest.raises(ValueError, match="not a Varda session"):
        readSession(bogus)


def test_a_session_from_a_newer_varda_is_refused_clearly(tmp_path):
    newer = tmp_path / "newer.varda"
    newer.write_text(
        json.dumps(
            {"format": "varda-session", "version": 99, "images": [], "workspaces": []}
        )
    )
    with pytest.raises(ValueError, match="newer version of Varda"):
        readSession(newer)


def test_older_sessions_are_migrated_step_by_step(tmp_path, monkeypatch):
    old = tmp_path / "old.varda"
    old.write_text(
        json.dumps(
            {
                "format": "varda-session",
                "version": 1,
                "imgs": ["/a.img"],
                "workspaces": [],
            }
        )
    )
    monkeypatch.setattr(session, "_VERSION", 2)
    monkeypatch.setitem(
        session.MIGRATIONS,
        1,
        lambda doc: {**doc, "images": doc.pop("imgs"), "version": 2},
    )
    assert readSession(old).images == ("/a.img",)


def test_a_session_without_a_version_is_read_as_the_first_format(tmp_path):
    legacy = tmp_path / "legacy.varda"
    legacy.write_text(
        json.dumps({"format": "varda-session", "images": ["/a"], "workspaces": []})
    )
    assert readSession(legacy).images == ("/a",)


# --- a corrected coordinate system ---


class _NoTabs:
    def addTab(self, widget, title=None) -> None:
        raise AssertionError("no workspaces in this session")


def _restore(qtbot, state: SessionState, image: VardaRaster) -> None:
    """Restore ``state`` with ``image`` freshly loaded for its one path."""

    def load(path, onSuccess, onFailure):
        onSuccess(image)

    restorer = SessionRestorer(ProjectImages(), _NoTabs(), loadImage=load)
    with qtbot.waitSignal(restorer.sigFinished, timeout=10_000):
        restorer.restore(state)


def _saveAndRead(image: VardaRaster, tmp_path) -> SessionState:
    capture = captureSession([image], [])
    return readSession(writeSession(capture.state, tmp_path / "work"))


def test_the_automatic_crs_fix_survives_a_session(qtbot, tmp_path):
    fixed = loadRaster(str(writeWgs84(tmp_path)))
    state = _saveAndRead(fixed, tmp_path)
    saved = state.metadata[fixed.filePath]["overrides"]["crs"]
    assert sameCrs(saved, "IAU_2015:49900")

    again = loadRaster(str(fixed.filePath))
    _restore(qtbot, state, again)

    assert again.crs is not None and sameCrs(again.crs, "IAU_2015:49900")
    assert again.extraMetadata["crsFix"]["automatic"] is True
    assert again.crsHealth is not None
    assert again.crsHealth.issues[0].code == "earth-crs-on-body"


def test_a_crs_chosen_in_the_session_wins(qtbot, tmp_path):
    fixed = loadRaster(str(writeWgs84(tmp_path)))
    fixed.setMetadataOverrides({"crs": "IAU_2015:30100"})  # the Moon, by hand
    state = _saveAndRead(fixed, tmp_path)

    again = loadRaster(str(fixed.filePath))  # the automatic fix applies again
    _restore(qtbot, state, again)

    assert again.metadataOverrides == {"crs": "IAU_2015:30100"}
    assert again.crsHealth is not None and again.crsHealth.body.body is not None
    assert again.crsHealth.body.body.name == "Moon"  # checked again
    assert "crsFix" not in again.extraMetadata


def test_the_files_own_crs_chosen_in_the_session_wins(qtbot, tmp_path):
    fixed = loadRaster(str(writeWgs84(tmp_path)))
    fixed.setMetadataOverrides({"crs": None})
    state = _saveAndRead(fixed, tmp_path)

    again = loadRaster(str(fixed.filePath))
    _restore(qtbot, state, again)

    assert again.crs is not None and again.crs.to_epsg() == 4326
    assert "crsFix" not in again.extraMetadata


def test_a_session_without_a_crs_keeps_the_automatic_fix(qtbot, tmp_path):
    fixed = loadRaster(str(writeWgs84(tmp_path)))
    state = SessionState(
        (fixed.filePath,), (), {fixed.filePath: {"name": "x", "overrides": {}}}
    )  # e.g. saved before Varda checked CRSs
    _restore(qtbot, state, fixed)
    assert fixed.crs is not None and sameCrs(fixed.crs, "IAU_2015:49900")
    assert fixed.name == "x"


def test_a_session_crs_proj_cant_read_falls_back_to_the_loaded_one(qtbot, tmp_path):
    fixed = loadRaster(str(writeWgs84(tmp_path)))
    state = SessionState(
        (fixed.filePath,),
        (),
        {fixed.filePath: {"name": "x", "overrides": {"crs": "NOT A CRS"}}},
    )
    _restore(qtbot, state, fixed)  # finishes
    assert fixed.crs is not None and sameCrs(fixed.crs, "IAU_2015:49900")
    assert fixed.name == "x"


def _automatic(image: VardaRaster) -> None:
    pass  # the fix made at load


def _appliedByHand(image: VardaRaster) -> None:
    fix = undoCrsFix(image).issues[0].fix
    assert fix is not None
    chooseCrs(image, fix.wkt, fix.reason)


def _setByHand(image: VardaRaster) -> None:
    chooseCrs(image, "IAU_2015:49910", "Its producer's projection")


def _undone(image: VardaRaster) -> None:
    undoCrsFix(image)


@pytest.mark.parametrize("decide", [_automatic, _appliedByHand, _setByHand, _undone])
def test_a_crs_decision_survives_a_session_into_provenance(qtbot, tmp_path, decide):
    image = loadRaster(str(writeWgs84(tmp_path)))
    decide(image)
    record, crs = dict(image.extraMetadata["crsFix"]), image.crs
    state = _saveAndRead(image, tmp_path)

    again = loadRaster(str(image.filePath))  # the automatic fix applies again
    _restore(qtbot, state, again)

    assert again.extraMetadata["crsFix"] == record
    assert sameCrs(again.crs, crs)
    result = BandMathAnalysis()
    result.expression.set("b2 - b1")
    made = result.run(again, lambda p, m: None)
    recordProvenance(made, result, again)
    written = writeRaster(made, tmp_path / "result.tif")
    entry = openDataSource(str(written)).extraMetadata[PROVENANCE_KEY]["inputs"][0]
    assert entry["crsFix"] == record


def test_a_session_without_records_still_loads(qtbot, tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    state = SessionState(  # saved before Varda kept crsFix records
        (image.filePath,),
        (),
        {image.filePath: {"name": "x", "overrides": {"crs": "IAU_2015:49910"}}},
    )
    _restore(qtbot, state, image)
    assert sameCrs(image.crs, "IAU_2015:49910") and image.name == "x"
    assert "crsFix" not in image.extraMetadata  # the load-time fix's no longer applies


def test_a_corrected_transform_survives_a_session(qtbot, tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    corrected = [0.25, 0.0, 100.0, 0.0, -0.25, 10.0]
    image.setMetadataOverrides({**image.metadataOverrides, "transform": corrected})
    state = _saveAndRead(image, tmp_path)
    assert state.metadata[image.filePath]["overrides"]["transform"] == corrected

    again = loadRaster(str(image.filePath))
    _restore(qtbot, state, again)

    assert again.metadataOverrides["transform"] == corrected
    assert again.transform == Affine(*corrected)
