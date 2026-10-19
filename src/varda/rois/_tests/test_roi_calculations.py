"""Calculated ROI columns: geometry, zonal statistics and expressions."""

import gc
import math
import time

import numpy as np
import pytest
import shapely
from affine import Affine
from pyproj import Geod, Proj
from rasterio.crs import CRS
from shapely.geometry import box

from varda.common.entities import Color, ROIMode, VardaRaster
from varda.geo._tests.fixtures import (
    HIRISE_POLAR_PROJ,
    LRO_WAC_PROJ,
    MOLA_MEGDR_PROJ,
    makeImage,
)
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.rois.roi_calculations import (
    AREA_STATISTICS,
    GEOMETRY_MEASURES,
    MAP_MEASURES,
    NO_MAP_COORDINATES,
    CalculatedColumns,
    PixelAreaCache,
    Expression,
    ExpressionError,
    GeometryMeasure,
    ZonalStatistic,
    areaStatistic,
    imageAreas,
    expressionVariables,
    mapUnits,
    parseExpression,
    suggestName,
)
from varda.geo.measure import pixelAreas
from varda.rois.roi_collection import NewROI, ROICollection
from varda.utilities.background import runInBackground

RED = Color(1.0, 0.0, 0.0, 0.5)


def _image(geo=False) -> VardaRaster:
    data = np.zeros((20, 30, 2), dtype=np.float32)
    data[:, :, 0] = np.arange(30)[None, :]  # band 0 = column index
    data[:, :, 1] = 5.0
    data[0, 0, :] = np.nan
    kwargs = {}
    if geo:  # 10 m pixels
        kwargs = dict(
            transform=Affine(10, 0, 1000, 0, -10, 5000), crs=CRS.from_epsg(32633)
        )
    return VardaRaster(
        ArrayDataSource(data, bandNames=["col", "five"], **kwargs), name="scene"
    )


def _rois(image):
    rois = ROICollection.fromImage(image)
    return rois


def _addPixelBox(rois, image, x0, y0, x1, y1, name="r"):
    """An ROI covering pixel columns x0..x1-1 and rows y0..y1-1."""
    if image.hasGeospatialData:
        (gx0, gy0), (gx1, gy1) = image.transform * (x0, y0), image.transform * (x1, y1)
        geometry = box(min(gx0, gx1), min(gy0, gy1), max(gx0, gx1), max(gy0, gy1))
    else:
        geometry = box(x0, y0, x1, y1)
    return rois.addROI(geometry, name, RED, ROIMode.RECTANGLE)


def test_geometry_in_pixels_and_map_units():
    image = _image(geo=True)
    rois = _rois(image)
    fid = _addPixelBox(rois, image, 2, 2, 6, 4)  # 4 x 2 pixels
    calc = CalculatedColumns(rois)
    calc.add("area_px", GeometryMeasure("area_pixels"))
    calc.add("area_m2", GeometryMeasure("area_map"))
    calc.add("perimeter_m", GeometryMeasure("perimeter_map"))
    assert rois.getProperty(fid, "area_px") == pytest.approx(8)
    assert rois.getProperty(fid, "area_m2") == pytest.approx(800)
    assert rois.getProperty(fid, "perimeter_m") == pytest.approx(120)


def test_zonal_statistics_of_a_band_inside_each_roi():
    image = _image()
    rois = _rois(image)
    fid = _addPixelBox(rois, image, 2, 2, 6, 4)  # columns 2..5
    calc = CalculatedColumns(rois)
    calc.add("mean_col", ZonalStatistic(image, 0, "mean"))
    calc.add("max_col", ZonalStatistic(image, 0, "max"))
    calc.add("n", ZonalStatistic(image, 1, "count"))
    assert rois.getProperty(fid, "mean_col") == pytest.approx(3.5)
    assert rois.getProperty(fid, "max_col") == pytest.approx(5)
    assert rois.getProperty(fid, "n") == 8


