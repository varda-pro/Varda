"""Processing > Optional Features: install, update or remove PyTorch, and
set up the Ames Stereo Pipeline (``asp_section``).

Opens on its own from the menu, or with a ``reason`` when someone picks a CNN
or ViT classifier before PyTorch is installed.
"""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from varda.branding.loader import RedrawSpinner
from varda.common.ui import PanelScrollArea
from varda.optional_features.asp_section import AspSection
from varda.optional_features.isis_section import IsisSection
from varda.utilities.background import onMainThread, runInBackground
from varda.optional_features.torch_runtime import (
    Backend,
    InstallCancelled,
    Installer,
    LOW_SPACE_FACTOR,
    TorchRuntime,
    defaultRuntime,
    detectBackend,
    platformBackends,
)

DEVICE_NAMES = {"cuda": "NVIDIA GPU", "mps": "Apple GPU", "cpu": "CPU"}


def formatSize(megabytes: float) -> str:
    if megabytes >= 1000:
        return f"{megabytes / 1000:.1f} GB"
    return f"{megabytes:.0f} MB"


def _freeSpaceMB(path: Path) -> float | None:
    for folder in (path, *path.parents):
        if folder.exists():
            return shutil.disk_usage(folder).free / 1e6
    return None


class OptionalFeaturesDialog(QDialog):
    sigInstalled = pyqtSignal()

    def __init__(
        self,
        runtime: TorchRuntime,
        backends: list[Backend],
        detected: Backend | None,
        *,
        reason: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Optional Features")
        self.runtime = runtime
        self.backends = backends
        self.detected = detected
        self._installer: Installer | None = None

        self.reasonLabel = QLabel(reason)
        self.reasonLabel.setWordWrap(True)
        self.reasonLabel.setVisible(bool(reason))

        title = QLabel("PyTorch")
        font = QFont(title.font())
        font.setBold(True)
        title.setFont(font)
        about = QLabel(
            "Needed for the CNN and ViT classifiers. Varda downloads PyTorch "
            "into its own folder; nothing else on your computer changes, and "
            "Uninstall removes it completely."
        )
        about.setWordWrap(True)
        self.statusLabel = QLabel()
        self.statusLabel.setWordWrap(True)

        self.backendBox = QComboBox()
        for backend in backends:
            label = backend.label
            if backend == detected:
                label += " (recommended)"
            self.backendBox.addItem(label, backend.key)
        self.backendBox.currentIndexChanged.connect(self._updateSize)
        self.sizeLabel = QLabel()
        self.sizeLabel.setWordWrap(True)  # the low-disk-space note makes it long
        location = QLabel(str(runtime.root))
        location.setWordWrap(True)
        location.setToolTip("Where PyTorch is installed")
        form = QFormLayout()
        form.addRow("Build:", self.backendBox)
        form.addRow("Download:", self.sizeLabel)
        form.addRow("Folder:", location)

        self.spinner = RedrawSpinner(24)
        self.spinner.setVisible(False)
        self.stepLabel = QLabel()
        self.stepLabel.setWordWrap(True)
        progress = QHBoxLayout()
        progress.addWidget(self.spinner)
        progress.addWidget(self.stepLabel, 1)

        self.detailsButton = QToolButton()
        self.detailsButton.setText("Details")
        self.detailsButton.setCheckable(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(140)
        self.log.setVisible(False)
        self.detailsButton.toggled.connect(self.log.setVisible)
        copyButton = QPushButton("Copy Diagnostics")
        copyButton.clicked.connect(self._copyDiagnostics)
        detailsRow = QHBoxLayout()
        detailsRow.addWidget(self.detailsButton)
        detailsRow.addStretch()
        detailsRow.addWidget(copyButton)

        self.installButton = QPushButton("Install")
        self.installButton.clicked.connect(self._install)
        self.cancelButton = QPushButton("Cancel Install")
        self.cancelButton.clicked.connect(self._cancel)
        self.uninstallButton = QPushButton("Uninstall")
        self.uninstallButton.clicked.connect(self._uninstall)
        # PyTorch's own buttons sit in its section; Close is the dialog's
        torchButtons = QHBoxLayout()
        torchButtons.addWidget(self.installButton)
        torchButtons.addWidget(self.cancelButton)
        torchButtons.addWidget(self.uninstallButton)
        torchButtons.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        # the sections scroll when they're taller than a laptop screen
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.reasonLabel)
        layout.addWidget(title)
        layout.addWidget(about)
        layout.addWidget(self.statusLabel)
        layout.addLayout(form)
        layout.addLayout(progress)
        layout.addLayout(torchButtons)
        layout.addLayout(detailsRow)
        layout.addWidget(self.log)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)
        self.aspSection = AspSection()
        layout.addWidget(self.aspSection)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)
        layout.addWidget(IsisSection())
        dialogLayout = QVBoxLayout(self)
        dialogLayout.addWidget(PanelScrollArea(body), 1)
        dialogLayout.addWidget(buttons)

        self.setMinimumWidth(480)
        self._setBusy(False)  # after the layout, which shows the buttons it adds
        self._refresh()
        # Open tall enough for the wrapped text (the minimum size ignores it)
        self.resize(self.sizeHint())

    # --- state ---------------------------------------------------------------

    def selectedBackend(self) -> Backend | None:
        key = self.backendBox.currentData()
        return next((b for b in self.backends if b.key == key), None)

    def _refresh(self) -> None:
        runtime = self.runtime
        installed = runtime.isInstalled()
        manifest = runtime.manifest() if installed else None
        if not self.backends:
            self.statusLabel.setText(
                f"Varda can't install PyTorch on this computer ({platform.system()} "
                f"{platform.machine()}). To use the classifiers, run Varda from "
                "source with: uv sync --extra ml"
            )
        elif manifest is not None:
            device = DEVICE_NAMES.get(manifest["device"], manifest["device"])
            text = f"Installed: PyTorch {manifest['torch']}, running on the {device}."
            if runtime.updateAvailable():
                text += " An update is available for this version of Varda."
            self.statusLabel.setText(text)
        else:
            self.statusLabel.setText("Not installed.")

        preferred = runtime.installedBackend() if installed else self.detected
        if preferred is not None:
            index = self.backendBox.findData(preferred.key)
            if index >= 0:
                self.backendBox.setCurrentIndex(index)
        if not installed:
            self.installButton.setText("Install")
        elif runtime.updateAvailable():
            self.installButton.setText("Update")
        else:
            self.installButton.setText("Reinstall")
        self.installButton.setEnabled(bool(self.backends))
        self.uninstallButton.setEnabled(installed)
        self._updateSize()

    def _updateSize(self) -> None:
        backend = self.selectedBackend()
        if backend is None:
            self.sizeLabel.setText("—")
            return
        download = self.runtime.downloadMB(backend)
        text = f"about {formatSize(download)}"
        free = _freeSpaceMB(self.runtime.root)
        if free is not None:
            text += f" ({formatSize(free)} free on this disk)"
            if free < LOW_SPACE_FACTOR * download:
                text += f". Installed, it needs up to {formatSize(LOW_SPACE_FACTOR * download)}."
        self.sizeLabel.setText(text)

    def _setBusy(self, busy: bool) -> None:
        self.spinner.setVisible(busy)
        self.installButton.setVisible(not busy)
        self.cancelButton.setVisible(busy)
        self.cancelButton.setEnabled(busy)
        self.uninstallButton.setEnabled(not busy and self.runtime.isInstalled())
        self.backendBox.setEnabled(not busy)

    # --- actions -------------------------------------------------------------

    def _install(self) -> None:
        backend = self.selectedBackend()
        if backend is None:
            return
        self.log.clear()
        self._installer = Installer(
            self.runtime,
            backend,
            onMainThread(self.log.appendPlainText),
            onMainThread(self.stepLabel.setText),
        )
        self._setBusy(True)
        runInBackground(
            self._installer.run,
            lambda _result: self._onInstalled(),
            self._onInstallError,
            description="Installing PyTorch",
        )

    def _cancel(self) -> None:
        if self._installer is not None:
            self.cancelButton.setEnabled(False)
            self.stepLabel.setText("Canceling…")
            self._installer.cancel()

    def _onInstalled(self) -> None:
        self._installer = None
        self._setBusy(False)
        self.stepLabel.setText("PyTorch is ready.")
        self._refresh()
        self.sigInstalled.emit()

    def _onInstallError(self, error: Exception) -> None:
        self._onFailed(str(error), isinstance(error, InstallCancelled))

    def _onFailed(self, message: str, cancelled: bool) -> None:
        self._installer = None
        self._setBusy(False)
        if cancelled:
            self.stepLabel.setText("Install canceled.")
        else:
            self.stepLabel.setText(f"Install failed: {message.splitlines()[0]}")
            self.detailsButton.setChecked(True)
        self._refresh()

    def _uninstall(self) -> None:
        answer = QMessageBox.question(
            self,
            "Uninstall PyTorch",
            f"Remove PyTorch and its folder?\n{self.runtime.root}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.runtime.uninstall()
        self.stepLabel.setText("PyTorch was removed.")
        self._refresh()

    def diagnostics(self) -> str:
        backend = self.selectedBackend()
        lines = [
            f"Platform: {platform.platform()} ({platform.machine()})",
            f"Recommended build: {self.detected.key if self.detected else 'none'}",
            f"Selected build: {backend.key if backend else 'none'}",
            f"Folder: {self.runtime.root}",
            f"uv: {' '.join(self.runtime.uv) or 'not found'}",
            f"Manifest: {self.runtime.manifest()}",
            "",
            self.log.toPlainText(),
        ]
        return "\n".join(lines)

    def _copyDiagnostics(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.diagnostics())

    def reject(self) -> None:
        self.aspSection.cancelRunning()
        if self._installer is not None:
            self._cancel()  # don't leave a download running with no window
        super().reject()


def openOptionalFeatures(
    parent: QWidget | None = None, reason: str = ""
) -> OptionalFeaturesDialog:
    """The dialog for this computer, with its recommended build."""
    return OptionalFeaturesDialog(
        defaultRuntime(),
        platformBackends(),
        detectBackend(),
        reason=reason,
        parent=parent,
    )
