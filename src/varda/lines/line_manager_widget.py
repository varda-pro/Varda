"""Lines manager: the kind of line Draw Line draws, and a table of the lines
(rename, recolor, delete, import from and export to GIS files)."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QColorDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from varda.common.entities import VardaRaster
from varda.common.ui import FlowLayout, MutedLabel
from varda.lines.line_collection import EXPORT_FILE_FILTER, LineCollection, LineKind
from varda.vector_import import ImportPlan, Target, addToLines
from varda.vector_import_dialog import VectorImporter

logger = logging.getLogger(__name__)

_COLUMNS = ("Name", "Kind", "Length", "Color")
KIND_TIPS = {
    LineKind.STRAIGHT: "Drag from one end to the other, or click both ends",
    LineKind.POLYLINE: "Click each vertex; double-click, right-click or Enter ends it",
    LineKind.FREEHAND: "Press, drag and release",
}


def formatLength(length: float, units: str) -> str:
    if units == "m":
        return f"{length / 1000:.3g} km" if length >= 1000 else f"{length:.3g} m"
    return f"{length:.1f} px"


class LineTableModel(QAbstractTableModel):
    def __init__(
        self, collection: LineCollection, image: VardaRaster | None, parent=None
    ) -> None:
        super().__init__(parent)
        self._collection = collection
        self._image = image
        self._rows = collection.lines()
        collection.sigCollectionChanged.connect(self._reload)

    def _reload(self) -> None:
        self.beginResetModel()
        self._rows = self._collection.lines()
        self.endResetModel()

    def fidAt(self, row: int) -> int:
        return self._rows[row].fid

    def rowOf(self, fid: int) -> int:
        return next((r for r, line in enumerate(self._rows) if line.fid == fid), -1)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return _COLUMNS[section]
        return None

    def flags(self, index):
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsEditable if index.column() == 0 else flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        line = self._rows[index.row()]
        column = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if column == 0:
                return line.name
            if column == 1:
                return line.kind.value
            if column == 2:
                return formatLength(*line.length(self._image))
            return ""
        if role == Qt.ItemDataRole.DecorationRole and column == 3:
            return QColor(line.color)
        if role == Qt.ItemDataRole.ToolTipRole and column == 3:
            return "Double-click to change the color"
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if index.column() == 0 and role == Qt.ItemDataRole.EditRole and str(value):
            self._collection.updateLine(self.fidAt(index.row()), name=str(value))
            return True
        return False


class LineManagerWidget(QWidget):
    sigSelectionChanged = pyqtSignal(list)  # fids of the selected lines

    def __init__(
        self,
        collection: LineCollection,
        image: VardaRaster | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.collection = collection
        self.image = image

        self.kindButtons = QButtonGroup(self)
        kinds = QHBoxLayout()
        kinds.addWidget(QLabel("Draw:"))
        for kind in LineKind:
            button = QToolButton()
            button.setText(kind.value)
            button.setCheckable(True)
            button.setToolTip(KIND_TIPS[kind])
            self.kindButtons.addButton(button)
            button.setProperty("kind", kind.name)
            kinds.addWidget(button)
        self.kindButtons.buttons()[0].setChecked(True)
        kinds.addStretch()

        self.model = LineTableModel(collection, image, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        rows, columns = self.table.verticalHeader(), self.table.horizontalHeader()
        assert rows is not None and columns is not None
        rows.setVisible(False)
        columns.setStretchLastSection(True)
        self.table.doubleClicked.connect(self._onDoubleClicked)
        selection = self.table.selectionModel()
        assert selection is not None
        selection.selectionChanged.connect(lambda *_: self._emitSelection())
        self.model.modelReset.connect(self._emitSelection)

        delete = QPushButton("Delete Selected")
        delete.clicked.connect(self.deleteSelected)
        export = QPushButton("Export…")
        export.setToolTip("Save the lines as GeoJSON, GeoPackage or a shapefile")
        export.clicked.connect(self._export)
        self.importer = VectorImporter(Target.LINES, image, self._addImported, self)
        self.importButton = QPushButton("Import…")
        self.importButton.setEnabled(image is not None)
        self.importButton.setToolTip(
            "Add lines from a vector file (shapefile, GeoPackage, GeoJSON, KML, GPX, …)"
            if image is not None
            else "Open an image to import lines onto"
        )
        self.importButton.clicked.connect(self.importer.chooseFile)
        buttons = FlowLayout()  # wraps in a narrow dock
        buttons.addWidget(delete)
        buttons.addWidget(self.importButton)
        buttons.addWidget(export)

        hint = MutedLabel(
            "Turn on Draw Line under a view and draw; select lines to plot their "
            "profiles (all lines when none are selected)."
        )
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(kinds)
        layout.addWidget(hint)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)

    def kind(self) -> LineKind:
        checked = self.kindButtons.checkedButton()
        return LineKind[checked.property("kind")] if checked else LineKind.STRAIGHT

    def selectedFids(self) -> list[int]:
        selection = self.table.selectionModel()
        assert selection is not None
        return [self.model.fidAt(i.row()) for i in selection.selectedRows()]

    def select(self, fids: list[int]) -> None:
        self.table.clearSelection()
        for fid in fids:
            row = self.model.rowOf(fid)
            if row >= 0:
                self.table.selectRow(row)

    def _emitSelection(self) -> None:
        self.sigSelectionChanged.emit(self.selectedFids())

    def deleteSelected(self) -> None:
        for fid in self.selectedFids():
            self.collection.removeLine(fid)

    def _onDoubleClicked(self, index: QModelIndex) -> None:
        if index.column() != 3:
            return
        fid = self.model.fidAt(index.row())
        line = self.collection.get(fid)
        color = QColorDialog.getColor(QColor(line.color), self, f"Color of {line.name}")
        if color.isValid():
            self.collection.updateLine(fid, color=color.name())

    def _addImported(self, plan: ImportPlan) -> None:
        addToLines(self.collection, plan)

    def _export(self) -> None:
        if not len(self.collection) or self.image is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Lines", "lines.geojson", EXPORT_FILE_FILTER
        )
        if not path:
            return
        try:
            self.collection.toFile(path, self.image)
        except (OSError, ValueError) as error:
            logger.exception("Exporting lines failed")
            QMessageBox.warning(self, "Export Lines", str(error))
