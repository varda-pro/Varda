"""Clicks on a browse view mapped to I/F pixels, and boxes mapped back."""

import numpy as np
from affine import Affine
from pyproj import CRS, Transformer
from shapely.geometry import Polygon

from varda.common.entities import VardaRaster
from varda.geo._tests.fixtures import CRISM_MTRDR_WKT, WGS84, makeImage
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.workspaces.crism_ratio.mapping import (
    dataToView,
    viewShapeToData,
    viewToData,
)
from varda.workspaces.linked_images.linking import Link
from varda.image_loading.crism_geometry import ColumnGeometry
from varda.workspaces.crism_ratio.mapping import imageToSensor, sensorToImage

EQC = CRS.from_user_input("+proj=eqc +R=3396190 +units=m +no_defs")
SINU = CRS.from_user_input("+proj=sinu +R=3396190 +units=m +no_defs")


def _image(transform, crs, shape=(40, 50, 2), name="a"):
    data = np.zeros(shape, dtype=np.float32)
    return VardaRaster(ArrayDataSource(data, transform=transform, crs=crs), name=name)


def _to_sinu(x: float, y: float) -> tuple[float, float]:
    return Transformer.from_crs(EQC, SINU, always_xy=True).transform(x, y)


def _views():
    """The I/F cube on an equirectangular grid and a browse image of the same
    ground on a sinusoidal grid, built so a known ground point lands on a
    known pixel in each."""
    data = _image(Affine(20, 0, 1000, 0, -20, 5000), EQC, name="iof")
    # ground point (1410, 4610) is data pixel (20.5, 19.5); put it on browse
    # pixel (7.5, 9.5) of a 10 m grid
    sx, sy = _to_sinu(1410, 4610)
    view = _image(Affine(10, 0, sx - 75, 0, -10, sy + 95), SINU, name="browse")
    return view, data


def test_map_link_converts_a_click_across_projections():
    view, data = _views()
    out = viewToData(view, data, Link.MAP, [[7.5, 9.5]])
    assert out is not None
    np.testing.assert_allclose(out, [[20.5, 19.5]], atol=0.01)


def test_a_click_outside_the_data_footprint_is_none():
    view, data = _views()
    assert viewToData(view, data, Link.MAP, [[7.5, 9.5], [-500, 9.5]]) is None


def test_pixel_link_is_the_identity():
    view, data = _views()
    out = viewToData(view, data, Link.PIXEL, [[3.2, 4.8]])
    assert out is not None
    np.testing.assert_allclose(out, [[3.2, 4.8]])
    assert viewToData(view, data, Link.PIXEL, [[60, 4]]) is None  # off 50 columns


def test_data_to_view_round_trips_a_box():
    view, data = _views()
    box = np.array([[18, 17], [23, 17], [23, 22], [18, 22]], dtype=float)
    onView = dataToView(view, data, Link.MAP, box)
    np.testing.assert_allclose(
        onView[0] + (onView[2] - onView[0]) / 2, [7.5, 9.5], atol=0.5
    )
    back = viewToData(view, data, Link.MAP, onView)
    assert back is not None
    np.testing.assert_allclose(back, box, atol=0.01)


def test_a_browse_view_of_another_body_maps_nowhere():
    data = makeImage(CRISM_MTRDR_WKT, centerLonLat=(10.0, 15.0), pixelSize=18.0)
    earth = makeImage(WGS84, centerLonLat=(10.0, 15.0), pixelSize=0.01)
    assert viewToData(earth, data, Link.MAP, [[3, 4]]) is None
    assert np.isnan(dataToView(earth, data, Link.MAP, np.array([[3.0, 4.0]]))).all()


# --- shapes drawn on the browse view ---------------------------------------------------


def _drawn(view, pixels):
    """A shape drawn on ``view`` over ``pixels``, as the Draw ROI tool gives
    it: in the view's CRS coordinates."""
    return Polygon(view.imageToGeo(pixels))


def _dataPixels(data, shape):
    return data.geoToImage(np.asarray(shape.exterior.coords))


