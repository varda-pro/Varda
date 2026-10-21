"""Tests for the interaction signals emitted by ImageListWidget."""

import numpy as np
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtWidgets import QApplication, QListWidgetItem

from varda.all_images_view_list.imageview_list import (
    ImageItemDelegate,
    ImageListWidget,
)
from varda.common.entities import VardaRaster
from varda.common.observable_list import ObservableList
from varda.geo._tests.fixtures import writeWgs84
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.image_loading_service import loadRaster
from varda.utilities.debug import generate_random_image


def _showWidget(qtbot, *rasters) -> ImageListWidget:
    images = ObservableList()
    for raster in rasters:
        images.append(raster)
    widget = ImageListWidget(images)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def _doubleClick(qtbot, widget: ImageListWidget, pos: QPoint) -> None:
    # Qt only emits itemDoubleClicked for an index that was already pressed, so a
    # real double-click is click-then-double-click.
    qtbot.mouseClick(widget.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    qtbot.mouseDClick(widget.viewport(), Qt.MouseButton.LeftButton, pos=pos)


def _rightClick(widget: ImageListWidget, pos: QPoint) -> None:
    # The offscreen platform doesn't synthesize context-menu events from a
    # right-press, so deliver the event the platform would.
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, pos, widget.viewport().mapToGlobal(pos)
    )
    QApplication.sendEvent(widget.viewport(), event)


def test_double_click_emits_activated_with_that_image(qtbot):
    first = generate_random_image((20, 20, 10))
    second = generate_random_image((20, 20, 10))
    widget = _showWidget(qtbot, first, second)
    pos = widget.visualItemRect(widget.item(1)).center()

    with qtbot.waitSignal(widget.sigImagesActivated) as blocker:
        _doubleClick(qtbot, widget, pos)

    assert blocker.args == [[second]]


def test_right_click_emits_context_request_with_clicked_image_first(qtbot):
    first = generate_random_image((20, 20, 10))
    second = generate_random_image((20, 20, 10))
    widget = _showWidget(qtbot, first, second)
    widget.item(0).setSelected(True)
    widget.item(1).setSelected(True)
    pos = widget.visualItemRect(widget.item(1)).center()

    with qtbot.waitSignal(widget.sigContextMenuRequested) as blocker:
        _rightClick(widget, pos)

    images, _globalPos = blocker.args
    assert images == [second, first]


def test_right_click_on_empty_space_emits_nothing(qtbot):
    widget = _showWidget(qtbot, generate_random_image((20, 20, 10)))
    emptyPos = widget.viewport().rect().bottomRight() - QPoint(2, 2)
    assert widget.itemAt(emptyPos) is None

    with qtbot.assertNotEmitted(widget.sigContextMenuRequested):
        _rightClick(widget, emptyPos)


def _item(widget: ImageListWidget, row: int) -> QListWidgetItem:
    item = widget.item(row)
    assert item is not None
    return item


def test_a_wgs84_mars_image_has_a_badge_and_says_why(qtbot, tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    widget = _showWidget(qtbot, image)
    item = _item(widget, 0)
    assert item.toolTip() == (
        "scene.tif\nMap: Its CRS says Earth; read as Mars (from its label)."
    )
    index = widget.indexFromItem(item)
    assert index.data(Qt.ItemDataRole.ToolTipRole) == item.toolTip()  # the view's
    assert isinstance(widget.itemDelegate(), ImageItemDelegate)
    assert ImageItemDelegate.hasBadge(index)


def test_an_image_without_findings_has_no_badge(qtbot):
    image = generate_random_image((20, 20, 10))
    widget = _showWidget(qtbot, image)
    item = _item(widget, 0)
    assert item.toolTip() == image.name
    assert not ImageItemDelegate.hasBadge(widget.indexFromItem(item))


def test_painting_the_badge_makes_no_new_thumbnail(qtbot, tmp_path, monkeypatch):
    image = loadRaster(str(writeWgs84(tmp_path)))
    widget = _showWidget(qtbot, image)
    widget.thumbnail(image)  # made once
    made = []

    def record(*args, **kwargs) -> None:
        made.append(args)

    monkeypatch.setattr(
        "varda.all_images_view_list.imageview_list.thumbnailRgba", record
    )
    monkeypatch.setattr("varda.geo.health.checkCrs", record)
    monkeypatch.setattr("varda.image_loading.crs_check.checkCrs", record)
    viewport = widget.viewport()
    assert viewport is not None
    for _ in range(3):
        viewport.repaint()
    assert made == []  # the badge reads the health checked at load


def test_an_assumed_wavelength_unit_is_named_in_the_tooltip(qtbot):
    """Release QA 0.12.0 #13: "Wavelength units not declared; assuming nm"
    went only to the log."""
    data = np.random.default_rng(0).random((6, 7, 5)).astype(np.float32)
    image = VardaRaster(
        ArrayDataSource(data, wavelengths=np.linspace(400, 2500, 5)), name="big"
    )
    widget = _showWidget(qtbot, image)
    assert _item(widget, 0).toolTip() == (
        "big\nWavelength units not declared; assuming nm from the values (400–2500)"
    )


LONG_NAME = "qa_cube_reflectance_corrected.img PCA"


def test_a_long_name_keeps_its_end(qtbot):
    """Release QA 0.12.0: "qa_cube.im…" hid that it was the PCA result."""
    widget = _showWidget(qtbot)
    metrics = widget.fontMetrics()
    shown = ImageItemDelegate.labelText(LONG_NAME, metrics, 100)
    assert shown != LONG_NAME and "…" in shown
    assert shown.startswith("qa") and shown.endswith("PCA")
    assert metrics.horizontalAdvance(shown) <= 100


def test_a_narrow_list_never_scrolls_sideways(qtbot):
    data = np.zeros((20, 20, 3), dtype=np.float32)
    image = VardaRaster(ArrayDataSource(data), name=LONG_NAME)
    widget = _showWidget(qtbot, image, generate_random_image((20, 20, 10)))
    widget.resize(widget.minimumSizeHint().width(), 300)
    qtbot.wait(50)
    viewport = widget.viewport()
    assert viewport is not None
    for row in range(widget.count()):
        item = widget.item(row)
        assert item is not None
        assert widget.visualItemRect(item).right() <= viewport.width(), item.text()
    bar = widget.horizontalScrollBar()
    assert bar is None or not bar.isVisible()