def test_no_data_pixels_are_left_out_of_statistics():
    image = _image()
    rois = _rois(image)
    fid = _addPixelBox(rois, image, 0, 0, 2, 1)  # (0,0) is NaN, (1,0) is 1
    calc = CalculatedColumns(rois)
    calc.add("mean_col", ZonalStatistic(image, 0, "mean"))
    calc.add("n", ZonalStatistic(image, 0, "count"))
    assert rois.getProperty(fid, "mean_col") == pytest.approx(1)
    assert rois.getProperty(fid, "n") == 1


def test_expressions_combine_other_columns():
    image = _image()
    rois = _rois(image)
    fid = _addPixelBox(rois, image, 2, 2, 6, 4)
    calc = CalculatedColumns(rois)
    calc.add("mean col", ZonalStatistic(image, 0, "mean"))
    calc.add("area_px", GeometryMeasure("area_pixels"))
    assert "mean_col" in expressionVariables(rois)
    calc.add("ratio", Expression("sqrt(mean_col * 2 + 1) / area_px"))
    assert rois.getProperty(fid, "ratio") == pytest.approx(np.sqrt(8) / 8)


@pytest.mark.parametrize(
    "text, problem",
    [
        ("__import__('os')", "not allowed"),
        ("mean_col.real", "not allowed"),
        ("missing + 1", "missing"),
        ("1 +", "syntax"),
        ("open('x')", "not allowed"),
    ],
)
def test_expressions_are_checked_before_running(text, problem):
    with pytest.raises(ExpressionError, match=problem):
        parseExpression(text, {"mean_col"})


def test_columns_recalculate_when_rois_are_drawn_or_edited():
    image = _image()
    rois = _rois(image)
    calc = CalculatedColumns(rois)
    calc.add("area_px", GeometryMeasure("area_pixels"))
    fid = _addPixelBox(rois, image, 0, 0, 2, 2)
    assert rois.getProperty(fid, "area_px") == pytest.approx(4)
    rois.updateROI(fid, geometry=box(0, 0, 3, 3))
    assert rois.getProperty(fid, "area_px") == pytest.approx(9)


def test_a_failing_value_is_blank_not_a_crash():
    image = _image()
    rois = _rois(image)
    fid = _addPixelBox(rois, image, 2, 2, 6, 4)
    calc = CalculatedColumns(rois)
    calc.add("zero", Expression("1 - 1"))
    calc.add("bad", Expression("1 / zero"))
    assert np.isnan(rois.getProperty(fid, "bad"))


def test_calculated_columns_are_known_and_described():
    image = _image()
    rois = _rois(image)
    calc = CalculatedColumns(rois)
    calc.add("mean_col", ZonalStatistic(image, 0, "mean"))
    assert calc.isCalculated("mean_col") and not calc.isCalculated("notes")
    assert calc.describe("mean_col") == "mean of col in scene"
    rois.removeColumn("mean_col")
    assert not calc.isCalculated("mean_col")


def test_name_suggestions():
    image = _image()
    assert suggestName(ZonalStatistic(image, 0, "mean")) == "mean_col"
    assert suggestName(GeometryMeasure("area_pixels")) == "area_px"


def test_map_unit_names_follow_the_crs():
    rois = _rois(_image(geo=True))
    assert mapUnits(rois) == "metre"
    assert suggestName(GeometryMeasure("area_map"), mapUnits(rois)) == "area_m2"
    assert mapUnits(_rois(_image())) is None


