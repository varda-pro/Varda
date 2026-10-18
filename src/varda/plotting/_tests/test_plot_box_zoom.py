"""The zoom box (pyqtgraph's rubber band) never outlives its drag: a box zoom
finishes even if the modifier is let go first, a drag in the plot doesn't turn
into dragging the selected curve away, and Esc clears any leftover box."""

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from varda.plotting._tests.test_plot_axis_wheel import X, _settle, _spectrum
from varda.plotting.plot import VardaPlotWidget

Modifier = Qt.KeyboardModifier
LEFT = Qt.MouseButton.LeftButton
NONE = Qt.MouseButton.NoButton


@pytest.fixture
def widget(qtbot):
    widget = VardaPlotWidget()
    widget.resize(900, 450)
    qtbot.addWidget(widget)
    widget.plot(X, _spectrum(0.4), name="first")
    _settle(qtbot, widget, widget.viewBox)
    return widget


def _send(widget, kind, scenePos, button, buttons, modifiers) -> None:
    viewport = widget.gv.viewport()
    pos = QPointF(widget.gv.mapFromScene(scenePos))
    QApplication.sendEvent(
        viewport,
        QMouseEvent(
            kind,
            pos,
            QPointF(viewport.mapToGlobal(pos.toPoint())),
            button,
            buttons,
            modifiers,
        ),
    )


def _press(widget, at: QPointF, modifiers=Modifier.NoModifier) -> None:
    _send(widget, QEvent.Type.MouseButtonPress, at, LEFT, LEFT, modifiers)


def _move(widget, to: QPointF, modifiers=Modifier.NoModifier) -> None:
    _send(widget, QEvent.Type.MouseMove, to, NONE, LEFT, modifiers)


def _release(widget, at: QPointF, modifiers=Modifier.NoModifier) -> None:
    _send(widget, QEvent.Type.MouseButtonRelease, at, LEFT, NONE, modifiers)


def _boxDrag(
    qtbot,
    widget,
    start: QPointF,
    end: QPointF,
    modifiers=Modifier.MetaModifier,
    releaseModifiers=None,
    release: bool = True,
) -> None:
    """A Cmd/Ctrl/Shift-drag from ``start`` to ``end`` (scene px), released
    with ``releaseModifiers`` held (default: the same ones), or never."""
    _press(widget, start, modifiers)
    for step in range(1, 6):
        qtbot.wait(25)  # pyqtgraph drops moves that come too fast
        _move(widget, start + (end - start) * (step / 5), modifiers)
    if release:
        qtbot.wait(25)
        _release(
            widget, end, modifiers if releaseModifiers is None else releaseModifiers
        )


def _plotArea(widget) -> tuple[QPointF, QPointF]:
    """Two points inside the plot area, away from the curve's ends."""
    rect = widget.viewBox.sceneBoundingRect()
    return (
        QPointF(rect.left() + 0.3 * rect.width(), rect.top() + 0.1 * rect.height()),
        QPointF(rect.left() + 0.6 * rect.width(), rect.top() + 0.3 * rect.height()),
    )


def _xSpan(widget) -> float:
    xMin, xMax = widget.viewBox.viewRange()[0]
    return xMax - xMin


def _boxShown(widget) -> bool:
    return widget.viewBox.rbScaleBox.isVisible()


@pytest.mark.parametrize(
    "modifier",
    [Modifier.MetaModifier, Modifier.ControlModifier, Modifier.ShiftModifier],
)
def test_a_box_zoom_hides_its_box_and_zooms(qtbot, widget, modifier):
    start, end = _plotArea(widget)
    spanBefore = _xSpan(widget)

    _boxDrag(qtbot, widget, start, end, modifiers=modifier)

    assert not _boxShown(widget)
    assert _xSpan(widget) < spanBefore


def test_letting_go_of_the_modifier_before_the_button_still_finishes_the_zoom(
    qtbot, widget
):
    start, end = _plotArea(widget)
    spanBefore = _xSpan(widget)

    _boxDrag(qtbot, widget, start, end, releaseModifiers=Modifier.NoModifier)

    assert not _boxShown(widget)
    assert _xSpan(widget) < spanBefore


def test_two_quick_box_zooms_leave_no_box(qtbot, widget):
    start, end = _plotArea(widget)
    _boxDrag(qtbot, widget, start, end, releaseModifiers=Modifier.NoModifier)
    _boxDrag(qtbot, widget, end, start, releaseModifiers=Modifier.NoModifier)
    assert not _boxShown(widget)


def test_pressing_the_modifier_mid_drag_keeps_panning(qtbot, widget):
    start, end = _plotArea(widget)
    _press(widget, start)
    qtbot.wait(25)
    _move(widget, start + QPointF(20, 0))
    for step in range(1, 4):
        qtbot.wait(25)
        _move(widget, start + (end - start) * (step / 3), Modifier.MetaModifier)
    assert not _boxShown(widget)
    _release(widget, end, Modifier.MetaModifier)
    assert not _boxShown(widget)


def test_a_box_zoom_with_a_curve_selected_zooms_and_does_not_drag_the_curve(
    qtbot, widget, monkeypatch
):
    dragged: list[object] = []
    monkeypatch.setattr(widget.gv, "_initiateDrag", dragged.append)
    widget.selectPlot(widget.plots[0])
    start, end = _plotArea(widget)
    spanBefore = _xSpan(widget)

    _boxDrag(qtbot, widget, start, end)

    assert dragged == []
    assert not _boxShown(widget)
    assert _xSpan(widget) < spanBefore


def test_a_drag_after_one_whose_release_was_lost_leaves_no_box(qtbot, widget):
    start, end = _plotArea(widget)
    # The release goes elsewhere (a drag and drop's own event loop, another
    # window), so pyqtgraph never sees the drag finish
    _boxDrag(qtbot, widget, start, end, release=False)
    assert _boxShown(widget)

    _boxDrag(qtbot, widget, start, end, modifiers=Modifier.NoModifier)

    assert not _boxShown(widget)


def test_a_new_drag_clears_a_leftover_box_as_it_starts(qtbot, widget):
    start, _ = _plotArea(widget)
    widget.viewBox.rbScaleBox.show()  # whatever left it behind

    _press(widget, start)
    for step in range(1, 4):
        qtbot.wait(25)
        _move(widget, start + QPointF(20 * step, 0))

    assert not _boxShown(widget)


def test_releasing_the_mouse_clears_a_leftover_box(qtbot, widget):
    start, _ = _plotArea(widget)
    widget.viewBox.rbScaleBox.show()
    _press(widget, start)
    _release(widget, start)
    assert not _boxShown(widget)


@pytest.mark.parametrize("target", ["gv", "widget"])
def test_escape_clears_a_leftover_box(qtbot, widget, target):
    widget.viewBox.rbScaleBox.show()
    receiver = widget.gv if target == "gv" else widget
    QApplication.sendEvent(
        receiver,
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Modifier.NoModifier),
    )
    assert not _boxShown(widget)


def test_a_plain_drag_from_the_selected_curve_still_drags_it(
    qtbot, widget, monkeypatch
):
    dragged: list[object] = []
    monkeypatch.setattr(widget.gv, "_initiateDrag", dragged.append)
    curve = widget.plots[0]
    widget.selectPlot(curve)
    x, y = curve.displayedData()
    onCurve = widget.viewBox.mapViewToScene(QPointF(x[100], y[100]))

    _press(widget, onCurve)
    qtbot.wait(25)
    _move(widget, onCurve + QPointF(0, 30))
    _release(widget, onCurve + QPointF(0, 30))

    assert dragged == [curve]
    assert not _boxShown(widget)
