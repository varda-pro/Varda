"""MTRDR-style ENVI products, their sidecar files and the optional COG.

Qt-free. ``EnviCubeWriter`` writes a BSQ float32 cube block by block (lines
or bands) into ``<image>.part`` with plain file I/O at each block's offset, so
the cube is never held and no memory-mapped page of it is ever dirtied in the
process; lines never written are filled with no-data when it closes. The
header (also through a ``.part``) and the image are renamed into place only
when the whole product was written. If anything raises, the parts are deleted
and a product already in the folder is left untouched. NaN and inf are written
as 65535, the MTRDR no-data value; no NaN is ever on disk.
"""

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self

import numpy as np
import numpy.typing as npt
import rasterio
from rasterio import shutil as rasterioShutil  # ty: ignore[unresolved-import]
from attrs import frozen

from varda.crism.projection import MapGrid
from varda.crism.smile import StandardVector
from varda.utilities.proj_lock import PROJ_LOCK

NODATA = 65535.0
_LIST_COLUMNS = 6
_DTYPE = np.dtype("<f4")
_SAMPLE_BYTES = _DTYPE.itemsize


@frozen
class EnviBands:
    """Per-band header content; ``defaultBands`` are 1-based as in ENVI."""

    names: tuple[str, ...] = ()
    wavelengths: np.ndarray | None = None
    fwhm: np.ndarray | None = None
    bbl: np.ndarray | None = None
    defaultBands: tuple[int, int, int] | None = None

    @classmethod
    def fromStandardVector(
        cls,
        vector: StandardVector,
        names: tuple[str, ...] = (),
        defaultBands: tuple[int, int, int] | None = None,
    ) -> Self:
        return cls(names, vector.wavelengths, vector.fwhm, vector.bbl, defaultBands)


def _enviList(values: Sequence[str]) -> str:
    rows = [
        ", ".join(values[i : i + _LIST_COLUMNS])
        for i in range(0, len(values), _LIST_COLUMNS)
    ]
    return "{\n  " + ",\n  ".join(rows) + "}"


def _floats(values: npt.ArrayLike) -> list[str]:
    return [f"{v:.6f}" for v in np.asarray(values, dtype=np.float64)]


def _clean(block: np.ndarray) -> np.ndarray:
    """float32 with NaN and inf as no-data."""
    return np.where(np.isfinite(block), block, NODATA).astype(_DTYPE)


def _partOf(path: Path) -> Path:
    return path.with_name(path.name + ".part")