def test_band_names_with_units_work_in_names_and_expressions():
    """µ (micro sign) is rewritten by Python's parser; names must match it."""
    data = np.ones((10, 10, 1), dtype=np.float32) * 2
    image = VardaRaster(ArrayDataSource(data, bandNames=["1.0012 µm"]), name="crism")
    rois = ROICollection.fromImage(image)
    fid = rois.addROI(box(1, 1, 4, 4), "r", RED, ROIMode.RECTANGLE)
    calc = CalculatedColumns(rois)
    statistic = ZonalStatistic(image, 0, "mean")
    name = suggestName(statistic)
    assert name.startswith("mean_1_0012") and "__" not in name
    calc.add(name, statistic)
    variable = next(iter(expressionVariables(rois)))
    calc.add("double", Expression(f"{variable} * 2"))
    assert rois.getProperty(fid, "double") == pytest.approx(4)


# --- areas and perimeters on the body ----------------------------------------


def _groundSquareOnPolarStereographic() -> tuple[ROICollection, int, Proj]:
    """A 1 km × 1 km ground square at 85°N on HiRISE's polar sphere: its map
    side is 1 km times the projection's scale there."""
    image = makeImage(HIRISE_POLAR_PROJ, centerLonLat=(0.0, 85.0), pixelSize=10.0)
    proj = Proj(HIRISE_POLAR_PROJ)
    scale = proj.get_factors(0.0, 85.0).parallel_scale
    (x,), (y,) = proj([0.0], [85.0])
    half = 500.0 * scale
    rois = ROICollection.fromImage(image)
    fid = rois.addROI(
        box(x - half, y - half, x + half, y + half), "square", RED, ROIMode.RECTANGLE
    )
    return rois, fid, proj


def test_a_ground_square_at_85N_has_its_ground_area_whatever_the_projection():
    rois, fid, _ = _groundSquareOnPolarStereographic()
    calc = CalculatedColumns(rois)
    calc.add("area_km2", GeometryMeasure("area_body"))
    calc.add("perimeter_km", GeometryMeasure("perimeter_body"))
    calc.add("area_m2", GeometryMeasure("area_map"))
    assert rois.getProperty(fid, "area_km2") == pytest.approx(1.000, abs=0.001)
    assert rois.getProperty(fid, "perimeter_km") == pytest.approx(4.000, abs=0.004)
    # the planar column stays the flat map's area, scale error and all
    assert rois.getProperty(fid, "area_m2") > 1.003e6


def test_a_geographic_box_at_the_equator_has_the_geodesic_area():
    image = makeImage(
        MOLA_MEGDR_PROJ, centerLonLat=(0.05, 0.0), pixelSize=1 / 128, width=32
    )
    rois = ROICollection.fromImage(image)
    polygon = box(0.0, -0.05, 0.1, 0.05)
    fid = rois.addROI(polygon, "box", RED, ROIMode.RECTANGLE)
    calc = CalculatedColumns(rois)
    calc.add("area_km2", GeometryMeasure("area_body"))
    dense = shapely.segmentize(polygon, 0.001)
    lon, lat = np.asarray(dense.exterior.coords).T
    area, _ = Geod(a=3396000, b=3396000).polygon_area_perimeter(lon, lat)
    assert abs(area) / 1e6 == pytest.approx(35.13, rel=0.001)
    assert rois.getProperty(fid, "area_km2") == pytest.approx(
        abs(area) / 1e6, rel=0.001
    )


def test_body_measures_are_described_with_method_and_body_model_term():
    rois, _, _ = _groundSquareOnPolarStereographic()
    calc = CalculatedColumns(rois)
    calc.add("area_km2", GeometryMeasure("area_body"))
    text = calc.describe("area_km2")
    assert text is not None
    assert text.startswith("Area on the body (km²)")
    assert "Geodesic (Karney 2013) on sphere R = 3376.20 km" in text
    assert "densified to 1 pixel" in text
    assert "IAU 2015 reference ellipsoid" in text


def test_body_measures_compute_for_bulk_added_rois():
    rois, fid, _ = _groundSquareOnPolarStereographic()
    calc = CalculatedColumns(rois)
    calc.add("area_km2", GeometryMeasure("area_body"))
    geometry = rois.getROI(fid).geometry
    (added,) = rois.addROIs([NewROI(geometry, "copy", RED, ROIMode.RECTANGLE)])
    assert rois.getProperty(added, "area_km2") == pytest.approx(1.000, abs=0.001)


