"""Profiles plot: a band sampled along lines, against distance along them.

Each line is one curve in its color. Hovering reads out the distance and
value and reports where that is on the image (sigCursor), so the views can
mark it. For a DEM with distances in meters the vertical exaggeration is
shown. Export: CSV (every sample, with its pixel and map position, under a
commented header naming the image, its CRS, the units, how distances were
measured and their limits), PNG, SVG and PDF."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyproj import CRS
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from varda.common.entities import VardaRaster
from varda.common.ui import MutedLabel
from varda.geo.bodies import surfaceText
from varda.geo.describe import describeCrs
from varda.lines.line_collection import VardaLine
from varda.lines.profile import PLANAR_METHOD, Profile, lineMeasurement, profile
from varda.plotting.plot_export import IMAGE_FILTER, exportImage
from varda.utilities.proj_lock import PROJ_LOCK
from varda.version import vardaVersion

KM_FROM = 5000.0  # m: longer profiles are plotted in kilometers
EXPORT_FILTER = f"CSV data (*.csv);;{IMAGE_FILTER}"


def bandLabel(image: VardaRaster, band: int) -> str:
    names = list(image.bandNames or [])
    if band < len(names) and names[band]:
        return str(names[band])
    wavelengths = image.wavelengths
    if wavelengths is not None and band < len(wavelengths):
        return f"Band {band + 1} ({wavelengths[band]})"
    return f"Band {band + 1}"


def valueUnit(image: VardaRaster, band: int) -> str:
    """The unit of a band's values, where Varda knows it: a result of one of
    its analyses keeps it in place of a wavelength unit (its band labeled
    by name, not wavelength); otherwise ""."""
    wavelengths = image.wavelengths
    if wavelengths is None or band >= len(wavelengths):
        return ""
    if np.issubdtype(np.asarray(wavelengths).dtype, np.number):
        return ""  # a wavelength's unit, not the values'
    return image.wavelengthUnits or ""


def crsLabel(crs: CRS | None) -> str:
    """A CRS in words: its body, surface and projection."""
    return describeCrs(crs).text


class ProfilePlotWidget(QWidget):
    sigCursor = pyqtSignal(object)  # image px (2,) of the hovered sample, or None

    def __init__(
        self,
        image: VardaRaster,
        parent: QWidget | None = None,
        emptyText: str = "Draw a line with Draw Line to see its profile.",
    ):
        super().__init__(parent)
        self.image = image
        self.lines: list[VardaLine] = []
        self.profiles: dict[int, Profile] = {}  # fid -> its profile
        self._bands: dict[int, np.ndarray] = {}  # band index -> 2D array
        self._scale = 1.0  # plotted distance = meters (or px) / scale

        self.bandBox = QComboBox()
        for band in range(image.bandCount):
            self.bandBox.addItem(bandLabel(image, band), band)
        self.bandBox.currentIndexChanged.connect(lambda _: self._replot())
        self.plot = pg.PlotWidget()
        self.plotItem: pg.PlotItem = self.plot.getPlotItem()
        self.plotItem.showGrid(x=True, y=True, alpha=0.25)
        self.plotItem.addLegend(offset=(-10, 10))
        self.cursorLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("w"))
        self.cursorLine.setVisible(False)
        self.plotItem.addItem(self.cursorLine, ignoreBounds=True)
        scene = self.plot.scene()
        assert isinstance(scene, pg.GraphicsScene)
        scene.sigMouseMoved.connect(self._onMouseMoved)
        self.plot.getViewBox().sigRangeChanged.connect(
            lambda *_: self._showExaggeration()
        )
        self.readout = QLabel()
        self.exaggeration = MutedLabel("")
        self.exaggeration.setToolTip(
            "How much the vertical axis is stretched against the horizontal, "
            "for values in meters (a DEM)"
        )
        export = QPushButton("Export…")
        export.setToolTip("Save the profiles as CSV, or the plot as PNG, SVG or PDF")
        export.clicked.connect(self._export)
        top = QHBoxLayout()
        top.addWidget(QLabel("Band:"))
        top.addWidget(self.bandBox, 1)
        top.addWidget(export)
        bottom = QHBoxLayout()
        bottom.addWidget(self.readout, 1)
        bottom.addWidget(self.exaggeration)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.plot, 1)
        layout.addLayout(bottom)
        self.empty = MutedLabel(emptyText)
        layout.addWidget(self.empty)
        self._replot()

    def band(self) -> int:
        return int(self.bandBox.currentData() or 0)

    def setLines(self, lines: Sequence[VardaLine]) -> None:
        self.lines = list(lines)
        self._replot()

    def _bandData(self, band: int) -> np.ndarray:
        if band not in self._bands:
            raw = self.image.getBands([band])
            data = np.ma.filled(raw, np.nan) if np.ma.isMaskedArray(raw) else raw
            data = np.asarray(data, dtype=float)[:, :, 0]
            nodata = self.image.nodata
            if nodata is not None and np.isfinite(nodata):
                data = np.where(data == nodata, np.nan, data)
            self._bands = {band: data}  # one band at a time: DEMs can be large
        return self._bands[band]

    def _replot(self) -> None:
        self.plotItem.clear()
        legend = self.plotItem.legend
        if legend is not None:
            legend.clear()
        self.plotItem.addItem(self.cursorLine, ignoreBounds=True)
        self.profiles = {}
        self.empty.setVisible(not self.lines)
        if not self.lines:
            self.readout.setText("")
            self.exaggeration.setText("")
            return
        band = self._bandData(self.band())
        georeferenced = self.image.hasGeospatialData
        for line in self.lines:
            self.profiles[line.fid] = profile(
                band,
                line.vertices,
                self.image.transform if georeferenced else None,
                self.image.crs if georeferenced else None,
            )
        units = next(iter(self.profiles.values())).distanceUnits
        longest = max(float(p.distance[-1]) for p in self.profiles.values())
        self._scale = 1000.0 if units == "m" and longest >= KM_FROM else 1.0
        unit = "km" if self._scale == 1000.0 else units
        for line in self.lines:
            result = self.profiles[line.fid]
            self.plot.plot(
                result.distance / self._scale,
                result.values,
                pen=pg.mkPen(line.color, width=2),
                name=line.name,
                connect="finite",
            )
        self.plot.setLabel("bottom", f"Distance ({unit})")
        self.plot.setLabel("left", self.bandBox.currentText())
        self.plot.enableAutoRange()
        self._showExaggeration()

    def _showExaggeration(self) -> None:
        if not self.profiles or self._unitsOf() != "m":
            self.exaggeration.setText("")
            return
        xPerPixel, yPerPixel = self.plot.getViewBox().viewPixelSize()
        if yPerPixel <= 0:
            return
        ratio = xPerPixel * self._scale / yPerPixel
        self.exaggeration.setText(f"Vertical exaggeration ≈ {ratio:.3g}×")

    def verticalExaggeration(self) -> float | None:
        if not self.profiles or self._unitsOf() != "m":
            return None
        xPerPixel, yPerPixel = self.plot.getViewBox().viewPixelSize()
        return xPerPixel * self._scale / yPerPixel if yPerPixel > 0 else None

    def _unitsOf(self) -> str:
        return next(iter(self.profiles.values())).distanceUnits if self.profiles else ""

    def _onMouseMoved(self, position) -> None:
        viewBox = self.plot.getViewBox()
        if not self.profiles or not viewBox.sceneBoundingRect().contains(position):
            self.cursorLine.setVisible(False)
            self.readout.setText("")
            self.sigCursor.emit(None)
            return
        distance = viewBox.mapSceneToView(position).x() * self._scale
        self.cursorLine.setPos(distance / self._scale)
        self.cursorLine.setVisible(True)
        parts, cursor = [], None
        for line in self.lines:
            result = self.profiles[line.fid]
            index = int(
                np.clip(
                    np.searchsorted(result.distance, distance),
                    0,
                    len(result.distance) - 1,
                )
            )
            value = result.values[index]
            if cursor is None:
                cursor = result.positions[index]
            text = "no data" if not np.isfinite(value) else f"{value:.4g}"
            parts.append(f"{line.name}: {text}")
        unit = "km" if self._scale == 1000.0 else self._unitsOf()
        self.readout.setText(
            f"{distance / self._scale:.4g} {unit}   " + "   ".join(parts)
        )
        self.sigCursor.emit(cursor)

    # --- export --------------------------------------------------------------

    def _export(self) -> None:
        if not self.profiles:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Profiles", "profiles.csv", EXPORT_FILTER
        )
        if not path:
            return
        try:
            self.exportTo(Path(path))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Export Profiles", str(error))

    def exportTo(self, path: Path) -> None:
        if path.suffix.lower() != ".csv":
            exportImage(self.plotItem, path)
            return
        georeferenced = self.image.hasGeospatialData
        units = self._unitsOf()
        band = int(self.bandBox.currentData() or 0)
        unit = valueUnit(self.image, band)
        with PROJ_LOCK:  # as every pyproj call, also on the GUI thread
            crs = CRS.from_user_input(self.image.crs) if georeferenced else None
            wkt = crs.to_wkt() if crs is not None else None
        header = [
            f"Varda {vardaVersion()} line profiles",
            f"image: {self.image.name}"
            + (f" ({self.image.filePath})" if self.image.filePath else ""),
            f"CRS: {crsLabel(crs)}",
            *([f"CRS WKT: {wkt}"] if wkt is not None else []),
            *(
                ["distance unit: px (the image has no georeferencing)"]
                if units == "px" or crs is None
                else self._distanceHeader(crs)
            ),
            f"values: {self.bandBox.currentText()}"
            + (f" ({unit})" if unit else " (unit not recorded in the image)"),
        ]
        with path.open("w", newline="") as f:
            f.writelines(f"# {line}\n" for line in header)
            out = csv.writer(f)
            header = ["line", f"distance_{units}", "x_px", "y_px"]
            if georeferenced:
                header += ["map_x", "map_y"]
            out.writerow([*header, self.bandBox.currentText()])
            for line in self.lines:
                result = self.profiles[line.fid]
                mapXY = (
                    self.image.imageToGeo(result.positions) if georeferenced else None
                )
                for i, (d, (x, y), v) in enumerate(
                    zip(result.distance, result.positions, result.values)
                ):
                    # repr: the shortest text that reads back as the same float
                    row = [
                        line.name,
                        repr(float(d)) if np.isfinite(d) else "",
                        f"{x:.3f}",
                        f"{y:.3f}",
                    ]
                    if mapXY is not None:
                        row += [repr(float(mapXY[i, 0])), repr(float(mapXY[i, 1]))]
                    out.writerow([*row, "" if not np.isfinite(v) else f"{v:.10g}"])

    def _distanceHeader(self, crs: CRS) -> list[str]:
        """How the distances were measured: geodesic on the CRS's surface,
        the body-model term, the projection's distortion along each line
        (not in the distances) and what the distances leave out."""
        with PROJ_LOCK:
            ellipsoid = crs.ellipsoid
            surface = surfaceText(ellipsoid) if ellipsoid is not None else "?"
        if ellipsoid is None:  # an engineering or local CRS, on no body
            return [
                f"distance unit: m ({PLANAR_METHOD})",
                "distance limits: on the map's plane, converted from the "
                "coordinate system's units; samples about 1 px apart; plus the "
                "image's georeferencing error, which Varda can't know",
            ]
        measurements = {
            line.fid: lineMeasurement(line.vertices, self.image.transform, crs)
            for line in self.lines
        }
        differences = [
            m.bodyModelDifference
            for m in measurements.values()
            if m is not None and m.reference is not None
        ]
        method = f"m (geodesic, Karney 2013, on {surface}"
        if differences:
            finite = [d for d in differences if np.isfinite(d)]
            amount = _percent(max(finite)) if finite else "an unknown amount"
            upTo = "up to " if len(self.lines) > 1 and finite else ""
            method += f"; IAU 2015 reference ellipsoid differs by {upTo}{amount}"
        lines = [f"distance unit: {method})"]
        for line in self.lines:
            measurement = measurements[line.fid]
            distortion = measurement.distortion if measurement else None
            if distortion is not None:
                where = "the line" if len(self.lines) == 1 else line.name
                lines.append(
                    f"projection distortion along {where}: {distortion.text} "
                    "(not in the distances)"
                )
        lines.append(
            "distance limits: horizontal, on the reference surface (not along "
            "topography); samples about 1 px apart; plus the image's "
            "georeferencing error, which Varda can't know"
        )
        if any(
            not np.isfinite(self.profiles[ln.fid].distance).all() for ln in self.lines
        ):
            lines.append(
                "distance gaps: samples that couldn't be converted to longitude "
                "and latitude, and all after them on their line, have no distance"
            )
        return lines


def _percent(fraction: float) -> str:
    """A relative difference as a percentage to 2 significant figures, never
    in scientific notation."""
    if fraction < 1e-5:
        return "< 0.001%"
    return f"{float(f'{fraction * 100:.2g}'):g}%"
