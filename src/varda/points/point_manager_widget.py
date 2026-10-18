"""Points Manager: a table of the saved pixel points with delete, import
and export."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QLabel,
    QMessageBox,
    QTableView,
    QWidget,
)

from varda.common.entities import Color, VardaRaster
from varda.common.parameter_widgets import connectWhileAlive
from varda.common.ui import ButtonBuilder, FlowLayout, VBoxBuilder
from varda.points.point_collection import EXPORT_FILE_FILTER, PointCollection
from varda.vector_import import ImportPlan, Target, addToPoints
from varda.vector_import_dialog import VectorImporter

logger = logging.getLogger(__name__)

_COLUMNS = ("Name", "Image", "X", "Y", "Geo X", "Geo Y")


class PointTableModel(QAbstractTableModel):
    def __init__(self, collection: PointCollection, parent=None) -> None:
        super().__init__(parent)
        self._collection = collection
        self._rows = collection.getAllPoints()
        collection.sigCollectionChanged.connect(self._reload)

    def _reload(self) -> None:
        self.beginResetModel()
        self._rows = self._collection.getAllPoints()
        self.endResetModel()

    def fidAt(self, row: int) -> int:
        return self._rows[row].fid

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

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        point = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                point.name,
                point.image,
                str(point.x),
                str(point.y),
                f"{point.geometry.x:.6f}",
                f"{point.geometry.y:.6f}",
            )[index.column()]
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            return point.color.toQColor()
        return None


class PointManagerWidget(QWidget):
    sigSelectionChanged = pyqtSignal(object)  # fid (int) or None

    def __init__(
        self,
        collection: PointCollection,
        parent: QWidget | None = None,
        image: VardaRaster | None = None,
    ):
        """``image``: the image imported points are placed on (Import… is
        off without one)."""
        super().__init__(parent)
        self._collection = collection
        self._image = image
        self.model = PointTableModel(collection, parent=self)
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
        self.deleteButton = ButtonBuilder("Delete Selected").onClick(
            self._deleteSelected
        )
        self.exportButton = ButtonBuilder("Export…").onClick(self._export)
        self.importer = VectorImporter(Target.POINTS, image, self._addImported, self)
        self.importButton = ButtonBuilder("Import…").onClick(self.importer.chooseFile)
        self.importButton.setEnabled(image is not None)
        self.importButton.setToolTip(
            "Add points from a vector file or a CSV of coordinates"
            if image is not None
            else "Open an image to import points onto"
        )
        # Why the last point wasn't saved (another body), or why points kept
        # their positions when their image's CRS changed; hidden otherwise
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.hide()
        connectWhileAlive(collection.sigProblem, self._showStatus, self)
        self.setLayout(
            VBoxBuilder()
            .withLayout(
                FlowLayout()  # wraps in a narrow dock
                .withWidget(self.deleteButton)
                .withWidget(self.importButton)
                .withWidget(self.exportButton)
            )
            .withWidget(self.table)
            .withWidget(self.status)
        )
        selectionModel = self.table.selectionModel()
        if selectionModel is not None:
            selectionModel.selectionChanged.connect(
                lambda *_: self.sigSelectionChanged.emit(self.selectedFid())
            )

    def selectedFid(self) -> int | None:
        selectionModel = self.table.selectionModel()
        rows = selectionModel.selectedRows() if selectionModel is not None else []
        return self.model.fidAt(rows[0].row()) if rows else None

    def addPixel(
        self,
        image: VardaRaster,
        x: int,
        y: int,
        *,
        name: str | None = None,
        color: Color = Color.white(),
        description: str = "",
    ) -> int | None:
        """Save pixel (x, y) of ``image`` (``PointCollection.addPixel``);
        None, with the reason in the status line, when it can't be (an
        image of another body than the points')."""
        try:
            fid = self._collection.addPixel(
                image, x, y, name=name, color=color, description=description
            )
        except ValueError as error:
            self._showStatus(f"Point not saved: {error}.")
            return None
        self._showStatus("")
        return fid

    def _showStatus(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _deleteSelected(self) -> None:
        fid = self.selectedFid()
        if fid is not None:
            self._collection.removePoint(fid)
            self._showStatus("")

    def _addImported(self, plan: ImportPlan) -> None:
        if self._image is None:
            return
        try:
            addToPoints(self._collection, self._image, plan)
        except ValueError as error:
            self._showStatus(f"Points not imported: {error}.")
            return
        self._showStatus("")

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export points", "points.geojson", EXPORT_FILE_FILTER
        )
        if not path:
            return
        try:
            written = self._collection.toFile(path)
        except (ValueError, OSError) as error:
            QMessageBox.critical(self, "Export failed", str(error))
            return
        self._showStatus("")
        logger.info("Points exported to %s", written)
