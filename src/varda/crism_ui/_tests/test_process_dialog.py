"""The Process CRISM Observation dialog: a batch over the pipeline."""

import json
import shutil
import threading
import logging
from pathlib import Path

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QMessageBox

from varda._actions import ALL_ACTIONS, PROCESSING_SUBMENUS
from varda._actions._processing_actions import (
    CRISM_CALIBRATION_ACTION_ID,
    CRISM_PROCESS_ACTION_ID,
)
from varda.crism import CalibrationLibrary, CrismObservation, ProcessResult
from varda.crism.calibration import CalibrationFileStatus, MissingCalibrationError
from varda.crism.pipeline import ProcessCancelled
from varda.crism.recipe import recipeToDict
from varda.crism_ui import calibration_dialog, process_dialog
from varda.crism_ui.calibration_dialog import (
    CalibrationDialog,
    catAuxFolder,
    setCatAuxFolder,
)
from varda.crism_ui.process_dialog import ProcessCrismDialog
from varda.image_loading.image_loading_service import openDataSource


def _observation(name: str) -> CrismObservation:
    folder = Path("/nowhere") / name
    return CrismObservation(
        name, 1, folder / f"{name}_01_if168l_trr3.img", folder / "ddr.img"
    )


def _result(tmp_path: Path, name: str) -> ProcessResult:
    path = tmp_path / f"{name}.img"
    return ProcessResult(path, None, None, None, None, path, path, path)


def _dialog(qtbot, tmp_path: Path, names: list[str]) -> ProcessCrismDialog:
    dialog = ProcessCrismDialog(libraryFor=lambda _: _library(tmp_path))
    qtbot.addWidget(dialog)
    dialog.addObservations([_observation(n) for n in names])
    dialog.outputFolder.setText(str(tmp_path / "out"))
    return dialog


def _library(tmp_path: Path) -> CalibrationLibrary:
    return CalibrationLibrary([], tmp_path / "cache")


@pytest.fixture(autouse=True)
def _calibrationPresent(monkeypatch):
    monkeypatch.setattr(process_dialog, "ensureCalibration", lambda *args: None)


def _run(qtbot, dialog: ProcessCrismDialog) -> None:
    with qtbot.waitSignal(dialog.sigFinished, timeout=30000):
        dialog.run()


def test_batch_continues_after_failure(qtbot, tmp_path, monkeypatch, caplog):
    def fake(observation, recipe, outputFolder, **kwargs):
        if observation.name == "b":
            raise ValueError("bad cube")
        return _result(tmp_path, observation.name)

    monkeypatch.setattr(process_dialog, "process", fake)
    dialog = _dialog(qtbot, tmp_path, ["a", "b", "c"])
    results: list[ProcessResult] = []
    dialog.sigProcessed.connect(results.append)
    with caplog.at_level(logging.ERROR, logger=process_dialog.__name__):
        _run(qtbot, dialog)
    assert dialog.statuses() == ["done", "failed: bad cube", "done"]
    assert len(results) == 2
    # the traceback goes to the log (final review, Important 7)
    (record,) = [r for r in caplog.records if r.exc_info is not None]
    assert "Traceback" in caplog.text and "bad cube" in caplog.text


def test_cancel_stops_batch(qtbot, tmp_path, monkeypatch):
    dialog = _dialog(qtbot, tmp_path, ["a", "b", "c"])

    def fake(observation, recipe, outputFolder, *, cancelled, **kwargs):
        if observation.name == "a":
            dialog.requestCancel()
            return _result(tmp_path, "a")
        raise AssertionError("ran after cancel")

    monkeypatch.setattr(process_dialog, "process", fake)
    _run(qtbot, dialog)
    assert dialog.statuses() == ["done", "canceled", "canceled"]


def test_a_cancel_inside_process_marks_that_row_canceled(qtbot, tmp_path, monkeypatch):
    def fake(observation, recipe, outputFolder, **kwargs):
        raise ProcessCancelled()

    monkeypatch.setattr(process_dialog, "process", fake)
    dialog = _dialog(qtbot, tmp_path, ["a", "b"])
    _run(qtbot, dialog)
    assert dialog.statuses() == ["canceled", "canceled"]


def test_progress_shows_on_the_running_row(qtbot, tmp_path, monkeypatch):
    seen: list[str] = []

    def fake(observation, recipe, outputFolder, *, progress, **kwargs):
        progress(0.5, "Pass 2: atmosphere")
        qtbot.waitUntil(lambda: "atmosphere" in dialog.statuses()[0], timeout=5000)
        seen.append(dialog.statuses()[0])
        return _result(tmp_path, observation.name)

    monkeypatch.setattr(process_dialog, "process", fake)
    dialog = _dialog(qtbot, tmp_path, ["a"])
    _run(qtbot, dialog)
    assert seen == ["running: Pass 2: atmosphere"]
    assert dialog.progressBar.value() == 100


