"""Importing vector files as ROIs, points and lines: reading, placing on an
image (reprojection, pixels, other bodies), routing by geometry type,
attributes, and round trips of Varda's own exports."""

import threading
import time

import attrs
import geopandas as gpd
import numpy as np
import pytest
from affine import Affine
from pyproj import CRS, Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    box,
)

from varda.common.entities import Color, ROIMode, VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.lines.line_collection import LineCollection, LineKind
from varda.points.point_collection import PointCollection
from varda.rois.roi_collection import ROICollection
from varda import vector_import
from varda.utilities.proj_lock import PROJ_LOCK
from varda.vector_import import (
    bodyLabel,
    Coordinates,
    ImportOptions,
    Target,
    VectorImportError,
    addToLines,
    addToPoints,
    addToROIs,
    defaultOptions,
    listLayers,
    planImport,
    readVectorFile,
)

UTM = CRS.from_epsg(32612)
MARS_EQC = CRS.from_user_input("+proj=eqc +R=3396190 +units=m +no_defs")
RED = Color.fromHexString("#ff000080")
BLUE = Color.fromHexString("#0000ffff")


def _utmImage() -> VardaRaster:
    """30 × 20 pixels of 10 m, from (500000, 4000000) down and east."""
    source = ArrayDataSource(
        np.zeros((20, 30, 1)),
        transform=Affine(10.0, 0.0, 500000.0, 0.0, -10.0, 4000000.0),
        crs=UTM,
    )
    return VardaRaster(source, name="utm")


def _marsImage() -> VardaRaster:
    """100 × 100 pixels of 100 m, centered on 0° N 0° E of Mars."""
    source = ArrayDataSource(
        np.zeros((100, 100, 1)),
        transform=Affine(100.0, 0.0, -5000.0, 0.0, -100.0, 5000.0),
        crs=MARS_EQC,
    )
    return VardaRaster(source, name="mars")


def _plainImage() -> VardaRaster:
    return VardaRaster(ArrayDataSource(np.zeros((30, 40, 1))), name="plain")


def _plan(path, image, target, **options):
    source = readVectorFile(path)
    chosen = attrs.evolve(defaultOptions(source, image, target), **options)
    return planImport(source, image, chosen, target)


# --- Round trips of Varda's own exports -------------------------------------------

FORMATS = ["geojson", "gpkg", "shp"]


@pytest.mark.parametrize("suffix", FORMATS)
@pytest.mark.parametrize("image", [_utmImage, _plainImage], ids=["utm", "pixels"])
def test_rois_round_trip(tmp_path, suffix, image):
    image = image()
    rois = ROICollection.fromImage(image)
    first = Polygon(image.imageToGeo([[2, 3], [9, 3], [9, 8], [2, 8]]))
    second = Polygon(image.imageToGeo([[12.5, 4], [20, 4], [15, 10.25]]))
    if not image.hasGeospatialData:
        first = Polygon([[2, 3], [9, 3], [9, 8], [2, 8]])
        second = Polygon([[12.5, 4], [20, 4], [15, 10.25]])
    rois.addROI(first, "Basalt", RED, ROIMode.RECTANGLE, notes="dark")
    rois.addROI(second, "Dust", BLUE, ROIMode.FREEHAND, notes="bright")
    path = tmp_path / f"rois.{suffix}"
    rois.toFile(str(path))

    imported = ROICollection.fromImage(image)
    plan = _plan(path, image, Target.ROIS)
    addToROIs(imported, plan)

    assert plan.outside == 0 and not plan.skipped
    pairs = list(zip(rois.getAllROIs(), imported.getAllROIs(), strict=True))
    for original, copy in pairs:
        assert copy.name == original.name
        assert copy.color == original.color
        assert copy.roiType == original.roiType
        assert copy.properties["notes"] == original.properties["notes"]
        # (a shapefile may wind the ring the other way)
        assert copy.geometry.normalize().equals_exact(
            original.geometry.normalize(), 1e-6
        )
    assert imported.userColumns == ["notes"]


