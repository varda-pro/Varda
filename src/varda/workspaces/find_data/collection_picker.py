"""Choosing what to search: a bar of the bodies with data (``BodyBar``) and,
for the chosen body, every catalog's collections in one list
(``CollectionFinder``) under a search field. Every word typed must appear in
a collection's mission, instrument, title, id, description or source
("crism", "kaguya dtm"); the list is grouped by mission and instrument, and
each collection says which catalog offers it."""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QRect,
    QSize,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QFont,
    QFontMetrics,
    QKeyEvent,
    QPainter,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QLineEdit,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from varda.catalogs.catalog import Collection
from varda.catalogs.sources import Source
from varda.common.ui import FlowLayout, MutedLabel, mutedTextColor

SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1  # the text words are matched in
KEY_ROLE = Qt.ItemDataRole.UserRole + 2  # a collection's source key
SOURCE_ROLE = Qt.ItemDataRole.UserRole + 3  # the catalog's name, drawn as a tag


def searchText(collection: Collection, source: str = "") -> str:
    return " ".join(
        (
            collection.body,
            collection.group,
            collection.title,
            collection.id,
            collection.description,
            source,
        )
    ).lower()


def _leafCount(item: QStandardItem) -> int:
    children = [item.child(r) for r in range(item.rowCount())]
    return sum(_leafCount(c) if c.data(KEY_ROLE) is None else 1 for c in children if c)


class _WordFilter(QSortFilterProxyModel):
    """Keeps the collections whose text holds every word (groups show while
    any collection in them does)."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.words: list[str] = []
        self.setRecursiveFilteringEnabled(True)

    def setWords(self, text: str) -> None:
        self.words = text.lower().split()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N803 (Qt's names)
        model = self.sourceModel()
        assert model is not None
        text = model.index(source_row, 0, source_parent).data(SEARCH_ROLE)
        if text is None:  # a group: shown for its collections
            return not self.words
        return all(word in text for word in self.words)


class _SourceTagDelegate(QStyledItemDelegate):
    """A collection's title, and its catalog in small muted type at the
    right: "MTRDR …   PDS ODE"."""

    def paint(
        self, painter: QPainter | None, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        source = index.data(SOURCE_ROLE)
        if not source or painter is None:
            super().paint(painter, option, index)
            return
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() - 2))
        smallMetrics = QFontMetrics(small)
        # the tag never takes more than a third of the row: the title matters more
        room = option.rect.width() // 3
        source = smallMetrics.elidedText(source, Qt.TextElideMode.ElideRight, room - 12)
        tagWidth = smallMetrics.horizontalAdvance(source) + 12
        title = QStyleOptionViewItem(option)
        self.initStyleOption(title, index)
        title.rect = option.rect.adjusted(0, 0, -tagWidth, 0)
        style = option.widget.style() if option.widget is not None else None
        if style is not None:
            style.drawControl(
                QStyle.ControlElement.CE_ItemViewItem, title, painter, option.widget
            )
        painter.save()
        painter.setFont(small)
        selected = option.state & QStyle.StateFlag.State_Selected
        painter.setPen(
            option.palette.highlightedText().color()
            if selected
            else mutedTextColor(option.palette)
        )
        tag = QRect(
            option.rect.right() - tagWidth,
            option.rect.top(),
            tagWidth - 6,
            option.rect.height(),
        )
        painter.drawText(
            tag, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, source
        )
        painter.restore()


class CollectionFinder(QWidget):
    """A search field over the chosen body's collections, grouped by mission
    and instrument. ``sigChosen`` gives the chosen collection's source key."""

    sigChosen = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search missions, instruments and products…")
        self.search.setClearButtonEnabled(True)
        self.model = QStandardItemModel(self)
        self.proxy = _WordFilter(self)
        self.proxy.setSourceModel(self.model)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setUniformRowHeights(True)
        self.tree.setItemDelegate(_SourceTagDelegate(self.tree))
        self.tree.setMinimumHeight(140)
        self.count = MutedLabel("")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.search)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.count)
        self.sources: list[Source] = []
        self.chosenKey = ""
        self.search.textChanged.connect(self._onSearch)
        self.search.installEventFilter(self)
        self.tree.activated.connect(self._choose)
        self.tree.clicked.connect(self._choose)

    def setSources(
        self, sources: Sequence[Source], body: str, select: str = ""
    ) -> None:
        """Fill it with ``body``'s ``sources`` (already its own), keeping or
        choosing ``select``. Nothing is chosen (or signaled) unless asked."""
        self.sources = list(sources)
        self.model.clear()
        groups: dict[str, QStandardItem] = {}
        current: QStandardItem | None = None
        for source in self.sources:
            collection = source.collection
            parent = self.model.invisibleRootItem()
            if collection.group:
                if collection.group not in groups:
                    group = QStandardItem(collection.group)
                    group.setSelectable(False)
                    parent.appendRow(group)
                    groups[collection.group] = group
                parent = groups[collection.group]
            title = collection.title
            if collection.group and title.startswith(collection.group + " "):
                title = title[len(collection.group) + 1 :]  # "MRO CRISM" says it
            item = QStandardItem(title)
            item.setData(searchText(collection, source.catalogName), SEARCH_ROLE)
            item.setData(source.key, KEY_ROLE)
            item.setData(source.tag, SOURCE_ROLE)
            item.setToolTip(
                f"{collection.title}\n{collection.description}\nFrom {source.catalogName}"
                if collection.description
                else f"{collection.title}\nFrom {source.catalogName}"
            )
            parent.appendRow(item)
            if source.key == select:
                current = item
        for item in groups.values():  # "MRO CRISM (8)"
            item.setText(f"{item.text()} ({_leafCount(item)})")
        self.body = body
        self.chosenKey = select if current is not None else ""
        self._onSearch(self.search.text())
        if current is not None:
            index = self.proxy.mapFromSource(current.index())
            self.tree.setCurrentIndex(index)
            self.tree.scrollTo(index)

    def chosen(self) -> Source | None:
        return next((s for s in self.sources if s.key == self.chosenKey), None)

    def choose(self, key: str) -> bool:
        """Choose a collection by its source key; whether it is listed."""
        for index in self._leaves(QModelIndex(), filtered=False):
            if index.data(KEY_ROLE) == key:
                shown = self.proxy.mapFromSource(index)
                if not shown.isValid():  # the search hides it
                    self.search.clear()
                    shown = self.proxy.mapFromSource(index)
                self._choose(shown)
                return True
        return False

    def _leaves(
        self, parent: QModelIndex = QModelIndex(), filtered: bool = True
    ) -> list[QModelIndex]:
        model = self.proxy if filtered else self.model
        found = []
        for row in range(model.rowCount(parent)):
            index = model.index(row, 0, parent)
            if index.data(KEY_ROLE) is not None:
                found.append(index)
            else:
                found += self._leaves(index, filtered)
        return found

    def _onSearch(self, text: str) -> None:
        self.proxy.setWords(text)
        leaves = self._leaves()
        if text.strip():
            self.tree.expandAll()
        elif len(self.sources) <= 12:
            self.tree.expandAll()
        else:
            self.tree.collapseAll()
            self._revealChosen()
        total = len(self.sources)
        where = f" on {self.body}" if getattr(self, "body", "") else ""
        self.count.setText(
            f"{total} collections{where}"
            if len(leaves) == total
            else f"{len(leaves)} of {total} collections{where}"
        )

    def _revealChosen(self) -> None:
        for index in self._leaves():
            if index.data(KEY_ROLE) == self.chosenKey:
                self.tree.setCurrentIndex(index)
                self.tree.scrollTo(index)

    def _choose(self, index: QModelIndex) -> None:
        key = index.data(KEY_ROLE)
        if key is None:  # a group opens and closes
            self.tree.setExpanded(index, not self.tree.isExpanded(index))
            return
        self.tree.setCurrentIndex(index)
        if key != self.chosenKey:
            self.chosenKey = key
            self.sigChosen.emit(key)

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:
        """Up, Down and Enter in the search field move through the list."""
        if (
            watched is self.search
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
        ):
            key = event.key()
            leaves = self._leaves()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                current = self.tree.currentIndex()
                if current.data(KEY_ROLE) is None and leaves:
                    current = leaves[0]
                if current.isValid():
                    self._choose(current)
                return True
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and leaves:
                keys = [i.data(KEY_ROLE) for i in leaves]
                current = self.tree.currentIndex().data(KEY_ROLE)
                at = keys.index(current) if current in keys else -1
                at = max(
                    0, min(len(leaves) - 1, at + (1 if key == Qt.Key.Key_Down else -1))
                )
                self.tree.setCurrentIndex(leaves[at])
                self.tree.scrollTo(leaves[at])
                return True
        return super().eventFilter(watched, event)


