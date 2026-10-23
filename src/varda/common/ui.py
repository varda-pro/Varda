from __future__ import annotations

import functools
from collections.abc import Iterator
from contextlib import contextmanager
from typing_extensions import override
from typing import Callable

from PyQt6.QtCore import Qt, QEvent, QPoint, QRect, QSize, pyqtSignal
from PyQt6.QtWidgets import (
    QToolButton,
    QComboBox,
    QLayoutItem,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QWidget,
    QSplitter,
    QLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QMainWindow,
    QLabel,
    QDialog,
    QTabBar,
    QTabWidget,
    QSlider,
    QFrame,
    QSpinBox,
    QSizePolicy,
    QScrollArea,
    QAbstractScrollArea,
    QApplication,
    QMenu,
)
from PyQt6.QtGui import (
    QColor,
    QMouseEvent,
    QPaintEvent,
    QPalette,
    QPixmap,
    QResizeEvent,
)
import PyQt6Ads as ads


class WrapperWidget(QWidget):
    def __init__(self, layout: QLayout, parent: QWidget | None = None):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)


class VBoxBuilder(QVBoxLayout):
    def __init__(self, alignment=Qt.AlignmentFlag(0), margins=None):
        super().__init__()
        self.setAlignment(alignment)
        if margins is not None:
            self.setContentsMargins(margins, margins, margins, margins)

    def withWidget(
        self, widget: QWidget | None, stretch: int = 0, alignment=Qt.AlignmentFlag(0)
    ) -> VBoxBuilder:
        self.addWidget(widget, stretch, alignment)
        return self

    def withLayout(self, layout: QLayout | None, stretch: int = 0) -> VBoxBuilder:
        self.addLayout(layout, stretch)
        return self

    def withStretch(self, stretch: int = 0) -> VBoxBuilder:
        self.addStretch(stretch)
        return self


class HBoxBuilder(QHBoxLayout):
    def __init__(self, alignment=Qt.AlignmentFlag(0), margins=None):
        super().__init__()
        self.setAlignment(alignment)
        if margins is not None:
            self.setContentsMargins(margins, margins, margins, margins)

    def withWidget(
        self, widget: QWidget | None, stretch: int = 0, alignment=Qt.AlignmentFlag(0)
    ) -> HBoxBuilder:
        self.addWidget(widget, stretch, alignment)
        return self

    def withLayout(self, layout: QLayout | None, stretch: int = 0) -> HBoxBuilder:
        self.addLayout(layout, stretch)
        return self

    def withStretch(self, stretch: int = 0) -> HBoxBuilder:
        self.addStretch(stretch)
        return self


