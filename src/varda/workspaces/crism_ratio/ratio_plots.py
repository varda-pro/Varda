"""The Ratio Explorer's plots: numerator, denominator (with its blandness
readout) and ratio, stacked on a shared wavelength axis. Every new selection
replaces each plot's spectrum and refits its Y range."""

from __future__ import annotations

import functools

import numpy as np
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from varda.common.entities import Color
from varda.common.spectral_axis import SpectralUnit
from varda.common.ui import MutedLabel
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    DENOMINATOR_COLOR,
    NUMERATOR_COLOR,
)
from varda.plotting.plot import VardaPlotWidget
from varda.workspaces.crism_ratio.blandness_readout import BlandnessReadout
from varda.workspaces.ratio_explorer import RatioComponents

RATIO_COLOR = Color(0.9, 0.5, 0.0, 1.0)


def _color(color: QColor) -> Color:
    return Color(color.redF(), color.greenF(), color.blueF(), 1.0)


_MIN_PLOT_HEIGHT = 120


NEEDS_WAVELENGTHS = (
    "The blandness check needs wavelengths (nm, µm or cm⁻¹); this image has "
    "none, so the plots are by band number."
)


class RatioPlotsPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.numeratorPlot = self._plot()
        self.denominatorPlot = self._plot()
        # the Ratio plot's sidebar (the library overlay picker) starts folded:
        # open, it would squeeze the plot narrower than the other two
        self.ratioPlot = self._plot()
        self._plots = (self.numeratorPlot, self.denominatorPlot, self.ratioPlot)
        # One X range for all three. pyqtgraph's setXLink aligns screen pixels,
        # so a narrower plot would show only a slice of the spectrum.
        self._syncingX = False
        for plot in self._plots:
            plot.viewBox.sigXRangeChanged.connect(
                functools.partial(self._followX, plot)
            )
        self.readout = BlandnessReadout()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for title, plot in (
            ("Numerator", self.numeratorPlot),
            ("Denominator", self.denominatorPlot),
        ):
            layout.addWidget(MutedLabel(title))
            layout.addWidget(plot, 1)
        layout.addWidget(self.readout)
        layout.addWidget(MutedLabel("Ratio"))
        layout.addWidget(self.ratioPlot, 1)

    @staticmethod
    def _plot() -> VardaPlotWidget:
        plot = VardaPlotWidget()
        plot.setSidebarVisible(False)
        plot.setMinimumHeight(_MIN_PLOT_HEIGHT)
        plot.setSpectralUnit(SpectralUnit.NANOMETRE)
        return plot

    def _followX(self, source: VardaPlotWidget, _viewBox, xRange) -> None:
        if self._syncingX:
            return
        self._syncingX = True
        try:
            for plot in self._plots:
                if plot is not source:
                    plot.viewBox.setXRange(*xRange, padding=0)
        finally:
            self._syncingX = False

    def showComponents(self, c: RatioComponents) -> None:
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
        for plot, y, color in (
            (self.numeratorPlot, c.numerator, _color(NUMERATOR_COLOR)),
            (self.denominatorPlot, c.denominator, _color(DENOMINATOR_COLOR)),
            (self.ratioPlot, c.ratio, RATIO_COLOR),
        ):
            for curve in [p for p in plot.plots if p.librarySpectrum is None]:
                plot.removePlot(curve)
            plot.setSpectralUnit(SpectralUnit.NANOMETRE if nm is not None else None)
            plot.plot(x, np.where(good, np.asarray(y, np.float64), np.nan), color)
            plot.fitToData()
        # the spectrum's own range, whatever library curves the Ratio plot has
        finite = good & np.isfinite(x)
        if finite.any():
            self.numeratorPlot.viewBox.setXRange(x[finite].min(), x[finite].max())
        if nm is None:
            self.readout.setUnavailable(NEEDS_WAVELENGTHS)
        else:
            self.readout.setSpectrum(nm[good], np.asarray(c.denominator)[good])
