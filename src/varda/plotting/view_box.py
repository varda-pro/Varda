"""A pyqtgraph ViewBox with Varda's drag conventions, shared by every plot."""

from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal

# Holding any of these while dragging zooms to the dragged box instead of panning
RECT_ZOOM_MODIFIERS = (
    Qt.KeyboardModifier.ShiftModifier
    | Qt.KeyboardModifier.ControlModifier
    | Qt.KeyboardModifier.MetaModifier
)


def dragMouseMode(modifiers: Qt.KeyboardModifier) -> int:
    """A plain drag pans; a Shift/Ctrl/Cmd-drag zooms to the dragged box."""
    if modifiers & RECT_ZOOM_MODIFIERS:
        return pg.ViewBox.RectMode
    return pg.ViewBox.PanMode


# How far (scene px, Manhattan) a left-drag on the Y axis travels before its
# direction decides between pulling a guide (sideways) and moving Y (along it)
GUIDE_DECISION_DISTANCE = 6


class ModifierDragViewBox(pg.ViewBox):
    """ViewBox whose left-drag behavior follows the keyboard modifiers, and
    which can pull vertical guides off its Y axis (``pullGuides``)."""

    # The wheel or a drag is about to change one axis alone (0: X, 1: Y),
    # because the cursor is over that axis. Emitted before the range changes.
    sigAxisGesture = pyqtSignal(int)
    # With ``pullGuides`` on, a left-drag from the Y axis that goes mainly
    # sideways pulls a vertical guide, like one off a ruler, instead of moving
    # Y. Each emits the X under the cursor, or None when the cursor is outside
    # the plot area (a release there cancels the guide).
    sigGuideMoved = pyqtSignal(object)
    sigGuideReleased = pyqtSignal(object)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pullGuides = False
        # The current drag: a guide (True), not one (False), or undecided (None)
        self._pullingGuide: bool | None = False

    def mouseDragEvent(self, ev, axis=None):
        if ev.isStart():
            pulls = self.pullGuides and axis == 1
            self._pullingGuide = (
                None if pulls and ev.button() == Qt.MouseButton.LeftButton else False
            )
        if self._pullingGuide is None:
            delta = ev.scenePos() - ev.buttonDownScenePos()
            if delta.manhattanLength() < GUIDE_DECISION_DISTANCE:
                ev.accept()  # keep the drag coming until its direction shows
                if ev.isFinish():
                    self._pullingGuide = False  # too short to be either
                return
            self._pullingGuide = abs(delta.x()) > abs(delta.y())
        if self._pullingGuide:
            ev.accept()
            self._moveGuide(ev)
            return
        if axis in (0, 1):
            self.sigAxisGesture.emit(axis)
        self.setMouseMode(dragMouseMode(ev.modifiers()))
        super().mouseDragEvent(ev, axis)

    def _moveGuide(self, ev) -> None:
        pos = ev.scenePos()
        x = (
            self.mapSceneToView(pos).x()
            if self.sceneBoundingRect().contains(pos)
            else None
        )
        self.sigGuideMoved.emit(x)
        if ev.isFinish():
            self._pullingGuide = False
            self.sigGuideReleased.emit(x)

    def wheelEvent(self, ev, axis=None):
        if axis in (0, 1):
            self.sigAxisGesture.emit(axis)
        super().wheelEvent(ev, axis)
