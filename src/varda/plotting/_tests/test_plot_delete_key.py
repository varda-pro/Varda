"""Backspace / Delete removes the selected curve from a plot, when the plot
has the keyboard focus (a click on it gives it the focus), not while typing
in its sidebar."""

import numpy as np
import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from varda.plotting.plot import VardaPlotWidget


@pytest.fixture
def widget(qtbot):
    widget = VardaPlotWidget()
    qtbot.addWidget(widget)
    return widget


def _twoCurves(widget):
    x = np.linspace(1000.0, 2500.0, 16)
    return widget.plot(x, x * 1e-4, name="a"), widget.plot(x, x * 2e-4, name="b")


@pytest.mark.parametrize("key", [Qt.Key.Key_Backspace, Qt.Key.Key_Delete])
def test_backspace_or_delete_removes_the_selected_curve(widget, qtbot, key):
    a, b = _twoCurves(widget)
    widget.selectPlot(a)

    qtbot.keyClick(widget, key)

    assert a not in widget.plots
    assert b in widget.plots
    assert widget.selectedCurve is None


def test_the_keys_do_nothing_without_a_selection(widget, qtbot):
    a, b = _twoCurves(widget)

    qtbot.keyClick(widget, Qt.Key.Key_Backspace)

    assert widget.plots == [a, b]


# --- with real clicks and the keyboard focus -------------------------------------------


@pytest.fixture
def shown(qtbot):
    widget = VardaPlotWidget()
    widget.resize(900, 450)
    qtbot.addWidget(widget)
    x = np.linspace(1000.0, 2500.0, 16)
    widget.plot(x, np.full_like(x, 0.3), name="low")
    widget.plot(x, np.full_like(x, 0.6), name="high")
    widget.show()
    widget.activateWindow()
    qtbot.waitUntil(widget.isActiveWindow)
    widget.viewBox.updateAutoRange()
    qtbot.wait(20)
    return widget


def _clickCurve(qtbot, widget: VardaPlotWidget, curve) -> None:
    """A real left click on ``curve`` in the middle of the plot."""
    x, y = curve.displayedData()
    scenePos = widget.viewBox.mapViewToScene(QPointF(x[8], y[8]))
    viewport = widget.gv.viewport()
    assert viewport is not None
    qtbot.mouseClick(
        viewport, Qt.MouseButton.LeftButton, pos=widget.gv.mapFromScene(scenePos)
    )


def _press(qtbot, key: Qt.Key) -> None:
    """``key`` to whichever widget has the keyboard focus, as typing does."""
    focus = QApplication.focusWidget()
    assert focus is not None
    qtbot.keyClick(focus, key)


@pytest.mark.parametrize("key", [Qt.Key.Key_Backspace, Qt.Key.Key_Delete])
def test_clicking_a_curve_then_pressing_the_key_removes_it(shown, qtbot, key):
    low, high = shown.plots
    _clickCurve(qtbot, shown, low)
    assert shown.selectedCurve is low
    focus = QApplication.focusWidget()
    assert focus is shown or shown.isAncestorOf(focus)  # the click took focus

    _press(qtbot, key)

    assert shown.plots == [high]
    assert shown.selectedCurve is None


def test_the_keys_do_nothing_with_nothing_selected_after_a_click(shown, qtbot):
    curves = list(shown.plots)
    _clickCurve(qtbot, shown, curves[0])
    shown.deselectPlot()

    _press(qtbot, Qt.Key.Key_Backspace)

    assert shown.plots == curves


@pytest.mark.parametrize("field", ["markerWavelength", "curveSetting", "markerList"])
def test_backspace_in_the_sidebar_does_not_remove_the_curve(shown, qtbot, field):
    low, _high = shown.plots
    _clickCurve(qtbot, shown, low)
    if field == "curveSetting":  # a box in the Selected Curve section
        target = shown.curveSettingsBox.findChildren(QAbstractSpinBox)[0]
    else:
        target = getattr(shown, field)
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton)
    target.setFocus()
    assert QApplication.focusWidget() is target or target.isAncestorOf(
        QApplication.focusWidget()
    )

    for key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
        _press(qtbot, key)

    assert low in shown.plots
    assert shown.selectedCurve is low


def test_a_text_field_beside_the_plot_keeps_its_focus_and_its_backspace(qtbot):
    """New or selected curves don't take the focus from a field being typed
    in (e.g. pixel spectra arriving as the cursor moves over an image)."""
    window = QWidget()
    field = QLineEdit()
    plot = VardaPlotWidget()
    layout = QVBoxLayout(window)
    layout.addWidget(field)
    layout.addWidget(plot)
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    field.setFocus()
    qtbot.keyClicks(field, "abc")

    curve = plot.plot(np.linspace(1000.0, 2500.0, 16), np.full(16, 0.3))
    plot.selectPlot(curve)
    assert QApplication.focusWidget() is field
    _press(qtbot, Qt.Key.Key_Backspace)

    assert field.text() == "ab"
    assert plot.plots == [curve]
