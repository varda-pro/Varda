"""How much memory images take.

Reading pixels in place gives exactly what the masked read gave (same values,
NaN in the same places, same dtype), without its extra copies. In-memory
images share a budget: beyond it the least recently used are read on demand
from their files, with the same results."""

import ctypes
import gc
import threading
from pathlib import Path

import tracemalloc

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

from varda.image_loading.data_sources.cached_data_source import CachedDataSource
from varda.image_loading.data_sources.in_memory_data_source import (
    InMemoryBudget,
    InMemoryDataSource,
)
from varda.image_loading.data_sources.rasterio_data_source import RasterioDataSource
from varda.image_loading.image_loading_service import wrapForReading

SHAPE = (6, 23, 17)  # bands, rows, columns


def maskedRead(path, bandIndices=None, window=None) -> np.ndarray:
    """The read as it was: masked, moved band-last, then filled."""
    with rasterio.open(path) as src:
        indexes = None if bandIndices is None else [int(i) + 1 for i in bandIndices]
        rioWindow = None
        if window is not None:
            row, col, height, width = window
            rioWindow = Window(col_off=col, row_off=row, width=width, height=height)  # ty: ignore[unknown-argument]
        data = np.moveaxis(src.read(indexes, window=rioWindow, masked=True), 0, -1)
    if np.ma.is_masked(data) and data.dtype.kind in "iub":
        data = data.astype(np.float32 if data.dtype.itemsize <= 2 else np.float64)
    return np.ma.filled(data, np.nan) if data.dtype.kind == "f" else np.ma.filled(data)


def writeCube(
    path, dtype, nodata, holes=True, interleave=None, mask=False, shape=SHAPE
):
    rng = np.random.default_rng(1)
    data = (rng.random(shape) * 100).astype(dtype)
    if holes and nodata is not None:
        data[:, 2:5, 3:9] = nodata  # a no-data block in every band
        data[2, 10, 4] = nodata  # and a single pixel in one band
    profile = dict(
        driver="ENVI" if interleave else "GTiff",
        count=shape[0],
        height=shape[1],
        width=shape[2],
        dtype=dtype,
        nodata=nodata,
        transform=from_origin(0, 0, 1, 1),
    )
    if interleave:
        profile["interleave"] = interleave
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
        if mask:
            valid = np.full(shape[1:], 255, np.uint8)
            valid[12:15, :] = 0
            dst.write_mask(valid)
    return str(path)


CASES = {
    "float32 ENVI BSQ": dict(dtype="float32", nodata=65535.0, interleave="bsq"),
    "float32 ENVI BIL": dict(dtype="float32", nodata=-9999.0, interleave="bil"),
    "float32 ENVI BIP": dict(dtype="float32", nodata=-9999.0, interleave="bip"),
    "float32 NaN no-data": dict(dtype="float32", nodata=float("nan")),
    "float64": dict(dtype="float64", nodata=-1.0),
    "float32 without no-data": dict(dtype="float32", nodata=None),
    "int16 with no-data": dict(dtype="int16", nodata=-1),
    "int16, no-data unused": dict(dtype="int16", nodata=-1, holes=False),
    "int32 with no-data": dict(dtype="int32", nodata=-1),
    "uint8 without no-data": dict(dtype="uint8", nodata=None),
    "float32 mask band": dict(dtype="float32", nodata=None, mask=True),
    "uint16 mask band": dict(dtype="uint16", nodata=None, mask=True),
    "complex64 with no-data": dict(dtype="complex64", nodata=0),
}

EDGE_WINDOWS = [  # (row, column, height, width) past an edge: clipped
    (20, 10, 10, 10),
    (0, 0, 30, 30),
    (-2, 0, 5, 5),
    (30, 0, 5, 5),
    (22, 16, 5, 5),
    (0, 17, 3, 3),
]


@pytest.mark.parametrize("case", CASES)
def test_reads_match_the_masked_read(tmp_path, case):
    options = CASES[case]
    suffix = ".img" if options.get("interleave") else ".tif"
    path = writeCube(tmp_path / f"cube{suffix}", **options)
    source = RasterioDataSource(path)
    reads = [
        (source.readAllBands(), maskedRead(path)),
        (source.getData(), maskedRead(path)),
        (source.getBands([4, 1]), maskedRead(path, bandIndices=[4, 1])),
        (
            source.getData(bandIndices=[2], window=(9, 2, 6, 5)),
            maskedRead(path, bandIndices=[2], window=(9, 2, 6, 5)),
        ),
        (source.getData(window=(12, 0, 3, 4)), maskedRead(path, window=(12, 0, 3, 4))),
        (source.getPixelSpectrum(4, 3), maskedRead(path, window=(3, 4, 1, 1))[0, 0]),
        *[(source.getData(window=w), maskedRead(path, window=w)) for w in EDGE_WINDOWS],
    ]
    for new, old in reads:
        assert new.dtype == old.dtype
        assert new.shape == old.shape
        np.testing.assert_array_equal(new, old)  # NaN in the same places
    source.close()


def test_unmasked_reads_are_the_raw_values(tmp_path):
    path = writeCube(tmp_path / "cube.tif", "int16", -1)
    source = RasterioDataSource(path)
    with rasterio.open(path) as src:
        raw = np.moveaxis(src.read(), 0, -1)
    data = source.getData(masked=False)
    assert data.dtype == np.int16
    np.testing.assert_array_equal(data, raw)
    source.close()


