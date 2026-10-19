"""The reprojection engine: the aligned output grid and its guards, warps
that keep planetocentric positions, the quality measures and the record."""

import json
import re
import tracemalloc

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from rasterio.enums import Resampling

from varda.common.entities import VardaRaster
from varda.geo._tests.fixtures import (
    CTX_DEM_PROJ,
    HIRISE_POLAR_PROJ,
    MOLA_MEGDR_PROJ,
    makeImage,
)
from varda.geo.measure import pixelAreas
from varda.geo.reproject import (
    MAX_OUTPUT_PIXELS,
    TargetGrid,
    gridFor,
    reprojectBand,
    reprojectionRecord,
    scaleChange,
    sourcePixelDistance,
    sourceResolution,
)
from varda.geo.transform import transformPoints

NORTH_POLAR = "IAU_2015:49930"
MARS_EQC = "IAU_2015:49910"
SPHERE_DEGREES = "+proj=longlat +R=3396190 +no_defs"
MARKER_LON_LAT = (77.505, 44.995)


def _crs(image: VardaRaster) -> CRS:
    assert image.crs is not None
    return image.crs


def _wkt(crs: str) -> str:
    return CRS.from_user_input(crs).to_wkt()


def _ctxAt70North():
    return makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 70.0), pixelSize=20.0)


