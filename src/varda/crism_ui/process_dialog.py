"""Processing > CRISM > Process CRISM Observation: a batch of observations
through the TRR3 to MTRDR-like pipeline with one recipe. Calibration files are
checked (and fetched) first: an observation missing a required file is
skipped before any pass, and the others are processed (as on the command
line)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QStandardPaths, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from superqt import QCollapsible

from varda.common.parameter import Parameter, ParameterGroup
from varda.common.parameter_widgets import groupWidget
from varda.common.ui import MutedLabel
from varda.crism import CrismObservation, ProcessResult, Recipe, process
from varda.crism.calibration import CalibrationLibrary, MissingCalibrationError
from varda.crism.pipeline import ProcessCancelled
from varda.crism.recipe import recipeFromDict, recipeToDict
from varda.crism_ui.calibration_dialog import (
    CalibrationDialog,
    LibraryFactory,
    defaultLibrary,
    ensureCalibration,
)
from varda.utilities.background import onMainThread, runInBackground

QUEUED = "queued"
CANCELED = "canceled"
MISSING = "failed: missing calibration files"
STEP_TITLES = {
    "photometry": "Photometry",
    "despike": "Despike",
    "atmosphere": "Atmospheric correction",
    "destripe": "Destripe",
    "smile": "Smile",
    "projection": "Map projection",
    "output": "Outputs",
}
_log = logging.getLogger(__name__)


def _copyValues(source: ParameterGroup, target: ParameterGroup) -> None:
    for key, param in source.params.items():
        other = target.params[key]
        if isinstance(param, ParameterGroup):
            assert isinstance(other, ParameterGroup)
            _copyValues(param, other)
        else:
            assert isinstance(other, Parameter)
            other.set(param.get())


class ProcessCrismDialog(QDialog):
    sigProcessed = pyqtSignal(object)  # each ProcessResult, as it finishes
    sigFinished = pyqtSignal()  # the batch ended (done, failed or canceled)

    def __init__(
        self,
        libraryFor: LibraryFactory = defaultLibrary,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Process CRISM Observation")
        self.setMinimumSize(760, 640)
        self._libraryFor = libraryFor
        self._observations: list[CrismObservation] = []
        self._cancelRequested = False
        self._running = False
        self._missingFolder: Path | None = None
        self._missingText = ""
        self._closing = False
        self.recipe = Recipe()

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Observation", "Status"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.addButton = QPushButton("Add Folder…")
        self.addButton.clicked.connect(self._addFolder)
        self.removeButton = QPushButton("Remove")
        self.removeButton.clicked.connect(self.removeSelected)
        rowButtons = QVBoxLayout()
        rowButtons.addWidget(self.addButton)
        rowButtons.addWidget(self.removeButton)
        rowButtons.addStretch()
        observations = QHBoxLayout()
        observations.addWidget(self.table, 1)
        observations.addLayout(rowButtons)

        self._steps: list[QCollapsible] = []
        stepsHost = QWidget()
        stepsLayout = QVBoxLayout(stepsHost)
        stepsLayout.setContentsMargins(0, 0, 0, 0)
        for key, group in self.recipe.params.items():
            assert isinstance(group, ParameterGroup)
            box = QCollapsible(STEP_TITLES.get(key, key.capitalize()))
            box.setContent(groupWidget(group))
            box.collapse(animate=False)
            self._steps.append(box)
            stepsLayout.addWidget(box)
        stepsLayout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(stepsHost)
        loadButton = QPushButton("Load Recipe…")
        loadButton.clicked.connect(self._browseLoadRecipe)
        saveButton = QPushButton("Save Recipe…")
        saveButton.clicked.connect(self._browseSaveRecipe)
        recipeButtons = QHBoxLayout()
        recipeButtons.addWidget(QLabel("Recipe"))
        recipeButtons.addStretch()
        recipeButtons.addWidget(loadButton)
        recipeButtons.addWidget(saveButton)

        documents = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        self.outputFolder = QLineEdit(str(Path(documents) / "CRISM products"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browseOutput)
        output = QHBoxLayout()
        output.addWidget(QLabel("Output folder:"))
        output.addWidget(self.outputFolder, 1)
        output.addWidget(browse)

        self.progressBar = QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressLabel = MutedLabel()
        self.progressLabel.setWordWrap(True)
        self.progressLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.getCalibrationButton = QPushButton("Get Calibration Files…")
        self.getCalibrationButton.clicked.connect(self._openCalibration)
        self.getCalibrationButton.hide()
        self.runButton = QPushButton("Run")
        self.runButton.setDefault(True)
        self.runButton.clicked.connect(self.run)
        self.cancelButton = QPushButton("Cancel")
        self.cancelButton.clicked.connect(self._cancelClicked)
        self.closeButton = QPushButton("Close")
        self.closeButton.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.getCalibrationButton)
        buttons.addStretch()
        buttons.addWidget(self.runButton)
        buttons.addWidget(self.cancelButton)
        buttons.addWidget(self.closeButton)

        layout = QVBoxLayout(self)
        layout.addLayout(observations, 1)
        layout.addLayout(recipeButtons)
        layout.addWidget(scroll, 2)
        layout.addLayout(output)
        layout.addWidget(self.progressBar)
        layout.addWidget(self.progressLabel)
        layout.addLayout(buttons)
        self._updateButtons()

        # progress reports come from the worker; each lands on the GUI thread
        self._report = onMainThread(self._guarded(self._onReport))
        self._emitProcessedOnMain = onMainThread(self._guarded(self._emitResult))

    def _guarded(self, callback: Callable[..., None]) -> Callable[..., None]:
        """``callback``, skipped once Qt has deleted this dialog."""

        def call(*args) -> None:
            if not sip.isdeleted(self):
                callback(*args)

        return call

    # --- recipe -----------------------------------------------------------------

    def stepTitles(self) -> list[str]:
        return [box.text() for box in self._steps]

    def saveRecipe(self, path: Path) -> None:
        Path(path).write_text(json.dumps(recipeToDict(self.recipe), indent=2))

    def loadRecipe(self, path: Path) -> None:
        """Replace the settings with those in the recipe file; raises
        ValueError (or OSError) when it can't be read."""
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict):
            raise ValueError("A recipe file holds a JSON object")
        _copyValues(recipeFromDict(data), self.recipe)

    def _browseSaveRecipe(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Recipe", "crism.recipe.json", "Recipes (*.json)"
        )
        if path:
            self.saveRecipe(Path(path))

    def _browseLoadRecipe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Recipe", "", "Recipes (*.json)"
        )
        if not path:
            return
        try:
            self.loadRecipe(Path(path))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Load Recipe", f"{path}: {error}")

    # --- observations -----------------------------------------------------------

    def observations(self) -> list[CrismObservation]:
        return list(self._observations)

    def statuses(self) -> list[str]:
        return [self._statusItem(row).text() for row in range(self.table.rowCount())]

    def _statusItem(self, row: int) -> QTableWidgetItem:
        item = self.table.item(row, 1)
        assert item is not None
        return item

    def addObservations(self, observations: Sequence[CrismObservation]) -> None:
        for observation in observations:
            if observation in self._observations:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(observation.name))
            self.table.setItem(row, 1, QTableWidgetItem(QUEUED))
            self._observations.append(observation)
        self._updateButtons()

    def _addFolder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Observation Folder")
        if not folder:
            return
        self.addButton.setEnabled(False)
        self.progressLabel.setText(f"Looking for observations in {folder}…")
        runInBackground(
            lambda: CrismObservation.discover(Path(folder)),
            lambda found: self._discovered(folder, found),
            lambda error: self._discoverFailed(folder, error),
            description="CRISM observation discovery",
            owner=self,
        )

    def _discovered(self, folder: str, found: list[CrismObservation]) -> None:
        self.progressLabel.setText("")
        self._updateButtons()
        if not found:
            QMessageBox.information(
                self, "Add Folder", f"No CRISM IR TRR3 observation found in {folder}."
            )
        self.addObservations(found)

    def _discoverFailed(self, folder: str, error: Exception) -> None:
        self.progressLabel.setText(f"Could not read {folder}: {error}")
        self._updateButtons()

    def removeSelected(self) -> None:
        if self._running:
            return
        model = self.table.selectionModel()
        assert model is not None
        for row in sorted({i.row() for i in model.selectedRows()}, reverse=True):
            self.table.removeRow(row)
            del self._observations[row]
        self._updateButtons()

    def _browseOutput(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Output Folder")
        if folder:
            self.outputFolder.setText(folder)

    def _updateButtons(self) -> None:
        idle = not self._running
        self.runButton.setEnabled(idle and bool(self._observations))
        self.cancelButton.setEnabled(not idle)
        self.addButton.setEnabled(idle)
        self.removeButton.setEnabled(idle)

    # --- running ----------------------------------------------------------------

    def requestCancel(self) -> None:
        """Ask the batch to stop; safe to call from any thread."""
        self._cancelRequested = True

    def reject(self) -> None:
        """Close; a running batch is canceled first (after asking), and the
        dialog closes once the worker has stopped."""
        if not self._running:
            super().reject()
            return
        answer = QMessageBox.question(
            self, "Process CRISM Observation", "Cancel the running batch?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._closing = True
        self._cancelClicked()

    def _cancelClicked(self) -> None:
        self.requestCancel()
        self.cancelButton.setEnabled(False)
        self.progressLabel.setText("Canceling…")

    def _setStatus(self, row: int, text: str) -> None:
        self._statusItem(row).setText(text)

    def _onReport(self, row: int, fraction: float, text: str) -> None:
        count = max(len(self._observations), 1)
        self.progressBar.setValue(round(100 * (row + fraction) / count))
        if text:
            self._setStatus(row, text)

    def run(self) -> None:
        """Check the calibration files, then process every observation."""
        if self._running or not self._observations:
            return
        self._running = True
        self._cancelRequested = False
        self.getCalibrationButton.hide()
        self.progressBar.setValue(0)
        self.progressBar.setRange(0, 0)  # busy
        self.progressLabel.setText("Getting calibration files…")
        for row in range(self.table.rowCount()):
            self._setStatus(row, QUEUED)
        self._updateButtons()
        observations = list(self._observations)
        library = self._libraryFor(observations)
        runInBackground(
            lambda: _missingCalibration(observations, library),
            lambda missing: self._calibrationChecked(observations, library, missing),
            self._failed,
            description="CRISM calibration check",
            owner=self,
        )

    def _calibrationChecked(
        self,
        observations: list[CrismObservation],
        library: CalibrationLibrary,
        missing: dict[int, MissingCalibrationError],
    ) -> None:
        self.progressBar.setRange(0, 100)
        self._missingText = ""
        if missing:
            lines = []
            for row, error in missing.items():
                self._setStatus(row, MISSING)
                lines += [
                    f"{observations[row].name}: {f.kind} {f.name} ({f.url or 'no known source'})"
                    + (f": {f.note}" if f.note else "")
                    for f in error.files
                ]
            first = observations[min(missing)]
            self._missingFolder = first.irCube.parent
            self._missingText = "Missing calibration files:\n" + "\n".join(lines)
            self.getCalibrationButton.show()
        self.progressLabel.setText(self._missingText)
        if len(missing) == len(observations):
            self._finish()
            return
        recipe = Recipe()
        _copyValues(self.recipe, recipe)  # the batch keeps the settings it started with
        output = Path(self.outputFolder.text())
        skip = set(missing)
        runInBackground(
            lambda: self._batch(observations, recipe, output, library, skip),
            lambda _: self._batchDone(),
            self._failed,
            description="CRISM batch",
            owner=self,
        )

    def _batch(
        self,
        observations: list[CrismObservation],
        recipe: Recipe,
        output: Path,
        library: CalibrationLibrary,
        skip: set[int],
    ) -> None:
        """On the worker thread: every call back into the GUI goes through
        ``self._report`` or the main-thread helpers. The rows in ``skip``
        (missing calibration) are left as they are."""
        report = self._report
        for row, observation in enumerate(observations):
            if row in skip:
                continue
            if self._cancelRequested:
                report(row, 0.0, CANCELED)
                continue
            report(row, 0.0, "running")
            try:
                result = process(
                    observation,
                    recipe,
                    output,
                    library=library,
                    progress=lambda fraction, message, row=row: report(
                        row, fraction, f"running: {message}"
                    ),
                    cancelled=lambda: self._cancelRequested,
                )
            except ProcessCancelled:
                report(row, 0.0, CANCELED)
            except Exception as error:  # noqa: BLE001 - one failure must not end the batch
                _log.exception("Processing %s failed", observation.name)
                report(row, 0.0, f"failed: {error}")
            else:
                report(row, 1.0, "done")
                self._emitProcessed(result)

    def _emitProcessed(self, result: ProcessResult) -> None:
        # called from the worker: hop to the GUI thread to emit
        self._emitProcessedOnMain(result)

    def _emitResult(self, result: ProcessResult) -> None:
        if not self._closing:
            self.sigProcessed.emit(result)

    def _batchDone(self) -> None:
        self.progressBar.setValue(100)
        failed = sum(s.startswith("failed") for s in self.statuses())
        canceled = sum(s == CANCELED for s in self.statuses())
        parts = [f"{self.table.rowCount() - failed - canceled} done"]
        if failed:
            parts.append(f"{failed} failed")
        if canceled:
            parts.append(f"{canceled} canceled")
        summary = ", ".join(parts)
        missing = self._missingText
        self.progressLabel.setText(f"{summary}\n{missing}" if missing else summary)
        self._finish()

    def _failed(self, error: Exception) -> None:
        self.progressBar.setRange(0, 100)
        self.progressLabel.setText(f"Failed: {error}")
        self._finish()

    def _finish(self) -> None:
        self._running = False
        self._updateButtons()
        self.sigFinished.emit()
        if self._closing:
            self._closing = False
            super().reject()

    def _openCalibration(self) -> None:
        dialog = CalibrationDialog(
            self._missingFolder, libraryFor=self._libraryFor, parent=self
        )
        dialog.open()


def _missingCalibration(
    observations: list[CrismObservation], library: CalibrationLibrary
) -> dict[int, MissingCalibrationError]:
    """Fetch what each observation lacks; the ones that still miss a required
    file, by row."""
    missing: dict[int, MissingCalibrationError] = {}
    for row, observation in enumerate(observations):
        try:
            ensureCalibration(observation, library)
        except MissingCalibrationError as error:
            missing[row] = error
    return missing
