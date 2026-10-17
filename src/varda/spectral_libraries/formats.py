"""Reading and writing spectral library files.

Read: ENVI spectral libraries (.sli + .hdr), CSV, and USGS splib07 folders.
Write: ENVI spectral libraries and CSV, both with each spectrum's value units
and provenance so they read back whole.

An ENVI spectral library has one wavelength axis for all its spectra.
Spectra measured on different axes (two sensors, say) are written on the
union of their wavelengths, each NaN where it has no sample; nothing is
interpolated. What ENVI's header can't hold (names exactly as given, value
units, provenance, a region mean's σ) goes in a sidecar, ``<name>.varda.json``,
beside the .hdr. ENVI and other tools ignore it; Varda reads it back and then
drops each spectrum's NaN samples, so each gets its own axis again.

CSV: the first column is wavelength (``wavelength_nm`` when Varda writes it),
then one column per spectrum, plus ``<name> std`` for a region mean's σ. Lines
starting with ``#`` are comments; Varda writes each spectrum's record on a
``# varda-spectrum: {json}`` line, which it reads back.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from collections.abc import Sequence
from pathlib import Path

import attrs
import numpy as np
import spectral.io.envi as envi

from varda.common.spectral_axis import (
    SpectralUnit,
    nanometresFrom,
    parseUnit,
    toNanometres,
    unitInText,
)
from varda.spectral_libraries.spectrum import LibrarySpectrum
from varda.utilities.resources import resource_path

logger = logging.getLogger(__name__)

# Bundled with packaged builds too (see Varda.spec)
USGS_LIBRARY_PATH = Path(resource_path("librarySpectra"))
SIDECAR_SUFFIX = ".varda.json"
SIDECAR_SCHEMA = 1
CSV_RECORD_PREFIX = "# varda-spectrum:"
_USGS_MISSING_VALUE = -1e30

LIBRARY_FORMAT_HELP = """\
<p>Load Library accepts three formats. Wavelengths may be in nm, µm or cm⁻¹:
name the unit in the wavelength column (e.g. <code>wavelength_um</code>) or in a
<code># units: µm</code> comment; otherwise Varda infers it. The library is resampled to your image's bands, and
it must overlap at least five of them. Matching ignores overall brightness,
so reflectance, I/F or scaled values all work.</p>

<p><b>CSV</b> (.csv or .txt): the first column is wavelength, every other column
is one spectrum, and the first non-comment row holds the names, e.g.</p>
<pre>
# optional comment lines start with #
wavelength_um,Kaolinite,Nontronite
1.0010,0.512,0.430
1.0075,0.515,0.431
</pre>
<p>Leave a cell empty where a value is missing.</p>

<p><b>ENVI spectral library</b> (.sli with its .hdr): the header needs
<code>spectra names</code>, <code>wavelength</code> and <code>wavelength units</code>,
as written by ENVI or by <code>spectral.envi.SpectralLibrary.save</code>.</p>

<p><b>USGS splib07 folder</b>: pick a folder with one sub-folder per spectrum, each
holding the spectrum's .txt file and its <i>*Wavelengths*.txt</i> file, as
downloaded from the USGS Spectral Library.</p>

<p>For CRISM, compare unratioed I/F endmembers with the <b>MICA numerators</b>
(bundled); ratioed type spectra only match ratioed data.</p>

