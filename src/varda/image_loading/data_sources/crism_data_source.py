"""CRISM data sources.

``CrismProductDataSource`` opens TRR3 cubes, DDR backplanes and CDR4 frames from
their PDS3 (``.lbl``) or PDS4 (``.xml``) labels through GDAL, with the bands of
a TRR3 in ascending wavelength and 65535 as no-data. ``CrismMapLabelDataSource``
opens map-projected (MTRDR/TER) labels through the ENVI header beside them,
whose georeference is the correct one: GDAL reads the label one pixel west and
north of it.
"""

from __future__ import annotations

import logging
import warnings
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from rasterio.errors import NotGeoreferencedWarning

from varda.crism.labels import readLabel
from varda.crism.products import CdrId, ProductId
from varda.crism.readers import Trr3, readDetectorFrame, readTrr3

from .delegating_data_source import DelegatingDataSource
from .envi_data_source import ENVIDataSource, findEnviHeader
from .rasterio_data_source import RasterioDataSource
from .registry import register_data_source

if TYPE_CHECKING:
    import numpy.typing as npt

logger = logging.getLogger(__name__)

NODATA = 65535.0
TRR_FLOOR = 1e-6  # a TRR3 sample below this is invalid, as in varda.crism.readers
SWEET_SPOT_COLUMNS = 100  # the central columns of an unbinned frame
LABEL_SUFFIXES = (".lbl", ".xml")
IMAGE_TYPES = ("TRR", "DDR")
MAP_TYPES = ("MTR", "TER")
WA_FOLDER = ("CRISM_CDR", "wa")


def _productType(path: Path) -> str | None:
    """``TRR``, ``DDR``, ``MTR``... for an observation product name, ``CDR`` for
    a CDR4 name, else None."""
    try:
        return ProductId.parse(path).productType
    except ValueError:
        pass
    try:
        return "CDR" if CdrId.parse(path).level == 4 else None
    except ValueError:
        return None


def _isCrismLabel(path: Path) -> bool:
    """The label states ``INSTRUMENT_ID`` CRISM (PDS3 keyword, or the PDS4
    instrument reference)."""
    try:
        label = readLabel(path)
    except Exception:  # any parse failure of a corrupt label: not ours to open
        logger.debug("%s: label unreadable", path.name, exc_info=True)
        return False
    return label.get("INSTRUMENT_ID") == "CRISM"


def _findCaseInsensitive(folder: Path, stem: str) -> Path | None:
    """The label (else image) of the product named ``stem`` in ``folder``."""
    try:
        files = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    except OSError:
        return None
    for suffix in (*LABEL_SUFFIXES, ".img"):
        if (stem.lower() + suffix) in files:
            return files[stem.lower() + suffix]
    return None


def findWavelengthFile(label: Path, name: str) -> Path | None:
    """The WA CDR called ``name`` (case-insensitive) beside ``label``, in a
    ``CRISM_CDR/wa`` folder there or in either of the two folders above."""
    if not name:
        return None
    folder = label.absolute().parent
    candidates = [
        folder,
        *(p.joinpath(*WA_FOLDER) for p in (folder, *folder.parents[:2])),
    ]
    stem = Path(name).stem
    return next(
        (found for c in candidates if (found := _findCaseInsensitive(c, stem))), None
    )


