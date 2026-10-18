"""The 3D page's data: ground size, the decimated, quantized height grid, and
a self-contained page."""

import base64
import json
import re

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS

from varda.geo.bodies import BODIES
from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.terrain.scene import (
    FILL_METHOD,
    TerrainSettings,
    buildTerrain,
    fillGaps,
    groundSize,
    mapUnit,
    pageHtml,
    pngDataUrl,
    quantize,
    trimToRectangle,
    verticalUnit,
)


def _dem(data, transform=None, crs=None, nodata=None) -> VardaRaster:
    data = np.asarray(data, np.float32)[..., None]
    if transform is None:
        return VardaRaster(ArrayDataSource(data, nodata=nodata))
    return VardaRaster(
        ArrayDataSource(
            data, transform=transform, crs=CRS.from_user_input(crs), nodata=nodata
        )
    )


def test_ground_size_in_meters_projected_geographic_or_pixels():
    projected = _dem(
        np.zeros((10, 20)), Affine(20, 0, 0, 0, -20, 0), "+proj=eqc +R=3389500 +units=m"
    )
    assert groundSize(projected) == pytest.approx((400, 200))
    geographic = _dem(
        np.zeros((10, 20)), Affine(0.01, 0, 0, 0, -0.01, 0), BODIES["Mars"].sphereCrs()
    )
    perDegree = np.pi * BODIES["Mars"].equatorialRadius / 180
    assert groundSize(geographic) == pytest.approx(
        (0.2 * perDegree, 0.1 * perDegree), rel=1e-4
    )
    assert groundSize(_dem(np.zeros((10, 20)))) == (20.0, 10.0)


def test_a_large_dem_is_averaged_down_and_no_data_becomes_nan():
    data = np.arange(100 * 60, dtype=float).reshape(100, 60)
    data[:4, :4] = -32767
    terrain = buildTerrain(_dem(data, nodata=-32767), maxSide=25)
    assert terrain.step == 4 and terrain.heights.shape == (25, 15)
    assert np.isnan(terrain.heights[0, 0])  # a block of only no data
    assert terrain.heights[1, 1] == pytest.approx(data[4:8, 4:8].mean())


def test_heights_quantize_to_16_bits_with_zero_for_no_data():
    heights = np.array([[-1500.0, np.nan], [-1000.0, -1250.0]])
    levels, low, step = quantize(heights)
    assert levels[0, 1] == 0 and levels[0, 0] == 1 and levels[1, 0] == 65535
    decoded = low + (levels.astype(float) - 1) * step
    assert decoded[1, 1] == pytest.approx(-1250, abs=step)


def test_the_page_is_self_contained_and_safe_to_inline():
    terrain = buildTerrain(_dem(np.random.default_rng(0).random((20, 30))))
    texture = pngDataUrl(np.zeros((20, 30, 4), np.uint8))
    html = pageHtml(
        terrain, texture, TerrainSettings(exaggeration=4), title="A </script> test"
    )
    assert "{{" not in html and "https://" not in html.split("Made with")[0]
    found = re.search(r'type="importmap">(.*?)</script>', html, re.S)
    assert found is not None
    importmap = json.loads(found.group(1))
    three = importmap["imports"]["three"]
    assert three.startswith("data:text/javascript;base64,")
    assert b"Three.js Authors" in base64.b64decode(three.split(",", 1)[1])[:200]
    found = re.search(r"window.VARDA_SCENE = (.*?);</script>", html, re.S)
    assert found is not None
    data = json.loads(found.group(1).replace("<\\/", "</"))
    assert (data["cols"], data["rows"]) == (30, 20) and data["settings"][
        "exaggeration"
    ] == 4
    assert data["texture"].startswith("data:image/png;base64,")
    assert 'id="panel"' not in html and "&lt;/script&gt;" in html
    assert 'id="panel"' in pageHtml(terrain, None, TerrainSettings(), panel=True)


def test_settings_ignore_keys_they_do_not_know():
    settings = TerrainSettings.fromDict({"exaggeration": 2.5, "future": 1})
    assert settings.exaggeration == 2.5 and settings.toDict()["sunAzimuth"] == 315


def test_gaps_inside_the_data_are_filled_but_the_outside_stays_empty():
    yy, xx = np.mgrid[0:30, 0:40]
    heights = (xx * 2.0).astype(np.float32)
    heights[10:14, 15:20] = np.nan  # a void on a slope
    heights[:, :3] = np.nan  # no data around the outside
    filled, mask = fillGaps(heights)
    assert mask.sum() == 20 and mask[10:14, 15:20].all()
    assert np.isfinite(filled[10:14, 15:20]).all() and np.isnan(filled[:, :3]).all()
    assert filled[12, 17] == pytest.approx(34, abs=3)  # follows the slope


def test_trimming_keeps_the_largest_rectangle_with_data_everywhere():
    heights = np.full((20, 30), np.nan, np.float32)
    heights[3:15, 4:25] = 1.0  # the main block, 12 × 21
    heights[2, 10:12] = 1.0  # ragged bits off its edge
    heights[15, 4:9] = 1.0
    heights[17:20, 0:3] = 1.0  # a small island elsewhere
    trimmed = trimToRectangle(heights)
    rows, cols = np.nonzero(np.isfinite(trimmed))
    assert (rows.min(), rows.max(), cols.min(), cols.max()) == (3, 14, 4, 24)
    assert np.isfinite(trimmed[3:15, 4:25]).all()
    assert np.isnan(trimToRectangle(np.full((4, 4), np.nan))).all()