class FlowLayout(QLayout):
    """Lays its items out like words in a paragraph: as many in a row as
    fit, then the next row. A row of buttons or fields in it wraps instead
    of being cut off when its panel is narrow; its minimum width is its
    widest item, its preferred width all of them in one row."""

    def __init__(self, parent: QWidget | None = None, margins: int = 0) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(margins, margins, margins, margins)

    def withWidget(self, widget: QWidget) -> FlowLayout:
        self.addWidget(widget)
        return self

    def addItem(self, a0: QLayoutItem | None) -> None:
        if a0 is not None:
            self._items.append(a0)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, a0: int) -> int:
        return self._arrange(QRect(0, 0, a0, 0), move=False)

    def setGeometry(self, a0: QRect) -> None:
        super().setGeometry(a0)
        self._arrange(a0, move=True)

    def _visible(self) -> list[QLayoutItem]:
        return [item for item in self._items if not item.isEmpty()]

    def _spacing(self, orientation: Qt.Orientation) -> int:
        if self.spacing() >= 0:
            return self.spacing()
        style = (self.parentWidget() or QWidget()).style()
        assert style is not None
        metric = (
            QStyle.PixelMetric.PM_LayoutHorizontalSpacing
            if orientation == Qt.Orientation.Horizontal
            else QStyle.PixelMetric.PM_LayoutVerticalSpacing
        )
        spacing = style.pixelMetric(metric)
        return spacing if spacing >= 0 else 6  # macOS leaves it to the widgets

    def _margins(self) -> QSize:
        m = self.contentsMargins()
        return QSize(m.left() + m.right(), m.top() + m.bottom())

    def sizeHint(self) -> QSize:
        items = self._visible()
        gap = self._spacing(Qt.Orientation.Horizontal)
        width = sum(i.sizeHint().width() for i in items) + gap * max(len(items) - 1, 0)
        height = max((i.sizeHint().height() for i in items), default=0)
        return QSize(width, height) + self._margins()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._visible():
            size = size.expandedTo(item.minimumSize())
        return size + self._margins()

    @staticmethod
    def _itemSize(item: QLayoutItem, available: int) -> QSize:
        """An item's size in a row ``available`` wide: its preferred width
        (no wider than the row, no narrower than its minimum), and a wrapped
        label's height at that width, so it keeps all its lines."""
        size = item.sizeHint()
        width = max(min(size.width(), available), item.minimumSize().width())
        if item.hasHeightForWidth():
            return QSize(width, item.heightForWidth(width))
        return QSize(width, size.height())

    def _arrange(self, rect: QRect, move: bool) -> int:
        """Place the items (with ``move``) in ``rect``; the height they take."""
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        xGap = self._spacing(Qt.Orientation.Horizontal)
        yGap = self._spacing(Qt.Orientation.Vertical)
        rows: list[list[QLayoutItem]] = [[]]
        x = area.x()
        for item in self._visible():
            width = min(item.sizeHint().width(), area.width())
            if rows[-1] and x + width > area.right() + 1:
                rows.append([])
                x = area.x()
            rows[-1].append(item)
            x += width + xGap
        y = area.y()
        for row in rows:
            sizes = [self._itemSize(item, area.width()) for item in row]
            height = max((size.height() for size in sizes), default=0)
            x = area.x()
            for item, size in zip(row, sizes):
                if move:
                    top = y + (height - size.height()) // 2
                    item.setGeometry(QRect(QPoint(x, top), size))
                x += size.width() + xGap
            y += height + yGap
        return y - yGap - rect.y() + m.bottom()


class ElidingComboBox(QComboBox):
    """A combo box that can shrink below its longest item: the current text
    is elided ("Moon: Lunar Orbiter…") and then, in full, in the tooltip
    (after any tooltip of its own, set as usual with ``setToolTip``)."""

    def __init__(self, parent: QWidget | None = None, minimumCharacters: int = 12):
        super().__init__(parent)
        self._hint = ""
        self.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.setMinimumContentsLength(minimumCharacters)
        self.currentTextChanged.connect(lambda _: self._updateToolTip())

    def setToolTip(self, a0: str | None) -> None:
        self._hint = a0 or ""
        self._updateToolTip()

    def _textRect(self, option: QStyleOptionComboBox) -> QRect:
        style = self.style()
        assert style is not None
        rect = style.subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        if not option.currentIcon.isNull():
            rect.setLeft(rect.left() + option.iconSize.width() + 4)
        return rect

    def _elided(self, option: QStyleOptionComboBox) -> str:
        return self.fontMetrics().elidedText(
            option.currentText,
            Qt.TextElideMode.ElideRight,
            self._textRect(option).width() - 2,
        )

    def isElided(self) -> bool:
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        return self._elided(option) != option.currentText

    def _updateToolTip(self) -> None:
        text = self.currentText() if self.isElided() else ""
        toolTip = "\n\n".join(t for t in (text, self._hint) if t)
        if toolTip != self.toolTip():
            super().setToolTip(toolTip)

    def resizeEvent(self, e: QResizeEvent | None) -> None:
        super().resizeEvent(e)
        self._updateToolTip()

    def paintEvent(self, e: QPaintEvent | None) -> None:
        if self.isEditable():
            super().paintEvent(e)
            return
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        if self.currentIndex() < 0 and self.placeholderText():
            option.palette.setBrush(
                QPalette.ColorRole.ButtonText,
                option.palette.brush(QPalette.ColorRole.PlaceholderText),
            )
            option.currentText = self.placeholderText()
        option.currentText = self._elided(option)
        # here, as items can change with signals blocked (no currentTextChanged)
        self._updateToolTip()
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)


class GroupBoxBuilder(QGroupBox):
    def __init__(self, title: str, layout: QLayout):
        super().__init__(title)
        self.setLayout(layout)


