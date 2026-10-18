"""Global imagery for the find-data map: a small image of each body bundled
with Varda (it works offline), and sharper tiles streamed from NASA's Solar
System Treks as the map zooms in.

Every layer here was checked against its Trek WMTS GetCapabilities
(``capabilitiesUrl``) on 2026-10-17. All use the same tile matrix set,
``default028mm`` in Trek's equirectangular ("EQ") projection: longitude
-180° to 180° east and latitude 90° to -90°, the top-left corner at (-180°,
90°), 256-pixel tiles, and at zoom ``z`` a grid of 2^(z+1) × 2^z tiles, each
180° / 2^z on a side. Rows count down from the north, columns east from
-180°. That is the map's own equirectangular projection, so tiles are drawn
as they come. No Qt.
"""

from __future__ import annotations

import io
import math
import os
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from varda.catalogs.bodies import bodyName
from varda.catalogs.catalog import CatalogError
from varda.catalogs.http import fetchBytes
from varda.utilities.resources import resource_path

Bounds = tuple[float, float, float, float]  # west, south, east, north (degrees)

TREK = "https://trek.nasa.gov/tiles"
TILE_MATRIX_SET = "default028mm"
TILE_PX = 256
TREK_CREDIT = "NASA Solar System Treks"
BUNDLED_FOLDER = "resources/basemaps"
MAX_TILES = 48  # per view; a coarser zoom is used beyond this
FETCH_THREADS = 6
CACHE_CAP_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class BasemapLayer:
    """A Trek EQ tile layer and its bundled low-resolution copy."""

    layerId: str  # Trek's layer identifier
    body: str
    title: str
    credit: str  # who made the data, for the map and the docs
    maxZoom: int  # the deepest TileMatrix in the capabilities
    extension: str  # "jpg" or "png", as the capabilities' ResourceURL says
    bundledZoom: int  # the zoom the bundled image was made from
    # Degrees the tiles' longitudes are east of the IAU's. Trek's Titan
    # tiles are centered on 180° though their capabilities say -180°: such a
    # layer's bundled copy is shifted back, and its tiles aren't streamed.
    lonOffset: float = 0.0

    @property
    def streams(self) -> bool:
        return self.lonOffset == 0 and self.maxZoom > self.bundledZoom

    @property
    def baseUrl(self) -> str:
        return f"{TREK}/{self.body}/EQ/{self.layerId}/1.0.0"

    @property
    def capabilitiesUrl(self) -> str:
        return f"{self.baseUrl}/WMTSCapabilities.xml"

    def tileUrl(self, tile: Tile) -> str:
        """The capabilities' RESTful template, {Style}/{TileMatrixSet}/
        {TileMatrix}/{TileRow}/{TileCol}, filled in."""
        return (
            f"{self.baseUrl}/default/{TILE_MATRIX_SET}/"
            f"{tile.zoom}/{tile.row}/{tile.col}.{self.extension}"
        )

    @property
    def bundledPath(self) -> Path:
        return Path(resource_path(f"{BUNDLED_FOLDER}/{self.layerId}.jpg"))

    @property
    def fullCredit(self) -> str:
        return f"{self.credit}; via {TREK_CREDIT}"


