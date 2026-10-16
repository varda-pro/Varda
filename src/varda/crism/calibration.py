"""Finding, choosing, deduplicating and fetching the calibration files an
observation needs: the wavelength table (WA), the bad-pixel maps (BP), and the
atmospheric transmission spectra (AT CDRs and CAT's volcano-scan ADRs).

Files are searched in the user's folders, the download cache and CAT's aux
folder, in that order; missing PDS files are fetched into the cache. Nothing
here touches the network unless ``ensure`` runs or a ``Fetcher`` is called.
"""

import csv
import hashlib
import shutil
import ssl
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path

import certifi
import numpy as np
from attrs import frozen

from varda.common.parameter import LabeledEnum
from varda.crism.products import CdrId, VsAdrId
from varda.crism.readers import DetectorFrame, Trr3, readDetectorFrame, readVsAdr
from varda.version import vardaVersion

PDS_VOLUMES_URL = "https://pds-geosciences.wustl.edu/mro/mro-m-crism-2-edr-v1/"
PDS_CDR_URL = PDS_VOLUMES_URL + "mrocr_0001/cdr/"
TIMEOUT_S = 60

_AT_TABLE = Path(__file__).parent / "resources" / "at_cdrs.csv"
# PIXEL_AVERAGING_WIDTH -> the binning code in a CDR name
_BINNING_CODES = {1: 0, 2: 1, 5: 2, 10: 3}
# MRO's SCLK counts seconds from 1980-01-01; PDS files date-stamped CDRs
# (BP among them) sit in the volume holding the year
_SCLK_EPOCH = datetime(1980, 1, 1)
_YEAR_VOLUMES = {
    2006: (1,), 2007: (1,), 2008: (2, 3), 2009: (3,), 2010: (3, 4), 2011: (4, 5),
    2012: (5, 6), 2013: (6, 7), 2014: (7, 8), 2015: (8, 9), 2016: (9, 10),
    2017: (10, 11), 2018: (11, 12), 2019: (12, 13), 2020: (13, 14), 2021: (14, 15),
    2022: (15,), 2023: (15,),
}  # fmt: skip

type Fetcher = Callable[[str, Path], None]
"""Downloads a URL to a path; raises ``OSError`` when it can't."""


class TransmissionSource(LabeledEnum):
    PDS_AT = "PDS atmospheric transmission CDRs"
    CAT_VS_ADR = "CAT volcano-scan ADRs"
    BOTH = "Both"


@frozen(eq=False)
class Transmission:
    """One candidate atmospheric transmission, ``values`` in the cube's band
    order (sample, band). ``artifacts`` is (2, sample, band) for a VS ADR (the
    McGuire and Pelkey artifact spectra). The SCLK period is that of the
    source scan; ``extrapolated`` says no candidate's period held the
    observation's SCLK, so the nearest was used."""

    scanId: str
    sourceName: str
    values: np.ndarray
    sha256: str
    artifacts: np.ndarray | None = None
    sclkStart: int | None = None
    sclkStop: int | None = None
    extrapolated: bool = False

    @property
    def key(self) -> str:
        """The scan ID and the source, ``"<scan>:AT"`` or ``"<scan>:VS"``: a
        PDS AT and a VS ADR of the same scan are different candidates."""
        return f"{self.scanId}:{'VS' if self.artifacts is not None else 'AT'}"


@frozen
class CalibrationFileStatus:
    """A file the observation uses. ``required`` files block processing when
    absent (a WA, and at least one AT); ``note`` says why this file was chosen,
    or why it is not available."""

    name: str
    kind: str
    localPath: Path | None
    url: str | None
    required: bool = True
    note: str = ""


class MissingCalibrationError(Exception):
    """Calibration files are absent and could not be fetched."""

    def __init__(self, files: list[CalibrationFileStatus]):
        self.files = files
        lines = [
            f"  {f.kind} {f.name}: {f.url or 'no known source'}"
            + (f" ({f.note})" if f.note else "")
            for f in files
        ]
        super().__init__(
            "Missing CRISM calibration files (put them in a calibration folder "
            "or connect to the internet to fetch them):\n" + "\n".join(lines)
        )


@frozen
class AtRow:
    """A PDS AT CDR: its name (no extension), the volcano scan it came from, the
    spacecraft-clock period it is valid for and (when known) the image's SHA-256."""

    name: str
    scanId: str
    sclkStart: int
    sclkStop: int
    sha256: str


