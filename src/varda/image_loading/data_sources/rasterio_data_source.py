"""
RasterioDataSource: default DataSource implementation for any GDAL-supported format.

Opens the file with rasterio and keeps the handle for on-demand reads.
All data methods transpose from rasterio's native (bands, h, w) to (h, w, bands).
"""

from __future__ import annotations

import threading
import weakref

import logging
from functools import cached_property
from typing import TYPE_CHECKING

import numpy as np

import rasterio as rio
from rasterio.enums import MaskFlags
from rasterio.windows import Window
from affine import Affine
import affine as affine_module
from pyproj import CRS

from varda.common.provenance import VARDA_NAMESPACE, fileMetadata
from varda.utilities.proj_lock import PROJ_LOCK

from .data_source import (
    DataSource,
    fallbackDisplayBands,
    geoToPixelIndex,
    pixelCenterToGeo,
)
from .registry import register_data_source

if TYPE_CHECKING:
    import numpy.typing as npt

# disable verbose rasterio logging
logging.getLogger("rasterio").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# GDAL metadata domains holding a planetary label (read by varda.geo.labels)
PLANETARY_LABEL_DOMAINS = ("xml:PDS4", "json:ISIS3")


def _maskedToNan(
    reader: rio.DatasetReader,
    data: np.ndarray,
    indexes: list[int],
    window: Window,
) -> np.ndarray:
    """``data`` (bands, h, w) with the pixels GDAL's mask marks invalid set to
    NaN, as ``read(masked=True).filled(nan)`` gave, but in place, one band's
    mask at a time. Integer data holding no-data becomes float32 (8 and 16-bit)
    or float64 first; integer data without any keeps its type. Other types
    (complex) get the no-data value, as ``filled()`` gave."""
    if all(MaskFlags.all_valid in reader.mask_flag_enums[i - 1] for i in indexes):
        return data
    mask = np.empty(data.shape[1:], dtype=np.uint8)

    def invalid(index: int) -> np.ndarray:
        reader.read_masks(index, window=window, out=mask)
        return mask == 0

    if data.dtype.kind in "iub":
        if not any(invalid(index).any() for index in indexes):
            return data
        data = data.astype(np.float32 if data.dtype.itemsize <= 2 else np.float64)
    if data.dtype.kind == "f":
        fill = np.nan
    elif (
        len({reader.nodatavals[i - 1] for i in indexes}) == 1
        and reader.nodata is not None
    ):
        fill = reader.nodata
    else:
        fill = np.ma.default_fill_value(data.dtype)
    for band, index in zip(data, indexes):
        np.putmask(band, invalid(index), fill)
    return data


