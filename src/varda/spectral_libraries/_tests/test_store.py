"""The app-wide library store: bundled, yours and loaded libraries, kept
across restarts, and telling everyone when anything changes."""

import numpy as np
import pytest

from varda.spectral_libraries.formats import writeCsvLibrary, writeEnviLibrary
from varda.spectral_libraries.spectrum import LibrarySpectrum
from varda.spectral_libraries.store import (
    MICA_LIBRARY_NAME,
    USGS_LIBRARY_NAME,
    LibraryKind,
    LibraryStore,
    defaultStore,
    setDefaultStore,
)

WL = np.linspace(1000.0, 2500.0, 151)


def _spectrum(name: str, depth: float = 0.2) -> LibrarySpectrum:
    values = 0.5 - depth * np.exp(-0.5 * ((WL - 2200) / 25) ** 2)
    return LibrarySpectrum(
        name, WL, values, "test", valueUnits="reflectance", provenance={"region": name}
    )


@pytest.fixture
def folder(tmp_path):
    return tmp_path / "libraries"


def _restart(folder) -> LibraryStore:
    """A new store on the same folder: what the app sees after a restart."""
    return LibraryStore(folder)


def test_the_bundled_libraries_are_always_there_and_read_only(folder):
    store = LibraryStore(folder)
    assert store.names()[:2] == [MICA_LIBRARY_NAME, USGS_LIBRARY_NAME]
    usgs = store.library(USGS_LIBRARY_NAME)
    assert usgs is not None and usgs.kind is LibraryKind.BUNDLED
    assert "Data Series 1035" in usgs.citation
    mica = store.library(MICA_LIBRARY_NAME)
    assert mica is not None and "Viviano-Beck" in mica.citation
    assert mica.spectra[0].valueUnits == "I/F"
    with pytest.raises(ValueError):
        store.addSpectra(usgs, [_spectrum("x")])
    with pytest.raises(ValueError):
        store.removeLibrary(usgs)


def test_your_libraries_are_saved_as_envi_and_survive_a_restart(folder):
    store = LibraryStore(folder)
    library = store.createLibrary("Field site")
    store.addSpectra(library, [_spectrum("a"), _spectrum("b", 0.1)])
    assert (folder / "Field site.hdr").is_file()
    assert (folder / "Field site.sli").is_file()

    after = _restart(folder).library("Field site")
    assert after is not None and after.kind is LibraryKind.USER
    assert [s.name for s in after.spectra] == ["a", "b"]
    assert after.spectra[1].provenance == {"region": "b"}
    np.testing.assert_array_equal(
        after.spectra[1].reflectance, library.spectra[1].reflectance
    )


def test_renaming_and_removing_spectra_and_libraries_is_saved(folder):
    store = LibraryStore(folder)
    library = store.createLibrary(
        "Mine", [_spectrum("a"), _spectrum("b"), _spectrum("c")]
    )
    store.renameSpectrum(library, 0, "alpha")
    store.removeSpectra(library, [1])
    store.renameLibrary(library, "Renamed")
    assert not (folder / "Mine.hdr").exists()
    after = _restart(folder).library("Renamed")
    assert after is not None
    assert [s.name for s in after.spectra] == ["alpha", "c"]

    store.removeLibrary(library)
    assert not list(folder.glob("Renamed*"))
    assert _restart(folder).library("Renamed") is None


def test_names_stay_unique(folder):
    store = LibraryStore(folder)
    first = store.createLibrary("Mine")
    second = store.createLibrary("Mine")
    assert (first.name, second.name) == ("Mine", "Mine (2)")
    assert {lib.name for lib in _restart(folder).userLibraries()} == {
        "Mine",
        "Mine (2)",
    }


def test_odd_characters_in_a_name_make_a_safe_file_name(folder):
    store = LibraryStore(folder)
    store.createLibrary("Gale/Mt. Sharp: clays", [_spectrum("a")])
    assert len(list(folder.glob("*.hdr"))) == 1
    assert _restart(folder).library("Gale/Mt. Sharp: clays") is not None


def test_loaded_files_are_remembered_and_read_only(folder, tmp_path):
    path = tmp_path / "elsewhere" / "theirs.csv"
    path.parent.mkdir()
    writeCsvLibrary(path, [_spectrum("x")])
    store = LibraryStore(folder)
    library = store.loadFile(path)
    assert library.kind is LibraryKind.FILE and not library.editable
    assert store.loadFile(path) is library  # loading again doesn't duplicate

    after = _restart(folder).library("theirs")
    assert after is not None and [s.name for s in after.spectra] == ["x"]

    store.removeLibrary(library)  # forgets it, keeps the file
    assert path.is_file()
    assert _restart(folder).library("theirs") is None