@cache
def atTable() -> tuple[AtRow, ...]:
    """The AT CDRs on the PDS volume (see the header of ``at_cdrs.csv``)."""
    with _AT_TABLE.open(newline="") as file:
        lines = (line for line in file if not line.startswith("#"))
        return tuple(
            AtRow(
                row["name"],
                row["scan_id"],
                int(row["sclk_start"]),
                int(row["sclk_stop"]),
                row["sha256"],
            )
            for row in csv.DictReader(lines)
        )


def downloadTo(url: str, target: Path) -> None:
    """Stream ``url`` to ``target`` through a ``.part`` file, so a failure never
    leaves a partial file under the real name. Raises ``OSError`` on failure."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(
        url, headers={"User-Agent": f"Varda/{vardaVersion()}"}
    )
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with (
            urllib.request.urlopen(
                request,
                timeout=TIMEOUT_S,
                context=context if url.startswith("https") else None,
            ) as response,
            part.open("wb") as out,
        ):
            shutil.copyfileobj(response, out)
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)


def _lookup(folder: Path, name: str) -> Path | None:
    """The file called ``name`` in ``folder``, whatever its case."""
    if not folder.is_dir():
        return None
    lowered = name.lower()
    return next((p for p in folder.iterdir() if p.name.lower() == lowered), None)


def _binningCode(trr3: Trr3) -> int:
    try:
        return _BINNING_CODES[trr3.binning]
    except KeyError:
        raise ValueError(
            f"{trr3.id.stem}: no CDR binning code for PIXEL_AVERAGING_WIDTH "
            f"{trr3.binning}"
        ) from None


def _bpNames(trr3: Trr3) -> list[str]:
    """The BP CDRs the TRR3's label lists among its source products."""
    sources = trr3.label.get("SOURCE_PRODUCT_ID", [])
    items = sources if isinstance(sources, list) else [sources]
    return sorted(
        str(item).lower()
        for item in items
        if str(item).upper().startswith("CDR4") and "_BP" in str(item).upper()
    )


def _bpUrls(stem: str) -> list[str]:
    """Where a BP CDR may sit: PDS files them by acquisition day, in the volume
    for that year. The day is computed from the SCLK, so also try its neighbors."""
    day = _SCLK_EPOCH + timedelta(seconds=CdrId.parse(stem).sclk)
    urls: list[str] = []
    for offset in (0, -1, 1):
        date = day + timedelta(days=offset)
        for volume in _YEAR_VOLUMES.get(date.year, ()):
            urls.append(
                f"{PDS_VOLUMES_URL}mrocr_{volume:04d}/cdr/{date.year}/"
                f"{date.year}_{date.timetuple().tm_yday:03d}/bp/{stem}"
            )
    return urls


def _candidateUrls(kind: str, stem: str) -> list[str]:
    """Base URLs (without extension) a CDR may be fetched from, most likely first."""
    return _bpUrls(stem) if kind == "BP" else [f"{PDS_CDR_URL}{kind.lower()}/{stem}"]


def _sha256(values: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(values, dtype=np.float64)).hexdigest()