def test_body_measures_are_blank_with_the_reason_without_georeferencing():
    image = _image()
    rois = _rois(image)
    fid = _addPixelBox(rois, image, 2, 2, 6, 4)
    calc = CalculatedColumns(rois)
    calc.add("area_km2", GeometryMeasure("area_body"))
    assert math.isnan(rois.getProperty(fid, "area_km2"))
    assert "no map coordinates" in (calc.describe("area_km2") or "")


def test_points_off_the_projection_are_blank_with_the_reason():
    crs = "+proj=ortho +lat_0=0 +lon_0=0 +R=3396190 +units=m +no_defs"
    image = makeImage(crs, centerLonLat=(0.0, 0.0), pixelSize=1e4)
    rois = ROICollection.fromImage(image)
    fid = rois.addROI(box(3e6, 0, 4e6, 1e6), "off", RED, ROIMode.RECTANGLE)
    calc = CalculatedColumns(rois)
    calc.add("area_km2", GeometryMeasure("area_body"))
    assert math.isnan(rois.getProperty(fid, "area_km2"))
    assert "Blank for off" in (calc.describe("area_km2") or "")


def test_body_measure_names_and_planar_labels():
    assert suggestName(GeometryMeasure("area_body")) == "area_km2"
    assert suggestName(GeometryMeasure("perimeter_body")) == "perimeter_km"
    assert GEOMETRY_MEASURES["area_map"][0] == "Area (map units², planar)"
    assert GEOMETRY_MEASURES["perimeter_map"][0] == "Perimeter (map units, planar)"
    assert {"area_body", "perimeter_body"} <= MAP_MEASURES


def _earthBoxOnWgs84():
    image = makeImage("EPSG:4326", centerLonLat=(10.0, 0.0), pixelSize=0.01)
    rois = ROICollection.fromImage(image)
    fid = rois.addROI(box(9.9, -0.1, 10.1, 0.1), "box", RED, ROIMode.RECTANGLE)
    calc = CalculatedColumns(rois)
    calc.add("area_km2", GeometryMeasure("area_body"))
    return image, rois, fid, calc


def test_a_crs_set_on_the_image_reaches_its_rois_and_their_body_areas():
    image, rois, fid, calc = _earthBoxOnWgs84()
    onEarth = rois.getProperty(fid, "area_km2")
    before = rois.getROI(fid).geometry
    image.setMetadataOverrides({"crs": "IAU_2015:49900"})  # Mars, geographic
    assert rois.crs == image.crs
    assert rois.gdf.crs == image.crs
    assert rois.getROI(fid).geometry.equals(before)  # the same pixels
    onMars = rois.getProperty(fid, "area_km2")
    assert onMars < onEarth * 0.3
    assert "sphere R = 3396.19 km" in (calc.describe("area_km2") or "")
    # Undo: back to the file's CRS
    image.setMetadataOverrides({})
    assert rois.crs == image.crs
    assert rois.getProperty(fid, "area_km2") == pytest.approx(onEarth)


def test_other_metadata_edits_leave_the_rois_alone():
    image, rois, fid, _ = _earthBoxOnWgs84()
    crs = rois.crs
    changed = []
    rois.sigCollectionChanged.connect(lambda: changed.append(True))
    image.setMetadataOverrides({"description": "edited"})
    assert rois.crs is crs
    assert not changed


