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


class ModifierDragViewBox(pg.ViewBox):
    """ViewBox whose left-drag behavior follows the keyboard modifiers."""

    # The wheel is about to zoom one axis alone (0: X, 1: Y), because the
    # cursor is over that axis. Emitted before the range changes.
    sigAxisWheel = pyqtSignal(int)

    def mouseDragEvent(self, ev, axis=None):
        self.setMouseMode(dragMouseMode(ev.modifiers()))
        super().mouseDragEvent(ev, axis)

    def wheelEvent(self, ev, axis=None):
        if axis in (0, 1):
            self.sigAxisWheel.emit(axis)
        super().wheelEvent(ev, axis)
