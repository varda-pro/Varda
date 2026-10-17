"""Add spectra to one of your libraries, or to a new one."""

from __future__ import annotations

import attrs
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QWidget,
)

from varda.common.ui import ElidingComboBox, MutedLabel, VBoxBuilder
from varda.library_manager.info_view import InfoBrowser, provenanceHtml
from varda.spectral_libraries.spectrum import LibrarySpectrum
from varda.spectral_libraries.store import LibraryStore, defaultStore

NEW_LIBRARY = "New library…"
# The library spectra were last added to, offered first next time
_lastTarget: str = ""


class AddToLibraryDialog(QDialog):
    """Pick (or name) one of your libraries and add ``spectra`` to it, with
    the provenance they carry."""

    sigAdded = pyqtSignal(str)  # the library's name

    def __init__(
        self,
        spectra: list[LibrarySpectrum],
        store: LibraryStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add to Library")
        self.store = defaultStore() if store is None else store
        self.spectra = spectra

        self.targetBox = ElidingComboBox()
        for library in self.store.userLibraries():
            self.targetBox.addItem(library.label(), library.name)
        self.targetBox.addItem(NEW_LIBRARY, "")
        index = self.targetBox.findData(_lastTarget) if _lastTarget else 0
        self.targetBox.setCurrentIndex(max(index, 0))
        self.newNameEdit = QLineEdit(self.store.uniqueName("My spectra"))
        self.targetBox.currentIndexChanged.connect(lambda _: self._updateNewName())

        self.nameEdit = QLineEdit(spectra[0].name if len(spectra) == 1 else "")
        self.nameEdit.setVisible(len(spectra) == 1)
        self.unitsEdit = QLineEdit(spectra[0].valueUnits)
        self.unitsEdit.setPlaceholderText("e.g. reflectance, I/F (blank: not stated)")
        self.unitsEdit.setToolTip(
            "What the values are; recorded with each spectrum that doesn't "
            "already state it"
        )

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow("Library", self.targetBox)
        form.addRow("New library's name", self.newNameEdit)
        if len(spectra) == 1:
            form.addRow("Spectrum name", self.nameEdit)
        form.addRow("Value units", self.unitsEdit)

        names = ", ".join(s.name for s in spectra[:6]) + (
            "…" if len(spectra) > 6 else ""
        )
        self.summaryLabel = MutedLabel(
            f"{len(spectra)} spectra ({names}). Recorded with each, e.g. the first:"
            if len(spectra) > 1
            else "Recorded with it:"
        )
        self.summaryLabel.setWordWrap(True)
        self.provenanceView = InfoBrowser()
        self.provenanceView.setHtml(provenanceHtml(spectra[0]))
        self.provenanceView.setMinimumHeight(90)
        self.provenanceView.setMaximumHeight(170)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.okButton = buttons.button(QDialogButtonBox.StandardButton.Ok)

        self.setLayout(
            VBoxBuilder()
            .withLayout(form)
            .withWidget(self.summaryLabel)
            .withWidget(self.provenanceView)
            .withWidget(buttons)
        )
        self.setMinimumWidth(380)
        self._form = form
        self._updateNewName()

    def _isNew(self) -> bool:
        return not self.targetBox.currentData()

    def _updateNewName(self) -> None:
        self._form.setRowVisible(self.newNameEdit, self._isNew())

    def finalSpectra(self) -> list[LibrarySpectrum]:
        """The spectra as they will be added: renamed, and with value units."""
        units = self.unitsEdit.text().strip()
        spectra = [
            s if s.valueUnits else attrs.evolve(s, valueUnits=units)
            for s in self.spectra
        ]
        name = self.nameEdit.text().strip()
        if len(spectra) == 1 and name:
            spectra[0] = attrs.evolve(spectra[0], name=name)
        return spectra

    def accept(self) -> None:
        global _lastTarget
        if self._isNew():
            library = self.store.createLibrary(
                self.newNameEdit.text().strip() or "My spectra"
            )
        else:
            library = self.store.library(self.targetBox.currentData())
            assert library is not None
        self.store.addSpectra(library, self.finalSpectra())
        _lastTarget = library.name
        self.sigAdded.emit(library.name)
        super().accept()
