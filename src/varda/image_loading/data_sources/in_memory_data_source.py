"""
InMemoryDataSource: thin wrapper that caches raster data in memory.

Wraps another DataSource, reading all raster data into memory for fast access
while delegating metadata and coordinate transforms to the wrapped source.

In-memory images share an ``InMemoryBudget``: when a new image would take
them over it, the least recently used are demoted, i.e. read on demand from
their files from then on (``demote``), as images too large to load are.
"""

from __future__ import annotations

import logging
import threading
import time
import weakref
from typing import TYPE_CHECKING

import numpy as np

from .cached_data_source import CachedDataSource
from .data_source import DataSource
from .delegating_data_source import DelegatingDataSource

if TYPE_CHECKING:
    import numpy.typing as npt

logger = logging.getLogger(__name__)


class InMemoryDataSource(DelegatingDataSource):
    """
    DataSource that wraps another DataSource, immediately caching all of the raster data in memory.
    This allows for quick data access at the cost of higher memory usage.

    After ``demote`` it reads from the wrapped source on demand instead, with
    the same results: values, NaN for no-data and dtype as in memory.
    """

    def __init__(self, source: DataSource, budget: InMemoryBudget | None = None):
        super().__init__(source)
        self._data: np.ndarray | None = source.readAllBands()
        self._shape: tuple[int, ...] = self._data.shape
        self._dtype = self._data.dtype
        self._onDemand: CachedDataSource | None = None
        self.lastUsed = time.monotonic()
        if budget is not None:
            budget.add(self)

    @property
    def nbytes(self) -> int:
        """Bytes held in memory (0 once demoted)."""
        data = self._data
        return 0 if data is None else data.nbytes

    @property
    def isDemoted(self) -> bool:
        return self._data is None

    def demote(self) -> None:
        """Let go of the pixels and read them on demand from now on. Arrays
        already handed out stay valid (they keep their memory until dropped)."""
        if self._data is None:
            return
        self._onDemand = CachedDataSource(self._source)
        self._data = None  # after _onDemand: a reader seeing None finds it set
        logger.info("Reading %s on demand from now on", self._source.filePath)

    def _held(self) -> np.ndarray | None:
        """The pixels, or None once demoted; counts as a use."""
        self.lastUsed = time.monotonic()
        return self._data

    def _asHeld(self, data: np.ndarray) -> np.ndarray:
        """An on-demand read in the dtype the pixels had in memory (integer
        data holding no-data is float there even where a window has none)."""
        return np.asarray(data, dtype=self._dtype)

    @property
    def _fromFile(self) -> CachedDataSource:
        assert self._onDemand is not None
        return self._onDemand

    # --- Data access ---

    def getBands(self, bandIndices: npt.ArrayLike) -> np.ndarray:
        return self.getData(bandIndices=bandIndices)

    def getPixelSpectrum(self, x: int, y: int) -> np.ndarray:
        if x < 0 or x >= self.width or y < 0 or y >= self.height:
            raise IndexError(
                f"Pixel ({x}, {y}) out of bounds for image ({self.width}, {self.height})"
            )
        data = self._held()
        if data is None:
            return self._asHeld(self._fromFile.getPixelSpectrum(x, y))
        # a copy: a view would keep the whole image alive as long as the spectrum
        return data[y, x, :].copy()

    def getData(
        self,
        bandIndices: npt.ArrayLike | None = None,
        window: tuple[int, int, int, int] | None = None,
        masked=True,  # TODO: make this arg do something if needed
    ) -> np.ndarray:
        data = self._held()
        if data is None:  # masked, as the pixels held in memory are
            return self._asHeld(self._fromFile.getData(bandIndices, window))
        if window is not None:
            row_off, col_off, h, w = window
            data = data[row_off : row_off + h, col_off : col_off + w, :]
        if bandIndices is not None:
            indices = [int(i) for i in np.asarray(bandIndices, dtype=np.uint)]
            # all bands in order (a one-band image, an RGB product) as they are
            # held, not a copy; others copied, so a band kept after the pixels
            # are let go (``demote``) doesn't keep the whole cube alive
            # (a view: whoever takes it may mark it read-only)
            data = (
                data[...]
                if indices == list(range(data.shape[2]))
                else data[:, :, indices]
            )
        return data

    def readAllBands(self) -> np.ndarray:
        data = self._held()
        return self._asHeld(self._fromFile.readAllBands()) if data is None else data

    def __getitem__(self, key) -> np.ndarray:
        data = self._held()
        if data is not None:
            return data[key]
        # numpy's indexing on the window read: an integer drops its axis
        key = key if isinstance(key, tuple) else (key,)
        if len(key) > 3 or not all(
            isinstance(k, (int, np.integer))
            or (isinstance(k, slice) and (k.step or 1) > 0)
            for k in key
        ):  # lists, reversed slices...: numpy on the whole image
            return self.readAllBands()[key]
        key = key + (slice(None),) * (3 - len(key))
        window = tuple(
            slice(int(k) % n, int(k) % n + 1) if isinstance(k, (int, np.integer)) else k
            for k, n in zip(key, self._shape)
        )
        drop = tuple(
            0 if isinstance(k, (int, np.integer)) else slice(None) for k in key
        )
        return self._asHeld(self._fromFile[window])[drop]

    # --- Metadata properties (shape from the data held; the rest delegated) ---

    @property
    def width(self) -> int:
        return self._shape[1]

    @property
    def height(self) -> int:
        return self._shape[0]

    @property
    def bandCount(self) -> int:
        return self._shape[2]

    @property
    def dtype(self) -> np.dtype:
        return self._dtype

    # --- Lifecycle ---

    def close(self) -> None:
        self._data = None  # no longer counted, nor kept alive by this source
        self._source.close()  # the file stays open for metadata until now

    def __repr__(self) -> str:
        where = "demoted" if self.isDemoted else "in memory"
        return f"InMemoryDataSource({self.width}x{self.height}x{self.bandCount}, dtype={self.dtype}, {where})"


class InMemoryBudget:
    """The bytes all in-memory images may hold together.

    ``reserve`` makes room for a new image by demoting the least recently
    used ones (they are then read on demand from their files), or says it
    can't. Images are counted while they're alive (weakly referenced), so
    one closed and dropped frees its share.
    """

    def __init__(self, limitBytes: int):
        self.limitBytes = limitBytes
        self._sources: weakref.WeakSet[InMemoryDataSource] = weakref.WeakSet()
        self._reserved = 0
        self._lock = threading.Lock()

    def add(self, source: InMemoryDataSource) -> None:
        with self._lock:
            self._sources.add(source)

    def used(self) -> int:
        """Bytes the in-memory images hold now."""
        with self._lock:
            return sum(source.nbytes for source in list(self._sources))

    def reserve(self, size: int) -> bool:
        """Make room for ``size`` more bytes, demoting the least recently used
        images as needed; False, demoting none, if it can't fit. Pair with
        ``release`` once the image is loaded (and ``add``-ed) or failed."""
        with self._lock:
            if self._reserved + size > self.limitBytes:
                return False
            held = sorted(
                (s for s in list(self._sources) if s.nbytes),
                key=lambda s: s.lastUsed,
            )
            over = sum(s.nbytes for s in held) + self._reserved + size
            over -= self.limitBytes
            for source in held:
                if over <= 0:
                    break
                over -= source.nbytes
                source.demote()
            self._reserved += size
            return True

    def release(self, size: int) -> None:
        with self._lock:
            self._reserved -= size
