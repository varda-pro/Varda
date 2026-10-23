"""Shape mode in the 3D Terrain workspace: opening a shape model, the
readout of a picked plate, plate data, and the layout on laptop screens."""

import numpy as np
import pytest
from PyQt6.QtWidgets import QMessageBox

from varda.common.layout_audit import assertNothingClipped
from varda.shapes._tests.meshes import icosphere, reversed_
from varda.shapes.formats import writeObj
from varda.shapes.plate_data import plateData, writePlateCsv
from varda.workspaces.terrain import shape_panel as shapePanelModule
from varda.workspaces.terrain.shape_panel import ShapePanel
from varda.workspaces.terrain.terrain_workspace import TerrainWorkspace
from varda.workspaces.workspace_kinds import buildWorkspace


@pytest.fixture
def panel(qtbot):
    widget = ShapePanel()
    qtbot.addWidget(widget)
    return widget


def opened(qtbot, panel, model, path):
    writeObj(model, path)
    panel.openShape(path)
    qtbot.waitUntil(lambda: panel.model is not None, timeout=10000)
    return panel.model


def test_opening_shows_plate_count_and_mean_edge(qtbot, panel, tmp_path):
    model = opened(qtbot, panel, icosphere(1.0, 3), tmp_path / "ball.obj")
    text = panel.info.text()
    assert f"{model.plateCount:,} plates" in text
    assert f"mean edge {model.meanEdgeKm * 1000:,.1f} m" in text
    assert "closed" in text


def test_a_reversed_model_says_its_normals_were_flipped(qtbot, panel, tmp_path):
    opened(qtbot, panel, reversed_(icosphere(1.0, 2)), tmp_path / "inside_out.obj")
    assert "Normals were flipped to face outward." in panel.info.text()


def test_a_bennu_model_in_meters_is_read_as_meters_when_accepted(
    qtbot, panel, tmp_path, monkeypatch
):
    asked = []

    def accept(*args, **kwargs):
        asked.append(args)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", accept)
    sphere = icosphere(0.245, 2)
    meters = type(sphere)(sphere.vertices * 1000, sphere.plates, "bennu")
    panel.bodyBox.setCurrentText("Bennu")
    writeObj(meters, tmp_path / "bennu.obj")
    panel.openShape(tmp_path / "bennu.obj")
    qtbot.waitUntil(
        lambda: panel.model is not None and panel.model.vertices.max() < 1,
        timeout=10000,
    )
    assert asked
    assert panel.model.body == "Bennu"


def test_a_pick_reads_the_full_model_where_the_ray_meets_it(qtbot, panel, tmp_path):
    model = opened(qtbot, panel, icosphere(1.0, 3), tmp_path / "ball.obj")
    slope = plateData(model, "slope", "deg", np.full(model.plateCount, 12.5),
                      np.full(model.plateCount, 0.5))  # fmt: skip
    panel.addPlateData(slope)
    panel.colorBox.setCurrentText("slope")
    qtbot.waitUntil(lambda: panel._caster is not None, timeout=10000)
    picked = []
    panel.pickedPlate.connect(picked.append)
    # the page's plate index is ignored: the ray decides
    panel.onPick(
        {"plate": 0, "xyz": [0, 0, 0], "origin": [3, 0, 0], "direction": [-1, 0, 0]}
    )
    text = panel.readout.text()
    assert text.startswith(f"Plate {picked[0]} · X ")
    assert "lat 0.000° lon 0.000°" in text or "lon 360.000°" in text
    assert "slope 12.5 ± 0.5 deg" in text


def test_plate_data_for_another_model_shows_the_message(qtbot, panel, tmp_path):
    opened(qtbot, panel, icosphere(1.0, 2), tmp_path / "ball.obj")
    other = icosphere(1.01, 2)
    path = tmp_path / "other.csv"
    writePlateCsv(
        path, other, [plateData(other, "slope", "deg", np.zeros(other.plateCount))]
    )
    panel.openPlateData(path)
    qtbot.waitUntil(lambda: "another shape model" in panel.status.text(), timeout=10000)
    assert "slope was made for another shape model" in panel.status.text()
    assert "Traceback" not in panel.status.text()
    assert panel.colorBox.findText("slope") == -1


def test_the_view_shows_the_model_being_read(qtbot, panel, tmp_path):
    path = tmp_path / "ball.obj"
    writeObj(icosphere(1.0, 3), path)
    panel.openShape(path)
    assert panel.view.busy.isBusy()
    assert panel.view.busy.message() == "Reading ball.obj…"
    qtbot.waitUntil(lambda: panel.model is not None, timeout=10000)
    assert panel.view.busy.message() == "Drawing…"  # until the page is ready


def test_a_model_that_cant_be_read_ends_the_wait(qtbot, panel, tmp_path):
    path = tmp_path / "broken.obj"
    path.write_text("not a model")
    panel.openShape(path)
    qtbot.waitUntil(lambda: "couldn't be read" in panel.status.text(), timeout=10000)
    assert not panel.view.busy.isBusy()


