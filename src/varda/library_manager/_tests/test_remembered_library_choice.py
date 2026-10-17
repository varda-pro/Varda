"""A plot's Libraries choice: the libraries turned off are remembered, the most
recent choice is the default for new plots (across restarts), libraries added
later come in on, and renaming a library keeps it off."""

import numpy as np
import pytest

from varda.library_manager.reference_picker import ReferenceSpectraPicker
from varda.spectral_libraries.spectrum import LibrarySpectrum
from varda.spectral_libraries.store import (
    MICA_LIBRARY_NAME,
    USGS_LIBRARY_NAME,
    LibraryStore,
)

WL = np.linspace(1000.0, 2500.0, 50)


def _spectrum(name: str) -> LibrarySpectrum:
    return LibrarySpectrum(name, WL, np.linspace(0.2, 0.4, 50), "test")


@pytest.fixture
def folder(tmp_path):
    return tmp_path / "libraries"


def _picker(qtbot, store: LibraryStore) -> ReferenceSpectraPicker:
    picker = ReferenceSpectraPicker(store)
    qtbot.addWidget(picker)
    return picker


def test_a_new_plot_starts_with_the_most_recent_choice_after_a_restart(qtbot, folder):
    first = _picker(qtbot, LibraryStore(folder))
    first.setIncluded(MICA_LIBRARY_NAME, False)

    restarted = LibraryStore(folder)  # the app started again
    picker = _picker(qtbot, restarted)
    assert picker.excludedLibraries() == [MICA_LIBRARY_NAME]
    assert picker.includedLibraries() == [USGS_LIBRARY_NAME]
    assert picker.librariesButton.text() == "Libraries (1 of 2)"


def test_all_turns_every_library_on_and_is_remembered(qtbot, folder):
    store = LibraryStore(folder)
    picker = _picker(qtbot, store)
    picker.setIncluded(USGS_LIBRARY_NAME, False)
    picker._includeAll()
    assert picker.excludedLibraries() == []
    assert LibraryStore(folder).plotLibrariesOff() == set()


def test_restoring_a_plots_choice_ignores_unknown_names_and_keeps_the_default(
    qtbot, folder
):
    store = LibraryStore(folder)
    picker = _picker(qtbot, store)
    picker.setExcluded([USGS_LIBRARY_NAME, "A library since deleted"])
    assert picker.excludedLibraries() == [USGS_LIBRARY_NAME]
    assert picker.includedLibraries() == [MICA_LIBRARY_NAME]
    # Restoring a session is not a new choice: new plots keep the default
    assert store.plotLibrariesOff() == set()


def test_a_library_added_later_comes_in_on(qtbot, folder):
    store = LibraryStore(folder)
    picker = _picker(qtbot, store)
    picker.setIncluded(MICA_LIBRARY_NAME, False)
    store.createLibrary("Dunes", [_spectrum("sand")])
    assert picker.includedLibraries() == [USGS_LIBRARY_NAME, "Dunes"]
    assert _picker(qtbot, store).includedLibraries() == [USGS_LIBRARY_NAME, "Dunes"]


def test_a_renamed_library_stays_off_in_open_and_new_plots(qtbot, folder):
    store = LibraryStore(folder)
    library = store.createLibrary("Dunes", [_spectrum("sand")])
    picker = _picker(qtbot, store)
    picker.setIncluded("Dunes", False)
    store.renameLibrary(library, "Dune field")
    assert picker.excludedLibraries() == ["Dune field"]
    assert "Dune field" not in picker.includedLibraries()
    assert _picker(qtbot, LibraryStore(folder)).excludedLibraries() == ["Dune field"]


def test_a_restored_choice_follows_a_rename_too(qtbot, folder):
    store = LibraryStore(folder)
    library = store.createLibrary("Dunes", [_spectrum("sand")])
    picker = _picker(qtbot, store)
    picker.setExcluded(["Dunes"])
    store.renameLibrary(library, "Dune field")
    assert picker.excludedLibraries() == ["Dune field"]
