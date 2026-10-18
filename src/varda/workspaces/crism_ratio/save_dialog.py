"""The CRISM Ratio workspace's prompt for what S and P save: the class
(prefilled with the reference mineral on the Ratio plot) and the name. Quick
to accept: the class is selected, so Enter saves and Esc cancels."""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

DONT_ASK_TEXT = "Don't ask; use the reference mineral"


class SaveRatioDialog(QDialog):
    """Asks for the class and the name of what is saved. ``classes`` fill
    the Class dropdown (in order, duplicates and blanks dropped);
    ``suggested`` is the class it opens with, selected."""

    def __init__(
        self,
        title: str,
        classes: Sequence[str],
        suggested: str,
        name: str,
        summary: str,
        dontAsk: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.classBox = QComboBox()
        self.classBox.setEditable(True)
        # Enter saves rather than adding the text to the list
        self.classBox.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # a long mineral name scrolls in the field instead of widening the dialog
        self.classBox.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.classBox.setMinimumContentsLength(24)
        self.classBox.addItems(list(dict.fromkeys(c for c in classes if c.strip())))
        self.classBox.setCurrentText(suggested)
        # Enter saves: the line edit's inline completion would keep the key
        # when the text matches a listed class
        edit = self.classBox.lineEdit()
        assert edit is not None
        edit.returnPressed.connect(self._onReturn)
        self.classBox.setToolTip(
            "The class (ROI Manager's Class column): the reference mineral on "
            "the Ratio plot, a class already used, or any name"
        )
        self._defaultName = name
        self.nameEdit = QLineEdit(name)
        self.nameEdit.setMinimumWidth(120)
        self.summaryLabel = QLabel(summary)
        self.summaryLabel.setWordWrap(True)
        self.dontAskCheck = QCheckBox(DONT_ASK_TEXT)
        self.dontAskCheck.setChecked(dontAsk)
        self.dontAskCheck.setToolTip(
            "S and P then save at once with the reference mineral (or the "
            "last class used); Shift+S and Shift+P always ask"
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        assert save is not None
        save.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.addRow("Class:", self.classBox)
        form.addRow("Name:", self.nameEdit)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.summaryLabel)
        layout.addWidget(self.dontAskCheck)
        layout.addWidget(buttons)

    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        self.classBox.setFocus()
        edit = self.classBox.lineEdit()
        if edit is not None:
            edit.selectAll()

    def _onReturn(self) -> None:
        if self.isVisible():  # once, whoever else sees the key
            self.accept()

    def className(self) -> str:
        return self.classBox.currentText().strip()

    def itemName(self) -> str:
        """The name typed, or the automatic one when it was emptied."""
        return self.nameEdit.text().strip() or self._defaultName

    def dontAsk(self) -> bool:
        return self.dontAskCheck.isChecked()