def test_rois_in_pixels_move_to_map_coordinates_when_the_image_gains_a_crs():
    transform = Affine(10, 0, 1000, 0, -10, 5000)
    data = np.zeros((20, 30, 1), dtype=np.float32)
    image = VardaRaster(ArrayDataSource(data, transform=transform), name="plain")
    rois = ROICollection.fromImage(image)
    fid = rois.addROI(box(2, 2, 6, 4), "r", RED, ROIMode.RECTANGLE)
    image.setMetadataOverrides({"crs": "EPSG:32633"})
    assert rois.crs is not None
    assert rois.getPixelCoordinates(fid) == pytest.approx(
        np.asarray(box(2, 2, 6, 4).exterior.coords)
    )
    image.setMetadataOverrides({})
    assert rois.crs is None
    assert rois.getROI(fid).geometry.equals(box(2, 2, 6, 4))


# --- areas of the pixels with data (pixel-area statistics) -------------------

WAC_RADIUS = 1737400.0


def _wacImage(data: np.ndarray, pixelSize: float, top: float) -> VardaRaster:
    """LRO WAC's equirectangular Moon (true scale at the equator), north-up,
    the image's top edge at map y ``top``."""
    transform = Affine(pixelSize, 0, 0, 0, -pixelSize, top)
    source = ArrayDataSource(
        data,
        bandNames=["value"],
        transform=transform,
        crs=CRS.from_string(LRO_WAC_PROJ),
    )
    return VardaRaster(source, name="wac")


def test_area_statistics_are_listed_with_labels():
    assert AREA_STATISTICS == {
        "area_km2": "area of pixels with data (km²)",
        "area_weighted_mean": "area-weighted mean",
        "sum_times_area_km2": "sum × pixel area (value·km²)",
    }


def test_the_area_of_100_pixels_of_100_m_at_the_equator_is_1_km2():
    image = makeImage(LRO_WAC_PROJ, centerLonLat=(0.0, 0.0), pixelSize=100.0)
    rois = ROICollection.fromImage(image)
    fid = _addPixelBox(rois, image, 27, 27, 37, 37)  # 10 × 10 pixels
    calc = CalculatedColumns(rois)
    calc.add("a", ZonalStatistic(image, 0, "area_km2"))
    assert rois.getProperty(fid, "a") == pytest.approx(100 * 0.1**2, rel=1e-6)


def test_1_km_pixels_at_60N_on_the_mars_equirectangular_have_half_the_area():
    image = makeImage("IAU_2015:49910", centerLonLat=(0.0, 60.0), pixelSize=1000.0)
    rois = ROICollection.fromImage(image)
    fid = _addPixelBox(rois, image, 27, 27, 37, 37)
    calc = CalculatedColumns(rois)
    calc.add("a", ZonalStatistic(image, 0, "area_km2"))
    calc.add("n", ZonalStatistic(image, 0, "count"))
    assert rois.getProperty(fid, "n") == 100
    assert rois.getProperty(fid, "a") == pytest.approx(50.0, rel=1e-3)


def test_the_area_weighted_mean_is_the_mean_for_uniform_areas():
    image = makeImage(LRO_WAC_PROJ, centerLonLat=(0.0, 0.0), pixelSize=100.0)
    rois = ROICollection.fromImage(image)
    fid = _addPixelBox(rois, image, 10, 30, 20, 34)  # rows near the equator
    calc = CalculatedColumns(rois)
    calc.add("mean", ZonalStatistic(image, 0, "mean"))
    calc.add("weighted", ZonalStatistic(image, 0, "area_weighted_mean"))
    assert rois.getProperty(fid, "weighted") == pytest.approx(
        rois.getProperty(fid, "mean"), rel=1e-7
    )


