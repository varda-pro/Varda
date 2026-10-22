"""Up and Down move the selected curve up and down (its Y offset) by a step
of the visible Y range, Shift by a larger one; with nothing selected, or
while typing in the sidebar, they do nothing to the curves."""

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractSpinBox, QApplication

from varda.plotting.plot import NUDGE_FRACTION, NUDGE_SHIFT_FACTOR, RangeMode
from varda.plotting._tests.test_plot_delete_key import _clickCurve, _press, shown  # noqa: F401

SHIFT = Qt.KeyboardModifier.ShiftModifier


def _height(widget) -> float:
    low, high = widget.viewBox.viewRange()[1]
    return high - low


def test_up_and_down_move_the_selected_curve(shown, qtbot):  # noqa: F811
    low, high = shown.plots
    _clickCurve(qtbot, shown, low)
    for key, sign in ((Qt.Key.Key_Up, 1), (Qt.Key.Key_Up, 1), (Qt.Key.Key_Down, -1)):
        before, step = low.config.offset.value, NUDGE_FRACTION * _height(shown)
        _press(qtbot, key)
        assert low.config.offset.value == pytest.approx(before + sign * step)
    assert low.config.offset.value > 0
    assert high.config.offset.value == 0.0
    # its own values don't change, only where it's drawn
    np.testing.assert_allclose(low.data()[1], 0.3)
    np.testing.assert_allclose(low.displayedData()[1], 0.3 + low.config.offset.value)
    assert shown.viewConfig.rangeMode.value is RangeMode.MANUAL  # view held


def test_shift_takes_bigger_steps(shown, qtbot):  # noqa: F811
    low, _ = shown.plots
    _clickCurve(qtbot, shown, low)
    step = NUDGE_FRACTION * _height(shown)
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Down, SHIFT)
    assert low.config.offset.value == pytest.approx(-NUDGE_SHIFT_FACTOR * step)


def test_nothing_moves_without_a_selection(shown, qtbot):  # noqa: F811
    low, high = shown.plots
    _clickCurve(qtbot, shown, low)
    shown.deselectPlot()
    _press(qtbot, Qt.Key.Key_Up)
    assert low.config.offset.value == high.config.offset.value == 0.0


def test_arrows_in_a_sidebar_field_are_the_fields(shown, qtbot):  # noqa: F811
    low, _ = shown.plots
    _clickCurve(qtbot, shown, low)
    box = shown.curveSettingsBox.findChildren(QAbstractSpinBox)[0]
    box.setFocus()
    _press(qtbot, Qt.Key.Key_Up)
    assert low.config.offset.value == 0.0
