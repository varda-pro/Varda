"""The app-wide set of spectral libraries every library list draws from.

Three kinds:

- **Bundled** with Varda (CRISM MICA numerators, USGS splib07a minerals):
  always present, read-only.
- **Yours**: libraries you create, saved as ENVI spectral libraries (.sli +
  .hdr, plus Varda's ``.varda.json`` sidecar) in the store's folder, Varda's
  app-data ``libraries`` folder in the app. Editable.
- **Loaded** from files elsewhere: read-only, remembered by path (in
  ``loaded.json`` in the same folder) and read again next time; one that has
  gone missing is listed in ``unavailable`` until you forget it.

The store also keeps the spectral plots' most recent **Libraries** choice (the
names turned off, in ``preferences.json`` in the same folder), the default
for new plots.

Any change emits ``changed``, so every list showing libraries can follow.
``defaultStore()`` is the store the app's windows share; the app points it at
its folder at startup (``setDefaultStore``). Unconfigured (tests, scripts), it
keeps your libraries in memory only.
"""

from __future__ import annotations

import functools
import json
import logging
import re
from collections.abc import Iterable, Sequence
from enum import Enum
from pathlib import Path

import attrs
from psygnal import Signal

from varda.spectral_libraries.formats import (
    USGS_LIBRARY_PATH,
    headerPath,
    loadCsvLibrary,
    loadEnviLibrary,
    loadLibrary,
    loadUsgsLibrary,
    readLibraryInfo,
    removeEnviLibrary,
    writeEnviLibrary,
    writeLibrary,
)
from varda.spectral_libraries.spectrum import LibrarySpectrum
from varda.utilities.resources import resource_path

logger = logging.getLogger(__name__)

MICA_LIBRARY_NAME = "CRISM MICA numerators"
USGS_LIBRARY_NAME = "USGS minerals"
MICA_LIBRARY_PATH = Path(
    resource_path("resources/spectral_libraries/crism_mica_numerators.csv")
)
MICA_CITATION = (
    "Viviano-Beck, C. E., Seelos, F. P., Murchie, S. L., et al. (2014). Revised "
    "CRISM spectral parameters and summary products based on the currently "
    "detected mineral diversity on Mars. Journal of Geophysical Research: "
    "Planets, 119(6), 1403–1431. https://doi.org/10.1002/2014JE004627"
)
USGS_CITATION = (
    "Kokaly, R. F., Clark, R. N., Swayze, G. A., Livo, K. E., Hoefen, T. M., "
    "Pearson, N. C., Wise, R. A., Benzel, W. M., Lowers, H. A., Driscoll, R. L., "
    "& Klein, A. J. (2017). USGS Spectral Library Version 7. U.S. Geological "
    "Survey Data Series 1035. https://doi.org/10.3133/ds1035"
)
REMEMBERED_FILE = "loaded.json"
PREFERENCES_FILE = "preferences.json"


class LibraryKind(Enum):
    BUNDLED = "bundled with Varda, read-only"
    USER = "yours"
    FILE = "loaded from a file, read-only"


@attrs.define(eq=False)
class SpectralLibrary:
    name: str
    spectra: list[LibrarySpectrum]
    kind: LibraryKind
    path: Path | None = None  # yours: the .hdr; loaded: the file or folder
    description: str = ""
    citation: str = ""

    @property
    def editable(self) -> bool:
        return self.kind is LibraryKind.USER

    def label(self) -> str:
        """Its name and size for a list, e.g. "USGS minerals (52)"."""
        return f"{self.name} ({len(self.spectra)})"


@functools.cache
def _bundledSpectra() -> tuple[tuple[str, tuple[LibrarySpectrum, ...], str, str], ...]:
    """(name, spectra, citation, description) of each bundled library, read once."""
    bundled = []
    if MICA_LIBRARY_PATH.is_file():
        mica = tuple(
            attrs.evolve(
                s,
                source=MICA_LIBRARY_NAME,
                valueUnits="I/F",
                provenance={
                    "library": MICA_LIBRARY_NAME,
                    "file": str(MICA_LIBRARY_PATH),
                },
            )
            for s in loadCsvLibrary(MICA_LIBRARY_PATH)
        )
        bundled.append(
            (
                MICA_LIBRARY_NAME,
                mica,
                MICA_CITATION,
                "CRISM type spectra (MICA): corrected I/F numerators, comparable "
                "to unratioed spectra from a CRISM cube.",
            )
        )
    usgs = tuple(loadUsgsLibrary(USGS_LIBRARY_PATH))
    bundled.append(
        (
            USGS_LIBRARY_NAME,
            usgs,
            USGS_CITATION,
            "Minerals from the USGS Spectral Library Version 7 (splib07a), at "
            "the library's own spectral resolution.",
        )
    )
    return tuple(bundled)


