"""The CRISM Ratio workspace: a browse view and the I/F view, linked by pixel
or map location, with the ratio plots, a blandness verdict and a column lock
that follows the numerator."""

import numpy as np
import pytest
from affine import Affine
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QMessageBox
from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.image_loading.crism_geometry import ColumnGeometry
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering.raster_view.overlay_handles import PyqtgraphTextOverlay
from varda.image_rendering.raster_view.pointer_event import (
    KeyEvent,
    PointerAction,
    PointerEvent,
)
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    RatioExplorerTool,
)
from varda.rois.region_statistics import boxPolygonPixels, computeRegionStatistics
from varda.workspaces import ratio_explorer
from varda.workspaces.crism_ratio import crism_ratio_workspace
from varda.workspaces.crism_ratio.crism_ratio_workspace import (
    CrismRatioWorkspace,
    NewCrismRatioDialog,
)
from varda.workspaces.linked_images.linking import Link
from varda.workspaces.workspace_kinds import buildWorkspace, describeWorkspace

CTRL = Qt.KeyboardModifier.ControlModifier
MARS = CRS.from_user_input("+proj=eqc +R=3396190 +units=m +no_defs")
LEFT, RIGHT = Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton


def raster(
    name, shape, pixel=100.0, bands=6, georeferenced=True, seed=0, micrometers=False
):
    """A (rows, cols) image of ``bands`` bands from 1000 to 2500 nm."""
    rows, cols = shape
    values = np.random.default_rng(seed).uniform(0.2, 0.4, (rows, cols, bands))
    return VardaRaster(
        ArrayDataSource(
            values.astype(np.float32),
            wavelengths=np.linspace(1000, 2500, bands) / (1000 if micrometers else 1),
            wavelengthUnits="micrometers" if micrometers else "nanometers",
            transform=Affine(pixel, 0, 0, 0, -pixel, 0) if georeferenced else None,
            crs=MARS if georeferenced else None,
        ),
        name=name,
    )


def mean(polygon, image):
    return np.asarray(computeRegionStatistics(polygon, image)["mean"])


def press(button, x, y):
    pos = QPointF(x, y)
    return PointerEvent(PointerAction.PRESS, pos, pos, button, CTRL)


def tool(workspace, index=0) -> RatioExplorerTool:
    active = workspace.toolManagers[index].activeTool
    assert isinstance(active, RatioExplorerTool)
    return active


def key(workspace, qtKey, index=0):
    assert tool(workspace, index).onKeyEvent(
        KeyEvent(qtKey.value, Qt.KeyboardModifier(0))
    )


def curveY(plot):
    assert len(plot.plots) == 1
    return plot.plots[0].data()[1]


@pytest.fixture
def pixelPair():
    return raster("browse", (30, 40), seed=1), raster("data", (30, 40), seed=2)


@pytest.fixture
def mapPair():
    # the browse pixels are twice the size of the I/F pixels
    return (
        raster("browse", (15, 20), pixel=200.0, bands=1, seed=1),
        raster("data", (30, 40), pixel=100.0, seed=2),
    )


def build(qtbot, browse, data, link):
    images = [browse, data]
    workspace = CrismRatioWorkspace(browse, data, images, link)
    qtbot.addWidget(workspace)
    return workspace


def test_pixel_match_measures_where_you_click(qtbot, pixelPair):
    browse, data = pixelPair
    workspace = build(qtbot, browse, data, Link.PIXEL)
    assert workspace.windowTitle() == "browse | data"

    tool(workspace).onPointerEvent(press(LEFT, 11.5, 21.5))
    tool(workspace).onPointerEvent(press(RIGHT, 31.5, 5.5))

    numerator = mean(boxPolygonPixels(11, 21, 5, 5), data)
    np.testing.assert_allclose(curveY(workspace.plots.numeratorPlot), numerator)