def test_a_trimmed_terrain_has_straight_edges():
    heights = np.ones((20, 20), np.float32)
    heights[:5, :5] = np.nan  # a corner missing
    terrain = buildTerrain(_dem(heights), fill=False, trim=True)
    finite = np.isfinite(terrain.heights)
    rows, cols = np.nonzero(finite)
    block = finite[rows.min() : rows.max() + 1, cols.min() : cols.max() + 1]
    assert block.all() and block.size == 15 * 20


def test_edges_are_smooth_unless_chosen_otherwise():
    assert TerrainSettings().edges == "smooth"
    assert TerrainSettings.fromDict({}).edges == "smooth"


def _sceneData(html: str) -> dict:
    found = re.search(r"window.VARDA_SCENE = (.*?);</script>", html, re.S)
    assert found is not None
    return json.loads(found.group(1).replace("<\\/", "</"))


def test_a_terrain_knows_where_its_points_stand():
    # 10 × 6 pixels averaged 4 at a time: blocks of 4, 4 and 2 across
    terrain = buildTerrain(_dem(np.zeros((6, 10))), maxSide=3)
    assert terrain.step == 4 and terrain.heights.shape == (2, 3)
    assert terrain.columns == pytest.approx([0.2, 0.6, 0.9])
    assert terrain.rows == pytest.approx([2 / 6, 5 / 6])
    data = _sceneData(pageHtml(terrain, None, TerrainSettings()))
    assert data["columnAt"] == pytest.approx([0.2, 0.6, 0.9])
    assert data["rowAt"] == pytest.approx([2 / 6, 5 / 6])


def test_height_units_default_to_meters_and_are_kept():
    assert TerrainSettings().heightUnit == "m"
    assert TerrainSettings.fromDict({"heightUnit": "km"}).heightUnit == "km"
    assert TerrainSettings(heightUnit="ft").metersPerHeightUnit == pytest.approx(0.3048)
    assert TerrainSettings(heightUnit="km").metersPerHeightUnit == 1000.0


@pytest.mark.parametrize(
    ("crs", "unit"),
    [
        ("EPSG:32612+5703", "m"),  # UTM with NAVD88 heights
        ("EPSG:2229+6360", "ft"),  # California state plane, NAVD88 in US feet
        ("EPSG:4979", "m"),  # WGS 84 with ellipsoidal heights
        ("EPSG:32612", None),  # no vertical part: nothing declared
    ],
)
def test_the_vertical_unit_a_crs_declares(crs, unit):
    assert verticalUnit(CRS.from_user_input(crs)) == unit


@pytest.mark.parametrize(
    ("crs", "unit"),
    [
        ("EPSG:32612", "m"),
        ("EPSG:4326", "°"),
        ("EPSG:2229", "US ft"),
        ("+proj=eqc +R=3389500 +units=km", "km"),
    ],
)
def test_map_units_are_labeled(crs, unit):
    assert mapUnit(CRS.from_user_input(crs)) == unit


def _gappy() -> np.ndarray:
    heights = np.ones((20, 30), np.float32)
    heights[8:11, 10:14] = np.nan  # a void inside
    heights[:, :2] = np.nan  # no data outside
    return heights


def test_a_terrain_knows_which_points_were_filled():
    terrain = buildTerrain(_dem(_gappy()))
    assert terrain.filled == 12
    assert terrain.filledMask is not None and terrain.filledMask.sum() == 12
    assert terrain.filledMask[8:11, 10:14].all()
    unfilled = buildTerrain(_dem(_gappy()), fill=False)
    assert unfilled.filled == 0
    assert unfilled.filledMask is not None and not unfilled.filledMask.any()
    # trimming drops filled points outside the rectangle from the mask
    trimmed = buildTerrain(_dem(_gappy()), trim=True)
    assert trimmed.filledMask is not None
    assert not (trimmed.filledMask & ~np.isfinite(trimmed.heights)).any()


def test_the_page_carries_the_filled_points():
    terrain = buildTerrain(_dem(_gappy()))
    data = _sceneData(pageHtml(terrain, None, TerrainSettings()))
    filled = data["filled"]
    assert filled["count"] == 12 and filled["method"] == FILL_METHOD
    bits = np.frombuffer(base64.b64decode(filled["mask"]), np.uint8)
    mask = np.unpackbits(bits)[: terrain.heights.size].reshape(terrain.heights.shape)
    assert terrain.filledMask is not None
    assert np.array_equal(mask.astype(bool), terrain.filledMask)
    clean = buildTerrain(_dem(np.ones((5, 5))))
    assert (
        _sceneData(pageHtml(clean, None, TerrainSettings()))["filled"]["mask"] is None
    )


def test_filled_points_are_hidden_unless_asked_and_the_page_says_so():
    assert TerrainSettings().showFilled is False
    terrain = buildTerrain(_dem(_gappy()))
    page = pageHtml(terrain, None, TerrainSettings(), panel=True)
    assert 'data-setting="showFilled"' in page and "12 points" in page
    clean = pageHtml(
        buildTerrain(_dem(np.ones((5, 5)))), None, TerrainSettings(), panel=True
    )
    assert 'data-setting="showFilled"' not in clean
