"""The Optional Features dialog: status, hardware choice, install and uninstall."""

import sys
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QMessageBox

from varda.common.layout_audit import assertNothingClipped
from varda.optional_features import optional_features_dialog
from varda.optional_features.optional_features_dialog import OptionalFeaturesDialog
from varda.optional_features.torch_runtime import (
    BACKENDS,
    InstallError,
    TorchRuntime,
    platformBackends,
)

LOCKS = Path(__file__).parents[4] / "resources" / "optional_features" / "torch"
LINUX = platformBackends("Linux", "x86_64")


class _Runtime(TorchRuntime):
    def pythonPath(self) -> Path:
        return Path(sys.executable)


@pytest.fixture
def runtime(tmp_path):
    return _Runtime(
        root=tmp_path / "torch", uv=["uv"], workerDir=Path("/w"), lockDir=LOCKS
    )


def _dialog(
    qtbot, runtime, backends=LINUX, detected=BACKENDS["linux-x64-cu130"], **kwargs
):
    dialog = OptionalFeaturesDialog(runtime, backends, detected, **kwargs)
    qtbot.addWidget(dialog)
    return dialog


def test_not_installed_offers_the_detected_build_and_its_size(qtbot, runtime):
    dialog = _dialog(qtbot, runtime)
    assert "Not installed" in dialog.statusLabel.text()
    assert dialog.installButton.isEnabled() and dialog.installButton.text() == "Install"
    assert not dialog.uninstallButton.isEnabled()
    assert not dialog.cancelButton.isVisibleTo(dialog)
    assert dialog.backendBox.count() == 2
    assert dialog.selectedBackend().key == "linux-x64-cu130"
    assert "(recommended)" in dialog.backendBox.currentText()
    cuda = optional_features_dialog.formatSize(
        runtime.downloadMB(BACKENDS["linux-x64-cu130"])
    )
    assert cuda.endswith("GB") and cuda in dialog.sizeLabel.text()
    dialog.backendBox.setCurrentIndex(0)
    cpu = optional_features_dialog.formatSize(
        runtime.downloadMB(BACKENDS["linux-x64-cpu"])
    )
    assert cpu.endswith("MB") and cpu in dialog.sizeLabel.text()


def test_installed_shows_the_version_and_device(qtbot, runtime):
    runtime.writeManifest(
        BACKENDS["linux-x64-cpu"], {"torch": "2.14.1+cpu", "device": "cpu"}
    )
    dialog = _dialog(qtbot, runtime)
    assert "2.14.1" in dialog.statusLabel.text() and "CPU" in dialog.statusLabel.text()
    assert dialog.installButton.text() == "Reinstall"
    assert dialog.uninstallButton.isEnabled()
    assert dialog.selectedBackend().key == "linux-x64-cpu"  # what is installed


def test_an_outdated_install_offers_an_update(qtbot, runtime):
    runtime.writeManifest(
        BACKENDS["linux-x64-cpu"], {"torch": "2.13.0", "device": "cpu"}
    )
    manifest = runtime.manifestPath.read_text().replace(
        runtime.lockHash(BACKENDS["linux-x64-cpu"]), "old"
    )
    runtime.manifestPath.write_text(manifest)
    dialog = _dialog(qtbot, runtime)
    assert dialog.installButton.text() == "Update"
    assert "update" in dialog.statusLabel.text().lower()


def test_unsupported_computers_explain_and_cannot_install(qtbot, runtime):
    dialog = _dialog(qtbot, runtime, backends=[], detected=None)
    assert not dialog.installButton.isEnabled()
    assert "can't" in dialog.statusLabel.text()


def test_the_reason_explains_why_the_dialog_opened(qtbot, runtime):
    dialog = _dialog(qtbot, runtime, reason="Train CNN on ROIs needs PyTorch.")
    assert "Train CNN on ROIs needs PyTorch." in dialog.reasonLabel.text()
    assert dialog.reasonLabel.isVisibleTo(dialog)


class _FakeInstaller:
    def __init__(self, runtime, backend, onLog, onStep, fail=False):
        self.runtime, self.backend, self.onLog, self.onStep = (
            runtime,
            backend,
            onLog,
            onStep,
        )
        self.fail = fail

    def cancel(self):
        pass

    def run(self):
        self.onStep("Downloading PyTorch")
        self.onLog("Installed 12 packages")
        if self.fail:
            raise InstallError("no network")
        self.runtime.writeManifest(self.backend, {"torch": "2.14.1", "device": "cuda"})


def test_installing_shows_progress_and_reports_success(qtbot, runtime, monkeypatch):
    monkeypatch.setattr(optional_features_dialog, "Installer", _FakeInstaller)
    dialog = _dialog(qtbot, runtime)
    with qtbot.waitSignal(dialog.sigInstalled, timeout=5000):
        dialog.installButton.click()
    assert "Installed 12 packages" in dialog.log.toPlainText()
    assert "2.14.1" in dialog.statusLabel.text()
    assert dialog.installButton.isEnabled() and dialog.uninstallButton.isEnabled()
    assert not dialog.spinner.isVisibleTo(dialog)


def test_a_failed_install_keeps_the_log_and_says_what_went_wrong(
    qtbot, runtime, monkeypatch
):
    monkeypatch.setattr(
        optional_features_dialog,
        "Installer",
        lambda *args: _FakeInstaller(*args, fail=True),
    )
    dialog = _dialog(qtbot, runtime)
    dialog.installButton.click()
    qtbot.waitUntil(lambda: "no network" in dialog.stepLabel.text(), timeout=5000)
    assert dialog.log.isVisibleTo(dialog)  # details open on failure
    assert "Not installed" in dialog.statusLabel.text()
    assert dialog.installButton.isEnabled()


def test_uninstall_asks_first_then_removes_the_folder(qtbot, runtime, monkeypatch):
    runtime.writeManifest(
        BACKENDS["linux-x64-cpu"], {"torch": "2.14.1", "device": "cpu"}
    )
    dialog = _dialog(qtbot, runtime)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    dialog.uninstallButton.click()
    assert runtime.isInstalled()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    dialog.uninstallButton.click()
    assert not runtime.root.exists()
    assert "Not installed" in dialog.statusLabel.text()


def test_diagnostics_name_the_platform_build_and_folder(qtbot, runtime):
    dialog = _dialog(qtbot, runtime)
    text = dialog.diagnostics()
    assert str(runtime.root) in text and "linux-x64-cu130" in text


@pytest.mark.parametrize("freeMB", [500_000, 1_000])  # plenty of disk, almost none
def test_the_dialog_scrolls_on_a_short_laptop_screen(
    qtbot, runtime, monkeypatch, freeMB
):
    # the low-space note lengthens the size text, so the free disk space of
    # the machine running the test must not decide the result
    monkeypatch.setattr(optional_features_dialog, "_freeSpaceMB", lambda path: freeMB)
    dialog = _dialog(qtbot, runtime)
    assert dialog.minimumSizeHint().height() < 400  # its sections scroll
    dialog.resize(dialog.minimumSizeHint().width(), 500)
    dialog.show()
    qtbot.waitExposed(dialog)
    assertNothingClipped(dialog)
