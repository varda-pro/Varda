"""Saved pixel points: where they are on the image and on the ground, and export."""

import geopandas as gpd
import numpy as np
import pytest
from affine import Affine
from pyproj import CRS

from varda.common.entities import Color, VardaRaster
from varda.geo._tests.fixtures import (
    CRISM_MTRDR_WKT,
    CTX_DEM_PROJ,
    WGS84,
    makeImage,
)
from varda.geo.equivalence import sameCrs
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.points.point_collection import PointCollection

RED = Color(1.0, 0.0, 0.0, 1.0)


def _georeferenced(name="geo") -> VardaRaster:
    source = ArrayDataSource(
        np.zeros((10, 10, 3)),
        transform=Affine(10.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0),
        crs=CRS.from_epsg(32612),
    )
    return VardaRaster(source, name=name)


def _plain(name="plain") -> VardaRaster:
    return VardaRaster(ArrayDataSource(np.zeros((10, 10, 3))), name=name)


def test_a_georeferenced_pixel_is_saved_at_its_ground_position():
    image = _georeferenced()
    points = PointCollection()

    fid = points.addPixel(image, 3, 4, name="rock", color=RED)

    (point,) = points.getAllPoints()
    assert point.fid == fid
    assert (point.name, point.image, point.x, point.y) == ("rock", "geo", 3, 4)
    assert point.color == RED
    assert (point.geometry.x, point.geometry.y) == image.pixelToGeo(3, 4)
    assert points.crs is not None and points.crs.to_epsg() == 32612


def test_a_plain_pixel_is_saved_at_its_pixel_centre():
    points = PointCollection()
    points.addPixel(_plain(), 3, 4, name="p", color=RED)

    (point,) = points.getAllPoints()
    assert (point.geometry.x, point.geometry.y) == (3.5, 4.5)
    assert points.crs is None


def test_points_are_named_automatically_and_can_be_removed():
    points = PointCollection()
    seen = []
    points.sigCollectionChanged.connect(lambda: seen.append("changed"))
    first = points.addPixel(_plain(), 1, 1, color=RED)
    second = points.addPixel(_plain(), 2, 2, color=RED)

    assert [p.name for p in points.getAllPoints()] == ["Point 1", "Point 2"]
    points.removePoint(first)

    assert points.fids == [second]
    assert len(points) == 1
    assert seen == ["changed"] * 3
    with pytest.raises(KeyError):
        points.getPoint(first)


def test_export_writes_a_gis_file_with_the_pixel_and_image_as_attributes(tmp_path):
    image = _georeferenced()
    points = PointCollection()
    points.addPixel(image, 3, 4, name="rock", color=RED, description="bland ✓")
    points.addPixel(image, 5, 6, name="soil", color=Color(0.0, 1.0, 0.0, 1.0))

    target = points.toFile(tmp_path / "points.geojson")

    frame = gpd.read_file(target)
    assert list(frame["name"]) == ["rock", "soil"]
    assert list(frame["pixel_x"]) == [3, 5]
    assert list(frame["pixel_y"]) == [4, 6]
    assert list(frame["image"]) == ["geo", "geo"]
    assert list(frame["description"]) == ["bland ✓", ""]
    assert frame["color"][0] == RED.toHexString()
    assert frame.crs is not None and frame.crs.to_epsg() == 32612
    assert frame.geometry[0].x == image.pixelToGeo(3, 4)[0]


def test_export_of_an_empty_collection_is_refused(tmp_path):
    with pytest.raises(ValueError, match="No points"):
        PointCollection().toFile(tmp_path / "points.geojson")


def test_points_from_two_mars_projections_share_one_crs(tmp_path):
    """The same ground spot is the same planetocentric lon/lat on each
    image's own body model (CTX's 3396.19 km sphere, CRISM's 3394.84 km
    one), as Varda places points: each image's center pixel (odd sizes) is
    centered on (77.5°E, 18°N)."""
    spot = (77.5, 18.0)
    ctx = makeImage(
        CTX_DEM_PROJ, centerLonLat=spot, pixelSize=20.0, width=65, height=65
    )
    crism = makeImage(
        CRISM_MTRDR_WKT, centerLonLat=spot, pixelSize=18.0, width=65, height=65
    )
    points = PointCollection()
    points.addPixel(ctx, 32, 32, name="ctx")
    points.addPixel(crism, 32, 32, name="crism")

    assert sameCrs(points.crs, ctx.crs)
    path = points.toFile(tmp_path / "points.gpkg")
    first, second = gpd.read_file(path).geometry
    assert first.distance(second) <= 1e-6


def test_point_from_other_body_refused():
    mars = makeImage(CRISM_MTRDR_WKT, centerLonLat=(10.0, 15.0), pixelSize=18.0)
    earth = makeImage(WGS84, centerLonLat=(10.0, 15.0), pixelSize=0.01)
    points = PointCollection()
    points.addPixel(mars, 3, 4)
    with pytest.raises(ValueError, match="Earth and Mars are different bodies"):
        points.addPixel(earth, 3, 4)
    assert len(points) == 1