def test_a_whole_read_peaks_near_the_size_of_the_image(tmp_path):
    path = writeCube(tmp_path / "cube.img", "float32", -9999.0, interleave="bsq")
    source = RasterioDataSource(path)
    tracemalloc.start()
    data = source.readAllBands()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    # the masked read peaked over twice the image (data, mask and the copy)
    assert peak < 1.25 * data.nbytes + 64 * 1024
    source.close()


def test_integer_data_with_no_data_peaks_at_its_float_copy_and_the_raw_read(tmp_path):
    path = writeCube(tmp_path / "cube.tif", "int16", -1, shape=(20, 200, 150))
    source = RasterioDataSource(path)
    tracemalloc.start()
    data = source.readAllBands()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert data.dtype == np.float32
    assert peak < 1.55 * data.nbytes  # float32 + the int16 read
    source.close()


def gdalCacheMax() -> int | None:
    """GDAL's block cache size in bytes, from the library rasterio loaded."""
    folder = Path(rasterio.__file__).parent
    libraries = [
        *folder.glob(".dylibs/libgdal*"),
        *folder.parent.glob("rasterio.libs/libgdal*"),
        *folder.parent.glob("rasterio.libs/gdal*.dll"),
    ]
    if not libraries:
        return None
    gdal = ctypes.CDLL(str(libraries[0]))
    gdal.GDALGetCacheMax64.restype = ctypes.c_int64
    return gdal.GDALGetCacheMax64()


@pytest.mark.skipif(gdalCacheMax() is None, reason="GDAL library not found")
def test_reading_leaves_gdals_block_cache_alone(tmp_path):
    """The cache is process-wide: a read must not change it, not even while
    it runs on a worker thread (other threads read meanwhile)."""
    before = gdalCacheMax()
    path = writeCube(tmp_path / "c.img", "float32", 0.0, interleave="bip")
    source = RasterioDataSource(path)
    source.readAllBands()
    assert gdalCacheMax() == before
    done = threading.Event()

    def readRepeatedly():
        for _ in range(200):
            source.readAllBands()
        done.set()

    seen = set()
    worker = threading.Thread(target=readRepeatedly)
    worker.start()
    while not done.is_set():
        seen.add(gdalCacheMax())
    worker.join()
    assert seen <= {before} and gdalCacheMax() == before
    source.close()


# --- The budget ---

CUBE_BYTES = int(np.prod(SHAPE)) * 4  # float32 in memory


def load(path, budget):
    return wrapForReading(RasterioDataSource(path), limitBytes=10**9, budget=budget)


@pytest.fixture
def cubes(tmp_path):
    return [
        writeCube(tmp_path / f"cube{i}.tif", "int16", -1) for i in range(3)
    ]  # int16 holding no-data: float32 in memory


def test_the_least_recently_used_image_is_demoted_to_make_room(cubes):
    budget = InMemoryBudget(int(2.5 * CUBE_BYTES))
    first, second = load(cubes[0], budget), load(cubes[1], budget)
    first.getBands([0])  # used after the second
    third = load(cubes[2], budget)
    assert isinstance(third, InMemoryDataSource) and not third.isDemoted
    assert second.isDemoted and not first.isDemoted
    assert budget.used() == 2 * CUBE_BYTES


def test_an_image_that_cannot_fit_reads_on_demand_and_demotes_nothing(cubes):
    budget = InMemoryBudget(2 * CUBE_BYTES)
    first = load(cubes[0], budget)
    budget.limitBytes = int(0.9 * CUBE_BYTES)  # less than the next image needs
    assert isinstance(load(cubes[1], budget), CachedDataSource)
    assert not first.isDemoted


def test_a_dropped_image_frees_its_share(cubes):
    budget = InMemoryBudget(10 * CUBE_BYTES)
    image = load(cubes[0], budget)
    assert budget.used() == CUBE_BYTES
    del image
    gc.collect()
    assert budget.used() == 0


def test_a_closed_image_frees_its_share_while_still_referenced(cubes):
    budget = InMemoryBudget(10 * CUBE_BYTES)
    image = load(cubes[0], budget)
    image.close()
    assert budget.used() == 0


def test_a_demoted_image_reads_exactly_what_it_held(cubes):
    image = load(cubes[0], InMemoryBudget(10 * CUBE_BYTES))
    reads = {
        "all": lambda: image.readAllBands(),
        "data": lambda: image.getData(),
        "bands": lambda: image.getBands([3, 0]),
        "window": lambda: image.getData(bandIndices=[2], window=(1, 2, 6, 7)),
        "clean window": lambda: image.getData(window=(15, 10, 3, 3)),
        "spectrum": lambda: image.getPixelSpectrum(4, 3),
        "row": lambda: image[3],
        "pixel": lambda: image[3, -1],
        "value": lambda: image[3, 4, 2],
        "slices": lambda: image[1:9:2, 4:, 1:3],
        "list": lambda: image[:, :, [0, 2]],
        "reversed": lambda: image[::-1, 2, ::-2],
    }
    held = {name: np.array(read()) for name, read in reads.items()}
    image.demote()
    assert image.isDemoted and image.nbytes == 0
    assert image.dtype == np.float32 and image.width == SHAPE[2]
    for name, read in reads.items():
        data = read()
        assert data.dtype == held[name].dtype, name
        np.testing.assert_array_equal(data, held[name], err_msg=name)