def test_a_click_on_the_browse_view_measures_the_if_image_through_the_map(
    qtbot, mapPair
):
    browse, data = mapPair
    workspace = build(qtbot, browse, data, Link.MAP)

    # browse pixel (3, 2) covers I/F pixels (6..7, 4..5); its center is (7, 5)
    tool(workspace).onPointerEvent(press(LEFT, 3.5, 2.5))
    tool(workspace).onPointerEvent(press(RIGHT, 9.5, 8.5))

    np.testing.assert_array_equal(
        tool(workspace).selection.numerator, boxPolygonPixels(7, 5, 5, 5)
    )
    numerator = mean(boxPolygonPixels(7, 5, 5, 5), data)
    denominator = mean(boxPolygonPixels(19, 17, 5, 5), data)
    np.testing.assert_allclose(curveY(workspace.plots.numeratorPlot), numerator)
    np.testing.assert_allclose(curveY(workspace.plots.denominatorPlot), denominator)
    np.testing.assert_allclose(
        curveY(workspace.plots.ratioPlot), numerator / denominator
    )
    # the other view shows the same boxes, so a click there continues the pair
    np.testing.assert_array_equal(
        tool(workspace, 1).selection.numerator, boxPolygonPixels(7, 5, 5, 5)
    )


def test_a_click_outside_the_if_cube_is_refused(qtbot, mapPair):
    data = mapPair[1]
    # a browse image larger than the cube: its far corner is off the cube
    big = raster("big", (30, 40), pixel=200.0, bands=1)
    other = build(qtbot, big, data, Link.MAP)
    tool(other).onPointerEvent(press(LEFT, 35.5, 25.5))
    assert tool(other).selection.numerator is None


def test_without_a_backplane_the_lock_is_unavailable_and_ratioing_works(
    qtbot, pixelPair
):
    workspace = build(qtbot, pixelPair[0], pixelPair[1], Link.PIXEL)

    assert not workspace.lockCheck.isEnabled()
    assert not workspace.lockCheck.isChecked()
    assert "unavailable" in workspace.lockStatus.text()

    tool(workspace).onPointerEvent(press(LEFT, 11.5, 21.5))
    tool(workspace).onPointerEvent(press(RIGHT, 31.5, 5.5))
    assert len(workspace.plots.ratioPlot.plots) == 1


@pytest.fixture
def withBackplane(monkeypatch):
    geometry = ColumnGeometry(ir_sample=np.tile(np.arange(40.0), (30, 1)))
    monkeypatch.setattr(ratio_explorer, "columnGeometryFor", lambda *_args: geometry)
    monkeypatch.setattr(
        crism_ratio_workspace, "columnGeometryFor", lambda *_args: geometry
    )
    monkeypatch.setattr(VardaRaster, "filePath", property(lambda self: "/fake/x.img"))


def test_the_lock_is_on_by_default_and_the_denominator_follows(
    qtbot, pixelPair, withBackplane
):
    workspace = build(qtbot, pixelPair[0], pixelPair[1], Link.PIXEL)
    assert workspace.lockCheck.isChecked()

    tool(workspace).onPointerEvent(press(LEFT, 11.5, 21.5))
    tool(workspace).onPointerEvent(press(RIGHT, 31.5, 5.5))
    np.testing.assert_array_equal(
        tool(workspace).selection.denominator, boxPolygonPixels(11, 5, 5, 5)
    )
    tool(workspace).onPointerEvent(press(LEFT, 20.5, 21.5))  # a new column
    np.testing.assert_array_equal(
        tool(workspace).selection.denominator, boxPolygonPixels(20, 5, 5, 5)
    )


def test_an_unprojected_crism_cube_locks_by_its_image_columns(qtbot):
    browse = raster("browse", (8, 640), georeferenced=False, seed=1)
    data = raster("data", (8, 640), georeferenced=False, seed=2)
    workspace = build(qtbot, browse, data, Link.PIXEL)
    assert workspace.lockCheck.isEnabled() and workspace.lockCheck.isChecked()
    assert "image columns" in workspace.lockStatus.text()
    tool(workspace).onPointerEvent(press(LEFT, 100.5, 2.5))
    tool(workspace).onPointerEvent(press(RIGHT, 300.5, 5.5))
    np.testing.assert_array_equal(
        tool(workspace).selection.denominator, boxPolygonPixels(100, 5, 5, 5)
    )


def _pair(workspace):
    tool(workspace).onPointerEvent(press(LEFT, 11.5, 21.5))
    tool(workspace).onPointerEvent(press(RIGHT, 31.5, 5.5))


def test_s_saves_the_pair_as_rois_named_with_the_verdict(qtbot, spectralPair):
    workspace = build(qtbot, spectralPair[0], spectralPair[1], Link.PIXEL)
    _pair(workspace)

    key(workspace, Qt.Key.Key_S)

    verdict = workspace.plots.readout.lastResult
    mark = "✓" if verdict.passed else "✗"
    names = [roi.name for roi in workspace.roiCollection.getAllROIs()]
    assert names == [
        f"Ratio 1 numerator (denominator {mark} Generic)",
        f"Ratio 1 denominator (denominator {mark} Generic)",
    ]


