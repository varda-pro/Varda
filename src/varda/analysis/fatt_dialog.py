"""The Factor Analysis Target Transformation window: analyze an image's
spectra, see which library spectra its factor space reproduces, and map
where the chosen ones fit."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from varda.analysis.eigenvalue_plot import EigenvaluePlot
from varda.analysis.fatt import (
    FactorAnalysis,
    FactorMethod,
    TargetFit,
    analyzeFactors,
    rankTargets,
    resampleLibrary,
    sampleSpectra,
    writeRanking,
)
from varda.analysis.fatt_map import (
    THRESHOLD_UNIT,
    FattMapAnalysis,
    bandsInRange,
    defaultRange,
)
from varda.analysis.library_picker import LibraryPicker
from varda.analysis.rasters import readCube
from varda.analysis.spectral_library import LibrarySpectrum
from varda.common.entities import VardaRaster
from varda.common.parameter import (
    EnumParameter,
    FloatParameter,
    ImageParameter,
    IntParameter,
)
from varda.common.parameter_widgets import parameterWidget
from varda.common.ui import (
    ButtonBuilder,
    HBoxBuilder,
    MutedLabel,
    SectionBox,
    VBoxBuilder,
)
from varda.rois.roi_collection import ROICollection
from varda.utilities.background import runInBackground
from varda.version import vardaVersion

WHOLE_IMAGE = "Whole image"
_COLUMNS = ["Map", "Target", "nRMSE (×10⁻⁴)", "Angle (°)"]
_PASS_COLOR = QColor("#2e7d32")
TARGET_PEN = pg.mkPen("#e53935", width=2)
FIT_PEN = pg.mkPen("#1e88e5", width=2, style=Qt.PenStyle.DashLine)


def _ordinal(n: int) -> str:
    """1 -> "1st", 2 -> "2nd", 3 -> "3rd", 11 -> "11th"."""
    suffix = (
        "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    )
    return f"{n}{suffix}"


class FattDialog(QDialog):
    sigMapRequested = pyqtSignal(object, object)  # (image, FattMapAnalysis)

    def __init__(
        self,
        images: Sequence[VardaRaster],
        *,
        rois: ROICollection | None = None,
        image: VardaRaster | None = None,
        library: list[LibrarySpectrum] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """``library``: offer only this library (default: the bundled ones and
        Load Library…)."""
        super().__init__(parent)
        self.setWindowTitle("Factor Analysis Target Transformation")
        self.resize(1100, 720)
        self.rois = rois
        self.factors: FactorAnalysis | None = None
        self._analyzed: tuple[VardaRaster, np.ndarray] | None = None  # image, bands
        self._targets: tuple[list[LibrarySpectrum], np.ndarray] | None = None
        self._fits: list[TargetFit] = []
        self._roiClass: str | None = None  # the analyzed ROI class, if any
        self._analysisSettings: dict[str, object] = {}  # for the CSV export

        # --- settings ---
        self.imageParam = ImageParameter("Image", "The image to analyze")
        self.imageParam.setProvider(lambda: list(images))
        if image is not None:
            self.imageParam.set(image)
        self.source = QComboBox()
        self.source.setToolTip(
            "The spectra to analyze: a subsample of the whole image, or the "
            "pixels of an ROI class"
        )
        self.stride = IntParameter(
            "Every Nth pixel",
            3,
            range=(1, 50),
            description="Whole image: use every Nth pixel of every Nth row",
        )
        self.minWavelength = FloatParameter(
            "From", 1700.0, range=(0.0, 100000.0), units="nm", showSlider=False
        )
        self.maxWavelength = FloatParameter(
            "To", 2600.0, range=(0.0, 100000.0), units="nm", showSlider=False
        )
        self.method = EnumParameter(
            "Decomposition", FactorMethod, FactorMethod.COVARIANCE
        )
        self.factorCount = IntParameter(
            "Factors",
            5,
            range=(1, 100),
            description="The dimension of the factor space (drag the cutoff)",
        )
        self.threshold = FloatParameter(
            "Threshold",
            FattMapAnalysis.threshold.default,
            range=(0.001, 1000.0),
            units="× 10⁻⁴ nRMSE",
            decimals=3,
            showSlider=False,
            description="Targets below this fit (1.5 for CRISM at 1.7–2.6 µm)",
        )
        self.analyzeButton = ButtonBuilder("Analyze").onClick(self.analyze)
        self.statusLabel = MutedLabel("")
        self.statusLabel.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Image", parameterWidget(self.imageParam))
        form.addRow("Spectra from", self.source)
        self.strideWidget = parameterWidget(self.stride)
        form.addRow("Every Nth pixel", self.strideWidget)
        form.addRow(
            "Wavelengths",
            HBoxBuilder()
            .withWidget(parameterWidget(self.minWavelength))
            .withWidget(QLabel("–"))
            .withWidget(parameterWidget(self.maxWavelength)),
        )
        form.addRow("Decomposition", parameterWidget(self.method))

        # --- factors ---
        self.factorBox = SectionBox("Indicator function")
        self.factorBox.setContent(
            MutedLabel("Press Analyze to find the image's factors.")
        )
        factors = QWidget()
        factors.setLayout(
            VBoxBuilder(margins=0)
            .withLayout(form)
            .withLayout(HBoxBuilder().withWidget(self.analyzeButton).withStretch())
            .withWidget(self.statusLabel)
            .withWidget(self.factorBox, stretch=1)
            .withLayout(
                HBoxBuilder()
                .withWidget(QLabel("Factors"))
                .withWidget(parameterWidget(self.factorCount))
            )
        )

        # --- targets ---
        self.picker = LibraryPicker(library)
        self.picker.sigLibraryChanged.connect(self._libraryChanged)
        self.filterEdit = QLineEdit()
        self.filterEdit.setPlaceholderText("Filter targets by name")
        self.filterEdit.setClearButtonEnabled(True)
        self.filterEdit.textChanged.connect(lambda _: self._applyFilter())
        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        vertical, header = self.table.verticalHeader(), self.table.horizontalHeader()
        assert vertical is not None and header is not None
        vertical.setVisible(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.currentCellChanged.connect(lambda row, *_: self._showFit(row))
        self.table.itemChanged.connect(lambda _: self._updateMapButton())
        self.fitPlot = pg.PlotWidget()
        self.fitPlot.setMinimumHeight(200)
        self.fitPlot.setLabel("bottom", "Wavelength (nm)")
        self.fitPlot.addLegend(offset=(10, 10))
        self.fitPlot.getPlotItem().setMenuEnabled(False)
        targets = QWidget()
        targets.setLayout(
            VBoxBuilder(margins=0)
            .withWidget(self.picker)
            .withWidget(self.filterEdit)
            .withWidget(self.table, stretch=3)
            .withWidget(self.fitPlot, stretch=2)
        )

        splitter = QSplitter()
        splitter.addWidget(factors)
        splitter.addWidget(targets)
        splitter.setSizes([420, 680])

        self.mapButton = ButtonBuilder("Map Ticked Targets…").onClick(self._requestMap)
        self.mapButton.setToolTip(
            "Map where the ticked targets fit, in small moving windows (DAFA/TT)"
        )
        self.exportButton = ButtonBuilder("Export Table…").onClick(self.exportRanking)
        self.exportButton.setToolTip(
            "Save the ranked targets (nRMSE and angle) as CSV, with the settings"
        )
        self.exportButton.setEnabled(False)
        note = MutedLabel(
            "FATT finds candidates, not detections: confirm a target, e.g. with "
            "ratioed spectra, before reporting it."
        )
        note.setWordWrap(True)
        self.setLayout(
            VBoxBuilder()
            .withWidget(splitter, stretch=1)
            .withWidget(note)
            .withLayout(
                HBoxBuilder()
                .withWidget(QLabel("Threshold"))
                .withWidget(parameterWidget(self.threshold))
                .withStretch()
                .withWidget(self.exportButton)
                .withWidget(self.mapButton)
                .withWidget(ButtonBuilder("Close").onClick(self.close))
            )
        )

        self.imageParam.sigParameterChanged.connect(lambda _: self._imageChanged())
        self.factorCount.sigParameterChanged.connect(lambda _: self._rank())
        self.threshold.sigParameterChanged.connect(lambda _: self._colorRows())
        self.source.currentIndexChanged.connect(lambda _: self._updateSourceState())
        self._imageChanged()

    # --- settings ---------------------------------------------------------------------

    def selectedImage(self) -> VardaRaster | None:
        return self.imageParam.get()

    def _imageChanged(self) -> None:
        image = self.selectedImage()
        with QSignalBlocker(self.source):
            self.source.clear()
            self.source.addItem(WHOLE_IMAGE)
            if self.rois is not None:
                for name in self.rois.classes():
                    self.source.addItem(f"ROI class: {name}", name)
        if image is not None:
            low, high = defaultRange(image)
            self.minWavelength.set(low)
            self.maxWavelength.set(high)
            self.picker.selectDefaultFor(image.name)
        self._updateSourceState()

    def _updateSourceState(self) -> None:
        self.strideWidget.setEnabled(self.source.currentData() is None)

    def _spectra(self, image: VardaRaster, bands: np.ndarray) -> np.ndarray:
        """The spectra to analyze, at ``bands`` (runs in the background)."""
        cube, valid = readCube(image)
        roiClass = self._roiClass
        if roiClass is None:
            return sampleSpectra(cube[:, :, bands], valid, int(self.stride.get()))
        assert self.rois is not None
        return sampleSpectra(
            cube[:, :, bands], valid & self.rois.classMask(roiClass, image)
        )

    # --- analysis ---------------------------------------------------------------------

    def analyze(self) -> None:
        image = self.selectedImage()
        if image is None:
            return
        try:
            bands, _ = bandsInRange(
                image, self.minWavelength.get(), self.maxWavelength.get()
            )
        except ValueError as error:
            self.statusLabel.setText(str(error))
            return
        method = FactorMethod(self.method.get())
        self._roiClass = self.source.currentData()
        stride = int(self.stride.get())
        self._analysisSettings = {
            "Image": f"{image.name} ({image.filePath or 'held in memory'})",
            "Spectra": (
                f"ROI class {self._roiClass}"
                if self._roiClass is not None
                else "whole image, every pixel"
                if stride == 1
                else f"whole image, every {_ordinal(stride)} pixel of every "
                f"{_ordinal(stride)} row"
            ),
            "Wavelengths": (
                f"{self.minWavelength.get():g}–{self.maxWavelength.get():g} nm "
                f"({len(bands)} bands)"
            ),
            "Decomposition": method.value,
        }
        self.analyzeButton.setEnabled(False)
        self.statusLabel.setText(f"Analyzing {image.name}…")

        def task() -> tuple[FactorAnalysis, int]:
            spectra = self._spectra(image, bands)
            return analyzeFactors(spectra, method), len(spectra)

        runInBackground(
            task,
            lambda result: self._showFactors(image, bands, *result),
            self._analysisFailed,
            description="Factor analysis",
        )

    def _analysisFailed(self, error: Exception) -> None:
        self.analyzeButton.setEnabled(True)
        self.statusLabel.setText(f"Factor analysis failed: {error}")

    def _showFactors(
        self, image: VardaRaster, bands: np.ndarray, factors: FactorAnalysis, count: int
    ) -> None:
        self.analyzeButton.setEnabled(True)
        self.factors = factors
        self._analyzed = (image, bands)
        self._analysisSettings["Spectra"] = (
            f"{self._analysisSettings['Spectra']}: {count} spectra"
        )
        self._targets = None
        suggestion = (
            "Hysime's signal subspace"
            if factors.method is FactorMethod.HYSIME
            else "the indicator function's minimum"
        )
        self.statusLabel.setText(
            f"{count} spectra, {len(bands)} bands. {factors.suggested} factors "
            f"suggested by {suggestion}; 10–15 is typical, and more fit noise."
        )
        self.factorCount.setRange((1, factors.maxFactors), value=factors.suggested)
        plot = EigenvaluePlot(factors.indicator, self.factorCount, logScale=True)
        plot.setLabel("bottom", "Factors")
        plot.setLabel("left", "IND")
        best = int(np.argmin(factors.indicator))
        plot.plot(
            [best + 1],
            [max(factors.indicator[best], 1e-300)],
            pen=None,
            symbol="o",
            symbolSize=9,
            symbolBrush="#ffff00",
        )
        self.factorBox.setContent(plot)
        self._rank()

    def _libraryChanged(self) -> None:
        self._targets = None
        self._rank()

    def _rank(self) -> None:
        if self.factors is None or self._analyzed is None:
            return
        image, bands = self._analyzed
        if self._targets is None:
            model = image.bandModel
            assert model.centers is not None
            fwhm = None if model.fwhm is None else model.fwhm[bands]
            self._targets = resampleLibrary(
                self.picker.library(), model.centers[bands], fwhm
            )
        ticked = set(self.tickedNames())
        basis = self.factors.basis(int(self.factorCount.get()))
        self._fits = rankTargets(basis, *self._targets)
        with QSignalBlocker(self.table):
            self.table.setRowCount(len(self._fits))
            for row, fit in enumerate(self._fits):
                use = QTableWidgetItem()
                use.setFlags(use.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                use.setCheckState(
                    Qt.CheckState.Checked
                    if fit.spectrum.name in ticked
                    else Qt.CheckState.Unchecked
                )
                self.table.setItem(row, 0, use)
                cells = (
                    fit.spectrum.name,
                    f"{fit.nrmse / THRESHOLD_UNIT:.3f}",
                    f"{np.degrees(fit.angle):.2f}",
                )
                for column, text in enumerate(cells, start=1):
                    self.table.setItem(row, column, QTableWidgetItem(text))
        self._colorRows()
        self._applyFilter()
        self._updateMapButton()
        self.exportButton.setEnabled(bool(self._fits))
        if self._fits:
            self.table.selectRow(0)
            self._showFit(0)

    def rankingSettings(self) -> dict[str, object]:
        """What the current ranking was made with, for its export."""
        return {
            **self._analysisSettings,
            "Factors": int(self.factorCount.get()),
            "Library": self.picker.libraryName() or "given",
            "Threshold": f"{self.threshold.get():g} × 10⁻⁴ nRMSE",
            "Made with": f"Varda {vardaVersion()}",
            "Exported": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def saveRanking(self, path: str | Path) -> None:
        writeRanking(
            path,
            self._fits,
            self.rankingSettings(),
            ticked=self.tickedNames(),
            threshold=float(self.threshold.get()) * THRESHOLD_UNIT,
        )

    def exportRanking(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export FATT Ranking", "fatt_ranking.csv", "CSV (*.csv)"
        )
        if path:
            self.saveRanking(path)

    def _colorRows(self) -> None:
        threshold = float(self.threshold.get()) * THRESHOLD_UNIT
        with QSignalBlocker(self.table):
            for row, fit in enumerate(self._fits):
                brush = QBrush(
                    _PASS_COLOR if fit.nrmse < threshold else QColor(0, 0, 0, 0)
                )
                item = self.table.item(row, 2)
                if item is not None:
                    item.setBackground(brush)

    def _applyFilter(self) -> None:
        text = self.filterEdit.text().strip().lower()
        for row, fit in enumerate(self._fits):
            self.table.setRowHidden(
                row, bool(text) and text not in fit.spectrum.name.lower()
            )

    def _showFit(self, row: int) -> None:
        self.fitPlot.clear()
        if not 0 <= row < len(self._fits) or self._analyzed is None:
            return
        image, bands = self._analyzed
        centers = np.asarray(image.bandModel.centers)[bands]
        fit = self._fits[row]
        self.fitPlot.setTitle(fit.spectrum.name)
        self.fitPlot.plot(
            centers, fit.target, pen=TARGET_PEN, name="Library", connect="finite"
        )
        self.fitPlot.plot(centers, fit.fit, pen=FIT_PEN, name="Target transformation")
        # A zoom or pan turns auto-range off; each mineral starts fitted again.
        # pyqtgraph applies auto-range when it next paints, so apply it now.
        self.fitPlot.enableAutoRange()
        self.fitPlot.getPlotItem().getViewBox().updateAutoRange()

    # --- mapping ----------------------------------------------------------------------

    def tickedNames(self) -> list[str]:
        names = []
        for row, fit in enumerate(self._fits):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                names.append(fit.spectrum.name)
        return names

    def _updateMapButton(self) -> None:
        count = len(self.tickedNames())
        self.mapButton.setEnabled(count > 0)
        self.mapButton.setText(
            f"Map {count} Ticked Target{'s' if count != 1 else ''}…"
            if count
            else "Map Ticked Targets…"
        )

    def mapAnalysis(self) -> FattMapAnalysis:
        """A FATT detection map of the ticked targets with these settings."""
        ticked = set(self.tickedNames())
        analysis = FattMapAnalysis(
            [fit.spectrum for fit in self._fits if fit.spectrum.name in ticked]
        )
        analysis.libraryName = self.picker.libraryName()
        analysis.minWavelength.set(self.minWavelength.get())
        analysis.maxWavelength.set(self.maxWavelength.get())
        analysis.threshold.set(self.threshold.get())
        return analysis

    def _requestMap(self) -> None:
        if self._analyzed is not None:
            self.sigMapRequested.emit(self._analyzed[0], self.mapAnalysis())