def test_a_shape_drawn_on_the_browse_view_covers_the_same_ground_on_the_cube():
    view, data = _views()
    square = [[6, 8], [9, 8], [9, 11], [6, 11]]  # around browse pixel (7.5, 9.5)
    shape = viewShapeToData(view, data, Link.MAP, _drawn(view, square))
    assert shape is not None
    # the same ground: centered on cube pixel (20.5, 19.5), at half the pixel count
    pixels = _dataPixels(data, shape)
    np.testing.assert_allclose(pixels.mean(axis=0), [20.5, 19.5], atol=0.1)
    np.testing.assert_allclose(np.ptp(pixels, axis=0), [1.5, 1.5], atol=0.05)


def test_a_drawn_shape_keeps_a_vertex_per_browse_pixel_across_projections():
    # a straight edge on one projection is curved on another; the shape's
    # vertices follow the curve, not the chord between its corners
    view, data = _views()
    square = [[0, 0], [40, 0], [40, 30], [0, 30]]
    shape = viewShapeToData(view, data, Link.MAP, _drawn(view, square))
    assert shape is not None
    assert len(shape.exterior.coords) > 100


def test_under_a_pixel_link_a_drawn_shape_keeps_its_pixels():
    view, data = _views()
    square = [[2, 3], [5, 3], [5, 6], [2, 6]]
    shape = viewShapeToData(view, data, Link.PIXEL, _drawn(view, square))
    assert shape is not None
    pixels = _dataPixels(data, shape)
    np.testing.assert_allclose(pixels.min(axis=0), [2, 3], atol=1e-9)
    np.testing.assert_allclose(pixels.max(axis=0), [5, 6], atol=1e-9)


def test_on_images_without_a_crs_a_drawn_shape_stays_in_pixels():
    view = _image(None, None, name="browse")
    data = _image(None, None, name="iof")
    square = Polygon([[2, 3], [5, 3], [5, 6], [2, 6]])
    shape = viewShapeToData(view, data, Link.PIXEL, square)
    assert shape is not None
    assert shape.equals(square)


def test_a_shape_drawn_on_another_body_maps_nowhere():
    data = makeImage(CRISM_MTRDR_WKT, centerLonLat=(10.0, 15.0), pixelSize=18.0)
    earth = makeImage(WGS84, centerLonLat=(10.0, 15.0), pixelSize=0.01)
    square = [[2, 3], [5, 3], [5, 6], [2, 6]]
    assert viewShapeToData(earth, data, Link.MAP, _drawn(earth, square)) is None


# --- sensor positions ------------------------------------------------------------------


def _geometry(lines: bool = True) -> ColumnGeometry:
    """A 10 × 20 map whose pixels see detector sample 19 - col (mirrored)
    and, in steps of 2, line 2 * row."""
    rows, cols = np.mgrid[0:10, 0:20].astype(np.float64)
    return ColumnGeometry(
        ir_sample=19 - cols, ir_line=2 * rows if lines else None, source="IN"
    )


def test_a_sensor_position_lands_on_the_pixel_that_saw_it():
    pixel = sensorToImage(_geometry(), 15.0, 8.0)
    assert pixel is not None
    assert (pixel.col, pixel.row) == (4, 4)
    assert (pixel.sample, pixel.line, pixel.offset) == (15.0, 8.0, 0.0)
    assert imageToSensor(_geometry(), 4, 4) == (15.0, 8.0)


def test_a_position_between_pixels_goes_to_the_nearest_and_says_how_far():
    pixel = sensorToImage(_geometry(), 15.0, 9.0)  # lines 8 and 10 were seen
    assert pixel is not None and pixel.col == 4 and pixel.row in (4, 5)
    assert pixel.offset == 1.0


def test_without_lines_or_values_there_is_no_position():
    assert sensorToImage(_geometry(lines=False), 15.0, 8.0) is None
    geometry = _geometry()
    geometry.ir_sample[:] = np.nan
    assert sensorToImage(geometry, 15.0, 8.0) is None
    assert imageToSensor(_geometry(), 99, 0) is None
    assert imageToSensor(geometry, 4, 4) is None
