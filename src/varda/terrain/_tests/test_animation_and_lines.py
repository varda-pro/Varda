"""Flyover animations from frames, and GIS lines placed on a DEM."""

import geopandas as gpd
import numpy as np
import pytest
import shapely
from PIL import Image
from pyproj import CRS, Transformer

from varda.terrain.animation import writeAnimation
from varda.geo._tests.fixtures import CTX_DEM_PROJ
from varda.geo.transform import transformPoints
from varda.terrain.vectors import placeLines, readLines, toDemCoordinates

MARS_EQC = CRS.from_user_input("+proj=eqc +R=3389500 +units=m +no_defs")


@pytest.mark.parametrize("suffix", [".gif", ".webp"])
def test_frames_become_a_looping_animation(tmp_path, suffix):
    frames = []
    for i in range(6):
        path = tmp_path / f"frame{i:03}.png"
        Image.fromarray(np.full((40, 60, 3), i * 40, np.uint8)).save(path)
        frames.append(path)
    target = writeAnimation(frames, tmp_path / f"orbit{suffix}", fps=10)
    with Image.open(target) as animation:
        assert animation.n_frames == 6 and animation.size == (60, 40)


@pytest.mark.parametrize("suffix", [".gpkg", ".geojson"])
def test_lines_are_placed_as_fractions_of_the_dem(tmp_path, suffix):
    # a GeoPackage keeps the Mars projection; GeoJSON loses it (and says
    # WGS 84), so its coordinates are taken as the DEM's
    frame = gpd.GeoDataFrame(
        geometry=[
            shapely.LineString([(2500, 2500), (7500, 5000)]),
            shapely.box(-500, -500, -100, -100),
        ],
        crs=MARS_EQC,
    )
    frame.to_file(tmp_path / f"traverse{suffix}")
    line = readLines(
        tmp_path / f"traverse{suffix}", MARS_EQC, (0, 0, 10000, 10000), "#f00"
    )
    assert line.name == "traverse" and len(line.paths) == 1  # the box is off the DEM
    np.testing.assert_allclose(line.paths[0], [(0.25, 0.25), (0.75, 0.5)])


def test_planetary_geojson_degrees_are_taken_on_the_dems_body(tmp_path):
    # GeoJSON always says CRS84; on Mars these are Mars degrees
    frame = gpd.GeoDataFrame(
        geometry=[shapely.LineString([(0.0, 0.0), (0.01, 0.0)])], crs="OGC:CRS84"
    )
    frame.to_file(tmp_path / "mars.geojson")
    perDegree = np.pi * 3389500 / 180
    line = readLines(
        tmp_path / "mars.geojson", MARS_EQC, (-1000, -1000, 1000, 1000), "#0f0"
    )
    start, end = line.paths[0]
    np.testing.assert_allclose(start, (0.5, 0.5))
    assert end[0] == pytest.approx(0.5 + 0.01 * perDegree / 2000, rel=1e-3)


def test_degrees_on_a_ctx_dem_are_the_iau_2015_mars_degrees():
    # GeoJSON's CRS84 degrees on a Mars DEM land where IAU_2015:49900's do
    points = [(77.5, 18.4), (77.6, 18.45), (77.45, 18.3)]
    frame = gpd.GeoDataFrame(geometry=[shapely.LineString(points)], crs="OGC:CRS84")
    dem = CRS.from_user_input(CTX_DEM_PROJ)
    (placed,) = toDemCoordinates(frame, dem)
    expected = Transformer.from_crs("IAU_2015:49900", dem, always_xy=True)
    x, y = expected.transform(*np.array(points).T)
    pixel = 20.0  # m
    np.testing.assert_allclose(placed, np.column_stack([x, y]), atol=0.5 * pixel)


def test_lines_that_miss_the_dem_are_an_error_for_the_user(tmp_path):
    frame = gpd.GeoDataFrame(
        geometry=[shapely.LineString([(5000, 5000), (6000, 6000)])], crs=MARS_EQC
    )
    frame.to_file(tmp_path / "far.gpkg")
    with pytest.raises(ValueError, match="crosses the DEM"):
        readLines(tmp_path / "far.gpkg", MARS_EQC, (0, 0, 1000, 1000), "#f00")


def test_lines_in_memory_are_placed_the_same_way():
    frame = gpd.GeoDataFrame(
        geometry=[shapely.LineString([(2500, 2500), (7500, 5000)])], crs=MARS_EQC
    )
    line = placeLines(frame, MARS_EQC, (0, 0, 10000, 10000), "#f00", "Rim")
    assert line.name == "Rim" and line.color == "#f00"
    np.testing.assert_allclose(line.paths[0], [(0.25, 0.25), (0.75, 0.5)])


@pytest.mark.parametrize(
    "code, center",
    [
        ("IAU_2015:49911", (-77.5, 18.4)),  # westing; planetographic
        ("IAU_2015:49912", (77.5, 18.4)),  # planetocentric
    ],
)
def test_degrees_on_an_iau_ellipsoid_dem_are_east_longitude_first(code, center):
    """GeoJSON degrees (east, the DEM's own latitude type) on a DEM whose
    geographic axes are latitude first."""
    frame = gpd.GeoDataFrame(
        geometry=[shapely.LineString([(77.5, 18.4), (77.6, 18.4)])], crs="OGC:CRS84"
    )
    dem = CRS.from_user_input(code)
    (placed,) = toDemCoordinates(frame, dem)
    x, y = transformPoints(dem.geodetic_crs, dem, [center[0]], [center[1]])
    np.testing.assert_allclose(placed[0], [x[0], y[0]], atol=1e-6)


def test_lines_on_another_body_say_so(tmp_path):
    frame = gpd.GeoDataFrame(
        geometry=[shapely.LineString([(2500, 2500), (7500, 5000)])],
        crs=CRS.from_epsg(32612),
    )
    frame.to_file(tmp_path / "earth.gpkg")
    with pytest.raises(ValueError, match="different bodies"):
        readLines(tmp_path / "earth.gpkg", MARS_EQC, (0, 0, 10000, 10000), "#f00")
