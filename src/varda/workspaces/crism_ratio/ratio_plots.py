"""The Ratio Explorer's plots: numerator, denominator (with its blandness
readout) and ratio, stacked on a shared wavelength axis. Every new selection
replaces each plot's spectrum and refits its Y range."""

from __future__ import annotations

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


class RatioPlotsPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.numeratorPlot = self._plot()
        self.denominatorPlot = self._plot()
        self.ratioPlot = self._plot()  # keeps its sidebar: the library overlay picker
        self.ratioPlot.setSidebarVisible(True)
        self.denominatorPlot.viewBox.setXLink(self.numeratorPlot.viewBox)
        self.ratioPlot.viewBox.setXLink(self.numeratorPlot.viewBox)
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

    def showComponents(self, c: RatioComponents) -> None:
        for plot, y, color in (
            (self.numeratorPlot, c.numerator, _color(NUMERATOR_COLOR)),
            (self.denominatorPlot, c.denominator, _color(DENOMINATOR_COLOR)),
            (self.ratioPlot, c.ratio, RATIO_COLOR),
        ):
            for curve in [p for p in plot.plots if p.librarySpectrum is None]:
                plot.removePlot(curve)
            plot.plot(np.asarray(c.wavelengths), np.asarray(y), color)
            plot.fitToData()
        self.readout.setSpectrum(c.wavelengths, c.denominator)