def test_a_remembered_file_that_has_gone_missing_is_reported(folder, tmp_path):
    path = tmp_path / "usb" / "field.hdr"
    writeEnviLibrary(path, [_spectrum("x")])
    LibraryStore(folder).loadFile(path)
    for file in path.parent.iterdir():
        file.unlink()

    store = _restart(folder)
    assert store.library("field") is None
    assert store.unavailable == {path.resolve(): "file not found"}
    # still remembered (the drive may come back) until forgotten
    assert _restart(folder).unavailable
    store.forgetUnavailable()
    assert not store.unavailable and not _restart(folder).unavailable


def test_every_change_notifies(folder, tmp_path):
    store = LibraryStore(folder)
    calls = []
    store.changed.connect(lambda: calls.append(1))
    library = store.createLibrary("Mine")
    store.addSpectra(library, [_spectrum("a")])
    store.renameSpectrum(library, 0, "b")
    store.removeSpectra(library, [0])
    store.renameLibrary(library, "Other")
    path = tmp_path / "x.csv"
    writeCsvLibrary(path, [_spectrum("x")])
    store.loadFile(path)
    store.removeLibrary(library)
    assert len(calls) == 7


def test_without_a_folder_your_libraries_live_in_memory(tmp_path):
    store = LibraryStore()
    library = store.createLibrary("Scratch", [_spectrum("a")])
    assert library.path is None and store.library("Scratch") is library


def test_export_writes_envi_or_csv_with_the_citation(folder, tmp_path):
    store = LibraryStore(folder)
    usgs = store.library(USGS_LIBRARY_NAME)
    assert usgs is not None
    csvPath = tmp_path / "usgs.csv"
    store.exportLibrary(usgs, csvPath)
    assert "ds1035" in csvPath.read_text()
    header = store.exportLibrary(usgs, tmp_path / "usgs.sli")
    loaded = store.loadFile(header)
    assert len(loaded.spectra) == len(usgs.spectra)
    assert loaded.name == "USGS minerals (2)"  # its own name, kept unique
    assert "ds1035" in loaded.citation


def test_the_default_store_is_shared_until_replaced(folder):
    setDefaultStore(None)
    assert defaultStore() is defaultStore()
    mine = LibraryStore(folder)
    setDefaultStore(mine)
    assert defaultStore() is mine
    setDefaultStore(None)


# --- the plots' library choice -------------------------------------------------------


def test_the_plots_library_choice_is_kept_across_restarts(folder):
    store = LibraryStore(folder)
    assert store.plotLibrariesOff() == set()  # nothing chosen yet: all on
    store.setPlotLibrariesOff({MICA_LIBRARY_NAME})
    assert (folder / "preferences.json").is_file()
    assert _restart(folder).plotLibrariesOff() == {MICA_LIBRARY_NAME}


def test_an_unreadable_preferences_file_means_all_libraries_on(folder):
    folder.mkdir(parents=True)
    (folder / "preferences.json").write_text("{not json", encoding="utf-8")
    assert LibraryStore(folder).plotLibrariesOff() == set()


def test_an_in_memory_store_keeps_the_choice_without_writing_files(tmp_path):
    store = LibraryStore()
    store.setPlotLibrariesOff({USGS_LIBRARY_NAME})
    assert store.plotLibrariesOff() == {USGS_LIBRARY_NAME}
    assert list(tmp_path.iterdir()) == []


def test_renaming_a_library_carries_its_choice_and_says_so(folder):
    store = LibraryStore(folder)
    library = store.createLibrary("Dunes", [_spectrum("a")])
    store.setPlotLibrariesOff({"Dunes", MICA_LIBRARY_NAME})
    renames = []
    store.renamed.connect(lambda old, new: renames.append((old, new)))
    store.renameLibrary(library, "Dune field")
    assert renames == [("Dunes", "Dune field")]
    assert store.plotLibrariesOff() == {"Dune field", MICA_LIBRARY_NAME}
    assert _restart(folder).plotLibrariesOff() == {"Dune field", MICA_LIBRARY_NAME}
