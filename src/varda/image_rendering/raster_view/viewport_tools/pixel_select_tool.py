from __future__ import annotations

from collections.abc import Callable
from typing import override, TYPE_CHECKING

import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal, QPointF
from PyQt6.QtGui import QColor

from varda.common.keys import controlClick
from varda.image_rendering.raster_view.viewport_tools.viewport_tool import ViewportTool
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.image_rendering.raster_view.pointer_event import PointerAction, PointerEvent

if TYPE_CHECKING:
    from varda.image_rendering.raster_view.viewport_protocol import (
        CrosshairHandle,
        PolygonOverlayHandle,
    )


class PixelSelectTool(ViewportTool):
    """Click a pixel to select it (a plain drag still pans the view); Ctrl-drag
    (⌘-drag on a Mac) follows the pointer. Emits the pixel's image coords upon
    mouse release.

    The tool only reports the selection; the owning workspace decides what to
    do with it (e.g. plot the spectrum in its Pixel Spectra dock). When the
    workspace selects a window around the pixel rather than one pixel, it
    gives the tool a ``windowProvider`` (width, height), and the window is
    outlined while dragging.
    """

    sigPixelSelected = pyqtSignal(QPointF)
    sigPixelDragged = pyqtSignal(QPointF)  # each pixel passed while Ctrl-dragging

    # Tool metadata
    toolName = "Pixel Select"
    # Short: it is shown on the view, which can be small
    toolDescription = (
        f"Click a pixel to plot its spectrum; {controlClick('drag')} follows "
        "the pointer"
    )
    toolCategory = "Selection"

    def __init__(self, viewport: ImageViewport, parent=None):
        super().__init__(viewport, parent)
        self._crosshair: CrosshairHandle | None = None
        self._windowOutline: PolygonOverlayHandle | None = None
        self.windowProvider: Callable[[], tuple[int, int]] = lambda: (1, 1)
        self.isDragging = False
        self.activate()

    def setWindowProvider(self, provider: Callable[[], tuple[int, int]]) -> None:
        """Where the size of the window around the selected pixel comes from."""
        self.windowProvider = provider

    def activate(self):
        super().activate()
        if self._crosshair is None:
            self._crosshair = self.viewport.addCrosshair()
        self.showText(f"{self.toolName}: {self.toolDescription}", timeout=6000)

    def deactivate(self):
        super().deactivate()
        self.hideText()
        if self._crosshair is not None:
            self._crosshair.remove()
            self._crosshair = None
        self._removeWindowOutline()

    def _removeWindowOutline(self) -> None:
        if self._windowOutline is not None:
            self._windowOutline.remove()
            self._windowOutline = None

    @override
    def onPointerEvent(self, event: PointerEvent) -> bool:
        if event.action == PointerAction.CLICK:
            # A plain click (its press was left to the view, to pan with)
            if event.button != Qt.MouseButton.LeftButton:
                return False
            self._updateCrosshair(event)
            return True
        if event.action == PointerAction.PRESS:
            if (
                event.button == Qt.MouseButton.LeftButton
                and event.modifiers & Qt.KeyboardModifier.ControlModifier
            ):
                self.isDragging = True
                self._updateCrosshair(event, emitSignal=False)
                self._showCrosshairs()
                return True
            return False
        if event.action == PointerAction.MOVE:
            if self.isDragging:
                self._updateCrosshair(event, emitSignal=False)
                self.sigPixelDragged.emit(event.imagePos)
                return True
            return False
        if event.action == PointerAction.RELEASE:
            if self.isDragging and event.button == Qt.MouseButton.LeftButton:
                self.isDragging = False
                self._updateCrosshair(event)
                self._hideCrosshairs()
                return True
            return False
        return False

    def _showCrosshairs(self):
        """Show the crosshairs at the current mouse position."""
        if self._crosshair is not None:
            self._crosshair.setVisible(True)

    def _hideCrosshairs(self):
        """Hide the crosshairs (and the window outline)."""
        if self._crosshair is not None:
            self._crosshair.setVisible(False)
        self._removeWindowOutline()

    def _updateCrosshair(self, event: PointerEvent, emitSignal=True):
        """Position the crosshairs at the pointer and optionally emit the
        pixel. The hint goes once the tool is used: it covers the image."""
        self.hideText()
        local = event.localPos
        # Quantize to a pixel, then offset to the pixel's center for display.
        centeredPos = pg.Point(int(local.x()), int(local.y())) + pg.Point(0.5, 0.5)
        if self._crosshair is not None:
            self._crosshair.setPos(centeredPos)
        self._outlineWindow(int(local.x()), int(local.y()))
        if emitSignal:
            self.sigPixelSelected.emit(event.imagePos)

    def _outlineWindow(self, col: int, row: int) -> None:
        """Outline the window around pixel (col, row) when it's wider than a
        pixel (even sizes extend right and down, as the selection does)."""
        width, height = self.windowProvider()
        if width == height == 1 or not self.isDragging:
            self._removeWindowOutline()
            return
        if self._windowOutline is None:
            self._windowOutline = self.viewport.addPolygonOverlay(
                QColor("red"), QColor(255, 0, 0, 30)
            )
        left, top = col - (width - 1) // 2, row - (height - 1) // 2
        self._windowOutline.setPoints(
            [
                QPointF(left, top),
                QPointF(left + width, top),
                QPointF(left + width, top + height),
                QPointF(left, top + height),
                QPointF(left, top),
            ]
        )
