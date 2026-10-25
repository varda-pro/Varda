"""Reduced reads: every n-th row and column of an image (or a window of it),
read from the file's overviews where it has them, so a texture or preview of
a HiRISE-sized image never decodes the whole image."""

import numpy as np
import rasterio
from affine import Affine

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.data_sources.cached_data_source import CachedDataSource
from varda.image_loading.data_sources.data_source import reducedIndices
from varda.image_loading.data_sources.rasterio_data_source import RasterioDataSource

CUBE = np.arange(30 * 20 * 2, dtype=np.float32).reshape(30, 20, 2)


def test_the_pixels_read_are_the_centers_of_equal_parts():
    # 10 pixels in ceil(10 / 3) = 4 parts of 2.5: centers 1.25, 3.75, 6.25, 8.75
    np.testing.assert_array_equal(reducedIndices(10, 3), [1, 3, 6, 8])
    np.testing.assert_array_equal(reducedIndices(8, 4), [2, 6])
    np.testing.assert_array_equal(reducedIndices(5, 1), [0, 1, 2, 3, 4])


def test_an_array_reads_the_centers_of_a_windows_parts():
    source = ArrayDataSource(CUBE)
    reduced = source.getDataReduced([1], (3, 2, 20, 15), 4)
    rows, cols = 3 + reducedIndices(20, 4), 2 + reducedIndices(15, 4)
    np.testing.assert_array_equal(reduced, CUBE[np.ix_(rows, cols, np.array([1]))])


def test_a_file_reads_the_same_pixels_as_an_array(tmp_path):
    """GDAL's reduced read (nearest) takes the same pixels, so one map
    transform (the window's, scaled by its size over the result's) fits both."""
    path = _geotiff(tmp_path / "same.tif")
    for window, step in (
        ((0, 0, 30, 20), 3),
        ((10, 4, 20, 16), 4),
        ((1, 1, 29, 19), 7),
    ):
        fromFile = RasterioDataSource(str(path)).getDataReduced([0, 1], window, step)
        inMemory = ArrayDataSource(CUBE).getDataReduced([0, 1], window, step)
        np.testing.assert_array_equal(fromFile, inMemory)


def _geotiff(path, nodata=None):
    data = np.moveaxis(CUBE, 2, 0).copy()
    if nodata is not None:
        data[0, :5, :5] = nodata
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=20,
        height=30,
        count=2,
        dtype="float32",
        transform=Affine(1, 0, 0, 0, -1, 30),
        nodata=nodata,
    ) as dst:
        dst.write(data)
    return path


def test_a_file_is_read_at_the_reduced_size_with_no_data_as_nan(tmp_path):
    source = RasterioDataSource(str(_geotiff(tmp_path / "a.tif", nodata=-1)))
    reduced = source.getDataReduced([0], None, 3)
    assert reduced.shape == (10, 7, 1)  # ceil(30 / 3), ceil(20 / 3)
    assert np.isnan(reduced[0, 0, 0])
    assert np.isfinite(reduced[5:, 3:]).all()
    window = source.getDataReduced([0, 1], (10, 4, 20, 16), 4)
    assert window.shape == (5, 4, 2)


def test_a_cached_source_reads_reduced_from_its_file(tmp_path):
    inner = RasterioDataSource(str(_geotiff(tmp_path / "b.tif")))
    cached = CachedDataSource(inner)
    reduced = cached.getDataReduced([0], None, 2)
    assert reduced.shape == (15, 10, 1)
    assert not cached._cache  # a reduced read isn't kept


def test_an_image_reads_reduced_with_its_edited_no_data(tmp_path):
    """An edited no-data value (HiRISE's fill, 0, which its JPEG 2000 files
    don't declare) is no data in a reduced read too, read at the reduced
    size: the same pixels as without it."""
    path = _geotiff(tmp_path / "edited.tif")
    image = VardaRaster(RasterioDataSource(str(path)), name="edited")
    plain = image.getDataReduced([0], None, 3)
    image.setMetadataOverrides({"nodata": 0.0})
    reduced = image.getDataReduced([0], None, 3)
    assert reduced.shape == (10, 7, 1)
    first = CUBE[reducedIndices(30, 3)[0], reducedIndices(20, 3)[0], 0]
    assert first != 0 and reduced[0, 0, 0] == first  # not the corner pixel
    np.testing.assert_array_equal(reduced, plain)
    image.setMetadataOverrides({"nodata": float(first)})
    assert np.isnan(image.getDataReduced([0], None, 3)[0, 0, 0])
