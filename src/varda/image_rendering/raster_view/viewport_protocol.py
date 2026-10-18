"""Renderer-agnostic contract for a raster viewport.

`RasterViewport` is the seam between the rest of Varda and whatever graphics
backend draws the image. Today the only implementation is the pyqtgraph-based
`ImageViewport`; the point of pinning the contract down now is that controllers
and tools can depend on *this* Protocol instead of reaching into pyqtgraph
internals, so a future VisPy/pygfx viewport becomes "implement one Protocol"
rather than "rewrite every consumer".

Backend-neutral overlays go through the `add*Overlay` factories (`addCrosshair`,
`addPolygonOverlay`, `addTextOverlay`, `addROIOverlay`), which return abstract
handles — consumers use these instead of constructing pyqtgraph items. Tool input
arrives as backend-neutral `PointerEvent`/`KeyEvent`s (see `pointer_event`), so
tools never touch the scene graph.

Provisional surface that still traffics in raw pyqtgraph objects (typed loosely
as `object` to keep pyqtgraph out of the contract): `addItem`/`removeItem` and
`showRegion`. These now back only the *interactive* region-selection ROI
(`VardaROIItem`, a `pg.ROI` with drag/resize handles) used by `RegionController`;
abstracting interactive ROI editing is the remaining step.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np
from psygnal import SignalInstance
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget

if TYPE_CHECKING:
    from varda.common.entities import VardaRaster
    from varda.image_rendering import ImageRenderer
    from varda.image_rendering.raster_view.viewport_tools.viewport_tool import (
        ViewportTool,
    )


class OverlayHandle(Protocol):
    """A graphics overlay owned by the viewport, controlled without knowing the backend.

    Returned by the viewport's `add*Overlay` factories. Consumers hold the handle
    and manipulate the overlay through it instead of constructing pyqtgraph items
    themselves, so the overlay's backend stays an implementation detail.
    """

    def setVisible(self, visible: bool) -> None: ...

    def remove(self) -> None:
        """Detach the overlay from the viewport."""
        ...


class CrosshairHandle(OverlayHandle, Protocol):
    """A pair of full-extent cross lines, positioned in viewport-local coordinates."""

    def setPos(self, pos: QPointF) -> None: ...


class PolygonOverlayHandle(OverlayHandle, Protocol):
    """A filled polygon outline, positioned in viewport-local coordinates."""

    def setPoints(self, points: Sequence[QPointF]) -> None: ...


class TextOverlayHandle(OverlayHandle, Protocol):
    """A text label positioned in view (data) coordinates."""

    def setText(self, text: str) -> None: ...

    def setPos(self, pos: QPointF) -> None: ...


class PointOverlayHandle(OverlayHandle, Protocol):
    """A point marker of fixed screen size at a viewport-local position."""

    def setPos(self, pos: QPointF) -> None: ...

    def setColor(self, color: QColor) -> None: ...

    def setHighlighted(self, highlighted: bool) -> None: ...


class PolylineOverlayHandle(OverlayHandle, Protocol):
    """An open line (a drawn line or profile) in viewport-local coordinates,
    a constant width on screen, with a highlight state."""

    def setPoints(self, points: Sequence[QPointF]) -> None: ...

    def setColor(self, color: QColor) -> None: ...

    def setHighlighted(self, highlighted: bool) -> None: ...


class ROIOverlayHandle(OverlayHandle, Protocol):
    """A display-only ROI polygon (colored outline + fill) with a highlight state.

    Points are in viewport-local coordinates. Unlike `PolygonOverlayHandle`
    (a transient drawing preview), this carries a per-ROI color and can be
    highlighted, matching how saved ROIs are shown.
    """

    def setPoints(self, points: Sequence[QPointF]) -> None: ...

    def setColor(self, color: QColor) -> None: ...

    def setHighlighted(self, highlighted: bool) -> None: ...


@runtime_checkable
class RasterViewport(Protocol):
    """The renderer-agnostic surface of an image viewport.

    A concrete viewport is also a `QWidget`; that relationship is left implicit
    rather than expressed by inheriting `QWidget` here (a Protocol must not, and
    a sip-based QWidget metaclass would clash with `ABCMeta` anyway).
    """

    # --- signals (psygnal; payload shapes in comments) ---

    sigImageChanged: SignalInstance  # ()
    sigPanStarted: SignalInstance  # (QPointF) press position, view coords
    sigPanned: SignalInstance  # (QPointF, QPointF) (current, start), view coords
    sigZoomed: SignalInstance  # (float, QPointF) (scaleFactor, anchorFraction)
    # sigPanStarted/sigPanned/sigZoomed fire only while self-navigation is disabled;
    # sigViewRangeChangedManually fires only while it is enabled.
    sigViewRangeChangedManually: SignalInstance  # () this viewport self-navigated

    # --- refresh / self-updating ---

    def refresh(self) -> None:
        """Redraw the image (and any overlay image) with current settings."""
        ...

    def autoRefresh(self) -> None:
        """Refresh only if self-updating is enabled."""
        ...

    def enableSelfUpdating(self) -> None: ...

    def disableSelfUpdating(self) -> None: ...

    # --- overlay images (a second raster blended on top) ---

    def overlayImage(self, overlayImageRenderer: ImageRenderer) -> None: ...

    def removeOverlayImage(self) -> None: ...

    # --- navigation ---

    def enableSelfNavigation(self) -> None:
        """Let mouse gestures pan/zoom this viewport's own view range."""
        ...

    def disableSelfNavigation(self) -> None:
        """Detect gestures and emit them as signals without moving the view."""
        ...

    # --- view / range ---

    def mapToView(self, point: QPointF) -> QPointF: ...

    def viewRect(self) -> QRectF: ...

    def setViewRange(self, rect: QRectF, padding: float = 0) -> None: ...

    # --- coordinate conversion ---

    def localToImage(self, point: QPointF) -> QPointF:
        """Viewport-local coordinates -> full-image pixel coordinates."""
        ...

    def imageToLocal(self, point: QPointF) -> QPointF:
        """Full-image pixel coordinates -> viewport-local coordinates."""
        ...

    def imageBounds(self) -> QRectF:
        """Bounding rect of the displayed image, in viewport-local coords."""
        ...

    def pixelToLocalCoords(self, pixelCoords: np.ndarray) -> np.ndarray:
        """Vectorized full-image pixel coords -> viewport-local coords."""
        ...

    # --- region display ---

    def showRegion(self, roi: object) -> None:
        """Display only the given ROI's region of the full image."""
        ...

    def clearRegion(self) -> None: ...

    @property
    def isShowingRegion(self) -> bool: ...

    # --- overlay primitives (backend-neutral; return handles) ---

    def addCrosshair(self, color: QColor | None = None) -> CrosshairHandle:
        """Add a hidden crosshair overlay; returns a handle to drive it."""
        ...

    def addPolygonOverlay(
        self,
        lineColor: QColor | None = None,
        fillColor: QColor | None = None,
        lineWidth: float = 2.0,
    ) -> PolygonOverlayHandle:
        """Add an (initially empty) polygon overlay; returns a handle to drive it."""
        ...

    def addTextOverlay(
        self,
        text: str,
        viewPos: QPointF | None = None,
        color: str = "white",
        fontSize: int = 12,
        backgroundColor: str = "black",
        backgroundAlpha: int = 150,
        anchor: tuple[float, float] = (0.0, 0.0),
    ) -> TextOverlayHandle:
        """Add a text label at `viewPos` (image coordinates); without one, it
        is pinned to the view's top-left corner and wrapped to its width."""
        ...

    def addROIOverlay(
        self, points: Sequence[QPointF], color: QColor
    ) -> ROIOverlayHandle:
        """Add a display-only ROI polygon overlay; returns a handle to drive it."""
        ...

    def addPolylineOverlay(
        self, points: Sequence[QPointF], color: QColor, width: float = 2.0
    ) -> PolylineOverlayHandle:
        """Add an open line overlay; returns a handle to drive it."""
        ...

    def addPointOverlay(
        self, pos: QPointF, color: QColor, symbol: str = "x"
    ) -> PointOverlayHandle:
        """Add a point marker ("x" or "o") of fixed screen size at a
        viewport-local position."""
        ...

    # --- raw items / tools (provisional; see module docstring) ---

    def addItem(self, item: object, ignoreBounds: bool = True) -> None: ...

    def removeItem(self, item: object) -> None: ...

    def installTool(self, tool: ViewportTool) -> None: ...

    def removeTool(self, tool: ViewportTool) -> None: ...

    def addToolBar(self, toolbar: QWidget) -> None: ...

    # --- domain accessors (already backend-agnostic) ---

    @property
    def imageEntity(self) -> VardaRaster: ...
