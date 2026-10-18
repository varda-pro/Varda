"""Sharper tiles for the find-data map's global imagery: when the map, in
Equirectangular, is zoomed in past the bundled image's detail, the tiles
for the view (and only those) are fetched in the background from NASA's
Solar System Treks, or read from Varda's tile cache, and drawn over the
bundled image. A failed fetch leaves the bundled image showing and says so
in a short status note. One request is in flight at a time: a newer view
cancels it (its remaining tiles are skipped) and runs when it ends, so tile
fetches hold at most one of the app's background threads."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QStandardPaths, QTimer, pyqtSignal

from varda.catalogs.basemaps import (
    BasemapLayer,
    StreamedImagery,
    TileCache,
    streamTiles,
    tilesToStream,
)
from varda.utilities.background import runInBackground
from varda.workspaces.find_data.map_view import FootprintMap

DEBOUNCE_MS = 350


def cacheFolder() -> Path:
    """Where tiles are kept: ``basemap-tiles`` in Varda's cache folder."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation
    )
    return Path(base) / "basemap-tiles"


class ImageryStreamer(QObject):
    """Streams one layer's tiles for the map's view."""

    sigStatus = pyqtSignal(str)  # a short note for the status bar

    def __init__(self, footprintMap: FootprintMap, parent: QObject | None = None):
        super().__init__(parent)
        self.map = footprintMap
        self.layer: BasemapLayer | None = None
        self.cache = TileCache(cacheFolder())
        self._request = 0  # the latest view asked for; older replies are dropped
        self._busy = False  # a request is in flight
        self._again = False  # a newer view waits for it
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self.refresh)
        # a bound method: the map is a sibling, so it can outlive the streamer
        self.map.viewBox.sigRangeChanged.connect(self._onViewChanged)

    def _onViewChanged(self) -> None:
        self._timer.start()

    def setLayer(self, layer: BasemapLayer | None) -> None:
        self.layer = layer
        self.refresh()

    def refresh(self) -> None:
        """Fetch the tiles for the current view, or clear them when the
        bundled image is enough (or the map isn't equirectangular)."""
        self._request += 1  # cancels the request in flight, if any
        request, layer = self._request, self.layer
        tiles = []
        if layer is not None and not self.map.projector.polar:
            pixels = self.map.viewBox.width() * self.map.devicePixelRatioF()
            _, tiles = tilesToStream(layer, self.map.viewBounds(), pixels)
        if layer is None or not tiles:
            self.map.setTiles(None)
            return
        if self._busy:
            self._again = True  # runs once the request in flight ends
            return
        cache = self.cache
        self._busy = True
        runInBackground(
            lambda: streamTiles(
                layer, tiles, cache, cancelled=lambda: request != self._request
            ),
            lambda result: self._show(request, result),
            lambda error: self._failed(request, error),
            description="Fetching basemap tiles",
            owner=self,
        )

    def _finished(self) -> None:
        """The request in flight ended; run the newest view if one waits."""
        self._busy = False
        if self._again:
            self._again = False
            self.refresh()

    def _show(self, request: int, result: StreamedImagery) -> None:
        self._finished()
        if request != self._request:
            return  # the view moved on
        self.map.setTiles(result.rgba, result.bounds)
        if result.failed:
            self.sigStatus.emit(
                f"{result.failed} sharper basemap tiles couldn't be fetched "
                "(offline?); the bundled image shows there."
            )

    def _failed(self, request: int, error: Exception) -> None:
        self._finished()
        if request == self._request:
            self.map.setTiles(None)
            self.sigStatus.emit(f"Sharper basemap tiles couldn't be shown: {error}")
