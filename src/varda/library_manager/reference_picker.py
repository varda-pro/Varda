"""Pick reference spectra for a plot from any libraries in the shared store."""

from __future__ import annotations

from collections.abc import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from varda.common.parameter_widgets import connectWhileAlive
from varda.common.ui import FlowLayout, MutedLabel, VBoxBuilder
from varda.library_manager.manager_window import openLibraryManager
from varda.spectral_libraries.spectrum import LibrarySpectrum
from varda.spectral_libraries.store import LibraryStore, defaultStore


class ReferenceSpectraPicker(QWidget):
    """Which libraries to draw from (one or several), a name search over
    their spectra, and Add to Plot."""

    sigAddRequested = pyqtSignal(object)  # list[LibrarySpectrum]

    def __init__(
        self, store: LibraryStore | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.store = defaultStore() if store is None else store
        # Libraries left out, by name; any library not unticked is searched,
        # so one created or loaded elsewhere shows up straight away. A new
        # plot starts with the most recent choice made in any plot.
        self._excluded = self._known(self.store.plotLibrariesOff())

        self.librariesButton = QToolButton()
        self.librariesButton.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.librariesButton.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.librariesMenu = QMenu(self.librariesButton)
        self.librariesMenu.aboutToShow.connect(self._fillMenu)
        self.librariesButton.setMenu(self.librariesMenu)
        self.librariesButton.setToolTip("Choose the libraries to pick spectra from")
        self.librariesButton.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.fromLabel = MutedLabel()  # the chosen libraries, wrapped
        self.fromLabel.setWordWrap(True)

        self.searchEdit = QLineEdit()
        self.searchEdit.setPlaceholderText("Search by name")
        self.searchEdit.setClearButtonEnabled(True)
        self.searchEdit.textChanged.connect(lambda _: self.refresh())
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setMinimumHeight(110)
        self.list.setMaximumHeight(200)
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.list.itemDoubleClicked.connect(lambda _: self._addSelected())
        self.addButton = QPushButton("Add to Plot")
        self.addButton.setToolTip("Plot the selected spectra (double-click works too)")
        self.addButton.clicked.connect(self._addSelected)
        manageButton = QPushButton("Manage…")
        manageButton.setToolTip("Open the Spectral Library Manager")
        manageButton.clicked.connect(
            lambda: openLibraryManager(self.window(), self.store)
        )
        self.statusLabel = MutedLabel()
        self.statusLabel.setWordWrap(True)

        buttons = FlowLayout()
        buttons.addWidget(self.addButton)
        buttons.addWidget(manageButton)
        self.setLayout(
            VBoxBuilder(margins=0)
            .withWidget(self.librariesButton)
            .withWidget(self.fromLabel)
            .withWidget(self.searchEdit)
            .withWidget(self.list)
            .withLayout(buttons)
            .withWidget(self.statusLabel)
        )
        connectWhileAlive(self.store.changed, self.refresh, self)
        connectWhileAlive(self.store.renamed, self._onRenamed, self)
        self.refresh()

    # --- which libraries ----------------------------------------------------------------

    def includedLibraries(self) -> list[str]:
        return [n for n in self.store.names() if n not in self._excluded]

    def excludedLibraries(self) -> list[str]:
        """The libraries turned off, by name (what a session saves)."""
        return sorted(self._excluded)

    def setExcluded(self, names: Iterable[str]) -> None:
        """Turn off exactly these libraries (e.g. restoring a session); names
        of libraries that no longer exist are ignored. New plots' default is
        left as it is: that follows the user's own choices."""
        self._excluded = self._known(names)
        self.refresh()

    def setIncluded(self, name: str, included: bool) -> None:
        if included:
            self._excluded.discard(name)
        else:
            self._excluded.add(name)
        self._chosen()

    def _known(self, names: Iterable[str]) -> set[str]:
        return set(names) & set(self.store.names())

    def _chosen(self) -> None:
        """The user changed the choice: remember it for new plots, too."""
        self.store.setPlotLibrariesOff(self._excluded)
        self.refresh()

    def _onRenamed(self, old: str, new: str) -> None:
        if old in self._excluded:
            self._excluded = self._excluded - {old} | {new}

    def _fillMenu(self) -> None:
        self.librariesMenu.clear()
        for library in self.store.libraries():
            action = self.librariesMenu.addAction(library.label())
            assert action is not None
            action.setCheckable(True)
            action.setChecked(library.name not in self._excluded)
            action.toggled.connect(
                lambda checked, n=library.name: self.setIncluded(n, checked)
            )
        self.librariesMenu.addSeparator()
        self.librariesMenu.addAction("All", self._includeAll)
        self.librariesMenu.addAction(
            "Manage Libraries…", lambda: openLibraryManager(self.window(), self.store)
        )

    def _includeAll(self) -> None:
        self._excluded.clear()
        self._chosen()

    # --- spectra ------------------------------------------------------------------------

    def refresh(self) -> None:
        included = [
            lib for lib in self.store.libraries() if lib.name not in self._excluded
        ]
        total = len(self.store.names())
        self.librariesButton.setText(f"Libraries ({len(included)} of {total})")
        self.fromLabel.setText(
            "From: " + (", ".join(lib.name for lib in included) or "no library")
        )
        text = self.searchEdit.text().strip().lower()
        self.list.clear()
        for library in included:
            for index, spectrum in enumerate(library.spectra):
                if text and text not in spectrum.name.lower():
                    continue
                label = (
                    spectrum.name
                    if len(included) == 1
                    else f"{spectrum.name} · {library.name}"
                )
                item = QListWidgetItem(label)
                item.setToolTip(f"{spectrum.name}\n{library.name}")
                item.setData(Qt.ItemDataRole.UserRole, (library.name, index))
                self.list.addItem(item)
        notes = [f"{self.list.count()} spectra"]
        if self.store.unavailable:
            count = len(self.store.unavailable)
            notes.append(
                f"{count} remembered librar{'y' if count == 1 else 'ies'} missing: "
                "see Manage…"
            )
        self.statusLabel.setText(" · ".join(notes))

    def selectedSpectra(self) -> list[LibrarySpectrum]:
        spectra = []
        for item in self.list.selectedItems():
            name, index = item.data(Qt.ItemDataRole.UserRole)
            library = self.store.library(name)
            if library is not None and index < len(library.spectra):
                spectra.append(library.spectra[index])
        return spectra

    def _addSelected(self) -> None:
        spectra = self.selectedSpectra()
        if spectra:
            self.sigAddRequested.emit(spectra)
