"""The Mosaic workspace, driven end to end on a synthetic scene split in two."""

import numpy as np
import pytest
from affine import Affine
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QMessageBox
from rasterio.crs import CRS

from varda.analysis.class_tools import isClassMap
from varda.analysis.geometry import crop
from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.mosaicking.grid import defaultGrid
from varda.mosaicking.render import Resampling, renderMosaic
from varda.workspaces.mosaic.mosaic_workspace import MosaicWorkspace
from varda.workspaces.mosaic.new_mosaic_dialog import NewMosaicDialog
from varda.workspaces.registry import buildWorkspace

PIXEL = 18.0
TRANSFORM = Affine(PIXEL, 0.0, 500000.0, 0.0, -PIXEL, 4000000.0)


def _scene() -> VardaRaster:
    cube = np.random.default_rng(0).uniform(0.1, 0.5, (40, 60, 4)).astype(np.float32)
    return VardaRaster(
        ArrayDataSource(
            cube,
            wavelengths=np.linspace(1000, 2000, 4),
            transform=TRANSFORM,
            crs=CRS.from_epsg(32612),
        ),
        name="scene",
    )


def _misregistered(image: VardaRaster, dx: float) -> VardaRaster:
    t = image.transform
    return VardaRaster(
        ArrayDataSource(
            np.asarray(image.getData(), np.float32),
            wavelengths=image.wavelengths,
            transform=Affine(t.a, t.b, t.c + dx, t.d, t.e, t.f),
            crs=image.crs,
        ),
        name="right",
    )


@pytest.fixture
def setup(qtbot):
    scene = _scene()
    left = crop(scene, 0, 0, 40, 36, name="left")
    right = _misregistered(crop(scene, 0, 24, 40, 36, name="right"), 3 * PIXEL)
    project = [scene, left, right]
    workspace = MosaicWorkspace([left, right], project)
    qtbot.addWidget(workspace)
    return workspace, scene, project


def _select(workspace, key):
    panel = workspace.inputsPanel
    for row in range(panel.list.count()):
        if int(panel.list.item(row).data(Qt.ItemDataRole.UserRole)) == key:
            panel.list.setCurrentRow(row)


def test_the_preview_renders_both_images(setup):
    workspace, _scene_, _p = setup
    assert workspace.canvas.imageItem.image is not None
    assert workspace.canvas.footprint.getData()[0] is not None


def test_arrow_keys_nudge_the_selected_image(setup):
    workspace, _s, _p = setup
    _select(workspace, 1)
    workspace.nudge(-3, 0)
    assert np.isclose(workspace.project.input(1).correction[0, 2], -3 * PIXEL)
    workspace.nudge(0, 1)  # up is north: +y
    assert np.isclose(workspace.project.input(1).correction[1, 2], PIXEL)


def test_tie_points_from_clicks_then_fit_put_the_image_back(qtbot, setup):
    workspace, scene, project = setup
    _select(workspace, 1)
    panel = workspace.tiePanel
    assert panel.movingKey == 1 and panel.referenceKey() == 0
    for col, row in [(26.5, 5.5), (30.5, 20.5), (34.5, 35.5)]:
        panel._onReferenceClicked(QPointF(col, row))  # the feature on the left image
        panel._onMovingClicked(
            QPointF(col - 24, row)
        )  # the same feature on the right image
    assert len(workspace.project.tiePointsOf(1)) == 3
    workspace.fit(1)
    assert "RMS 0.00 pixels" in workspace.inputsPanel.status.text()

    grid = defaultGrid(workspace.project)
    workspace._exported(
        renderMosaic(workspace.project, grid), grid, Resampling.NEAREST, True
    )
    mosaic, sources = project[-2], project[-1]
    assert np.array_equal(np.asarray(mosaic.getData()), np.asarray(scene.getData()))
    assert isClassMap(sources) and sources.extraMetadata["classes"] == ["left", "right"]
    assert mosaic.extraMetadata["mosaic"]["inputs"] == ["left", "right"]
    record = mosaic.extraMetadata["provenance"]
    assert record["process"] == "Mosaic"
    assert [i["name"] for i in record["inputs"]] == ["left", "right"]
    assert record["parameters"]["resampling"] == "NEAREST"
    assert record["parameters"]["recipe"] == workspace.project.state()
    assert record["parameters"]["grid"]["width"] == grid.width
    sourcesRecord = sources.extraMetadata["provenance"]
    assert sourcesRecord["inputs"][0]["provenance"] == record
    # each source's alignment quality, so a pixel's quality can be traced
    left, right = sources.extraMetadata["mosaic"]["sources"]
    assert (left["class"], left["image"], left["tiePoints"]) == (0, "left", 0)
    assert left["rmsPixels"] is None and left["medianPhaseSnr"] is None
    assert (right["class"], right["image"], right["tiePoints"]) == (1, "right", 3)
    assert right["kept"] == 3 and right["rmsPixels"] == pytest.approx(0, abs=0.01)
    assert right["medianPhaseSnr"] is None  # placed by hand
    assert sourcesRecord["parameters"]["sources"] == [left, right]


def test_the_recipe_survives_a_session(setup):
    workspace, _s, project = setup
    _select(workspace, 1)
    workspace.nudge(-3, 0)
    workspace.project.move(1, -1)
    kind, images = workspace.kind, workspace.sessionImages()
    assert kind == "mosaic"
    restored = buildWorkspace(kind, images, project, workspace.sessionState())
    assert [i.key for i in restored.project.inputs] == [1, 0]
    assert np.isclose(restored.project.input(1).correction[0, 2], -3 * PIXEL)
    restored.close()


def test_the_new_mosaic_dialog_refuses_unusable_choices(qtbot, monkeypatch):
    scene = _scene()
    other = VardaRaster(
        ArrayDataSource(np.zeros((5, 5, 2), np.float32)), name="two bands"
    )
    dialog = NewMosaicDialog([scene, other])
    qtbot.addWidget(dialog)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    created = []
    dialog.connectOnAccept(created.append)
    dialog.list.item(0).setCheckState(Qt.CheckState.Checked)
    dialog._create()
    assert "two images" in warnings[-1]
    dialog.list.item(1).setCheckState(Qt.CheckState.Checked)
    dialog._create()
    assert created == [] and warnings
