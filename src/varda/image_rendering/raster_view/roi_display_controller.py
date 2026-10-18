"""ROI display controller — manages ROI overlays across viewports."""

from __future__ import annotations

import logging
from typing import Any, Callable, TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, QPointF

from varda.rois.roi_collection import ROICollection
from varda.image_rendering.raster_view.image_viewport import ImageViewport

if TYPE_CHECKING:
    from varda.image_rendering.raster_view.viewport_protocol import ROIOverlayHandle

logger = logging.getLogger(__name__)


def _toQPoints(coords: np.ndarray) -> list[QPointF]:
    """Convert an Nx2 array of (col, row) coordinates to a list of QPointF."""
    return [QPointF(float(col), float(row)) for col, row in coords]


class ROIDisplayController(QObject):
    """Display ROIs from an ROICollection on registered viewports.

    Listens to collection signals and keeps the visual overlays in sync.
    Handles coordinate conversion for viewports that display subregions. The
    overlays are backend-neutral `ROIOverlayHandle`s obtained from each viewport.
    """

    roiHighlighted = pyqtSignal(int)  # fid
    roiSelected = pyqtSignal(int)  # fid
    displayUpdated = pyqtSignal()

    def __init__(
        self, collection: ROICollection, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._collection = collection

        # {viewport_id: viewport_object}
        self._viewports: dict[str, ImageViewport] = {}
        # {viewport_id: {fid: ROIOverlayHandle}}
        self._items: dict[str, dict[int, ROIOverlayHandle]] = {}
        # {viewport_id: callback} for signal disconnection
        self._viewportCallbacks: dict[str, Callable] = {}

        self._highlightedFid: int | None = None

        # Connect collection signals
        self._collection.sigROIAdded.connect(self._onROIAdded)
        self._collection.sigROIsAdded.connect(self._onROIsAdded)
        self._collection.sigROIRemoved.connect(self._onROIRemoved)
        self._collection.sigROIUpdated.connect(self._onROIUpdated)
        self._collection.sigVisibilityChanged.connect(self._onVisibilityChanged)

    # --- Viewport management ---

    def registerViewport(self, viewportId: str, viewport: Any) -> None:
        self._viewports[viewportId] = viewport
        self._items[viewportId] = {}

        # Listen for region changes so ROI positions update when viewport pans
        def callback(vid=viewportId):
            self._refreshViewport(vid)

        self._viewportCallbacks[viewportId] = callback
        viewport.sigImageChanged.connect(callback)
        # Display any existing ROIs
        self._displayAllForViewport(viewportId)

    def unregisterViewport(self, viewportId: str) -> None:
        if viewportId not in self._viewports:
            return
        viewport = self._viewports[viewportId]
        viewport.sigImageChanged.disconnect(self._viewportCallbacks[viewportId])
        del self._viewportCallbacks[viewportId]
        for handle in self._items[viewportId].values():
            handle.remove()
        del self._items[viewportId]
        del self._viewports[viewportId]

    # --- Highlight ---

    def highlightROI(self, fid: int | None) -> None:
        if self._highlightedFid == fid:
            return
        self._highlightedFid = fid
        for viewport_items in self._items.values():
            for item_fid, handle in viewport_items.items():
                handle.setHighlighted(item_fid == fid)
        if fid is not None:
            self.roiHighlighted.emit(fid)

    # --- Signal handlers ---

    def _onROIAdded(self, fid: int) -> None:
        for vid in self._viewports:
            self._addOverlay(vid, fid)
        self.displayUpdated.emit()

    def _onROIsAdded(self, fids: list[int]) -> None:
        for vid in self._viewports:
            for fid in fids:
                self._addOverlay(vid, fid)
        self.displayUpdated.emit()

    def _onROIRemoved(self, fid: int) -> None:
        for vid in self._viewports:
            handle = self._items[vid].pop(fid, None)
            if handle is not None:
                handle.remove()
        if self._highlightedFid == fid:
            self._highlightedFid = None
        self.displayUpdated.emit()

    def _onROIUpdated(self, fid: int) -> None:
        roi = self._collection.getROI(fid)
        pixelCoords = self._collection.getPixelCoordinates(fid)
        for vid, viewport in self._viewports.items():
            handle = self._items[vid].get(fid)
            if handle is not None:
                handle.setPoints(_toQPoints(viewport.pixelToLocalCoords(pixelCoords)))
                handle.setColor(roi.color.toQColor())
        self.displayUpdated.emit()

    def _onVisibilityChanged(self, fid: int) -> None:
        visible = self._collection.isVisible(fid)
        for items in self._items.values():
            handle = items.get(fid)
            if handle is not None:
                handle.setVisible(visible)
        self.displayUpdated.emit()

    # --- Internal ---

    def _addOverlay(self, viewportId: str, fid: int) -> None:
        """Create (or replace) the overlay for `fid` on a single viewport."""
        viewport = self._viewports[viewportId]
        roi = self._collection.getROI(fid)
        pixelCoords = self._collection.getPixelCoordinates(fid)
        localCoords = viewport.pixelToLocalCoords(pixelCoords)
        handle = viewport.addROIOverlay(_toQPoints(localCoords), roi.color.toQColor())
        if fid == self._highlightedFid:
            handle.setHighlighted(True)
        handle.setVisible(self._collection.isVisible(fid))
        self._items[viewportId][fid] = handle

    def _refreshViewport(self, viewportId: str) -> None:
        """Recompute local coordinates for all ROI overlays on a viewport."""
        viewport = self._viewports[viewportId]
        for fid, handle in self._items[viewportId].items():
            pixelCoords = self._collection.getPixelCoordinates(fid)
            handle.setPoints(_toQPoints(viewport.pixelToLocalCoords(pixelCoords)))

    def _displayAllForViewport(self, viewportId: str) -> None:
        for fid in self._collection.fids:
            self._addOverlay(viewportId, fid)

    def cleanup(self) -> None:
        for vid, viewport in self._viewports.items():
            if vid in self._viewportCallbacks:
                viewport.sigImageChanged.disconnect(self._viewportCallbacks[vid])
            for handle in self._items[vid].values():
                handle.remove()
            self._items[vid].clear()
        self._viewports.clear()
        self._items.clear()
        self._viewportCallbacks.clear()
        self._highlightedFid = None