@pytest.mark.parametrize("suffix", FORMATS)
@pytest.mark.parametrize("image", [_utmImage, _plainImage], ids=["utm", "pixels"])
def test_points_round_trip(tmp_path, suffix, image):
    image = image()
    points = PointCollection()
    points.addPixel(image, 3, 4, name="Rock", color=RED, description="vesicular")
    points.addPixel(image, 17, 9, name="Soil", color=BLUE)
    path = tmp_path / f"points.{suffix}"
    points.toFile(path)

    imported = PointCollection()
    addToPoints(imported, image, _plan(path, image, Target.POINTS))

    for original, copy in zip(points.getAllPoints(), imported.getAllPoints()):
        assert (copy.name, copy.x, copy.y) == (original.name, original.x, original.y)
        assert copy.color == original.color
        assert copy.description == original.description
        assert copy.geometry.equals_exact(original.geometry, 1e-6)
        assert copy.properties == {}
    assert len(imported) == 2


@pytest.mark.parametrize("suffix", FORMATS)
@pytest.mark.parametrize("image", [_utmImage, _plainImage], ids=["utm", "pixels"])
def test_lines_round_trip(tmp_path, suffix, image):
    image = image()
    lines = LineCollection()
    lines.addLine([[1, 1], [20.5, 15]], LineKind.STRAIGHT, name="A", color="#ff0000")
    lines.addLine(
        [[2, 2], [5, 9], [25, 3]],
        LineKind.FREEHAND,
        name="B",
        color="#00ff00",
        properties={"survey": "2024"},
    )
    path = tmp_path / f"lines.{suffix}"
    lines.toFile(path, image)

    imported = LineCollection()
    addToLines(imported, _plan(path, image, Target.LINES))

    for original, copy in zip(lines.lines(), imported.lines(), strict=True):
        assert (copy.name, copy.color, copy.kind) == (
            original.name,
            original.color,
            original.kind,
        )
        np.testing.assert_allclose(copy.vertices, original.vertices, atol=1e-6)
    assert imported.lines()[1].properties == {"survey": "2024"}


def test_a_geopackage_layer_is_chosen(tmp_path):
    image = _utmImage()
    path = tmp_path / "two.gpkg"
    gpd.GeoDataFrame(
        {"name": ["a"]}, geometry=[Point(image.pixelToGeo(1, 1))], crs=UTM
    ).to_file(path, layer="first")
    gpd.GeoDataFrame(
        {"name": ["b", "c"]},
        geometry=[Point(image.pixelToGeo(2, 2)), Point(image.pixelToGeo(5, 5))],
        crs=UTM,
    ).to_file(path, layer="second")

    assert listLayers(path) == ("first", "second")
    source = readVectorFile(path, layer="second")
    assert source.layers == ("first", "second") and source.layer == "second"
    plan = planImport(
        source, image, defaultOptions(source, image, Target.POINTS), Target.POINTS
    )
    assert [f.name for f in plan.features] == ["b", "c"]


# --- Coordinates ----------------------------------------------------------------------


def test_longitude_latitude_are_reprojected_to_the_utm_image(tmp_path):
    image = _utmImage()
    toLonLat = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    # the centers of pixels (4, 7) and (25, 18)
    centers = [image.pixelToGeo(4, 7), image.pixelToGeo(25, 18)]
    lonLat = [Point(*toLonLat.transform(x, y)) for x, y in centers]
    path = tmp_path / "wgs84.geojson"
    gpd.GeoDataFrame({"name": ["a", "b"]}, geometry=lonLat, crs=4326).to_file(path)

    plan = _plan(path, image, Target.POINTS)
    collection = PointCollection()
    addToPoints(collection, image, plan)

    assert [(p.x, p.y) for p in collection.getAllPoints()] == [(4, 7), (25, 18)]
    pixel = plan.features[0].geometry
    assert (pixel.x, pixel.y) == pytest.approx((4.5, 7.5), abs=1e-6)
    assert any("Reprojected" in note for note in plan.notes)


def test_wgs84_geojson_on_a_mars_image_is_degrees_on_mars(tmp_path):
    image = _marsImage()
    # 0.05° E, 0.02° S on a sphere of 3396190 m: x = R·λ, y = R·φ
    radius = 3396190.0
    x, y = radius * np.radians(0.05), radius * np.radians(-0.02)
    path = tmp_path / "mars.geojson"
    gpd.GeoDataFrame(
        {"name": ["site"]}, geometry=[Point(0.05, -0.02)], crs=4326
    ).to_file(path)

    plan = _plan(path, image, Target.POINTS)

    expected = image.geoToImage([[x, y]])[0]
    point = plan.features[0].geometry
    assert (point.x, point.y) == pytest.approx(tuple(expected), abs=1e-6)
    assert any("degrees of longitude and latitude on Mars" in n for n in plan.notes)


