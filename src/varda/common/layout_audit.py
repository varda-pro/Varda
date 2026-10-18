"""Find text the user can't read: labels, buttons, combo boxes and fields
that are squeezed below the size their text needs, or pushed past the right
edge of a panel (e.g. a dock's scroll area, wider than the dock).

``clippedWidgets(window)`` lists them; the layout tests use it at laptop
sizes. Text cut off on purpose, with an ellipsis, counts as readable when
the widget's tooltip holds the full text."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QRect
from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QStyle,
    QStyleOptionComboBox,
    QWidget,
    QApplication,
)
import PyQt6Ads as ads

CHECKED = (QLabel, QAbstractButton, QComboBox, QLineEdit, QAbstractSpinBox)
# their insides are drawn and elided by the widget itself
SKIPPED_INSIDE = (
    QAbstractItemView,
    QAbstractSpinBox,  # its line edit (the spin box itself is checked)
    ads.CDockAreaTitleBar,
    ads.CAutoHideSideBar,
)
SLACK = 1  # px of rounding to forgive


def plainText(text: str) -> str:
    document = QTextDocument()
    document.setHtml(text)
    return document.toPlainText()


def describe(widget: QWidget) -> str:
    text = ""
    if isinstance(widget, (QLabel, QAbstractButton, QLineEdit)):
        text = widget.text()
    elif isinstance(widget, QComboBox):
        text = widget.currentText()
    name = widget.objectName() or type(widget).__name__
    return f"{name} {plainText(text)[:60]!r}"


def _explained(widget: QWidget, text: str) -> bool:
    """Elided on purpose: the full text is in the tooltip."""
    return bool(text) and plainText(text) in plainText(widget.toolTip())


def textProblem(widget: QWidget) -> str | None:
    """Why the widget's own text doesn't fit it, if it doesn't."""
    width = widget.width()
    if isinstance(widget, QLabel):
        if not widget.text() or (widget.pixmap() and not widget.pixmap().isNull()):
            return None
        if widget.wordWrap():
            needed = widget.heightForWidth(width)
            if needed > widget.height() + SLACK:
                return f"needs {needed}px of height, has {widget.height()}"
            return None
        if width + SLACK < widget.sizeHint().width() and not _explained(
            widget, widget.text()
        ):
            return f"needs {widget.sizeHint().width()}px, has {width}"
        return None
    if isinstance(widget, QComboBox):
        text = widget.currentText()
        if widget.isEditable() or not text:
            return None
        option = QStyleOptionComboBox()
        widget.initStyleOption(option)
        style = widget.style()
        assert style is not None
        field = style.subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            widget,
        ).width()
        if not widget.itemIcon(widget.currentIndex()).isNull():
            field -= widget.iconSize().width() + 4
        needed = widget.fontMetrics().horizontalAdvance(text)
        if needed > field + SLACK and not _explained(widget, text):
            return f"text needs {needed}px, has {field}"
        return None
    minimum = widget.minimumSizeHint().width()
    if isinstance(widget, (QAbstractButton, QAbstractSpinBox, QLineEdit)) and (
        width + SLACK < minimum
    ):
        return f"needs {minimum}px, has {width}"
    return None


def clipProblem(widget: QWidget, root: QWidget) -> str | None:
    """Whether an ancestor cuts off the widget's left or right side (a
    horizontal scroll is clipping too: the user rarely notices it)."""
    rect = QRect(widget.mapTo(root, QPoint(0, 0)), widget.size())
    parent = widget.parentWidget()
    while parent is not None and parent is not root.parentWidget():
        visible = QRect(parent.mapTo(root, QPoint(0, 0)), parent.size())
        if (
            rect.left() < visible.left() - SLACK
            or rect.right() > visible.right() + SLACK
        ):
            return f"cut off by {describe(parent)}"
        if parent is root:
            break
        parent = parent.parentWidget()
    return None


def _skipped(widget: QWidget, root: QWidget) -> bool:
    parent = widget.parentWidget()
    while parent is not None and parent is not root:
        if isinstance(parent, SKIPPED_INSIDE):
            return True
        parent = parent.parentWidget()
    return False


def settleLayouts() -> None:
    """Run the layout passes Qt has queued. Showing a window can add a scroll
    bar, which narrows the panel and re-wraps its text; the layouts only catch
    up on a queued LayoutRequest, so a size read right after ``show`` can be
    one pass stale (and the audit flakes with the length of the text)."""
    for _ in range(5):  # a pass can queue another
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)


def clippedWidgets(root: QWidget) -> list[str]:
    """The visible text widgets under ``root`` that are cut off, each with
    the reason."""
    settleLayouts()
    problems = []
    for widget in root.findChildren(QWidget):
        if (
            not isinstance(widget, CHECKED)
            or not widget.isVisibleTo(root)
            or widget.width() <= 0
            or _skipped(widget, root)
        ):
            continue
        problem = textProblem(widget) or clipProblem(widget, root)
        if problem:
            problems.append(f"{describe(widget)}: {problem}")
    return problems


def assertNothingClipped(root: QWidget) -> None:
    """For tests: fail, listing them, if any text under ``root`` is cut off."""
    problems = clippedWidgets(root)
    assert not problems, "Cut off:\n" + "\n".join(problems)