def test_p_saves_the_numerator_pixel_as_a_point_named_with_the_verdict(
    qtbot, spectralPair
):
    workspace = build(qtbot, spectralPair[0], spectralPair[1], Link.PIXEL)
    _pair(workspace)

    key(workspace, Qt.Key.Key_P)

    (point,) = workspace.pointCollection.getAllPoints()
    mark = "✓" if workspace.plots.readout.lastResult.passed else "✗"
    assert point.name.endswith(f"(denominator {mark} Generic)")
    assert (point.x, point.y) == (11, 21)
    assert point.image == "data"


def _editSlopeLimit(workspace, value: float) -> None:
    workspace.plots.readout.editors["maxSlope"].setValue(value)


@pytest.fixture
def spectralPair():
    """Images with enough bands (60, 1000-2500 nm) for every blandness window."""
    return (
        raster("browse", (30, 40), seed=1, bands=60),
        raster("data", (30, 40), seed=2, bands=60),
    )


def test_s_puts_the_windows_values_and_limits_in_the_rois_description(
    qtbot, spectralPair
):
    workspace = build(qtbot, spectralPair[0], spectralPair[1], Link.PIXEL)
    _pair(workspace)
    _editSlopeLimit(workspace, 0.5)

    key(workspace, Qt.Key.Key_S)

    result = workspace.plots.readout.lastResult
    assert result.windows
    for fid in workspace.roiCollection.fids:
        description = workspace.roiCollection.getProperty(fid, "description")
        assert "Generic check" in description
        for w in result.windows:
            assert f"{w.window.name} {w.window.lowNm:g}–{w.window.highNm:g} nm" in (
                description
            )
            assert f"at {w.dipNm:.0f} nm" in description
            assert f"limit {100 * w.depthLimit:.1f} %" in description
        assert "|slope| ≤ 0.5 /µm (edited: slope)" in description
        assert "max(3.0 %, 3 × noise)" in description
        assert "excluded 1000–1050, 1950–2100, 2650–4000 nm" in description


def test_p_puts_the_verdict_in_the_points_description(qtbot, spectralPair):
    workspace = build(qtbot, spectralPair[0], spectralPair[1], Link.PIXEL)
    _pair(workspace)
    key(workspace, Qt.Key.Key_P)
    (point,) = workspace.pointCollection.getAllPoints()
    assert point.description == workspace.plots.readout.describe()
    assert "1.05–1.7 µm" in point.description


def test_the_session_keeps_edited_limits_and_the_co2_choice(qtbot, spectralPair):
    browse, data = spectralPair
    workspace = build(qtbot, browse, data, Link.PIXEL)
    _pair(workspace)
    _editSlopeLimit(workspace, 0.5)
    workspace.plots.readout.co2Check.setChecked(False)
    state = workspace.sessionState()
    assert state["blandnessLimits"] == {"maxSlope": 0.5}
    assert state["blandnessExcludeCo2"] is False

    copy = buildWorkspace("crism_ratio", [browse, data], [browse, data], state)
    assert isinstance(copy, CrismRatioWorkspace)
    qtbot.addWidget(copy)
    assert copy.plots.readout.limitOverrides() == {"maxSlope": 0.5}
    assert not copy.plots.readout.co2Check.isChecked()
    _pair(copy)
    result = copy.plots.readout.lastResult
    assert result.limits is not None and result.limits.maxSlope == 0.5
    assert (1950.0, 2100.0) not in result.exclusions


def test_the_managers_start_auto_hidden(qtbot, pixelPair):
    workspace = build(qtbot, pixelPair[0], pixelPair[1], Link.PIXEL)
    assert workspace.roiDock.isAutoHide()
    assert workspace.pointsDock.isAutoHide()