def test_area_weights_follow_latitude_in_a_roi_spanning_latitudes():
    """Rows of 1° at 50–80°N: a row's pixels have the exact spherical area
    R² Δλ (sin φ_top − sin φ_bottom); the value of a pixel is its row."""
    degree = WAC_RADIUS * math.pi / 180  # 1° of map in meters
    rows = np.arange(30)
    data = np.repeat(rows[:, None], 4, axis=1)[:, :, None].astype(np.float32)
    data[0, 0, 0] = np.nan  # no data: neither counted nor weighted
    image = _wacImage(data, degree, top=80 * degree)
    rois = ROICollection.fromImage(image)
    fid = _addPixelBox(rois, image, 0, 0, 4, 30)
    calc = CalculatedColumns(rois)
    calc.add("mean", ZonalStatistic(image, 0, "mean"))
    calc.add("weighted", ZonalStatistic(image, 0, "area_weighted_mean"))
    calc.add("area", ZonalStatistic(image, 0, "area_km2"))
    calc.add("sum_area", ZonalStatistic(image, 0, "sum_times_area_km2"))

    top = np.radians(80.0 - rows)
    rowArea = (
        WAC_RADIUS**2
        * math.radians(1.0)
        * (np.sin(top) - np.sin(top - math.radians(1.0)))
    )
    counts = np.full(30, 4)
    counts[0] = 3
    areas = rowArea * counts / 1e6  # km² per row
    assert rois.getProperty(fid, "area") == pytest.approx(areas.sum(), rel=1e-6)
    assert rois.getProperty(fid, "sum_area") == pytest.approx(
        (rows * areas).sum(), rel=1e-6
    )
    weighted = (rows * areas).sum() / areas.sum()
    assert rois.getProperty(fid, "weighted") == pytest.approx(weighted, rel=1e-6)
    # the low rows (further south) are larger: the weighted mean is pulled up
    assert rois.getProperty(fid, "weighted") > rois.getProperty(fid, "mean") + 1


def test_area_statistics_are_blank_without_georeferencing_and_say_why():
    image = _image()
    rois = _rois(image)
    fid = _addPixelBox(rois, image, 2, 2, 6, 4)
    calc = CalculatedColumns(rois)
    calc.add("a", ZonalStatistic(image, 0, "area_km2"))
    assert math.isnan(rois.getProperty(fid, "a"))
    assert NO_MAP_COORDINATES in (calc.describe("a") or "")


def test_area_statistics_are_described_with_the_pixel_area_method():
    image = makeImage(LRO_WAC_PROJ, centerLonLat=(0.0, 0.0), pixelSize=100.0)
    rois = ROICollection.fromImage(image)
    _addPixelBox(rois, image, 27, 27, 37, 37)
    calc = CalculatedColumns(rois)
    calc.add("a", ZonalStatistic(image, 0, "area_km2"))
    text = calc.describe("a") or ""
    assert text.startswith("area of pixels with data (km²) of Band 1 in fixture")
    assert "exact per row" in text and "sphere R = 1737.40 km" in text


def test_area_statistic_names():
    image = makeImage(LRO_WAC_PROJ, centerLonLat=(0.0, 0.0), pixelSize=100.0)
    band = suggestName(ZonalStatistic(image, 0, "mean")).removeprefix("mean_")
    for statistic in AREA_STATISTICS:
        assert suggestName(ZonalStatistic(image, 0, statistic)) == (
            f"{band}_{statistic}"
        )


def test_area_statistics_follow_a_crs_set_on_the_image_and_bulk_added_rois():
    image = makeImage("EPSG:4326", centerLonLat=(10.0, 0.0), pixelSize=0.01)
    rois = ROICollection.fromImage(image)
    fid = _addPixelBox(rois, image, 10, 10, 20, 20)
    calc = CalculatedColumns(rois)
    calc.add("a", ZonalStatistic(image, 0, "area_km2"))
    onEarth = rois.getProperty(fid, "a")
    image.setMetadataOverrides({"crs": "IAU_2015:49900"})  # Mars, geographic
    assert rois.getProperty(fid, "a") < onEarth * 0.3
    (added,) = rois.addROIs(
        [NewROI(rois.getROI(fid).geometry, "copy", RED, ROIMode.RECTANGLE)]
    )
    assert rois.getProperty(added, "a") == pytest.approx(rois.getProperty(fid, "a"))