def test_projected_coordinates_in_a_geojson_are_not_taken_as_degrees(tmp_path):
    image = _marsImage()
    path = tmp_path / "lost.geojson"
    gpd.GeoDataFrame(
        {"name": ["a"]}, geometry=[Point(image.pixelToGeo(10, 20))], crs=4326
    ).to_file(path)

    source = readVectorFile(path)

    assert source.crs is None and "can't be degrees" in source.crsNote
    options = defaultOptions(source, image, Target.POINTS)
    assert options.coordinates is Coordinates.IMAGE
    point = planImport(source, image, options, Target.POINTS).features[0].geometry
    assert (point.x, point.y) == pytest.approx((10.5, 20.5))


def test_a_file_without_a_crs_can_be_read_as_pixels(tmp_path):
    image = _utmImage()
    path = tmp_path / "pixels.gpkg"
    gpd.GeoDataFrame(
        {"name": ["a"]}, geometry=[LineString([(1, 1), (8.5, 6)])]
    ).to_file(path)
    source = readVectorFile(path)
    assert source.crs is None

    # fits the image's pixels, so pixels are the guess
    options = defaultOptions(source, image, Target.LINES)
    assert options.coordinates is Coordinates.PIXELS
    plan = planImport(source, image, options, Target.LINES)
    assert list(plan.features[0].geometry.coords) == [(1, 1), (8.5, 6)]

    with pytest.raises(VectorImportError, match="doesn't say"):
        planImport(source, image, ImportOptions(Coordinates.FILE), Target.LINES)


def test_an_image_without_a_crs_refuses_map_coordinates(tmp_path):
    image = _plainImage()
    path = tmp_path / "utm.shp"
    gpd.GeoDataFrame(
        {"name": ["a"]}, geometry=[Point(500010, 3999990)], crs=UTM
    ).to_file(path)
    source = readVectorFile(path)
    options = defaultOptions(source, image, Target.POINTS)
    assert options.coordinates is Coordinates.FILE
    with pytest.raises(VectorImportError, match="no map coordinates"):
        planImport(source, image, options, Target.POINTS)


def test_another_crs_can_be_given(tmp_path):
    image = _utmImage()
    path = tmp_path / "unknown.gpkg"
    gpd.GeoDataFrame({"name": ["a"]}, geometry=[Point(image.pixelToGeo(6, 2))]).to_file(
        path
    )
    source = readVectorFile(path)
    plan = planImport(
        source, image, ImportOptions(Coordinates.OTHER, otherCrs=UTM), Target.POINTS
    )
    point = plan.features[0].geometry
    assert (point.x, point.y) == pytest.approx((6.5, 2.5))


# --- Routing, skipping and attributes ---------------------------------------------------


def _mixed(tmp_path, image: VardaRaster):
    """Two polygons (one a multipolygon of two), three points (two in a
    multipoint), a line and a collection with a point, in pixels."""
    path = tmp_path / "mixed.gpkg"
    gpd.GeoDataFrame(
        {"name": ["p", "mp", "pt", "mpt", "ln", "gc"], "unit": list("ABCDEF")},
        geometry=[
            box(1, 1, 5, 5),
            MultiPolygon([box(6, 6, 8, 8), box(10, 10, 12, 12)]),
            Point(3, 3),
            MultiPoint([(4, 4), (7, 7)]),
            LineString([(0, 0), (9, 9)]),
            GeometryCollection([Point(2, 2)]),
        ],
    ).to_file(path)
    return path


def test_geometry_types_route_to_their_target(tmp_path):
    image = _plainImage()
    path = _mixed(tmp_path, image)
    source = readVectorFile(path)
    assert source.countsByType() == {"polygons": 2, "points": 2, "lines": 1, "other": 1}

    rois = _plan(path, image, Target.ROIS)
    assert len(rois.features) == 3  # each polygon of the multipolygon
    assert rois.skipped == {"points": 4, "lines": 1}
    assert "4 points skipped: use the Points Manager to import them" in rois.summary()

    points = _plan(path, image, Target.POINTS)
    assert [f.name for f in points.features] == ["pt", "mpt 1", "mpt 2", "gc"]
    assert points.skipped == {"polygons": 3, "lines": 1}

    lines = _plan(path, image, Target.LINES)
    assert len(lines.features) == 1 and lines.skipped == {"polygons": 3, "points": 4}