def test_the_session_keeps_the_link_box_size_lock_and_check(
    qtbot, mapPair, withBackplane
):
    browse, data = mapPair
    workspace = build(qtbot, browse, data, Link.MAP)
    workspace.config.boxWidth.set(7)
    workspace.config.boxHeight.set(3)
    workspace.lockCheck.setChecked(False)
    workspace.plots.readout.checkCombo.setCurrentText("Olivine")

    described = describeWorkspace(workspace)
    assert described is not None
    kind, images = described
    assert (kind, images) == ("crism_ratio", [browse, data])
    copy = buildWorkspace(kind, images, [browse, data], workspace.sessionState())
    assert isinstance(copy, CrismRatioWorkspace)
    qtbot.addWidget(copy)

    assert copy.link is Link.MAP
    assert (copy.config.boxWidth.value, copy.config.boxHeight.value) == (7, 3)
    assert not copy.lockCheck.isChecked()
    assert copy.plots.readout.currentCheckName() == "Olivine"
    assert set(workspace.sessionState()) >= {
        "link",
        "boxSize",
        "lockColumn",
        "blandness",
        "plotLibrariesOff",
    }


def test_a_session_with_an_unknown_link_is_skipped_not_raised(qtbot, pixelPair):
    images = list(pixelPair)
    assert buildWorkspace("crism_ratio", images, images, {"link": "BOGUS"}) is None


def test_a_link_problem_is_shown_across_the_top(qtbot):
    browse = raster("browse", (15, 20), georeferenced=False)
    data = raster("data", (30, 40))
    workspace = build(qtbot, browse, data, Link.PIXEL)
    assert not workspace.linkNotice.isHidden()
    assert "grid" in workspace.linkNotice.text()


def test_the_workspace_fits_a_laptop(qtbot, mapPair):
    workspace = build(qtbot, mapPair[0], mapPair[1], Link.MAP)
    workspace.showNormal()
    workspace.resize(1280, 800)
    qtbot.waitExposed(workspace)
    qtbot.wait(50)
    _pair_on_map(workspace)
    assertNothingClipped(workspace)


def test_the_three_plots_share_one_x_range_on_a_laptop(qtbot, mapPair):
    workspace = build(qtbot, mapPair[0], mapPair[1], Link.MAP)
    workspace.showNormal()
    workspace.resize(1280, 800)
    qtbot.waitExposed(workspace)
    qtbot.wait(50)
    _pair_on_map(workspace)
    plots = workspace.plots
    ranges = [
        p.viewBox.viewRange()[0]
        for p in (plots.numeratorPlot, plots.denominatorPlot, plots.ratioPlot)
    ]
    for xRange in ranges[1:]:
        np.testing.assert_allclose(xRange, ranges[0], rtol=1e-6)
    low, high = ranges[0]
    assert low <= 1000 and high >= 2500  # the whole spectrum, not a slice
    # the user's own zoom follows on every plot
    plots.ratioPlot.viewBox.setXRange(1500, 2000, padding=0)
    for plot in (plots.numeratorPlot, plots.denominatorPlot):
        np.testing.assert_allclose(plot.viewBox.viewRange()[0], [1500, 2000])


def test_the_tool_hint_fits_its_view_on_a_laptop(qtbot, mapPair):
    workspace = build(qtbot, mapPair[0], mapPair[1], Link.MAP)  # hints shown
    workspace.showNormal()
    workspace.resize(1280, 800)  # and the views narrowed after
    qtbot.waitExposed(workspace)
    qtbot.wait(50)
    for index, view in enumerate((workspace.browseView, workspace.dataView)):
        overlay = tool(workspace, index)._textOverlay
        assert isinstance(overlay, PyqtgraphTextOverlay)
        assert overlay._item.textItem.boundingRect().width() <= view.width()


def _pair_on_map(workspace):
    tool(workspace).onPointerEvent(press(LEFT, 3.5, 2.5))
    tool(workspace).onPointerEvent(press(RIGHT, 9.5, 8.5))


# --- the dialog ----------------------------------------------------------------


def test_the_dialog_defaults_to_the_sr_product_as_browse(qtbot):
    images = [
        raster("frt_if", (30, 40)),
        raster("frt_07_SR_summary", (30, 40)),
    ]
    dialog = NewCrismRatioDialog(images)
    qtbot.addWidget(dialog)
    assert dialog.browse() is images[1]
    assert dialog.data() is images[0]


def test_the_dialog_requires_a_map_link_when_the_grids_differ(qtbot, mapPair):
    dialog = NewCrismRatioDialog(list(mapPair))
    qtbot.addWidget(dialog)
    assert dialog.link() is Link.MAP
    assert not dialog.pixelAvailable()


