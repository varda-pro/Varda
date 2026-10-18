"""Pulling a reference line (marker) off the Y axis, like a guide off a ruler:
a left-drag that starts on the Y axis and goes mainly sideways drops a marker
where it's released; a drag along the axis still moves Y."""

import pyqtgraph as pg
import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QLabel

from varda.plotting._tests.test_plot_axis_wheel import (
    X,
    _drag,
    _dragThrough,
    _fitYInsideTheData,
    _overAxis,
    _settle,
    _spectrum,
)
from varda.plotting.pixel_spectra_plot import PixelSpectraPlotWidget
from varda.plotting.plot import RangeMode, VardaPlotWidget
from varda.plotting.view_box import ModifierDragViewBox


@pytest.fixture(params=[VardaPlotWidget, PixelSpectraPlotWidget])
def widget(request, qtbot):
    widget = request.param()
    widget.resize(900, 450)
    qtbot.addWidget(widget)
    widget.plot(X, _spectrum(0.4), name="first")
    _settle(qtbot, widget, widget.viewBox)
    return widget


def _wavelengthAt(widget: VardaPlotWidget, scenePos: QPointF) -> float:
    return widget.viewBox.mapSceneToView(scenePos).x()


def test_a_sideways_drag_off_the_y_axis_drops_a_marker_where_released(qtbot, widget):
    start = _overAxis(widget.plotItem, "left")
    offset = QPointF(300, 10)
    release = start + offset
    rangeBefore = widget.viewBox.viewRange()

    _drag(qtbot, widget.gv, start, offset)
    _settle(qtbot, widget, widget.viewBox)

    assert len(widget.markers) == 1
    assert widget.markers[0].value() == pytest.approx(
        _wavelengthAt(widget, release), abs=1.0
    )
    assert widget.markerList.count() == 1  # listed like any marker
    xAfter, yAfter = widget.viewBox.viewRange()  # no pan
    assert (xAfter, yAfter) == (
        pytest.approx(rangeBefore[0]),
        pytest.approx(rangeBefore[1]),
    )
    assert widget.viewConfig.rangeMode.value is RangeMode.AUTO


def test_the_pulled_marker_follows_the_cursor_while_dragging(qtbot, widget):
    start = _overAxis(widget.plotItem, "left")
    path = [start + QPointF(dx, 0) for dx in (0, 40, 100, 160, 220)]
    shown: list[tuple[float, list[float]]] = []
    # after the plot has handled each move: where the cursor is, the markers
    widget.viewBox.sigGuideMoved.connect(
        lambda x: shown.append((x, [m.value() for m in widget.markers]))
    )

    _dragThrough(qtbot, widget.gv, path)

    cursorXs = [x for x, _ in shown]
    assert len(set(cursorXs)) >= 3  # it moved along
    assert cursorXs == sorted(cursorXs)
    assert cursorXs[-1] == pytest.approx(_wavelengthAt(widget, path[-1]))
    for x, markers in shown:
        assert markers == [pytest.approx(x)]


def test_a_pulled_marker_is_a_normal_marker(qtbot, widget):
    start = _overAxis(widget.plotItem, "left")
    _drag(qtbot, widget.gv, start, QPointF(250, 0))
    (marker,) = widget.markers
    assert marker.movable

    widget.removeMarker(marker)
    assert widget.markers == []


def test_a_vertical_drag_on_the_y_axis_moves_y_and_adds_no_marker(qtbot, widget):
    _fitYInsideTheData(qtbot, widget)
    xBefore, yBefore = widget.viewBox.viewRange()

    _drag(qtbot, widget.gv, _overAxis(widget.plotItem, "left"), QPointF(3, 60))
    _settle(qtbot, widget, widget.viewBox)

    xAfter, yAfter = widget.viewBox.viewRange()
    assert widget.markers == []
    assert xAfter == pytest.approx(xBefore)
    assert yAfter != pytest.approx(yBefore)
    assert widget.viewConfig.rangeMode.value is RangeMode.MANUAL  # as before


def test_releasing_back_over_the_axis_cancels_the_marker(qtbot, widget):
    start = _overAxis(widget.plotItem, "left")
    path = [start + QPointF(dx, 0) for dx in (0, 60, 160, 60, 0)]

    _dragThrough(qtbot, widget.gv, path)
    _settle(qtbot, widget, widget.viewBox)

    assert widget.markers == []
    assert widget.markerList.count() == 0


def test_releasing_outside_the_plot_area_cancels_the_marker(qtbot, widget):
    start = _overAxis(widget.plotItem, "left")
    below = widget.viewBox.sceneBoundingRect().bottom() + 20
    path = [start, start + QPointF(80, 0), QPointF(start.x() + 200, below)]

    _dragThrough(qtbot, widget.gv, path)

    assert widget.markers == []


def test_a_sideways_right_drag_on_the_y_axis_still_stretches(qtbot, widget):
    _fitYInsideTheData(qtbot, widget)
    yBefore = widget.viewBox.viewRange()[1]

    _drag(
        qtbot,
        widget.gv,
        _overAxis(widget.plotItem, "left"),
        QPointF(200, 60),
        Qt.MouseButton.RightButton,
    )
    _settle(qtbot, widget, widget.viewBox)
    assert widget.markers == []
    assert widget.viewBox.viewRange()[1] != pytest.approx(yBefore)


def test_a_plain_view_box_does_not_pull_guides(qtbot):
    """Other plots sharing the view box (e.g. the histogram) keep their drags."""
    view = pg.PlotWidget(viewBox=ModifierDragViewBox())
    view.resize(600, 400)
    qtbot.addWidget(view)
    received: list[object] = []
    viewBox = view.getPlotItem().getViewBox()
    viewBox.sigGuideReleased.connect(received.append)
    view.plot(X, _spectrum(0.4))
    _settle(qtbot, view, viewBox)

    _drag(qtbot, view, _overAxis(view.getPlotItem(), "left"), QPointF(200, 0))
    assert received == []


def test_the_hint_mentions_pulling_a_marker_off_the_y_axis(widget):
    texts = " ".join(label.text() for label in widget.findChildren(QLabel))
    assert "off the Y axis" in texts
