from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from psygnal import Signal
from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFontDatabase
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
import pyqtgraph as pg
import numpy as np

from varda import log
from varda.common.entities import VardaRaster
from varda.image_rendering import ImageRenderer
from varda.image_rendering.image_renderer import RenderMode
from varda.image_rendering.raster_view.navigable_view_box import NavigableViewBox
from varda.image_rendering.raster_view.image_region_item import (
    VardaImageItem,
)
from varda.image_rendering.raster_view.overlay_handles import (
    PyqtgraphCrosshair,
    PyqtgraphPointOverlay,
    PyqtgraphPolygonOverlay,
    PyqtgraphPolylineOverlay,
    PyqtgraphROIOverlay,
    PyqtgraphTextOverlay,
)
from varda.image_rendering.raster_view.pointer_event import (
    PointerAction,
    PointerEvent,
)

if TYPE_CHECKING:
    # Imported under TYPE_CHECKING only: importing the tool module at runtime would
    # cycle back through this one (tools reference ImageViewport).
    from varda.image_rendering.raster_view.viewport_tools.viewport_tool import (
        ViewportTool,
    )
    from varda.image_rendering.raster_view.viewport_protocol import (
        RasterViewport,
        CrosshairHandle,
        PolygonOverlayHandle,
        PolylineOverlayHandle,
        PointOverlayHandle,
        TextOverlayHandle,
        ROIOverlayHandle,
    )


# Maps the pyqtgraph scene mouse-event types this viewport bridges into the
# backend-neutral PointerAction delivered to tools. Double-click is intentionally
# absent — the previous tool event filter never dispatched it either.
_POINTER_ACTIONS = {
    QEvent.Type.GraphicsSceneMousePress: PointerAction.PRESS,
    QEvent.Type.GraphicsSceneMouseMove: PointerAction.MOVE,
    QEvent.Type.GraphicsSceneMouseRelease: PointerAction.RELEASE,
    QEvent.Type.GraphicsSceneMouseDoubleClick: PointerAction.DOUBLE_CLICK,
}


IMAGE_Z = -2
OVERLAY_IMAGE_Z = -1


class _PixelReadoutOverlay(QLabel):
    """A floating HUD pinned to the bottom-left of its parent viewport.

    Shows the hovered pixel's coordinate, geospatial position, and band values.
    Parented to the viewport widget (not the pyqtgraph scene) so it stays fixed
    while the view pans/zooms. Transparent to mouse events so it never blocks
    navigation when the cursor passes over the corner.
    """

    _MARGIN = 8

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setStyleSheet(
            "background-color: rgba(0, 0, 0, 160); color: #eeeeee;"
            "padding: 4px 6px; border-radius: 4px;"
        )
        self.hide()

    def showReadout(self, text: str) -> None:
        self.setText(text)
        self.reposition()
        self.show()
        self.raise_()

    def hideReadout(self) -> None:
        self.hide()

    def reposition(self) -> None:
        """Re-pin to the bottom-left corner of the parent (call after text/size changes)."""
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        self.move(self._MARGIN, parent.height() - self.height() - self._MARGIN)


class _LegendOverlay(QLabel):
    """A key (rich text) pinned to the top-right of the image area, e.g. the
    classes of an overlaid classification. Like the readout it ignores the
    mouse, so it never blocks navigation, unless it has links to click."""

    _MARGIN = 8

    def __init__(self, parent: QWidget, area: QWidget):
        super().__init__(parent)
        self._area = area
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet(
            "background-color: rgba(0, 0, 0, 160); color: #eeeeee;"
            "padding: 4px 8px; border-radius: 4px;"
        )
        self.hide()

    def reposition(self) -> None:
        self.adjustSize()
        area = self._area.geometry()
        self.move(area.right() - self.width() - self._MARGIN, area.top() + self._MARGIN)


