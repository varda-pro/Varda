"""Export Image: pick an image and a destination, then write it."""

import numpy as np
import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QDialog, QMessageBox

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
    assert "can't be used in file names" in message
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