<p>Libraries you load are remembered, and appear in every library list;
manage them in <b>Processing → Spectral Libraries…</b>.</p>
"""


# --- USGS splib07 folders -----------------------------------------------------------


def _readUsgsFile(path: Path) -> np.ndarray:
    """A one-value-per-line USGS .txt file, skipping the header line."""
    with open(path) as f:
        next(f)
        values = np.array([float(line) for line in f if line.strip()], dtype=float)
    values[values < _USGS_MISSING_VALUE] = np.nan
    return values


def listUsgsSpectra(folder: Path = USGS_LIBRARY_PATH) -> list[str]:
    """Sorted names of the spectrum folders in a USGS library folder."""
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_dir())


def loadUsgsSpectrum(folder: Path, name: str) -> tuple[str, np.ndarray, np.ndarray]:
    """(name, wavelengths in nm, reflectance) of one USGS spectrum folder."""
    path = folder / name
    wavelengthFiles = list(path.glob("*Wavelengths*.txt"))
    if not wavelengthFiles:
        raise FileNotFoundError(f"No wavelength file found in {path}")
    reflectance = _readUsgsFile(path / (path.name + ".txt"))
    # splib07 wavelength files are in micrometres
    wavelengths = toNanometres(
        _readUsgsFile(wavelengthFiles[0]), SpectralUnit.MICROMETRE
    )
    return name.removeprefix("splib07a_"), wavelengths, reflectance


def loadUsgsLibrary(
    folder: Path = USGS_LIBRARY_PATH, source: str = "USGS splib07a"
) -> list[LibrarySpectrum]:
    """A folder of USGS splib07 spectra (one sub-folder per spectrum)."""
    library = []
    for name in listUsgsSpectra(folder):
        try:
            title, wavelengths, reflectance = loadUsgsSpectrum(folder, name)
        except (FileNotFoundError, ValueError):
            continue
        provenance = {"library": source, "file": str(folder / name)}
        library.append(
            LibrarySpectrum(
                title,
                wavelengths,
                reflectance,
                source,
                valueUnits="reflectance",
                provenance=provenance,
            )
        )
    return library


# --- ENVI --------------------------------------------------------------------------


def headerPath(path: str | Path) -> Path:
    """The .hdr of an ENVI library given its .sli or .hdr."""
    path = Path(path)
    return path if path.suffix.lower() == ".hdr" else path.with_suffix(".hdr")


def sidecarPath(path: str | Path) -> Path:
    """Varda's sidecar beside an ENVI library."""
    header = headerPath(path)
    return header.with_name(header.stem + SIDECAR_SUFFIX)


def _readSidecar(header: Path, count: int) -> dict | None:
    path = sidecarPath(header)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if len(data.get("spectra", [])) != count:
        logger.warning(
            "Ignoring %s: it describes %d spectra, the library has %d",
            path,
            len(data.get("spectra", [])),
            count,
        )
        return None
    return data


def readLibraryInfo(path: str | Path) -> dict:
    """The library-level part of a Varda sidecar (name, description,
    citation), or {} when there is none."""
    sidecar = sidecarPath(path)
    if not sidecar.is_file():
        return {}
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    return {k: data[k] for k in ("name", "description", "citation") if k in data}


def _fromJsonList(values: list | None) -> np.ndarray | None:
    if values is None:
        return None
    return np.array([np.nan if v is None else v for v in values], dtype=np.float64)


def loadEnviLibrary(path: str | Path) -> list[LibrarySpectrum]:
    """An ENVI spectral library (.sli + .hdr), with Varda's sidecar if any."""
    header = headerPath(path)
    fields = envi.read_envi_header(str(header))
    if int(fields.get("lines", 0)) == 0 or int(fields.get("samples", 0)) == 0:
        return []  # an empty library (SPy can't open one)
    library = envi.open(str(header))
    names = list(library.names)
    unit = parseUnit(library.metadata.get("wavelength units"))
    wavelengths = nanometresFrom(library.bands.centers, unit)
    spectra = np.asarray(library.spectra, dtype=np.float64)
    sidecar = _readSidecar(header, len(names))
    if sidecar is None:
        provenance = {"library": header.stem, "file": str(header.with_suffix(".sli"))}
        return [
            LibrarySpectrum(
                name, wavelengths, spectra[i], header.stem, provenance=provenance
            )
            for i, name in enumerate(names)
        ]
    result = []
    for i, record in enumerate(sidecar["spectra"]):
        values = spectra[i]
        std = _fromJsonList(record.get("std"))
        keep = np.isfinite(values) & np.isfinite(wavelengths)
        result.append(
            LibrarySpectrum(
                record.get("name", names[i]),
                wavelengths[keep],
                values[keep],
                record.get("source", ""),
                valueUnits=record.get("valueUnits", ""),
                std=None if std is None else std[keep],
                provenance=record.get("provenance", {}),
            )
        )
    return result


def commonAxis(spectra: Sequence[LibrarySpectrum]) -> np.ndarray:
    """The sorted union of the spectra's (finite) wavelengths, in nm."""
    if not spectra:
        return np.empty(0)
    every = np.concatenate(
        [np.asarray(s.wavelengths, dtype=np.float64) for s in spectra]
    )
    return np.unique(every[np.isfinite(every)])


