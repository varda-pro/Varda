"""Choose one spectral library from the shared store (bundled, yours, or
loaded from a file); a library loaded here appears in every other list."""

from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QWidget,
)

from varda.common.parameter_widgets import connectWhileAlive
from varda.common.ui import ElidingComboBox
from varda.spectral_libraries.formats import LIBRARY_FORMAT_HELP
from varda.spectral_libraries.spectrum import LibrarySpectrum
from varda.spectral_libraries.store import (
    MICA_LIBRARY_NAME,
    USGS_LIBRARY_NAME,
    LibraryStore,
    defaultStore,
)

# CRISM product IDs start with the observation type (FRT, HRL, HRS, FRS, ATO, ...)
CRISM_PREFIXES = ("frt", "hrl", "hrs", "frs", "ato", "ats", "msp", "hsp", "fft")
LIBRARY_FILE_FILTER = "Spectral libraries (*.sli *.hdr *.csv *.txt)"


class LibraryPicker(QWidget):
    """A library chooser with Load Library… and a help button on the format.

    It lists the shared store's libraries and follows its changes. Given a
    ``library``, it offers only that one (for scripts and tests)."""

    sigLibraryChanged = pyqtSignal()

    def __init__(
        self,
        library: list[LibrarySpectrum] | None = None,
        parent: QWidget | None = None,
        store: LibraryStore | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = defaultStore() if store is None else store
        self._given = library
        self._shown: tuple[str, int] = ("", -1)  # name and size last offered
        self.box = ElidingComboBox()
        self.box.currentIndexChanged.connect(lambda _: self._emitIfChanged())
        loadButton = QPushButton("Load Library…")
        loadButton.setToolTip(
            "An ENVI spectral library (.sli), a CSV, or a folder of USGS spectra"
        )
        loadButton.clicked.connect(self._load)
        loadButton.setVisible(library is None)
        self.helpButton = QPushButton("?")
        self.helpButton.setFixedWidth(28)
        self.helpButton.setToolTip("How to prepare a spectral library to load")
        self.helpButton.clicked.connect(self._showHelp)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.box, stretch=1)
        layout.addWidget(loadButton)
        layout.addWidget(self.helpButton)

        if library is None:
            connectWhileAlive(self.store.changed, self._refill, self)
        self._refill()

    def _refill(self) -> None:
        """List the store's libraries again, keeping the current one."""
        current = self.libraryName()
        with QSignalBlocker(self.box):
            self.box.clear()
            if self._given is not None:
                self.box.addItem(f"Library ({len(self._given)})", "Library")
            else:
                for library in self.store.libraries():
                    self.box.addItem(library.label(), library.name)
            index = self.box.findData(current or USGS_LIBRARY_NAME)
            self.box.setCurrentIndex(max(index, 0))
        self._emitIfChanged(initial=not current)

    def _emitIfChanged(self, initial: bool = False) -> None:
        """Say so when what's offered changed: another library, or the same
        one with spectra added or removed."""
        shown = (self.libraryName(), len(self.library()))
        if shown != self._shown:
            self._shown = shown
            if not initial:
                self.sigLibraryChanged.emit()

    def selectDefaultFor(self, imageName: str) -> None:
        """MICA for CRISM products (by their product-ID prefix), USGS otherwise."""
        isCrism = imageName.lower().startswith(CRISM_PREFIXES)
        self.selectLibrary(MICA_LIBRARY_NAME if isCrism else USGS_LIBRARY_NAME)

    def selectLibrary(self, name: str) -> None:
        index = self.box.findData(name)
        if index >= 0:
            self.box.setCurrentIndex(index)

    def libraryName(self) -> str:
        return self.box.currentData() or ""

    def library(self) -> list[LibrarySpectrum]:
        if self._given is not None:
            return self._given
        library = self.store.library(self.libraryName())
        return [] if library is None else list(library.spectra)

    def _showHelp(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Preparing a spectral library")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(LIBRARY_FORMAT_HELP)
        box.open()

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Spectral Library", "", LIBRARY_FILE_FILTER
        )
        if path:
            self.loadFile(path)

    def loadFile(self, path: str) -> None:
        """Load a library into the shared store and choose it."""
        try:
            library = self.store.loadFile(path)
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(
                self, "Load Library", f"Could not read {path}:\n{error}"
            )
            return
        self.selectLibrary(library.name)
