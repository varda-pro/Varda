"""Linked spectra: one plot per image, stacked, each showing that image's
spectrum where the linked cursor is. Products of one pipeline (a result,
its sample counts, its flags) have different units, so each plot has its
own axes and pans and zooms on its own.

A bit mask (a pipeline's per-frame exclusions: bit i set where frame i was
left out) reads better as bits than as a number: such images show as a
chart of bits by wavelength, which is the default for whole-number images
named like masks and can be switched for any whole-number image.

Export writes every image's spectrum at the cursor as CSV: the pixel read
(column, row), the cursor's map position in that image's CRS, and the
x axis's unit (a wavelength unit, or band numbers), under a commented
header naming each image's CRS."""

from __future__ import annotations

import csv
import re
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyproj import CRS
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from varda.common.keys import controlClick
from varda.common.entities import VardaRaster
from varda.common.palette import classColor
from varda.common.ui import MutedLabel
from varda.version import vardaVersion
from varda.workspaces.linked_images.linking import (
    LinkedSpectrum,
    bitPlanes,
    highestBit,
    spectrumAt,
)

MASK_NAMES = re.compile(r"exclu|mask|bits|\bqa\b|quality", re.IGNORECASE)
BIT_COLORS = np.array([[24, 24, 28, 255], [255, 212, 0, 255]], np.uint8)


def wholeNumbers(image: VardaRaster) -> bool:
    return bool(np.issubdtype(image.dtype, np.integer))


