"""Table model backed by ROICollection."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex, QVariant
from PyQt6.QtGui import QColor, QFont

from varda.common.entities import VardaROI, Color
from varda.rois.roi_calculations import CalculatedColumns
from varda.rois.roi_collection import ROICollection


# Fixed columns shown for every collection
# "Class" is the ROI name: ROIs that share one form a class
_FIXED_COLUMNS = ["FID", "Class", "Color", "Type"]


def _formatValue(value) -> str:
    """Numbers to six significant figures; a missing (NaN) number is blank."""
    if isinstance(value, float | np.floating):
        return "" if np.isnan(value) else f"{value:.6g}"
    return str(value)


class ROITableModel(QAbstractTableModel):
    """Table model exposing ROIs from an ROICollection.

    Fixed columns: FID (read-only), Class (the name, editable), Color (editable
    via delegate, applies to the whole class), Type (read-only). Additional dynamic columns come from user-added metadata.
    """

    def __init__(
        self,
        collection: ROICollection,
        parent=None,
        calculated: CalculatedColumns | None = None,
    ) -> None:
        """``calculated``: the collection's calculated columns, shown read-only
        with their formula as the header tooltip."""
        super().__init__(parent)
        self._collection = collection
        self._calculated = calculated
        self._denominatorFid: int | None = None
        self._templateFid: int | None = None
        self._roiCache: list[VardaROI] = []
        self._refreshCache()

        collection.sigROIAdded.connect(self._onChanged)
        collection.sigROIsAdded.connect(self._onChanged)
        collection.sigROIRemoved.connect(self._onChanged)
        collection.sigROIUpdated.connect(self._onChanged)
        collection.sigColumnsChanged.connect(self._onChanged)
        collection.sigVisibilityChanged.connect(self._onVisibilityChanged)

    @property
    def collection(self) -> ROICollection:
        return self._collection

    @property
    def denominatorFid(self) -> int | None:
        return self._denominatorFid

    def setDenominatorFid(self, fid: int | None) -> None:
        """Mark the row with this fid as the ratio denominator (or clear)."""
        if fid == self._denominatorFid:
            return
        self._denominatorFid = fid
        if self.rowCount() > 0:
            top = self.index(0, 0)
            bottom = self.index(self.rowCount() - 1, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom)

    @property
    def templateFid(self) -> int | None:
        return self._templateFid

    def setTemplateFid(self, fid: int | None) -> None:
        """Mark the row with this fid as the placement template (or clear)."""
        if fid == self._templateFid:
            return
        self._templateFid = fid
        if self.rowCount() > 0:
            top = self.index(0, 0)
            bottom = self.index(self.rowCount() - 1, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom)

    # --- QAbstractTableModel interface ---

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._roiCache)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(_FIXED_COLUMNS) + len(self._dynamicColumns())

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return QVariant()

        row = index.row()
        if row >= len(self._roiCache):
            return QVariant()
        roi = self._roiCache[row]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return roi.fid
            if col == 1:
                suffix = ""
                if roi.fid == self._denominatorFid:
                    suffix += "  ÷"
                if roi.fid == self._templateFid:
                    suffix += "  ⊞"
                return f"{roi.name}{suffix}"
            if col == 2:
                return None  # color shown via DecorationRole
            if col == 3:
                return roi.roiType.name
            # Dynamic columns
            dyn_col = self._dynamicColumns()
            if col - len(_FIXED_COLUMNS) < len(dyn_col):
                key = dyn_col[col - len(_FIXED_COLUMNS)]
                return _formatValue(roi.properties.get(key, ""))

        # The FID column carries a checkbox: is the ROI shown on the image?
        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            visible = self._collection.isVisible(roi.fid)
            return Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked

        if role == Qt.ItemDataRole.DecorationRole and col == 2:
            return roi.color.toQColor()

        if role == Qt.ItemDataRole.FontRole and roi.fid in (
            self._denominatorFid,
            self._templateFid,
        ):
            font = QFont()
            font.setBold(True)
            return font

        if role == Qt.ItemDataRole.EditRole:
            if col == 1:
                return roi.name

        return QVariant()

    def headerData(
        self, section: int, orientation, role: int = Qt.ItemDataRole.DisplayRole
    ):
        if orientation == Qt.Orientation.Horizontal:
            all_cols = list(_FIXED_COLUMNS) + self._dynamicColumns()
            if section < len(all_cols):
                if role == Qt.ItemDataRole.DisplayRole:
                    return all_cols[section]
                if role == Qt.ItemDataRole.ToolTipRole and section == 0:
                    return "Untick an ROI to hide it on the image"
                if role == Qt.ItemDataRole.ToolTipRole and self._calculated:
                    formula = self._calculated.describe(all_cols[section])
                    if formula is not None:
                        return f"Calculated: {formula}"
        return super().headerData(section, orientation, role)

    def flags(self, index: QModelIndex):
        base = super().flags(index)
        if not index.isValid():
            return base
        col = index.column()
        if col == 0:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        # Class and Color are editable, and user columns unless calculated
        if col == 1 or col == 2:
            return base | Qt.ItemFlag.ItemIsEditable
        if col >= len(_FIXED_COLUMNS):
            column = self._dynamicColumns()[col - len(_FIXED_COLUMNS)]
            if self._calculated is None or not self._calculated.isCalculated(column):
                return base | Qt.ItemFlag.ItemIsEditable
        return base

    def setData(
        self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if not index.isValid():
            return False

        row = index.row()
        if row >= len(self._roiCache):
            return False
        roi = self._roiCache[row]
        col = index.column()

        if col == 0 and role == Qt.ItemDataRole.CheckStateRole:
            checked = Qt.CheckState(value) == Qt.CheckState.Checked
            self._collection.setVisible(roi.fid, checked)
            return True
        if col == 1:  # Class: an existing class's ROIs share its color
            name = str(value)
            classColor = self._collection.classColor(name)
            if classColor is not None and name != roi.name:
                self._collection.updateROI(roi.fid, name=name, color=classColor)
            else:
                self._collection.updateROI(roi.fid, name=name)
        elif col == 2 and isinstance(value, QColor):  # Color, for the whole class
            color = Color.fromQColor(value)
            for fid in self._collection.classes().get(roi.name, [roi.fid]):
                self._collection.updateROI(fid, color=color)
        elif col >= len(_FIXED_COLUMNS):
            dyn_col = self._dynamicColumns()
            key = dyn_col[col - len(_FIXED_COLUMNS)]
            self._collection.setProperty(roi.fid, key, value)
        else:
            return False

        # Note: _onChanged will be called via the collection signal,
        # which refreshes the cache and resets the model.
        return True

    def fidForRow(self, row: int) -> int | None:
        """Get the fid for a given row index."""
        if row < len(self._roiCache):
            return self._roiCache[row].fid
        return None

    def userColumnAt(self, section: int) -> str | None:
        """Return the user column name at a header section, or None if fixed."""
        userCols = self._dynamicColumns()
        idx = section - len(_FIXED_COLUMNS)
        if 0 <= idx < len(userCols):
            return userCols[idx]
        return None

    # --- Internal ---

    def _dynamicColumns(self) -> list[str]:
        """Return names of user-added columns"""
        return self._collection.userColumns

    def _refreshCache(self) -> None:
        self._roiCache = self._collection.getAllROIs()

    def _onVisibilityChanged(self, fid: int) -> None:
        """Repaint just that ROI's checkbox (a reset would lose the selection)."""
        for row, roi in enumerate(self._roiCache):
            if roi.fid == fid:
                index = self.index(row, 0)
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])

    def _onChanged(self, *args) -> None:
        self.beginResetModel()
        self._refreshCache()
        self.endResetModel()
