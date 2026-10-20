"""Backend-neutral input events delivered to viewport tools.

Tools used to receive raw `QGraphicsSceneMouseEvent`s and map coordinates
themselves (`imageItem.mapFromScene`, then `localToImage`). That tied every tool
to the pyqtgraph scene graph. Instead, the viewport now translates its backend's
native events into these plain dataclasses — coordinates are pre-mapped once, in
the viewport (the only place that knows the backend) — and hands them to tools.

Coordinates come in two frames so tools don't have to map anything:
  - `localPos`: viewport-local (data) coordinates — for positioning overlays.
  - `imagePos`: full-image pixel coordinates — for geometry / pixel readout.
For a viewport showing the whole image these coincide; for a viewport showing an
inner region they differ (that is exactly what `localToImage` accounts for).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QPointF

if TYPE_CHECKING:
    from PyQt6.QtGui import QKeyEvent


class PointerAction(Enum):
    """The kind of pointer interaction a `PointerEvent` represents."""

    PRESS = auto()
    MOVE = auto()
    RELEASE = auto()
    DOUBLE_CLICK = auto()  # the second press of a double-click
    # A press and release without a drag that no tool took the press of (so a
    # plain drag can still pan): delivered after the release
    CLICK = auto()


@dataclass(frozen=True)
class PointerEvent:
    """A pointer interaction, with coordinates already mapped into useful frames."""

    action: PointerAction
    localPos: QPointF  # viewport-local (data) coords
    imagePos: QPointF  # full-image pixel coords
    button: Qt.MouseButton
    modifiers: Qt.KeyboardModifier


@dataclass(frozen=True)
class KeyEvent:
    """A key press delivered to a tool."""

    key: int  # a Qt.Key value, as returned by QKeyEvent.key()
    modifiers: Qt.KeyboardModifier
    text: str = ""

    @classmethod
    def fromQt(cls, event: "QKeyEvent") -> "KeyEvent":
        return cls(key=event.key(), modifiers=event.modifiers(), text=event.text())