class LinkedSpectraPanel(QWidget):
    def __init__(self, images: Sequence[VardaRaster], parent: QWidget | None = None):
        super().__init__(parent)
        self.images = list(images)
        self.spectra: list[LinkedSpectrum | None] = [None] * len(self.images)
        self.layoutWidget = pg.GraphicsView()
        stack = pg.GraphicsLayout()
        self.layoutWidget.setCentralItem(stack)
        self.plots: list[pg.PlotItem] = []
        self.curves: list[pg.PlotDataItem] = []
        self.bitImages: list[pg.ImageItem] = []
        self.bitWidths = [1] * len(self.images)  # bits shown, growing as needed
        self.asBits = [
            wholeNumbers(image) and bool(MASK_NAMES.search(image.name))
            for image in self.images
        ]
        self._positions: list[np.ndarray] = []
        for row, image in enumerate(self.images):
            plot = pg.PlotItem()
            stack.addItem(plot, row=row, col=0)
            plot.setTitle(image.name, size="9pt")
            plot.showGrid(x=True, y=True, alpha=0.2)
            curve = plot.plot(pen=pg.mkPen(classColor(row), width=1.5))
            bits = pg.ImageItem(axisOrder="row-major")
            bits.setLookupTable(BIT_COLORS)
            bits.setLevels((0, 1))
            plot.addItem(bits)
            self.plots.append(plot)
            self.curves.append(curve)
            self.bitImages.append(bits)
        # whole-number images can be shown as bits
        self.bitBoxes: dict[str, QAction] = {}
        self.bitsButton = QToolButton()
        self.bitsButton.setText("As Bits")
        self.bitsButton.setToolTip(
            "Show whole-number images as bits by wavelength (bit masks)"
        )
        self.bitsButton.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        bitsMenu = QMenu(self.bitsButton)
        for index, image in enumerate(self.images):
            if not wholeNumbers(image):
                continue
            action = bitsMenu.addAction(image.name)
            assert action is not None
            action.setCheckable(True)
            action.setChecked(self.asBits[index])
            action.toggled.connect(lambda on, index=index: self.showAsBits(index, on))
            self.bitBoxes[image.name] = action
        self.bitsButton.setMenu(bitsMenu)
        self.bitsButton.setVisible(bool(self.bitBoxes))
        self.hint = MutedLabel(
            f"Click or {controlClick('drag')} in any view (Pixel Select) to "
            "read every image there."
        )
        self.hint.setWordWrap(True)
        export = QPushButton("Export…")
        export.setToolTip("Save the spectra at the cursor as CSV")
        export.clicked.connect(self._export)
        bottom = QHBoxLayout()
        bottom.addWidget(self.hint, 1)
        bottom.addWidget(self.bitsButton)
        bottom.addWidget(export)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.layoutWidget, 1)
        layout.addLayout(bottom)

    def showAsBits(self, index: int, on: bool) -> None:
        self.asBits[index] = on
        if self._positions:
            self.showAt(self._positions)

    def showAt(self, positions: Sequence[np.ndarray]) -> None:
        """Each image's spectrum at its position (image coordinates)."""
        self._positions = list(positions)
        for i, (image, point) in enumerate(zip(self.images, positions)):
            x, y = (float(v) for v in np.asarray(point).reshape(-1)[:2])
            spectrum = spectrumAt(image, x, y)
            self.spectra[i] = spectrum
            plot, curve = self.plots[i], self.curves[i]
            bits = self.bitImages[i]
            if spectrum is None:
                curve.setData([], [])
                bits.setVisible(False)
                plot.setTitle(f"{image.name}: off the image", size="9pt")
                continue
            title = f"{image.name} ({int(x)}, {int(y)})"
            if self.asBits[i]:
                curve.setData([], [])
                title += self._showBits(i, spectrum)
            else:
                bits.setVisible(False)
                curve.setData(spectrum.displayAxis, spectrum.values, connect="finite")
                plot.setLabel("left", "")
            plot.setTitle(title, size="9pt")
            plot.setLabel("bottom", spectrum.axisLabel)

    def _showBits(self, index: int, spectrum: LinkedSpectrum) -> str:
        """Draw a spectrum of bit masks as bits by wavelength; returns how
        many bits are set, for the title."""
        dtype = self.images[index].dtype
        width = max(self.bitWidths[index], highestBit(spectrum.values, dtype))
        self.bitWidths[index] = width
        planes = bitPlanes(spectrum.values, width, dtype)
        axis = spectrum.displayAxis
        step = float(np.median(np.diff(axis))) if axis.size > 1 else 1.0
        bits = self.bitImages[index]
        bits.setImage(planes, levels=(0, 1), autoLevels=False)
        bits.setRect(QRectF(axis[0] - step / 2, -0.5, step * axis.size, width))
        bits.setVisible(True)
        self.plots[index].setLabel("left", "Bit")
        values = int(np.isfinite(planes[0]).sum())
        count = int(np.nansum(planes))
        return f": {count} bits set, {count / max(values, 1):.1f} per band"

    def _export(self) -> None:
        if not any(s is not None for s in self.spectra):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Spectra", "linked_spectra.csv", "CSV data (*.csv)"
        )
        if path:
            try:
                self.exportTo(Path(path))
            except OSError as error:
                QMessageBox.warning(self, "Export Spectra", str(error))

    def exportTo(self, path: Path) -> None:
        """The spectra at the cursor: per image, the pixel read (``col``,
        ``row``, counted from 0), the cursor's map position (``map_x``,
        ``map_y``, in the image's CRS; empty without georeferencing), ``x``
        in ``x_unit`` (a wavelength unit, or "band" for band numbers) and the
        value."""
        header = [f"Varda {vardaVersion()} linked spectra at the cursor"]
        rows = []
        for image, spectrum, point in zip(self.images, self.spectra, self._positions):
            if spectrum is None:
                continue
            x, y = (float(v) for v in np.asarray(point).reshape(-1)[:2])
            col, row = int(np.floor(x)), int(np.floor(y))
            mapX = mapY = ""
            if image.hasGeospatialData:
                mapped = image.imageToGeo(np.array([[x, y]]))[0]
                mapX, mapY = repr(float(mapped[0])), repr(float(mapped[1]))
                crs = CRS.from_user_input(image.crs)
                header.append(f"{image.name}: CRS {crs.name}; WKT {crs.to_wkt()}")
            else:
                header.append(f"{image.name}: no georeferencing")
            for axis, value in zip(spectrum.displayAxis, spectrum.values):
                rows.append(
                    [
                        image.name,
                        col,
                        row,
                        mapX,
                        mapY,
                        repr(float(axis)),
                        spectrum.axisUnit,
                        "" if np.isnan(value) else f"{value:.10g}",
                    ]
                )
        with path.open("w", newline="") as f:
            f.writelines(f"# {line}\n" for line in header)
            out = csv.writer(f)
            columns = ["image", "col", "row", "map_x", "map_y", "x", "x_unit"]
            out.writerow([*columns, "value"])
            out.writerows(rows)