def test_features_outside_the_image_are_skipped_and_partly_inside_kept(tmp_path):
    image = _plainImage()  # 40 × 30
    path = tmp_path / "edge.gpkg"
    straddling = box(35, 25, 50, 40)
    gpd.GeoDataFrame(
        {"name": ["in", "out", "edge"]},
        geometry=[box(1, 1, 3, 3), box(100, 100, 110, 110), straddling],
    ).to_file(path)

    plan = _plan(path, image, Target.ROIS)

    assert [f.name for f in plan.features] == ["in", "edge"]
    assert plan.features[1].geometry.equals(straddling)  # not clipped
    assert plan.outside == 1
    assert "1 skipped: entirely outside the image" in plan.summary()


def test_csv_longitude_latitude_points(tmp_path):
    image = _utmImage()
    toLonLat = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    lon, lat = toLonLat.transform(*image.pixelToGeo(12, 5))
    path = tmp_path / "sites.csv"
    path.write_text(f"Site,Latitude,Longitude,depth\nA,{lat},{lon},3.5\nB,,,1\n")

    source = readVectorFile(path)
    assert (source.xColumn, source.yColumn) == ("Longitude", "Latitude")
    assert source.crs is not None and source.crs.to_epsg() == 4326
    plan = planImport(
        source,
        image,
        ImportOptions(Coordinates.FILE, nameColumn="Site"),
        Target.POINTS,
    )
    collection = PointCollection()
    addToPoints(collection, image, plan)

    point = collection.getAllPoints()[0]
    assert (point.name, point.x, point.y) == ("A", 12, 5)
    assert point.properties == {"depth": 3.5}
    assert plan.empty == 1  # B has no coordinates


def test_csv_columns_can_be_chosen(tmp_path):
    path = tmp_path / "table.csv"
    path.write_text("id,a,b\n1,2.5,3.5\n")
    source = readVectorFile(path, xColumn="b", yColumn="a")
    assert source.crs is None
    plan = planImport(
        source, _plainImage(), ImportOptions(Coordinates.PIXELS), Target.POINTS
    )
    assert (plan.features[0].geometry.x, plan.features[0].geometry.y) == (3.5, 2.5)


def test_name_and_class_columns_are_guessed_and_chosen(tmp_path):
    image = _plainImage()
    path = tmp_path / "units.gpkg"
    gpd.GeoDataFrame(
        {"NAME": ["one", "two"], "Unit": ["Basalt", "Basalt"], "age": [3.1, 3.2]},
        geometry=[box(1, 1, 4, 4), box(6, 6, 9, 9)],
    ).to_file(path)
    source = readVectorFile(path)

    options = defaultOptions(source, image, Target.ROIS)
    assert (options.nameColumn, options.classColumn) == ("NAME", "Unit")
    assert defaultOptions(source, image, Target.POINTS).classColumn is None

    rois = ROICollection()
    addToROIs(rois, planImport(source, image, options, Target.ROIS))
    # ROIs that share a name are a class; the name column stays a column
    assert list(rois.classes()) == ["Basalt"]
    assert rois.getROI(0).properties == {"NAME": "one", "age": 3.1}
    assert rois.getROI(0).color == rois.getROI(1).color

    named = ROICollection()
    chosen = ImportOptions(Coordinates.PIXELS, nameColumn="NAME")
    addToROIs(named, planImport(source, image, chosen, Target.ROIS))
    assert list(named.classes()) == ["one", "two"]
    assert named.getROI(0).properties == {"Unit": "Basalt", "age": 3.1}


def test_rois_without_a_name_take_the_files_name(tmp_path):
    image = _plainImage()
    path = tmp_path / "outcrops.gpkg"
    gpd.GeoDataFrame(
        {"area": [1, 2]}, geometry=[box(1, 1, 2, 2), box(3, 3, 4, 4)]
    ).to_file(path)
    rois = ROICollection()
    addToROIs(rois, _plan(path, image, Target.ROIS))
    assert list(rois.classes()) == ["outcrops"]


def test_an_unreadable_file_is_reported(tmp_path):
    path = tmp_path / "broken.geojson"
    path.write_text("not json")
    with pytest.raises(VectorImportError, match="couldn't read broken.geojson"):
        readVectorFile(path)


