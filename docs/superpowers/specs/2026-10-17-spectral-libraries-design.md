# Spectral libraries: one shared store, your own libraries, provenance

Status: built on `feature/spectral-libraries`. User guide:
`docs/user-guide/spectral-libraries.md`.

## Problem

The spectral plots' reference overlay only knew the bundled USGS library;
the FATT window and the Spectral Hourglass each kept their own list (bundled
+ Load Library…), so a library loaded in one was invisible to the others and
forgotten at restart. There was no way to build a library from image spectra.

## Design

- **`varda.spectral_libraries`** (Qt-free, in the import-linter contract):
  - `spectrum.py`: `LibrarySpectrum` (name, wavelengths nm, values, source,
    `valueUnits`, `std`, `provenance` dict), `SpectrumOrigin` (image, region,
    pixel count, note) and `spectrumFromImage` / `copiedSpectrum`.
  - `formats.py`: the readers moved from `analysis/spectral_library.py` and
    `plotting/library_spectra.py` (ENVI, CSV, USGS folders), and writers:
    ENVI (`.sli` float64 + `.hdr`, wavelengths nm, the union of the spectra's
    axes with NaN padding, never interpolated) plus a `<name>.varda.json`
    sidecar (exact names, units, provenance, σ, library name/description/
    citation); CSV with `<name> std` columns and `# varda-spectrum: {json}`
    records. Both round-trip.
  - `store.py`: `LibraryStore` with a psygnal `changed` signal. Bundled
    libraries (read once, cached), yours (ENVI in the store's folder, saved
    on every change), loaded files (read-only, remembered in `loaded.json`;
    unreadable ones listed in `unavailable` and kept until forgotten).
    `defaultStore()` is the app-wide instance; `startup.createVarda` points
    it at `AppDataLocation/libraries`. Unconfigured (tests, scripts) it
    keeps your libraries in memory; the root `conftest.py` resets it per test.
- **`varda.library_manager`** (Qt): `SpectralLibraryManager` (Processing →
  Spectral Libraries…), `AddToLibraryDialog`, `ReferenceSpectraPicker` (the
  plots' Library Spectra section) and `info_view` (provenance/citation HTML).
- `analysis.library_picker.LibraryPicker` lists the store and follows its
  `changed` (via `connectWhileAlive`), emitting `sigLibraryChanged` only when
  the current library or its size changes.
- Plot curves carry `origin` (pixel/box from the pixel plot, ROI mean from
  the ROI manager, endmembers in the Hourglass) or `librarySpectrum` (a
  plotted library spectrum); Add to Library builds the spectrum from these,
  using the curve's own data (never the display scale/offset).

## Choices

- ENVI + sidecar rather than per-spectrum ENVI header lists: names with
  commas/braces and per-sample σ arrays don't fit ENVI lists; the `.sli`/
  `.hdr` stay standard for other software.
- Loaded files are read-only: copy spectra into a library of yours to edit.
- The remembered list lives in the libraries folder (JSON), not QSettings,
  so the store stays Qt-free and testable with a temp folder.
- Loaded libraries are read at startup (fast for typical sizes; the bundled
  ones take ~25 ms). Very large libraries would need lazy loading.

## Open questions

- Should the plots' library choice persist per plot or across sessions?
  (Now: per plot, in memory; new libraries are included automatically.)
- Should a library of yours hold a citation/description you can edit in the
  manager? (Stored and shown, not yet editable in the UI.)
- Resampling a library to a sensor and saving that as a new library.