def onAxis(axis: np.ndarray, wavelengths: np.ndarray, values: np.ndarray) -> np.ndarray:
    """``values`` placed at their wavelengths on ``axis`` (NaN elsewhere)."""
    out = np.full(axis.size, np.nan)
    wavelengths = np.asarray(wavelengths, dtype=np.float64)
    finite = np.isfinite(wavelengths)
    out[np.searchsorted(axis, wavelengths[finite])] = np.asarray(
        values, dtype=np.float64
    )[finite]
    return out


def _headerText(text: str) -> str:
    """Text safe inside an ENVI header list or brace field."""
    return " ".join(re.sub(r"[{},]+", " ", text).split())


def _jsonList(values: np.ndarray) -> list[float | None]:
    return [None if not np.isfinite(v) else float(v) for v in values]


def writeEnviLibrary(
    path: str | Path,
    spectra: Sequence[LibrarySpectrum],
    *,
    name: str = "",
    description: str = "",
    citation: str = "",
) -> Path:
    """Write ``spectra`` as an ENVI spectral library (float64, wavelengths in
    nm) plus Varda's sidecar; returns the .hdr path."""
    header = headerPath(path)
    header.parent.mkdir(parents=True, exist_ok=True)
    axis = commonAxis(spectra)
    data = np.array(
        [onAxis(axis, s.wavelengths, s.reflectance) for s in spectra], dtype="<f8"
    ).reshape(len(spectra), axis.size)
    data.tofile(header.with_suffix(".sli"))
    names = ", ".join(_headerText(s.name) or "unnamed" for s in spectra)
    lines = [
        "ENVI",
        f"description = {{{_headerText(description or name or header.stem)}}}",
        f"samples = {axis.size}",
        f"lines = {len(spectra)}",
        "bands = 1",
        "header offset = 0",
        "file type = ENVI Spectral Library",
        "data type = 5",
        "interleave = bsq",
        "byte order = 0",
        "wavelength units = Nanometers",
        f"spectra names = {{{names}}}",
        "wavelength = {" + ", ".join(repr(float(w)) for w in axis) + "}",
    ]
    header.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sidecar = {
        "schema": SIDECAR_SCHEMA,
        "name": name or header.stem,
        "description": description,
        "citation": citation,
        "spectra": [
            {
                "name": s.name,
                "source": s.source,
                "valueUnits": s.valueUnits,
                "provenance": dict(s.provenance),
                "std": None
                if s.std is None
                else _jsonList(onAxis(axis, s.wavelengths, s.std)),
            }
            for s in spectra
        ],
    }
    sidecarPath(header).write_text(
        json.dumps(sidecar, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return header


def removeEnviLibrary(path: str | Path) -> None:
    """Delete an ENVI library's files (.hdr, .sli and Varda's sidecar)."""
    header = headerPath(path)
    for file in (header, header.with_suffix(".sli"), sidecarPath(header)):
        file.unlink(missing_ok=True)


# --- CSV ---------------------------------------------------------------------------


def loadCsvLibrary(path: str | Path) -> list[LibrarySpectrum]:
    """A CSV whose first column is wavelength and each other column a spectrum
    (named by its header); ``<name> std`` columns are that spectrum's σ and
    ``<name> n`` columns (pixel counts, from a plot export) are skipped."""
    path = Path(path)
    with open(path, newline="", encoding="utf-8") as f:
        allLines = list(f)
    comments = [line for line in allLines if line.lstrip().startswith("#")]
    lines = [line for line in allLines if not line.lstrip().startswith("#")]
    rows = list(csv.reader(lines))
    header, body = rows[0], [r for r in rows[1:] if r]
    values = np.array([[float(v) if v.strip() else np.nan for v in r] for r in body])
    # The unit from the wavelength column's name (e.g. wavelength_um), else a
    # "# units: ..." comment, else inferred from the values
    unitComments = [
        c for c in comments if "unit" in c.lower() and CSV_RECORD_PREFIX not in c
    ]
    unit = unitInText(header[0]) or next(
        (u for c in unitComments if (u := unitInText(c)) is not None), None
    )
    wavelengths = nanometresFrom(values[:, 0], unit)
    records = {
        r["name"]: r
        for c in comments
        if c.strip().startswith(CSV_RECORD_PREFIX)
        for r in [json.loads(c.strip().removeprefix(CSV_RECORD_PREFIX))]
    }
    columns = {name.strip(): i + 1 for i, name in enumerate(header[1:])}
    spectra = []
    for name, column in columns.items():
        if name.endswith((" std", " n")) and name.rsplit(" ", 1)[0] in columns:
            continue
        stdColumn = columns.get(f"{name} std")
        record = records.get(name)
        spectrum = LibrarySpectrum(
            name,
            wavelengths,
            values[:, column],
            path.stem,
            std=None if stdColumn is None else values[:, stdColumn],
            provenance={"library": path.stem, "file": str(path)},
        )
        if record is not None:
            keep = np.isfinite(spectrum.reflectance) & np.isfinite(wavelengths)
            spectrum = attrs.evolve(
                spectrum,
                wavelengths=wavelengths[keep],
                reflectance=spectrum.reflectance[keep],
                std=None if spectrum.std is None else spectrum.std[keep],
                source=record.get("source", path.stem),
                valueUnits=record.get("valueUnits", ""),
                provenance=record.get("provenance", {}),
            )
        spectra.append(spectrum)
    return spectra


def _csvNumber(value: float) -> str:
    return repr(float(value)) if np.isfinite(value) else ""


def writeCsvLibrary(
    path: str | Path,
    spectra: Sequence[LibrarySpectrum],
    *,
    name: str = "",
    citation: str = "",
) -> None:
    """Write ``spectra`` as a CSV library on the union of their wavelengths
    (nm), with ``<name> std`` columns for region means and one
    ``# varda-spectrum:`` record per spectrum."""
    axis = commonAxis(spectra)
    header = ["wavelength_nm"]
    columns = []
    for s in spectra:
        header.append(s.name)
        columns.append(onAxis(axis, s.wavelengths, s.reflectance))
        if s.std is not None:
            header.append(f"{s.name} std")
            columns.append(onAxis(axis, s.wavelengths, s.std))
    comments = [f"# Spectral library: {name or Path(path).stem}"]
    if citation:
        comments.append(f"# Cite: {' '.join(citation.split())}")
    comments.append("# Wavelengths in nm; empty cells have no value.")
    for s in spectra:
        record = {
            "name": s.name,
            "source": s.source,
            "valueUnits": s.valueUnits,
            "provenance": dict(s.provenance),
        }
        comments.append(f"{CSV_RECORD_PREFIX} {json.dumps(record, ensure_ascii=False)}")
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write("\n".join(comments) + "\n")
        writer = csv.writer(f)
        writer.writerow(header)
        for i, wavelength in enumerate(axis):
            writer.writerow(
                [_csvNumber(wavelength)] + [_csvNumber(c[i]) for c in columns]
            )


# --- any -----------------------------------------------------------------------------


def loadLibrary(path: str | Path) -> list[LibrarySpectrum]:
    """Load a library file by extension (.sli/.hdr ENVI, .csv/.txt), or a
    folder of USGS spectra."""
    path = Path(path)
    if path.is_dir():
        return loadUsgsLibrary(path, source=path.name)
    if not path.exists():
        raise FileNotFoundError(f"{path} doesn't exist")
    if path.suffix.lower() in {".sli", ".hdr"}:
        return loadEnviLibrary(path)
    if path.suffix.lower() in {".csv", ".txt"}:
        return loadCsvLibrary(path)
    raise ValueError(f"Unsupported spectral library: {path.name}")


def writeLibrary(
    path: str | Path,
    spectra: Sequence[LibrarySpectrum],
    *,
    name: str = "",
    description: str = "",
    citation: str = "",
) -> Path:
    """Write by extension: .sli/.hdr (ENVI + sidecar) or .csv. Returns the
    path written (the .hdr for ENVI)."""
    path = Path(path)
    if path.suffix.lower() in {".sli", ".hdr"}:
        return writeEnviLibrary(
            path, spectra, name=name, description=description, citation=citation
        )
    if path.suffix.lower() == ".csv":
        writeCsvLibrary(path, spectra, name=name, citation=citation)
        return path
    raise ValueError(f"Can't write a spectral library as {path.suffix or path.name}")
