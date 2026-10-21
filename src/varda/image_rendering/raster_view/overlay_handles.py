"""pyqtgraph-backed implementations of the viewport overlay handles.

These wrap concrete pyqtgraph / Qt graphics items and satisfy the backend-neutral
handle Protocols in `viewport_protocol`. They are the *only* place these specific
items are constructed; consumers receive a handle and never see the item, so a
future VisPy/pygfx viewport can return its own handles from the same factories.

Overlays are added to the viewport's `ViewBox` with ``ignoreBounds=True`` so they
never affect auto-range — the view stays framed on the image.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QGraphicsPolygonItem


class PyqtgraphCrosshair:
    """A vertical + horizontal `InfiniteLine` pair driven as one crosshair."""

    def __init__(self, viewBox: pg.ViewBox, color: QColor):
        pen = pg.mkPen(color)
        self._vLine = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self._hLine = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        self._vLine.hide()
        self._hLine.hide()
        self._viewBox = viewBox
        viewBox.addItem(self._vLine, ignoreBounds=True)
        viewBox.addItem(self._hLine, ignoreBounds=True)

    def setPos(self, pos: QPointF) -> None:
        # InfiniteLine uses only the relevant axis of the point.
        self._vLine.setPos(pos)
        self._hLine.setPos(pos)

    def setVisible(self, visible: bool) -> None:
        self._vLine.setVisible(visible)
        self._hLine.setVisible(visible)

    def remove(self) -> None:
        self._viewBox.removeItem(self._vLine)
        self._viewBox.removeItem(self._hLine)


class PyqtgraphPolygonOverlay:
    """A filled `QGraphicsPolygonItem` whose points are in viewport-local coords."""

    def __init__(
        self,
        viewBox: pg.ViewBox,
        lineColor: QColor,
        fillColor: QColor,
        lineWidth: float,
    ):
        self._item = QGraphicsPolygonItem()
        self._item.setPen(QPen(lineColor, lineWidth))
        self._item.setBrush(QBrush(fillColor))
        self._viewBox = viewBox
        viewBox.addItem(self._item, ignoreBounds=True)

    def setPoints(self, points: Sequence[QPointF]) -> None:
        poly = QPolygonF()
        for p in points:
            poly.append(p)
        self._item.setPolygon(poly)

    def setVisible(self, visible: bool) -> None:
        self._item.setVisible(visible)

    def remove(self) -> None:
        self._viewBox.removeItem(self._item)


class PyqtgraphTextOverlay:
    """A `pg.TextItem` label positioned in view (data) coordinates, or, with
    no position, pinned to the view's top-left corner in screen pixels: it
    stays there whatever the view is panned, zoomed or resized to."""

    def __init__(
        self,
        viewBox: pg.ViewBox,
        text: str,
        pos: QPointF | None,
        color: str,
        fontSize: int,
        backgroundColor: str,
        backgroundAlpha: int,
        anchor: tuple[float, float],
    ):
        self._item = pg.TextItem(text=text, color=color, anchor=anchor)
        self._text = text
        self._fontSize = fontSize
        self._setPointSize(fontSize)
        if backgroundColor:
            self._item.fill = pg.mkBrush(color=backgroundColor, alpha=backgroundAlpha)
            self._item.border = pg.mkPen(color=backgroundColor, width=1)
        self._viewBox = viewBox
        self._pinned = pos is None
        self._wrapToView()
        if pos is None:
            # a child of the view box itself (not of its data group): its
            # position is in the view's pixels, in front of the image
            self._item.setParentItem(viewBox)
            self._item.setPos(QPointF(self._MARGIN / 2, self._MARGIN / 2))
            self._item.setZValue(1000)
        else:
            self._item.setPos(pos)
            viewBox.addItem(self._item, ignoreBounds=True)
        viewBox.sigResized.connect(self._wrapToView)

    _MARGIN = 8  # px kept free at the view's right edge
    _MIN_WRAP = 80  # px: narrower than this (a view not laid out yet), don't wrap
    _MIN_FIT = 24  # px: shorter than this (a view not laid out yet), don't fit
    _MIN_POINT_SIZE = 8  # a hint in a short view gets no smaller than this

    def _setPointSize(self, size: int) -> None:
        font = self._item.textItem.font()
        font.setPointSize(size)
        self._item.textItem.setFont(font)

    def _wrap(self) -> None:
        self._item.setTextWidth(-1)  # its natural width
        available = self._viewBox.width() - self._MARGIN
        natural = self._item.textItem.boundingRect().width()
        if natural > available >= self._MIN_WRAP:
            self._item.setTextWidth(available)

    def _tooTall(self) -> bool:
        available = self._viewBox.height() - self._MARGIN
        return available >= self._MIN_FIT and (
            self._item.textItem.boundingRect().height() > available
        )

    def _wrapToView(self, _viewBox: pg.ViewBox | None = None) -> None:
        """Wrap the text at word breaks when it is wider than the view, so a
        narrow view never cuts it off. A hint in the corner that is then
        taller than the view gets a smaller font, down to a legible size,
        and, if it still doesn't fit, its last words are left out (the full
        text is its tooltip)."""
        self._item.setText(self._text)
        self._item.setToolTip("")
        size = self._fontSize
        self._setPointSize(size)
        self._wrap()
        if not self._pinned:
            return
        while self._tooTall() and size > self._MIN_POINT_SIZE:
            size -= 1
            self._setPointSize(size)
            self._wrap()
        words = self._text.split()
        while self._tooTall() and len(words) > 1:
            words.pop()
            self._item.setText(" ".join(words) + "…")
            self._item.setToolTip(self._text)

    def setText(self, text: str) -> None:
        self._text = text
        self._wrapToView()

    def setPos(self, pos: QPointF) -> None:
        self._item.setPos(pos)

    def setVisible(self, visible: bool) -> None:
        self._item.setVisible(visible)

    def remove(self) -> None:
        self._viewBox.sigResized.disconnect(self._wrapToView)
        if self._pinned:
            scene = self._item.scene()
            if scene is not None:
                scene.removeItem(self._item)
            self._item.setParentItem(None)
        else:
            self._viewBox.removeItem(self._item)


class PyqtgraphPointOverlay:
    """A marker of fixed screen size at a viewport-local position (a one-point
    `ScatterPlotItem`, which keeps its pixel size across zooms)."""

    SIZE = 14
    _HIGHLIGHT = QColor(255, 255, 0)

    def __init__(
        self, viewBox: pg.ViewBox, pos: QPointF, color: QColor, symbol: str = "x"
    ):
        self._item = pg.ScatterPlotItem(
            [pos.x()], [pos.y()], symbol=symbol, size=self.SIZE, pxMode=True
        )
        self._viewBox = viewBox
        self._color = color
        self._highlighted = False
        self._updateStyle()
        viewBox.addItem(self._item, ignoreBounds=True)

    def setPos(self, pos: QPointF) -> None:
        self._item.setData([pos.x()], [pos.y()])

    def setColor(self, color: QColor) -> None:
        self._color = color
        self._updateStyle()

    def setHighlighted(self, highlighted: bool) -> None:
        self._highlighted = highlighted
        self._updateStyle()

    def _updateStyle(self) -> None:
        pen = (
            pg.mkPen(self._HIGHLIGHT, width=3)
            if self._highlighted
            else pg.mkPen(self._color, width=2)
        )
        self._item.setPen(pen)
        self._item.setBrush(pg.mkBrush(None))

    def setVisible(self, visible: bool) -> None:
        self._item.setVisible(visible)

    def remove(self) -> None:
        self._viewBox.removeItem(self._item)


class PyqtgraphROIOverlay(pg.GraphicsObject):
    """A display-only ROI polygon: colored outline + fill, with a highlight state.

    Points are in viewport-local coordinates. This is both the handle and the
    graphics item (a `pg.GraphicsObject`), so it can paint a closed, filled polygon
    while still satisfying the `ROIOverlayHandle` protocol.
    """

    _HIGHLIGHT = QColor(255, 255, 0)

    def __init__(
        self,
        viewBox: pg.ViewBox,
        points: Sequence[QPointF],
        color: QColor,
        clipTo: Callable[[], QRectF] | None = None,
    ):
        """``clipTo``: the area (viewport-local) the ROI may paint in, e.g. the
        displayed image, so a zoom panel shows no ROI parts outside its region."""
        super().__init__()
        self._clipTo = clipTo
        self._color = color
        self._highlighted = False
        self._polygon = QPolygonF()
        self._pen = QPen()
        self._brush = QBrush()
        self._buildPolygon(points)
        self._updateStyle()
        # NB: not `_viewBox` — pg.GraphicsItem uses that name internally.
        self._hostViewBox = viewBox
        viewBox.addItem(self, ignoreBounds=True)

    def setPoints(self, points: Sequence[QPointF]) -> None:
        self.prepareGeometryChange()
        self._buildPolygon(points)
        self.update()

    def setColor(self, color: QColor) -> None:
        self._color = color
        self._updateStyle()
        self.update()

    def setHighlighted(self, highlighted: bool) -> None:
        if self._highlighted != highlighted:
            self._highlighted = highlighted
            self._updateStyle()
            self.update()

    def remove(self) -> None:
        self._hostViewBox.removeItem(self)

    def boundingRect(self) -> QRectF:
        return self._polygon.boundingRect()

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addPolygon(self._polygon)
        return path

    def paint(self, painter, option, widget=None) -> None:
        if self._clipTo is not None:
            painter.setClipRect(self._clipTo(), Qt.ClipOperation.IntersectClip)
        painter.setPen(self._pen)
        painter.setBrush(self._brush)
        painter.drawPolygon(self._polygon)

    def _buildPolygon(self, points: Sequence[QPointF]) -> None:
        pts = list(points)
        polygon = QPolygonF()
        for point in pts:
            polygon.append(point)
        if len(pts) >= 3:
            polygon.append(pts[0])  # close the polygon
        self._polygon = polygon

    def _updateStyle(self) -> None:
        color = self._color
        if self._highlighted:
            color = QColor(
                self._HIGHLIGHT.red(),
                self._HIGHLIGHT.green(),
                self._HIGHLIGHT.blue(),
                color.alpha(),
            )
        self._pen = pg.mkPen(color=(color.red(), color.green(), color.blue()), width=2)
        self._brush = pg.mkBrush(color)


class PyqtgraphPolylineOverlay(pg.GraphicsObject):
    """An open line through points in viewport-local coordinates (a drawn
    line or profile), a constant width on screen, with a highlight state."""

    _HIGHLIGHT = QColor(255, 255, 0)

    def __init__(
        self,
        viewBox: pg.ViewBox,
        points: Sequence[QPointF],
        color: QColor,
        width: float = 2.0,
        clipTo: Callable[[], QRectF] | None = None,
    ):
        super().__init__()
        self._clipTo = clipTo
        self._color = color
        self._width = width
        self._highlighted = False
        self._path = QPainterPath()
        self.setPoints(points)
        self._hostViewBox = viewBox
        viewBox.addItem(self, ignoreBounds=True)

    def setPoints(self, points: Sequence[QPointF]) -> None:
        self.prepareGeometryChange()
        path = QPainterPath()
        for index, point in enumerate(points):
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        self._path = path
        self.update()

    def setColor(self, color: QColor) -> None:
        self._color = color
        self.update()

    def setHighlighted(self, highlighted: bool) -> None:
        if self._highlighted != highlighted:
            self._highlighted = highlighted
            self.update()

    def remove(self) -> None:
        self._hostViewBox.removeItem(self)

    def boundingRect(self) -> QRectF:
        # pad by the pen, in view units, so wide lines aren't clipped
        rect = self._path.boundingRect()
        pixel = self.pixelLength(None) or 0.0
        pad = (self._width + 2) * pixel
        return rect.adjusted(-pad, -pad, pad, pad)

    def paint(self, painter, option, widget=None) -> None:
        if self._clipTo is not None:
            painter.setClipRect(self._clipTo(), Qt.ClipOperation.IntersectClip)
        color = self._HIGHLIGHT if self._highlighted else self._color
        pen = pg.mkPen(color, width=self._width * (1.75 if self._highlighted else 1))
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path)
