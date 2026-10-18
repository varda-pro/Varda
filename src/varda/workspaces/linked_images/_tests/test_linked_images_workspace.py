"""The Linked Images workspace: N images side by side, panned and zoomed
together, one cursor whose spectra show for every image, lines whose
profiles show for every image, and all of it kept with the session."""

import csv

import numpy as np
import pytest
from affine import Affine
from PyQt6.QtCore import QPointF, QRectF
from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.geo._tests.fixtures import CRISM_MTRDR_WKT, WGS84, makeImage
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering.raster_view.viewport_tools.pixel_select_tool import (
    PixelSelectTool,
)
from varda.lines.line_collection import LineKind
from varda.workspaces.linked_images.linked_images_workspace import (
    LinkedImagesWorkspace,
    NewLinkedImagesDialog,
)
from varda.workspaces.linked_images.linking import Link
from varda.workspaces.workspace_kinds import buildWorkspace, describeWorkspace

EQC = CRS.from_user_input("+proj=eqc +R=1560800 +units=m +no_defs")  # Europa


def _product(name, values, transform=Affine(100, 0, 0, 0, -100, 0)):
    """A (20, 30) product with 6 bands from 1 to 5 µm, each pixel's band b
    holding ``values(row, col, b)``."""
    rows, cols, bands = np.mgrid[0:20, 0:30, 0:6]
    return VardaRaster(
        ArrayDataSource(
            values(rows, cols, bands).astype(np.float32),
            wavelengths=np.linspace(1.0, 5.0, 6),
            wavelengthUnits="micrometers",
            transform=transform,
            crs=EQC,
        ),
        name=name,
    )


@pytest.fixture
def products():
    return [
        _product("AGGREGATE", lambda r, c, b: 1000 + 10 * r + c + b),
        _product("COUNT", lambda r, c, b: (r + c + b) % 10),
        _product("FLAGS", lambda r, c, b: (r * c + b) % 3),
    ]


@pytest.fixture
def workspace(qtbot, products):
    widget = LinkedImagesWorkspace(products, list(products), Link.PIXEL)
    qtbot.addWidget(widget)
    return widget


def test_every_image_gets_a_view_and_a_spectrum_plot(workspace, products):
    assert [v.imageEntity for v in workspace.viewports] == products
    assert len(workspace.spectra.plots) == 3
    assert workspace.windowTitle() == "AGGREGATE | COUNT | FLAGS"


def test_the_cursor_shows_every_images_spectrum_there(workspace):
    workspace.placeCursor(1, 4.5, 2.5)  # pixel (4, 2), placed on COUNT
    aggregate, count, flags = (c.getData() for c in workspace.spectra.curves)
    np.testing.assert_allclose(aggregate[0], [1, 1.8, 2.6, 3.4, 4.2, 5])  # µm
    np.testing.assert_allclose(aggregate[1], 1000 + 20 + 4 + np.arange(6))
    np.testing.assert_allclose(count[1], (2 + 4 + np.arange(6)) % 10)
    np.testing.assert_allclose(flags[1], (8 + np.arange(6)) % 3)
    assert "(4, 2)" in workspace.spectra.plots[0].titleLabel.text
    assert workspace.cursorShown == [True, True, True]


def test_a_ctrl_drag_moves_the_cursor_as_it_goes(workspace):
    manager = workspace.toolManagers[2]
    assert isinstance(manager.activeTool, PixelSelectTool)  # on from the start
    assert manager.toolActions[PixelSelectTool].isChecked()
    manager.activeTool.sigPixelDragged.emit(QPointF(7.2, 3.9))
    assert workspace.cursorAt == (2, 7.2, 3.9)
    _, count = workspace.spectra.curves[1].getData()
    np.testing.assert_allclose(count, (3 + 7 + np.arange(6)) % 10)


def test_by_map_location_a_finer_image_is_read_where_the_cursor_is(qtbot, products):
    fine = _product(
        "FINE", lambda r, c, b: 100 * r + c, transform=Affine(50, 0, 0, 0, -50, 0)
    )
    widget = LinkedImagesWorkspace([products[0], fine], [], Link.MAP)
    qtbot.addWidget(widget)
    widget.placeCursor(0, 4.5, 2.5)  # 450 m east, 250 m south: the fine (9, 5)
    _, values = widget.spectra.curves[1].getData()
    np.testing.assert_allclose(values, 100 * 5 + 9)
    # off an image: no curve for it
    widget.placeCursor(0, 25.5, 15.5)  # 2550 m east: past the fine grid's 1500 m
    _, values = widget.spectra.curves[1].getData()
    assert values is None or len(values) == 0
    assert widget.cursorShown == [True, False]


