"""A message box for a ``PlainError``: the plain message, the library's own
text under Details."""

from PyQt6.QtWidgets import QMessageBox, QWidget

from varda.image_loading.plain_errors import PlainError


def showPlainError(parent: QWidget | None, title: str, error: PlainError) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(error.message)
    if error.details and error.details != error.message:
        box.setDetailedText(error.details)
    box.exec()
