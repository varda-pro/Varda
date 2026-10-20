"""Which browse images go on the map, and how they are drawn there."""

import numpy as np
import pytest
import shapely

from varda.workspaces.find_data.basemap import MapFrame
from varda.workspaces.find_data.preview_images import PictureCache
from varda.workspaces.find_data.previews import (
    PREVIEW_PIXELS,
    drape,
    footprintBox,
    isMapOfFootprint,
    overlap,
)
from varda.workspaces.find_data.projection import MapProjection, Projector

# a CTX-like strip near Jezero, tilted as an orbit track is
STRIP = shapely.Polygon(
    [(77.53, 18.65), (77.62, 17.94), (77.13, 17.88), (77.04, 18.59), (77.53, 18.65)]
)


def mapOf(footprint: shapely.Geometry, height: int, width: int) -> np.ndarray:
    """A north-up simple cylindrical browse image of the footprint's box:
    textured inside the footprint, black fill outside."""
    west, south, east, north = footprint.bounds
    lon = west + (np.arange(width) + 0.5) / width * (east - west)
    lat = north - (np.arange(height) + 0.5) / height * (north - south)
    inside = shapely.contains_xy(footprint, *np.meshgrid(lon, lat))
    texture = np.random.default_rng(0).integers(40, 220, (height, width))
    gray = np.where(inside, texture, 0).astype(np.uint8)
    return np.dstack([gray, gray, gray, np.full_like(gray, 255)])


def _boxShape(footprint: shapely.Geometry, height: int) -> tuple[int, int]:
    west, south, east, north = footprint.bounds
    return height, round(height * (east - west) / (north - south))


def test_a_map_of_the_footprint_goes_on_the_map():
    rgba = mapOf(STRIP, *_boxShape(STRIP, 400))
    box = footprintBox(STRIP)
    assert box is not None
    assert overlap(rgba, STRIP, box) > 0.97
    assert isMapOfFootprint(rgba, STRIP)


def test_an_equirectangular_map_at_the_scenes_latitude_goes_too():
    height, width = _boxShape(STRIP, 400)
    narrower = round(width * np.cos(np.radians(18.3)))
    assert isMapOfFootprint(mapOf(STRIP, height, narrower), STRIP)


def test_a_strip_in_sensor_geometry_does_not():
    # the strip's own rows and columns: no fill, and the wrong shape
    texture = np.random.default_rng(1).integers(40, 220, (500, 100)).astype(np.uint8)
    rgba = np.dstack([texture, texture, texture, np.full_like(texture, 255)])
    assert not isMapOfFootprint(rgba, STRIP)


def test_a_figure_around_the_map_does_not():
    # the map inset in a white page with room for plots, of the box's shape
    height, width = _boxShape(STRIP, 400)
    page = np.full((height, width, 4), 255, np.uint8)
    inset = mapOf(STRIP, height // 2, width // 2)
    corner = page[: inset.shape[0], width - inset.shape[1] :]
    corner[inset[..., 0] > 0] = inset[inset[..., 0] > 0]
    assert not isMapOfFootprint(page, STRIP)


@pytest.mark.parametrize(
    "footprint",
    [
        shapely.Point(),
        shapely.MultiPolygon(
            [
                shapely.box(179.0, 10.0, 180.0, 11.0),
                shapely.box(-180.0, 10.0, -179.0, 11.0),
            ]
        ),
        shapely.box(-10.0, 88.0, 10.0, 90.0),
    ],
    ids=["empty", "across 180", "at the pole"],
)
def test_a_box_cannot_place_some_footprints(footprint):
    assert footprintBox(footprint) is None
    assert not isMapOfFootprint(np.zeros((10, 10, 4), np.uint8), footprint)


def test_draped_it_is_clear_outside_the_footprint():
    rgba = mapOf(STRIP, *_boxShape(STRIP, 400))
    projector = Projector(MapProjection.EQUIRECTANGULAR, "Mars")
    out = drape(rgba, STRIP, MapFrame.of(projector), STRIP.bounds)
    assert out.shape == rgba.shape
    inside = out[..., 3] > 0
    assert np.array_equal(out[inside, :3], rgba[inside, :3])
    assert not out[~inside, 0].any()  # only the black fill is cleared
    assert inside.mean() == pytest.approx((rgba[..., 0] > 0).mean(), abs=0.01)


def test_a_large_image_is_drawn_smaller():
    height, width = _boxShape(STRIP, 3 * PREVIEW_PIXELS)
    rgba = mapOf(STRIP, height, width)
    projector = Projector(MapProjection.EQUIRECTANGULAR, "Mars")
    out = drape(rgba, STRIP, MapFrame.of(projector), STRIP.bounds)
    assert max(out.shape[:2]) <= PREVIEW_PIXELS


def test_in_a_polar_view_it_is_reprojected_onto_the_footprint():
    footprint = shapely.box(30.0, 70.0, 34.0, 72.0)
    rgba = mapOf(footprint, *_boxShape(footprint, 300))
    projector = Projector(MapProjection.NORTH_POLAR, "Mars")
    bounds = projector.mapBounds(footprint.bounds)
    assert bounds is not None
    out = drape(rgba, footprint, MapFrame.of(projector), bounds)
    # the footprint's center, projected, is drawn; the box's projected
    # corner, outside the curved footprint, is clear
    x0, y0, x1, y1 = bounds
    height, width = out.shape[:2]
    cx, cy = (float(v) for v in projector.forward(32.0, 71.0))
    row = int((y1 - cy) / (y1 - y0) * height)
    col = int((cx - x0) / (x1 - x0) * width)
    assert out[row, col, 3] == 255
    assert out[0, 0, 3] == 0


def test_the_cache_keeps_the_latest_within_its_limit():
    cache = PictureCache(limit=2 * 100)
    for url in ("a", "b", "c"):
        cache.put(url, np.zeros(100, np.uint8))
    assert cache.get("a") is None
    assert cache.get("b") is not None and cache.get("c") is not None
    cache.get("b")  # used: "c" is now the oldest
    cache.put("d", np.zeros(100, np.uint8))
    assert cache.get("c") is None and cache.get("b") is not None