def test_panning_one_view_pans_them_all(workspace):
    first, second, third = workspace.viewports
    second.setViewRange(QRectF(5, 5, 10, 8))
    second.sigViewRangeChangedManually.emit()
    for view in (first, third):
        rect = view.viewRect()
        assert rect.center().x() == pytest.approx(
            second.viewRect().center().x(), abs=0.5
        )
        assert rect.width() == pytest.approx(second.viewRect().width(), rel=0.05)


def test_a_line_drawn_on_one_image_profiles_them_all(workspace):
    workspace.addLine(1, np.array([[2.5, 2.5], [20.5, 12.5]]), LineKind.STRAIGHT)
    (line,) = workspace.lines.lines()
    assert [len(c) for c in workspace.imageLines] == [1, 1, 1]
    assert all(len(p.profiles) == 1 for p in workspace.profilePlots)
    profile = next(iter(workspace.profilePlots[0].profiles.values()))
    assert profile.values[0] == pytest.approx(1000 + 20 + 2)  # band 1 at (2, 2)
    workspace.lineManager.select([line.fid])
    workspace.lines.removeLine(line.fid)
    assert all(len(c) == 0 for c in workspace.imageLines)


def test_the_workspace_comes_back_from_a_session(workspace, products, qtbot):
    workspace.placeCursor(0, 3.5, 6.5)
    workspace.addLine(0, np.array([[1, 1], [9, 9]]), LineKind.STRAIGHT)
    workspace.renderers[1].settings.mono.band.set(4)
    described = describeWorkspace(workspace)
    assert described == ("linked", products)
    restored = buildWorkspace(
        "linked", products, list(products), workspace.sessionState()
    )
    assert isinstance(restored, LinkedImagesWorkspace)
    qtbot.addWidget(restored)
    assert restored.link is Link.PIXEL
    assert restored.cursorAt == (0, 3.5, 6.5)
    assert [line.name for line in restored.lines.lines()] == ["Line 1"]
    assert restored.renderers[1].settings.mono.band.get() == 4


def test_the_new_workspace_dialog_offers_the_images_on_one_grid(qtbot, products):
    other = _product("OTHER", lambda r, c, b: r, transform=Affine(9, 0, 0, 0, -9, 0))
    dialog = NewLinkedImagesDialog([*products, other])
    qtbot.addWidget(dialog)
    assert dialog.chosen() == products  # the first image's grid, ticked
    dialog.linkBox.setCurrentText(Link.MAP.value)
    assert dialog.link() is Link.MAP


def test_a_bit_mask_shows_as_bits_by_wavelength(qtbot, products):
    exclude = VardaRaster(
        ArrayDataSource(
            np.full((20, 30, 6), 0b1000000000000000101, np.uint32),
            wavelengths=np.linspace(1.0, 5.0, 6),
            wavelengthUnits="micrometers",
        ),
        name="MISE_EXCLUDE",
    )
    widget = LinkedImagesWorkspace([products[0], exclude], [], Link.PIXEL)
    qtbot.addWidget(widget)
    panel = widget.spectra
    assert panel.asBits == [False, True]  # named like a mask, whole numbers
    widget.placeCursor(0, 3.5, 3.5)
    shown = panel.bitImages[1].image
    assert shown is not None and shown.shape == (19, 6)  # bits 0-18 by band
    np.testing.assert_array_equal(shown[[0, 1, 2, 18], 0], [1, 0, 1, 1])
    assert "18 bits set, 3.0 per band" in panel.plots[1].titleLabel.text
    panel.bitBoxes[exclude.name].setChecked(False)  # back to a line
    assert not panel.asBits[1]
    _, values = panel.curves[1].getData()
    assert values[0] == 0b1000000000000000101


