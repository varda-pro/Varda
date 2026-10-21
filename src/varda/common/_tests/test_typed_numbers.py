"""A number box takes a value only when it's typed in full (release QA
0.12.0 #11): Qt's spin boxes dropped the characters they rejected and kept
the rest, so "1e309" became 1309 and "-1" became 1."""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QAbstractSpinBox

from varda.common.parameter import FloatParameter, IntParameter, Vec2Parameter
from varda.common.parameter_widgets import (
    FloatParameterWidget,
    IntParameterWidget,
    Vec2ParameterWidget,
    parameterWidget,
)
from varda.common.vec2 import Vec2


def _text(box: QAbstractSpinBox) -> str:
    edit = box.lineEdit()
    assert edit is not None
    return edit.text()


def _typeInto(qtbot, box, text: str) -> None:
    box.setFocus()
    box.selectAll()
    QTest.keyClicks(box, text)


@pytest.mark.parametrize("junk", ["1e309", "99999999999", "0x10", "-1", "12abc"])
def test_junk_typed_in_a_whole_number_box_is_refused_whole(qtbot, junk):
    param = IntParameter("Components", 10, range=(1, 1000))
    widget = parameterWidget(param)
    assert isinstance(widget, IntParameterWidget)
    qtbot.addWidget(widget)
    widget.show()
    box = widget.spinBox
    _typeInto(qtbot, box, junk)
    assert _text(box) == junk  # kept as typed, never mangled
    QTest.keyClick(box, Qt.Key.Key_Return)
    assert param.get() == 10
    assert box.value() == 10 and _text(box) == "10"
    assert junk in box.refusal and "1 to 1000" in box.refusal


@pytest.mark.parametrize("junk", ["1e309", "0x10", "99999999999", "nm"])
def test_junk_typed_in_a_wavelength_box_is_refused_whole(qtbot, junk):
    param = Vec2Parameter("Wavelengths", Vec2(1000.0, 2600.0), decimals=1, suffix=" nm")
    widget = parameterWidget(param)
    assert isinstance(widget, Vec2ParameterWidget)
    qtbot.addWidget(widget)
    widget.show()
    box = widget.xSpinBox
    _typeInto(qtbot, box, junk)
    assert _text(box) == f"{junk} nm"  # the unit stays after it
    QTest.keyClick(box, Qt.Key.Key_Return)
    assert param.get().x == 1000.0
    assert _text(box) == "1000.0 nm"
    assert junk in box.refusal


def test_a_negative_value_below_a_float_range_is_refused(qtbot):
    param = FloatParameter("Threshold", 5.0, range=(0.0, 10.0))
    widget = parameterWidget(param)
    assert isinstance(widget, FloatParameterWidget)
    qtbot.addWidget(widget)
    widget.show()
    _typeInto(qtbot, widget.spinBox, "-1")
    QTest.keyClick(widget.spinBox, Qt.Key.Key_Return)
    assert param.get() == 5.0
    assert "-1" in widget.spinBox.refusal


def test_a_value_typed_in_full_is_taken_once_and_exactly(qtbot):
    """Not 1, 13 and 130 on the way to 1309."""
    param = IntParameter("Components", 10, range=(1, 2000))
    widget = parameterWidget(param)
    assert isinstance(widget, IntParameterWidget)
    qtbot.addWidget(widget)
    widget.show()
    seen = []
    param.sigParameterChanged.connect(seen.append)
    _typeInto(qtbot, widget.spinBox, "1309")
    assert seen == []
    QTest.keyClick(widget.spinBox, Qt.Key.Key_Return)
    assert seen == [1309] and param.get() == 1309
    assert widget.spinBox.refusal == ""


def test_junk_left_behind_by_moving_on_is_refused_too(qtbot):
    """Typing junk and then clicking elsewhere (e.g. Run) keeps the value
    shown before, never a part of the junk."""
    param = Vec2Parameter("Wavelengths", Vec2(1000.0, 2600.0), suffix=" nm")
    widget = parameterWidget(param)
    assert isinstance(widget, Vec2ParameterWidget)
    qtbot.addWidget(widget)
    widget.show()
    box = widget.ySpinBox
    _typeInto(qtbot, box, "1e309")
    box.finishTyping()  # what leaving the box (focus out) does
    assert param.get().y == 2600.0
    assert "1e309" in box.refusal
