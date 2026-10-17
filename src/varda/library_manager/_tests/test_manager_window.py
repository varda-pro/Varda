"""The Spectral Library Manager: browsing, building, editing, exporting."""

import numpy as np
import pytest

from varda.common.layout_audit import assertNothingClipped
from varda.library_manager.add_to_library_dialog import NEW_LIBRARY
from varda.library_manager.manager_window import (
    SpectralLibraryManager,
    openLibraryManager,
)
from varda.spectral_libraries.formats import loadCsvLibrary, loadEnviLibrary
from varda.spectral_libraries.spectrum import LibrarySpectrum
from varda.spectral_libraries.store import (
    MICA_LIBRARY_NAME,
    USGS_LIBRARY_NAME,
    LibraryStore,
)

WL = np.linspace(1000.0, 2500.0, 50)


def _spectrum(name: str) -> LibrarySpectrum:
    return LibrarySpectrum(
        name,
        WL,
        np.linspace(0.2, 0.4, 50),
        "scene",
        valueUnits="I/F",
        provenance={"sourceImage": "scene", "region": f"ROI “{name}”"},
    )


@pytest.fixture
def store(tmp_path):
    return LibraryStore(tmp_path / "libraries")


@pytest.fixture
def manager(qtbot, store):
    window = SpectralLibraryManager(store)
    qtbot.addWidget(window)
    return window


def test_bundled_libraries_are_listed_read_only_with_their_citation(manager):
    manager.selectLibrary(USGS_LIBRARY_NAME)
    assert manager.table.rowCount() == len(
        manager.store.library(USGS_LIBRARY_NAME).spectra
    )
    assert not manager.renameLibraryButton.isEnabled()
    assert not manager.removeLibraryButton.isEnabled()
    assert manager.exportButton.isEnabled()
    assert "https://doi.org/10.3133/ds1035" in manager.info.toPlainText()
    manager.selectLibrary(MICA_LIBRARY_NAME)
    assert "Viviano-Beck" in manager.info.toPlainText()


def test_search_filters_the_spectra(manager):
    manager.selectLibrary(USGS_LIBRARY_NAME)
    manager.searchEdit.setText("kaolin")
    shown = [
        r for r in range(manager.table.rowCount()) if not manager.table.isRowHidden(r)
    ]
    assert shown and all(
        "kaolin" in manager.table.item(r, 0).text().lower() for r in shown
    )
    assert f"{len(shown)} of" in manager.countLabel.text()


def test_selecting_spectra_previews_them_and_shows_their_record(manager):
    library = manager.store.createLibrary("Mine", [_spectrum("a"), _spectrum("b")])
    manager.selectLibrary(library.name)
    manager.selectRows([0])
    assert len(manager.preview.getPlotItem().listDataItems()) == 1
    text = manager.info.toPlainText()
    assert "ROI “a”" in text and "I/F" in text
    manager.selectRows([0, 1])
    assert len(manager.preview.getPlotItem().listDataItems()) == 2


def test_building_and_editing_your_own_library(manager, store):
    manager.newLibrary("Field site")
    assert manager.currentLibrary().name == "Field site"
    store.addSpectra(store.library("Field site"), [_spectrum("a"), _spectrum("b")])
    assert manager.table.rowCount() == 2
    manager.selectRows([0])
    manager.renameSelectedSpectrum("alpha")
    assert store.library("Field site").spectra[0].name == "alpha"
    manager.selectRows([1])
    manager.removeSelectedSpectra()
    assert [s.name for s in store.library("Field site").spectra] == ["alpha"]
    manager.renameLibrary("Site A")
    assert manager.currentLibrary().name == "Site A"
    manager.removeCurrentLibrary()
    assert store.library("Site A") is None


def test_copying_bundled_spectra_into_a_new_library_keeps_their_record(manager, store):
    manager.selectLibrary(MICA_LIBRARY_NAME)
    manager.selectRows([0, 1])
    dialog = manager.copyDialog()
    assert dialog is not None
    dialog.targetBox.setCurrentIndex(dialog.targetBox.findText(NEW_LIBRARY))
    dialog.newNameEdit.setText("CRISM picks")
    dialog.accept()
    library = store.library("CRISM picks")
    assert library is not None and len(library.spectra) == 2
    assert library.spectra[0].provenance["library"] == MICA_LIBRARY_NAME
    assert library.spectra[0].provenance["copiedFrom"] == MICA_LIBRARY_NAME
    assert library.spectra[0].valueUnits == "I/F"


def test_exporting_as_envi_and_csv(manager, store, tmp_path):
    store.createLibrary("Mine", [_spectrum("a")])
    manager.selectLibrary("Mine")
    header = manager.exportCurrent(tmp_path / "out.sli")
    assert header is not None
    assert loadEnviLibrary(header)[0].provenance["region"] == "ROI “a”"
    manager.exportCurrent(tmp_path / "out.csv")
    assert loadCsvLibrary(tmp_path / "out.csv")[0].valueUnits == "I/F"


def test_loading_a_library_selects_it(manager, tmp_path):
    path = tmp_path / "theirs.csv"
    path.write_text("wavelength_nm,x\n1000,0.1\n1100,0.2\n")
    manager.loadLibrary(path)
    assert manager.currentLibrary().name == "theirs"
    assert manager.removeLibraryButton.text() == "Forget"


def test_missing_files_are_shown_until_forgotten(qtbot, tmp_path):
    folder = tmp_path / "libraries"
    path = tmp_path / "gone.csv"
    path.write_text("wavelength_nm,x\n1000,0.1\n1100,0.2\n")
    LibraryStore(folder).loadFile(path)
    path.unlink()
    window = SpectralLibraryManager(LibraryStore(folder))
    qtbot.addWidget(window)
    assert not window.missingBox.isHidden()
    assert "gone.csv" in window.missingLabel.toPlainText()
    window.forgetMissingButton.click()
    assert window.missingBox.isHidden()


def test_the_menu_opens_one_manager(qtbot, store):
    first = openLibraryManager(store=store)
    qtbot.addWidget(first)
    assert openLibraryManager(store=store) is first


def test_the_manager_fits_a_laptop_screen(qtbot, manager):
    manager.store.createLibrary("A library with a rather long name", [_spectrum("a")])
    manager.resize(1280, 800)
    manager.show()
    qtbot.waitExposed(manager)
    assertNothingClipped(manager)
    manager.resize(manager.minimumSizeHint())
    assert manager.minimumSizeHint().width() <= 1280
    assert manager.minimumSizeHint().height() <= 800