LAYERS: tuple[BasemapLayer, ...] = (
    BasemapLayer(
        "Mercury_MESSENGER_MDIS_Basemap_BDR_Mosaic_Global_166m",
        "Mercury",
        "MESSENGER MDIS global mosaic",
        "NASA/JHUAPL/Carnegie Institution of Washington (MESSENGER MDIS)",
        7,
        "jpg",
        2,
    ),
    BasemapLayer(
        "Venus_Magellan_C3-MDIR_Global_Mosaic_2025m",
        "Venus",
        "Magellan radar global mosaic",
        "NASA/JPL (Magellan SAR); USGS",
        5,
        "png",
        1,
    ),
    BasemapLayer(
        "LRO_WAC_Mosaic_Global_303ppd_v02",
        "Moon",
        "LRO WAC global mosaic",
        "NASA/GSFC/Arizona State University (LRO LROC WAC)",
        8,
        "jpg",
        2,
    ),
    BasemapLayer(
        "Mars_MGS_MOLA_ClrShade_merge_global_463m",
        "Mars",
        "MOLA color shaded relief",
        "NASA/JPL/GSFC (MGS MOLA); USGS",
        7,
        "jpg",
        2,
    ),
    BasemapLayer(
        "Mars_Viking_MDIM21_ClrMosaic_global_232m",
        "Mars",
        "Viking MDIM 2.1 color mosaic",
        "NASA/JPL (Viking Orbiter); USGS",
        7,
        "jpg",
        1,
    ),
    BasemapLayer(
        "Phobos_Viking_Mosaic_40ppd_DLRcontrol",
        "Phobos",
        "Viking global mosaic",
        "NASA/JPL (Viking Orbiter); DLR",
        5,
        "jpg",
        1,
    ),
    BasemapLayer(
        "Ceres_Dawn_FC_DLR_global_59ppd_Feb2016",
        "Ceres",
        "Dawn FC global mosaic",
        "NASA/JPL-Caltech/UCLA/MPS/DLR/IDA (Dawn FC)",
        5,
        "jpg",
        1,
    ),
    BasemapLayer(
        "Vesta_Dawn_HAMO_Photo_DLR_global_74ppd_IAU",
        "Vesta",
        "Dawn FC HAMO global mosaic",
        "NASA/JPL-Caltech/UCLA/MPS/DLR/IDA (Dawn FC)",
        6,
        "png",
        1,
    ),
    BasemapLayer(
        "Io_GalileoSSI_Voyager_Global_Mosaic_ClrMerge_1km",
        "Io",
        "Voyager and Galileo color mosaic",
        "NASA/JPL (Voyager ISS, Galileo SSI); USGS",
        4,
        "png",
        1,
    ),
    BasemapLayer(
        "Europa_Voyager_GalileoSSI_global_mosaic_500m",
        "Europa",
        "Voyager and Galileo global mosaic",
        "NASA/JPL (Voyager ISS, Galileo SSI); USGS",
        5,
        "png",
        1,
    ),
    BasemapLayer(
        "Ganymede_Voyager_GalileoSSI_global_mosaic_1km",
        "Ganymede",
        "Voyager and Galileo global mosaic",
        "NASA/JPL (Voyager ISS, Galileo SSI); USGS",
        5,
        "png",
        1,
    ),
    BasemapLayer(
        "Callisto_Voyager_GalileoSSI_global_mosaic_1km",
        "Callisto",
        "Voyager and Galileo global mosaic",
        "NASA/JPL (Voyager ISS, Galileo SSI); USGS",
        5,
        "png",
        1,
    ),
    BasemapLayer(
        "Enceladus_Cassini_mosaic_global_110m",
        "Enceladus",
        "Cassini ISS global mosaic",
        "NASA/JPL/Space Science Institute (Cassini ISS)",
        5,
        "jpg",
        1,
    ),
    BasemapLayer(
        "Titan_ISS_P19658_Mosaic_Global_4km",
        "Titan",
        "Cassini ISS global mosaic",
        "NASA/JPL/Space Science Institute (Cassini ISS)",
        3,
        "png",
        2,
        lonOffset=180.0,
    ),
)


def layersFor(body: str) -> list[BasemapLayer]:
    """The body's imagery layers, the default first (none for Earth)."""
    body = bodyName(body) if body else ""
    return [layer for layer in LAYERS if layer.body == body]


def layerById(layerId: str) -> BasemapLayer | None:
    return next((layer for layer in LAYERS if layer.layerId == layerId), None)


# --- tiles ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tile:
    zoom: int
    row: int  # from the north
    col: int  # from -180° east

    @property
    def bounds(self) -> Bounds:
        size = tileDegrees(self.zoom)
        west = -180.0 + self.col * size
        north = 90.0 - self.row * size
        return (west, north - size, west + size, north)