def test_missing_calibration_stops_before_any_pass(qtbot, tmp_path, monkeypatch):
    missing = CalibrationFileStatus(
        "wa0000000l_3", "WA", None, "https://x/wa", True, "no network"
    )

    def absent(observation, library):
        raise MissingCalibrationError([missing])

    monkeypatch.setattr(process_dialog, "ensureCalibration", absent)

    def forbidden(*args, **kwargs):
        raise AssertionError("process must not start")

    monkeypatch.setattr(process_dialog, "process", forbidden)
    dialog = _dialog(qtbot, tmp_path, ["a"])
    _run(qtbot, dialog)
    assert dialog.statuses()[0].startswith("failed: missing calibration")
    assert "wa0000000l_3" in dialog.progressLabel.text()
    assert "no network" in dialog.progressLabel.text()
    assert not dialog.getCalibrationButton.isHidden()


def test_recipe_save_load(qtbot, tmp_path):
    dialog = _dialog(qtbot, tmp_path, [])
    dialog.recipe.despike.threshold.set(7.5)
    dialog.recipe.destripe.enabled.set(True)
    saved = tmp_path / "recipe.json"
    dialog.saveRecipe(saved)
    assert json.loads(saved.read_text()) == recipeToDict(dialog.recipe)
    dialog.recipe.despike.threshold.set(3.0)
    dialog.recipe.destripe.enabled.set(False)
    dialog.loadRecipe(saved)
    assert dialog.recipe.despike.threshold.get() == 7.5
    assert dialog.recipe.destripe.enabled.get()


def test_each_step_has_its_own_titled_box(qtbot, tmp_path):
    dialog = _dialog(qtbot, tmp_path, [])
    assert dialog.stepTitles() == [
        "Photometry",
        "Despike",
        "Atmospheric correction",
        "Destripe",
        "Smile",
        "Map projection",
        "Outputs",
    ]


def test_remove_selected_rows(qtbot, tmp_path):
    dialog = _dialog(qtbot, tmp_path, ["a", "b", "c"])
    dialog.table.selectRow(1)
    dialog.removeSelected()
    assert [o.name for o in dialog.observations()] == ["a", "c"]


def test_menu_actions_registered():
    ids = {a.id: a for a in ALL_ACTIONS}
    for actionId in (CRISM_PROCESS_ACTION_ID, CRISM_CALIBRATION_ACTION_ID):
        assert [m.id for m in ids[actionId].menus or []] == ["varda/processing/crism"]
    assert ids[CRISM_PROCESS_ACTION_ID].title == "Process CRISM Observation…"
    assert ids[CRISM_CALIBRATION_ACTION_ID].title == "Get CRISM Calibration Files…"
    assert ("varda/processing", "CRISM") in [
        (parent, item.title) for parent, item in PROCESSING_SUBMENUS
    ]


def test_cat_aux_folder_setting(tmp_path):
    settings = QSettings(str(tmp_path / "v.ini"), QSettings.Format.IniFormat)
    assert catAuxFolder(settings) is None
    setCatAuxFolder(tmp_path, settings)
    assert settings.value("crism/catAuxFolder") == str(tmp_path)
    assert catAuxFolder(settings) == tmp_path


def test_default_library_searches_the_observations_and_their_cdr_ancestors(tmp_path):
    (tmp_path / "CRISM_CDR").mkdir()
    folder = tmp_path / "obs" / "trdr"
    folder.mkdir(parents=True)
    observation = CrismObservation("o", 1, folder / "a.img", folder / "b.img")
    folders = calibration_dialog.libraryFolders([observation])
    assert folders[0] == folder
    assert tmp_path in folders
    assert tmp_path / "obs" not in folders


def test_calibration_dialog_lists_files(qtbot, tmp_path, demoData):
    dialog = CalibrationDialog(
        libraryFor=lambda obs: CalibrationLibrary([], tmp_path / "cache")
    )
    qtbot.addWidget(dialog)
    dialog.setFolder(demoData / "data-for-paper" / "FRS00031442")
    qtbot.waitUntil(lambda: dialog.table.rowCount() > 0, timeout=20000)
    items = [dialog.table.item(r, 0) for r in range(dialog.table.rowCount())]
    kinds = [item.text() for item in items if item is not None]
    assert "WA" in kinds and "AT" in kinds