def test_a_weighted_mean_over_zero_area_is_blank_not_an_error():
    values = np.array([1.0, 2.0])
    assert math.isnan(areaStatistic("area_weighted_mean", values, np.zeros(2)))
    assert areaStatistic("area_km2", values, np.zeros(2)) == 0.0
    assert math.isnan(areaStatistic("area_km2", values, None))
    assert areaStatistic("area_km2", np.array([np.nan]), np.ones(1)) == 0.0


def test_area_columns_on_a_large_polar_image_reuse_its_pixel_areas():
    """A 4000 × 4000 polar stereographic image (areal-scale method,
    interpolated): after the first computation, its pixel areas are reused,
    so ROI edits and further columns are quick."""
    image = makeImage(
        HIRISE_POLAR_PROJ,
        centerLonLat=(0.0, 85.0),
        pixelSize=1000.0,
        width=4000,
        height=4000,
    )
    rois = ROICollection.fromImage(image)
    fid = _addPixelBox(rois, image, 0, 0, 4000, 4000)
    calc = CalculatedColumns(rois)
    calc.add("a", ZonalStatistic(image, 0, "area_km2"))  # computes the areas
    first = rois.getProperty(fid, "a")
    calc.add("w", ZonalStatistic(image, 0, "area_weighted_mean"))
    calc.add("s", ZonalStatistic(image, 0, "sum_times_area_km2"))
    started = time.perf_counter()
    calc.recalculate(columns=["a"])  # as on an ROI edit
    assert time.perf_counter() - started < 1.0
    assert rois.getProperty(fid, "a") == first
    # the map area over areal scales ≥ 1 (true scale at the pole)
    assert 0.85 * 4000**2 < first < 4000**2


# --- the pixel-area cache and background computation -------------------------


def _polar(size: int = 64, name: str = "polar") -> VardaRaster:
    return makeImage(
        HIRISE_POLAR_PROJ,
        centerLonLat=(0.0, 85.0),
        pixelSize=1000.0,
        width=size,
        height=size,
        name=name,
    )


def test_cached_areas_match_per_pixel_areas_within_1e_6():
    """Float32 storage and, above 4,000,000 pixels, interpolation stay
    within 10⁻⁶ of ``pixelAreas`` evaluated at each pixel."""
    rng = np.random.default_rng(1)
    for image in (_polar(), _polar(2560)):  # 4096 pixels, and 6.5 Mpx
        areas = imageAreas(image.crs, image.transform, image.width, image.height)
        assert areas is not None and areas.dtype == np.float32
        rows = rng.integers(0, image.height, 300)
        cols = rng.integers(0, image.width, 300)
        assert image.crs is not None
        exact = pixelAreas(image.crs, image.transform, rows, cols)
        assert areas[rows, cols] == pytest.approx(exact, rel=1e-6)


def test_row_only_areas_stay_a_broadcast_view_and_cost_nothing():
    image = makeImage(LRO_WAC_PROJ, centerLonLat=(0.0, 0.0), pixelSize=100.0)
    cache = PixelAreaCache()
    areas = imageAreas(image.crs, image.transform, image.width, image.height)
    assert areas is not None and 0 in areas.strides
    cache.put(image, image.crs, image.transform, areas)
    assert cache.nbytes == 0


def test_a_cache_entry_goes_with_its_image():
    cache = PixelAreaCache()
    image = _polar()
    cache.put(
        image,
        image.crs,
        image.transform,
        imageAreas(image.crs, image.transform, 64, 64),
    )
    assert len(cache) == 1
    del image
    gc.collect()
    assert len(cache) == 0


