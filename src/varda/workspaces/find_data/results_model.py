"""The search results as a table: a check (to download), the product ID with
its thumbnail, its start time and the catalog's properties (the most common
ones as columns)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap

from varda.catalogs.catalog import Product

MAX_PROPERTY_COLUMNS = 8
SORT_ROLE = Qt.ItemDataRole.UserRole + 1


class ResultsModel(QAbstractTableModel):
    sigChecksChanged = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.products: list[Product] = []
        self.checked: set[str] = set()
        self.properties: list[str] = []
        self.thumbnails: dict[str, QPixmap] = {}  # by product id, as they arrive

    @property
    def headers(self) -> list[str]:
        return ["", "Product", "Start", *self.properties]

    def setProducts(self, products: Sequence[Product]) -> None:
        self.beginResetModel()
        self.products = list(products)
        ids = {p.id for p in self.products}
        self.checked &= ids
        self.thumbnails = {i: t for i, t in self.thumbnails.items() if i in ids}
        counts = Counter(key for p in self.products for key in p.properties)
        self.properties = [key for key, _ in counts.most_common(MAX_PROPERTY_COLUMNS)]
        self.endResetModel()
        self.sigChecksChanged.emit()

    def setThumbnail(self, productId: str, pixmap: QPixmap) -> None:
        self.thumbnails[productId] = pixmap
        row = next((r for r, p in enumerate(self.products) if p.id == productId), -1)
        if row >= 0:
            index = self.index(row, 1)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def checkedProducts(self) -> list[Product]:
        return [p for p in self.products if p.id in self.checked]

    def setChecked(self, ids: Sequence[str], checked: bool = True) -> None:
        self.checked = self.checked | set(ids) if checked else self.checked - set(ids)
        if self.products:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self.products) - 1, 0)
            )
        self.sigChecksChanged.emit()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.products)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return self.headers[section]
        return None

    def flags(self, index):
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsUserCheckable if index.column() == 0 else flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        product = self.products[index.row()]
        column = index.column()
        if column == 0:
            if role == Qt.ItemDataRole.CheckStateRole:
                return (
                    Qt.CheckState.Checked
                    if product.id in self.checked
                    else Qt.CheckState.Unchecked
                )
            if role == SORT_ROLE:
                return int(product.id in self.checked)
            return None
        if column == 1:
            value = product.id
        elif column == 2:
            value = product.start.strftime("%Y-%m-%d %H:%M") if product.start else ""
        else:
            value = product.properties.get(self.properties[column - 3], "")
        if role == SORT_ROLE:
            return value if isinstance(value, (int, float)) else str(value)
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{value:g}" if isinstance(value, float) else str(value)
        if role == Qt.ItemDataRole.ToolTipRole and column == 1:
            return product.title
        if role == Qt.ItemDataRole.DecorationRole and column == 1:
            return self.thumbnails.get(product.id)
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            product = self.products[index.row()]
            self.setChecked(
                [product.id],
                value == Qt.CheckState.Checked.value or value == Qt.CheckState.Checked,
            )
            return True
        return False