# --- Layers without attributes ----------------------------------------------------


def test_a_csv_of_only_longitude_and_latitude_imports(tmp_path):
    image = _utmImage()
    toLonLat = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    lon, lat = toLonLat.transform(*image.pixelToGeo(7, 3))
    path = tmp_path / "bare.csv"
    path.write_text(f"lon,lat\n{lon},{lat}\n")
    plan = _plan(path, image, Target.POINTS)
    assert len(plan.features) == 1 and plan.features[0].attributes == {}
    assert plan.summary().startswith("Imported 1 point.")


def test_a_geojson_with_empty_properties_imports(tmp_path):
    path = tmp_path / "bare.geojson"
    path.write_text(
        '{"type": "FeatureCollection", "features": ['
        '{"type": "Feature", "properties": {}, '
        '"geometry": {"type": "Point", "coordinates": [3.5, 4.5]}},'
        '{"type": "Feature", "properties": {}, '
        '"geometry": {"type": "Point", "coordinates": [10, 20]}}]}'
    )
    plan = _plan(path, _plainImage(), Target.POINTS)
    assert [(f.geometry.x, f.geometry.y) for f in plan.features] == [
        (3.5, 4.5),
        (10, 20),
    ]


def test_a_geometry_only_geopackage_imports(tmp_path):
    path = tmp_path / "bare.gpkg"
    gpd.GeoDataFrame(geometry=[box(1, 1, 3, 3), box(5, 5, 6, 6)]).to_file(path)
    rois = ROICollection()
    addToROIs(rois, _plan(path, _plainImage(), Target.ROIS))
    assert len(rois) == 2 and list(rois.classes()) == ["bare"]


# --- Longitude conventions ----------------------------------------------------------

MARS_DEGREES = CRS.from_user_input("+proj=longlat +R=3396190 +no_defs")


def _degreesImage(west: float) -> VardaRaster:
    """20° × 20° of Mars in 0.1° pixels, from ``west`` east and 10° N south."""
    source = ArrayDataSource(
        np.zeros((200, 200, 1)),
        transform=Affine(0.1, 0.0, west, 0.0, -0.1, 10.0),
        crs=MARS_DEGREES,
    )
    return VardaRaster(source, name="degrees")


def _lonLatFile(tmp_path, longitudes: list[float]):
    path = tmp_path / "sites.geojson"
    gpd.GeoDataFrame(
        {"name": [str(lon) for lon in longitudes]},
        geometry=[Point(lon, 5.0) for lon in longitudes],
        crs=4326,
    ).to_file(path)
    return path


@pytest.mark.parametrize(
    "west, longitude, expected",
    [(260.0, -90.0, 100.0), (-100.0, 270.0, 100.0)],
    ids=["0-360 image, -180..180 file", "-180..180 image, 0-360 file"],
)
def test_longitudes_take_a_geographic_images_convention(
    tmp_path, west, longitude, expected
):
    image = _degreesImage(west)
    plan = _plan(_lonLatFile(tmp_path, [longitude]), image, Target.POINTS)
    point = plan.features[0].geometry
    assert (point.x, point.y) == pytest.approx((expected, 50.0))
    assert any("shifted by a multiple of 360°" in note for note in plan.notes)


def test_a_projected_image_across_180_degrees_from_its_meridian(tmp_path):
    """175° E to 185° E in a projection centered on 0°: 182° E and -178° are
    the same place, east of 180°."""
    radius = 3396190.0
    west = radius * np.radians(175.0)
    pixel = radius * np.radians(0.1)
    source = ArrayDataSource(
        np.zeros((100, 100, 1)),
        transform=Affine(pixel, 0.0, west, 0.0, -pixel, radius * np.radians(5.0)),
        crs=MARS_EQC,
    )
    image = VardaRaster(source, name="across")
    plan = _plan(_lonLatFile(tmp_path, [182.0, -178.0]), image, Target.POINTS)
    assert len(plan.features) == 2 and plan.outside == 0
    for feature in plan.features:
        assert (feature.geometry.x, feature.geometry.y) == pytest.approx((70, 0))


# --- Reading in the background --------------------------------------------------------


