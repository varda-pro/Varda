"""Global imagery: Trek tile math, the tile cache, streaming with a stand-in
fetcher, and the bundled images; plus a live check (VARDA_LIVE_TESTS=1)
that every layer's capabilities and a tile load."""

import io
import os
import time
import urllib.request

import numpy as np
import pytest
from PIL import Image

from varda.catalogs import basemaps
from varda.catalogs.basemaps import (
    LAYERS,
    MAX_TILES,
    Tile,
    TileCache,
    assemble,
    bundledImage,
    loadTile,
    layerById,
    layersFor,
    streamTiles,
    tileDegrees,
    tilesFor,
    tilesToStream,
    zoomFor,
)
from varda.catalogs.catalog import CatalogError

LIVE = os.environ.get("VARDA_LIVE_TESTS") == "1"
MOLA = layersFor("Mars")[0]


def png(color: tuple[int, int, int], size: int = 256) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buffer, "PNG")
    return buffer.getvalue()


# --- tile math ---------------------------------------------------------------------


def test_zoom_zero_is_two_tiles_of_180_degrees():
    assert tileDegrees(0) == 180.0
    assert tilesFor((-180, -90, 180, 90), 0) == [Tile(0, 0, 0), Tile(0, 0, 1)]
    assert Tile(0, 0, 0).bounds == (-180.0, -90.0, 0.0, 90.0)
    assert Tile(0, 0, 1).bounds == (0.0, -90.0, 180.0, 90.0)


def test_tile_bounds_count_rows_from_the_north_and_columns_from_180_west():
    # zoom 3: 22.5° tiles, 16 × 8
    assert Tile(3, 0, 0).bounds == (-180.0, 67.5, -157.5, 90.0)
    assert Tile(3, 7, 15).bounds == (157.5, -90.0, 180.0, -67.5)
    assert len(tilesFor((-180, -90, 180, 90), 3)) == 16 * 8


def test_tiles_covering_gale_crater():
    # Gale, about 5.4°S 137.8°E, at zoom 5 (5.625° tiles)
    tiles = tilesFor((136.0, -7.0, 139.5, -4.0), 5)
    # rows 16 (0° to -5.625°) and 17 (to -11.25°); column 56 is 135° to 140.625°
    assert {t.row for t in tiles} == {16, 17}
    assert {t.col for t in tiles} == {56}
    for t in tiles:
        west, south, east, north = t.bounds
        assert west < 139.5 and east > 136.0 and south < -4.0 and north > -7.0


def test_a_view_on_a_tile_edge_takes_only_the_tiles_it_covers():
    assert tilesFor((0.0, 0.0, 22.5, 22.5), 3) == [Tile(3, 3, 8)]


def test_views_off_the_globe_are_clipped():
    assert tilesFor((170, 80, 200, 100), 1) == [Tile(1, 0, 3)]
    assert tilesFor((190, 0, 200, 10), 1) == []


def test_zoom_matches_screen_pixels():
    # the whole globe across 512 px is exactly zoom 0 (2 × 256 px)
    assert zoomFor(360, 512, 7) == 0
    assert zoomFor(360, 513, 7) == 1
    # 10° across 1000 px: 0.01°/px; zoom 7 tiles are 0.0055°/px
    assert zoomFor(10, 1000, 7) == 7
    assert zoomFor(10, 1000, 5) == 5  # the layer's deepest
    assert zoomFor(0, 1000, 7) == 0


def test_no_tiles_when_the_bundled_image_is_as_sharp():
    zoom, tiles = tilesToStream(MOLA, (-180, -90, 180, 90), 1500)
    assert tiles == [] and zoom <= MOLA.bundledZoom


def test_tiles_only_for_the_view_and_a_capped_count():
    # 15° across 1200 px is 0.0125°/px; zoom 6 tiles are 0.011°/px
    zoom, tiles = tilesToStream(MOLA, (130, -10, 145, 0), 1200)
    assert zoom == 6
    assert 0 < len(tiles) <= MAX_TILES
    # a wide, sharp view falls back to a coarser zoom
    zoom, tiles = tilesToStream(MOLA, (0, -40, 120, 40), 6000)
    assert len(tiles) <= MAX_TILES and zoom < 7


