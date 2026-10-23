"""CachedDataSource: reads a large image on demand, keeping the last few band
reads (e.g. the displayed bands) in a small least-recently-used cache.

Used instead of loading the whole image into memory when it would take too
much of it (see ``image_loading_service.wrapForReading``). Whole-image reads
(an analysis) go straight to the file and are not cached. Every read (bands,
windows, spectra, the whole image) is serialized with one lock: the display
(GUI thread) and an analysis (a worker) may read at once, and a rasterio file
handle is not thread-safe.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

import numpy as np
import numpy.typing as npt

from .data_source import DataSource
from .delegating_data_source import DelegatingDataSource

DEFAULT_ENTRIES = 8


class CachedDataSource(DelegatingDataSource):
    def __init__(self, source: DataSource, maxEntries: int = DEFAULT_ENTRIES):
        super().__init__(source)
        self.maxEntries = maxEntries
        self._cache: OrderedDict[tuple[int, ...], np.ndarray] = OrderedDict()
        self._lock = threading.RLock()

    def getBands(self, bandIndices: npt.ArrayLike) -> np.ndarray:
        key = tuple(int(i) for i in np.atleast_1d(np.asarray(bandIndices)))
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
            data = np.asarray(self._source.getData(bandIndices=list(key)))
            data.setflags(write=False)  # shared by every caller
            self._cache[key] = data
            if len(self._cache) > self.maxEntries:
                self._cache.popitem(last=False)
            return data

    def getData(
        self,
        bandIndices: npt.ArrayLike | None = None,
        window: tuple[int, int, int, int] | None = None,
        masked=True,
    ) -> np.ndarray:
        if window is None and bandIndices is not None:
            return self.getBands(bandIndices)
        with self._lock:
            return self._source.getData(
                bandIndices=bandIndices, window=window, masked=masked
            )

    def getDataReduced(
        self,
        bandIndices: npt.ArrayLike | None,
        window: tuple[int, int, int, int] | None,
        step: int,
    ) -> np.ndarray:
        with self._lock:  # from the file's overviews, not kept
            return self._source.getDataReduced(bandIndices, window, step)

    def getPixelSpectrum(self, x: int, y: int) -> np.ndarray:
        with self._lock:
            return self._source.getPixelSpectrum(x, y)

    def readAllBands(self) -> np.ndarray:
        with self._lock:
            return self._source.readAllBands()

    def __getitem__(self, key) -> np.ndarray:
        with self._lock:
            return self._source[key]

    def __repr__(self) -> str:
        return f"CachedDataSource({self._source!r}, {len(self._cache)}/{self.maxEntries} cached)"