def test_reading_holds_the_proj_lock(tmp_path, monkeypatch):
    path = tmp_path / "a.gpkg"
    gpd.GeoDataFrame(geometry=[Point(1, 1)], crs=UTM).to_file(path)
    held = []
    readFile = gpd.read_file

    def reading(*args, **kwargs):
        # another thread can't take the lock while the file is read
        other = threading.Thread(
            target=lambda: held.append(not PROJ_LOCK.acquire(blocking=False))
        )
        other.start()
        other.join()
        return readFile(*args, **kwargs)

    monkeypatch.setattr(vector_import.gpd, "read_file", reading)
    readVectorFile(path)
    assert held == [True]


# --- ISIS sample and line --------------------------------------------------------------


def test_isis_sample_and_line_are_one_based_pixel_centers(tmp_path):
    image = _plainImage()
    path = tmp_path / "campt.csv"
    path.write_text("Sample,Line,name\n1,1,first\n3,2,third\n")
    source = readVectorFile(path)
    assert "ISIS" in source.crsNote
    options = defaultOptions(source, image, Target.POINTS)
    assert options.coordinates is Coordinates.PIXELS
    points = PointCollection()
    addToPoints(points, image, planImport(source, image, options, Target.POINTS))
    assert [(p.x, p.y) for p in points.getAllPoints()] == [(0, 0), (2, 1)]
    # the first pixel's center, as Varda saves a clicked pixel
    assert points.getAllPoints()[0].geometry.equals(Point(0.5, 0.5))


# --- Large imports ---------------------------------------------------------------------


def test_five_thousand_features_add_at_once(tmp_path):
    image = _plainImage()
    count = 5000
    path = tmp_path / "many.gpkg"
    corners = np.random.default_rng(0).uniform(0, 29, (count, 2))
    gpd.GeoDataFrame(
        {"name": [f"f{i}" for i in range(3 * count)]},
        geometry=[box(x, y, x + 1, y + 1) for x, y in corners]
        + [Point(x, y) for x, y in corners]
        + [LineString([(x, y), (x + 1, y + 1)]) for x, y in corners],
    ).to_file(path)
    rois, points, lines = ROICollection(), PointCollection(), LineCollection()
    changes = {"rois": 0, "added": 0, "each": 0, "points": 0, "lines": 0}

    def counter(key):
        def count(*_):
            changes[key] += 1

        return count

    rois.sigCollectionChanged.connect(counter("rois"))
    rois.sigROIsAdded.connect(counter("added"))
    rois.sigROIAdded.connect(counter("each"))
    points.sigCollectionChanged.connect(counter("points"))
    lines.sigCollectionChanged.connect(counter("lines"))

    started = time.perf_counter()
    addToROIs(rois, _plan(path, image, Target.ROIS))
    addToPoints(points, image, _plan(path, image, Target.POINTS))
    addToLines(lines, _plan(path, image, Target.LINES))
    elapsed = time.perf_counter() - started

    assert (len(rois), len(points), len(lines)) == (count, count, count)
    assert changes == {"rois": 1, "added": 1, "each": 0, "points": 1, "lines": 1}
    assert elapsed < 10  # seconds; adding ROIs one by one is quadratic
    assert rois.fids == list(range(count))


# --- Planetary round trips and the body choice ------------------------------------------

MARS_POLAR = CRS.from_user_input(
    "+proj=stere +lat_0=90 +lon_0=0 +k=1 +R=3396190 +units=m +no_defs"
)


def _polarImage() -> VardaRaster:
    """100 × 100 pixels of 1 m around Mars's north pole: map coordinates that
    look like degrees."""
    source = ArrayDataSource(
        np.zeros((100, 100, 1)),
        transform=Affine(1.0, 0.0, -50.0, 0.0, -1.0, 50.0),
        crs=MARS_POLAR,
    )
    return VardaRaster(source, name="pole")


@pytest.mark.parametrize("suffix", FORMATS)
def test_points_and_lines_round_trip_near_the_pole(tmp_path, suffix):
    image = _polarImage()
    points = PointCollection()
    points.addPixel(image, 10, 20, name="a")
    points.addPixel(image, 70, 90, name="b")
    lines = LineCollection()
    lines.addLine([[1, 2], [50.5, 60], [99, 3]], name="rim")
    points.toFile(tmp_path / f"points.{suffix}")
    lines.toFile(tmp_path / f"lines.{suffix}", image)

    importedPoints, importedLines = PointCollection(), LineCollection()
    addToPoints(
        importedPoints,
        image,
        _plan(tmp_path / f"points.{suffix}", image, Target.POINTS),
    )
    addToLines(importedLines, _plan(tmp_path / f"lines.{suffix}", image, Target.LINES))

    assert [(p.x, p.y) for p in importedPoints.getAllPoints()] == [(10, 20), (70, 90)]
    # a shapefile can't hold the crs column's WKT (254 characters), and GDAL
    # rewrites this projection in its .prj (k=1 as lat_ts=90): micrometers
    tolerance = 1e-4 if suffix == "shp" else 1e-6
    np.testing.assert_allclose(
        importedLines.lines()[0].vertices, lines.lines()[0].vertices, atol=tolerance
    )


