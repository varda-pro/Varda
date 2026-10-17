"""The Spectral Library Manager: browse, build, edit and export libraries.

Bundled and loaded libraries are read-only; copy spectra out of them into
one of yours to change them. Everything goes through the shared store, so
every library list in Varda follows."""

from __future__ import annotations

from html import escape
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6 import sip
from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from varda.analysis.library_picker import LIBRARY_FILE_FILTER
from varda.common.parameter_widgets import connectWhileAlive
from varda.common.ui import FlowLayout, MutedLabel, VBoxBuilder, WrapperWidget
from varda.library_manager.add_to_library_dialog import AddToLibraryDialog
from varda.library_manager.info_view import InfoBrowser, libraryHtml, provenanceHtml
from varda.spectral_libraries.spectrum import LibrarySpectrum, copiedSpectrum
from varda.spectral_libraries.store import (
    LibraryKind,
    LibraryStore,
    SpectralLibrary,
    defaultStore,
)

EXPORT_FILTER = "ENVI spectral library (*.sli);;CSV (*.csv)"
_COLUMNS = ["Name", "Value units", "Source"]
_PREVIEW_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]


def _button(text: str, toolTip: str, slot) -> QPushButton:
    button = QPushButton(text)
    button.setToolTip(toolTip)
    button.clicked.connect(slot)
    return button


