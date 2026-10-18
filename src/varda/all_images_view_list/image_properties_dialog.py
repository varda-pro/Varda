"""Image Properties: what an image is (file, format, size, map projection)
and the metadata Varda reads from it, with the parts that matter for
analysis editable: the name, the no-data value, the wavelengths and their
unit, band names, FWHM, which bands are good, and the description.

Edits take effect in Varda at once (and are kept in sessions and
packages); for an ENVI file they can also be saved into its header. Only
values that differ from the file's become edits."""

from __future__ import annotations

import math

import numpy as np
from pyproj import CRS
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
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

from varda.common.entities import VardaRaster
from varda.common.spectral_axis import BBL_KEY, FWHM_KEY, parseEnviList
from varda.common.ui import MutedLabel, PanelScrollArea
from varda.geo.describe import describeCrs, formatMeters
from varda.image_loading.envi_header import headerFor, saveToHeader
from varda.utilities.proj_lock import PROJ_LOCK

UNITS = ["nm", "µm", "cm⁻¹", "Unknown"]
NAME, WAVELENGTH, FWHM, GOOD = 1, 2, 3, 4  # table columns (0: the band number)


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


class ImagePropertiesDialog(QDialog):
    def __init__(self, image: VardaRaster, parent: QWidget | None = None):
        super().__init__(parent)
        self.image = image
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
        self.crsBox = self._coordinateSystem(image)
        for label in self.findChildren(QLabel):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

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
        panelLayout.addLayout(edits)
        panelLayout.addWidget(bands, 1)
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

    def _coordinateSystem(self, image: VardaRaster) -> QGroupBox:
        """The image's coordinate system in words, its authority code, its
        pixel size, and its WKT (collapsed)."""
        box = QGroupBox("Coordinate system")
        form = QFormLayout(box)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        crs = image.crs if image.hasGeospatialData else None
        description = describeCrs(crs, image.transform, image.width, image.height)
        form.addRow(wrapped(description.text))
        self.wktToggle = QPushButton("Show WKT")
        self.wktToggle.setCheckable(True)
        self.wktEdit = QPlainTextEdit()
        self.wktEdit.setReadOnly(True)
        self.wktEdit.setMaximumHeight(120)
        self.wktEdit.setVisible(False)
        self.wktToggle.toggled.connect(self.wktEdit.setVisible)
        self.wktToggle.toggled.connect(
            lambda shown: self.wktToggle.setText("Hide WKT" if shown else "Show WKT")
        )
        if crs is None:
            return box
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
        form.addRow(self.wktToggle)
        form.addRow(self.wktEdit)
        return box

    def fillWavelengths(self) -> None:
        start, step = self.fillStart.value(), self.fillStep.value()
        for row in range(self.table.rowCount()):
            self._item(row, WAVELENGTH).setText(f"{start + row * step:g}")

    def resetToFile(self) -> None:
        self.image.setMetadataOverrides({})
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
        self.image.setMetadataOverrides(overrides, name or None)
        if self.saveToFile.isChecked() and self.image.filePath and overrides:
            try:
                saveToHeader(self.image.filePath, overrides)
            except OSError as error:
                QMessageBox.warning(
                    self, self.windowTitle(), f"The header couldn't be saved: {error}"
                )
                return
        super().accept()