def test_the_dialog_allows_either_link_on_one_grid(qtbot, pixelPair):
    dialog = NewCrismRatioDialog(list(pixelPair))
    qtbot.addWidget(dialog)
    assert dialog.pixelAvailable() and dialog.mapAvailable()


def test_the_dialog_refuses_a_map_link_with_an_ungeoreferenced_image(
    qtbot, monkeypatch
):
    images = [raster("browse", (30, 40), georeferenced=False), raster("data", (30, 40))]
    dialog = NewCrismRatioDialog(images)
    qtbot.addWidget(dialog)
    assert not dialog.mapAvailable()
    assert dialog.link() is Link.PIXEL
    assert "georeferenc" in dialog.linkNote.text()


def test_the_dialog_cannot_open_when_no_link_works(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: shown.append(args[2]))
    images = [raster("browse", (15, 20), georeferenced=False), raster("data", (30, 40))]
    dialog = NewCrismRatioDialog(images)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(dialog, "accept", lambda: shown.append("accepted"))
    dialog._accept()
    assert shown and shown[0] != "accepted"


# --- fix round 1 -----------------------------------------------------------------


def test_a_micrometer_cube_gets_the_same_verdict_as_a_nanometer_cube(qtbot):
    browse = raster("browse", (30, 40), seed=1)
    nm = raster("data", (30, 40), seed=2, bands=60)
    um = raster("data", (30, 40), seed=2, bands=60, micrometers=True)
    verdicts = []
    for data in (nm, um):
        workspace = build(qtbot, browse, data, Link.PIXEL)
        _pair(workspace)
        verdicts.append(
            [
                (w.window.name, round(w.depth, 6), round(w.dipNm, 3), round(w.slope, 6))
                for w in workspace.plots.readout.lastResult.windows
            ]
        )
        x = workspace.plots.numeratorPlot.plots[0].data()[0]
        np.testing.assert_allclose(x, np.linspace(1000, 2500, 60))
    assert verdicts[0] and verdicts[0] == verdicts[1]


def test_p_without_a_denominator_adds_no_stale_verdict(qtbot, pixelPair):
    workspace = build(qtbot, pixelPair[0], pixelPair[1], Link.PIXEL)
    _pair(workspace)
    key(workspace, Qt.Key.Key_Escape)
    tool(workspace).onPointerEvent(press(LEFT, 20.5, 10.5))

    key(workspace, Qt.Key.Key_P)

    (point,) = workspace.pointCollection.getAllPoints()
    assert "denominator" not in point.name


def test_clicking_the_if_view_works(qtbot, mapPair):
    workspace = build(qtbot, mapPair[0], mapPair[1], Link.MAP)
    tool(workspace, 1).onPointerEvent(press(LEFT, 7.5, 5.5))
    tool(workspace, 1).onPointerEvent(press(RIGHT, 19.5, 17.5))
    np.testing.assert_array_equal(
        tool(workspace, 1).selection.numerator, boxPolygonPixels(7, 5, 5, 5)
    )
    assert len(workspace.plots.ratioPlot.plots) == 1
    np.testing.assert_array_equal(
        tool(workspace, 0).selection.denominator, boxPolygonPixels(19, 17, 5, 5)
    )


def test_the_lock_uses_the_if_geometry_under_a_map_link(qtbot, mapPair, monkeypatch):
    geometry = ColumnGeometry(ir_sample=np.tile(np.arange(40.0), (30, 1)))
    seen = []

    def load(path, *_args):
        seen.append(path)
        return geometry

    monkeypatch.setattr(ratio_explorer, "columnGeometryFor", load)
    monkeypatch.setattr(
        crism_ratio_workspace, "columnGeometryFor", lambda *_args: geometry
    )
    browse, data = mapPair
    monkeypatch.setattr(
        type(data), "filePath", property(lambda s: f"/fake/{s.name}.img")
    )
    workspace = build(qtbot, browse, data, Link.MAP)

    tool(workspace).onPointerEvent(press(LEFT, 3.5, 2.5))  # I/F pixel (7, 5)
    tool(workspace).onPointerEvent(press(RIGHT, 9.5, 8.5))  # I/F row 17

    np.testing.assert_array_equal(
        tool(workspace).selection.denominator, boxPolygonPixels(7, 17, 5, 5)
    )
    assert set(seen) == {"/fake/data.img"}
