"""A plot for exploring pixel spectra from viewport clicks.

Spectra either accumulate (Collect) or supersede the previous one (Replace), and
each new spectrum takes the next color of a user-selected palette so it reads as
new. Pixel curves are tracked separately from other curves (e.g. library spectra
added for comparison), so Replace and Clear only touch spectra that came from
clicks.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from enum import Enum

import attrs
import matplotlib
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QPushButton, QWidget

from varda.common.entities import Color, VardaRaster
from varda.common.parameter import EnumParameter, IntParameter, ParameterGroup
from varda.common.ui import ButtonBuilder, FlowLayout, SectionBox, VBoxBuilder
from varda.plotting.plot import Curve, VardaPlotWidget
from varda.rois.region_statistics import boxPolygonPixels, computeRegionStatistics
from varda.common.parameter_widgets import groupWidget
from varda.spectral_libraries.spectrum import SpectrumOrigin
from varda.spectral_libraries.store import LibraryStore

logger = logging.getLogger(__name__)


class SpectrumMode(Enum):
    COLLECT = 1
    REPLACE = 2


class ColorScheme(Enum):
    """matplotlib colormaps; values are the colormap names.

    Qualitative palettes (a handful of distinct entries) are cycled entry by
    entry. Continuous maps are sampled at ``CONTINUOUS_STEPS`` evenly spread
    positions and then cycle, so successive spectra stay clearly distinct
    instead of taking near-identical neighboring shades.
    """

    # qualitative
    TAB10 = "tab10"
    TAB20 = "tab20"
    SET1 = "Set1"
    SET2 = "Set2"
    DARK2 = "Dark2"
    PAIRED = "Paired"
    ACCENT = "Accent"
    # continuous
    VIRIDIS = "viridis"
    PLASMA = "plasma"
    INFERNO = "inferno"
    MAGMA = "magma"
    CIVIDIS = "cividis"
    TURBO = "turbo"
    SPECTRAL = "Spectral"
    COOLWARM = "coolwarm"


# Distinct samples taken from a continuous colormap before it cycles.
CONTINUOUS_STEPS = 8
# Colormaps with at most this many entries are treated as qualitative palettes.
_MAX_QUALITATIVE_ENTRIES = 24


def paletteColor(scheme: ColorScheme, index: int) -> Color:
    """The ``index``-th color of the scheme, cycling past its end."""
    colormap = matplotlib.colormaps[scheme.value]
    if colormap.N <= _MAX_QUALITATIVE_ENTRIES:
        r, g, b, _a = colormap(index % colormap.N)
    else:
        position = (index % CONTINUOUS_STEPS) / (CONTINUOUS_STEPS - 1)
        r, g, b, _a = colormap(position)
    return Color(float(r), float(g), float(b), 1.0)


class PixelSpectraConfig(ParameterGroup):
    mode = EnumParameter(
        "Mode",
        SpectrumMode,
        SpectrumMode.COLLECT,
        "Collect keeps every clicked spectrum; Replace shows only the latest.",
    )
    colorScheme = EnumParameter(
        "Color Scheme",
        ColorScheme,
        ColorScheme.TAB10,
        "Palette cycled through so each new spectrum is distinct.",
    )
    windowWidth = IntParameter(
        "Window Width",
        1,
        range=(1, 99),
        units="px",
        description="Pixels across each selection: 1 plots one pixel; more plots the region's mean ± 1 σ",
    )
    windowHeight = IntParameter(
        "Window Height",
        1,
        range=(1, 99),
        units="px",
        description="Pixels down each selection",
    )

    def window(self) -> tuple[int, int]:
        return int(self.windowWidth.value), int(self.windowHeight.value)


@attrs.frozen
class PixelOrigin:
    """The image pixel a plotted spectrum was read from."""

    image: VardaRaster
    x: int  # the center pixel
    y: int
    width: int = 1  # the window around it, in pixels
    height: int = 1

    def corners(self) -> np.ndarray:
        """The window's (col, row) pixel-corner polygon (even sizes extend
        right and down)."""
        return boxPolygonPixels(self.x, self.y, self.width, self.height)


class PixelSpectraPlotWidget(VardaPlotWidget):
    # The set of pixel curves (or where they come from) changed
    sigPixelCurvesChanged = pyqtSignal()
    # The user wants this pixel curve's pixel saved as a point
    sigSavePointRequested = pyqtSignal(object)  # Curve

    def __init__(
        self,
        parent: QWidget | None = None,
        store: LibraryStore | None = None,
    ):
        super().__init__(parent, store)
        self.pixelConfig = PixelSpectraConfig()
        self.pixelCurves: list[Curve] = []
        # Where each pixel curve was read from (spectra added by other means,
        # e.g. ratios, have no origin)
        self.pixelOrigins: dict[Curve, PixelOrigin] = {}
        self._colorIndex = 0

        # Multi-plot controls; a workspace's PixelSpectraDocks wires these up.
        # Standalone, the box stays checked and the button does nothing.
        self.activeCheckBox = QCheckBox("Receives pixel clicks")
        self.activeCheckBox.setChecked(True)
        self.activeCheckBox.setToolTip("Ctrl+click pixel selections are plotted here")
        self.newPlotButton = QPushButton("New Pixel Plot")
        self.newPlotButton.setToolTip(
            "Open another plot, e.g. to collect spectra from a different region"
        )
        # Saving pixels as points; the workspace's Points Manager answers.
        self.savePointButton = ButtonBuilder("Save Point").onClick(
            self._requestSavePoint
        )
        self.savePointButton.setToolTip(
            "Save the selected (else the latest) pixel spectrum's pixel as a point"
        )
        self.saveAllPointsButton = ButtonBuilder("Save All Points").onClick(
            self._requestSaveAllPoints
        )
        self.sigPixelCurvesChanged.connect(self._updateSaveButtons)
        self._updateSaveButtons()

        # Directly after the base widget's "View" section
        self.insertSidebarSection(
            1,
            SectionBox(
                "Pixel Spectra",
                VBoxBuilder()
                .withWidget(groupWidget(self.pixelConfig))
                .withWidget(self.activeCheckBox)
                .withLayout(
                    FlowLayout()
                    .withWidget(
                        ButtonBuilder("Clear Spectra").onClick(self.clearPixelSpectra)
                    )
                    .withWidget(self.newPlotButton)
                )
                .withLayout(
                    FlowLayout()
                    .withWidget(self.savePointButton)
                    .withWidget(self.saveAllPointsButton)
                ),
            ),
        )

    def _pixelCurvesWithOrigin(self) -> list[Curve]:
        return [curve for curve in self.pixelCurves if curve in self.pixelOrigins]

    def _updateSaveButtons(self) -> None:
        enabled = bool(self._pixelCurvesWithOrigin())
        self.savePointButton.setEnabled(enabled)
        self.saveAllPointsButton.setEnabled(enabled)

    def _requestSavePoint(self) -> None:
        candidates = self._pixelCurvesWithOrigin()
        if not candidates:
            return
        selected = self.selectedCurve
        curve = selected if selected in candidates else candidates[-1]
        self.sigSavePointRequested.emit(curve)

    def _requestSaveAllPoints(self) -> None:
        for curve in self._pixelCurvesWithOrigin():
            self.sigSavePointRequested.emit(curve)

    def addPixelSpectrum(self, image: VardaRaster, x: int, y: int) -> Curve | None:
        """Plot the spectrum at pixel (x, y); returns None if out of bounds."""
        curves = self.addPixelSpectra([image], x, y)
        return curves[0] if curves else None

    def addPixelSpectra(
        self,
        images: Sequence[VardaRaster],
        x: int,
        y: int,
        *,
        labelWithImageName: bool = False,
    ) -> list[Curve]:
        """Plot the spectrum at pixel (x, y) of each image as one selection.

        In Replace mode the previous selection is cleared once, so every image
        of this selection stays. Images where (x, y) is out of bounds are
        skipped; a selection that hits no image leaves the plot unchanged.
        """
        inBounds = []
        for image in images:
            if 0 <= x < image.width and 0 <= y < image.height:
                inBounds.append(image)
            else:
                logger.warning(
                    f"Pixel ({x}, {y}) is outside the bounds of {image.name}"
                )
        if not inBounds:
            return []
        self._applyMode()

        width, height = self.pixelConfig.window()
        curves = []
        for image in inBounds:
            origin = PixelOrigin(image, x, y, width, height)
            single = width == height == 1
            where = f"({x}, {y})" if single else f"{width}×{height} at ({x}, {y})"
            kind = "Pixel" if single else "Region"
            label = f"{image.name} {where}" if labelWithImageName else f"{kind} {where}"
            curve = self._addOrigin(origin, label)
            if curve is not None:
                curves.append(curve)
        self.sigPixelCurvesChanged.emit()
        return curves

    def _addOrigin(self, origin: PixelOrigin, label: str) -> Curve | None:
        """Plot one pixel's spectrum, or a window's mean with a ±1 σ band."""
        image = origin.image
        where = f"(col {origin.x}, row {origin.y})"
        if origin.width == origin.height == 1:
            values = image.getSpectrum(origin.x, origin.y).values
            curve = self._addTracked(self.xValuesFor(image, len(values)), values, label)
            region, pixelCount = f"pixel {where}", 1
        else:
            statistics = computeRegionStatistics(origin.corners(), image)
            if not statistics["pixel_count"]:
                return None
            mean = np.asarray(statistics["mean"], dtype=float)
            spread = np.asarray(statistics["std"], dtype=float)
            color = self._nextColor()
            fill = color.toQColor()
            fill.setAlpha(60)
            curve = self.plotWithFill(
                self.xValuesFor(image, len(mean)),
                mean,
                mean - spread,
                mean + spread,
                pg.mkBrush(fill),
                std=spread,
                count=np.asarray(statistics["valid_count"]),
                color=color,
                name=label,
            )
            self._track(curve)
            region = f"{origin.width}×{origin.height} px box at {where}"
            pixelCount = int(np.asarray(statistics["pixel_count"]))
        self.pixelOrigins[curve] = origin
        curve.origin = SpectrumOrigin(
            image.name, image.filePath or "", region, pixelCount
        )
        return curve

    def addSpectrum(self, wavelengths, values, label: str) -> Curve:
        """Plot any spectrum under the pixel-plot rules (mode and palette)."""
        return self.addSpectra([(wavelengths, values, label)])[0]

    def addSpectra(self, entries: Sequence[tuple]) -> list[Curve]:
        """Plot several (wavelengths, values, label) spectra as one selection:
        Replace mode clears once, so all of them stay."""
        self._applyMode()
        curves = [self._addTracked(w, v, label) for w, v, label in entries]
        self.sigPixelCurvesChanged.emit()
        return curves

    def _applyMode(self) -> None:
        if self.pixelConfig.mode.value is SpectrumMode.REPLACE:
            self.clearPixelSpectra()

    def _addTracked(self, wavelengths, values, label: str) -> Curve:
        curve = self.plot(wavelengths, values, color=self._nextColor(), name=label)
        self._track(curve)
        return curve

    def _track(self, curve: Curve) -> None:
        self.pixelCurves.append(curve)
        # Its marker on the image follows a change of color
        curve.config.color.sigParameterChanged.connect(self._onCurveColorChanged)

    def _onCurveColorChanged(self, _color) -> None:
        self.sigPixelCurvesChanged.emit()

    def clearPixelSpectra(self) -> None:
        for curve in list(self.pixelCurves):
            self.removePlot(curve)
        self._colorIndex = 0

    def removePlot(self, curve: Curve) -> None:
        super().removePlot(curve)
        if curve in self.pixelCurves:
            self.pixelCurves.remove(curve)
            self.pixelOrigins.pop(curve, None)
            self.sigPixelCurvesChanged.emit()

    def _referenceCurve(self) -> Curve | None:
        """Prefer the latest pixel spectrum over other curves when none is selected."""
        if self.selectedCurve is None and self.pixelCurves:
            return self.pixelCurves[-1]
        return super()._referenceCurve()

    def _nextColor(self) -> Color:
        scheme = self.pixelConfig.colorScheme.value
        assert isinstance(scheme, ColorScheme)
        color = paletteColor(scheme, self._colorIndex)
        self._colorIndex += 1
        return color
