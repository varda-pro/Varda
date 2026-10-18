"""The find-data map: footprints over a graticule, a basemap image (with
sharper tiles over it as you zoom), the names of features and vector
overlays, in equirectangular or polar stereographic (``projection``), with a
box to search in. Click selects the footprints under the pointer, Shift adds
to the selection, Cmd (Ctrl) removes from it, and Shift-drag adds every
footprint a box touches."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pyqtgraph as pg
import shapely
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QGraphicsRectItem, QLabel, QVBoxLayout, QWidget

from varda.catalogs.catalog import Product
from varda.catalogs.gazetteer import Gazetteer, selectLabels
from varda.common.ui import MutedLabel
from varda.workspaces.find_data.projection import (
    Bounds,
    MapProjection,
    Projector,
    graticule,
)

FOOTPRINT_PEN = pg.mkPen("#38c8ff", width=1)
SELECTED_PEN = pg.mkPen("#ffd200", width=2.5)
AREA_PEN = pg.mkPen("#ff4fd8", width=2, style=Qt.PenStyle.DashLine)
BAND_PEN = pg.mkPen("#ffd200", width=1, style=Qt.PenStyle.DashLine)
GRATICULE_PEN = pg.mkPen((255, 255, 255, 70), width=1)
OVERLAY_COLORS = ("#7CFC00", "#ff8c00", "#ff4040", "#c080ff", "#ffffff")
NAME_COLOR = (255, 248, 220)
NAME_FILL = (0, 0, 0, 120)

# What a click or a box does to the selection
REPLACE, ADD, REMOVE = "replace", "add", "remove"


def outlines(geometries: Sequence[shapely.Geometry]) -> tuple[np.ndarray, np.ndarray]:
    """Every polygon ring and line as one x, y pair of arrays, with NaN
    between pieces (so one curve item draws them all)."""
    xs, ys = [], []
    for geometry in geometries:
        for part in getattr(geometry, "geoms", [geometry]):
            rings = (
                [part.exterior, *part.interiors]
                if isinstance(part, shapely.Polygon)
                else [part]
                if isinstance(part, shapely.LineString)
                else []
            )
            for ring in rings:
                coords = np.asarray(ring.coords)
                if len(coords):
                    xs += [coords[:, 0], [np.nan]]
                    ys += [coords[:, 1], [np.nan]]
    if not xs:
        return np.zeros(0), np.zeros(0)
    return np.concatenate(xs), np.concatenate(ys)


class MapViewBox(pg.ViewBox):
    """Shift-drag draws a box (sigBoxed, in map coordinates) instead of
    panning."""

    sigBoxed = pyqtSignal(QRectF)

    def __init__(self):
        super().__init__()
        self.band = QGraphicsRectItem()
        self.band.setPen(BAND_PEN)
        self.band.setZValue(20)
        self.band.hide()
        self.addItem(self.band, ignoreBounds=True)

    def mouseDragEvent(self, ev, axis=None):
        if (
            ev.button() != Qt.MouseButton.LeftButton
            or not ev.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            super().mouseDragEvent(ev, axis)
            return
        ev.accept()
        rect = QRectF(
            self.mapSceneToView(ev.buttonDownScenePos()),
            self.mapSceneToView(ev.scenePos()),
        ).normalized()
        if ev.isFinish():
            self.band.hide()
            self.sigBoxed.emit(rect)
        else:
            self.band.setRect(rect)
            self.band.show()


class FootprintMap(QWidget):
    # product ids, and REPLACE, ADD or REMOVE
    sigSelect = pyqtSignal(list, str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.projector = Projector()
        self._products: list[Product] = []
        self._shapes: list[shapely.Geometry] = []  # the footprints, projected
        self._selectedProducts: list[Product] = []
        self._overlayLayers: list[list[shapely.Geometry]] = []
        self._overlays: list[pg.GraphicsObject] = []
        self._labels: list[pg.TextItem] = []
        self._gazetteer: Gazetteer | None = None
        self._namesShown = True
        self._namePositions: tuple[np.ndarray, np.ndarray] | None = None
        self._nameItems: list[pg.TextItem] = []  # reused as the view changes
        self.viewBox = MapViewBox()
        self.viewBox.sigBoxed.connect(self._onBox)
        self.plot = pg.PlotWidget(viewBox=self.viewBox)
        self.plot.setAspectLocked(True)
        # rows are y, whatever pyqtgraph's global imageAxisOrder says
        self.basemapItem = pg.ImageItem(axisOrder="row-major")
        self.basemapItem.setZValue(-10)
        self.plot.addItem(self.basemapItem)
        # sharper tiles of the global imagery, over the bundled image
        self.tilesItem = pg.ImageItem(axisOrder="row-major")
        self.tilesItem.setZValue(-9)
        self.plot.addItem(self.tilesItem)
        self.graticuleItem = pg.PlotCurveItem(pen=GRATICULE_PEN, connect="finite")
        self.graticuleItem.setZValue(-5)
        self.plot.addItem(self.graticuleItem)
        self.footprints = pg.PlotCurveItem(pen=FOOTPRINT_PEN, connect="finite")
        self.selected = pg.PlotCurveItem(pen=SELECTED_PEN, connect="finite")
        self.selected.setZValue(5)
        self.plot.addItem(self.footprints)
        self.plot.addItem(self.selected)
        self.area = pg.RectROI(
            [0, 0], [1, 1], pen=AREA_PEN, movable=True, resizable=True
        )
        self.area.setVisible(False)
        self.plot.addItem(self.area)
        scene = self.plot.scene()
        assert isinstance(scene, pg.GraphicsScene)
        scene.sigMouseClicked.connect(self._onClick)
        scene.sigMouseMoved.connect(self._onMove)
        self.viewBox.sigRangeChanged.connect(lambda *_: self._drawGraticule())
        self._namesTimer = QTimer(self)
        self._namesTimer.setSingleShot(True)
        self._namesTimer.setInterval(30)
        self._namesTimer.timeout.connect(self._drawNames)
        self.viewBox.sigRangeChanged.connect(lambda *_: self._namesTimer.start())
        self.pointer = QLabel()
        self.pointer.setToolTip(
            "Click selects footprints; Shift-click adds, Cmd-click (Ctrl-click) "
            "removes; Shift-drag adds every footprint in a box."
        )
        # who made the imagery and the names shown
        self.credit = MutedLabel()
        self.credit.setWordWrap(True)
        font = self.credit.font()
        font.setPointSizeF(font.pointSizeF() * 0.85)
        self.credit.setFont(font)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot, 1)
        layout.addWidget(self.pointer)
        layout.addWidget(self.credit)
        self.credit.hide()
        self._showAxes()
        self.showWhole()

    # --- projection and view -------------------------------------------------------------

    def setProjection(self, projection: MapProjection, body: str) -> None:
        """Draw in ``projection`` on ``body``'s sphere, keeping the view and
        the search box where they were (the basemap must be drawn again)."""
        if (projection, body or "Earth") == (
            self.projector.projection,
            self.projector.body,
        ):
            return
        view, area = self.viewBounds(), self.areaBounds()
        self.projector = Projector(projection, body)
        self.basemapItem.clear()
        self.tilesItem.clear()
        self._namePositions = None
        self._showAxes()
        self.setFootprints(self._products)
        self.setSelected(self._selectedProducts)
        layers, self._overlayLayers = self._overlayLayers, []
        self.clearOverlays()
        for geometries in layers:
            self.addOverlay(geometries)
        self.setArea(area)
        self.zoomTo(view, padding=0)

    def _showAxes(self) -> None:
        polar = self.projector.polar
        for axis in ("bottom", "left"):
            self.plot.showAxis(axis, not polar)
        self.plot.showGrid(x=not polar, y=not polar, alpha=0.25)
        if not polar:
            self.plot.setLabel("bottom", "Longitude (°E)")
            self.plot.setLabel("left", "Latitude (°)")
        self._drawGraticule()

    def _drawGraticule(self) -> None:
        for label in self._labels:
            self.viewBox.removeItem(label)
        self._labels = []
        if not self.projector.polar:
            self.graticuleItem.setData([], [])
            return
        lines, labels = graticule(self.projector, self.mapView())
        self.graticuleItem.setData(*outlines(lines))
        for x, y, text in labels:
            label = pg.TextItem(text, color=(255, 255, 255, 150), anchor=(0, 1))
            label.setPos(x, y)
            label.setZValue(-4)
            self.viewBox.addItem(label, ignoreBounds=True)
            self._labels.append(label)

    def mapView(self) -> Bounds:
        """The view in map coordinates."""
        (x0, x1), (y0, y1) = self.viewBox.viewRange()
        return (x0, y0, x1, y1)

    def showWhole(self) -> None:
        self.zoomTo(self.projector.hemisphere, padding=0.02)

    def zoomTo(self, bounds: Bounds, padding: float = 0.15) -> None:
        """Show longitude-latitude bounds."""
        mapBounds = self.projector.mapBounds(bounds)
        if mapBounds is not None:
            self.zoomToMap(mapBounds, padding)

    def zoomToMap(self, bounds: Bounds, padding: float = 0.15) -> None:
        x0, y0, x1, y1 = bounds
        self.viewBox.setRange(xRange=(x0, x1), yRange=(y0, y1), padding=padding)

    def viewBounds(self) -> Bounds:
        """The view in longitude and latitude."""
        return self.projector.lonLatBounds(self.mapView())

    def _onMove(self, position: QPointF) -> None:
        point = self.viewBox.mapSceneToView(position)
        longitude, latitude = (
            float(v) for v in self.projector.inverse(point.x(), point.y())
        )
        self.pointer.setText(
            f"{abs(latitude):.3f}°{'N' if latitude >= 0 else 'S'}, {longitude:.3f}°E"
        )

    # --- the search box ---------------------------------------------------------------

    def showArea(self, visible: bool) -> None:
        if visible and not self.area.isVisible():
            x0, y0, x1, y1 = self.mapView()
            width, height = x1 - x0, y1 - y0
            self.area.setPos((x0 + width / 4, y0 + height / 4))
            self.area.setSize((width / 2, height / 2))
        self.area.setVisible(visible)

    def areaBounds(self) -> Bounds:
        """The search box in longitude and latitude."""
        x, y = self.area.pos()
        width, height = self.area.size()
        return self.projector.lonLatBounds(
            (float(x), float(y), float(x + width), float(y + height))
        )

    def setArea(self, bounds: Bounds) -> None:
        mapBounds = self.projector.mapBounds(bounds)
        if mapBounds is None:
            return
        x0, y0, x1, y1 = mapBounds
        self.area.setPos((x0, y0))
        self.area.setSize((x1 - x0, y1 - y0))

    # --- layers ------------------------------------------------------------------------

    def setFootprints(self, products: Sequence[Product]) -> None:
        self._products = list(products)
        self._shapes = [self.projector.geometry(p.footprint) for p in self._products]
        self.footprints.setData(*outlines(self._shapes))

    def setSelected(self, products: Sequence[Product]) -> None:
        self._selectedProducts = list(products)
        shapes = [self.projector.geometry(p.footprint) for p in self._selectedProducts]
        self.selected.setData(*outlines(shapes))

    def setBasemap(self, rgba: np.ndarray | None, bounds: Bounds | None = None) -> None:
        """An RGBA image (rows north to south) covering ``bounds`` in map
        coordinates."""
        if rgba is None or bounds is None:
            self.basemapItem.clear()
            return
        x0, y0, x1, y1 = bounds
        # rows run north to south; the plot's y runs up
        self.basemapItem.setImage(rgba[::-1], autoLevels=False)
        self.basemapItem.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))

    def setTiles(self, rgba: np.ndarray | None, bounds: Bounds | None = None) -> None:
        """Sharper tiles over the basemap: RGBA (rows north to south, clear
        where a tile is missing) covering longitude-latitude ``bounds``."""
        if rgba is None or bounds is None:
            self.tilesItem.clear()
            return
        x0, y0, x1, y1 = bounds
        self.tilesItem.setImage(rgba[::-1], autoLevels=False)
        self.tilesItem.setRect(QRectF(x0, y0, x1 - x0, y1 - y0))

    def setCredit(self, text: str) -> None:
        self.credit.setText(text)
        self.credit.setVisible(bool(text))

    # --- names ---------------------------------------------------------------------------

    def setNames(self, gazetteer: Gazetteer | None) -> None:
        """Label ``gazetteer``'s features (None for no names)."""
        self._gazetteer = gazetteer
        self._namePositions = None
        self._drawNames()

    def showNames(self, shown: bool) -> None:
        self._namesShown = shown
        self._drawNames()

    def shownNames(self) -> list[str]:
        """The names labeled now."""
        return [item.toPlainText() for item in self._nameItems if item.isVisible()]

    def _positions(self, gazetteer: Gazetteer) -> tuple[np.ndarray, np.ndarray]:
        """The features in map coordinates (NaN in a polar view's other
        hemisphere)."""
        if self._namePositions is None:
            x, y = self.projector.forward(gazetteer.longitude, gazetteer.latitude)
            if self.projector.polar:
                away = (gazetteer.latitude < 0) == self.projector.north
                x, y = np.where(away, np.nan, x), np.where(away, np.nan, y)
            self._namePositions = (x, y)
        return self._namePositions

    def _drawNames(self) -> None:
        gazetteer = self._gazetteer
        chosen: list[int] = []
        if gazetteer is not None and self._namesShown and len(gazetteer):
            view = self.mapView()
            widthPx = self.viewBox.width()
            if widthPx > 0 and view[2] > view[0]:
                kmPerUnit = 0.001 if self.projector.polar else gazetteer.kmPerDegree
                x, y = self._positions(gazetteer)
                chosen = selectLabels(
                    gazetteer, x, y, view, widthPx / (view[2] - view[0]), kmPerUnit
                )
        while len(self._nameItems) < len(chosen):
            item = pg.TextItem(color=NAME_COLOR, fill=NAME_FILL, anchor=(0.5, 0.5))
            item.setZValue(8)
            self.viewBox.addItem(item, ignoreBounds=True)
            self._nameItems.append(item)
        for item, index in zip(self._nameItems, chosen):
            assert gazetteer is not None
            x, y = self._positions(gazetteer)
            item.setText(gazetteer.names[index])
            item.setToolTip(
                f"{gazetteer.names[index]}: {gazetteer.kinds[index]}"
                + (
                    f", {gazetteer.diameter[index]:g} km"
                    if gazetteer.diameter[index] > 0
                    else ""
                )
            )
            item.setPos(float(x[index]), float(y[index]))
            item.setVisible(True)
        for item in self._nameItems[len(chosen) :]:
            item.setVisible(False)

    def addOverlay(self, geometries: Sequence[shapely.Geometry]) -> None:
        """Longitude-latitude geometries, drawn in the next color."""
        color = OVERLAY_COLORS[len(self._overlayLayers) % len(OVERLAY_COLORS)]
        self._overlayLayers.append(list(geometries))
        shapes = [self.projector.geometry(g) for g in geometries]
        shapes = [g for g in shapes if not g.is_empty]
        x, y = outlines(shapes)
        points = [
            g for g in shapes if isinstance(g, (shapely.Point, shapely.MultiPoint))
        ]
        item = pg.PlotCurveItem(x, y, pen=pg.mkPen(color, width=2), connect="finite")
        item.setZValue(3)
        self.plot.addItem(item)
        self._overlays.append(item)
        if points:
            coords = np.array([c for g in points for c in shapely.get_coordinates(g)])
            scatter = pg.ScatterPlotItem(
                coords[:, 0], coords[:, 1], size=5, brush=pg.mkBrush(color), pen=None
            )
            scatter.setZValue(3)
            self.plot.addItem(scatter)
            self._overlays.append(scatter)

    def clearOverlays(self) -> None:
        for item in self._overlays:
            self.plot.removeItem(item)
        self._overlays, self._overlayLayers = [], []

    # --- selecting ---------------------------------------------------------------------------

    def productsIn(self, shape: shapely.Geometry) -> list[str]:
        """The ids of the footprints that touch a shape in map coordinates."""
        return [
            p.id
            for p, s in zip(self._products, self._shapes)
            if not s.is_empty and s.intersects(shape)
        ]

    def _onClick(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or event.double():
            return
        point: QPointF = self.viewBox.mapSceneToView(event.scenePos())
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:  # Cmd on a Mac
            mode = REMOVE
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            mode = ADD
        else:
            mode = REPLACE
        hits = self.productsIn(shapely.Point(point.x(), point.y()))
        if hits or mode == REPLACE:
            self.sigSelect.emit(hits, mode)

    def _onBox(self, rect: QRectF) -> None:
        box = shapely.box(rect.left(), rect.top(), rect.right(), rect.bottom())
        hits = self.productsIn(box)
        if hits:
            self.sigSelect.emit(hits, ADD)
