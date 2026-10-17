import json
from enum import Enum
from pathlib import Path

import numpy as np
from PyQt6.QtCore import (
    Qt,
    QObject,
    pyqtSignal,
    QPoint,
    QPointF,
    QByteArray,
    QMimeData,
    QSize,
)
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QGraphicsItem,
    QLabel,
    QListWidget,
    QMessageBox,
    QSizePolicy,
    QToolButton,
    QWidget,
)
import pyqtgraph as pg

from varda.common.entities import VardaRaster, Color

from varda.common.ui import (
    VBoxBuilder,
    HBoxBuilder,
    SectionBox,
    ButtonBuilder,
    WrapperWidget,
    PanelScrollArea,
    ElidingComboBox,
    MutedLabel,
)
from varda.common.vec2 import Vec2
from varda.common.spectral_axis import SpectralUnit, fromNanometres, unitLabel
from varda.plotting.plot_export import EXPORT_FILTER, exportPlot
from varda.plotting.library_spectra import (
    DEFAULT_LIBRARY_PATH,
    listSpectra,
    loadSpectrum,
)
from varda.plotting.spectrum_matching import (
    matchToReference,
)
from varda.plotting.view_box import ModifierDragViewBox
from varda.common.parameter import (
    ParameterGroup,
    FloatParameter,
    Vec2Parameter,
    ColorParameter,
    BoolParameter,
    EnumParameter,
    IntParameter,
)
from varda.common.parameter_widgets import groupWidget

CURVE_MIME_TYPE = "application/x-varda-curve"


class CurveConfig(ParameterGroup):
    width = FloatParameter(
        "Curve Width",
        default=2.0,
        range=(0.1, 10.0),
        units="px",
        description="Width of the curve in pixels",
        step=0.5,
    )
    color = ColorParameter(
        "Curve Color",
        default="#ff0000",
        description="Color of the curve",
    )
    # Steps are sized for reflectance (0-1) data: a whole-unit step would move
    # or flatten a spectrum right out of view.
    offset = FloatParameter(
        "Y Offset",
        default=0.0,
        units="y",
        description="Vertical offset of the curve",
        step=0.01,
        decimals=4,
    )
    scale = FloatParameter(
        "Y Scale",
        default=1.0,
        range=(0.001, 1000.0),
        units="y",
        description="Vertical scale of the curve",
        step=0.1,
        decimals=3,
        showSlider=False,
    )