def test_titan_is_bundled_only():
    titan = layersFor("Titan")[0]
    assert titan.lonOffset == 180 and not titan.streams
    assert tilesToStream(titan, (0, 0, 10, 10), 2000)[1] == []


def test_tile_urls_follow_the_capabilities_template():
    assert MOLA.capabilitiesUrl == (
        "https://trek.nasa.gov/tiles/Mars/EQ/"
        "Mars_MGS_MOLA_ClrShade_merge_global_463m/1.0.0/WMTSCapabilities.xml"
    )
    assert MOLA.tileUrl(Tile(3, 2, 5)) == (
        "https://trek.nasa.gov/tiles/Mars/EQ/"
        "Mars_MGS_MOLA_ClrShade_merge_global_463m/1.0.0/default/default028mm/3/2/5.jpg"
    )


def test_layers_per_body():
    assert layerById(MOLA.layerId) is MOLA and layerById("nope") is None
    assert [layer.title for layer in layersFor("mars")] == [
        "MOLA color shaded relief",
        "Viking MDIM 2.1 color mosaic",
    ]
    assert layersFor("Earth") == [] and layersFor("") == []
    assert all(layer.credit for layer in LAYERS)


def test_assemble_places_tiles_and_leaves_missing_ones_transparent():
    red = np.full((256, 256, 4), (255, 0, 0, 255), np.uint8)
    rgba, bounds = assemble({Tile(2, 1, 3): red, Tile(2, 1, 4): None})
    assert rgba.shape == (256, 512, 4)
    assert bounds == (-45.0, 0.0, 45.0, 45.0)
    assert (rgba[:, :256, 0] == 255).all() and (rgba[:, 256:, 3] == 0).all()


# --- the cache ---------------------------------------------------------------------


def test_cache_hit_and_miss(tmp_path):
    cache = TileCache(tmp_path)
    tile = Tile(4, 3, 9)
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return png((1, 2, 3))

    assert cache.get(MOLA, tile) is None
    assert loadTile(MOLA, tile, cache, fetch)[0, 0, 2] == 3
    assert loadTile(MOLA, tile, cache, fetch)[0, 0, 2] == 3
    assert calls == [MOLA.tileUrl(tile)]  # the second came from disk
    assert cache.path(MOLA, tile) == (
        tmp_path / "Mars" / MOLA.layerId / "4" / "3" / "9.jpg"
    )


def test_cache_evicts_the_least_recently_used(tmp_path):
    cache = TileCache(tmp_path, capBytes=1000)
    tiles = [Tile(5, 0, col) for col in range(4)]
    for i, tile in enumerate(tiles[:3]):
        cache.put(MOLA, tile, b"x" * 300)
        os.utime(cache.path(MOLA, tile), (time.time() - 100 + i, time.time() - 100 + i))
    cache.get(MOLA, tiles[0])  # used again: now the newest
    cache.put(MOLA, tiles[3], b"x" * 300)  # 1200 B > 1000: evict to ≤ 900
    assert cache.get(MOLA, tiles[1]) is None  # the oldest went
    assert cache.get(MOLA, tiles[0]) is not None
    assert cache.get(MOLA, tiles[3]) is not None
    assert cache.size() <= 900


def test_a_failed_fetch_is_not_cached(tmp_path):
    cache = TileCache(tmp_path)

    def offline(url: str) -> bytes:
        raise CatalogError("trek.nasa.gov couldn't be reached.")

    with pytest.raises(CatalogError):
        loadTile(MOLA, Tile(1, 0, 0), cache, offline)
    assert cache.get(MOLA, Tile(1, 0, 0)) is None


def test_a_reply_that_isnt_an_image_is_not_cached(tmp_path):
    cache = TileCache(tmp_path)
    portal = b"<html>Log in to the hotel Wi-Fi</html>"
    with pytest.raises(ValueError):
        loadTile(MOLA, Tile(1, 0, 0), cache, lambda url: portal)
    assert cache.get(MOLA, Tile(1, 0, 0)) is None


def test_a_cached_tile_that_doesnt_decode_is_deleted_and_fetched_again(tmp_path):
    cache = TileCache(tmp_path)
    tile = Tile(2, 1, 1)
    cache.put(MOLA, tile, b"not an image")  # e.g. cached by an older Varda
    pixels = loadTile(MOLA, tile, cache, lambda url: png((9, 9, 9)))
    assert pixels[0, 0, 0] == 9
    assert cache.get(MOLA, tile) == png((9, 9, 9))
    # offline, a bad cached tile is dropped rather than kept forever
    cache.put(MOLA, tile, b"broken again")

    def offline(url: str) -> bytes:
        raise CatalogError("offline")

    with pytest.raises(CatalogError):
        loadTile(MOLA, tile, cache, offline)
    assert cache.get(MOLA, tile) is None


