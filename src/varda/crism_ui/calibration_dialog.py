"""Processing > CRISM > Get CRISM Calibration Files: which calibration files an
observation needs, where each is, and a button to fetch the missing ones."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from PyQt6.QtCore import QSettings, QStandardPaths, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from varda.common.ui import MutedLabel
from varda.crism import CalibrationLibrary, CrismObservation
from varda.crism.calibration import CalibrationFileStatus, MissingCalibrationError
from varda.utilities.background import runInBackground

CAT_AUX_KEY = "crism/catAuxFolder"
type LibraryFactory = Callable[[Sequence[CrismObservation]], CalibrationLibrary]


def catAuxFolder(settings: QSettings | None = None) -> Path | None:
    """The CAT aux folder set in the calibration dialog, if any."""
    value = (settings or QSettings()).value(CAT_AUX_KEY, "")
    return Path(str(value)) if value else None


def setCatAuxFolder(folder: Path | None, settings: QSettings | None = None) -> None:
    (settings or QSettings()).setValue(CAT_AUX_KEY, str(folder) if folder else "")


def cacheFolder() -> Path:
    """Where fetched calibration files are kept."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    return Path(base) / "crism"


def libraryFolders(observations: Sequence[CrismObservation]) -> list[Path]:
    """Each observation's own folder, then every ancestor that holds a
    ``CRISM_CDR`` folder (the library looks in ``<folder>/CRISM_CDR/<kind>``)."""
    folders: list[Path] = []
    for observation in observations:
        folder = observation.irCube.parent
        for candidate in (
            folder,
            *(p for p in folder.parents if (p / "CRISM_CDR").is_dir()),
        ):
            if candidate not in folders:
                folders.append(candidate)
    return folders


def defaultLibrary(
    observations: Sequence[CrismObservation], settings: QSettings | None = None
) -> CalibrationLibrary:
    return CalibrationLibrary(
        libraryFolders(observations), cacheFolder(), catAuxFolder(settings)
    )


def ensureCalibration(
    observation: CrismObservation, library: CalibrationLibrary
) -> None:
    """Fetch what the observation is missing; raises MissingCalibrationError
    when a required file can't be had."""
    library.ensure(observation.readIr()[0])


class CalibrationDialog(QDialog):
    def __init__(
        self,
        folder: Path | None = None,
        libraryFor: LibraryFactory = defaultLibrary,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("CRISM Calibration Files")
        self.setMinimumSize(720, 420)
        self._libraryFor = libraryFor
        self._observation: CrismObservation | None = None
        self._folder: Path | None = None

        self.folderEdit = QLineEdit()
        self.folderEdit.setPlaceholderText("An observation folder")
        self.folderEdit.editingFinished.connect(self._folderEdited)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browseFolder)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Kind", "File", "Status", "Required", "Note"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.message = MutedLabel()
        self.message.setWordWrap(True)
        self.fetchButton = QPushButton("Fetch Missing")
        self.fetchButton.clicked.connect(self.fetchMissing)
        self.fetchButton.setEnabled(False)
        self.catAux = QLineEdit(str(catAuxFolder() or ""))
        self.catAux.setToolTip(
            "Optional: the CRISM Analysis Toolkit (CAT) data folder. Its volcano "
            "scans give extra atmospheric transmission spectra."
        )
        self.catAux.setPlaceholderText("Optional: the CAT aux_files folder")
        self.catAux.editingFinished.connect(self._saveCatAux)
        browseAux = QPushButton("Browse…")
        browseAux.clicked.connect(self._browseCatAux)

        top = QHBoxLayout()
        top.addWidget(QLabel("Observation folder:"))
        top.addWidget(self.folderEdit, 1)
        top.addWidget(browse)
        aux = QHBoxLayout()
        aux.addWidget(QLabel("CAT aux folder:"))
        aux.addWidget(self.catAux, 1)
        aux.addWidget(browseAux)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.addButton(self.fetchButton, QDialogButtonBox.ButtonRole.ActionRole)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.message)
        layout.addLayout(aux)
        layout.addWidget(buttons)
        if folder is not None:
            self.setFolder(folder)

    def _browseFolder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Observation Folder")
        if folder:
            self.setFolder(Path(folder))

    def _browseCatAux(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "CAT aux_files Folder")
        if folder:
            self.catAux.setText(folder)
            self._saveCatAux()

    def _saveCatAux(self) -> None:
        text = self.catAux.text().strip()
        setCatAuxFolder(Path(text) if text else None)

    def _folderEdited(self) -> None:
        text = self.folderEdit.text().strip()
        if text and Path(text) != self._folder:
            self.setFolder(Path(text))

    def setFolder(self, folder: Path) -> None:
        """Show the calibration files of the observation under ``folder``."""
        self.folderEdit.setText(str(folder))
        self._folder = folder
        self._observation = None
        self.table.setRowCount(0)
        self.fetchButton.setEnabled(False)
        self.message.setText("Looking for observations…")
        runInBackground(
            lambda: CrismObservation.discover(folder),
            lambda found: self._found(folder, found),
            lambda error: self._lookupFailed(folder, error),
            description="CRISM observation discovery",
            owner=self,
        )

    def _found(self, folder: Path, found: list[CrismObservation]) -> None:
        if folder != self._folder:  # the folder changed meanwhile
            return
        if not found:
            self.message.setText(f"No CRISM IR observation found under {folder}.")
            return
        self._observation = found[0]
        self.message.setText(
            found[0].name + (f" (first of {len(found)})" if len(found) > 1 else "")
        )
        self._refresh()

    def _lookupFailed(self, folder: Path, error: Exception) -> None:
        if folder == self._folder:
            self.message.setText(f"Could not read {folder}: {error}")

    def _refresh(self) -> None:
        observation = self._observation
        if observation is None:
            return
        library = self._libraryFor([observation])
        self._runTask(lambda: library.status(observation.readIr()[0]))

    def fetchMissing(self) -> None:
        observation = self._observation
        if observation is None:
            return
        library = self._libraryFor([observation])
        self.message.setText("Fetching…")

        def fetch() -> list[CalibrationFileStatus]:
            try:
                return library.ensure(observation.readIr()[0])
            except MissingCalibrationError:
                return library.status(observation.readIr()[0])

        self._runTask(fetch)

    def _runTask(self, task: Callable[[], list[CalibrationFileStatus]]) -> None:
        folder = self._folder
        self.fetchButton.setEnabled(False)

        def show(statuses: list[CalibrationFileStatus]) -> None:
            if folder == self._folder:
                self._showStatus(statuses)

        def failed(error: Exception) -> None:
            if folder == self._folder:
                self.message.setText(f"Failed: {error}")

        runInBackground(
            task, show, failed, description="CRISM calibration status", owner=self
        )

    def _showStatus(self, statuses: list[CalibrationFileStatus]) -> None:
        self.table.setRowCount(len(statuses))
        for row, entry in enumerate(statuses):
            status = str(entry.localPath) if entry.localPath else "missing"
            values = [
                entry.kind,
                entry.name,
                status,
                "yes" if entry.required else "no",
                entry.note,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        missing = [e for e in statuses if e.localPath is None and e.url]
        self.fetchButton.setEnabled(bool(missing))
        blocking = [e for e in statuses if e.localPath is None and e.required]
        self.message.setText(
            f"Missing required: {', '.join(e.name for e in blocking)}"
            if blocking
            else "All required files are available."
        )