class ImageViewport(QWidget):
    """
    Generic image viewer: holds a single Viewbox with an ImageRegionItem, and helper methods.

    Navigation lives in the ViewBox (`NavigableViewBox`). By default the viewport navigates
    itself (native pan/zoom). With `disableSelfNavigation()` it instead re-emits drag/scroll
    as high-level gestures (`sigPanned`, `sigZoomed`) for an external controller to handle —
    used when something else (e.g. RegionController) drives what the viewport shows.
    """

    sigImageChanged = Signal()

    # Navigation gestures, forwarded from the ViewBox. Emitted only while self-navigation
    # is disabled. Positions are in view (data) coordinates.
    sigPanStarted = Signal(QPointF)  # press position
    sigPanned = Signal(QPointF, QPointF)  # (current position, start position)
    sigZoomed = Signal(float, QPointF)  # (scaleFactor, anchorFraction)
    # Emitted when a user gesture pans/zooms this viewport's own view (self-navigation on).
    # Mirrors pyqtgraph's viewBox.sigRangeChangedManually but without its mask argument.
    sigViewRangeChangedManually = Signal()
    # Emitted on a right-click that no active tool consumed, so a consumer can show a
    # context menu. The viewport stays generic — it just reports where (in image pixels)
    # and the global cursor position. (imageCol, imageRow, QPoint global).
    sigContextMenuRequested = pyqtSignal(float, float, object)
    # A link in the legend was clicked (its href), see setLegend
    sigLegendLink = pyqtSignal(str)

    def __init__(self, imageRenderer: ImageRenderer, parent=None):
        super().__init__(parent)
        self.selfUpdating = True

        self._imageRenderer = imageRenderer
        self._imageItem: VardaImageItem = VardaImageItem(self._imageRenderer)

        self._overlayImageRenderer: ImageRenderer | None = None
        self._overlayImageItem: VardaImageItem | None = None

        self._vb = NavigableViewBox(lockAspect=True, invertY=True)
        # Images sit under everything drawn on them (ROIs, markers, previews),
        # with an overlay image between the base image and those
        self._imageItem.setZValue(IMAGE_Z)
        self._vb.addItem(self._imageItem)
        self._gv = pg.GraphicsView()
        self._gv.setCentralItem(self._vb)
        layout = QVBoxLayout(self)
        layout.addWidget(self._gv)
        self.setLayout(layout)

        # Forward navigation signals so consumers depend on the viewport, not the ViewBox.
        self._vb.sigPanStarted.connect(self.sigPanStarted.emit)
        self._vb.sigPanned.connect(self.sigPanned.emit)
        self._vb.sigZoomed.connect(self.sigZoomed.emit)
        self._vb.sigRangeChangedManually.connect(self._onViewBoxRangeChangedManually)

        self._imageItem.sigImageChanged.connect(self.sigImageChanged.emit)
        self._imageRenderer.sigShouldRefresh.connect(self.autoRefresh)

        # Tools installed on this viewport. The viewport filters the imageItem's
        # scene events and delivers translated PointerEvents to each tool.
        self._tools: list[ViewportTool] = []
        self._imageItem.installEventFilter(self)

        # Bottom-left HUD showing the hovered pixel's coordinate / geo position / values.
        # Hover-moves arrive as the scene's sigMouseMoved (in scene coords, no button
        # needed); _onHover updates the readout, or hides it when the cursor is off the
        # image. The scene has no "mouse left" signal and stops emitting once the cursor
        # exits the view, so the final hide comes from the widget-level Leave event. A
        # QGraphicsView routes enter/leave to its viewport() child, so we filter that.
        self._readoutOverlay = _PixelReadoutOverlay(self)
        self._legendOverlay = _LegendOverlay(self, self._gv)
        self._legendOverlay.linkActivated.connect(self.sigLegendLink)
        self._gv.scene().sigMouseMoved.connect(self._onHover)
        self._gv.viewport().installEventFilter(self)

    def overlayImage(self, overlayImageRenderer: ImageRenderer):
        """Overlay an image on top of the current image.
        It's possible that we may want to support multiple overlay images in the future, or overlay with different blending modes,
        but for now we'll just support one.
        """
        self._overlayImageRenderer = overlayImageRenderer
        if self._overlayImageItem is not None:
            log.info("An image is already overlayed. Replacing it with the new one.")
            self.removeOverlayImage()
        self._overlayImageItem = VardaImageItem(overlayImageRenderer)
        if self._imageItem.isShowingRegion:
            self._overlayImageItem.setROI(self._imageItem.roi)
        self._overlayImageItem.setZValue(OVERLAY_IMAGE_Z)
        self._vb.addItem(self._overlayImageItem)

        overlayImageRenderer.sigShouldRefresh.connect(self.autoRefresh)

    def hasOverlayImage(self) -> bool:
        return self._overlayImageItem is not None

    def removeOverlayImage(self):
        """Remove the overlay image."""
        if self._overlayImageItem is not None:
            self._vb.removeItem(self._overlayImageItem)
            self._overlayImageItem = None
            self._overlayImageRenderer.sigShouldRefresh.disconnect(self.autoRefresh)
            self._overlayImageRenderer = None
        else:
            log.warning("No overlay image to remove.")

    def disableSelfUpdating(self):
        """Disable self-updating of the image item."""
        self.selfUpdating = False

    def enableSelfUpdating(self):
        """Enable self-updating of the image item."""
        self.selfUpdating = True

    def autoRefresh(self):
        if self.selfUpdating:
            self.refresh()

    def refresh(self):
        """Refresh the image display with current settings."""
        self._imageItem.refresh()
        if self._overlayImageItem is not None:
            self._overlayImageItem.refresh()

    # --- Navigation ---

    def enableSelfNavigation(self):
        """Let mouse gestures pan/zoom this viewport's own view range (the default)."""
        self._vb.setSelfNavigating(True)

    def disableSelfNavigation(self):
        """Stop this viewport from panning/zooming itself in response to gestures.

        Gestures are still detected and emitted as signals; the viewport just doesn't
        move its own view. Used when an external controller (e.g. RegionController)
        drives what the viewport shows.
        """
        self._vb.setSelfNavigating(False)

    def _onViewBoxRangeChangedManually(self, *args):
        self.sigViewRangeChangedManually.emit()

    # --- View / range helpers ---

    def mapToView(self, point: QPointF) -> QPointF:
        """Map a point from ViewBox-local coordinates to view (data) coordinates."""
        return self._vb.mapToView(point)

    def viewRect(self) -> QRectF:
        """The currently displayed range, in view (data) coordinates."""
        return self._vb.viewRect()

    def setViewRange(self, rect: QRectF, padding: float = 0):
        """Set the displayed range, in view (data) coordinates."""
        self._vb.setRange(rect=rect, padding=padding)

    # --- Coordinate conversion ---

    def localToImage(self, point):
        """Convert viewport-local coordinates to full-image pixel coordinates."""
        return self._imageItem.localToImage(point)

    def imageToLocal(self, point):
        """Convert full-image pixel coordinates to viewport-local coordinates."""
        return self._imageItem.imageToLocal(point)

    def imageBounds(self) -> QRectF:
        """The bounding rectangle of the displayed image, in viewport-local coordinates."""
        return self._imageItem.boundingRect()

    def pixelToLocalCoords(self, pixelCoords: np.ndarray) -> np.ndarray:
        """
        Convert full-image pixel coordinates to the viewport's local coordinates
        (since a viewport may be showing only an inner region of the image).
        """
        if not self._imageItem.isShowingRegion:
            return pixelCoords
        pointsList = [(float(c), float(r)) for c, r in pixelCoords]
        return np.array(self._imageItem.imageToLocal(pointsList))

    # --- Region display ---

    def showRegion(self, roi):
        """Display only the given ROI's region of the full image (and of any overlay)."""
        self._imageItem.setROI(roi)
        if self._overlayImageItem is not None:
            self._overlayImageItem.setROI(roi)

    def clearRegion(self):
        """Show the full image instead of a region."""
        self._imageItem.clearROI()
        if self._overlayImageItem is not None:
            self._overlayImageItem.clearROI()

    @property
    def isShowingRegion(self) -> bool:
        """Whether the viewport is showing a subregion rather than the full image."""
        return self._imageItem.isShowingRegion

    # --- Overlay primitives ---

    def addCrosshair(self, color: QColor | None = None) -> "CrosshairHandle":
        """Add a hidden crosshair overlay; returns a handle to drive it."""
        return PyqtgraphCrosshair(self._vb, color or QColor("red"))

    def addPolygonOverlay(
        self,
        lineColor: QColor | None = None,
        fillColor: QColor | None = None,
        lineWidth: float = 2.0,
    ) -> "PolygonOverlayHandle":
        """Add an (initially empty) polygon overlay; returns a handle to drive it."""
        return PyqtgraphPolygonOverlay(
            self._vb,
            lineColor or QColor(255, 0, 0),
            fillColor or QColor(255, 0, 0, 100),
            lineWidth,
        )

    def addTextOverlay(
        self,
        text: str,
        viewPos: QPointF | None = None,
        color: str = "white",
        fontSize: int = 12,
        backgroundColor: str = "black",
        backgroundAlpha: int = 150,
        anchor: tuple[float, float] = (0.0, 0.0),
    ) -> "TextOverlayHandle":
        """Add a text label at `viewPos` (image coordinates); without one, it
        is pinned to the view's top-left corner and wrapped to its width."""
        return PyqtgraphTextOverlay(
            self._vb,
            text,
            viewPos,
            color,
            fontSize,
            backgroundColor,
            backgroundAlpha,
            anchor,
        )

    def addROIOverlay(
        self, points: Sequence[QPointF], color: QColor
    ) -> "ROIOverlayHandle":
        """Add a display-only ROI polygon overlay; returns a handle to drive it.
        It is clipped to the displayed image (a zoom panel's region)."""
        return PyqtgraphROIOverlay(self._vb, points, color, clipTo=self.imageBounds)

    def addPolylineOverlay(
        self, points: Sequence[QPointF], color: QColor, width: float = 2.0
    ) -> "PolylineOverlayHandle":
        """Add an open line overlay (a drawn line); returns a handle to drive
        it. It is clipped to the displayed image (a zoom panel's region)."""
        return PyqtgraphPolylineOverlay(
            self._vb, points, color, width, clipTo=self.imageBounds
        )

    def addPointOverlay(
        self, pos: QPointF, color: QColor, symbol: str = "x"
    ) -> "PointOverlayHandle":
        """Add a marker ("x" or "o") of fixed screen size at a viewport-local position."""
        return PyqtgraphPointOverlay(self._vb, pos, color, symbol)

    # --- Items / tools ---

    def addItem(self, item, ignoreBounds: bool = True):
        """Add a graphics item to the viewport.

        By default the item does not contribute to the viewbox's auto-range bounds,
        so the viewport stays centered on the image even when ROIs or other overlays
        extend outside the image extent.
        """
        self._vb.addItem(item, ignoreBounds=ignoreBounds)

    def removeItem(self, item):
        """Remove a graphics item from the viewport."""
        self._vb.removeItem(item)

    def installTool(self, tool: ViewportTool):
        """Register a tool to receive translated pointer events from this viewport."""
        if tool not in self._tools:
            self._tools.append(tool)

    def removeTool(self, tool: ViewportTool):
        """Stop a tool from receiving pointer events from this viewport."""
        if tool in self._tools:
            self._tools.remove(tool)

    def eventFilter(self, a0, a1):
        """Filter events for two installed sources.

        On the imageItem: translate scene mouse events into PointerEvents. Mapping
        scene -> local -> image pixel coordinates happens here, once, so tools never
        touch the pyqtgraph scene graph. Each installed tool gets the event until one
        reports it handled (returns True).

        On the view's viewport widget: hide the hover readout on Leave (see __init__).
        """
        obj, event = a0, a1
        # The viewport reports Leave when the cursor exits the plot entirely — the one
        # hide case sigMouseMoved/_onHover can't see. Observed, not consumed.
        if obj is self._gv.viewport() and event.type() == QEvent.Type.Leave:
            self._readoutOverlay.hideReadout()
        # Note: not gated on self._tools — a right-click should still raise the
        # context menu on a viewport with no active tool (the tool loop below is
        # simply a no-op when empty).
        if obj is self._imageItem:
            action = _POINTER_ACTIONS.get(event.type())
            if action is not None:
                pointerEvent = self._buildPointerEvent(action, event)
                consumed = False
                for tool in list(self._tools):
                    if tool.onPointerEvent(pointerEvent):
                        consumed = True
                        event.accept()
                        break
                # A right-click that no tool consumed (e.g. it didn't cancel an active
                # drawing) requests a context menu. Tools that consume the right-click
                # — like drawing cancel — short-circuit above and never reach here.
                if (
                    not consumed
                    and pointerEvent.action == PointerAction.PRESS
                    and pointerEvent.button == Qt.MouseButton.RightButton
                ):
                    self.sigContextMenuRequested.emit(
                        float(pointerEvent.imagePos.x()),
                        float(pointerEvent.imagePos.y()),
                        QCursor.pos(),
                    )
                    event.accept()
                    return True
                if consumed:
                    return True
        return super().eventFilter(obj, event)

    def _buildPointerEvent(self, action: PointerAction, event) -> PointerEvent:
        localPos = self._imageItem.mapFromScene(event.scenePos())
        imagePos = self._imageItem.localToImage(localPos)
        return PointerEvent(
            action=action,
            localPos=localPos,
            imagePos=imagePos,
            button=event.button(),
            modifiers=event.modifiers(),
        )

    # --- Hover readout ---

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        self._readoutOverlay.reposition()
        self._legendOverlay.reposition()

    def _onHover(self, scenePos: QPointF) -> None:
        """Update the bottom-left readout for the pixel under the cursor."""
        local = self._imageItem.mapFromScene(scenePos)
        imagePos = self._imageItem.localToImage(local)
        col, row = int(imagePos.x()), int(imagePos.y())
        image = self._imageRenderer.image
        if col < 0 or row < 0 or col >= image.width or row >= image.height:
            self._readoutOverlay.hideReadout()
            return
        self._readoutOverlay.showReadout(self._buildReadoutText(col, row))

    def _buildReadoutText(self, col: int, row: int) -> str:
        image = self._imageRenderer.image
        lines = [f"px {col}, {row}"]
        if image.hasGeospatialData:
            crs = image.crs
            if crs is not None and not crs.is_geographic:
                x, y = image.pixelToGeo(col, row)
                lines.append(f"xy  {x:.2f}, {y:.2f}")
            latLon = image.pixelToLatLon(col, row)
            if latLon is not None:
                lat, lon = latLon
                lines.append(f"lat/lon  {lat:.5f}°, {lon:.5f}°")
        lines.append(self._formatBandValues(image, col, row))
        return "\n".join(lines)

    def _displayedBands(self) -> list[tuple[str, int]]:
        """The (label, band index) pairs currently being rendered."""
        settings = self._imageRenderer.settings
        if settings.mode.get() == RenderMode.MONO:
            return [("Value", int(settings.mono.band.get()))]
        return [
            ("R", int(settings.rgb.red.get())),
            ("G", int(settings.rgb.green.get())),
            ("B", int(settings.rgb.blue.get())),
        ]

    def _formatBandValues(self, image: VardaRaster, col: int, row: int) -> str:
        nodata = image.nodata
        parts = []
        for label, band in self._displayedBands():
            value = image[row, col, band]
            if np.ma.is_masked(value) or (nodata is not None and value == nodata):
                shown = "nodata"
            elif np.issubdtype(np.asarray(value).dtype, np.integer):
                shown = str(int(value))
            else:
                shown = f"{float(value):.4g}"
            parts.append(f"{label} {shown}")
        return "  ".join(parts)

    def setLegend(self, html: str, clickable: bool = False) -> None:
        """Show a key over the top-right of the image ("" hides it). With
        ``clickable``, its links (``<a href="...">``) emit sigLegendLink."""
        legend = self._legendOverlay
        legend.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, not clickable
        )
        legend.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            if clickable
            else Qt.TextInteractionFlag.NoTextInteraction
        )
        self._legendOverlay.setText(html)
        self._legendOverlay.setVisible(bool(html))
        if html:
            self._legendOverlay.reposition()
            self._legendOverlay.raise_()

    def legendText(self) -> str:
        return (
            self._legendOverlay.text() if self._legendOverlay.isVisibleTo(self) else ""
        )

    def addToolBar(self, toolbar):
        """Add a toolbar to the viewport."""
        self.layout().addWidget(toolbar)

    # --- Escape hatches ---

    @property
    def imageEntity(self) -> VardaRaster:
        return self._imageRenderer.image


if TYPE_CHECKING:

    def _assert_implements_protocol(viewport: ImageViewport) -> RasterViewport:
        """Static-only conformance check.

        `ty` flags this return if `ImageViewport` stops satisfying the
        `RasterViewport` contract (e.g. a method signature drifts). Never called
        at runtime; it exists purely so the type checker guards the seam.
        """
        return viewport