class SplitterBuilder(QSplitter):
    def __init__(self, orientation: Qt.Orientation):
        super().__init__()
        self.setOrientation(orientation)

    def withWidget(self, widget: QWidget, stretchFactor: int = 1) -> SplitterBuilder:
        self.addWidget(widget)
        self.setStretchFactor(self.indexOf(widget), stretchFactor)
        return self

    def withLayout(self, layout: QLayout, stretchFactor: int = 1) -> SplitterBuilder:
        wrapper = WrapperWidget(layout)
        self.addWidget(wrapper)
        self.setStretchFactor(self.indexOf(wrapper), stretchFactor)
        return self


class _ScrollContent(QWidget):
    """Content wrapper for ScrollArea.

    Returns sizeHint() as minimumSizeHint() so that setWidgetResizable(True)
    never squishes the content below its natural size on either axis.
    """

    def minimumSizeHint(self):
        return self.sizeHint()


class VerticalScrollArea(QScrollArea):
    """A vertical-only scroll area whose width adapts to its content.

    Design goals:
    - Scrolls vertically when content is taller than the viewport.
    - Never scrolls horizontally; instead, the scroll area itself expands
      to match the content's natural width, so the parent layout accommodates it.
    - Content is never squished below its preferred size on either axis.

    How it works:
    - setWidgetResizable(True) lets the content grow/shrink with the viewport,
      but sizes the widget to max(viewport_size, minimumSizeHint). _ScrollContent
      overrides minimumSizeHint to return sizeHint, so the content is never
      squished below its natural size.
    - sizeHint/minimumSizeHint on this ScrollArea forward the content's width
      to the parent layout, so the scroll area takes up the right amount of
      horizontal space.
    - An event filter on _ScrollContent catches LayoutRequest events (fired
      when widgets are added/removed dynamically) and calls updateGeometry()
      on the ScrollArea, which notifies the parent layout to re-query the
      updated width.
    """

    def __init__(self, contents: QWidget | QLayout, parent: QWidget | None = None):
        super().__init__(parent)
        container = _ScrollContent()
        container.installEventFilter(self)
        if isinstance(contents, QLayout):
            container.setLayout(contents)
        else:
            inner = QVBoxLayout()
            inner.setContentsMargins(0, 0, 0, 0)
            inner.setSpacing(0)
            inner.addWidget(contents)
            container.setLayout(inner)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(container)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()
        return super().eventFilter(obj, event)

    def sizeHint(self) -> QSize:
        widget = self.widget()
        if widget is not None:
            return QSize(widget.sizeHint().width(), super().sizeHint().height())
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        widget = self.widget()
        if widget is not None:
            return QSize(
                widget.minimumSizeHint().width(), super().minimumSizeHint().height()
            )
        return super().minimumSizeHint()


class _PanelContents(QWidget):
    """Holds a ``PanelScrollArea``'s panel. Asked how tall it must be at a
    width, it gives the panel's minimum height there, not its preferred
    one, so a panel with wrapping text squeezes its tables and plots before
    it scrolls (QScrollArea sizes such panels by ``heightForWidth``)."""

    def __init__(self, panel: QWidget) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel)

    def heightForWidth(self, a0: int) -> int:
        layout = self.layout()
        assert layout is not None
        return layout.minimumHeightForWidth(a0)


def _reach(layout: QLayout, part: QWidget | QLayout) -> int | None:
    """The height of a column of rows down to and including ``part``, by
    their preferred heights; None when ``part`` isn't in it."""
    height = layout.contentsMargins().top()
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        # QLayoutItem's widget(): a QStackedLayout's own widget() takes an index
        if part in (QLayoutItem.widget(item), item.layout()):
            return height + item.sizeHint().height()
        inner = item.layout()
        within = _reach(inner, part) if inner is not None else None
        if within is not None:
            return height + within
        if not item.isEmpty():
            height += item.sizeHint().height() + max(layout.spacing(), 0)
    return None