class Curve(QObject):
    sigClicked = pyqtSignal(object)  # emits self when clicked

    def __init__(
        self,
        plotDataItem: pg.PlotDataItem,
        config: CurveConfig,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.plotDataItem = plotDataItem
        self.plotDataItem.sigClicked.connect(lambda: self.sigClicked.emit(self))
        self.config = config
        # Extra graphics that belong to this curve (e.g. a +/- std-dev band):
        # they follow its Y offset/scale and are removed with it.
        self.bandItems: list[QGraphicsItem] = []
        # For a mean spectrum: per-sample standard deviation and pixel count,
        # in the data's units (before the Y scale/offset); exported with it.
        self.std: np.ndarray | None = None
        self.count: np.ndarray | None = None
        self._pointSize: int | None = None  # None: samples not marked

        self.config.sigParameterChanged.connect(self.onConfigChanged)

        self.onConfigChanged()

    def onConfigChanged(self):
        pen = pg.mkPen(
            color=self.config.color.value.toQColor(), width=self.config.width.value
        )
        self.plotDataItem.setPen(pen)
        self._applyPointStyle()
        # translate-then-scale composes to y -> y * scale + offset, matching
        # displayedData() and the view limits. (scale-then-translate would
        # give scale * (y + offset).)
        transform = (
            pg.QtGui.QTransform()
            .translate(0.0, self.config.offset.value)
            .scale(1.0, self.config.scale.value)
        )
        self.plotDataItem.setTransform(transform)
        for item in self.bandItems:
            item.setTransform(transform)

    def setDataPoints(self, size: int | None) -> None:
        """Mark each sample with a dot of ``size`` px, or none (``None``)."""
        self._pointSize = size
        self._applyPointStyle()

    def _applyPointStyle(self) -> None:
        if self._pointSize is None:
            self.plotDataItem.setSymbol(None)
            return
        self.plotDataItem.setSymbol("o")
        self.plotDataItem.setSymbolSize(self._pointSize)
        self.plotDataItem.setSymbolBrush(pg.mkBrush(self.config.color.value.toQColor()))
        self.plotDataItem.setSymbolPen(None)

    def data(self) -> tuple[np.ndarray, np.ndarray]:
        """The curve's own data, without its Y scale and offset."""
        x, y = self.plotDataItem.getData()
        if x is None or y is None:
            return np.array([]), np.array([])
        return np.asarray(x, dtype=float), np.asarray(y, dtype=float)

    def displayedData(self) -> tuple[np.ndarray, np.ndarray]:
        """The curve's data as shown, i.e. with its Y scale and offset applied."""
        x, y = self.data()
        return x, y * self.config.scale.value + self.config.offset.value

    def isTransformed(self) -> bool:
        """Whether the curve is shown with a Y scale or offset."""
        return self.config.scale.value != 1.0 or self.config.offset.value != 0.0

    def setClickable(self, clickable: bool):
        self.plotDataItem.setCurveClickable(clickable, width=20)

    def setHighlighted(self, highlighted: bool):
        if highlighted:
            self.plotDataItem.setShadowPen(pg.mkPen("#ffff0088", width=10))
        else:
            self.plotDataItem.setShadowPen(None)

    def serialize(self) -> dict:
        x, y = self.plotDataItem.getData()
        return {
            "x": x.tolist() if x is not None else [],
            "y": y.tolist() if y is not None else [],
            "name": self.plotDataItem.name() or "",
            "color": self.config.color.value.toHexString(),
            "width": self.config.width.value,
            "offset": self.config.offset.value,
            "scale": self.config.scale.value,
        }

    @classmethod
    def deserialize(cls, data: dict) -> "Curve":
        curve = cls.fromData(
            data["x"],
            data["y"],
            Color.fromHexString(data["color"]),
            name=data["name"] or None,
        )
        curve.config.width.set(data["width"])
        curve.config.offset.set(data["offset"])
        curve.config.scale.set(data["scale"])
        return curve

    @classmethod
    def fromData(cls, x, y, color: Color, **kwargs):
        plotItem = pg.PlotDataItem(x, y, **kwargs)
        defaultConfig = CurveConfig()
        curve = cls(plotItem, defaultConfig)

        # initialize parameters
        curve.config.color.set(color)

        return curve


class RangeMode(Enum):
    AUTO = 1
    FIT_Y_TO_X_RANGE = 2
    MANUAL = 3


class ViewConfig(ParameterGroup):
    rangeMode = EnumParameter(
        "Range",
        RangeMode,
        RangeMode.AUTO,
        "Auto fits both axes to all data. Fit Y To X Range keeps your X window "
        "and refits Y to the data inside it whenever spectra change (scrolling "
        "or dragging on the Y axis takes Y over: Manual). Manual uses the "
        "ranges below.",
    )
    showLegend = BoolParameter("Show Legend", True, "Show the curve legend.")
    showDataPoints = BoolParameter(
        "Show Data Points",
        False,
        "Mark each wavelength that has measured channel data; the line between "
        "marks is only interpolation.",
    )
    dataPointSize = IntParameter(
        "Data Point Size", 6, range=(2, 20), description="Marker size in pixels"
    )


class AppearanceConfig(ParameterGroup):
    backgroundColor = ColorParameter("Background Color", "#000000")


class MarkerConfig(ParameterGroup):
    showLabels = BoolParameter(
        "Show Labels", True, "Show each marker's wavelength beside its line"
    )
    verticalLabels = BoolParameter(
        "Vertical Labels",
        False,
        "Run the labels along the marker lines so close markers stay legible",
    )


# Where marker labels sit along the line (fraction of the view height), and how
# far each one steps down when it would overlap a neighbor.
MARKER_LABEL_TOP = 0.95
MARKER_LABEL_STEP = 0.07


class _MarkerLabel(pg.InfLineLabel):
    """A marker's wavelength label. Draggable along its line; once the user has
    placed it, the automatic collision avoidance leaves it alone."""

    def __init__(self, line: pg.InfiniteLine, **kwds) -> None:
        super().__init__(line, movable=True, **kwds)
        self.userPlaced = False

    def mouseDragEvent(self, ev) -> None:
        super().mouseDragEvent(ev)
        if ev.isAccepted():
            self.userPlaced = True


class RangeConfig(ParameterGroup):
    viewRangeX = Vec2Parameter(
        "X View Range",
        default=Vec2(0.0, 1.0),
        valueNames=("Min", "Max"),
        step=1.0,
        decimals=2,
    )
    viewRangeY = Vec2Parameter(
        "Y View Range",
        default=Vec2(0.0, 1.0),
        valueNames=("Min", "Max"),
        step=0.01,
        decimals=4,
    )


class _PlotGraphicsView(pg.GraphicsView):
    def __init__(self, parent: "VardaPlotWidget"):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._dragStartPos: QPoint | None = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            parent = self.parent()
            if isinstance(parent, VardaPlotWidget) and parent.selectedCurve is not None:
                self._dragStartPos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._dragStartPos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.pos() - self._dragStartPos).manhattanLength() >= 10
        ):
            self._dragStartPos = None
            parent = self.parent()
            if isinstance(parent, VardaPlotWidget) and parent.selectedCurve is not None:
                self._initiateDrag(parent.selectedCurve)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._dragStartPos = None
        super().mouseReleaseEvent(event)

    def _initiateDrag(self, curve: Curve) -> None:
        data = curve.serialize()
        data["source_id"] = id(self.parent())
        mimeData = QMimeData()
        mimeData.setData(CURVE_MIME_TYPE, QByteArray(json.dumps(data).encode("utf-8")))
        drag = QDrag(self)
        drag.setMimeData(mimeData)
        drag.exec(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(CURVE_MIME_TYPE):
            event.accept()
        # Do NOT call super() — pg.GraphicsView.dragEnterEvent calls ev.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(CURVE_MIME_TYPE):
            event.accept()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(CURVE_MIME_TYPE):
            parent = self.parent()
            if isinstance(parent, VardaPlotWidget):
                parent.onCurveDrop(event)


class VardaPlotWidget(QWidget):
    # The selected curve changed: the curve, or None when nothing is selected
    sigSelectedCurveChanged = pyqtSignal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        libraryPath: Path = DEFAULT_LIBRARY_PATH,
    ):
        super().__init__(parent)
        self.selectedCurve: Curve | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # for Backspace/Delete
        self.libraryPath = libraryPath

        self.plots: list[Curve] = []
        # The spectral unit of the x axis, from the images plotted (None: band
        # numbers or not known yet). Reference spectra (nm) are converted to it.
        self.spectralUnit: SpectralUnit | None = None
        self.gv = _PlotGraphicsView(self)
        # if the user clicks on the plot area and none of the plots catch the click (therefore selecting it), deselect any selected plot
        self.gv.scene().sigMouseClicked.connect(self.onSceneClicked)
        self.viewBox = ModifierDragViewBox()
        self.plotItem = pg.PlotItem(viewBox=self.viewBox)
        self.legend = self.plotItem.addLegend()
        # Left-drag pans, Shift/Cmd+left-drag zooms to the dragged box,
        # right-drag stretches the axes, wheel zooms about the cursor (over an
        # axis, that axis only: over Y it stretches the spectra).
        self.viewBox.setMouseMode(pg.ViewBox.PanMode)
        self.viewBox.setMouseEnabled(x=True, y=True)
        # pyqtgraph's default (-1/8) zooms ~26% per wheel notch, which feels
        # jumpy on a trackpad; this is roughly a third of that.
        self.viewBox.state["wheelScaleFactor"] = -1.0 / 24.0
        self.gv.setCentralItem(self.plotItem)

        # Whether manual range params have been seeded with a starting value.
        # Auto-range stays auto until the user opts into manual; the first
        # opt-in seeds the manual params from the current view, subsequent
        # toggles preserve whatever the user last set.
        self._manualRangeInitialized = False

        self.viewConfig = ViewConfig()
        self.viewConfig.sigParameterChanged.connect(self.onViewParamsChanged)

        self.rangeConfig = RangeConfig()
        self.rangeConfig.sigParameterChanged.connect(self.onRangeParamsChanged)

        self.markerConfig = MarkerConfig()
        self.markerConfig.sigParameterChanged.connect(
            lambda _: self._applyMarkerLabelStyle()
        )

        self.appearanceConfig = AppearanceConfig()
        self.appearanceConfig.sigParameterChanged.connect(
            self.onAppearanceParamsChanged
        )

        # sigRangeChangedManually fires only on user interaction (rubber-band
        # zoom, pan, wheel), not on programmatic setRange calls.
        self.viewBox.sigRangeChangedManually.connect(self._onUserViewChange)
        self.viewBox.sigAxisGesture.connect(self._onAxisGesture)

        self.curveSettingsBox = SectionBox("Selected Curve", self._curvePlaceholder())

        hint = MutedLabel(
            "Drag to pan · Shift-drag to zoom to a box · Scroll to zoom · "
            "Scroll over an axis to zoom only it (over Y: stretch the spectra) · "
            "Right-drag to stretch axes · Drag on an axis to move only it"
        )
        hint.setWordWrap(True)

        # Most-used controls first: getting the view right, then what's
        # plotted, then per-curve tweaks, then cosmetics.
        sidebar = VBoxBuilder(Qt.AlignmentFlag.AlignTop).withWidget(
            SectionBox(
                "View",
                VBoxBuilder()
                .withWidget(ButtonBuilder("Fit to Data").onClick(self.fitToData))
                .withWidget(
                    ButtonBuilder("Export…")
                    .onClick(self.exportWithDialog)
                    .withToolTip(
                        "Save the plot as PNG, SVG or PDF, or its curves as CSV"
                    )
                )
                .withWidget(groupWidget(self.viewConfig))
                .withWidget(groupWidget(self.rangeConfig))
                .withWidget(hint),
            )
        )

        # Markers: draggable vertical wavelength lines spanning every spectrum,
        # for reading off where features sit and comparing them across curves.
        self.markers: list[pg.InfiniteLine] = []
        self.markerWavelength = QDoubleSpinBox()
        self.markerWavelength.setRange(0.0, 1_000_000.0)
        self.markerWavelength.setDecimals(1)
        self.markerWavelength.setSpecialValueText("view center")
        self.markerWavelength.setToolTip(
            "Wavelength for a new marker; leave at 0 to add it at the center of "
            "the visible range and drag it into place."
        )
        self.markerList = QListWidget()
        self.markerList.setMaximumHeight(90)
        sidebar.withWidget(
            SectionBox(
                "Markers",
                VBoxBuilder()
                .withLayout(
                    HBoxBuilder()
                    .withWidget(self.markerWavelength)
                    .withWidget(
                        ButtonBuilder("Add").onClick(
                            lambda: self.addMarker(
                                self.markerWavelength.value() or None
                            )
                        )
                    )
                )
                .withWidget(self.markerList)
                .withLayout(
                    HBoxBuilder()
                    .withWidget(
                        ButtonBuilder("Remove Selected").onClick(
                            self._removeSelectedMarker
                        )
                    )
                    .withWidget(ButtonBuilder("Clear").onClick(self.clearMarkers))
                )
                .withWidget(groupWidget(self.markerConfig)),
            )
        )
        # Labels dodge each other in pixel space, so re-lay them out whenever
        # the mapping from wavelengths to pixels changes.
        self.viewBox.sigRangeChanged.connect(lambda *_: self._layoutMarkerLabels())
        self.viewBox.sigResized.connect(lambda *_: self._layoutMarkerLabels())

        spectraNames = listSpectra(libraryPath) if libraryPath else []
        if spectraNames:
            self.libraryCombo = ElidingComboBox()  # long names are elided
            self.libraryCombo.addItems(spectraNames)
            libraryHint = MutedLabel(
                "Added spectra are scaled to overlay the selected curve "
                "within the visible wavelength range."
            )
            libraryHint.setWordWrap(True)
            sidebar.withWidget(
                SectionBox(
                    "Library Spectra",
                    VBoxBuilder()
                    .withWidget(self.libraryCombo)
                    .withWidget(
                        ButtonBuilder("Add to Plot").onClick(self._addLibrarySpectrum)
                    )
                    .withWidget(libraryHint),
                )
            )

        sidebar.withWidget(self.curveSettingsBox)
        sidebar.withWidget(SectionBox("Appearance", groupWidget(self.appearanceConfig)))
        self._sidebar = sidebar
        self.sidebarArea = PanelScrollArea(WrapperWidget(sidebar))
        # A slim arrow beside the plot folds the settings away in narrow docks
        self.sidebarToggle = QToolButton()
        self.sidebarToggle.setCheckable(True)
        self.sidebarToggle.setChecked(True)
        self.sidebarToggle.setAutoRaise(True)
        self.sidebarToggle.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
        )
        self.sidebarToggle.toggled.connect(self._onSidebarToggled)
        self._onSidebarToggled(True)

        self.setLayout(
            HBoxBuilder()
            .withWidget(self.gv, stretch=2)
            .withWidget(self.sidebarToggle)
            .withWidget(self.sidebarArea)
        )

    def _onSidebarToggled(self, visible: bool) -> None:
        self.sidebarArea.setVisible(visible)
        self.sidebarToggle.setArrowType(
            Qt.ArrowType.RightArrow if visible else Qt.ArrowType.LeftArrow
        )
        self.sidebarToggle.setToolTip(
            "Hide the plot settings" if visible else "Show the plot settings"
        )

    def setSidebarVisible(self, visible: bool) -> None:
        self.sidebarToggle.setChecked(visible)

    def isSidebarVisible(self) -> bool:
        return self.sidebarToggle.isChecked()

    def insertSidebarSection(self, index: int, section: QWidget) -> None:
        """Insert a section into the settings sidebar (0 = top)."""
        self._sidebar.insertWidget(index, section)

    def sizeHint(self) -> QSize:
        # Sensible window size so plot isn't squashed by default.
        # Layout stretch factors only distribute space *beyond*
        # sizeHint, so without this the widget opens at the sum of children's
        # natural sizes (sidebar wide, plot tiny) and stretch never kicks in.
        return QSize(1200, 600)

    def onAppearanceParamsChanged(self):
        self.gv.setBackground(self.appearanceConfig.backgroundColor.value.toQColor())

    def onViewParamsChanged(self):
        self.legend.setVisible(self.viewConfig.showLegend.value)
        for curve in self.plots:
            curve.setDataPoints(self._dataPointSize())
        mode = self.viewConfig.rangeMode.value
        if mode is RangeMode.AUTO:
            self.viewBox.setAutoVisible(y=False)
            self.plotItem.enableAutoRange()
            return
        if not self._manualRangeInitialized:
            self._seedManualRangeFromView()
        if mode is RangeMode.MANUAL:
            self.viewBox.setAutoVisible(y=False)
            self.plotItem.disableAutoRange()
        else:
            # Fit Y to X range: X comes from the range boxes; Y keeps fitting
            # itself to the data inside that window as curves come and go.
            self.viewBox.disableAutoRange(pg.ViewBox.XAxis)
            self.viewBox.setAutoVisible(y=True)
            self.viewBox.enableAutoRange(pg.ViewBox.YAxis)
        self.onRangeParamsChanged()

    def onRangeParamsChanged(self):
        mode = self.viewConfig.rangeMode.value
        if mode is RangeMode.AUTO:
            return
        xRange = self.rangeConfig.viewRangeX.value
        self.plotItem.setXRange(xRange.x, xRange.y, padding=0)
        if mode is RangeMode.MANUAL:
            yRange = self.rangeConfig.viewRangeY.value
            self.plotItem.setYRange(yRange.x, yRange.y, padding=0)
        else:
            # pyqtgraph only re-fits a visible-only axis on an X change when X
            # is auto-ranged too, so ask for the Y re-fit explicitly.
            self.viewBox.enableAutoRange(pg.ViewBox.YAxis)

    def _seedManualRangeFromView(self) -> None:
        xRange, yRange = self.viewBox.viewRange()
        self.rangeConfig.viewRangeX.set(Vec2(float(xRange[0]), float(xRange[1])))
        self.rangeConfig.viewRangeY.set(Vec2(float(yRange[0]), float(yRange[1])))
        self._manualRangeInitialized = True

    def _onUserViewChange(self) -> None:
        # User did a rubber-band zoom, pan, or wheel zoom: sync the range
        # params to the new view. In Auto mode the user has taken over, so
        # switch to Manual. In Fit-Y mode keep the mode: the X window is
        # theirs, and Y goes back to fitting the data inside it (the mouse
        # interaction turned Y auto-range off). Scrolling or dragging on the
        # Y axis is the exception: _onAxisGesture has already gone Manual.
        self._seedManualRangeFromView()
        mode = self.viewConfig.rangeMode.value
        if mode is RangeMode.AUTO:
            self.viewConfig.rangeMode.set(RangeMode.MANUAL)
        elif mode is RangeMode.FIT_Y_TO_X_RANGE:
            self.viewBox.enableAutoRange(pg.ViewBox.YAxis)

    def _onAxisGesture(self, axis: int) -> None:
        # Scrolling or dragging on the Y axis stretches or moves Y. In Fit-Y
        # mode the user has taken Y over, so go Manual (from the current view)
        # rather than refitting their change away; the change itself follows.
        if axis == 1 and self.viewConfig.rangeMode.value is RangeMode.FIT_Y_TO_X_RANGE:
            self._seedManualRangeFromView()
            self.viewConfig.rangeMode.set(RangeMode.MANUAL)

    def fitToData(self) -> None:
        """Frame all curves once, leaving the range under manual control."""
        self.viewBox.autoRange()
        self._onUserViewChange()

    @staticmethod
    def _curvePlaceholder() -> QLabel:
        label = MutedLabel("Click a curve to edit it.")
        return label

    # --- Wavelength markers ---

    def addMarker(self, wavelength: float | None = None) -> pg.InfiniteLine:
        """Add a draggable vertical marker at ``wavelength`` (default: the center
        of the visible wavelength range) with a label showing its position."""
        if wavelength is None:
            xMin, xMax = self.viewBox.viewRange()[0]
            wavelength = (xMin + xMax) / 2.0
        marker = pg.InfiniteLine(
            pos=wavelength,
            angle=90,
            movable=True,
            pen=pg.mkPen("#ffffffaa", width=1, style=Qt.PenStyle.DashLine),
            hoverPen=pg.mkPen("#ffff00", width=2),
        )
        marker.label = _MarkerLabel(
            marker,
            text="{value:.1f}",
            position=MARKER_LABEL_TOP,
            color="#ffffff",
            fill="#00000080",
            angle=90 if self.markerConfig.verticalLabels.value else 0,
        )
        marker.label.setVisible(self.markerConfig.showLabels.value)
        # ignoreBounds: markers must not affect auto-range or the view limits
        self.plotItem.addItem(marker, ignoreBounds=True)
        marker.sigPositionChanged.connect(self._onMarkerMoved)
        self.markers.append(marker)
        self._refreshMarkerList()
        self._layoutMarkerLabels()
        return marker

    def removeMarker(self, marker: pg.InfiniteLine) -> None:
        if marker not in self.markers:
            return
        self.plotItem.removeItem(marker)
        self.markers.remove(marker)
        self._refreshMarkerList()
        self._layoutMarkerLabels()

    def _onMarkerMoved(self) -> None:
        self._refreshMarkerList()
        self._layoutMarkerLabels()

    def _applyMarkerLabelStyle(self) -> None:
        for marker in self.markers:
            marker.label.setVisible(self.markerConfig.showLabels.value)
            marker.label.setAngle(90 if self.markerConfig.verticalLabels.value else 0)
        self._layoutMarkerLabels()

    def _layoutMarkerLabels(self) -> None:
        """Step a label down its line while it would overlap (in pixels) an
        already placed label at the same height. Labels the user dragged keep
        their place."""
        placed: list[tuple[float, float, float]] = []  # (left px, right px, position)
        for marker in sorted(self.markers, key=lambda m: m.value()):
            label = marker.label
            if not isinstance(label, _MarkerLabel) or label.userPlaced:
                continue
            centre = self.viewBox.mapViewToScene(QPointF(marker.value(), 0.0)).x()
            half = label.boundingRect().width() / 2.0
            left, right = centre - half, centre + half
            position = MARKER_LABEL_TOP
            while position > MARKER_LABEL_STEP and any(
                pos == position and lo < right and hi > left for lo, hi, pos in placed
            ):
                position -= MARKER_LABEL_STEP
            placed.append((left, right, position))
            if label.orthoPos != position:
                label.setPosition(position)

    def clearMarkers(self) -> None:
        for marker in list(self.markers):
            self.removeMarker(marker)

    def _removeSelectedMarker(self) -> None:
        row = self.markerList.currentRow()
        if 0 <= row < len(self.markers):
            self.removeMarker(self.markers[row])

    def _refreshMarkerList(self) -> None:
        self.markerList.clear()
        for marker in self.markers:
            self.markerList.addItem(f"{marker.value():.1f}")

    def _curvesChanged(self) -> None:
        """After curves are added, removed or moved: new view limits, and an
        auto-ranged axis refits now. pyqtgraph would refit only at the next
        repaint, and a visible-only Y (Fit Y To X Range) not even then, so a
        spectrum taller than the view (a hovered pixel, say) stayed cut off."""
        self._updateViewLimits()
        if self.plots and self.viewConfig.rangeMode.value is not RangeMode.MANUAL:
            self.viewBox.updateAutoRange()

    def _updateViewLimits(self) -> None:
        # Constrain panning and zooming so the view never extends past the
        # bounds of the plotted data.
        if not self.plots:
            self.viewBox.setLimits(
                xMin=None,
                xMax=None,
                yMin=None,
                yMax=None,
                maxXRange=None,
                maxYRange=None,
            )
            return
        xMin, xMax = float("inf"), float("-inf")
        yMin, yMax = float("inf"), float("-inf")
        for curve in self.plots:
            x, y = curve.plotDataItem.getData()
            if x is None or y is None or len(x) == 0:
                continue
            yScaled = (
                np.asarray(y) * curve.config.scale.value + curve.config.offset.value
            )
            finite = np.isfinite(x) & np.isfinite(yScaled)
            if not finite.any():
                continue
            # Only finite values: one NaN band would make the limits NaN, and
            # pyqtgraph then refuses every new range (the view freezes)
            xMin = min(xMin, float(np.min(x[finite])))
            xMax = max(xMax, float(np.max(x[finite])))
            yMin = min(yMin, float(np.min(yScaled[finite])))
            yMax = max(yMax, float(np.max(yScaled[finite])))
        if not np.isfinite(xMin) or xMax <= xMin or yMax <= yMin:
            return

        # apply padding
        xRange = xMax - xMin
        yRange = yMax - yMin
        xMin = xMin - (xRange * 0.05)
        xMax = xMax + (xRange * 0.05)
        yMin = yMin - (yRange * 0.05)
        yMax = yMax + (yRange * 0.05)

        self.viewBox.setLimits(
            xMin=xMin,
            xMax=xMax,
            yMin=yMin,
            yMax=yMax,
            maxXRange=xMax - xMin,
            maxYRange=yMax - yMin,
        )

    def plot(self, x, y, color: Color = Color(1.0, 0.0, 0.0, 0.5), **kwargs) -> Curve:
        """
        TODO: Maybe give each new plot a different starting color?

        :param self: Description
        :param x: Description
        :param y: Description
        :param kwargs: Description
        """
        curve = Curve.fromData(x, y, color, **kwargs)
        self._adoptCurve(curve)
        return curve

    def _adoptCurve(self, curve: Curve) -> None:
        curve.setClickable(True)
        curve.sigClicked.connect(self.selectPlot)
        # offset/scale changes shift visible y-bounds, so refresh limits.
        curve.config.sigParameterChanged.connect(self._updateViewLimits)
        curve.setDataPoints(self._dataPointSize())
        self.plots.append(curve)
        self.plotItem.addItem(curve.plotDataItem)
        self._curvesChanged()

    def _dataPointSize(self) -> int | None:
        if not self.viewConfig.showDataPoints.value:
            return None
        return int(self.viewConfig.dataPointSize.value)

    def keyPressEvent(self, a0) -> None:
        # Backspace / Delete remove the selected curve
        if a0 is not None and a0.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            if self.selectedCurve is not None:
                self.removePlot(self.selectedCurve)
                a0.accept()
                return
        super().keyPressEvent(a0)

    def selectPlot(self, curve: Curve) -> None:
        if self.selectedCurve is not None:
            self.selectedCurve.setHighlighted(False)
        self.selectedCurve = curve
        curve.setHighlighted(True)
        self.sigSelectedCurveChanged.emit(curve)
        self.curveSettingsBox.setContent(
            WrapperWidget(
                VBoxBuilder(Qt.AlignmentFlag.AlignTop)
                .withWidget(groupWidget(curve.config))
                .withWidget(
                    ButtonBuilder("Remove Curve").onClick(
                        lambda: self.removePlot(curve)
                    )
                )
            )
        )

    def onSceneClicked(self, event):
        if event.isAccepted():
            return
        self.deselectPlot()

    def deselectPlot(self):
        if self.selectedCurve is None:
            return
        self.selectedCurve.setHighlighted(False)
        self.curveSettingsBox.setContent(self._curvePlaceholder())
        self.selectedCurve = None
        self.sigSelectedCurveChanged.emit(None)

    def _addLibrarySpectrum(self) -> None:
        folderName = self.libraryCombo.currentText()
        name, wavelengths, reflectance = loadSpectrum(self.libraryPath, folderName)
        self.addReferenceSpectrum(name, wavelengths, reflectance)

    def addReferenceSpectrum(self, name: str, x, y) -> Curve:
        """Plot a reference (e.g. library) spectrum so it is immediately comparable.

        ``x`` is in nm (library spectra are). It is shown in the plot's spectral
        unit; when a curve to compare against exists (see ``_referenceCurve``),
        its Y scale/offset are set so it overlays that curve within the visible
        wavelength range. The view itself is left where it is.
        """
        x = np.asarray(x, dtype=float)
        if self.spectralUnit is not None:
            x = fromNanometres(x, self.spectralUnit)
        y = np.asarray(y, dtype=float)
        target = self._referenceCurve()
        if target is None:
            return self.plot(x, y, name=name)

        targetX, targetY = target.displayedData()
        curve = self.plot(x, y, name=name)
        visibleX = self.viewBox.viewRange()[0]
        scale, offset = matchToReference(
            x, y, targetX, targetY, (float(visibleX[0]), float(visibleX[1]))
        )
        curve.config.scale.set(scale)
        curve.config.offset.set(offset)
        return curve

    def _referenceCurve(self) -> Curve | None:
        """The curve a new reference spectrum is matched to: the selected one,
        else the most recently added."""
        if self.selectedCurve is not None:
            return self.selectedCurve
        return self.plots[-1] if self.plots else None

    def removePlot(self, curve: Curve) -> None:
        if curve not in self.plots:
            return
        self.plots.remove(curve)
        self._removeCurveItems(curve)
        if self.selectedCurve is curve:
            self.deselectPlot()
        self._curvesChanged()

    def onCurveDrop(self, event) -> None:
        data = json.loads(bytes(event.mimeData().data(CURVE_MIME_TYPE)).decode("utf-8"))
        if data.get("source_id") == id(self):
            event.ignore()
            return
        self._adoptCurve(Curve.deserialize(data))
        event.accept()

    def plotWithFill(
        self,
        x,
        y,
        yLower,
        yUpper,
        fillBrush,
        std: np.ndarray | None = None,
        count: np.ndarray | None = None,
        **kwargs,
    ) -> Curve:
        """Plot a curve with a filled region between yLower and yUpper.

        Useful for displaying mean +/- standard deviation. The band belongs to
        the returned curve: it follows its Y offset/scale and is removed with it.
        ``std`` and ``count`` (per sample) are kept on the curve for export.
        """
        curve = self.plot(x, y, **kwargs)
        curve.std = None if std is None else np.asarray(std, dtype=float)
        curve.count = None if count is None else np.asarray(count)

        upperCurve = pg.PlotDataItem(x, yUpper, pen=pg.mkPen(None))
        lowerCurve = pg.PlotDataItem(x, yLower, pen=pg.mkPen(None))
        fill = pg.FillBetweenItem(lowerCurve, upperCurve, brush=fillBrush)
        for item in (upperCurve, lowerCurve):
            self.plotItem.addItem(item)
        # The fill always reports its full Y extent, so Fit Y To X Range could
        # never shrink to the window; the band's own curves bound it instead
        self.plotItem.addItem(fill, ignoreBounds=True)
        curve.bandItems = [upperCurve, lowerCurve, fill]
        curve.onConfigChanged()  # apply the curve's transform to the band
        return curve

    @staticmethod
    def getPlottableWavelengths(image: VardaRaster, bandCount: int) -> np.ndarray:
        """The x values for an image's spectra: its wavelengths in its own unit
        when the bands are a spectral axis, otherwise band numbers."""
        model = image.bandModel
        if model.centers is not None and model.bandCount == bandCount:
            return model.native(model.centers)
        return np.arange(bandCount, dtype=float)

    def xValuesFor(self, image: VardaRaster, bandCount: int) -> np.ndarray:
        """``getPlottableWavelengths``, and the plot takes the image's unit for
        its x axis (label, and converting reference spectra)."""
        model = image.bandModel
        self.setSpectralUnit(model.unit if model.isSpectral else None)
        return self.getPlottableWavelengths(image, bandCount)

    def exportWithDialog(self) -> None:
        path, chosenFilter = QFileDialog.getSaveFileName(
            self, "Export Plot", "spectra.png", EXPORT_FILTER
        )
        if not path:
            return
        target = Path(path)
        if not target.suffix:  # take the suffix from the chosen filter
            target = target.with_suffix(chosenFilter.split("*")[-1].rstrip(")"))
        try:
            exportPlot(self, target)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Export failed", str(error))

    def setSpectralUnit(self, unit: SpectralUnit | None) -> None:
        self.spectralUnit = unit
        label = f"Wavelength ({unitLabel(unit)})" if unit is not None else "Band"
        self.plotItem.setLabel("bottom", label)

    def clearPlots(self):
        for curve in self.plots:
            self._removeCurveItems(curve)
        self.plots.clear()
        self._curvesChanged()

    def _removeCurveItems(self, curve: Curve) -> None:
        self.plotItem.removeItem(curve.plotDataItem)
        for item in curve.bandItems:
            self.plotItem.removeItem(item)


if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication
    import numpy as np

    app = QApplication([])

    w = VardaPlotWidget()
    x = np.linspace(0, 10, 100)
    y = np.sin(x)
    w.plot(x, y, pen=pg.mkPen(color="g", width=2), name="Sine Wave", antialias=True)

    x2 = np.linspace(0, 10, 100)
    y2 = np.cos(x2)
    w.plot(x2, y2, pen=pg.mkPen(color="r", width=2), name="Cosine Wave", antialias=True)
    w.show()

    app.exec()
