"""Export Image: pick an image and a destination, then write it in the background."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PyQt6.QtCore import QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QWidget,
)

from varda.branding.loader import BusyDialog
from varda.common.entities import VardaRaster
from varda.common.parameter import ImageParameter
from varda.common.ui import ButtonBuilder, HBoxBuilder, VBoxBuilder
from varda.image_loading.error_box import showPlainError
from varda.image_loading.plain_errors import explainWriteError
from varda.image_loading.raster_writer import (
    SAVE_FILE_FILTER,
    resolveOutputPath,
    suggestedOutputPath,
    writeRaster,
)
from varda.common.parameter_widgets import parameterWidget
from varda.utilities.background import runInBackground


LAST_FOLDER_KEY = "export/lastFolder"
CONFIRMATION_MS = 15000


class ExportImageDialog(QDialog):
    """A bare name typed in Save as goes into the last folder exported to,
    else the image's folder; the full path is shown before and after
    writing. Names no system can take, or a folder that doesn't exist, are
    refused with a message before anything is written."""

    sigExported = pyqtSignal(object, str)  # (VardaRaster, path written)

    def __init__(
        self,
        images: Sequence[VardaRaster],
        *,
        image: VardaRaster | None = None,
        parent: QWidget | None = None,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Image")
        self.settings = settings if settings is not None else QSettings()

        self.imageParam = ImageParameter("Image", "The image to write")
        self.imageParam.setProvider(lambda: list(images))
        if image is not None:
            self.imageParam.set(image)

        self.pathEdit = QLineEdit()
        self.pathEdit.setPlaceholderText("ENVI (.img / .hdr) or GeoTIFF (.tif)")
        self.browseButton = ButtonBuilder("Browse…").onClick(self._browse)
        self.exportButton = ButtonBuilder("Export").onClick(self.accept)

        form = QFormLayout()
        form.addRow("Image", parameterWidget(self.imageParam))
        form.addRow(
            "Save as",
            HBoxBuilder(margins=0)
            .withWidget(self.pathEdit)
            .withWidget(self.browseButton),
        )
        self.setLayout(
            VBoxBuilder()
            .withLayout(form)
            .withStretch()
            .withLayout(
                HBoxBuilder()
                .withWidget(self.exportButton)
                .withStretch()
                .withWidget(ButtonBuilder("Cancel").onClick(self.reject))
            )
        )

        self._suggestPath()
        self.imageParam.sigParameterChanged.connect(lambda _: self._suggestPath())
        self.pathEdit.textChanged.connect(
            lambda text: self.exportButton.setEnabled(bool(text.strip()))
        )
        self.exportButton.setEnabled(bool(self.pathEdit.text().strip()))
        self.accepted.connect(self._export)

    def selectedImage(self) -> VardaRaster:
        return self.imageParam.get()

    def _suggestPath(self) -> None:
        image = self.selectedImage()
        if image is not None:
            self.pathEdit.setText(str(suggestedOutputPath(image)))

    def _folderForBareNames(self) -> Path:
        last = Path(str(self.settings.value(LAST_FOLDER_KEY, "")))
        if str(last) not in ("", ".") and last.is_dir():
            return last
        image = self.selectedImage()
        return suggestedOutputPath(image).parent if image is not None else Path.home()

    def accept(self) -> None:
        """Export, once the typed path resolves to somewhere writable."""
        try:
            target = resolveOutputPath(self.pathEdit.text(), self._folderForBareNames())
        except ValueError as error:
            QMessageBox.warning(self, "Export Image", str(error))
            return
        self.pathEdit.setText(str(target))
        super().accept()

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export image as", self.pathEdit.text(), SAVE_FILE_FILTER
        )
        if path:
            self.pathEdit.setText(str(Path(path)))

    def _export(self) -> None:
        image = self.selectedImage()
        path = self.pathEdit.text().strip()
        progress = BusyDialog(
            f"Writing {image.name} to {path}…",
            title="Export Image",
            parent=self.parentWidget(),
        )
        progress.show()

        def onFinished(written: Path) -> None:
            progress.close()
            self.settings.setValue(LAST_FOLDER_KEY, str(Path(path).parent))
            owner = self.parentWidget()
            if isinstance(owner, QMainWindow) and owner.statusBar() is not None:
                owner.statusBar().showMessage(
                    f"Exported {image.name} to {written}", CONFIRMATION_MS
                )
            self.sigExported.emit(image, path)

        def onFailed(error: Exception) -> None:
            progress.close()
            showPlainError(
                self.parentWidget(), "Export failed", explainWriteError(path, error)
            )

        runInBackground(
            lambda: writeRaster(image, path),
            onFinished,
            onFailed,
            description=f"Writing {image.name} to {path}",
        )