def test_real_process_opens_in_varda(qtbot, tmp_path, demoData):
    """The dialog's batch with the real pipeline on FRS00031442; the product
    opens through the image loader."""
    data = demoData / "data-for-paper"
    aux = data / "CAT_v7_4_aux" / "aux_files"
    if not aux.is_dir():
        raise pytest.skip.Exception("CAT aux files not found")

    def offline(url: str, target: Path) -> None:
        raise OSError("offline (test)")

    dialog = ProcessCrismDialog(
        libraryFor=lambda obs: CalibrationLibrary(
            [data / "ATO0004EC45", *(o.irCube.parent for o in obs)],
            tmp_path / "cache",
            aux,
            offline,
        )
    )
    qtbot.addWidget(dialog)
    dialog.addObservations(CrismObservation.discover(data / "FRS00031442"))
    dialog.outputFolder.setText(str(tmp_path / "out"))
    results: list[ProcessResult] = []
    dialog.sigProcessed.connect(results.append)
    with qtbot.waitSignal(dialog.sigFinished, timeout=600000):
        dialog.run()
    assert dialog.statuses() == ["done"], dialog.progressLabel.text()
    (result,) = results
    assert result.mapProjected is not None
    source = openDataSource(str(result.mapProjected))
    assert source.width > 0 and source.height > 0


def test_closing_mid_run_cancels_the_rest(qtbot, tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def fake(observation, recipe, outputFolder, **kwargs):
        started.set()
        assert release.wait(10)
        return _result(tmp_path, observation.name)

    monkeypatch.setattr(process_dialog, "process", fake)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    dialog = _dialog(qtbot, tmp_path, ["a", "b", "c"])
    results: list[ProcessResult] = []
    dialog.sigProcessed.connect(results.append)
    dialog.run()
    qtbot.waitUntil(started.is_set, timeout=10000)
    dialog.reject()  # asks, cancels, and waits for the worker
    assert dialog.statuses()[1:] == ["queued", "queued"]  # not stopped yet
    with qtbot.waitSignal(dialog.sigFinished, timeout=10000):
        release.set()
    assert dialog.statuses()[1:] == ["canceled", "canceled"]
    assert results == []


def test_declining_the_close_prompt_keeps_the_batch(qtbot, tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def fake(observation, recipe, outputFolder, **kwargs):
        started.set()
        assert release.wait(10)
        return _result(tmp_path, observation.name)

    monkeypatch.setattr(process_dialog, "process", fake)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    dialog = _dialog(qtbot, tmp_path, ["a"])
    dialog.run()
    qtbot.waitUntil(started.is_set, timeout=10000)
    dialog.reject()
    with qtbot.waitSignal(dialog.sigFinished, timeout=10000):
        release.set()
    assert dialog.statuses() == ["done"]


def test_duplicate_observations_are_skipped(qtbot, tmp_path):
    dialog = _dialog(qtbot, tmp_path, ["a", "b"])
    dialog.addObservations([_observation("a"), _observation("c")])
    assert [o.name for o in dialog.observations()] == ["a", "b", "c"]


def test_library_folders_of_a_folder_without_cdrs_is_just_that_folder(tmp_path):
    folder = tmp_path / "obs"
    folder.mkdir()
    observation = CrismObservation("o", 1, folder / "a.img", folder / "b.img")
    assert calibration_dialog.libraryFolders([observation]) == [folder]


def test_calibration_is_fetched_then_the_batch_runs(
    qtbot, tmp_path, monkeypatch, demoData
):
    """Only the TRR3 and DDR are local: the dialog fetches the CDRs (a fake
    fetch copies them from another folder) and then processes."""
    data = demoData / "data-for-paper"
    source = data / "ATO0004EC45"
    if not source.is_dir():
        raise pytest.skip.Exception("ATO0004EC45 not found")
    fetched: list[str] = []

    def fakeFetch(url: str, target: Path) -> None:
        name = url.rsplit("/", 1)[-1].lower()
        match = next((p for p in source.rglob("*") if p.name.lower() == name), None)
        if match is None:
            raise OSError("not found")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(match, target)
        fetched.append(name)

    obs = CrismObservation.discover(data / "FRS00031442")
    monkeypatch.setattr(
        process_dialog, "process", lambda o, r, out, **k: _result(tmp_path, o.name)
    )
    monkeypatch.setattr(
        process_dialog, "ensureCalibration", calibration_dialog.ensureCalibration
    )
    library = CalibrationLibrary([], tmp_path / "cache", fetch=fakeFetch)
    dialog = ProcessCrismDialog(libraryFor=lambda _: library)
    qtbot.addWidget(dialog)
    dialog.addObservations(obs)
    dialog.outputFolder.setText(str(tmp_path / "out"))
    _run(qtbot, dialog)
    assert dialog.statuses() == ["done"], dialog.progressLabel.text()
    assert any(name.endswith(".img") for name in fetched)