@register_data_source("TIFF/GeoTIFF", (".tif", ".tiff", ".geotiff", ".gtiff"))
@register_data_source(
    "Common Images", (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tga")
)
class RasterioDataSource(DataSource):
    """DataSource backed by a rasterio-opened file handle.

    Works with any format GDAL supports: GeoTIFF, ENVI, PNG, JPEG, BMP, HDF5, etc.

    A GDAL handle must not be used by two threads at once, so pixels are read
    through one handle per thread (``_reader``); the display and a background
    analysis can read the same image together.
    """

    def __init__(self, filePath: str):
        self._filePath = filePath
        self._src: rio.DatasetReader = rio.open(filePath)
        self._readerThread = threading.get_ident()
        self._threadReaders = threading.local()
        # every other thread's handle, until close() or that thread ends
        self._extraReaders: weakref.WeakSet[rio.DatasetReader] = weakref.WeakSet()
        self._readersLock = threading.Lock()

    def _reader(self) -> rio.DatasetReader:
        """This thread's handle: the original for the thread that opened the
        file, a new one (kept for reuse) for any other."""
        if threading.get_ident() == self._readerThread:
            return self._src
        reader = getattr(self._threadReaders, "reader", None)
        if reader is None:
            reader = rio.open(self._src.name)
            self._threadReaders.reader = reader
            with self._readersLock:
                self._extraReaders.add(reader)
        return reader

    # --- Data access ---

    def getBands(self, bandIndices: npt.ArrayLike) -> np.ndarray:
        return self.getData(bandIndices=bandIndices)

    def getPixelSpectrum(self, x: int, y: int) -> np.ndarray:
        if x < 0 or x >= self.width or y < 0 or y >= self.height:
            raise IndexError(
                f"Pixel ({x}, {y}) out of bounds for image of size ({self.width}, {self.height})"
            )
        window = (y, x, 1, 1)
        data = self.getData(window=window)
        # (bands, 1, 1) -> (bands,)
        result = data.squeeze()
        return result

    def getData(
        self,
        bandIndices: npt.ArrayLike | None = None,
        window: tuple[int, int, int, int] | None = None,
        masked=True,
    ) -> np.ndarray:
        return self._read(bandIndices, window, masked)

    def getDataReduced(
        self,
        bandIndices: npt.ArrayLike | None,
        window: tuple[int, int, int, int] | None,
        step: int,
        masked: bool = True,
    ) -> np.ndarray:
        """Read at the reduced size: GDAL takes the nearest pixels from the
        file's overviews (JPEG 2000 resolution levels, GeoTIFF overviews)
        when it has them."""
        return self._read(bandIndices, window, masked, max(1, int(step)))

    def _read(
        self,
        bandIndices: npt.ArrayLike | None,
        window: tuple[int, int, int, int] | None,
        masked: bool,
        step: int = 1,
    ) -> np.ndarray:
        """Pixels as (h, w, bands); with ``masked``, GDAL's no-data mask as NaN
        (integer data holding no-data becomes float first: NaN has no integer
        form).

        Read into one array, band by band in memory (the result is a band-last
        view of it, so a band stays contiguous for display), with the mask
        applied in place: no masked array and no second copy, so a read peaks
        at about the size of its result.
        """
        reader = self._reader()
        rowOff, colOff, height, width = window or (0, 0, self.height, self.width)
        # clipped to the image, as rasterio's read clips a window
        rowOff, colOff = max(rowOff, 0), max(colOff, 0)
        height = (
            max(min(window[0] + height, self.height) - rowOff, 0) if window else height
        )
        width = max(min(window[1] + width, self.width) - colOff, 0) if window else width
        indexes = (
            list(range(1, self.bandCount + 1))
            if bandIndices is None
            # rasterio uses 1-based band indexing
            else [int(i) + 1 for i in np.asarray(bandIndices, dtype=np.uint)]
        )
        if len({reader.dtypes[i - 1] for i in indexes}) > 1:
            raise ValueError(f"The bands of {self.filePath} have different data types")
        data = np.empty(
            (len(indexes), -(-height // step), -(-width // step)),
            dtype=reader.dtypes[indexes[0] - 1],
        )
        if data.size:
            rioWindow = Window.from_slices(
                (rowOff, rowOff + height), (colOff, colOff + width)
            )
            reader.read(indexes, window=rioWindow, out=data)
            if masked:
                data = _maskedToNan(reader, data, indexes, rioWindow)
        # (bands, h, w) -> (h, w, bands)
        return np.moveaxis(data, 0, -1)

    def readAllBands(self) -> np.ndarray:
        return self.getData()

    def __getitem__(self, key) -> np.ndarray:
        if not isinstance(key, tuple):
            key = (key,)

        # Pad with full slices for missing dimensions
        while len(key) < 3:
            key = key + (slice(None),)

        row_key, col_key, band_key = key

        # Resolve band indices
        band_indices = self._resolve_slice(band_key, self.bandCount)

        # Resolve spatial window
        row_indices = self._resolve_slice(row_key, self.height)
        col_indices = self._resolve_slice(col_key, self.width)

        row_start = row_indices[0] if len(row_indices) > 0 else 0
        row_stop = row_indices[-1] + 1 if len(row_indices) > 0 else 0
        col_start = col_indices[0] if len(col_indices) > 0 else 0
        col_stop = col_indices[-1] + 1 if len(col_indices) > 0 else 0

        window = (row_start, col_start, row_stop - row_start, col_stop - col_start)
        data = self.getData(bandIndices=band_indices, window=window)

        # Handle step if slices had steps > 1
        row_step = row_key.step if isinstance(row_key, slice) and row_key.step else 1
        col_step = col_key.step if isinstance(col_key, slice) and col_key.step else 1
        if row_step != 1 or col_step != 1:
            data = data[::row_step, ::col_step, :]

        return data

    @staticmethod
    def _resolve_slice(key, length: int) -> np.ndarray:
        if isinstance(key, (int, np.integer)):
            return np.array([int(key)])
        elif isinstance(key, slice):
            return np.arange(*key.indices(length))
        elif isinstance(key, (list, np.ndarray)):
            return np.asarray(key)
        else:
            raise TypeError(f"Unsupported index type: {type(key)}")

    # --- File-intrinsic properties ---

    @property
    def filePath(self) -> str:
        return self._filePath

    @property
    def width(self) -> int:
        return self._src.width

    @property
    def height(self) -> int:
        return self._src.height

    @property
    def bandCount(self) -> int:
        return self._src.count

    @property
    def dtype(self) -> np.dtype:
        return np.dtype(self._src.dtypes[0])

    @property
    def nodata(self) -> float | None:
        return self._src.nodata

    @cached_property
    def wavelengths(self) -> np.ndarray:
        tags = self._src.tags()
        if "wavelength" in tags:
            try:
                wl_strings = [w.strip() for w in tags["wavelength"].split(",")]
                return np.array([float(w) for w in wl_strings])
            except ValueError:
                return np.array(wl_strings, dtype="U50")
        return np.arange(self.bandCount)

    @cached_property
    def wavelengthsType(self) -> type:
        wl = self.wavelengths
        if wl.dtype.kind == "f":
            return float
        elif wl.dtype.kind == "i":
            return int
        return str

    @cached_property
    def wavelengthUnits(self) -> str:
        tags = self._src.tags()
        return tags.get("wavelength_units", "Unknown")

    @cached_property
    def bandNames(self) -> list[str]:
        names = []
        for i in range(1, self.bandCount + 1):
            name = self._src.tags(i).get("name", f"Band {i}")
            names.append(name)
        return names

    @property
    def transform(self) -> Affine:
        t = self._src.transform
        if t is None:
            return affine_module.identity
        return t

    @property
    def crs(self) -> CRS | None:
        # Built once: every access used to create a new pyproj CRS, i.e. PROJ
        # calls from whichever thread asked (see utilities/proj_lock.py)
        return self._crsState()[0]

    @property
    def rawCrsText(self) -> str | None:
        return self._crsState()[1]

    @property
    def crsError(self) -> str | None:
        return self._crsState()[2]

    def _crsState(self) -> tuple[CRS | None, str | None, str | None]:
        """The CRS, its raw text and its parse error, read once."""
        with PROJ_LOCK:
            if not hasattr(self, "_crsRead"):
                self._crsRead = self._readCrs()
            return self._crsRead

    def _readCrs(self) -> tuple[CRS | None, str | None, str | None]:
        """The CRS, the file's WKT for it, and why PROJ couldn't parse it."""
        rawText = None
        try:
            srcCrs = self._src.crs
            if srcCrs is None:
                return None, None, None
            rawText = srcCrs.to_wkt()
            return CRS.from_wkt(rawText), rawText, None
        except Exception as error:
            logger.error(f"Failed to parse the CRS of {self.filePath}: {error}")
            return None, rawText, str(error) or type(error).__name__

    def gdalMetadata(self) -> dict[str, dict[str, str]]:
        namespaces = self._src.tag_namespaces() or []
        return {
            domain: self._src.tags(ns=domain)
            for domain in PLANETARY_LABEL_DOMAINS
            if domain in namespaces
        }

    @property
    def driver(self) -> str:
        return self._src.driver

    @cached_property
    def defaultBands(self) -> np.ndarray:
        return fallbackDisplayBands(
            self.wavelengths,
            self.wavelengthUnits,
            self.nodata,
            self.extraMetadata,
            self.bandCount,
        )

    @cached_property
    def isParameterImage(self) -> bool:
        return False

    @cached_property
    def extraMetadata(self) -> dict:
        return self._vardaMetadata()

    def _vardaMetadata(self) -> dict:
        """What Varda wrote in the file's ``VARDA`` domain (its provenance and
        scientific metadata), and where it was downloaded from; {} for other
        files."""
        return fileMetadata(
            self._reader().tags(ns=VARDA_NAMESPACE), (self.filePath, self._src.name)
        )

    @cached_property
    def description(self) -> str:
        return ""

    # --- Coordinate transformation ---

    def pixelToGeo(self, col: int, row: int) -> tuple[float, float]:
        return pixelCenterToGeo(self.transform, col, row)

    def geoToPixel(self, x: float, y: float) -> tuple[int, int]:
        return geoToPixelIndex(self.transform, x, y)

    # --- Lifecycle ---

    def close(self) -> None:
        with self._readersLock:
            readers = [self._src, *self._extraReaders]
            self._extraReaders.clear()
        for reader in readers:
            if not reader.closed:
                reader.close()

    def __del__(self):
        if hasattr(self, "_readersLock"):  # __init__ may have failed to open
            self.close()

    def __repr__(self) -> str:
        return f"RasterioDataSource({self._filePath!r}, {self.width}x{self.height}x{self.bandCount})"
