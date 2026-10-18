"""Calculated ROI columns: geometry, zonal statistics and expressions."""

import math

import numpy as np
import pytest
import shapely
from affine import Affine
from pyproj import Geod, Proj
from rasterio.crs import CRS
from shapely.geometry import box

from varda.common.entities import Color, ROIMode, VardaRaster
from varda.geo._tests.fixtures import HIRISE_POLAR_PROJ, MOLA_MEGDR_PROJ, makeImage
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.rois.roi_calculations import (
    GEOMETRY_MEASURES,
    MAP_MEASURES,
    CalculatedColumns,
    Expression,
    ExpressionError,
    GeometryMeasure,
    ZonalStatistic,
    expressionVariables,
    mapUnits,
    parseExpression,
    suggestName,
)
from varda.rois.roi_collection import NewROI, ROICollection

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
