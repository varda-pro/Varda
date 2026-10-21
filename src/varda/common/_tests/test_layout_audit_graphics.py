"""The layout audit looks inside graphics views too (release QA 0.12.0): a
hint drawn over an image that runs past the bottom of its view, and a drawn
control (the color map's gradient and handles) partly hidden by the bottom
of a scroll area, are cut off just as a squeezed label is."""

import pyqtgraph as pg
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from varda.common.layout_audit import clippedWidgets
from varda.common.ui import PanelScrollArea

HINT = (
    "Pixel Select: Click a pixel to plot its spectrum; Ctrl-drag follows the "
    "pointer and plots each pixel passed"
)


def _view(qtbot, width: int, height: int) -> tuple[pg.GraphicsLayoutWidget, pg.ViewBox]:
    widget = pg.GraphicsLayoutWidget()
    viewBox = widget.addViewBox()
    qtbot.addWidget(widget)
    widget.resize(width, height)
    widget.show()
    qtbot.waitExposed(widget)
    return widget, viewBox


def _pinnedText(viewBox: pg.ViewBox, text: str, width: float) -> pg.TextItem:
    """A hint in the view's corner, wrapped to ``width`` (as tools show them)."""
    item = pg.TextItem(text)
    item.setParentItem(viewBox)
    item.setPos(QPointF(4, 4))
    item.setTextWidth(width)
    return item


def test_the_audit_finds_a_hint_running_past_the_bottom_of_its_view(qtbot):
    widget, viewBox = _view(qtbot, 220, 50)
    _pinnedText(viewBox, HINT, 150)
    problems = clippedWidgets(widget)
    assert len(problems) == 1
    assert "Pixel Select: Click a pixel" in problems[0]
    assert "cut off" in problems[0]


def test_a_hint_that_fits_its_view_is_not_flagged(qtbot):
    widget, viewBox = _view(qtbot, 220, 300)
    _pinnedText(viewBox, HINT, 150)
    assert clippedWidgets(widget) == []


def test_a_label_placed_on_the_image_may_be_panned_out_of_view(qtbot):
    widget, viewBox = _view(qtbot, 220, 120)
    viewBox.setRange(xRange=(0, 10), yRange=(0, 10), padding=0)
    label = pg.TextItem("a label on the image, half out of view")
    viewBox.addItem(label)
    label.setPos(QPointF(8, 5))
    assert clippedWidgets(widget) == []


def _gradientPanel(qtbot, height: int) -> PanelScrollArea:
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.addSpacing(60)
    layout.addWidget(pg.GradientWidget())
    layout.addSpacing(200)
    area = PanelScrollArea(panel)
    qtbot.addWidget(area)
    area.resize(260, height)
    area.show()
    qtbot.waitExposed(area)
    return area


def test_the_audit_finds_a_drawn_control_partly_hidden_by_a_scroll_area(qtbot):
    # its handles hang off the bottom of the scroll area: the bar looks whole
    area = _gradientPanel(qtbot, 85)
    problems = clippedWidgets(area)
    assert len(problems) == 1
    assert problems[0].startswith("GradientWidget")


def test_a_drawn_control_in_full_view_or_scrolled_away_is_not_flagged(qtbot):
    assert clippedWidgets(_gradientPanel(qtbot, 120)) == []
    assert clippedWidgets(_gradientPanel(qtbot, 40)) == []  # wholly below
