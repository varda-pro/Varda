"""Readers for CRISM TRR3 cubes, DDR backplanes, one-line CDR4 detector frames
(WA, AT, BP, SB) and volcano-scan ADRs, plus a slew-row mask.

Pixels come from the label's memory-mapped layout; only the rows asked for are
copied. Invalid samples (65535, below 1e-6, non-finite) become NaN in memory.
"""

from enum import IntEnum
from pathlib import Path

import numpy as np
from attrs import define

from varda.crism.labels import ArrayLayout, ProductLabel, readLabel
from varda.crism.products import ProductId

_INVALID = 65535.0
_FLOOR = 1e-6
_ROWNUM_MASK = 0x1FF  # the label's 9-bit BIT_MASK
_MARS_RADIUS_M = 3396190.0
_SLEW_FACTOR = 4.0


def _labelFor(path: Path) -> ProductLabel:
    """Read the label of ``path``, which may be the ``.IMG`` it describes."""
    path = Path(path)
    if path.suffix.lower() == ".img":
        path = next(
            (
                p
                for p in path.parent.glob("*")
                if p.stem == path.stem and p.suffix.lower() in (".lbl", ".xml")
            ),
            path.with_suffix(".lbl"),
        )
    return readLabel(path)


def _detectorRows(layout: ArrayLayout) -> np.ndarray:
    """The ROWNUM table: one detector row number per band, in file order."""
    if layout.rownumOffset is None:
        raise ValueError(f"{layout.dataFile.name}: no ROWNUM table")
    # Some VS ADR labels point the table into the image (a stale record
    # number); the table always follows the image, so never read before its end.
    imageEnd = (
        layout.offset
        + layout.lines * layout.bands * layout.samples * layout.dtype.itemsize
    )
    table = np.fromfile(
        layout.dataFile,
        dtype=">u2",
        count=layout.bands,
        offset=max(layout.rownumOffset, imageEnd),
    )
    return (table & _ROWNUM_MASK).astype(np.int64)


def _nodataToNan(values: np.ndarray, floor: float | None = None) -> np.ndarray:
    """float64 copy with 65535, non-finite and (optionally) tiny samples as NaN."""
    result = np.array(values, dtype=np.float64)
    bad = ~np.isfinite(result) | (result == _INVALID)
    if floor is not None:
        bad |= result < floor
    result[bad] = np.nan
    return result


def _number(label: ProductLabel, key: str) -> float:
    value = label.get(key)
    if not isinstance(value, (int, float, str)):
        raise ValueError(f"{label.path.name}: {key} is missing")
    return float(value)


@define(frozen=True, eq=False)
class Trr3:
    """A TRR3 (I/F) cube, presented with bands in ascending wavelength."""

    id: ProductId
    label: ProductLabel
    detectorRows: np.ndarray
    reversed: bool = False  # the file stores wavelength descending

    @property
    def shape(self) -> tuple[int, int, int]:
        image = self.label.image
        return (image.lines, image.samples, image.bands)

    @property
    def binning(self) -> int:
        return int(_number(self.label, "PIXEL_AVERAGING_WIDTH"))

    @property
    def sclk(self) -> float:
        """Spacecraft clock start, the number after the partition ``/``."""
        count = self.label.get("SPACECRAFT_CLOCK_START_COUNT")
        if not isinstance(count, str):
            raise ValueError(f"{self.label.path.name}: no SPACECRAFT_CLOCK_START_COUNT")
        return float(count.rsplit("/", 1)[-1])

    @property
    def wavelengthFileName(self) -> str:
        return str(self.label.get("MRO:WAVELENGTH_FILE_NAME", ""))

    def lines(self, rows: slice) -> np.ndarray:
        """float64 ``(line, sample, band)``, ascending wavelength, NaN for
        invalid samples. Only the requested lines are read from disk."""
        chunk = self.label.image.memmap()[rows]  # (l, band, sample)
        cube = _nodataToNan(chunk, _FLOOR).transpose(0, 2, 1)
        return cube[:, :, ::-1] if self.reversed else cube


def readTrr3(path: Path) -> Trr3:
    """Open a TRR3 ``.lbl``. L rows ascend with descending wavelength, so the
    band order is reversed when the file's detector rows ascend."""
    label = readLabel(path)
    rows = _detectorRows(label.image)
    flip = bool(rows[0] < rows[-1])
    return Trr3(ProductId.parse(path), label, rows[::-1].copy() if flip else rows, flip)