def test_earth_degrees_on_mars_are_an_explicit_choice(tmp_path):
    image = _marsImage()
    source = readVectorFile(_lonLatFile(tmp_path, [0.01]))
    options = defaultOptions(source, image, Target.POINTS)
    assert options.coordinates is Coordinates.BODY
    with pytest.raises(VectorImportError, match="Degrees on Mars"):
        planImport(source, image, ImportOptions(Coordinates.FILE), Target.POINTS)
    with pytest.raises(VectorImportError, match="on Earth and the image is of Mars"):
        planImport(
            source,
            image,
            ImportOptions(Coordinates.OTHER, otherCrs=CRS.from_epsg(4326)),
            Target.POINTS,
        )


# A sphere of 3000 km: within 1 % of no known body's radii
UNKNOWN_EQC = CRS.from_user_input("+proj=eqc +R=3000000 +units=m +no_defs")


def _unknownBodyImage() -> VardaRaster:
    """100 × 100 pixels of 100 m, centered on 0° N 0° E of an unknown body."""
    source = ArrayDataSource(
        np.zeros((100, 100, 1)),
        transform=Affine(100.0, 0.0, -5000.0, 0.0, -100.0, 5000.0),
        crs=UNKNOWN_EQC,
    )
    return VardaRaster(source, name="unknown")


def test_wgs84_geojson_on_an_unknown_body_is_degrees_on_that_body(tmp_path):
    # an unknown body is not Earth: GeoJSON's WGS 84 means the image's degrees
    image = _unknownBodyImage()
    radius = 3000000.0
    x, y = radius * np.radians(0.02), radius * np.radians(-0.01)
    path = tmp_path / "unknown.geojson"
    gpd.GeoDataFrame(
        {"name": ["site"]}, geometry=[Point(0.02, -0.01)], crs=4326
    ).to_file(path)
    source = readVectorFile(path)
    options = defaultOptions(source, image, Target.POINTS)
    assert options.coordinates is Coordinates.BODY

    plan = planImport(source, image, options, Target.POINTS)

    expected = image.geoToImage([[x, y]])[0]
    point = plan.features[0].geometry
    assert (point.x, point.y) == pytest.approx(tuple(expected), abs=1e-6)
    assert any("on the image's body" in n for n in plan.notes)
    assert bodyLabel(image.crs) == "the image's body"


def test_earth_degrees_on_an_unknown_body_are_refused_with_the_hint(tmp_path):
    image = _unknownBodyImage()
    source = readVectorFile(_lonLatFile(tmp_path, [0.01]))
    with pytest.raises(
        VectorImportError, match="on Earth and the image is of an unknown body"
    ):
        planImport(source, image, ImportOptions(Coordinates.FILE), Target.POINTS)
    with pytest.raises(VectorImportError, match="Degrees on the image's body"):
        planImport(source, image, ImportOptions(Coordinates.FILE), Target.POINTS)


# --- Smaller things ------------------------------------------------------------------------


def test_coordinates_proj_cant_convert_are_counted_apart(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("lon,lat\n-111,95\n")
    plan = _plan(path, _utmImage(), Target.POINTS)
    assert (plan.unplaced, plan.outside) == (1, 0)
    assert "couldn't be converted" in plan.summary()


def test_invalid_polygons_are_reported(tmp_path):
    path = tmp_path / "bowtie.gpkg"
    bowtie = Polygon([(1, 1), (5, 5), (5, 1), (1, 5)])
    gpd.GeoDataFrame({"name": ["bow"]}, geometry=[bowtie]).to_file(path)
    plan = _plan(path, _plainImage(), Target.ROIS)
    assert len(plan.features) == 1
    assert any("1 polygon invalid" in note for note in plan.notes)