class BodyBar(QWidget):
    """The bodies with data, as a row of buttons (wrapping when narrow),
    each with how many collections it has. ``sigBodyChanged`` on a click."""

    sigBodyChanged = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.group.buttonClicked.connect(self._onClicked)
        self.flow = FlowLayout(self)
        self.flow.setSpacing(6)
        self.buttons: dict[str, QToolButton] = {}

    def setBodies(self, counts: Sequence[tuple[str, int]], current: str) -> None:
        """Show these bodies (in order) with their counts; ``current`` checked
        (without a signal)."""
        names = [name for name, _ in counts]
        for name in list(self.buttons):
            if name not in names:
                button = self.buttons.pop(name)
                self.group.removeButton(button)
                self.flow.removeWidget(button)
                button.deleteLater()
        for name, count in counts:
            button = self.buttons.get(name)
            if button is None:
                button = QToolButton()
                button.setCheckable(True)
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
                button.setProperty("body", name)
                button.setAccessibleName(name)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                self.group.addButton(button)
                self.buttons[name] = button
            button.setText(f"{name}  {count}")
            button.setToolTip(f"{count} collections of {name}")
        # keep the given order
        for name in names:
            self.flow.removeWidget(self.buttons[name])
        for name in names:
            self.flow.addWidget(self.buttons[name])
        self._applyStyle()
        self.setCurrent(current)

    def current(self) -> str:
        button = self.group.checkedButton()
        return str(button.property("body")) if button is not None else ""

    def setCurrent(self, body: str) -> None:
        button = self.buttons.get(body)
        if button is not None:
            button.setChecked(True)

    def _onClicked(self, button: QToolButton) -> None:
        self.sigBodyChanged.emit(str(button.property("body")))

    def changeEvent(self, a0: QEvent | None) -> None:
        super().changeEvent(a0)
        if a0 is not None and a0.type() == QEvent.Type.PaletteChange:
            self._applyStyle()

    def _applyStyle(self) -> None:
        palette = self.palette()
        edge = mutedTextColor(palette)
        edge.setAlphaF(0.45)
        highlight = palette.highlight().color()
        self.setStyleSheet(
            "QToolButton { border: 1px solid %s; border-radius: 13px;"
            " padding: 4px 12px; background: transparent; }"
            "QToolButton:hover { border-color: %s; }"
            "QToolButton:checked { background: %s; color: %s; border-color: %s;"
            " font-weight: 600; }"
            % (
                edge.name(edge.NameFormat.HexArgb),
                highlight.name(),
                highlight.name(),
                palette.highlightedText().color().name(),
                highlight.name(),
            )
        )

    def sizeHint(self) -> QSize:
        return self.flow.sizeHint()
