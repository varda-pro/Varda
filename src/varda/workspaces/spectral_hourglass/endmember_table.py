"""The endmember table: one row per endmember class, the active row is where
lasso selections go."""

from __future__ import annotations

import json
from collections.abc import Sequence

import numpy as np
from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QFileDialog,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from varda.common.keys import shortcutText
from varda.common.spectral_axis import SpectralUnit
from varda.common.ui import FlowLayout, HBoxBuilder, WrapperWidget
from varda.library_manager.add_to_library_dialog import AddToLibraryDialog
from varda.workspaces.spectral_hourglass.hourglass_model import HourglassModel

_COLUMNS = ["", "Name", "Pixels", "Identified as"]

# ASCII unit names for CSV column headers (read back by the library loader)
EXPORT_UNIT_TOKENS = {
    SpectralUnit.NANOMETRE: "nm",
    SpectralUnit.MICROMETRE: "um",
    SpectralUnit.WAVENUMBER: "cm-1",
}


class EndmemberTable(QWidget):
    sigActiveChanged = pyqtSignal(object)  # endmember id or None

    def __init__(self, model: HourglassModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self._activeId: int | None = None

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        vertical, header = self.table.verticalHeader(), self.table.horizontalHeader()
        assert vertical is not None and header is not None
        vertical.setVisible(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 24)
        for column in (1, 2, 3):  # drag the dividers to resize
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(1, 160)
        header.resizeSection(2, 60)
        header.setStretchLastSection(True)
        self.table.setSortingEnabled(True)  # click a header to sort
        self.table.itemChanged.connect(self._onItemChanged)
        self.table.cellDoubleClicked.connect(self._onCellDoubleClicked)
        self.table.currentCellChanged.connect(lambda row, *_: self._onCurrentRow(row))

        self.addButton = QPushButton("New")
        self.addButton.setToolTip("Add an empty endmember and make it the lasso target")
        self.addButton.clicked.connect(self._addEndmember)
        self.mergeButton = QPushButton("Merge")
        self.mergeButton.setToolTip("Merge the selected endmembers into the first")
        self.mergeButton.clicked.connect(self._mergeSelected)
        self.deleteButton = QPushButton("Delete")
        self.deleteButton.clicked.connect(self._deleteSelected)
        self.undoButton = QPushButton("Undo")
        self.undoButton.setToolTip(
            f"Undo the last endmember edit ({shortcutText('Ctrl+Z')})"
        )
        self.undoButton.clicked.connect(self.model.undo)
        self.suggestCount = QSpinBox()
        self.suggestCount.setRange(2, 30)
        self.suggestCount.setValue(5)
        self.suggestCount.setToolTip("How many groups to suggest")
        self.suggestButton = QPushButton("Suggest")
        self.suggestButton.setToolTip(
            "Find that many corners of the candidate cloud (ATGP) and group "
            "the candidates close to each corner into new endmembers; then "
            "refine them in the n-D view"
        )
        self.suggestButton.clicked.connect(self._suggest)
        saveButton = QPushButton("Save…")
        saveButton.setToolTip(
            "Save the endmember classes (names, colors, pixels) as JSON"
        )
        saveButton.clicked.connect(self._save)
        loadButton = QPushButton("Load…")
        loadButton.clicked.connect(self._load)
        exportButton = QPushButton("Export Spectra…")
        exportButton.setToolTip("Write the endmember mean spectra as a CSV library")
        exportButton.clicked.connect(self._exportSpectra)
        self.addToLibraryButton = QPushButton("Add to Library…")
        self.addToLibraryButton.setToolTip(
            "Save the selected endmembers' mean spectra (all, if none is "
            "selected) in one of your spectral libraries, with ± 1 σ, the "
            "pixel count and the image"
        )
        self.addToLibraryButton.clicked.connect(self._openAddToLibrary)

        # button rows wrap in a narrow dock
        edit = FlowLayout()
        for button in (
            self.addButton,
            self.mergeButton,
            self.deleteButton,
            self.undoButton,
        ):
            edit.addWidget(button)
        suggest = (
            HBoxBuilder(margins=0)
            .withWidget(QLabel("Suggest"))
            .withWidget(self.suggestCount)
            .withWidget(self.suggestButton)
        )
        edit.addWidget(WrapperWidget(suggest))
        files = FlowLayout()
        for button in (saveButton, loadButton, exportButton, self.addToLibraryButton):
            files.addWidget(button)

        layout = QVBoxLayout(self)
        layout.addLayout(edit)
        layout.addWidget(self.table)
        layout.addLayout(files)

        model.sigEndmembersChanged.connect(self.refresh)
        self.refresh()

    # --- rows ---------------------------------------------------------------------

    def refresh(self) -> None:
        self.undoButton.setEnabled(self.model.canUndo())
        endmembers = self.model.endmembers
        with QSignalBlocker(self.table):
            # rows must not move while being filled; re-sorted below
            self.table.setSortingEnabled(False)
            self.table.setRowCount(len(endmembers))
            for row, endmember in enumerate(endmembers):
                swatch = QTableWidgetItem()
                swatch.setBackground(QBrush(QColor(endmember.color)))
                swatch.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                swatch.setToolTip("Double-click to change the color")
                name = QTableWidgetItem(endmember.name)
                count = QTableWidgetItem()
                count.setData(
                    Qt.ItemDataRole.DisplayRole, int(endmember.pixels.size)
                )  # sorts numerically
                count.setFlags(count.flags() & ~Qt.ItemFlag.ItemIsEditable)
                count.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                ident = QTableWidgetItem(endmember.identification)
                ident.setFlags(ident.flags() & ~Qt.ItemFlag.ItemIsEditable)
                for column, item in enumerate((swatch, name, count, ident)):
                    item.setData(Qt.ItemDataRole.UserRole, endmember.id)
                    self.table.setItem(row, column, item)
            self.table.setSortingEnabled(True)
            row = self._rowOf(self._activeId)
            if row is not None:
                self.table.setCurrentCell(row, 1)
        if self._activeId not in [e.id for e in endmembers]:
            self._setActive(endmembers[-1].id if endmembers else None)

    def _rowOf(self, endmemberId: int | None) -> int | None:
        """The (possibly sorted) table row showing an endmember."""
        for row in range(self.table.rowCount()):
            if endmemberId is not None and self._rowId(row) == endmemberId:
                return row
        return None

    def _rowId(self, row: int) -> int | None:
        item = self.table.item(row, 1)
        return None if item is None else int(item.data(Qt.ItemDataRole.UserRole))

    def activeEndmemberId(self) -> int | None:
        return self._activeId

    def setActiveEndmember(self, endmemberId: int | None) -> None:
        self._setActive(endmemberId)
        row = self._rowOf(endmemberId)
        if row is not None:
            with QSignalBlocker(self.table):
                self.table.setCurrentCell(row, 1)

    def _setActive(self, endmemberId: int | None) -> None:
        if endmemberId != self._activeId:
            self._activeId = endmemberId
            self.sigActiveChanged.emit(endmemberId)

    def _onCurrentRow(self, row: int) -> None:
        if row >= 0:
            self._setActive(self._rowId(row))

    def selectRows(self, rows: Sequence[int]) -> None:
        self.table.clearSelection()
        mode = self.table.selectionMode()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for row in rows:
            self.table.selectRow(row)
        self.table.setSelectionMode(mode)

    def _selectedIds(self) -> list[int]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        ids = [self._rowId(r) for r in rows]
        return [i for i in ids if i is not None]

    # --- edits ----------------------------------------------------------------------

    def _onItemChanged(self, item: QTableWidgetItem) -> None:
        if item.column() == 1:
            self.model.renameEndmember(
                int(item.data(Qt.ItemDataRole.UserRole)), item.text()
            )

    def _onCellDoubleClicked(self, row: int, column: int) -> None:
        endmemberId = self._rowId(row)
        if column != 0 or endmemberId is None:
            return
        color = QColorDialog.getColor(
            QColor(self.model.endmember(endmemberId).color), self
        )
        if color.isValid():
            self.model.recolorEndmember(endmemberId, color.name())

    def _addEndmember(self) -> None:
        endmember = self.model.addEndmember()
        self.setActiveEndmember(endmember.id)

    def _mergeSelected(self) -> None:
        ids = self._selectedIds()
        if len(ids) >= 2:
            self.setActiveEndmember(self.model.mergeEndmembers(ids).id)

    def _deleteSelected(self) -> None:
        for endmemberId in self._selectedIds():
            self.model.removeEndmember(endmemberId)

    def _suggest(self) -> None:
        try:
            created = self.model.suggestEndmembers(self.suggestCount.value())
        except ValueError as error:
            QMessageBox.information(self, "Suggest endmembers", str(error))
            return
        if created:
            self.setActiveEndmember(created[0].id)

    # --- files ------------------------------------------------------------------------

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Endmembers",
            f"{self.model.image.name}_endmembers.json",
            "JSON (*.json)",
        )
        if path:
            with open(path, "w") as f:
                json.dump(self.model.endmembersToJson(), f, indent=1)

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Endmembers", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            with open(path) as f:
                self.model.endmembersFromJson(json.load(f))
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(
                self, "Load Endmembers", f"Could not load {path}:\n{error}"
            )

    def addToLibraryDialog(self) -> AddToLibraryDialog | None:
        """Add to Library for the selected endmembers (all usable ones when
        none is selected); None before Prepare or without wavelengths."""
        if self.model.prepared is None:
            return None
        usable = {e.id for e in self.model.usableEndmembers()}
        ids = [i for i in self._selectedIds() if i in usable] or [
            e.id for e in self.model.usableEndmembers()
        ]
        spectra = self.model.endmemberLibrarySpectra(ids)
        if not spectra:
            return None
        dialog = AddToLibraryDialog(spectra, parent=self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        return dialog

    def _openAddToLibrary(self) -> None:
        dialog = self.addToLibraryDialog()
        if dialog is None:
            QMessageBox.information(
                self,
                "Add to Library",
                "Add to Library needs endmembers with pixels, after Prepare, "
                "on an image whose bands have wavelengths.",
            )
            return
        dialog.open()

    def _exportSpectra(self) -> None:
        endmembers = self.model.usableEndmembers()
        if not endmembers or self.model.prepared is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Endmember Spectra",
            f"{self.model.image.name}_endmembers.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        wavelengths = self.model.wavelengths
        x = (
            wavelengths
            if wavelengths is not None
            else np.arange(self.model.cube.shape[2]) + 1
        )
        spectra = [self.model.endmemberSpectrum(e.id) for e in endmembers]
        # The unit in the column name, so the file loads back as a library
        unit = self.model.image.bandModel.unit
        column = (
            f"wavelength_{EXPORT_UNIT_TOKENS[unit]}"
            if wavelengths is not None and unit is not None
            else "band"
        )
        header = ",".join([column, *[e.name.replace(",", " ") for e in endmembers]])
        np.savetxt(
            path,
            np.column_stack([x, *spectra]),
            delimiter=",",
            header=header,
            comments="",
        )