class PanelScrollArea(QScrollArea):
    """A panel (a form, a list of details, a table with buttons) that
    scrolls vertically when it can't get any shorter, and never sideways:
    it can get as narrow as the panel's minimum width, no narrower, so the
    panel's labels wrap, its combo boxes elide and its button rows wrap
    instead of disappearing off the edge. ``VardaDockWidget`` puts panels
    in one."""

    def __init__(self, panel: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.panel = panel
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        contents = _PanelContents(panel)
        self.setWidget(contents)
        contents.installEventFilter(self)
        self._kept: QWidget | QLayout | None = None

    def keepInView(self, part: QWidget | QLayout) -> None:
        """Never get shorter than the panel's top down to ``part`` (its
        essential rows, e.g. a color map with its handles), a widget or
        layout in the panel's column of rows: cut through by the bottom
        edge, a drawn control looks whole with its handles hidden."""
        self._kept = part
        self.updateGeometry()

    def _keptHeight(self) -> int:
        """How far down the panel ``part`` reaches, by the rows' preferred
        heights (known before the panel is laid out: a dock manager reads
        the minimum size once, when the dock is added)."""
        layout = self.panel.layout()
        if self._kept is None or layout is None:
            return 0
        return _reach(layout, self._kept) or 0

    def eventFilter(self, a0, a1) -> bool:
        if a0 is self.widget() and a1 is not None:
            if a1.type() == QEvent.Type.LayoutRequest:
                self.updateGeometry()  # its minimum width may have changed
        return super().eventFilter(a0, a1)

    def _withScrollBar(self, width: int) -> int:
        bar = self.verticalScrollBar()
        extra = bar.sizeHint().width() if bar is not None else 0
        return width + extra + 2 * self.frameWidth()

    def minimumSizeHint(self) -> QSize:
        width = self._withScrollBar(self.panel.minimumSizeHint().width())
        height = max(
            super().minimumSizeHint().height(),
            self._keptHeight() + 2 * self.frameWidth(),
        )
        return QSize(width, height)

    def sizeHint(self) -> QSize:
        hint = self.panel.sizeHint()
        # a tall panel asks for most of the screen, not more: a dialog
        # holding it then opens on screen, and scrolls
        screen = self.screen()
        tallest = (
            screen.availableGeometry().height() * 3 // 4 if screen else hint.height()
        )
        return QSize(self._withScrollBar(hint.width()), min(hint.height(), tallest))


class FormBuilder(QFormLayout):
    def __init__(self):
        super().__init__()

    def withRow(self, label: str, widget: QWidget) -> FormBuilder:
        self.addRow(label, widget)
        return self


class ButtonBuilder(QPushButton):
    def __init__(self, label: str):
        super().__init__(label)

    def onClick(self, callback) -> ButtonBuilder:
        self.clicked.connect(callback)
        return self

    def default(self) -> ButtonBuilder:
        self.setDefault(True)
        return self

    def withToolTip(self, text: str) -> ButtonBuilder:
        self.setToolTip(text)
        return self


class FoldingSection(QWidget):
    """A titled section whose content folds away under its header. The
    content is shown or hidden through the layout, so content that wraps
    (a ``FlowLayout``) gets the height its rows need. ``sigToggledByUser``
    says the user clicked the header (not ``expand``/``collapse``)."""

    sigToggledByUser = pyqtSignal(bool)

    def __init__(self, title: str, content: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self._title = title
        self._content = content
        self.header = QToolButton(self)
        self.header.setCheckable(True)
        self.header.setAutoRaise(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.header.clicked.connect(self._onClicked)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(content)
        self.collapse()

    def content(self) -> QWidget:
        return self._content

    def isExpanded(self) -> bool:
        return self.header.isChecked()

    def expand(self) -> None:
        self._show(True)

    def collapse(self) -> None:
        self._show(False)

    def _show(self, expanded: bool) -> None:
        self.header.setChecked(expanded)
        self.header.setText(f"{'▼' if expanded else '▶'} {self._title}")
        self._content.setVisible(expanded)

    def _onClicked(self, checked: bool) -> None:
        self._show(checked)
        self.sigToggledByUser.emit(checked)


class SectionBox(QWidget):
    def __init__(
        self,
        name: str | None = None,
        content: QWidget | QLayout | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.title = name
        self.frame = QFrame()
        self.frame.setFrameShape(QFrame.Shape.NoFrame)
        self.frame.setObjectName("SectionBox")
        self.frame.setStyleSheet("""
        QFrame#SectionBox {
            border: 2px solid palette(mid); 
            border-radius: 6px;
        }
        """)
        self.content: QWidget | QLayout | None = content
        self.frameLayout = QVBoxLayout()
        if isinstance(content, QLayout):
            self.frameLayout.addLayout(content)
        elif isinstance(content, QWidget):
            self.frameLayout.addWidget(content)
        elif content is not None:
            raise TypeError("SectionBox requires a QWidget or QLayout (or None)")
        self.frame.setLayout(self.frameLayout)

        self.setLayout(
            VBoxBuilder()
            .withWidget(QLabel(name) if name else None)
            .withWidget(self.frame)
        )

    def setContent(self, content: QWidget | QLayout | None):
        # clear existing items
        self._clearLayout(self.frameLayout)
        self.content = content
        # now set new layout
        if content is None:
            return
        elif isinstance(content, QLayout):
            self.frameLayout.addLayout(content)
        elif isinstance(content, QWidget):
            self.frameLayout.addWidget(content)
        else:
            raise TypeError("SectionBox requires a QWidget or QLayout (or None)")

    def _clearLayout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if w := item.widget():
                w.setParent(None)
            elif nested := item.layout():
                self._clearLayout(nested)


class FilePathBox(QWidget):
    def __init__(
        self,
        defaultPath: str = "",
        fileFilter: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.fileFilter = fileFilter
        self.result: str | None

        # setup UI
        self.filePathText = QLineEdit(defaultPath)
        self.openFileSelectorButton = QPushButton("Browse Files...")
        layout = QHBoxLayout()
        layout.addWidget(self.filePathText)
        layout.addWidget(self.openFileSelectorButton)
        self.setLayout(layout)

        # connect signals
        self.openFileSelectorButton.clicked.connect(self.getFilePath)
        self.filePathText.textChanged.connect(self.onTextChanged)

    def getFilePath(self):
        filename, _ = QFileDialog.getOpenFileName(
            None,
            "Select Image File",
            "",
            self.fileFilter,
        )
        if filename:
            self.filePathText.setText(filename)

    def onTextChanged(self):
        self.result = self.filePathText.text()


class FileInputDialog(QDialog):
    """Dialog for requesting a file path from the user."""

    def __init__(
        self, message="Select a file:", defaultPath="", fileFilter=None, parent=None
    ):
        super().__init__(parent)
        self.setWindowTitle("File Selection")

        # Message label
        self.label = QLabel(message)

        self.fileInput = FilePathBox(defaultPath, fileFilter, parent=self)

        # OK and Cancel buttons
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.accept)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)

        # Layouts
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.label)
        main_layout.addWidget(self.fileInput)
        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)

    @staticmethod
    def getFilePath(
        message="Select a file:", default_path="", fileFilter=None, parent=None
    ):
        """
        Static method to show the dialog and return the selected file path.

        Args:
            message: The message to display.
            default_path: The default path to show.
            fileFilter: Optional filter for file types.
            parent: Optional parent widget.

        Returns:
            str: The selected file path, or None if canceled.
        """
        dialog = FileInputDialog(message, default_path, fileFilter, parent)
        if dialog.exec():
            return dialog.fileInput.result  # Return the selected path
        return None  # Return None if canceled


class DetachableTabWidget(QTabWidget):
    """Tabs that work like a browser's: drag them into order, drag one out of
    the bar (or double-click it) to open it in a window of its own, close one
    with its button or a middle click. Closing the window puts it back."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabBar(self.DetachableTabBar(self))
        self.setTabsClosable(True)
        self.setMovable(True)
        self.detachedWindows = {}

    def discardTab(self, widget: QWidget) -> None:
        """Take a widget out of the tabs — docked, or detached into its own
        window — without destroying it. The caller decides its fate."""
        window = self.detachedWindows.pop(widget, None)
        if window is not None:
            window.discard()
        else:
            index = self.indexOf(widget)
            if index != -1:
                self.removeTab(index)
        widget.setParent(None)

    def detachTab(self, index, at: QPoint | None = None):
        """Open tab ``index`` in its own window (with its title bar at the
        screen position ``at``, if given)."""
        widget = self.widget(index)
        if widget is None:
            return

        label = self.tabText(index)
        size = widget.size()
        self.removeTab(index)

        detachedTabWindow = self.DetachedTabWindow(label, widget, self)
        detachedTabWindow.resize(size)
        if at is not None:
            detachedTabWindow.move(at - QPoint(60, 10))
        detachedTabWindow.show()

        self.detachedWindows[widget] = detachedTabWindow

    def reattachTab(self, widget, label):
        del self.detachedWindows[widget]
        self.addTab(widget, label)
        self.setCurrentWidget(widget)

    class DetachableTabBar(QTabBar):
        TEAR_OFF = 40  # px the pointer must leave the bar by to tear a tab off

        def __init__(self, parentTabWidget):
            super().__init__()
            self.parentTabWidget = parentTabWidget
            self._dragging = False

        def mousePressEvent(self, event):
            index = self.tabAt(event.position().toPoint())
            if event.button() == Qt.MouseButton.MiddleButton and index != -1:
                self.parentTabWidget.tabCloseRequested.emit(index)
                return
            self._dragging = event.button() == Qt.MouseButton.LeftButton and index != -1
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event):
            super().mouseMoveEvent(event)
            margin = self.TEAR_OFF
            outside = (
                not self.rect()
                .adjusted(-margin, -margin, margin, margin)
                .contains(event.position().toPoint())
            )
            if self._dragging and outside:
                self._dragging = False
                # end the bar's own drag before the tab leaves it
                super().mouseReleaseEvent(
                    QMouseEvent(
                        QEvent.Type.MouseButtonRelease,
                        event.position(),
                        event.globalPosition(),
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.NoButton,
                        event.modifiers(),
                    )
                )
                self.parentTabWidget.detachTab(
                    self.currentIndex(), event.globalPosition().toPoint()
                )

        def mouseReleaseEvent(self, event):
            self._dragging = False
            super().mouseReleaseEvent(event)

        def mouseDoubleClickEvent(self, event):
            index = self.tabAt(event.pos())
            if index != -1:
                self.parentTabWidget.detachTab(index)
            super().mouseDoubleClickEvent(event)

    class DetachedTabWindow(QMainWindow):
        def __init__(self, label, widget, tab_widget):
            super().__init__()
            self.setWindowTitle(label)
            self.tabWidget = tab_widget
            self.label = label
            self.widget = widget
            self.newTabWidget = QTabWidget()
            self.setCentralWidget(self.newTabWidget)
            self.newTabWidget.addTab(self.widget, self.label)
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        def discard(self) -> None:
            """Close without reattaching the widget to the main tab widget."""
            self.newTabWidget.removeTab(0)
            self.widget = None
            self.close()

        def closeEvent(self, event):
            # Closing the window normally puts the widget back into the main tabs
            if self.widget is not None:
                self.tabWidget.reattachTab(self.widget, self.label)
            super().closeEvent(event)


class FloatSlider(QSlider):
    sigFloatValueChanged: pyqtSignal = pyqtSignal(float)

    def __init__(
        self, precision=3, range: tuple[float, float] | None = None, parent=None
    ):
        super().__init__(parent)
        self.precision = precision
        self.valueChanged.connect(self.onValueChanged)

        if range is not None:
            self.setRange(range[0], range[1])

    @override
    def setRange(self, min: float, max: float) -> None:
        min = min * pow(10, self.precision)
        max = max * pow(10, self.precision)
        super().setRange(int(min), int(max))

    @override
    def setValue(self, a0: float) -> None:
        super().setValue(int(a0 * pow(10, self.precision)))

    def onValueChanged(self, value):
        floatVal = value / pow(10, self.precision)
        self.sigFloatValueChanged.emit(floatVal)


class SpinBoxBuilder:
    """Convenience class for building QSpinBox widgets in a declarative style.

    Chain together methods to configure the QSpinBox, and call build() to get the final widget."""

    def __init__(self, parent: QWidget | None = None) -> None:
        self.widget = QSpinBox(parent)

    def sizePolicy(
        self, horizontal: QSizePolicy.Policy, vertical: QSizePolicy.Policy
    ) -> SpinBoxBuilder:
        self.widget.setSizePolicy(horizontal, vertical)
        return self

    def minimumSize(self, width: int, height: int) -> SpinBoxBuilder:
        self.widget.setMinimumSize(width, height)
        return self

    def range(self, range: tuple[int, int]) -> SpinBoxBuilder:
        self.widget.setRange(range[0], range[1])
        return self

    def default(self, value: int) -> SpinBoxBuilder:
        self.widget.setValue(value)
        return self

    def binding(self, onValueChanged: Callable[[int], None]) -> SpinBoxBuilder:
        self.widget.valueChanged.connect(onValueChanged)
        return self

    def build(self) -> QSpinBox:
        return self.widget


class SliderBuilder:
    """Convenience class for building QSlider widgets in a declarative style.

    Chain together methods to configure the QSlider, and call build() to get the final widget.
    """

    def __init__(self, parent=None):
        self.widget = QSlider(parent)

    def orientation(self, orientation: Qt.Orientation) -> SliderBuilder:
        self.widget.setOrientation(orientation)
        return self

    def range(self, range: tuple[int, int]) -> SliderBuilder:
        self.widget.setRange(range[0], range[1])
        return self

    def default(self, value: int) -> SliderBuilder:
        self.widget.setValue(value)
        return self

    def binding(self, onValueChanged: Callable[[int], None]) -> SliderBuilder:
        self.widget.valueChanged.connect(onValueChanged)
        return self

    def build(self) -> QSlider:
        return self.widget


### PyQt6Ads Stuff ##

ads.CDockManager.setConfigFlags(ads.CDockManager.eConfigFlag.DefaultOpaqueConfig)
ads.CDockManager.setConfigFlag(
    ads.CDockManager.eConfigFlag.DockAreaHasCloseButton, False
)
ads.CDockManager.setConfigFlag(
    ads.CDockManager.eConfigFlag.ActiveTabHasCloseButton, False
)
ads.CDockManager.setAutoHideConfigFlags(
    ads.CDockManager.eAutoHideFlag.DefaultAutoHideConfig
)


class VardaDockWidget(ads.CDockWidget):
    """A dock whose contents are never cut off: a panel that isn't a scroll
    area of its own (a table, a plot, an image view) goes into a
    ``PanelScrollArea``, and the dock gets no narrower than the panel's
    minimum width. (ADS's own scroll area scrolls sideways instead, where
    users don't notice it.) So give it panels that can get narrow: wrap
    text, elide combo boxes, put button rows in a ``FlowLayout``.
    ``widget()`` is still the panel given (as with ADS's scroll area)."""

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(title, parent)
        self._panel: QWidget | None = None  # the panel it put in a scroll area
        self.setMinimumSizeHintMode(
            ads.CDockWidget.eMinimumSizeHintMode.MinimumSizeHintFromContent
        )

    def setWidget(
        self,
        widget: QWidget | None,
        InsertMode: ads.CDockWidget.eInsertMode = ads.CDockWidget.eInsertMode.AutoScrollArea,  # noqa: N803 (ADS's name)
    ) -> None:
        """Show ``widget`` (``InsertMode`` is ignored: see the class)."""
        self._panel = None
        if widget is not None and not isinstance(widget, QAbstractScrollArea):
            self._panel, widget = widget, PanelScrollArea(widget)
        super().setWidget(widget, ads.CDockWidget.eInsertMode.ForceNoScrollArea)

    def widget(self) -> QWidget | None:
        return self._panel or super().widget()

    def keepInView(self, part: QWidget | QLayout) -> None:
        """Never get so short that ``part`` of the panel is cut off (see
        ``PanelScrollArea.keepInView``)."""
        area = super().widget()
        assert isinstance(area, PanelScrollArea), "for a panel set with setWidget"
        area.keepInView(part)

    def withWidget(self, widget: QWidget) -> VardaDockWidget:
        self.setWidget(widget)
        return self


def showDock(dock: ads.CDockWidget) -> None:
    """Open ``dock`` wherever it lives: back in its place if it was closed,
    slid out if it hides at an edge, brought to the front if it is a tab."""
    dock.toggleView(True)
    container = dock.autoHideDockContainer() if dock.isAutoHide() else None
    if container is not None:
        container.collapseView(False)
    else:
        dock.setAsCurrentTab()
        dock.raise_()


def dockManagerOf(widget: QWidget) -> ads.CDockManager | None:
    """The dock manager of the dock ``widget`` is in (None outside one)."""
    parent = widget.parentWidget()
    while parent is not None and not isinstance(parent, ads.CDockWidget):
        parent = parent.parentWidget()
    return parent.dockManager() if parent is not None else None


def panelsMenu(manager: ads.CDockManager, parent: QWidget | None = None) -> QMenu:
    """ "Panels": every panel of ``manager`` by name, checked when open.
    Choosing one opens it (``showDock``), so a panel closed by mistake comes
    back."""
    menu = QMenu("Panels", parent)
    docks = sorted(manager.dockWidgetsMap().values(), key=lambda d: d.windowTitle())
    for dock in docks:
        action = menu.addAction(dock.windowTitle())
        assert action is not None
        action.setCheckable(True)
        action.setChecked(not dock.isClosed())
        action.triggered.connect(lambda _checked, d=dock: showDock(d))
    return menu


MUTED_TEXT_WEIGHT = 0.65  # share of the normal text color in muted text


def mutedTextColor(palette: QPalette) -> QColor:
    """Secondary text: the text color blended towards the window color, so
    it reads as quieter than normal text but stays legible on light and dark
    themes (unlike palette(mid), which is near the background in macOS dark
    mode)."""
    text = palette.color(QPalette.ColorRole.WindowText)
    window = palette.color(QPalette.ColorRole.Window)
    w = MUTED_TEXT_WEIGHT
    return QColor.fromRgbF(
        w * text.redF() + (1 - w) * window.redF(),
        w * text.greenF() + (1 - w) * window.greenF(),
        w * text.blueF() + (1 - w) * window.blueF(),
    )


class MutedLabel(QLabel):
    """A label for hints and descriptions, in ``mutedTextColor``; it follows
    the system switching between light and dark mode."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._applyColor()

    def changeEvent(self, a0: QEvent | None) -> None:
        super().changeEvent(a0)
        if a0 is not None and a0.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ):
            self._applyColor()

    def _applyColor(self) -> None:
        color = mutedTextColor(QApplication.palette())
        palette = self.palette()
        if palette.color(QPalette.ColorRole.WindowText) != color:  # no loop
            palette.setColor(QPalette.ColorRole.WindowText, color)
            self.setPalette(palette)


def notListedLabel(note: str = "", parent: QWidget | None = None) -> MutedLabel:
    """A wrapped muted note under a dropdown of images, naming the images it
    leaves out and why (``grid.ImageChoices.note``); hidden while empty."""
    label = MutedLabel(note, parent)
    label.setWordWrap(True)
    label.setVisible(bool(note))
    return label


def openOwnedDialog(dialog: QDialog) -> QDialog:
    """Open a window-modal dialog that cleans up after itself. The dialog must
    have a parent: with none, nothing keeps it alive, and garbage collection
    closes it a moment after it appears."""
    assert dialog.parent() is not None, "give the dialog a parent window"
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.open()
    return dialog


class ScaledPixmapLabel(QLabel):
    """A label showing a picture as large as it fits, keeping its shape, and
    sharp on high-density screens: it keeps the full-size picture and scales
    it to the label's size in device pixels whenever the label resizes (a
    plain ``QLabel`` shows the pixmap it was given, blurred when stretched).
    Text set with ``setText`` replaces the picture."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # the picture follows the label's size, never the other way round
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def setFullPixmap(self, pixmap: QPixmap) -> None:
        self._full = pixmap
        self._rescale()

    def fullPixmap(self) -> QPixmap | None:
        return self._full

    def setText(self, a0: str | None) -> None:
        self._full = None
        super().setText(a0)

    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        super().resizeEvent(a0)
        self._rescale()

    def _rescale(self) -> None:
        if self._full is None or self._full.isNull():
            return
        ratio = self.devicePixelRatioF()
        scaled = self._full.scaled(
            self.size() * ratio,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(ratio)
        super().setPixmap(scaled)


@contextmanager
def busyCursor() -> Iterator[None]:
    """The system's wait cursor while work blocks the GUI thread (building a
    workspace and drawing its views): nothing repaints then, so the animated
    indicator can't say Varda is busy."""
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


def showsBusyCursor[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Run ``method`` (e.g. a workspace's ``__init__``) under ``busyCursor``."""

    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with busyCursor():
            return method(*args, **kwargs)

    return wrapper
