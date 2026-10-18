"""The CRISM Ratio workspace's plots: the numerator and denominator spectra
(overlaid on one plot, or separate), the ratio, and the denominator's
blandness readout, each shown in its own dock. The plots share one wavelength
range; every new selection replaces their spectra and refits their Y ranges.
Each plot can keep a shape (square, or taller than wide), so a spectrum is
never squashed into a strip."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject, QSize
from PyQt6.QtGui import QAction, QActionGroup, QColor, QCursor, QResizeEvent
from PyQt6.QtWidgets import QMenu, QSizePolicy, QVBoxLayout, QWidget

from varda.analysis.blandness import BlandnessResult
from varda.common.entities import Color
from varda.common.parameter import LabeledEnum
from varda.common.spectral_axis import SpectralUnit
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    DENOMINATOR_COLOR,
    NUMERATOR_COLOR,
)
from varda.plotting.plot import VardaPlotWidget
from varda.workspaces.crism_ratio.blandness_readout import BlandnessReadout
from varda.workspaces.ratio_explorer import RatioComponents

RATIO_COLOR = Color(0.9, 0.5, 0.0, 1.0)
FAILING_COLOR = QColor(220, 50, 47)  # failing windows and their dips


def _color(color: QColor) -> Color:
    return Color(color.redF(), color.greenF(), color.blueF(), 1.0)


_MIN_PLOT_SIZE = 150  # px, each side


NEEDS_WAVELENGTHS = (
    "The blandness check needs wavelengths (nm, µm or cm⁻¹); this image has "
    "none, so the plots are by band number."
)


class PlotShape(LabeledEnum):
    """The shape a plot keeps as its dock is resized."""

    FREE = "Free"
    SQUARE = "Square"
    TALL = "Tall (3:4)"


_ASPECT = {PlotShape.SQUARE: 1.0, PlotShape.TALL: 3 / 4}  # width / height


class AspectBox(QWidget):
    """Holds one widget at its shape's width:height, as large as fits and
    centered, leaving the spare space empty; Free fills the box. (The plot
    widget keeps its shape; the data's scale is not locked.)"""

    def __init__(self, child: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.child = child
        child.setParent(self)
        self.shape = PlotShape.FREE
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def setShape(self, shape: PlotShape) -> None:
        self.shape = shape
        self._place()

    def minimumSizeHint(self) -> QSize:
        return self.child.minimumSizeHint().expandedTo(self.child.minimumSize())

    def sizeHint(self) -> QSize:
        return QSize(360, 480)

    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        super().resizeEvent(a0)
        self._place()

    def _place(self) -> None:
        width, height = self.width(), self.height()
        aspect = _ASPECT.get(self.shape)
        if aspect is not None:
            width, height = (
                min(width, round(height * aspect)),
                min(height, round(width / aspect)),
            )
        self.child.setGeometry(
            (self.width() - width) // 2, (self.height() - height) // 2, width, height
        )


class ShapeAction(QAction):
    """A dock title-bar menu choosing the shape of a dock's plots."""

    def __init__(self, boxes: Sequence[AspectBox], parent: QObject) -> None:
        super().__init__(parent)
        self.boxes = list(boxes)
        self.setToolTip(
            "Plot shape: Free fills the panel; Square and Tall (3:4) keep the "
            "plot's width to height as the panel is resized"
        )
        self._menu = QMenu()
        self._group = QActionGroup(self)
        self._choices: dict[PlotShape, QAction] = {}
        for shape in PlotShape:
            action = self._menu.addAction(shape.value)
            assert action is not None
            action.setCheckable(True)
            action.setData(shape.name)
            self._group.addAction(action)
            self._choices[shape] = action
        # bound methods, so Qt drops the connections with the action. The
        # menu pops up from the trigger rather than being the action's menu:
        # the title bar would draw its arrow over the text
        self._group.triggered.connect(self._onChosen)
        self.triggered.connect(self._popUp)
        self.setShape(PlotShape.FREE)

    def _popUp(self) -> None:
        self._menu.popup(QCursor.pos())

    @property
    def shape(self) -> PlotShape:
        return self.boxes[0].shape

    def setShape(self, shape: PlotShape) -> None:
        for box in self.boxes:
            box.setShape(shape)
        self._choices[shape].setChecked(True)
        self.setText(f"Shape: {shape.value} ▾")

    def _onChosen(self, action: QAction) -> None:
        self.setShape(PlotShape[action.data()])


def _plot() -> VardaPlotWidget:
    plot = VardaPlotWidget()
    # the sidebar (and the Ratio plot's library overlay picker in it) starts
    # folded: open, it would squeeze the plot
    plot.setSidebarVisible(False)
    plot.setMinimumSize(_MIN_PLOT_SIZE, _MIN_PLOT_SIZE)
    plot.setSpectralUnit(SpectralUnit.NANOMETRE)
    return plot


class RatioPlots(QObject):
    """The plots and the readout, as three panels for the workspace's docks:
    ``spectraPanel`` (numerator and denominator), ``ratioPanel`` and
    ``readout``. ``spectraActions`` and ``ratioActions`` go in the docks'
    title bars."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # the denominator (with the dip markers) is always on spectraPlot;
        # the numerator joins it, or has numeratorPlot to itself when separate
        self.spectraPlot = _plot()
        self.numeratorPlot = _plot()
        self.ratioPlot = _plot()
        self._plots = (self.spectraPlot, self.numeratorPlot, self.ratioPlot)
        # One X range for all of them. pyqtgraph's setXLink aligns screen
        # pixels, so a narrower plot would show only a slice of the spectrum.
        self._syncingX = False
        for plot in self._plots:
            plot.viewBox.sigXRangeChanged.connect(self._followX)

        self.numeratorBox = AspectBox(self.numeratorPlot)
        self.numeratorBox.hide()
        self.spectraBox = AspectBox(self.spectraPlot)
        self.ratioPanel = AspectBox(self.ratioPlot)
        self.spectraPanel = QWidget()
        layout = QVBoxLayout(self.spectraPanel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.numeratorBox, 1)
        layout.addWidget(self.spectraBox, 1)

        self.separateAction = QAction("Separate", self)
        self.separateAction.setCheckable(True)
        self.separateAction.setToolTip(
            "Plot the numerator and the denominator on two plots, one above "
            "the other, instead of overlaid on one"
        )
        self.separateAction.toggled.connect(self._onSeparateToggled)
        self.spectraShape = ShapeAction((self.numeratorBox, self.spectraBox), self)
        self.ratioShape = ShapeAction((self.ratioPanel,), self)
        self.spectraActions = [self.separateAction, self.spectraShape]
        self.ratioActions = [self.ratioShape]

        self.readout = BlandnessReadout()
        # what marks the failing windows on the denominator's curve: a shaded
        # region per window, and a marker with a label at its deepest dip
        self.dipMarkers: list[pg.GraphicsObject] = []
        self._components: RatioComponents | None = None
        self._denominatorCurve: tuple[np.ndarray, np.ndarray] | None = None
        self.readout.sigVerdictChanged.connect(self._markFailingWindows)

    def isSeparate(self) -> bool:
        return self.separateAction.isChecked()

    def setSeparate(self, separate: bool) -> None:
        self.separateAction.setChecked(separate)

    def _onSeparateToggled(self, separate: bool) -> None:
        self.numeratorBox.setVisible(separate)
        if self._components is not None:
            xRange = self.ratioPlot.viewBox.viewRange()[0]
            self._drawSpectra(*self._curves(self._components))
            for plot in (self.spectraPlot, self.numeratorPlot):
                plot.viewBox.setXRange(*xRange, padding=0)

    def _followX(self, source: pg.ViewBox, xRange) -> None:
        if self._syncingX:
            return
        self._syncingX = True
        try:
            for plot in self._plots:
                if plot.viewBox is not source:
                    plot.viewBox.setXRange(*xRange, padding=0)
        finally:
            self._syncingX = False

    @staticmethod
    def _curves(c: RatioComponents) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
        """The x values (nm, or band numbers without wavelengths), which
        bands are good, and the wavelengths in nm (None without them)."""
        # the plots and the blandness checks work in nm whatever the image's
        # unit; without wavelengths the plots go by band number
        model = c.image.bandModel
        bands = len(c.ratio)
        nm = model.centers if model.bandCount == bands else None
        x = np.arange(1, bands + 1, dtype=np.float64) if nm is None else nm
        # bad bands (ENVI bbl) are left out, as in the Ratio Cube
        good = np.ones(bands, bool)
        if model.badBands.size == bands:
            good = ~model.badBands
        return x, good, nm

    @staticmethod
    def _replace(
        plot: VardaPlotWidget,
        x: np.ndarray,
        curves: Sequence[tuple[np.ndarray, Color, str | None]],
        unit: SpectralUnit | None,
    ) -> None:
        """Put ``curves`` (y, color, legend name) in place of the plot's
        spectra (library spectra stay) and refit."""
        for curve in [p for p in plot.plots if p.librarySpectrum is None]:
            plot.removePlot(curve)
        plot.setSpectralUnit(unit)
        for y, color, name in curves:
            plot.plot(x, y, color, name=name)
        plot.fitToData()

    def _drawSpectra(self, x: np.ndarray, good: np.ndarray, nm) -> None:
        c = self._components
        assert c is not None
        unit = SpectralUnit.NANOMETRE if nm is not None else None
        numerator = (
            np.where(good, np.asarray(c.numerator, np.float64), np.nan),
            _color(NUMERATOR_COLOR),
            "Numerator",
        )
        denominator = (
            np.where(good, np.asarray(c.denominator, np.float64), np.nan),
            _color(DENOMINATOR_COLOR),
            "Denominator",
        )
        if self.isSeparate():
            self._replace(self.numeratorPlot, x, [numerator], unit)
            self._replace(self.spectraPlot, x, [denominator], unit)
        else:
            self._replace(self.numeratorPlot, x, [], unit)
            self._replace(self.spectraPlot, x, [numerator, denominator], unit)

    def showComponents(self, c: RatioComponents) -> None:
        self._components = c
        x, good, nm = self._curves(c)
        self._drawSpectra(x, good, nm)
        ratio = np.where(good, np.asarray(c.ratio, np.float64), np.nan)
        self._replace(
            self.ratioPlot,
            x,
            [(ratio, RATIO_COLOR, None)],
            SpectralUnit.NANOMETRE if nm is not None else None,
        )
        self._denominatorCurve = (
            (nm[good], np.asarray(c.denominator, np.float64)[good])
            if nm is not None
            else None
        )
        # the spectrum's own range, whatever library curves the Ratio plot has
        finite = good & np.isfinite(x)
        if finite.any():
            self.spectraPlot.viewBox.setXRange(x[finite].min(), x[finite].max())
        if nm is None:
            self.readout.setUnavailable(NEEDS_WAVELENGTHS)
        else:
            self.readout.setSpectrum(nm[good], np.asarray(c.denominator)[good])

    def _markFailingWindows(self, result: BlandnessResult) -> None:
        plotItem = self.spectraPlot.plotItem
        for item in self.dipMarkers:
            plotItem.removeItem(item)
        self.dipMarkers = []
        if self._denominatorCurve is None:
            return
        x, y = self._denominatorCurve
        order = np.argsort(x)
        shade = QColor(FAILING_COLOR)
        shade.setAlpha(40)
        for w in result.windows:
            if w.passed:
                continue
            region = pg.LinearRegionItem(
                (w.window.lowNm, w.window.highNm),
                movable=False,
                brush=pg.mkBrush(shade),
                pen=pg.mkPen(None),
            )
            region.setZValue(-10)
            region.setToolTip(f"{w.window.name}: not bland")
            plotItem.addItem(region, ignoreBounds=True)
            self.dipMarkers.append(region)
            if not np.isfinite(w.depth):
                continue
            at = float(np.interp(w.dipNm, x[order], y[order]))
            marker = pg.ScatterPlotItem(
                [w.dipNm], [at], symbol="t", size=10, brush=pg.mkBrush(FAILING_COLOR)
            )
            label = pg.TextItem(
                f"{w.window.name}: {100 * w.depth:.1f} %",
                color=FAILING_COLOR,
                anchor=(0.5, 0),
            )
            label.setPos(w.dipNm, at)
            for item in (marker, label):
                plotItem.addItem(item, ignoreBounds=True)
                self.dipMarkers.append(item)
