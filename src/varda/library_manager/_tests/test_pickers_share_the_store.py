"""Every library list draws from the shared store: the plots' reference
picker and the FATT/Spectral Hourglass picker follow its changes."""

import numpy as np
import pytest
from PyQt6.QtCore import Qt

from varda.analysis.library_picker import LibraryPicker
from varda.common.layout_audit import assertNothingClipped
from varda.library_manager.reference_picker import ReferenceSpectraPicker
from varda.plotting.plot import VardaPlotWidget
from varda.spectral_libraries.formats import writeCsvLibrary
from varda.spectral_libraries.spectrum import LibrarySpectrum
from varda.spectral_libraries.store import (
    MICA_LIBRARY_NAME,
    USGS_LIBRARY_NAME,
    LibraryStore,
    defaultStore,
)

WL = np.linspace(1000.0, 2500.0, 50)


def _spectrum(name: str) -> LibrarySpectrum:
    return LibrarySpectrum(name, WL, np.linspace(0.2, 0.4, 50), "test")


@pytest.fixture
def store(tmp_path):
    return LibraryStore(tmp_path / "libraries")


def _names(picker: ReferenceSpectraPicker) -> list[str]:
    return [
        item.text() for item in picker.list.findItems("", Qt.MatchFlag.MatchContains)
    ]


def test_the_reference_picker_offers_every_library_with_search(qtbot, store):
    picker = ReferenceSpectraPicker(store)
    qtbot.addWidget(picker)
    total = sum(len(lib.spectra) for lib in store.libraries())
    assert picker.list.count() == total
    assert "2 of 2" in picker.librariesButton.text()
    assert any(name.endswith(f"· {MICA_LIBRARY_NAME}") for name in _names(picker))

    picker.searchEdit.setText("kaolin")
    assert picker.list.count() >= 2  # one in each bundled library
    assert all("kaolin" in name.lower() for name in _names(picker))


def test_choosing_one_library_limits_the_spectra_to_it(qtbot, store):
    picker = ReferenceSpectraPicker(store)
    qtbot.addWidget(picker)
    picker.setIncluded(MICA_LIBRARY_NAME, False)
    usgs = store.library(USGS_LIBRARY_NAME)
    assert usgs is not None
    assert picker.list.count() == len(usgs.spectra)
    assert picker.librariesButton.text() == "Libraries (1 of 2)"
    assert picker.fromLabel.text() == f"From: {USGS_LIBRARY_NAME}"
    assert _names(picker)[0] == usgs.spectra[0].name  # no library suffix
    picker._fillMenu()
    ticked = [a.text() for a in picker.librariesMenu.actions() if a.isChecked()]
    assert ticked == [usgs.label()]


def test_a_library_made_anywhere_shows_up_in_every_picker(qtbot, store):
    reference = ReferenceSpectraPicker(store)
    single = LibraryPicker(store=store)
    for widget in (reference, single):
        qtbot.addWidget(widget)
    before = reference.list.count()

    library = store.createLibrary("Field site", [_spectrum("dune")])
    assert reference.list.count() == before + 1
    assert single.box.findData("Field site") >= 0

    single.selectLibrary("Field site")
    with qtbot.waitSignal(single.sigLibraryChanged):
        store.addSpectra(library, [_spectrum("crater floor")])
    assert [s.name for s in single.library()] == ["dune", "crater floor"]


def test_a_library_loaded_in_one_picker_appears_in_the_others(qtbot, store, tmp_path):
    path = tmp_path / "theirs.csv"
    writeCsvLibrary(path, [_spectrum("x")])
    fatt, hourglass = LibraryPicker(store=store), LibraryPicker(store=store)
    for widget in (fatt, hourglass):
        qtbot.addWidget(widget)
    fatt.loadFile(str(path))
    assert fatt.libraryName() == "theirs"
    assert hourglass.box.findData("theirs") >= 0
    assert hourglass.libraryName() == USGS_LIBRARY_NAME  # its own choice is kept


def test_adding_spectra_from_the_picker_plots_them_as_references(qtbot):
    plot = VardaPlotWidget()
    qtbot.addWidget(plot)
    assert plot.referencePicker.store is defaultStore()
    plot.referencePicker.searchEdit.setText("Kaolinite")
    plot.referencePicker.list.setCurrentRow(0)
    plot.referencePicker.addButton.click()
    assert len(plot.plots) == 1
    assert plot.plots[0].librarySpectrum is not None
    assert "Kaolinite" in plot.plots[0].librarySpectrum.name


def test_missing_remembered_libraries_are_mentioned(qtbot, tmp_path):
    folder = tmp_path / "libraries"
    path = tmp_path / "gone.csv"
    writeCsvLibrary(path, [_spectrum("x")])
    LibraryStore(folder).loadFile(path)
    path.unlink()
    picker = ReferenceSpectraPicker(LibraryStore(folder))
    qtbot.addWidget(picker)
    assert "1 remembered library missing" in picker.statusLabel.text()


def test_the_plot_sidebar_with_the_picker_fits_a_laptop_screen(qtbot):
    plot = VardaPlotWidget()
    qtbot.addWidget(plot)
    plot.resize(1280, 800)
    plot.show()
    qtbot.waitExposed(plot)
    assertNothingClipped(plot)
    plot.referencePicker.setIncluded(MICA_LIBRARY_NAME, False)
    assertNothingClipped(plot)
