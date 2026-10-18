"""Ratio Explorer: quick numerator/denominator box ratios by clicking."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, override

import attrs
import numpy as np
from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QColor

from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.image_rendering.raster_view.pointer_event import (
    KeyEvent,
    PointerAction,
    PointerEvent,
)
from varda.image_rendering.raster_view.viewport_tools.viewport_tool import ViewportTool
from varda.rois.region_statistics import boxPolygonPixels

if TYPE_CHECKING:
    from varda.image_rendering.raster_view.viewport_protocol import ROIOverlayHandle

NUMERATOR_COLOR = QColor(0, 220, 0)
DENOMINATOR_COLOR = QColor(255, 0, 255)

# (numerator polygon, clicked row, clicked col) -> denominator polygon
DenominatorPlacer = Callable[[np.ndarray, int, int], np.ndarray]
# (new numerator polygon, current denominator polygon) -> new denominator
# polygon, or None to keep the denominator where it is
DenominatorFollower = Callable[[np.ndarray, np.ndarray], np.ndarray | None]
# clicked view (x, y) -> data pixel (x, y), or None when outside the data
ClickMapper = Callable[[float, float], tuple[float, float] | None]
# box polygon in data pixels -> the same polygon in this view's pixels
BoxProjector = Callable[[np.ndarray], np.ndarray]


@attrs.frozen
class RatioSelection:
    """The current boxes as (4, 2) (col, row) pixel-corner polygons, or None."""

    numerator: np.ndarray | None
    denominator: np.ndarray | None


def boxSize(polygon: np.ndarray) -> tuple[int, int]:
    """(width, height) in pixels of a box polygon from ``boxPolygonPixels``."""
    return int(polygon[1, 0] - polygon[0, 0]), int(polygon[2, 1] - polygon[1, 1])


class RatioExplorerTool(ViewportTool):
    """Ctrl+left-click drops a numerator box, Ctrl+right-click a same-size
    denominator box; plain clicks still navigate (pan, zoom, context menu).

    The tool only reports the boxes; the workspace computes and plots the
    ratio. Box size comes from a provider so a workspace setting applies
    without re-creating the tool, and denominator placement can be delegated
    (e.g. to lock it to the numerator's sensor column).
    """

    toolName = "Ratio Explorer"
    toolDescription = (
        "Ctrl/Cmd+left-click: numerator box, Ctrl/Cmd+right-click: denominator "
        "box. S saves both as ROIs, Esc clears."
    )
    toolCategory = "Selection"

    sigSelectionChanged = pyqtSignal(object)  # RatioSelection
    sigSaveRequested = pyqtSignal()

    def __init__(self, viewport: ImageViewport, parent=None):
        super().__init__(viewport, parent)
        self._boxSize: Callable[[], tuple[int, int]] = lambda: (5, 5)
        self._placeDenominator: DenominatorPlacer | None = None
        self._followDenominator: DenominatorFollower | None = None
        self._mapClick: ClickMapper | None = None
        self._projectBox: BoxProjector | None = None
        self.selection = RatioSelection(None, None)
        self._numeratorOverlay: ROIOverlayHandle | None = None
        self._denominatorOverlay: ROIOverlayHandle | None = None
        self._shown = False  # boxes are drawn only while the tool is on
        # The boxes are kept in image pixels; a zoom panel's local coordinates
        # change with every pan and zoom, so they're placed again each time
        viewport.sigImageChanged.connect(self._redraw)

    def setBoxSizeProvider(self, provider: Callable[[], tuple[int, int]]) -> None:
        self._boxSize = provider

    def setDenominatorPlacer(self, placer: DenominatorPlacer | None) -> None:
        self._placeDenominator = placer

    def setDenominatorFollower(self, follower: DenominatorFollower | None) -> None:
        """Let the denominator move when a new numerator is placed."""
        self._followDenominator = follower

    def setClickMapper(self, mapper: ClickMapper | None) -> None:
        """Convert clicks on this view to the pixels the boxes are defined in
        (for a view on another grid than the data)."""
        self._mapClick = mapper

    def setBoxProjector(self, projector: BoxProjector | None) -> None:
        """Draw the boxes (always in data pixels) through ``projector``."""
        self._projectBox = projector
        self._redraw()

    def activate(self):
        super().activate()
        self._shown = True
        self.showText(self.toolDescription, timeout=4000)

    def deactivate(self):
        self._shown = False
        self._redraw()  # removes the boxes; the selection is kept
        super().deactivate()

    @override
    def onPointerEvent(self, event: PointerEvent) -> bool:
        if event.button not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            return False
        if not (event.modifiers & Qt.KeyboardModifier.ControlModifier):
            return False  # plain clicks navigate: pan, zoom, context menu
        if event.action == PointerAction.RELEASE:
            return True  # consumed: the press already acted
        if event.action != PointerAction.PRESS:
            return False

        x, y = event.imagePos.x(), event.imagePos.y()
        if self._mapClick is not None:
            mapped = self._mapClick(x, y)
            if mapped is None:
                self.showText("Outside the I/F cube", timeout=2000)
                return True
            x, y = mapped
        col, row = int(x), int(y)
        if event.button == Qt.MouseButton.LeftButton:
            width, height = self._boxSize()
            numerator = boxPolygonPixels(col, row, width, height)
            denominator = self.selection.denominator
            if denominator is not None and self._followDenominator is not None:
                moved = self._followDenominator(numerator, denominator)
                denominator = denominator if moved is None else moved
            self._setSelection(numerator, denominator)
            return True

        numerator = self.selection.numerator
        if numerator is None:
            self.showText("Left-click a numerator box first", timeout=2000)
            return True
        if self._placeDenominator is not None:
            denominator = self._placeDenominator(numerator, row, col)
        else:
            denominator = boxPolygonPixels(col, row, *boxSize(numerator))
        self._setSelection(numerator, denominator)
        return True

    @override
    def onKeyEvent(self, event: KeyEvent) -> bool:
        key = event.key.value if isinstance(event.key, Qt.Key) else event.key
        if key == Qt.Key.Key_S.value:
            self.sigSaveRequested.emit()
            return True
        if key == Qt.Key.Key_Escape.value:
            self._setSelection(None, None)
            return True
        return False

    def _setSelection(
        self, numerator: np.ndarray | None, denominator: np.ndarray | None
    ) -> None:
        self.selection = RatioSelection(numerator, denominator)
        self._redraw()
        self.sigSelectionChanged.emit(self.selection)

    def _redraw(self) -> None:
        """Place the boxes on their image pixels in the viewport's current
        coordinates (or remove them while the tool is off)."""
        numerator = self.selection.numerator if self._shown else None
        denominator = self.selection.denominator if self._shown else None
        self._numeratorOverlay = self._drawBox(
            self._numeratorOverlay, numerator, NUMERATOR_COLOR
        )
        self._denominatorOverlay = self._drawBox(
            self._denominatorOverlay, denominator, DENOMINATOR_COLOR
        )

    def _drawBox(
        self,
        handle: ROIOverlayHandle | None,
        polygon: np.ndarray | None,
        color: QColor,
    ) -> ROIOverlayHandle | None:
        if polygon is None:
            if handle is not None:
                handle.remove()
            return None
        if self._projectBox is not None:
            polygon = self._projectBox(polygon)
        points = [
            QPointF(float(c), float(r))
            for c, r in self.viewport.pixelToLocalCoords(polygon)
        ]
        if handle is None:
            return self.viewport.addROIOverlay(points, color)
        handle.setPoints(points)
        return handle