class EnviCubeWriter:
    """Context manager writing one BSQ float32 ENVI product atomically.

    ``extraHeader`` entries (``"varda history"``, the sigma keys, ...) are
    written as ``key = {value}`` after the standard keys.
    """

    def __init__(
        self,
        path: Path,
        lines: int,
        samples: int,
        bands: EnviBands,
        grid: MapGrid | None,
        description: str,
        extraHeader: Mapping[str, str],
    ):
        self._path = path
        self._header = path.with_suffix(".hdr")
        self._lines = lines
        self._samples = samples
        self._bands = bands
        self._grid = grid
        self._description = description
        self._extra = dict(extraHeader)
        self._count = max(len(bands.names), self._bandCount(bands))
        self._file: BinaryIO | None = None
        self._linesWritten = np.zeros(lines, dtype=bool)
        self._bandsWritten = np.zeros(self._count, dtype=bool)

    @staticmethod
    def _bandCount(bands: EnviBands) -> int:
        for values in (bands.wavelengths, bands.fwhm, bands.bbl):
            if values is not None:
                return len(values)
        return 0

    def __enter__(self) -> Self:
        self._file = _partOf(self._path).open("w+b")
        self._file.truncate(self._count * self._lines * self._samples * _SAMPLE_BYTES)
        return self

    def _rows(self, band: int, firstLine: int, rows: np.ndarray) -> None:
        """Lines of one band, contiguous in BSQ, at their offset in the file."""
        if self._file is None:
            raise RuntimeError("EnviCubeWriter used outside its with block")
        self._file.seek(
            (band * self._lines + firstLine) * self._samples * _SAMPLE_BYTES
        )
        self._file.write(np.ascontiguousarray(rows, dtype=_DTYPE).tobytes())

    def write(self, firstLine: int, block: np.ndarray) -> None:
        """Write ``block`` (lines, samples, bands) from image line ``firstLine``."""
        lines, samples, bands = np.shape(block)
        if firstLine < 0 or firstLine + lines > self._lines:
            raise ValueError(
                f"lines {firstLine}–{firstLine + lines - 1} are outside the "
                f"product's {self._lines}"
            )
        if (samples, bands) != (self._samples, self._count):
            raise ValueError(
                f"a block of {samples} samples × {bands} bands for a product of "
                f"{self._samples} × {self._count}"
            )
        clean = _clean(block)
        for band in range(clean.shape[2]):
            self._rows(band, firstLine, clean[:, :, band])
        self._linesWritten[firstLine : firstLine + clean.shape[0]] = True

    def writeBands(self, firstBand: int, block: np.ndarray) -> None:
        """Write ``block`` (lines, samples, bands): every line of the bands from
        ``firstBand`` on, e.g. one band block of an area-weighted product."""
        lines, samples, bands = np.shape(block)
        if firstBand < 0 or firstBand + bands > self._count:
            raise ValueError(
                f"bands {firstBand}–{firstBand + bands - 1} are outside the "
                f"product's {self._count}"
            )
        if (lines, samples) != (self._lines, self._samples):
            raise ValueError(
                f"a block of {lines} lines × {samples} samples for a product of "
                f"{self._lines} × {self._samples}"
            )
        clean = _clean(block)
        for k in range(clean.shape[2]):
            self._rows(firstBand + k, 0, clean[:, :, k])
        self._bandsWritten[firstBand : firstBand + clean.shape[2]] = True

    def _fillUnwritten(self) -> None:
        """No-data in every line of every band that neither ``write`` nor
        ``writeBands`` reached (the truncated file holds zeros there)."""
        missing = ~self._linesWritten
        if not missing.any():
            return
        edges = np.flatnonzero(np.diff(np.concatenate(([0], missing.astype(int), [0]))))
        runs = list(zip(edges[::2], edges[1::2]))
        for band in np.flatnonzero(~self._bandsWritten):
            for first, stop in runs:
                rows = np.full((stop - first, self._samples), NODATA, dtype=_DTYPE)
                self._rows(int(band), int(first), rows)

    def __exit__(
        self,
        excType: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        image, header = _partOf(self._path), _partOf(self._header)
        complete = False
        try:
            if excType is None:
                self._fillUnwritten()
                complete = True
        finally:
            if self._file is not None:
                self._file.close()
                self._file = None
            if not complete:
                image.unlink(missing_ok=True)
        if not complete:
            return
        try:
            header.write_text(self._headerText())
            os.replace(image, self._path)
            os.replace(header, self._header)
        finally:
            image.unlink(missing_ok=True)
            header.unlink(missing_ok=True)

    def _headerText(self) -> str:
        bands = self._bands
        lines = [
            "ENVI",
            f"description = {{\n  {self._description}}}",
            f"samples = {self._samples}",
            f"lines = {self._lines}",
            f"bands = {self._count}",
            "header offset = 0",
            "file type = ENVI Standard",
            "data type = 4",
            "interleave = bsq",
            "sensor type = Unknown",
            "byte order = 0",
        ]
        if self._grid is not None:
            lines += [
                f"map info = {{{self._grid.enviMapInfo()}}}",
                f"coordinate system string = {{{self._grid.crsWkt()}}}",
            ]
        if bands.defaultBands is not None:
            lines.append(
                "default bands = {" + ",".join(map(str, bands.defaultBands)) + "}"
            )
        lines.append(f"data ignore value = {NODATA:.8e}")
        if bands.wavelengths is not None:
            lines += [
                "wavelength units = Nanometers",
                f"wavelength = {_enviList(_floats(bands.wavelengths))}",
            ]
        if bands.fwhm is not None:
            lines.append(f"fwhm = {_enviList(_floats(bands.fwhm))}")
        if bands.bbl is not None:
            lines.append(f"bbl = {_enviList([str(int(v)) for v in bands.bbl])}")
        if bands.names:
            lines.append(f"band names = {_enviList(list(bands.names))}")
        lines += [f"{key} = {{{value}}}" for key, value in self._extra.items()]
        return "\n".join(lines) + "\n"


def historyString(steps: Sequence[str]) -> str:
    """The processing history, steps joined with ``_`` (the ``varda history`` value)."""
    return "_".join(steps)


def sha256File(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def jsonSafe(value: object) -> object:
    """``value`` as plain JSON data: numpy scalars and arrays become Python
    values and lists, tuples lists, paths strings, and non-finite floats
    (inf and NaN, e.g. a transmission score) become None (JSON null)."""
    if isinstance(value, np.ndarray):
        return [jsonSafe(item) for item in value]  # ty: ignore[not-iterable]
    if isinstance(value, np.generic):
        return jsonSafe(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): jsonSafe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonSafe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _sidecar(stem: Path, suffix: str) -> Path:
    return stem.with_name(stem.name + suffix)


def writeSidecars(
    stem: Path,
    recipe: Mapping[str, object],
    provenance: Mapping[str, object],
    diagnostics: Mapping[str, np.ndarray],
) -> tuple[Path, Path, Path]:
    """Write ``<stem>.recipe.json``, ``.provenance.json`` and ``.diagnostics.npz``
    (each through a temporary name). Non-finite floats in the JSON are null."""
    recipePath = _sidecar(stem, ".recipe.json")
    provenancePath = _sidecar(stem, ".provenance.json")
    diagnosticsPath = _sidecar(stem, ".diagnostics.npz")
    for path, content in ((recipePath, recipe), (provenancePath, provenance)):
        part = _partOf(path)
        part.write_text(json.dumps(jsonSafe(content), indent=2, allow_nan=False))
        os.replace(part, path)
    part = _partOf(diagnosticsPath)
    with part.open("wb") as file:
        np.savez_compressed(file, **dict(diagnostics))  # ty: ignore[invalid-argument-type]
    os.replace(part, diagnosticsPath)
    return recipePath, provenancePath, diagnosticsPath


def writeCog(enviImage: Path, target: Path) -> Path:
    """Convert an ENVI product to a Cloud Optimized GeoTIFF (DEFLATE), keeping
    its no-data value and map projection. Only opening it, where GDAL builds
    the CRS from the header's WKT, holds ``PROJ_LOCK`` (as ``writeRaster``
    does): the GUI thread's pyproj calls wait on it, not on the conversion."""
    part = _partOf(target)
    with PROJ_LOCK:
        src = rasterio.open(enviImage)
    with src:
        rasterioShutil.copy(src, part, driver="COG", compress="DEFLATE")
    os.replace(part, target)
    return target
