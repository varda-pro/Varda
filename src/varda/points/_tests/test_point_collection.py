"""Saved pixel points: where they are on the image and on the ground, and export."""

import math

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


def test_pixel_and_map_points_never_share_a_collection():
    mapped = PointCollection()
    mapped.addPixel(_georeferenced(), 3, 4)
    with pytest.raises(ValueError, match="plain has no georeferencing; the points"):
        mapped.addPixel(_plain(), 3, 4)
    pixels = PointCollection()
    pixels.addPixel(_plain(), 3, 4)
    with pytest.raises(ValueError, match="geo is georeferenced; the points are in"):
        pixels.addPixel(_georeferenced(), 3, 4)
    assert (len(mapped), len(pixels), pixels.crs) == (1, 1, None)


def test_a_point_from_a_sphere_keeps_its_planetocentric_place_on_an_ellipsoid():
    """Saved from CTX's sphere into points on the planetographic Mars
    ellipsoid: the same planetocentric latitude (Snyder, 1987, eq. 3-28)."""
    ographic = makeImage("IAU_2015:49901", centerLonLat=(-77.5, 45.3), pixelSize=0.01)
    ctx = makeImage(
        CTX_DEM_PROJ, centerLonLat=(77.5, 45.0), pixelSize=20.0, width=65, height=65
    )
    points = PointCollection()
    points.addPixel(ographic, 0, 0)
    fid = points.addPixel(ctx, 32, 32)
    geometry = points.getPoint(fid).geometry
    a, c = 3396190.0, 3376200.0
    latC = math.degrees(math.atan((c / a) ** 2 * math.tan(math.radians(geometry.y))))
    assert latC == pytest.approx(45.0, abs=1e-9)
    assert (-geometry.x) % 360 == pytest.approx(77.5, abs=1e-9)


def test_removing_the_last_point_forgets_the_crs():
    points = PointCollection()
    points.removePoint(points.addPixel(_georeferenced(), 3, 4))
    assert points.crs is None
    points.addPixel(_plain(), 3, 4)
    with pytest.raises(ValueError, match="geo is georeferenced; the points are in"):
        points.addPixel(_georeferenced(), 3, 4)
    assert (len(points), points.crs) == (1, None)


MARS_GEOGRAPHIC = "IAU_2015:49900"


def test_points_follow_a_crs_set_on_their_image():
    """Set coordinate system… on a WGS 84 image of Mars: its points are read
    on Mars, and new points from it are accepted."""
    image = makeImage(WGS84, centerLonLat=(10.0, 15.0), pixelSize=0.01)
    points = PointCollection()
    fid = points.addPixel(image, 3, 4)
    before = points.getPoint(fid).geometry
    image.setMetadataOverrides({"crs": MARS_GEOGRAPHIC})
    assert sameCrs(points.crs, image.crs)
    assert points.getPoint(fid).geometry.equals(before)  # the same map numbers
    points.addPixel(image, 5, 6)  # no longer "different bodies"
    assert len(points) == 2


def test_points_of_a_changed_image_are_converted_to_the_collections_crs():
    """Two Mars images; one's CRS is changed to another Mars CRS: its points
    are moved to where that CRS puts their pixels, in the points' CRS."""
    first = makeImage(MARS_GEOGRAPHIC, centerLonLat=(10.0, 15.0), pixelSize=0.01)
    second = makeImage(MARS_GEOGRAPHIC, centerLonLat=(10.0, 15.0), pixelSize=0.01)
    points = PointCollection()
    points.addPixel(first, 3, 4)
    fid = points.addPixel(second, 3, 4)
    shifted = "+proj=longlat +R=3396190 +lon_0=0 +pm=1 +no_defs"  # 1° east
    second.setMetadataOverrides({"crs": shifted})
    assert sameCrs(points.crs, first.crs)
    moved = points.getPoint(fid).geometry
    assert moved.x == pytest.approx(first.pixelToGeo(3, 4)[0] + 1.0, abs=1e-9)
    assert moved.y == pytest.approx(first.pixelToGeo(3, 4)[1], abs=1e-9)


def test_points_of_an_image_changed_to_another_body_are_kept_with_a_message():
    first = makeImage(MARS_GEOGRAPHIC, centerLonLat=(10.0, 15.0), pixelSize=0.01)
    second = makeImage(MARS_GEOGRAPHIC, centerLonLat=(10.0, 15.0), pixelSize=0.01)
    points = PointCollection()
    points.addPixel(first, 3, 4)
    fid = points.addPixel(second, 3, 4)
    before = points.getPoint(fid).geometry
    problems = []
    points.sigProblem.connect(problems.append)
    second.setMetadataOverrides({"crs": WGS84})
    assert points.getPoint(fid).geometry.equals(before)
    assert sameCrs(points.crs, first.crs)
    (problem,) = problems
    assert "Earth and Mars are different bodies" in problem
    assert "1 point" in problem and "kept" in problem


def test_other_metadata_edits_leave_the_points_alone():
    image = makeImage(WGS84, centerLonLat=(10.0, 15.0), pixelSize=0.01)
    points = PointCollection()
    points.addPixel(image, 3, 4)
    crs = points.crs
    changed = []
    points.sigCollectionChanged.connect(lambda: changed.append(True))
    image.setMetadataOverrides({"description": "edited"})
    assert points.crs is crs and not changed
