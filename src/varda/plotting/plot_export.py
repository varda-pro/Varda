"""Export a spectral plot: publication-quality images (PNG, SVG, PDF) or the
plotted curves as CSV."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters as exporters
from PyQt6.QtCore import QMarginsF, QRectF, QSizeF
from PyQt6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
from PyQt6.QtSvg import QSvgGenerator

from varda.common.spectral_axis import SpectralUnit

if TYPE_CHECKING:
    from varda.plotting.plot import Curve, VardaPlotWidget

EXPORT_FILTER = (
    "PNG image (*.png);;SVG vector image (*.svg);;PDF (*.pdf);;CSV data (*.csv)"
)
MIN_PNG_WIDTH = 2000  # px; about 300 dpi across a journal column and a half
PDF_DPI = 300
_UNIT_COLUMN = {
    SpectralUnit.NANOMETRE: "wavelength_nm",
    SpectralUnit.MICROMETRE: "wavelength_um",
    SpectralUnit.WAVENUMBER: "wavenumber_cm-1",
}


IMAGE_FILTER = "PNG image (*.png);;SVG vector image (*.svg);;PDF (*.pdf)"


def exportImage(plotItem: pg.PlotItem, path: str | Path) -> Path:
    """Write any pyqtgraph plot as a PNG, SVG or PDF; the suffix picks the
    format."""
    target = Path(path)
    writers = {".png": _png, ".svg": _svg, ".pdf": _pdf}
    writer = writers.get(target.suffix.lower())
    if writer is None:
        raise ValueError(f"Cannot export '{target.suffix}'; use png, svg or pdf.")
    writer(plotItem, target)
    return target


def exportPlot(widget: VardaPlotWidget, path: str | Path) -> Path:
    """Write the plot to ``path``; the suffix picks the format."""
    target = Path(path)
    if target.suffix.lower() == ".csv":
        _csv(widget, target)
        return target
    writers = {".png": _png, ".svg": _svg, ".pdf": _pdf}
    writer = writers.get(target.suffix.lower())
    if writer is None:
        raise ValueError(f"Cannot export '{target.suffix}'; use png, svg, pdf or csv.")
    writer(widget.plotItem, target)
    return target


def _png(plotItem: pg.PlotItem, target: Path) -> None:
    exporter = exporters.ImageExporter(plotItem)
    width = max(MIN_PNG_WIDTH, int(plotItem.sceneBoundingRect().width() * 3))
    exporter.parameters()["width"] = width
    exporter.export(str(target))


def _svg(plotItem: pg.PlotItem, target: Path) -> None:
    # Qt's own SVG writer: pyqtgraph's SVGExporter calls paint() on PlotDataItem
    # containers, which raises under PyQt6
    source = plotItem.sceneBoundingRect()
    generator = QSvgGenerator()
    generator.setFileName(str(target))
    generator.setSize(source.size().toSize())
    generator.setViewBox(QRectF(0, 0, source.width(), source.height()))
    generator.setTitle("Varda plot")
    _render(plotItem, generator, QRectF(0, 0, source.width(), source.height()))


def _render(plotItem: pg.PlotItem, device, targetRect: QRectF) -> None:
    painter = QPainter(device)
    try:
        scene = plotItem.scene()
        if scene is not None:
            scene.render(painter, targetRect, plotItem.sceneBoundingRect())
    finally:
        painter.end()


def _pdf(plotItem: pg.PlotItem, target: Path) -> None:
    source = plotItem.sceneBoundingRect()
    writer = QPdfWriter(str(target))
    writer.setResolution(PDF_DPI)
    # A page the shape of the plot, 6 inches wide
    widthInches = 6.0
    heightInches = widthInches * source.height() / max(source.width(), 1.0)
    pageSize = QPageSize(QSizeF(widthInches, heightInches), QPageSize.Unit.Inch)
    writer.setPageLayout(
        QPageLayout(pageSize, QPageLayout.Orientation.Portrait, QMarginsF())
    )
    _render(plotItem, writer, QRectF(0, 0, writer.width(), writer.height()))


def _csv(widget: VardaPlotWidget, target: Path) -> None:
    """The curves' values as plotted, continuum removed if they're shown so
    (with each sample's continuum, to get the values back), but without any
    display Y scale/offset (comment lines record both); for mean spectra
    also the standard deviation and pixel count n behind each mean."""
    names = [
        c.plotDataItem.name() or f"curve {i + 1}" for i, c in enumerate(widget.plots)
    ]
    curves = [(name, c, *c.shownData()) for name, c in zip(names, widget.plots)]
    xColumn = _UNIT_COLUMN.get(widget.spectralUnit, "x") if widget.spectralUnit else "x"
    removed = widget.continuumConfig.enabled.value
    with target.open("w", newline="") as f:
        if removed:
            window = widget.continuumWindow()
            over = (
                "each whole curve"
                if window is None
                else (
                    f"the samples from {min(window):g} to {max(window):g} ({xColumn})"
                )
            )
            f.write(
                "# Continuum removed: values are each curve divided by its "
                f"continuum, the upper convex hull of {over}; 'continuum' "
                "columns give it, and std is divided by it too\n"
            )
        transformed = [(n, c) for n, c, _, _ in curves if c.isTransformed()]
        if transformed:
            f.write(
                "# Values are the curves' own data. The plot showed these "
                "curves as y * scale + offset:\n"
            )
            for name, c in transformed:
                f.write(
                    f"# {name}: scale={c.config.scale.value:.10g}, "
                    f"offset={c.config.offset.value:.10g}\n"
                )
        out = csv.writer(f)
        sharedX = bool(curves) and all(
            len(x) == len(curves[0][2]) and np.allclose(x, curves[0][2], equal_nan=True)
            for _, _, x, _ in curves
        )
        if sharedX:
            columns = [
                col for name, c, _, y in curves for col in _columns(name, c, y, removed)
            ]
            out.writerow([xColumn, *[header for header, _ in columns]])
            for row in zip(curves[0][2], *[values for _, values in columns]):
                out.writerow([f"{v:.10g}" for v in row])
        else:
            spread = any(
                c.std is not None or c.count is not None for _, c, _, _ in curves
            )
            out.writerow(
                [
                    "curve",
                    "x",
                    "y",
                    *(["continuum"] if removed else []),
                    *(["std", "n"] if spread else []),
                ]
            )
            for name, c, x, y in curves:
                std, count = c.shownSpread()
                for i, (xv, yv) in enumerate(zip(x, y)):
                    extra = [_cell(c.continuum, i)] if removed else []
                    if spread:
                        extra += [_cell(std, i), _cell(count, i)]
                    out.writerow([name, f"{xv:.10g}", f"{yv:.10g}", *extra])


def _columns(
    name: str, curve: Curve, y: np.ndarray, removed: bool = False
) -> list[tuple[str, np.ndarray]]:
    columns = [(name, y)]
    if removed and curve.continuum is not None:
        columns.append((f"{name} continuum", curve.continuum))
    std, count = curve.shownSpread()
    if std is not None:
        columns.append((f"{name} std", std))
    if count is not None:
        columns.append((f"{name} n", count))
    return columns


def _cell(values: np.ndarray | None, i: int) -> str:
    return "" if values is None else f"{values[i]:.10g}"