def bundledLibraries() -> list[SpectralLibrary]:
    return [
        SpectralLibrary(
            name, list(spectra), LibraryKind.BUNDLED, None, description, citation
        )
        for name, spectra, citation, description in _bundledSpectra()
    ]


def _fileStem(name: str) -> str:
    """A file name for a library name: no path separators or odd characters."""
    return re.sub(r"[^\w\- .()]+", "_", name).strip(" .") or "library"


class LibraryStore:
    changed = Signal()
    renamed = Signal(str, str)  # a library's old name, its new name

    def __init__(self, folder: Path | None = None, *, bundled: bool = True) -> None:
        self.folder = folder
        self._libraries: list[SpectralLibrary] = bundledLibraries() if bundled else []
        # Libraries that couldn't be read: path -> why. Remembered files among
        # them stay remembered (a drive may be unplugged) until forgotten.
        self.unavailable: dict[Path, str] = {}
        self._unreadRemembered: list[Path] = []
        self._plotLibrariesOff: set[str] = set()
        if folder is not None:
            self._loadFolder(folder)
            self._plotLibrariesOff = self._readPlotLibrariesOff(folder)

    # --- reading ---------------------------------------------------------------------

    def libraries(self) -> list[SpectralLibrary]:
        return list(self._libraries)

    def names(self) -> list[str]:
        return [library.name for library in self._libraries]

    def library(self, name: str) -> SpectralLibrary | None:
        return next((lib for lib in self._libraries if lib.name == name), None)

    def userLibraries(self) -> list[SpectralLibrary]:
        return [lib for lib in self._libraries if lib.editable]

    def uniqueName(self, name: str) -> str:
        name = " ".join(name.split()) or "Library"
        taken = set(self.names())
        if name not in taken:
            return name
        number = 2
        while f"{name} ({number})" in taken:
            number += 1
        return f"{name} ({number})"

    # --- persistence -----------------------------------------------------------------

    def _loadFolder(self, folder: Path) -> None:
        for header in sorted(folder.glob("*.hdr")):
            try:
                spectra = loadEnviLibrary(header)
            except (OSError, ValueError, KeyError) as error:
                logger.warning("Couldn't read your library %s: %s", header, error)
                self.unavailable[header] = str(error)
                continue
            info = readLibraryInfo(header)
            self._libraries.append(
                SpectralLibrary(
                    self.uniqueName(info.get("name", header.stem)),
                    spectra,
                    LibraryKind.USER,
                    header,
                    info.get("description", ""),
                    info.get("citation", ""),
                )
            )
        for path in self._remembered():
            try:
                self._addFile(path)
            except (OSError, ValueError, KeyError) as error:
                reason = "file not found" if not path.exists() else str(error)
                logger.warning("Couldn't read the library %s: %s", path, reason)
                self.unavailable[path] = reason
                self._unreadRemembered.append(path)

    def _rememberedFile(self) -> Path | None:
        return None if self.folder is None else self.folder / REMEMBERED_FILE

    def _remembered(self) -> list[Path]:
        file = self._rememberedFile()
        if file is None or not file.is_file():
            return []
        try:
            return [Path(p) for p in json.loads(file.read_text(encoding="utf-8"))]
        except (OSError, ValueError) as error:
            logger.warning("Couldn't read %s: %s", file, error)
            return []

    def _saveRemembered(self) -> None:
        file = self._rememberedFile()
        if file is None:
            return
        loaded = [lib.path for lib in self._libraries if lib.kind is LibraryKind.FILE]
        paths = [str(p) for p in loaded + self._unreadRemembered]
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(paths, indent=1), encoding="utf-8")

    # --- the plots' library choice -----------------------------------------------------

    def plotLibrariesOff(self) -> set[str]:
        """The libraries a new spectral plot leaves out, by name: the most
        recent choice in any plot. Any other library is searched, so one added
        later is on."""
        return set(self._plotLibrariesOff)

    def setPlotLibrariesOff(self, names: Iterable[str]) -> None:
        """Remember the libraries turned off (in the folder, if there is one)."""
        self._plotLibrariesOff = set(names)
        if self.folder is None:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / PREFERENCES_FILE).write_text(
            json.dumps({"plotLibrariesOff": sorted(self._plotLibrariesOff)}, indent=1),
            encoding="utf-8",
        )

    @staticmethod
    def _readPlotLibrariesOff(folder: Path) -> set[str]:
        file = folder / PREFERENCES_FILE
        if not file.is_file():
            return set()
        try:
            names = json.loads(file.read_text(encoding="utf-8"))["plotLibrariesOff"]
            return {str(name) for name in names}
        except (OSError, ValueError, KeyError, TypeError) as error:
            logger.warning("Couldn't read %s: %s", file, error)
            return set()

    def _save(self, library: SpectralLibrary) -> None:
        """Write one of your libraries to the folder (memory only without one)."""
        if self.folder is None:
            return
        stem = _fileStem(library.name)
        target, number = self.folder / f"{stem}.hdr", 2
        while target != library.path and target.exists():
            target, number = self.folder / f"{stem} ({number}).hdr", number + 1
        if library.path is not None and library.path != target:
            removeEnviLibrary(library.path)
        library.path = writeEnviLibrary(
            target,
            library.spectra,
            name=library.name,
            description=library.description,
            citation=library.citation,
        )

    def _changed(self, library: SpectralLibrary | None = None) -> None:
        if library is not None and library.editable:
            self._save(library)
        self.changed.emit()

    # --- loading files -----------------------------------------------------------------

    def _addFile(self, path: Path) -> SpectralLibrary:
        spectra = loadLibrary(path)
        info = {} if path.is_dir() else readLibraryInfo(path)
        isEnvi = path.suffix.lower() in {".sli", ".hdr"}
        stem = headerPath(path).stem if isEnvi else path.stem
        library = SpectralLibrary(
            self.uniqueName(info.get("name", stem)),
            spectra,
            LibraryKind.FILE,
            path,
            info.get("description", ""),
            info.get("citation", ""),
        )
        self._libraries.append(library)
        return library

    def loadFile(self, path: str | Path) -> SpectralLibrary:
        """Read a library file (or USGS folder), remember it, and add it.
        Raises OSError/ValueError/KeyError when it can't be read."""
        path = Path(path).resolve()
        existing = next(
            (
                lib
                for lib in self._libraries
                if lib.kind is LibraryKind.FILE and lib.path == path
            ),
            None,
        )
        if existing is not None:
            return existing
        library = self._addFile(path)
        self.unavailable.pop(path, None)
        if path in self._unreadRemembered:
            self._unreadRemembered.remove(path)
        self._saveRemembered()
        self._changed()
        return library

    def forgetUnavailable(self) -> None:
        """Stop remembering the files that couldn't be read."""
        self.unavailable.clear()
        self._unreadRemembered.clear()
        self._saveRemembered()
        self._changed()

    # --- editing -----------------------------------------------------------------------

    def _requireEditable(self, library: SpectralLibrary) -> None:
        if not library.editable:
            raise ValueError(f"“{library.name}” is read-only")

    def createLibrary(
        self,
        name: str,
        spectra: Iterable[LibrarySpectrum] = (),
        *,
        description: str = "",
    ) -> SpectralLibrary:
        library = SpectralLibrary(
            self.uniqueName(name),
            list(spectra),
            LibraryKind.USER,
            description=description,
        )
        self._libraries.append(library)
        self._changed(library)
        return library

    def addSpectra(
        self, library: SpectralLibrary, spectra: Iterable[LibrarySpectrum]
    ) -> None:
        self._requireEditable(library)
        library.spectra.extend(spectra)
        self._changed(library)

    def renameSpectrum(self, library: SpectralLibrary, index: int, name: str) -> None:
        self._requireEditable(library)
        library.spectra[index] = attrs.evolve(library.spectra[index], name=name)
        self._changed(library)

    def removeSpectra(self, library: SpectralLibrary, indices: Sequence[int]) -> None:
        self._requireEditable(library)
        drop = set(indices)
        library.spectra[:] = [s for i, s in enumerate(library.spectra) if i not in drop]
        self._changed(library)

    def renameLibrary(self, library: SpectralLibrary, name: str) -> None:
        self._requireEditable(library)
        if name == library.name:
            return
        old, library.name = library.name, self.uniqueName(name)
        if old in self._plotLibrariesOff:  # a library turned off stays off
            self.setPlotLibrariesOff(self._plotLibrariesOff - {old} | {library.name})
        self.renamed.emit(old, library.name)
        self._changed(library)

    def setDescription(self, library: SpectralLibrary, description: str) -> None:
        self._requireEditable(library)
        library.description = description
        self._changed(library)

    def removeLibrary(self, library: SpectralLibrary) -> None:
        """Delete one of your libraries (and its files), or forget a loaded one.
        Bundled libraries stay."""
        if library.kind is LibraryKind.BUNDLED:
            raise ValueError(f"“{library.name}” is bundled with Varda")
        self._libraries.remove(library)
        if library.editable and library.path is not None:
            removeEnviLibrary(library.path)
        else:
            self._saveRemembered()
        self.changed.emit()

    def exportLibrary(self, library: SpectralLibrary, path: str | Path) -> Path:
        """Write a library as ENVI (.sli/.hdr, with the sidecar) or CSV."""
        return writeLibrary(
            path,
            library.spectra,
            name=library.name,
            description=library.description,
            citation=library.citation,
        )


_default: LibraryStore | None = None


def defaultStore() -> LibraryStore:
    """The store the app's windows share (in memory until ``setDefaultStore``)."""
    global _default
    if _default is None:
        _default = LibraryStore()
    return _default


def setDefaultStore(store: LibraryStore | None) -> None:
    """Make ``store`` the shared one (None: a fresh in-memory one when next asked)."""
    global _default
    _default = store
