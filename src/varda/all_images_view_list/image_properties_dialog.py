"""Image Properties: what an image is (file, format, size, map projection,
with what the coordinate-system check found and the fixes for it; for a
class map, each class's pixels and ground area) and the
metadata Varda reads from it, with the parts that matter for
analysis editable: the name, the no-data value, the wavelengths and their
unit, band names, FWHM, which bands are good, and the description.

Edits take effect in Varda at once (and are kept in sessions and
packages); for an ENVI file they can also be saved into its header. Only
values that differ from the file's become edits."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from pyproj import CRS
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontDatabase, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from varda.all_images_view_list.set_crs_dialog import SetCrsDialog
from varda.analysis.class_tools import (
    ClassAreas,
    classAreas,
    classAreasCsv,
    isClassMap,
)
from varda.common.entities import VardaRaster
from varda.common.provenance import CRS_FIX_KEY
from varda.common.spectral_axis import BBL_KEY, FWHM_KEY, parseEnviList
from varda.common.ui import FlowLayout, MutedLabel, PanelScrollArea
from varda.geo.describe import describeCrs, formatMeters
from varda.geo.health import SET_CRS, CrsFix, CrsIssue
from varda.image_loading.crs_check import (
    applyCrsFix,
    checkImageCrs,
    chooseCrs,
    undoCrsFix,
)
from varda.image_loading.data_sources.fits_data_source import FITS_HEADER_KEY
from varda.image_loading.envi_header import headerFor, saveToHeader
from varda.utilities.background import runInBackground
from varda.utilities.proj_lock import PROJ_LOCK

UNITS = ["nm", "µm", "cm⁻¹", "Unknown"]
NAME, WAVELENGTH, FWHM, GOOD = 1, 2, 3, 4  # table columns (0: the band number)
CLASS_COLUMNS = ["Class", "Name", "Pixels", "Area (km²)", "%"]
COMPUTING = "Computing…"
CLASS_ROWS_SHOWN = 8  # more classes scroll inside the table


def parseNoData(text: str) -> float | None:
    """A typed no-data value: a number, ``nan``, or nothing for none."""
    text = text.strip()
    if not text or text.lower() == "none":
        return None
    return float(text)  # ValueError for anything else; "nan" parses


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return a == b or (math.isnan(a) and math.isnan(b))


def _listOf(metadata: dict, key: str, count: int) -> list[float] | None:
    try:
        values = parseEnviList(metadata.get(key))
    except (TypeError, ValueError):
        return None
    return values if values is not None and len(values) == count else None


def wrapped(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def authorityText(crs: CRS, authority: str | None) -> str:
    """The CRS's own authority code, or "matches <code>" for one PROJ finds
    to be the same CRS under another name, or "none"."""
    if authority is None:
        return "none"
    with PROJ_LOCK:
        own = crs.to_authority(min_confidence=100)
    if own is not None and ":".join(own) == authority:
        return authority
    return f"matches {authority}"


def selectable(widget: QWidget) -> None:
    for label in widget.findChildren(QLabel):
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)


class CrsIssueRow(QWidget):
    """One finding of the coordinate-system check: what, what to do, and
    **Apply** for a fix offered, or a note and **Undo** for one in use."""

    sigApply = pyqtSignal(object)  # the CrsFix
    sigUndo = pyqtSignal()

    def __init__(
        self, issue: CrsIssue, chosen: bool, parent: QWidget | None = None
    ) -> None:
        """``chosen``: the fix in use was applied by the user (not at load)."""
        super().__init__(parent)
        self.fix: CrsFix | None = issue.fix
        self.button: QPushButton | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        fixed = issue.fix is not None and issue.fix.automatic  # resolved
        severity = "Fixed" if fixed else issue.severity.value.title()
        layout.addWidget(wrapped(f"{severity}: {issue.message}"))
        suggestion = MutedLabel(issue.suggestion)
        suggestion.setWordWrap(True)
        layout.addWidget(suggestion)
        if issue.fix is None:
            return
        row = FlowLayout()
        if issue.fix.automatic:
            note = wrapped(
                "You applied this fix."
                if chosen
                else "Varda applied this fix when the image opened."
            )
            note.setToolTip(issue.fix.reason)  # the message says the rest
            layout.addWidget(note)
            self.button = QPushButton("Undo")
            self.button.setToolTip(
                "Use the file's own coordinate system and pixel grid"
            )
            self.button.clicked.connect(self.sigUndo)
        else:
            self.button = QPushButton("Apply")
            self.button.setToolTip(issue.fix.reason)
            self.button.clicked.connect(self._onApply)
        row.addWidget(self.button)
        layout.addLayout(row)

    def _onApply(self) -> None:
        self.sigApply.emit(self.fix)


class ImagePropertiesDialog(QDialog):
    def __init__(
        self,
        image: VardaRaster,
        parent: QWidget | None = None,
        images: Sequence[VardaRaster] = (),
    ):
        """``images``: the open images, offered by Set Coordinate System."""
        super().__init__(parent)
        self.image = image
        self.images = images
        self.setCrsDialog: SetCrsDialog | None = None
        # the Coordinate system group's controls, made by _coordinateSystem
        self.applyButtons: list[QPushButton] = []
        self.undoButton: QPushButton | None = None
        self.setCrsButton = QPushButton()
        self.wktToggle = QPushButton()
        self.wktEdit = QPlainTextEdit()
        self.setWindowTitle(f"Properties of {image.name}")
        source = image.dataSource
        about = QFormLayout()
        about.addRow("File:", QLabel(image.filePath or "held in memory (not saved)"))
        about.addRow("Format:", QLabel(image.driver or "–"))
        about.addRow(
            "Size:",
            QLabel(
                f"{image.width} × {image.height} pixels, {image.bandCount} bands, "
                f"{np.dtype(image.dtype).name}"
            ),
        )
        aboutBox = QGroupBox("Image")
        aboutBox.setLayout(about)
        selectable(aboutBox)
        self.crsBox = QGroupBox("Coordinate system")
        self._crsLayout = QVBoxLayout(self.crsBox)
        self._crsContent: QWidget | None = None
        # the Classes group (class maps only), filled by measureClasses
        self.classesBox: QGroupBox | None = None
        self.classTable = QTableWidget(0, len(CLASS_COLUMNS))
        self.classMethod = MutedLabel("")
        self.classTotals = QLabel("")
        self.copyCsvButton = QPushButton("Copy as CSV")
        self.exportCsvButton = QPushButton("Export CSV…")
        self.classAreas: ClassAreas | None = None
        self._classRun = 0  # the latest measurement; older results are dropped
        if isClassMap(image):
            self.classesBox = self._classesGroup()
        self.refreshCoordinateSystem()

        self.nameEdit = QLineEdit(image.name)
        self.noDataEdit = QLineEdit("" if image.nodata is None else f"{image.nodata:g}")
        self.noDataEdit.setPlaceholderText("none")
        self.noDataEdit.setToolTip(
            "The value that marks pixels without data (fill values, e.g. -999 or "
            "65535); nan for NaN; empty for none"
        )
        self.unitsBox = QComboBox()
        self.unitsBox.setEditable(True)
        self.unitsBox.addItems(UNITS)
        self.unitsBox.setCurrentText(image.wavelengthUnits or "Unknown")
        self.descriptionEdit = QPlainTextEdit(image.description)
        self.descriptionEdit.setMaximumHeight(60)
        edits = QFormLayout()
        edits.addRow("Name:", self.nameEdit)
        edits.addRow("No-data value:", self.noDataEdit)
        edits.addRow("Wavelength unit:", self.unitsBox)
        edits.addRow("Description:", self.descriptionEdit)

        # one row per band
        count = image.bandCount
        wavelengths = (
            [f"{w:g}" for w in np.asarray(image.wavelengths, dtype=float)]
            if image.wavelengthsType is not str
            else [""] * count
        )
        fwhm = _listOf(image.extraMetadata, FWHM_KEY, count)
        good = _listOf(image.extraMetadata, BBL_KEY, count)
        self.table = QTableWidget(count, 5)
        self.table.setHorizontalHeaderLabels(
            ["Band", "Name", "Wavelength", "FWHM", "Good"]
        )
        rowHeader = self.table.verticalHeader()
        assert rowHeader is not None
        rowHeader.setVisible(False)
        names = image.bandNames
        for row in range(count):
            number = QTableWidgetItem(str(row + 1))
            number.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, number)
            self.table.setItem(
                row, NAME, QTableWidgetItem(names[row] if row < len(names) else "")
            )
            self.table.setItem(row, WAVELENGTH, QTableWidgetItem(wavelengths[row]))
            self.table.setItem(
                row, FWHM, QTableWidgetItem("" if fwhm is None else f"{fwhm[row]:g}")
            )
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(
                Qt.CheckState.Unchecked
                if good is not None and not good[row]
                else Qt.CheckState.Checked
            )
            self.table.setItem(row, GOOD, check)
        columnHeader = self.table.horizontalHeader()
        assert columnHeader is not None
        columnHeader.setSectionResizeMode(NAME, QHeaderView.ResizeMode.Stretch)
        # filling the wavelengths of many bands at once
        self.fillStart, self.fillStep = QDoubleSpinBox(), QDoubleSpinBox()
        for box in (self.fillStart, self.fillStep):
            box.setRange(-1e9, 1e9)
            box.setDecimals(4)
        self.fillStep.setValue(10.0)
        fill = QPushButton("Fill Wavelengths")
        fill.setToolTip("Band 1 at the start, each next band one step on")
        fill.clicked.connect(self.fillWavelengths)
        fillRow = QHBoxLayout()
        fillRow.addWidget(QLabel("Start:"))
        fillRow.addWidget(self.fillStart)
        fillRow.addWidget(QLabel("Step:"))
        fillRow.addWidget(self.fillStep)
        fillRow.addWidget(fill)
        fillRow.addStretch()
        bands = QGroupBox("Bands")
        bandsLayout = QVBoxLayout(bands)
        bandsLayout.addWidget(self.table)
        bandsLayout.addLayout(fillRow)

        header = (
            headerFor(image.filePath)
            if image.filePath and image.driver == "ENVI"
            else None
        )
        self.saveToFile = QCheckBox(
            "Also save into the ENVI header (backed up first)"
            if header is not None
            else "Saving into the file is for ENVI images; this keeps the edits in Varda and in sessions"
        )
        self.saveToFile.setEnabled(header is not None)
        reset = QPushButton("Reset to the File's")
        reset.setToolTip("Drop the edits made in Varda and show the file's metadata")
        reset.clicked.connect(self.resetToFile)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        panel = QWidget()
        panelLayout = QVBoxLayout(panel)
        panelLayout.setContentsMargins(0, 0, 0, 0)
        panelLayout.addWidget(aboutBox)
        panelLayout.addWidget(self.crsBox)
        if self.classesBox is not None:
            panelLayout.addWidget(self.classesBox)
        panelLayout.addLayout(edits)
        panelLayout.addWidget(bands, 1)
        fitsHeader = image.extraMetadata.get(FITS_HEADER_KEY)
        if isinstance(fitsHeader, str):
            panelLayout.addWidget(self._fitsHeaderBox(fitsHeader))
        panelLayout.addWidget(self.saveToFile)
        panelLayout.addWidget(
            MutedLabel(
                "Edits apply in Varda at once and are kept in sessions and packages."
            )
        )
        layout = QVBoxLayout(self)
        layout.addWidget(PanelScrollArea(panel), 1)  # scrolls on a short screen
        layout.addWidget(buttons)
        self.resize(640, 720)
        self._source = source

    @staticmethod
    def _fitsHeaderBox(text: str) -> QGroupBox:
        """The file's primary FITS header, read-only, one card per line."""
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        view.setMinimumHeight(120)
        view.setMaximumHeight(220)
        box = QGroupBox("FITS header")
        QVBoxLayout(box).addWidget(view)
        return box

    def refreshCoordinateSystem(self) -> None:
        """(Re)build the Coordinate system group for the image's CRS now."""
        if self._crsContent is not None:
            self._crsContent.setParent(None)
            self._crsContent.deleteLater()
        self._crsContent = self._coordinateSystem(self.image)
        selectable(self._crsContent)
        self._crsLayout.addWidget(self._crsContent)
        if self.classesBox is not None:  # the areas depend on the CRS
            self.measureClasses()

    def _classesGroup(self) -> QGroupBox:
        """The Classes group: a row per class (pixels, ground area, share of
        the classified pixels), the totals, how the areas were measured, and
        Copy as CSV / Export CSV…."""
        table = self.classTable
        table.setHorizontalHeaderLabels(CLASS_COLUMNS)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        rowHeader = table.verticalHeader()
        assert rowHeader is not None
        rowHeader.setVisible(False)
        columnHeader = table.horizontalHeader()
        assert columnHeader is not None
        columnHeader.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        columnHeader.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for label in (self.classMethod, self.classTotals):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copyCsvButton.setToolTip(
            "Copy the table, with the method and totals, as CSV"
        )
        self.copyCsvButton.clicked.connect(self.copyCsv)
        self.exportCsvButton.setToolTip("Save the table, with the method, as CSV")
        self.exportCsvButton.clicked.connect(self.exportCsv)
        box = QGroupBox("Classes")
        layout = QVBoxLayout(box)
        layout.addWidget(table)
        layout.addWidget(self.classTotals)
        layout.addWidget(self.classMethod)
        layout.addLayout(
            FlowLayout().withWidget(self.copyCsvButton).withWidget(self.exportCsvButton)
        )
        return box

    def measureClasses(self) -> None:
        """Measure each class's area in the background (the table says
        "Computing…" until then)."""
        self._classRun += 1
        run, image = self._classRun, self.image
        self.classAreas = None
        table = self.classTable
        table.clearSpans()
        table.setRowCount(1)
        table.setItem(0, 0, QTableWidgetItem(COMPUTING))
        table.setSpan(0, 0, 1, len(CLASS_COLUMNS))
        self._fitClassTable()
        self.classTotals.setText("")
        self.classMethod.setText("")
        self._enableCsv(False)

        def measure() -> tuple[int, ClassAreas]:
            return run, classAreas(image)

        runInBackground(
            measure,
            self._showClassAreas,
            self._classAreasFailed,
            description="Measuring class areas",
            owner=self,
        )

    def _enableCsv(self, enabled: bool) -> None:
        self.copyCsvButton.setEnabled(enabled)
        self.exportCsvButton.setEnabled(enabled)

    def _showClassAreas(self, result: tuple[int, ClassAreas]) -> None:
        run, areas = result
        if run != self._classRun:  # the CRS changed meanwhile
            return
        table = self.classTable
        table.clearSpans()
        table.setRowCount(len(areas.rows))
        for r, row in enumerate(areas.rows):
            values = [
                str(row.index),
                row.name,
                f"{row.pixels:,}",
                "–" if math.isnan(row.areaKm2) else f"{row.areaKm2:,.6g}",
                f"{100 * row.fraction:.2f}",
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column != 1:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                table.setItem(r, column, item)
        self._fitClassTable()
        area = "" if math.isnan(areas.totalKm2) else f", {areas.totalKm2:,.6g} km²"
        self.classTotals.setText(
            f"{areas.classifiedPixels:,} classified pixels{area} "
            f"({areas.unclassifiedPixels:,} unclassified). "
            "% is of the classified pixels; each pixel counts whole."
        )
        self.classMethod.setText(areas.method)
        self.classAreas = areas
        self._enableCsv(True)

    def _fitClassTable(self) -> None:
        """As tall as its rows, up to CLASS_ROWS_SHOWN (and a horizontal
        scroll bar's height, in case the columns don't fit)."""
        table = self.classTable
        header, rows = table.horizontalHeader(), table.verticalHeader()
        bar = table.horizontalScrollBar()
        assert header is not None and rows is not None and bar is not None
        shown = max(1, min(table.rowCount(), CLASS_ROWS_SHOWN))
        table.setFixedHeight(
            header.sizeHint().height()
            + shown * rows.defaultSectionSize()
            + bar.sizeHint().height()
            + 2 * table.frameWidth()
        )

    def _classAreasFailed(self, error: Exception) -> None:
        self.classTable.clearSpans()
        self.classTable.setRowCount(0)
        self.classMethod.setText(f"The classes couldn't be read: {error}")

    def _csv(self) -> str:
        assert self.classAreas is not None
        return classAreasCsv(self.classAreas, self.image.name)

    def copyCsv(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None and self.classAreas is not None:
            clipboard.setText(self._csv())

    def exportCsv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Class Areas",
            f"{self.image.name} class areas.csv",
            "CSV files (*.csv)",
        )
        if not path:
            return
        try:
            self.writeCsv(path)
        except OSError as error:
            QMessageBox.warning(
                self, self.windowTitle(), f"The CSV couldn't be saved: {error}"
            )

    def writeCsv(self, path: str) -> None:
        """The class areas as CSV into ``path``. Raises OSError."""
        with open(path, "w", encoding="utf-8", newline="") as file:
            file.write(self._csv())

    def _coordinateSystem(self, image: VardaRaster) -> QWidget:
        """The image's coordinate system in words, what the check found
        (with Apply or Undo for a fix), Set coordinate system…, its
        authority code, its pixel size, and its WKT (collapsed)."""
        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        crs = image.crs if image.hasGeospatialData else None
        description = describeCrs(crs, image.transform, image.width, image.height)
        unreadable = self._unreadableCrsText()
        if unreadable is not None:  # the file's text, and why PROJ refused it
            error = image.dataSource.crsError or "PROJ can't read it"
            form.addRow(wrapped(f"Unreadable coordinate system ({error})"))
        else:
            form.addRow(wrapped(description.text))
        self.applyButtons = []
        self.undoButton = None
        health = image.crsHealth
        record = image.extraMetadata.get(CRS_FIX_KEY)
        chosen = isinstance(record, dict) and record.get("automatic") is False
        for issue in health.issues if health is not None else ():
            row = CrsIssueRow(issue, chosen)
            row.sigApply.connect(self.applyFix)
            row.sigUndo.connect(self.undoFix)
            if row.button is not None and row.fix is not None:
                if row.fix.automatic:
                    self.undoButton = row.button
                else:
                    self.applyButtons.append(row.button)
            form.addRow(row)
        self.setCrsButton = QPushButton(SET_CRS)
        self.setCrsButton.setToolTip(
            "Read the image's map coordinates in another coordinate system"
        )
        self.setCrsButton.clicked.connect(self.showSetCrs)
        self.wktToggle = QPushButton("Show WKT")
        self.wktToggle.setCheckable(True)
        self.wktEdit = QPlainTextEdit()
        self.wktEdit.setReadOnly(True)
        self.wktEdit.setMaximumHeight(120)
        self.wktEdit.setVisible(False)
        self.wktToggle.toggled.connect(self._showWkt)
        buttons = FlowLayout().withWidget(self.setCrsButton)
        form.addRow(buttons)
        if unreadable is not None:
            self.wktEdit.setPlainText(unreadable)
            buttons.addWidget(self.wktToggle)
            form.addRow(self.wktEdit)
            return content
        if crs is None:
            return content
        if description.latitudeType:  # a CRS on a body
            form.addRow(
                "Conventions:",
                wrapped(
                    f"{description.latitudeType} latitude, "
                    f"{description.longitudeDirection}-positive longitude, "
                    f"{description.longitudeDomain}"
                ),
            )
        form.addRow("Authority:", QLabel(authorityText(crs, description.authority)))
        t = image.transform
        unit = (
            description.units if description.units == "°" else f" {description.units}"
        )
        ground = (
            f" ({formatMeters(description.pixelSizeM)} m on the ground)"
            if description.pixelSizeM is not None
            else ""
        )
        form.addRow("Pixel size:", QLabel(f"{abs(t.a):g} × {abs(t.e):g}{unit}{ground}"))
        with PROJ_LOCK:
            self.wktEdit.setPlainText(crs.to_wkt(pretty=True))
        buttons.addWidget(self.wktToggle)
        form.addRow(self.wktEdit)
        return content

    def _showWkt(self, shown: bool) -> None:
        self.wktEdit.setVisible(shown)
        self.wktToggle.setText("Hide WKT" if shown else "Show WKT")

    def applyFix(self, fix: CrsFix) -> None:
        """Use the fix offered (its CRS, and its pixel grid if any; applies at
        once), recorded as the user's."""
        applyCrsFix(self.image, fix)
        self.refreshCoordinateSystem()

    def undoFix(self) -> None:
        """Keep the file's own CRS and grid instead of the fix (applies at
        once)."""
        undoCrsFix(self.image)
        self.refreshCoordinateSystem()

    def _unreadableCrsText(self) -> str | None:
        """The CRS text the file has when PROJ couldn't read it, else None."""
        if self.image.crs is not None:
            return None
        return self.image.dataSource.rawCrsText or None

    def showSetCrs(self) -> None:
        # an unreadable CRS starts the field, to be corrected by hand
        self.setCrsDialog = SetCrsDialog(
            self.image, self.images, self, text=self._unreadableCrsText() or ""
        )
        self.setCrsDialog.accepted.connect(self._onCrsChosen)
        self.setCrsDialog.open()

    def _onCrsChosen(self) -> None:
        dialog = self.setCrsDialog
        assert dialog is not None
        wkt = dialog.chosenWkt()
        if wkt is not None:
            chooseCrs(self.image, wkt, dialog.reason())
            self.refreshCoordinateSystem()

    def fillWavelengths(self) -> None:
        start, step = self.fillStart.value(), self.fillStep.value()
        for row in range(self.table.rowCount()):
            self._item(row, WAVELENGTH).setText(f"{start + row * step:g}")

    def resetToFile(self) -> None:
        """Back to the image as it opened: the file's metadata, and the CRS
        check (with its automatic fix) as at load."""
        self.image.setMetadataOverrides({})
        checkImageCrs(self.image)
        self.reject()

    def _item(self, row: int, column: int) -> QTableWidgetItem:
        item = self.table.item(row, column)
        assert item is not None
        return item

    def _column(self, column: int) -> list[str]:
        return [
            self._item(r, column).text().strip() for r in range(self.table.rowCount())
        ]

    def edits(self) -> tuple[dict, str]:
        """The metadata that differs from the file's, and the name. Raises
        ValueError (for the user) on a value that doesn't read."""
        source = self._source
        count = self.image.bandCount
        overrides: dict = {}
        try:
            nodata = parseNoData(self.noDataEdit.text())
        except ValueError:
            raise ValueError("The no-data value must be a number, nan, or empty.")
        if not _same(nodata, source.nodata):
            overrides["nodata"] = nodata
        units = self.unitsBox.currentText().strip()
        if units != (source.wavelengthUnits or "Unknown"):
            overrides["wavelengthUnits"] = units
        description = self.descriptionEdit.toPlainText().strip()
        if description != (source.description or "").strip():
            overrides["description"] = description
        names = self._column(NAME)
        if names != list(source.bandNames)[:count]:
            overrides["bandNames"] = names
        wavelengths = self._column(WAVELENGTH)
        if any(wavelengths):
            try:
                values = [float(w) for w in wavelengths]
            except ValueError:
                raise ValueError("Give every band a wavelength (a number), or none.")
            fileValues = (
                list(np.asarray(source.wavelengths, dtype=float))
                if source.wavelengthsType is not str
                else None
            )
            if fileValues is None or not np.allclose(values, fileValues):
                overrides["wavelengths"] = values
        fwhm = self._column(FWHM)
        if any(fwhm):
            try:
                values = [float(w) for w in fwhm]
            except ValueError:
                raise ValueError("Give every band an FWHM (a number), or none.")
            if values != _listOf(source.extraMetadata, FWHM_KEY, count):
                overrides["fwhm"] = values
        good = [
            int(self._item(r, GOOD).checkState() == Qt.CheckState.Checked)
            for r in range(count)
        ]
        fileGood = _listOf(source.extraMetadata, BBL_KEY, count) or [1] * count
        if good != [int(g) for g in fileGood]:
            overrides["bbl"] = good
        return overrides, self.nameEdit.text().strip()

    def accept(self) -> None:
        try:
            overrides, name = self.edits()
        except ValueError as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        # a corrected CRS or pixel grid isn't edited here: keep it
        kept = {
            k: v
            for k, v in self.image.metadataOverrides.items()
            if k in ("crs", "transform")
        }
        self.image.setMetadataOverrides({**overrides, **kept}, name or None)
        if self.saveToFile.isChecked() and self.image.filePath and overrides:
            try:
                saveToHeader(self.image.filePath, overrides)
            except OSError as error:
                QMessageBox.warning(
                    self, self.windowTitle(), f"The header couldn't be saved: {error}"
                )
                return
        super().accept()