def _centralColumns(samples: int, binning: int) -> slice:
    """The central 100 columns of an unbinned frame (270-369 of 640), scaled by
    binning, and the whole frame when it is narrower."""
    width = min(samples, max(SWEET_SPOT_COLUMNS // binning, 1))
    start = (samples - width) // 2
    return slice(start, start + width)


@register_data_source("CRISM TRDR/DDR/CDR (PDS)", LABEL_SUFFIXES)
class CrismProductDataSource(RasterioDataSource):
    """A CRISM TRR3, DDR or CDR4 product opened from its label."""

    @classmethod
    def canOpen(cls, filePath: str) -> bool:
        path = Path(filePath)
        kind = _productType(path)
        return (
            kind in (*IMAGE_TYPES, "CDR")
            and path.suffix.lower() in LABEL_SUFFIXES
            and _isCrismLabel(path)
        )

    def __init__(self, filePath: str):
        with warnings.catch_warnings():  # unprojected by design: GDAL only notes it
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            super().__init__(filePath)
        self._labelPath = Path(filePath)
        self._kind = _productType(self._labelPath)
        self._trr3: Trr3 | None = (
            readTrr3(self._labelPath) if self._kind == "TRR" else None
        )
        # GDAL lists the bands in file order; a TRR3 is shown ascending in wavelength
        self._fileOrder = np.arange(self._src.count)
        if self._trr3 is not None and self._trr3.flipped:
            self._fileOrder = self._fileOrder[::-1]

    # --- Data access ---

    def getData(
        self,
        bandIndices: npt.ArrayLike | None = None,
        window: tuple[int, int, int, int] | None = None,
        masked=True,
    ) -> np.ndarray:
        indices = (
            self._fileOrder
            if bandIndices is None
            else self._fileOrder[np.asarray(bandIndices, dtype=np.intp)]
        )
        data = super().getData(indices, window, masked=False)
        if not masked:
            return data
        if data.dtype.kind != "f":
            data = data.astype(np.float32)
        invalid = (data == NODATA) | ~np.isfinite(data)
        if self._kind == "TRR":
            invalid |= data < TRR_FLOOR
        data[invalid] = np.nan
        return data

    # --- Metadata ---

    @property
    def nodata(self) -> float:
        return NODATA

    @cached_property
    def _columnWavelengths(self) -> np.ndarray | None:
        """``(sample, band)`` center wavelengths (nm) from the WA CDR, in the
        order of the bands shown; None when it is not found."""
        if self._trr3 is None:
            return None
        waPath = findWavelengthFile(self._labelPath, self._trr3.wavelengthFileName)
        if waPath is None:
            logger.warning(
                "%s: wavelength file %s not found; showing band numbers",
                self._labelPath.name,
                self._trr3.wavelengthFileName,
            )
            return None
        try:
            return readDetectorFrame(waPath).alignedTo(self._trr3.detectorRows)
        except ValueError as error:
            logger.warning("%s: %s; showing band numbers", waPath.name, error)
            return None

    @cached_property
    def _bandWavelengths(self) -> np.ndarray | None:
        """Median of the central columns per band; a band with none is the 65535 nm
        (65.535 um) placeholder."""
        columns = self._columnWavelengths
        if columns is None or self._trr3 is None:
            return None
        central = columns[_centralColumns(columns.shape[0], self._trr3.binning)]
        medians = np.full(central.shape[1], NODATA)
        valid = np.isfinite(central).any(axis=0)
        medians[valid] = np.nanmedian(central[:, valid], axis=0)
        return medians

    @cached_property
    def wavelengths(self) -> np.ndarray:
        measured = self._bandWavelengths
        return measured if measured is not None else np.arange(1, self.bandCount + 1)

    @cached_property
    def wavelengthsType(self) -> type:
        return float if self._bandWavelengths is not None else int

    @cached_property
    def wavelengthUnits(self) -> str:
        return "Nanometers" if self._bandWavelengths is not None else "Band number"

    @cached_property
    def bandNames(self) -> list[str]:
        names = readLabel(self._labelPath).bandNames
        if len(names) == self.bandCount:
            return [names[i] for i in self._fileOrder]
        return [f"Band {i + 1}" for i in range(self.bandCount)]

    @cached_property
    def extraMetadata(self) -> dict:
        extra: dict = {}
        measured = self._bandWavelengths
        if measured is not None:
            extra["bbl"] = [int(w < NODATA) for w in measured]
            extra["crismColumnWavelengths"] = self._columnWavelengths
        return extra

    def __repr__(self) -> str:
        return f"CrismProductDataSource({self._filePath!r}, {self.width}x{self.height}x{self.bandCount})"


@register_data_source("CRISM MTRDR/TER (PDS)", LABEL_SUFFIXES)
class CrismMapLabelDataSource(DelegatingDataSource):
    """A map-projected CRISM product opened from its label, read through the
    ENVI header beside it."""

    @classmethod
    def canOpen(cls, filePath: str) -> bool:
        path = Path(filePath)
        return (
            _productType(path) in MAP_TYPES
            and path.suffix.lower() in LABEL_SUFFIXES
            and findEnviHeader(path) is not None
        )

    def __init__(self, filePath: str):
        header = findEnviHeader(filePath)
        if header is None:
            raise FileNotFoundError(f"No ENVI header beside {Path(filePath).name}")
        super().__init__(ENVIDataSource(str(header)))
        self._labelPath = filePath

    @property
    def filePath(self) -> str:
        return self._labelPath

    def getBands(self, bandIndices: npt.ArrayLike) -> np.ndarray:
        return self._source.getBands(bandIndices)

    def getData(
        self,
        bandIndices: npt.ArrayLike | None = None,
        window: tuple[int, int, int, int] | None = None,
        masked=True,
    ) -> np.ndarray:
        return self._source.getData(bandIndices, window, masked)

    def __getitem__(self, key) -> np.ndarray:
        return self._source[key]

    def __repr__(self) -> str:
        return f"CrismMapLabelDataSource({self._labelPath!r})"
