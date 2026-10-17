"""Scrolling over an axis zooms that axis alone, as in the FATT window's plot:
over the Y axis it stretches the spectra vertically, and the stretch stays
(through new spectra) in every range mode until the user resets the view."""

import numpy as np
import pyqtgraph as pg
import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication, QLabel

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.plotting.pixel_spectra_plot import PixelSpectraPlotWidget
from varda.plotting.plot import RangeMode, VardaPlotWidget
from varda.workspaces.linked_images.spectra_panel import LinkedSpectraPanel

X = np.linspace(400.0, 2500.0, 200)


def _spectrum(level: float) -> np.ndarray:
    return level + 0.1 * np.sin(X / 100.0)


def _wheel(view: pg.GraphicsView, scenePos: QPointF, notches: int = 2) -> None:
    """A real wheel event (zooming in) on the view at ``scenePos``."""
    viewPos = QPointF(view.mapFromScene(scenePos))
    viewport = view.viewport()
    assert viewport is not None
    event = QWheelEvent(
        viewPos,
        QPointF(viewport.mapToGlobal(viewPos.toPoint())),
        QPoint(0, 0),
        QPoint(0, 120 * notches),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(viewport, event)


def _overAxis(plotItem: pg.PlotItem, name: str, fraction: float = 0.5) -> QPointF:
    """A point on the ``left`` or ``bottom`` axis, just outside the plot area,
    ``fraction`` of the way along it (from the top or left). (An axis's own
    bounding rect spans the plot when it draws grid lines.)"""
    rect = plotItem.getViewBox().sceneBoundingRect()
    if name == "left":
        return QPointF(rect.left() - 8, rect.top() + fraction * rect.height())
    return QPointF(rect.left() + fraction * rect.width(), rect.bottom() + 8)


def _settle(qtbot, widget, viewBox: pg.ViewBox) -> None:
    """Paint, as pyqtgraph finishes range updates (and auto-range) there."""
    widget.show()
    qtbot.waitExposed(widget)
    qtbot.wait(20)
    viewBox.updateAutoRange()


def _span(r) -> float:
    return float(r[1] - r[0])


@pytest.fixture(params=[VardaPlotWidget, PixelSpectraPlotWidget])
def widget(request, qtbot):
    widget = request.param()
    widget.resize(900, 450)
    qtbot.addWidget(widget)
    widget.plot(X, _spectrum(0.4), name="first")
    _settle(qtbot, widget, widget.viewBox)
    return widget


@pytest.mark.parametrize("mode", list(RangeMode))
def test_scrolling_over_the_y_axis_stretches_y_and_the_stretch_stays(
    qtbot, widget, mode
):
    widget.viewConfig.rangeMode.set(mode)
    _settle(qtbot, widget, widget.viewBox)
    xBefore, yBefore = widget.viewBox.viewRange()
    anchor = _overAxis(widget.plotItem, "left", fraction=0.25)
    anchorY = widget.viewBox.mapSceneToView(anchor).y()

    _wheel(widget.gv, anchor)
    _settle(qtbot, widget, widget.viewBox)
    xAfter, yAfter = widget.viewBox.viewRange()
    assert xAfter == pytest.approx(xBefore)  # X untouched
    assert _span(yAfter) < 0.95 * _span(yBefore)  # Y stretched
    # about the cursor: the value under it stays under it (to the pixel)
    assert widget.viewBox.mapSceneToView(anchor).y() == pytest.approx(
        anchorY, abs=0.01 * _span(yAfter)
    )
    # the user has taken over Y: Manual, with the range boxes showing the view
    assert widget.viewConfig.rangeMode.value is RangeMode.MANUAL
    yBox = widget.rangeConfig.viewRangeY.value
    assert (yBox.x, yBox.y) == pytest.approx(tuple(yAfter))

    widget.plot(X, _spectrum(0.45), name="second")
    _settle(qtbot, widget, widget.viewBox)
    assert widget.viewBox.viewRange()[1] == pytest.approx(yAfter)


def test_the_stretch_stays_while_pixel_spectra_are_replaced(qtbot):
    widget = PixelSpectraPlotWidget()
    widget.resize(900, 450)
    qtbot.addWidget(widget)
    widget.addSpectrum(X, _spectrum(0.4), "pixel")
    widget.viewConfig.rangeMode.set(RangeMode.FIT_Y_TO_X_RANGE)
    _settle(qtbot, widget, widget.viewBox)

    _wheel(widget.gv, _overAxis(widget.plotItem, "left"))
    _settle(qtbot, widget, widget.viewBox)
    stretched = widget.viewBox.viewRange()[1]

    for level in (0.41, 0.39, 0.4):  # hovering over the next pixels
        widget.addSpectrum(X, _spectrum(level), "pixel")
        _settle(qtbot, widget, widget.viewBox)
    assert widget.viewBox.viewRange()[1] == pytest.approx(stretched)


@pytest.mark.parametrize("mode", [RangeMode.AUTO, RangeMode.MANUAL])
def test_scrolling_over_the_x_axis_zooms_x_only(qtbot, widget, mode):
    widget.viewConfig.rangeMode.set(mode)
    _settle(qtbot, widget, widget.viewBox)
    xBefore, yBefore = widget.viewBox.viewRange()

    _wheel(widget.gv, _overAxis(widget.plotItem, "bottom"))
    _settle(qtbot, widget, widget.viewBox)
    xAfter, yAfter = widget.viewBox.viewRange()
    assert _span(xAfter) < 0.95 * _span(xBefore)
    assert yAfter == pytest.approx(yBefore)

    widget.plot(X, _spectrum(0.45), name="second")
    _settle(qtbot, widget, widget.viewBox)
    assert widget.viewBox.viewRange()[0] == pytest.approx(xAfter)


def test_scrolling_over_the_x_axis_in_fit_y_mode_still_refits_y(qtbot, widget):
    widget.viewConfig.rangeMode.set(RangeMode.FIT_Y_TO_X_RANGE)
    _settle(qtbot, widget, widget.viewBox)
    xBefore = widget.viewBox.viewRange()[0]

    _wheel(widget.gv, _overAxis(widget.plotItem, "bottom"))
    _settle(qtbot, widget, widget.viewBox)
    xAfter = widget.viewBox.viewRange()[0]
    assert _span(xAfter) < 0.95 * _span(xBefore)
    assert widget.viewConfig.rangeMode.value is RangeMode.FIT_Y_TO_X_RANGE

    widget.plot(X, _spectrum(0.8), name="higher")
    _settle(qtbot, widget, widget.viewBox)
    xEnd, (_yMin, yMax) = widget.viewBox.viewRange()
    assert xEnd == pytest.approx(xAfter)
    assert yMax >= 0.8  # Y refit to show the new spectrum


def test_fit_to_data_undoes_the_stretch(qtbot, widget):
    fitted = widget.viewBox.viewRange()[1]
    _wheel(widget.gv, _overAxis(widget.plotItem, "left"))
    _settle(qtbot, widget, widget.viewBox)
    assert _span(widget.viewBox.viewRange()[1]) < 0.95 * _span(fitted)

    widget.fitToData()
    _settle(qtbot, widget, widget.viewBox)
    assert widget.viewBox.viewRange()[1] == pytest.approx(fitted, rel=0.05)


def test_the_hint_mentions_scrolling_over_an_axis(widget):
    texts = " ".join(label.text() for label in widget.findChildren(QLabel))
    assert "over an axis" in texts


def test_the_linked_images_spectra_keep_a_y_stretch_like_fatt(qtbot):
    """The Linked Images spectra are plain pyqtgraph plots, like FATT's."""
    rows, cols, bands = np.mgrid[0:4, 0:5, 0:6]
    cube = (0.4 + 0.01 * (rows + cols) + 0.05 * np.sin(bands)).astype(np.float32)
    image = VardaRaster(
        ArrayDataSource(cube, wavelengths=np.linspace(1.0, 2.5, 6)), name="cube"
    )
    panel = LinkedSpectraPanel([image])
    panel.resize(600, 400)
    qtbot.addWidget(panel)
    plot = panel.plots[0]
    viewBox = plot.getViewBox()
    panel.showAt([np.array([1.0, 1.0])])
    _settle(qtbot, panel, viewBox)
    xBefore, yBefore = viewBox.viewRange()

    _wheel(panel.layoutWidget, _overAxis(plot, "left"))
    _settle(qtbot, panel, viewBox)
    xAfter, yAfter = viewBox.viewRange()
    assert xAfter == pytest.approx(xBefore)
    assert _span(yAfter) < 0.95 * _span(yBefore)

    panel.showAt([np.array([3.0, 2.0])])  # the cursor moves on
    _settle(qtbot, panel, viewBox)
    assert viewBox.viewRange()[1] == pytest.approx(yAfter)