class CalibrationLibrary:
    def __init__(
        self,
        folders: Sequence[Path],
        cacheFolder: Path,
        catAuxFolder: Path | None = None,
        fetch: Fetcher | None = None,
    ):
        self._folders = [Path(f) for f in folders]
        self._cache = Path(cacheFolder)
        self._catAux = Path(catAuxFolder) if catAuxFolder is not None else None
        self._fetch: Fetcher = fetch or downloadTo
        self._failures: dict[str, str] = {}

    def _searchFolders(self, kind: str) -> list[Path]:
        lowered = kind.lower()
        folders = [p for f in self._folders for p in (f, f / "CRISM_CDR" / lowered)]
        folders += [self._cache / lowered, self._cache]
        if self._catAux is not None:
            folders.append(self._catAux / "CDRs" / kind.upper())
        return folders

    def _find(self, kind: str, stem: str) -> Path | None:
        """The ``.img`` of a CDR, when a label sits beside it."""
        for folder in self._searchFolders(kind):
            image = _lookup(folder, stem + ".img")
            if image is not None and _lookup(folder, stem + ".lbl") is not None:
                return image
        return None

    def _entry(
        self, kind: str, stem: str, required: bool = True, note: str = ""
    ) -> CalibrationFileStatus:
        urls = _candidateUrls(kind, stem.lower())
        failure = self._failures.get(stem.lower())
        return CalibrationFileStatus(
            stem.lower(),
            kind,
            self._find(kind, stem),
            urls[0] + ".img" if urls else None,
            required,
            "; ".join(n for n in (note, failure) if n),
        )

    def _atRows(self, trr3: Trr3) -> list[AtRow]:
        wavelengthFilter = CdrId.parse(trr3.wavelengthFileName).wavelengthFilter
        code = _binningCode(trr3)
        return [
            row
            for row in atTable()
            if (cdr := CdrId.parse(row.name)).sensor == trr3.id.sensor
            and cdr.binning == code
            and cdr.wavelengthFilter == wavelengthFilter
        ]

    @staticmethod
    def _distance(row: AtRow, sclk: float) -> float:
        """0 when the row's SCLK period holds ``sclk``, else the gap to it."""
        return max(row.sclkStart - sclk, sclk - row.sclkStop, 0.0)

    def _rowsByScan(self, trr3: Trr3) -> dict[str, list[AtRow]]:
        """The AT rows of each scan, the best period for the observation first
        (the one holding its SCLK, else the nearest, later start on ties)."""
        sclk = trr3.sclk
        rows = sorted(
            self._atRows(trr3), key=lambda r: (self._distance(r, sclk), -r.sclkStart)
        )
        scans: dict[str, list[AtRow]] = {}
        for row in rows:
            scans.setdefault(row.scanId, []).append(row)
        return scans

    def status(self, trr3: Trr3) -> list[CalibrationFileStatus]:
        """The files the observation uses and where each is, or would be
        fetched from: its WA (required), one AT per volcano scan (at least one
        is required) and its BPs (optional)."""
        entries = [self._entry("WA", Path(trr3.wavelengthFileName).stem)]
        scans = self._rowsByScan(trr3)
        for rows in scans.values():
            row = rows[0]
            held = self._distance(row, trr3.sclk) == 0
            note = (
                f"scan {row.scanId}: period holds the observation's SCLK"
                if held
                else f"scan {row.scanId}: nearest period (SCLK outside it)"
            )
            entries.append(self._entry("AT", row.name, note=note))
        if not scans:
            entries.append(
                CalibrationFileStatus(
                    "(none)",
                    "AT",
                    None,
                    None,
                    note=f"no PDS AT CDRs for sensor {trr3.id.sensor} "
                    "(atmospheric transmission CDRs are IR only)",
                )
            )
        entries += [
            self._entry("BP", name, False, "optional bad-pixel map")
            for name in _bpNames(trr3)
        ]
        return entries

    def ensure(self, trr3: Trr3) -> list[CalibrationFileStatus]:
        """Fetch what ``status`` shows missing into the cache (BPs best effort)
        and return the final status, with a note on each file that could not be
        fetched. Raises ``MissingCalibrationError`` when the WA is missing or
        no AT at all is available."""
        for entry in self.status(trr3):
            if entry.localPath is None:
                self._download(entry)
        entries = self.status(trr3)
        wa = entries[0]
        ats = [e for e in entries if e.kind == "AT"]
        missing = [wa] if wa.localPath is None else []
        if all(e.localPath is None for e in ats):
            missing += ats
        if missing:
            raise MissingCalibrationError(missing)
        return entries

    def _download(self, entry: CalibrationFileStatus) -> None:
        if entry.url is None:
            return
        folder = self._cache / entry.kind.lower()
        error: OSError | None = None
        for base in _candidateUrls(entry.kind, entry.name):
            try:
                for suffix in (".img", ".lbl"):
                    self._fetch(base + suffix, folder / (entry.name + suffix))
                self._failures.pop(entry.name, None)
                return
            except OSError as caught:
                error = caught
        self._failures[entry.name] = f"could not be fetched: {error}"

    def _require(self, kind: str, stem: str) -> Path:
        entry = self._entry(kind, stem)
        if entry.localPath is None:
            raise MissingCalibrationError([entry])
        return entry.localPath

    @staticmethod
    def _aligned(trr3: Trr3, frame: DetectorFrame) -> np.ndarray:
        """``(sample, band)`` in the cube's band order; the columns must match."""
        columns, cubeColumns = frame.values.shape[0], trr3.shape[1]
        if columns != cubeColumns:
            raise ValueError(
                f"{frame.name} has {columns} columns, but {trr3.id.stem} has "
                f"{cubeColumns} columns"
            )
        return frame.alignedTo(trr3.detectorRows)

    def wavelengths(self, trr3: Trr3) -> np.ndarray:
        """``(sample, band)`` center wavelengths from the observation's WA CDR."""
        path = self._require("WA", Path(trr3.wavelengthFileName).stem)
        return self._aligned(trr3, readDetectorFrame(path))

    def badPixels(self, trr3: Trr3) -> np.ndarray | None:
        """``(sample, band)`` bool, True where any BP CDR found flags the pixel
        (nonzero or invalid); None when the label lists none or none is
        available (BPs are optional; ``status`` shows the skipped ones)."""
        bad: np.ndarray | None = None
        for name in _bpNames(trr3):
            path = self._find("BP", name)
            if path is None:
                continue
            values = self._aligned(trr3, readDetectorFrame(path))
            flagged = np.isnan(values) | (values != 0)
            bad = flagged if bad is None else bad | flagged
        return bad

    def transmissions(
        self, trr3: Trr3, source: TransmissionSource
    ) -> list[Transmission]:
        """The candidates available locally, distinct by content. PDS ATs come
        first, those whose SCLK period holds the observation's SCLK before the
        rest (nearest period first when none does)."""
        found: list[Transmission] = []
        if source in (TransmissionSource.PDS_AT, TransmissionSource.BOTH):
            found += self._pdsTransmissions(trr3)
        if source in (TransmissionSource.CAT_VS_ADR, TransmissionSource.BOTH):
            found += self._vsAdrTransmissions(trr3)
        unique: dict[str, Transmission] = {}
        for transmission in found:
            unique.setdefault(transmission.sha256, transmission)
        return list(unique.values())

    def _pdsTransmissions(self, trr3: Trr3) -> list[Transmission]:
        """One transmission per scan: its best-ranked period that is local."""
        sclk = trr3.sclk
        chosen: list[tuple[AtRow, Path]] = []
        for rows in self._rowsByScan(trr3).values():
            local = ((row, self._find("AT", row.name)) for row in rows)
            hit = next(((row, path) for row, path in local if path is not None), None)
            if hit is not None:
                chosen.append(hit)
        chosen.sort(key=lambda c: (self._distance(c[0], sclk), -c[0].sclkStart))
        extrapolated = all(self._distance(row, sclk) > 0 for row, _ in chosen)
        result = []
        for row, path in chosen:
            values = self._aligned(trr3, readDetectorFrame(path))
            result.append(
                Transmission(
                    row.scanId,
                    path.stem.upper(),
                    values,
                    _sha256(values),
                    None,
                    row.sclkStart,
                    row.sclkStop,
                    extrapolated,
                )
            )
        return result

    def _vsAdrTransmissions(self, trr3: Trr3) -> list[Transmission]:
        """CAT's volcano-scan ADRs for the cube's sensor, binning and wavelength
        filter; the newest version of each scan. Windowed ADRs (fewer samples or
        bands than the cube) are skipped; a same-size mismatch raises."""
        if self._catAux is None:
            return []
        folder = self._catAux / "ADR" / "VS"
        wavelengthFilter = CdrId.parse(trr3.wavelengthFileName).wavelengthFilter
        code = _binningCode(trr3)
        candidates: list[tuple[VsAdrId, Path]] = []
        for path in folder.glob("*") if folder.is_dir() else []:
            if path.suffix.lower() != ".img":
                continue
            try:
                adr = VsAdrId.parse(path)
            except ValueError:
                continue
            if (adr.sensor, adr.binning, adr.wavelengthFilter) == (
                trr3.id.sensor,
                code,
                wavelengthFilter,
            ):
                candidates.append((adr, path))
        candidates.sort(key=lambda c: (c[0].scanId, -c[0].version))
        result: dict[str, Transmission] = {}
        for adr, path in candidates:
            if adr.scanId in result:
                continue
            frames = readVsAdr(path)
            samples, bands = frames[0].values.shape
            if samples < trr3.shape[1] or bands < len(trr3.detectorRows):
                continue  # a windowed ADR
            aligned = [self._aligned(trr3, frame) for frame in frames]
            result[adr.scanId] = Transmission(
                adr.scanId,
                path.stem.upper(),
                aligned[0],
                _sha256(aligned[0]),
                np.stack(aligned[1:]),
            )
        return list(result.values())
