"""Export Image: pick an image and a destination, then write it."""

import numpy as np
import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QDialog, QLabel, QMainWindow, QMessageBox

from varda.common.layout_audit import assertNothingClipped
from varda.image_loading import export_image_dialog

from varda.image_loading.export_image_dialog import ExportImageDialog
from varda.image_loading.image_loading_service import loadRaster, openDataSource
from varda.image_loading.raster_writer import writeRaster
from varda.utilities.debug import generate_random_image


def test_dialog_preselects_the_given_image_and_suggests_its_name(qtbot):
    first, second = (
        generate_random_image((20, 20, 10)),
        generate_random_image((20, 20, 10)),
    )
    dialog = ExportImageDialog([first, second], image=second)
    qtbot.addWidget(dialog)

    assert dialog.selectedImage() is second
    assert dialog.pathEdit.text().endswith(f"{second.name}.img")


def test_export_is_disabled_without_a_path(qtbot):
    image = generate_random_image((20, 20, 10))
    dialog = ExportImageDialog([image], image=image)
    qtbot.addWidget(dialog)

    dialog.pathEdit.setText("")
    assert not dialog.exportButton.isEnabled()
    dialog.pathEdit.setText("/tmp/x.img")
    assert dialog.exportButton.isEnabled()


def test_accepting_writes_the_image_and_reports_the_path(qtbot, tmp_path):
    image = generate_random_image((20, 20, 10))
    dialog = ExportImageDialog([image], image=image)
    qtbot.addWidget(dialog)
    target = tmp_path / "exported.img"
    dialog.pathEdit.setText(str(target))

    with qtbot.waitSignal(dialog.sigExported, timeout=10_000) as blocker:
        dialog.accept()

    assert blocker.args == [image, str(target)]
    loaded = openDataSource(str(target))
    np.testing.assert_array_equal(loaded.readAllBands(), np.asarray(image.getData()))


# --- release QA #4, #10, #16, #24 -------------------------------------------


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


def _dialog(qtbot, settings, image=None):
    image = image or generate_random_image((20, 20, 10))
    dialog = ExportImageDialog([image], image=image, settings=settings)
    qtbot.addWidget(dialog)
    return dialog


def test_a_bare_name_goes_next_to_the_image_and_the_full_path_is_shown(
    qtbot, settings, tmp_path
):
    image = loadRaster(
        str(writeRaster(generate_random_image((20, 20, 10)), tmp_path / "source.img"))
    )
    dialog = _dialog(qtbot, settings, image)
    dialog.pathEdit.setText("relative name.tif")

    with qtbot.waitSignal(dialog.sigExported, timeout=10_000) as blocker:
        dialog.accept()

    written = tmp_path / "relative name.tif"
    assert blocker.args[1] == str(written)
    assert written.is_file()
    assert dialog.pathEdit.text() == str(written)


def test_a_bare_name_goes_to_the_last_export_folder(qtbot, settings, tmp_path):
    first = tmp_path / "exports"
    first.mkdir()
    dialog = _dialog(qtbot, settings)
    dialog.pathEdit.setText(str(first / "one.tif"))
    with qtbot.waitSignal(dialog.sigExported, timeout=10_000):
        dialog.accept()

    again = _dialog(qtbot, settings)
    again.pathEdit.setText("two.tif")
    with qtbot.waitSignal(again.sigExported, timeout=10_000) as blocker:
        again.accept()
    assert blocker.args[1] == str(first / "two.tif")


def test_a_name_no_system_can_take_is_refused_before_writing(
    qtbot, settings, tmp_path, monkeypatch
):
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args: warnings.append(args[2]) or 0
    )
    dialog = _dialog(qtbot, settings)
    dialog.pathEdit.setText(str(tmp_path / "bad<>|?*.tif"))

    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted
    (message,) = warnings
    assert "can't be used in a file name" in message
    assert not list(tmp_path.glob("bad*"))