def test_every_view_and_panel_is_a_dock_and_display_has_histograms(workspace):
    import PyQt6Ads as ads

    assert [d.windowTitle() for d in workspace.viewDocks] == [
        "AGGREGATE",
        "COUNT",
        "FLAGS",
    ]
    closable = ads.CDockWidget.DockWidgetFeature.DockWidgetClosable
    assert not any(d.features().value & closable.value for d in workspace.viewDocks)
    panels = {d.windowTitle() for d in workspace.dockManager.dockWidgetsMap().values()}
    assert {"Display", "Lines", "Linked Spectra", "Linked Profiles"} <= panels
    assert [h.imageRenderer for h in workspace.histograms] == workspace.renderers


def test_each_spectrum_plot_pans_and_zooms_on_its_own(workspace):
    workspace.placeCursor(0, 4.5, 2.5)
    first, second = (p.getViewBox() for p in workspace.spectra.plots[:2])
    first.setXRange(2.0, 3.0, padding=0)
    assert second.viewRange()[0] != pytest.approx([2.0, 3.0])


def test_the_spectra_csv_says_where_and_what_x_is(workspace, tmp_path):
    workspace.placeCursor(1, 4.5, 2.5)  # pixel (4, 2): 450 m east, 250 m south
    path = tmp_path / "spectra.csv"
    workspace.spectra.exportTo(path)
    lines = path.read_text().splitlines()
    comments = [line for line in lines if line.startswith("#")]
    assert "Varda" in comments[0] and any("AGGREGATE: CRS" in c for c in comments)
    rows = list(csv.reader(line for line in lines if not line.startswith("#")))
    assert rows[0] == ["image", "col", "row", "map_x", "map_y", "x", "x_unit", "value"]
    first = rows[1]
    assert first[:3] == ["AGGREGATE", "4", "2"]
    assert (float(first[3]), float(first[4])) == pytest.approx((450.0, -250.0))
    assert (float(first[5]), first[6]) == (pytest.approx(1.0), "µm")
    assert float(first[7]) == pytest.approx(1000 + 20 + 4)


def test_a_notice_says_when_images_are_not_linked_as_asked(workspace, qtbot, products):
    workspace.show()
    assert not workspace.linkNotice.isVisible()  # one grid, linked by pixel
    other = _product("OTHER", lambda r, c, b: r, transform=Affine(9, 0, 0, 0, -9, 0))
    widget = LinkedImagesWorkspace([products[0], other], [], Link.PIXEL)
    qtbot.addWidget(widget)
    widget.show()
    assert widget.linkNotice.isVisible() and "OTHER" in widget.linkNotice.text()
    plain = VardaRaster(
        ArrayDataSource(np.zeros((20, 30, 6), np.float32)), name="PLAIN"
    )
    byMap = LinkedImagesWorkspace([products[0], plain], [], Link.MAP)
    qtbot.addWidget(byMap)
    byMap.show()
    assert byMap.linkNotice.isVisible() and "PLAIN" in byMap.linkNotice.text()


def test_images_of_two_bodies_are_not_linked_and_nothing_breaks(qtbot):
    mars = makeImage(
        CRISM_MTRDR_WKT, centerLonLat=(10.0, 15.0), pixelSize=18.0, name="CRISM"
    )
    earth = makeImage(WGS84, centerLonLat=(10.0, 15.0), pixelSize=0.01, name="EARTH")
    widget = LinkedImagesWorkspace([mars, earth], [], Link.MAP)
    qtbot.addWidget(widget)
    widget.show()
    assert "EARTH: on Earth, not Mars; not linked" in widget.linkNotice.text()

    widget.placeCursor(0, 3.5, 6.5)
    assert widget.cursorShown == [True, False]
    widget.placeCursor(1, 3.5, 6.5)
    assert widget.cursorShown == [False, True]

    before = widget.viewports[0].viewRect()
    widget.viewports[1].setViewRange(QRectF(5, 5, 10, 8))
    widget.viewports[1].sigViewRangeChangedManually.emit()
    assert widget.viewports[0].viewRect() == before

    widget.addLine(0, np.array([[1, 1], [9, 9]]), LineKind.STRAIGHT)
    assert [len(c) for c in widget.imageLines] == [1, 0]
    assert widget.addLine(1, np.array([[1, 1], [9, 9]]), LineKind.STRAIGHT) is None
    assert len(widget.lines.lines()) == 1