def test_default_resolution_preserves_pixel_area():
    image = _ctxAt70North()
    grid = gridFor(_crs(image), image.transform, 64, 64, NORTH_POLAR)
    source = float(pixelAreas(_crs(image), image.transform, 32, 32))
    # eqc with lat_ts 18.4: 20 m north–south, 20 cos 70° / cos 18.4° east–west
    assert source == pytest.approx(
        20 * 20 * np.cos(np.radians(70)) / np.cos(np.radians(18.4)), rel=1e-3
    )
    assert sourceResolution(_crs(image), image.transform, 64, 64) == pytest.approx(
        np.sqrt(source), rel=1e-3
    )
    output = float(pixelAreas(grid.crs, grid.affine, grid.height // 2, grid.width // 2))
    assert output == pytest.approx(source, rel=0.01)


def test_grid_is_aligned():
    image = _ctxAt70North()
    grid = gridFor(_crs(image), image.transform, 64, 64, NORTH_POLAR)
    a, b, c, d, e, f = grid.transform
    assert b == 0 and d == 0 and a > 0 and e == -a
    for value in (c, f):
        assert value / a == pytest.approx(round(value / a), abs=1e-6)


def test_grid_covers_the_source():
    image = _ctxAt70North()
    grid = gridFor(_crs(image), image.transform, 64, 64, NORTH_POLAR)
    cols, rows = np.meshgrid(np.arange(65.0), np.arange(65.0))
    xs, ys = transformPoints(
        _crs(image), grid.crs, *(image.transform * (cols.ravel(), rows.ravel()))
    )
    col, row = ~grid.affine * (xs, ys)
    assert col.min() >= 0 and row.min() >= 0
    assert col.max() <= grid.width and row.max() <= grid.height


def _roundTrip(values: np.ndarray, method: Resampling) -> np.ndarray:
    """``values`` on the Mars sphere's equirectangular map at 60°N, to north
    polar stereographic and back onto their own grid."""
    image = makeImage(MARS_EQC, centerLonLat=(30.0, 60.0), pixelSize=1000.0)
    polar = gridFor(_crs(image), image.transform, 64, 64, NORTH_POLAR)
    there = reprojectBand(values, _crs(image), image.transform, polar, method)
    own = TargetGrid(_wkt(MARS_EQC), tuple(image.transform)[:6], 64, 64)
    return reprojectBand(there.astype(np.float64), polar.crs, polar.affine, own, method)


def test_round_trip_checker():
    rows, cols = np.mgrid[0:64, 0:64]
    # nearest: each 8 × 8 square of the checker comes back where it was
    labels = (rows // 8 * 8 + cols // 8).astype(np.float64)
    back = _roundTrip(labels, Resampling.nearest)
    for label in range(64):
        r, c = divmod(label, 8)
        if not (0 < r < 7 and 0 < c < 7):
            continue  # edge squares lose pixels at the map's edge
        found = np.nonzero(back == label)
        assert abs(found[0].mean() - (r * 8 + 3.5)) <= 0.5
        assert abs(found[1].mean() - (c * 8 + 3.5)) <= 0.5
    # bilinear: a smooth checker (period 32 px) keeps its values
    smooth = 0.5 + 0.5 * np.sin(2 * np.pi * cols / 32) * np.sin(2 * np.pi * rows / 32)
    back = _roundTrip(smooth, Resampling.bilinear)
    inner = (slice(3, -3), slice(3, -3))
    assert np.isfinite(back[inner]).all()
    np.testing.assert_allclose(back[inner], smooth[inner], atol=0.05)


def test_sphere_to_ellipsoid_marker():
    values = np.zeros((100, 100))
    values[50, 50] = 1.0
    transform = Affine(0.01, 0, 77.0, 0, -0.01, 45.5)
    grid = gridFor(
        SPHERE_DEGREES, transform, 100, 100, "IAU_2015:49912", resolution=500.0
    )
    out = reprojectBand(values, SPHERE_DEGREES, transform, grid, Resampling.nearest)
    assert out.dtype == np.float32 and out.shape == (grid.height, grid.width)
    rows, cols = np.nonzero(out == 1.0)
    assert rows.size > 0, "the marker fell off the grid"
    (x,), (y,) = transformPoints(
        SPHERE_DEGREES, grid.crs, [MARKER_LON_LAT[0]], [MARKER_LON_LAT[1]]
    )
    col, row = ~grid.affine * (x, y)
    # PROJ's ballpark put it 40 rows (20 km) south
    assert abs(rows.mean() + 0.5 - row) <= 0.5
    assert abs(cols.mean() + 0.5 - col) <= 0.5


@pytest.mark.parametrize(
    "target",
    [
        "IAU_2015:49901",  # west, planetographic, latitude first
        "+proj=longlat +a=3396190 +b=3376200 +no_defs",  # same surface as 49901
    ],
)
def test_marker_on_a_latitude_first_west_target(target):
    """GDAL's traditional GIS order leaves IAU_2015:49901 latitude first."""
    values = np.zeros((100, 100))
    values[50, 50] = 1.0
    transform = Affine(0.01, 0, 77.0, 0, -0.01, 45.5)
    grid = gridFor(SPHERE_DEGREES, transform, 100, 100, target)
    out = reprojectBand(values, SPHERE_DEGREES, transform, grid, Resampling.nearest)
    rows, cols = np.nonzero(out == 1.0)
    assert rows.size > 0, "the marker fell off the grid"
    (x,), (y,) = transformPoints(
        SPHERE_DEGREES, grid.crs, [MARKER_LON_LAT[0]], [MARKER_LON_LAT[1]]
    )
    col, row = ~grid.affine * (x, y)
    assert abs(rows.mean() + 0.5 - row) <= 0.5
    assert abs(cols.mean() + 0.5 - col) <= 0.5


@pytest.mark.parametrize(
    "target", [MARS_EQC, "+proj=longlat +R=3376200 +no_defs", "IAU_2015:49901"]
)
def test_polar_cap_spans_every_longitude_once(target):
    image = makeImage(HIRISE_POLAR_PROJ, centerLonLat=(0.0, 90.0), pixelSize=2000.0)
    grid = gridFor(_crs(image), image.transform, 64, 64, target)
    unit = 1.0 if CRS.from_user_input(target).is_geographic else 3396190 * np.pi / 180
    width = grid.width * grid.transform[0] / unit
    assert 360 <= width <= 360 + 2 * grid.transform[0] / unit
    out = reprojectBand(
        np.ones((64, 64)), _crs(image), image.transform, grid, Resampling.nearest
    )
    assert np.isfinite(out).mean() > 0.5


def test_0_360_source_lands_on_a_180_grid():
    image = makeImage(MOLA_MEGDR_PROJ, centerLonLat=(200.0, 10.0), pixelSize=0.05)
    grid = gridFor(_crs(image), image.transform, 64, 64, MARS_EQC)
    assert grid.transform[2] < 0  # west of lon_0 = 0, not past 180°
    values = np.ones((64, 64))
    out = reprojectBand(values, _crs(image), image.transform, grid, Resampling.nearest)
    assert np.isfinite(out).mean() > 0.9


def test_antimeridian_refused():
    image = makeImage(MOLA_MEGDR_PROJ, centerLonLat=(180.0, 0.0), pixelSize=20 / 64)
    with pytest.raises(ValueError, match="lon_0") as error:
        gridFor(_crs(image), image.transform, 64, 64, MARS_EQC)
    assert "180" in str(error.value)


def test_size_guard():
    image = makeImage(
        MARS_EQC, centerLonLat=(0.0, 0.0), pixelSize=10_000.0, width=100, height=100
    )
    tracemalloc.start()
    try:
        with pytest.raises(ValueError) as error:
            gridFor(_crs(image), image.transform, 100, 100, MARS_EQC, resolution=1.0)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 50_000_000
    message = str(error.value)
    assert f"{MAX_OUTPUT_PIXELS:,}" in message
    suggested = re.search(r"at least ([\d,.]+) m", message)
    assert suggested is not None
    coarser = float(suggested.group(1).replace(",", ""))
    grid = gridFor(_crs(image), image.transform, 100, 100, MARS_EQC, resolution=coarser)
    assert grid.width * grid.height <= MAX_OUTPUT_PIXELS


def test_source_pixel_distance():
    image = makeImage(MARS_EQC, centerLonLat=(30.0, 10.0), pixelSize=1000.0)
    own = TargetGrid(_wkt(MARS_EQC), tuple(image.transform)[:6], 64, 64)
    distance = sourcePixelDistance(
        _crs(image), image.transform, 64, 64, own, chunkRows=7
    )
    assert distance.dtype == np.float32
    np.testing.assert_allclose(distance, 0.0, atol=1e-6)
    shifted = Affine.translation(500.0, 0.0) * image.transform
    grid = TargetGrid(_wkt(MARS_EQC), tuple(shifted)[:6], 64, 64)
    distance = sourcePixelDistance(
        _crs(image), image.transform, 64, 64, grid, chunkRows=7
    )
    covered = np.isfinite(distance)
    assert covered[:, :63].all()
    np.testing.assert_allclose(distance[covered], 0.5, atol=1e-6)


def test_scale_change_on_own_grid_is_one():
    image = _ctxAt70North()
    own = TargetGrid(_wkt(CTX_DEM_PROJ), tuple(image.transform)[:6], 64, 64)
    biggest, mean = scaleChange(_crs(image), image.transform, 64, 64, own)
    assert biggest == pytest.approx(1.0, abs=1e-6)
    assert mean == pytest.approx(1.0, abs=1e-6)


def test_record_is_json():
    transform = Affine(0.01, 0, 77.0, 0, -0.01, 45.5)
    grid = gridFor(SPHERE_DEGREES, transform, 100, 100, "IAU_2015:49912")
    scale = scaleChange(SPHERE_DEGREES, transform, 100, 100, grid)
    record = reprojectionRecord(
        SPHERE_DEGREES, grid, Resampling.bilinear, 500.0, scale=scale
    )
    text = json.dumps(record, allow_nan=False)
    assert "geoc" in text
    assert record["method"] == "bilinear"
    assert record["grid"]["width"] == grid.width
    assert record["to"]["crs"] == grid.crs
    assert {"gdal", "proj", "from", "to", "resolutionM"} <= record.keys()
    assert record["scaleChange"]["max"] >= record["scaleChange"]["mean"] > 0