def test_the_reader_runs_in_the_background(qtbot, panel, tmp_path, monkeypatch):
    calls = []
    real = shapePanelModule.runInBackground

    def recording(task, onDone, onFailed, **kwargs):
        calls.append(kwargs.get("description"))
        return real(task, onDone, onFailed, **kwargs)

    monkeypatch.setattr(shapePanelModule, "runInBackground", recording)
    model = opened(qtbot, panel, icosphere(1.0, 2), tmp_path / "ball.obj")
    assert "Reading a shape model" in calls
    path = tmp_path / "slope.csv"
    writePlateCsv(
        path, model, [plateData(model, "slope", "deg", np.zeros(model.plateCount))]
    )
    panel.openPlateData(path)
    assert "Reading plate data" in calls
    qtbot.waitUntil(lambda: "slope" in panel.plateData, timeout=10000)


@pytest.mark.parametrize("size", [(1280, 800), (1024, 700)])
def test_shape_mode_fits_laptop_screens(qtbot, size):
    workspace = TerrainWorkspace([])
    qtbot.addWidget(workspace)
    workspace.surfaceBox.setCurrentText("Shape model")
    workspace.resize(*size)
    workspace.show()
    qtbot.waitExposed(workspace)
    assert workspace.shapePanel is not None and workspace.shapePanel.isVisible()
    assertNothingClipped(workspace)


def test_shape_mode_comes_back_from_a_session(qtbot, tmp_path):
    model = icosphere(1.0, 2)
    writeObj(model, tmp_path / "ball.obj")
    csv = tmp_path / "slope.csv"
    writePlateCsv(
        csv, model, [plateData(model, "slope", "deg", np.arange(model.plateCount))]
    )
    first = TerrainWorkspace([])
    qtbot.addWidget(first)
    first.surfaceBox.setCurrentText("Shape model")
    panel = first.shapePanel
    assert panel is not None
    panel.openShape(tmp_path / "ball.obj")
    qtbot.waitUntil(lambda: panel.model is not None, timeout=10000)
    panel.openPlateData(csv)
    qtbot.waitUntil(lambda: "slope" in panel.plateData, timeout=10000)
    panel.colorBox.setCurrentText("slope")
    state = first.sessionState()
    assert state["shape"]["path"] == str(tmp_path / "ball.obj")
    assert state["shape"]["plateData"] == [{"path": str(csv)}]

    second = buildWorkspace("terrain", [], [], state)
    assert isinstance(second, TerrainWorkspace)
    qtbot.addWidget(second)
    restored = second.shapePanel
    assert restored is not None and second.surfaceBox.currentText() == "Shape model"
    qtbot.waitUntil(lambda: "slope" in restored.plateData, timeout=10000)
    assert restored.colorBox.currentText() == "slope"


def test_a_table_with_several_value_columns_asks_which(
    qtbot, panel, tmp_path, monkeypatch
):
    from astropy.io import fits
    from PyQt6.QtWidgets import QInputDialog

    model = opened(qtbot, panel, icosphere(1.0, 2), tmp_path / "ball.obj")
    n = model.plateCount
    table = fits.BinTableHDU.from_columns(
        [
            fits.Column(name="QUALITY", format="D", array=np.ones(n)),
            fits.Column(name="SLOPE", format="D", unit="deg", array=np.arange(n)),
        ]
    )
    path = tmp_path / "two.fits"
    fits.HDUList([fits.PrimaryHDU(), table]).writeto(path)
    offered = []

    def choose(parent, title, label, items, *args, **kwargs):
        offered.append(list(items))
        return "SLOPE", True

    monkeypatch.setattr(QInputDialog, "getItem", choose)
    panel.openPlateData(path)
    qtbot.waitUntil(lambda: "SLOPE" in panel.plateData, timeout=10000)
    assert offered == [["QUALITY", "SLOPE"]]
    assert panel.sessionState()["plateData"] == [{"path": str(path), "column": "SLOPE"}]


def test_a_model_kept_in_km_is_not_asked_about_again_on_restore(
    qtbot, tmp_path, monkeypatch
):
    asked = []

    def decline(*args, **kwargs):
        asked.append(args)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", decline)
    sphere = icosphere(0.245, 2)
    writeObj(
        type(sphere)(sphere.vertices * 1000, sphere.plates, "big"), tmp_path / "big.obj"
    )
    first = ShapePanel()
    qtbot.addWidget(first)
    first.bodyBox.setCurrentText("Bennu")
    first.openShape(tmp_path / "big.obj")
    qtbot.waitUntil(lambda: first.model is not None, timeout=10000)
    assert len(asked) == 1
    second = ShapePanel()
    qtbot.addWidget(second)
    second.restoreSessionState(first.sessionState())
    qtbot.waitUntil(lambda: second.model is not None, timeout=10000)
    assert len(asked) == 1