class DdrBand(IntEnum):
    INCIDENCE_AREOID = 0
    EMISSION_AREOID = 1
    PHASE = 2
    LATITUDE = 3
    LONGITUDE = 4
    INCIDENCE_MOLA = 5
    EMISSION_MOLA = 6
    SLOPE = 7
    SLOPE_AZIMUTH = 8
    ELEVATION = 9
    THERMAL_INERTIA = 10
    ALBEDO = 11
    LOCAL_SOLAR_TIME = 12
    SPARE = 13


@define(frozen=True, eq=False)
class Ddr:
    """A derived data record: 14 backplanes in BSQ order (see ``DdrBand``)."""

    id: ProductId
    label: ProductLabel

    def band(self, band: DdrBand, rows: slice | None = None) -> np.ndarray:
        """float64 ``(line, sample)`` with 65535 as NaN."""
        plane = self.label.image.memmap()[int(band)]
        return _nodataToNan(plane[rows] if rows is not None else plane)


def readDdr(path: Path) -> Ddr:
    return Ddr(ProductId.parse(path), readLabel(path))


@define(frozen=True, eq=False)
class DetectorFrame:
    """One line of a detector-frame product: ``values`` is (sample, band) with
    NaN for 65535, and ``detectorRows`` names each band's detector row."""

    name: str
    values: np.ndarray
    detectorRows: np.ndarray

    def alignedTo(self, rows: np.ndarray) -> np.ndarray:
        """``(sample, len(rows))`` in the order of ``rows``."""
        index = {int(row): i for i, row in enumerate(self.detectorRows)}
        missing = [int(r) for r in rows if int(r) not in index]
        if missing:
            raise ValueError(f"{self.name}: detector row {missing[0]} is missing")
        return self.values[:, [index[int(r)] for r in rows]]


def readDetectorFrame(path: Path, line: int = 0) -> DetectorFrame:
    """Line ``line`` of a WA, AT, BP or SB CDR (one line) or a VS ADR."""
    label = _labelFor(path)
    frame = label.image.memmap()[line]  # (band, sample)
    return DetectorFrame(
        Path(path).stem.upper(), _nodataToNan(frame.T), _detectorRows(label.image)
    )


def readVsAdr(path: Path) -> tuple[DetectorFrame, DetectorFrame, DetectorFrame]:
    """Transmission, McGuire artifact and Pelkey artifact of a volcano-scan ADR."""
    first, second, third = (readDetectorFrame(path, line) for line in range(3))
    return first, second, third


def _alongTrackSteps(ddr: Ddr) -> np.ndarray:
    """Per-row median ground step (m) from the previous row; row 0 copies row 1."""
    lat = np.radians(ddr.band(DdrBand.LATITUDE))
    lon = np.radians(ddr.band(DdrBand.LONGITUDE))
    dLat = np.diff(lat, axis=0)
    dLon = np.diff(lon, axis=0)
    dLon = (dLon + np.pi) % (2 * np.pi) - np.pi
    a = (
        np.sin(dLat / 2) ** 2
        + np.cos(lat[1:]) * np.cos(lat[:-1]) * np.sin(dLon / 2) ** 2
    )
    step = 2 * _MARS_RADIUS_M * np.arcsin(np.sqrt(a))
    with np.errstate(all="ignore"):
        medians = np.nanmedian(step, axis=1)
    return np.concatenate(([medians[0]], medians))


def usableLines(ddr: Ddr) -> np.ndarray:
    """``(lines,)`` bool, False for slew rows.

    A row is a slew row when its median along-track ground step (great-circle
    distance to the previous row, from DDR latitude and longitude) exceeds 4x
    the scene median; only the largest contiguous run of good rows is kept.
    The slew-mask idea is adapted from jezero-rim-mapping (MIT, Valantinas,
    https://github.com/euchas/jezero-rim-mapping, code/crism_io.py); this
    implementation is written independently.
    """
    steps = _alongTrackSteps(ddr)
    good = np.isfinite(steps) & (steps <= _SLEW_FACTOR * np.nanmedian(steps))
    usable = np.zeros_like(good)
    bestStart = bestLength = start = 0
    for i, ok in enumerate([*good, False]):
        if ok:
            continue
        if i - start > bestLength:
            bestStart, bestLength = start, i - start
        start = i + 1
    usable[bestStart : bestStart + bestLength] = True
    return usable