def test_the_cache_drops_the_least_recently_used_above_its_limit():
    images = [_polar(name=f"p{i}") for i in range(3)]
    areas = imageAreas(images[0].crs, images[0].transform, 64, 64)
    assert areas is not None
    cache = PixelAreaCache(maxBytes=2 * areas.nbytes)
    cache.put(images[0], images[0].crs, images[0].transform, areas)
    cache.put(images[1], images[1].crs, images[1].transform, areas.copy())
    assert cache.get(images[0])[0]  # used: now the newer of the two
    cache.put(images[2], images[2].crs, images[2].transform, areas.copy())
    assert images[0] in cache and images[2] in cache
    assert images[1] not in cache
    assert cache.nbytes <= cache.maxBytes


class _HeldRunner:
    """Holds each task until the test runs it."""

    def __init__(self):
        self.pending: list[tuple] = []

    def __call__(self, task, onDone) -> None:
        self.pending.append((task, onDone))

    def finish(self) -> None:
        pending, self.pending = self.pending, []
        for task, onDone in pending:
            onDone(task())


def test_area_columns_are_blank_while_computing_then_fill_in():
    image = _polar()
    rois = ROICollection.fromImage(image)
    fid = _addPixelBox(rois, image, 10, 10, 20, 20)
    runner = _HeldRunner()
    calc = CalculatedColumns(rois, runner=runner, areaCache=PixelAreaCache())
    calc.add("a", ZonalStatistic(image, 0, "area_km2"))
    calc.add("w", ZonalStatistic(image, 0, "area_weighted_mean"))
    assert len(runner.pending) == 1  # one computation for both columns
    assert math.isnan(rois.getProperty(fid, "a"))
    assert "computing the pixel areas" in (calc.describe("a") or "")
    runner.finish()
    assert rois.getProperty(fid, "a") > 0
    assert math.isfinite(rois.getProperty(fid, "w"))
    assert "computing" not in (calc.describe("a") or "")


def test_a_result_for_an_old_crs_is_dropped_and_asked_again():
    image = makeImage("EPSG:4326", centerLonLat=(10.0, 0.0), pixelSize=0.01)
    rois = ROICollection.fromImage(image)
    fid = _addPixelBox(rois, image, 10, 10, 20, 20)
    runner = _HeldRunner()
    cache = PixelAreaCache()
    calc = CalculatedColumns(rois, runner=runner, areaCache=cache)
    calc.add("a", ZonalStatistic(image, 0, "area_km2"))
    (old,) = runner.pending
    runner.pending.clear()
    image.setMetadataOverrides({"crs": "IAU_2015:49900"})  # Mars, meanwhile
    task, onDone = old
    onDone(task())  # Earth's areas arrive late: dropped
    assert not cache.get(image)[0]
    assert math.isnan(rois.getProperty(fid, "a"))
    runner.finish()  # Mars's
    assert cache.get(image)[0]
    onMars = rois.getProperty(fid, "a")
    synchronous = CalculatedColumns(rois, areaCache=PixelAreaCache())
    synchronous.add("b", ZonalStatistic(image, 0, "area_km2"))
    assert onMars == pytest.approx(rois.getProperty(fid, "b"))


def test_area_columns_compute_off_the_gui_thread(qtbot):
    image = _polar(2048)  # 4 Mpx: about 0.3 s of PROJ work

    def runner(task, onDone) -> None:
        runInBackground(task, onDone, lambda error: onDone(None))

    rois = ROICollection.fromImage(image)
    fid = _addPixelBox(rois, image, 0, 0, 2048, 2048)
    calc = CalculatedColumns(rois, runner=runner, areaCache=PixelAreaCache())
    started = time.perf_counter()
    calc.add("a", ZonalStatistic(image, 0, "area_km2"))
    assert time.perf_counter() - started < 0.2  # returned before the areas
    assert math.isnan(rois.getProperty(fid, "a"))
    qtbot.waitUntil(lambda: math.isfinite(rois.getProperty(fid, "a")), timeout=30000)
    assert 0.85 * 2048**2 < rois.getProperty(fid, "a") < 2048**2