class SpectralLibraryManager(QDialog):
    def __init__(
        self, store: LibraryStore | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Spectral Libraries")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        self.store = defaultStore() if store is None else store

        # --- missing files ---
        self.missingLabel = InfoBrowser()  # long paths wrap instead of widening
        self.missingLabel.setMaximumHeight(100)
        self.forgetMissingButton = _button(
            "Forget Missing",
            "Stop looking for these files at startup",
            self.store.forgetUnavailable,
        )
        missingRow = FlowLayout()
        missingRow.addWidget(self.forgetMissingButton)
        self.missingBox = WrapperWidget(
            VBoxBuilder(margins=0).withWidget(self.missingLabel).withLayout(missingRow)
        )

        # --- libraries ---
        self.libraryList = QListWidget()
        self.libraryList.setMinimumWidth(170)
        self.libraryList.currentRowChanged.connect(lambda _: self._showLibrary())
        self.newButton = _button(
            "New…", "Create an empty library of your own", self._new
        )
        self.loadButton = _button(
            "Load…",
            "Load an ENVI spectral library (.sli), a CSV, or a USGS splib07 folder; "
            "Varda remembers it",
            self._load,
        )
        self.loadFolderButton = _button(
            "Load USGS Folder…",
            "Load a folder of USGS splib07 spectra",
            self._loadFolder,
        )
        self.renameLibraryButton = _button(
            "Rename…", "Rename the selected library", self._renameLibrary
        )
        self.removeLibraryButton = _button(
            "Delete…",
            "Delete your library (its files too), or forget a loaded one",
            self._removeLibrary,
        )
        self.exportButton = _button(
            "Export…",
            "Save the library as an ENVI spectral library or a CSV, with each "
            "spectrum's record",
            self._export,
        )
        libraryButtons = FlowLayout()
        for button in (
            self.newButton,
            self.loadButton,
            self.loadFolderButton,
            self.renameLibraryButton,
            self.removeLibraryButton,
            self.exportButton,
        ):
            libraryButtons.addWidget(button)
        libraries = WrapperWidget(
            VBoxBuilder(margins=0)
            .withWidget(QLabel("<b>Libraries</b>"))
            .withWidget(self.libraryList, stretch=1)
            .withLayout(libraryButtons)
        )

        # --- spectra ---
        self.searchEdit = QLineEdit()
        self.searchEdit.setPlaceholderText("Search spectra by name")
        self.searchEdit.setClearButtonEnabled(True)
        self.searchEdit.textChanged.connect(lambda _: self._applySearch())
        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        vertical, header = self.table.verticalHeader(), self.table.horizontalHeader()
        assert vertical is not None and header is not None
        vertical.setVisible(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.itemSelectionChanged.connect(self._showSelection)
        self.renameSpectrumButton = _button(
            "Rename…", "Rename the selected spectrum", self._renameSpectrum
        )
        self.removeSpectraButton = _button(
            "Delete",
            "Delete the selected spectra from your library",
            self.removeSelectedSpectra,
        )
        self.copyButton = _button(
            "Copy to Library…",
            "Copy the selected spectra, with their records, into one of your "
            "libraries (or a new one)",
            self._copySelected,
        )
        spectrumButtons = FlowLayout()
        for button in (
            self.renameSpectrumButton,
            self.removeSpectraButton,
            self.copyButton,
        ):
            spectrumButtons.addWidget(button)
        self.countLabel = MutedLabel()
        spectra = WrapperWidget(
            VBoxBuilder(margins=0)
            .withWidget(self.searchEdit)
            .withWidget(self.table, stretch=1)
            .withWidget(self.countLabel)
            .withLayout(spectrumButtons)
        )

        # --- preview and record ---
        self.preview = pg.PlotWidget()
        self.preview.setMinimumHeight(140)
        self.preview.setLabel("bottom", "Wavelength (nm)")
        self.preview.getPlotItem().setMenuEnabled(False)
        self.preview.addLegend(offset=(10, 10))
        self.info = InfoBrowser()
        self.info.setMinimumHeight(90)
        details = QSplitter(Qt.Orientation.Vertical)
        details.addWidget(self.preview)
        details.addWidget(self.info)
        details.setSizes([260, 180])

        splitter = QSplitter()
        splitter.addWidget(libraries)
        splitter.addWidget(spectra)
        splitter.addWidget(details)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([220, 380, 380])

        self.setLayout(
            VBoxBuilder().withWidget(self.missingBox).withWidget(splitter, stretch=1)
        )
        self.resize(1000, 620)
        connectWhileAlive(self.store.changed, self.refresh, self)
        self.refresh()

    # --- showing ----------------------------------------------------------------------

    def refresh(self) -> None:
        """List the store again, keeping the selected library."""
        current = self.currentLibrary()
        currentName = current.name if current is not None else ""
        with QSignalBlocker(self.libraryList):
            self.libraryList.clear()
            for library in self.store.libraries():
                item = QListWidgetItem(library.label())
                item.setData(Qt.ItemDataRole.UserRole, library.name)
                if not library.editable:
                    item.setToolTip(f"{library.name}: {library.kind.value}")
                    item.setForeground(self.palette().placeholderText())
                else:
                    item.setToolTip(f"{library.name}: yours, editable")
                self.libraryList.addItem(item)
        names = self.store.names()
        if current is not None and current in self.store.libraries():
            row = self.store.libraries().index(current)
        else:
            row = names.index(currentName) if currentName in names else 0
        self.libraryList.setCurrentRow(row)
        self._showLibrary()
        self._showMissing()

    def _showMissing(self) -> None:
        missing = self.store.unavailable
        self.missingBox.setVisible(bool(missing))
        if missing:
            listed = "<br>".join(
                f"{escape(str(path))} ({escape(why)})" for path, why in missing.items()
            )
            self.missingLabel.setHtml(
                f"<b>{len(missing)} remembered librar{'y' if len(missing) == 1 else 'ies'} "
                f"couldn't be read</b>, so it isn't listed:<br>{listed}<br>"
                "Reconnect the drive or load the file from its new place."
            )

    def currentLibrary(self) -> SpectralLibrary | None:
        item = self.libraryList.currentItem()
        if item is None:
            return None
        return self.store.library(item.data(Qt.ItemDataRole.UserRole))

    def selectLibrary(self, name: str) -> None:
        names = self.store.names()
        if name in names:
            self.libraryList.setCurrentRow(names.index(name))

    def _showLibrary(self) -> None:
        library = self.currentLibrary()
        spectra = [] if library is None else library.spectra
        with QSignalBlocker(self.table):
            self.table.setRowCount(len(spectra))
            for row, spectrum in enumerate(spectra):
                for column, text in enumerate(
                    (spectrum.name, spectrum.valueUnits, spectrum.source)
                ):
                    item = QTableWidgetItem(text)
                    item.setToolTip(text)
                    self.table.setItem(row, column, item)
        editable = library is not None and library.editable
        removable = library is not None and library.kind is not LibraryKind.BUNDLED
        self.renameLibraryButton.setEnabled(editable)
        self.removeLibraryButton.setEnabled(removable)
        self.removeLibraryButton.setText(
            "Forget"
            if library is not None and library.kind is LibraryKind.FILE
            else "Delete…"
        )
        self.exportButton.setEnabled(library is not None)
        self.renameSpectrumButton.setEnabled(editable)
        self.removeSpectraButton.setEnabled(editable)
        self._applySearch()
        self._showSelection()

    def _applySearch(self) -> None:
        text = self.searchEdit.text().strip().lower()
        shown = 0
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            hidden = bool(text) and (item is None or text not in item.text().lower())
            self.table.setRowHidden(row, hidden)
            shown += not hidden
        self.countLabel.setText(f"{shown} of {self.table.rowCount()} spectra shown")

    def selectedRows(self) -> list[int]:
        model = self.table.selectionModel()
        if model is None:
            return []
        return sorted(
            i.row() for i in model.selectedRows() if not self.table.isRowHidden(i.row())
        )

    def selectedSpectra(self) -> list[LibrarySpectrum]:
        library = self.currentLibrary()
        if library is None:
            return []
        return [library.spectra[row] for row in self.selectedRows()]

    def selectRows(self, rows: list[int]) -> None:
        self.table.clearSelection()
        mode = self.table.selectionMode()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for row in rows:
            self.table.selectRow(row)
        self.table.setSelectionMode(mode)

    def _showSelection(self) -> None:
        library = self.currentLibrary()
        spectra = self.selectedSpectra()
        self.preview.clear()
        for i, spectrum in enumerate(spectra[:20]):
            pen = pg.mkPen(_PREVIEW_COLORS[i % len(_PREVIEW_COLORS)], width=1.5)
            x, y = np.asarray(spectrum.wavelengths), np.asarray(spectrum.reflectance)
            self.preview.plot(x, y, pen=pen, name=spectrum.name, connect="finite")
        self.copyButton.setEnabled(bool(spectra))
        self.renameSpectrumButton.setEnabled(
            library is not None and library.editable and len(spectra) == 1
        )
        self.removeSpectraButton.setEnabled(
            library is not None and library.editable and bool(spectra)
        )
        html = "" if library is None else libraryHtml(library)
        if len(spectra) == 1:
            html = provenanceHtml(spectra[0]) + "<hr>" + html
        elif spectra:
            html = f"<p>{len(spectra)} spectra selected.</p><hr>" + html
        self.info.setHtml(html)

    # --- library actions ----------------------------------------------------------------

    def newLibrary(self, name: str) -> SpectralLibrary:
        library = self.store.createLibrary(name)
        self.selectLibrary(library.name)
        return library

    def _new(self) -> None:
        name, ok = QInputDialog.getText(
            self, "New Library", "Name:", text=self.store.uniqueName("My spectra")
        )
        if ok and name.strip():
            self.newLibrary(name.strip())

    def loadLibrary(self, path: str | Path) -> SpectralLibrary | None:
        try:
            library = self.store.loadFile(path)
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(
                self, "Load Library", f"Could not read {path}:\n{error}"
            )
            return None
        self.selectLibrary(library.name)
        return library

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Spectral Library", "", LIBRARY_FILE_FILTER
        )
        if path:
            self.loadLibrary(path)

    def _loadFolder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Load a USGS splib07 Folder")
        if path:
            self.loadLibrary(path)

    def renameLibrary(self, name: str) -> None:
        library = self.currentLibrary()
        if library is not None and library.editable and name.strip():
            self.store.renameLibrary(library, name.strip())
            self.selectLibrary(library.name)

    def _renameLibrary(self) -> None:
        library = self.currentLibrary()
        if library is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename Library", "Name:", text=library.name
        )
        if ok:
            self.renameLibrary(name)

    def removeCurrentLibrary(self) -> None:
        library = self.currentLibrary()
        if library is not None and library.kind is not LibraryKind.BUNDLED:
            self.store.removeLibrary(library)

    def _removeLibrary(self) -> None:
        library = self.currentLibrary()
        if library is None:
            return
        if library.editable:
            answer = QMessageBox.question(
                self,
                "Delete Library",
                f"Delete “{library.name}” and its {len(library.spectra)} spectra? "
                "Its files are removed; this can't be undone.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.removeCurrentLibrary()

    def exportCurrent(self, path: str | Path) -> Path | None:
        library = self.currentLibrary()
        if library is None:
            return None
        try:
            return self.store.exportLibrary(library, path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(
                self, "Export Library", f"Could not write {path}:\n{error}"
            )
            return None

    def _export(self) -> None:
        library = self.currentLibrary()
        if library is None:
            return
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export Library", f"{library.name}.sli", EXPORT_FILTER
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() not in {".sli", ".hdr", ".csv"}:
            target = target.with_suffix(".csv" if "CSV" in chosen else ".sli")
        self.exportCurrent(target)

    # --- spectrum actions ---------------------------------------------------------------

    def renameSelectedSpectrum(self, name: str) -> None:
        library = self.currentLibrary()
        rows = self.selectedRows()
        if (
            library is None
            or not library.editable
            or len(rows) != 1
            or not name.strip()
        ):
            return
        self.store.renameSpectrum(library, rows[0], name.strip())
        self.selectRows(rows)

    def _renameSpectrum(self) -> None:
        spectra = self.selectedSpectra()
        if len(spectra) != 1:
            return
        name, ok = QInputDialog.getText(
            self, "Rename Spectrum", "Name:", text=spectra[0].name
        )
        if ok:
            self.renameSelectedSpectrum(name)

    def removeSelectedSpectra(self) -> None:
        library = self.currentLibrary()
        if library is not None and library.editable:
            self.store.removeSpectra(library, self.selectedRows())

    def copyDialog(self) -> AddToLibraryDialog | None:
        """Add to Library for the selected spectra, their records kept."""
        library = self.currentLibrary()
        spectra = self.selectedSpectra()
        if library is None or not spectra:
            return None
        copies = [copiedSpectrum(s, library.name) for s in spectra]
        dialog = AddToLibraryDialog(copies, self.store, parent=self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        return dialog

    def _copySelected(self) -> None:
        dialog = self.copyDialog()
        if dialog is not None:
            dialog.open()


_open: SpectralLibraryManager | None = None


def openLibraryManager(
    parent: QWidget | None = None, store: LibraryStore | None = None
) -> SpectralLibraryManager:
    """Show the manager, reusing the open one; non-modal, so plots and images
    stay usable alongside."""
    global _open
    store = defaultStore() if store is None else store
    if _open is None or sip.isdeleted(_open) or _open.store is not store:
        _open = SpectralLibraryManager(store, parent)
        _open.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    _open.show()
    _open.raise_()
    _open.activateWindow()
    return _open