def tileDegrees(zoom: int) -> float:
    return 180.0 / 2**zoom


def zoomFor(viewWidthDeg: float, pixelsWide: float, maxZoom: int) -> int:
    """The coarsest zoom whose pixels are no larger than the screen's (in
    degrees of longitude), within the layer's zooms."""
    if viewWidthDeg <= 0 or pixelsWide <= 0:
        return 0
    degreesPerPixel = viewWidthDeg / pixelsWide
    zoom = math.ceil(math.log2(180.0 / (TILE_PX * degreesPerPixel)) - 1e-9)
    return min(max(zoom, 0), maxZoom)


def tilesFor(view: Bounds, zoom: int) -> list[Tile]:
    """The tiles at ``zoom`` that cover a longitude-latitude view (clipped
    to the globe), north to south and west to east."""
    west, south, east, north = view
    west, east = max(west, -180.0), min(east, 180.0)
    south, north = max(south, -90.0), min(north, 90.0)
    if west >= east or south >= north:
        return []
    size = tileDegrees(zoom)
    cols, rows = 2 ** (zoom + 1), 2**zoom
    col0 = min(int((west + 180.0) // size), cols - 1)
    col1 = min(math.ceil((east + 180.0) / size) - 1, cols - 1)
    row0 = min(int((90.0 - north) // size), rows - 1)
    row1 = min(math.ceil((90.0 - south) / size) - 1, rows - 1)
    return [
        Tile(zoom, row, col)
        for row in range(row0, row1 + 1)
        for col in range(col0, col1 + 1)
    ]


def tilesToStream(
    layer: BasemapLayer, view: Bounds, pixelsWide: float
) -> tuple[int, list[Tile]]:
    """The zoom and tiles to fetch for a view, or no tiles when the bundled
    image is already as sharp (or the layer isn't streamed). A view needing
    more than MAX_TILES gets a coarser zoom."""
    if not layer.streams:
        return layer.bundledZoom, []
    west, _, east, _ = view
    zoom = zoomFor(min(east, 180.0) - max(west, -180.0), pixelsWide, layer.maxZoom)
    while zoom > layer.bundledZoom:
        tiles = tilesFor(view, zoom)
        if len(tiles) <= MAX_TILES:
            return zoom, tiles
        zoom -= 1
    return zoom, []


def decodeImage(data: bytes) -> np.ndarray:
    """A JPEG or PNG as RGBA, rows north to south."""
    with Image.open(io.BytesIO(data)) as image:
        return np.asarray(image.convert("RGBA"))


def assemble(tiles: Mapping[Tile, np.ndarray | None]) -> tuple[np.ndarray, Bounds]:
    """Tiles of one zoom as one RGBA image (rows north to south) and its
    longitude-latitude bounds. A missing tile is transparent, so whatever
    is under it (the bundled image) shows through."""
    if not tiles:
        raise ValueError("No tiles to assemble.")
    zoom = next(iter(tiles)).zoom
    rows = [t.row for t in tiles]
    cols = [t.col for t in tiles]
    row0, col0 = min(rows), min(cols)
    height = (max(rows) - row0 + 1) * TILE_PX
    width = (max(cols) - col0 + 1) * TILE_PX
    out = np.zeros((height, width, 4), np.uint8)
    for tile, pixels in tiles.items():
        if pixels is None:
            continue
        y, x = (tile.row - row0) * TILE_PX, (tile.col - col0) * TILE_PX
        h, w = min(pixels.shape[0], TILE_PX), min(pixels.shape[1], TILE_PX)
        out[y : y + h, x : x + w] = pixels[:h, :w]
    size = tileDegrees(zoom)
    west = -180.0 + col0 * size
    north = 90.0 - row0 * size
    bounds = (
        west,
        north - height / TILE_PX * size,
        west + width / TILE_PX * size,
        north,
    )
    return out, bounds


# --- the cache ----------------------------------------------------------------------


class TileCache:
    """Tiles on disk, ``<folder>/<body>/<layer>/<zoom>/<row>/<col>.<ext>``.
    When the folder grows past ``capBytes``, the least recently used tiles
    (by modification time, renewed on every hit) are deleted until it is
    under 90% of the cap."""

    def __init__(self, folder: Path, capBytes: int = CACHE_CAP_BYTES):
        self.folder = Path(folder)
        self.capBytes = capBytes
        self._total: int | None = None  # bytes, counted on first use

    def path(self, layer: BasemapLayer, tile: Tile) -> Path:
        return (
            self.folder
            / layer.body
            / layer.layerId
            / str(tile.zoom)
            / str(tile.row)
            / f"{tile.col}.{layer.extension}"
        )

    def get(self, layer: BasemapLayer, tile: Tile) -> bytes | None:
        path = self.path(layer, tile)
        try:
            data = path.read_bytes()
            os.utime(path)  # recently used
        except OSError:
            return None
        return data

    def put(self, layer: BasemapLayer, tile: Tile, data: bytes) -> None:
        path = self.path(layer, tile)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(path.suffix + ".part")
            partial.write_bytes(data)
            partial.replace(path)
        except OSError:
            return  # a cache that can't be written is only slower
        self._total = (
            self.size() + len(data)
            if self._total is None
            else (self._total + len(data))
        )
        if self._total > self.capBytes:
            self.evict()

    def _files(self) -> list[Path]:
        if not self.folder.exists():
            return []
        return [p for p in self.folder.rglob("*") if p.is_file()]

    def size(self) -> int:
        if self._total is None:
            self._total = sum(p.stat().st_size for p in self._files())
        return self._total

    def evict(self) -> None:
        """Delete the least recently used tiles until under 90% of the cap."""
        files = sorted(
            ((p.stat().st_mtime, p.stat().st_size, p) for p in self._files()),
            key=lambda entry: entry[0],
        )
        total = sum(size for _, size, _ in files)
        target = int(self.capBytes * 0.9)
        for _, size, path in files:
            if total <= target:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                pass
        self._total = total


Fetch = Callable[[str], bytes]


def fetchTile(
    layer: BasemapLayer, tile: Tile, cache: TileCache, fetch: Fetch = fetchBytes
) -> bytes:
    """A tile from the cache, or from Trek (and then cached). Raises
    CatalogError when it can't be fetched."""
    data = cache.get(layer, tile)
    if data is None:
        data = fetch(layer.tileUrl(tile))
        if not data:
            raise CatalogError(f"{layer.title}: an empty tile.")
        cache.put(layer, tile, data)
    return data


@dataclass(frozen=True)
class StreamedImagery:
    rgba: np.ndarray  # rows north to south
    bounds: Bounds
    failed: int  # tiles that couldn't be fetched or read


def streamTiles(
    layer: BasemapLayer,
    tiles: list[Tile],
    cache: TileCache,
    fetch: Fetch | None = None,
) -> StreamedImagery:
    """Fetch (or read from the cache) and assemble the tiles of one view, a
    few at a time. Tiles that fail are left transparent and counted.
    ``fetch`` defaults to ``fetchBytes`` (looked up when called)."""
    get = fetch or fetchBytes

    def one(tile: Tile) -> np.ndarray | None:
        try:
            return decodeImage(fetchTile(layer, tile, cache, get))
        except (CatalogError, OSError, ValueError):
            return None

    with ThreadPoolExecutor(FETCH_THREADS) as pool:
        pixels = dict(zip(tiles, pool.map(one, tiles)))
    rgba, bounds = assemble(pixels)
    return StreamedImagery(rgba, bounds, sum(p is None for p in pixels.values()))


def bundledImage(layer: BasemapLayer) -> np.ndarray:
    """The layer's bundled global image as RGBA, rows north to south,
    covering -180° to 180° east and 90° to -90°."""
    return decodeImage(layer.bundledPath.read_bytes())


GLOBE: Bounds = (-180.0, -90.0, 180.0, 90.0)