def test_tiles_use_a_short_timeout(monkeypatch):
    seen: list[float] = []

    def fetchBytes(
        url: str, headers=None, limit: int = 0, timeout: float = 60
    ) -> bytes:
        seen.append(timeout)
        return b""

    monkeypatch.setattr(basemaps, "fetchBytes", fetchBytes)
    basemaps.fetchTileBytes("https://trek.nasa.gov/x.jpg")
    assert seen == [basemaps.TILE_TIMEOUT_S] and basemaps.TILE_TIMEOUT_S <= 10


# --- streaming and offline fallback --------------------------------------------------


def test_stream_assembles_fetched_tiles(tmp_path):
    tiles = tilesFor((0.0, 0.0, 44.0, 22.0), 3)
    rgba = streamTiles(MOLA, tiles, TileCache(tmp_path), lambda url: png((0, 200, 0)))
    assert rgba.failed == 0
    assert rgba.bounds == (0.0, 0.0, 45.0, 22.5)
    assert (rgba.rgba[..., 1] == 200).all() and (rgba.rgba[..., 3] == 255).all()


def test_a_superseded_stream_skips_the_tiles_it_hasnt_fetched(tmp_path):
    tiles = tilesFor((0.0, 0.0, 90.0, 45.0), 4)  # 32 tiles
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return png((0, 0, 0))

    # canceled after the first few tiles: the rest are never requested
    streamTiles(
        MOLA, tiles, TileCache(tmp_path), fetch, cancelled=lambda: len(fetched) >= 3
    )
    assert len(fetched) < len(tiles)
    assert len(fetched) <= 3 + basemaps.FETCH_THREADS


def test_offline_tiles_are_transparent_over_the_bundled_image(tmp_path):
    def offline(url: str) -> bytes:
        raise CatalogError("trek.nasa.gov couldn't be reached.")

    tiles = tilesFor((0.0, 0.0, 44.0, 22.0), 3)
    streamed = streamTiles(MOLA, tiles, TileCache(tmp_path), offline)
    assert streamed.failed == len(tiles)
    assert (streamed.rgba[..., 3] == 0).all()  # the bundled image shows through


def test_cached_tiles_serve_offline(tmp_path):
    cache = TileCache(tmp_path)
    tiles = tilesFor((0.0, 0.0, 20.0, 20.0), 3)
    streamTiles(MOLA, tiles, cache, lambda url: png((10, 20, 30)))

    def offline(url: str) -> bytes:
        raise CatalogError("offline")

    assert streamTiles(MOLA, tiles, cache, offline).failed == 0


@pytest.mark.parametrize("layer", LAYERS, ids=lambda layer: layer.layerId)
def test_every_layer_has_a_bundled_global_image(layer):
    rgba = bundledImage(layer)
    height, width = rgba.shape[:2]
    assert width == 2 * height == 2 ** (layer.bundledZoom + 1) * 256
    assert rgba[..., :3].mean() > 20  # not blank


def test_bundled_images_stay_small():
    total = sum(layer.bundledPath.stat().st_size for layer in LAYERS)
    assert total < 3_000_000


# --- live -----------------------------------------------------------------------------


@pytest.mark.skipif(not LIVE, reason="set VARDA_LIVE_TESTS=1 to reach Trek")
@pytest.mark.parametrize("layer", LAYERS, ids=lambda layer: layer.layerId)
def test_live_capabilities_and_a_tile(layer):
    with urllib.request.urlopen(layer.capabilitiesUrl, timeout=60) as response:
        capabilities = response.read().decode()
    assert "default028mm" in capabilities
    assert f"<ows:Identifier>{layer.maxZoom}</ows:Identifier>" in capabilities
    assert "<TopLeftCorner>-180.0 90.0</TopLeftCorner>" in capabilities
    data = basemaps.fetchBytes(layer.tileUrl(Tile(0, 0, 0)))
    assert basemaps.decodeImage(data).shape == (256, 256, 4)