def test_a_missing_folder_is_refused_in_plain_words(
    qtbot, settings, tmp_path, monkeypatch
):
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args: warnings.append(args[2]) or 0
    )
    dialog = _dialog(qtbot, settings)
    dialog.pathEdit.setText(str(tmp_path / "no such folder" / "x.tif"))
    dialog.accept()
    assert "doesn't exist" in warnings[0]


def test_a_write_failure_reads_plainly_with_details(
    qtbot, settings, tmp_path, monkeypatch
):
    shown = []
    monkeypatch.setattr(
        "varda.image_loading.export_image_dialog.showPlainError",
        lambda parent, title, error: shown.append(error),
    )
    monkeypatch.setattr(
        "varda.image_loading.export_image_dialog.writeRaster",
        lambda image, path: (_ for _ in ()).throw(
            OSError(28, "No space left on device")
        ),
    )
    dialog = _dialog(qtbot, settings)
    dialog.pathEdit.setText(str(tmp_path / "x.tif"))
    dialog.accept()
    qtbot.waitUntil(lambda: bool(shown), timeout=5000)
    assert "full" in shown[0].message
    assert "No space left" in shown[0].details


# --- release QA 0.12.0 #3, #17, #18 ----------------------------------------

TYPICAL_PATH = "/Users/someone/Documents/Data/CRISM/frt00003e12_07_if166j_mtr3.img"


def test_save_as_shows_a_typical_path_whole_and_the_full_path_in_a_tooltip(
    qtbot, settings
):
    dialog = _dialog(qtbot, settings)
    dialog.pathEdit.setText(TYPICAL_PATH)
    dialog.show()
    qtbot.waitExposed(dialog)

    field = dialog.pathEdit
    shown = field.width() - 2 * field.style().pixelMetric(
        field.style().PixelMetric.PM_DefaultFrameWidth
    )
    assert field.fontMetrics().horizontalAdvance(TYPICAL_PATH) + 8 <= shown
    assert field.toolTip() == TYPICAL_PATH
    assert dialog.width() <= 1024 - 32  # still fits a small laptop screen
    assertNothingClipped(dialog)


def test_the_dialog_says_the_file_carries_its_provenance(qtbot, settings):
    dialog = _dialog(qtbot, settings)
    texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "provenance" in texts.lower()


def test_the_status_bar_names_the_file_first_then_clears(
    qtbot, settings, tmp_path, monkeypatch
):
    monkeypatch.setattr("varda.image_loading.export_image_dialog.CONFIRMATION_MS", 300)
    window = QMainWindow()
    qtbot.addWidget(window)
    image = generate_random_image((20, 20, 10))
    dialog = ExportImageDialog([image], image=image, parent=window, settings=settings)
    target = tmp_path / "pca result.tif"
    dialog.pathEdit.setText(str(target))

    with qtbot.waitSignal(dialog.sigExported, timeout=10_000):
        dialog.accept()

    bar = window.statusBar()
    assert bar.currentMessage() == f"Exported pca result.tif to {tmp_path}"
    assert bar.toolTip() == str(target)
    qtbot.waitUntil(lambda: bar.currentMessage() == "", timeout=3000)
    assert bar.toolTip() == ""


def test_the_confirmation_does_not_linger():
    assert export_image_dialog.CONFIRMATION_MS <= 8000


@pytest.mark.parametrize("typed", ["CON.tif", "nul", "bad<>|?*.tif"])
def test_refused_names_are_explained_for_every_system(
    qtbot, settings, tmp_path, monkeypatch, typed
):
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args: warnings.append(args[2]) or 0
    )
    dialog = _dialog(qtbot, settings)
    dialog.pathEdit.setText(str(tmp_path / typed))
    dialog.accept()

    (message,) = warnings
    assert "reserved by Windows" not in message
    assert "Windows forbids" not in message
    assert "won't open on Windows" in message
    assert "portable" in message
    assert "Rename" in message  # says what to do
