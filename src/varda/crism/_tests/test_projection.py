import math
import re
from pathlib import Path

import numpy as np
import pyproj
import pytest
from scipy import ndimage
from scipy.spatial import KDTree

from varda.crism.projection import (
    IAU2000_A,
    IAU2000_B,
    MapGrid,
    applyAreaWeights,
    applyGlt,
    areaWeights,
    mapCoordinates,
    mtrdrGrid,
    nearestNeighborGlt,
    referenceLatitude,
    sphereRadius,
)
from varda.crism.readers import DdrBand, readDdr

_MTRDR = {
    "FRT00005850": ("frt00005850_07_if167j_mtr3", "frt00005850_07_de167l_mtr1", 18.0),
    "HRL000040FF": ("hrl000040ff_07_if183j_mtr3", "hrl000040ff_07_de183l_mtr1", 36.0),
}
_FRT_DDR = "data-for-paper/FRT00005850/ddr/frt00005850_07_de167l_ddr1.lbl"
_FRT_IN = "data-for-paper/FRT00005850/mtrdr/frt00005850_07_in167j_mtr3.img"
_DEFAULT_FACTOR = 2.0


def _headerValue(header: str, key: str) -> str:
    match = re.search(rf"^{key}\s*=\s*\{{(.*?)\}}", header, re.M | re.S)
    assert match is not None, key
    return match.group(1)


def _number(text: str, key: str) -> float:
    match = re.search(rf"^\s*{key}\s*=\s*([-0-9.]+)", text, re.M)
    assert match is not None, key
    return float(match.group(1))


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _mtrdrDeLatLon(demoData: Path, obs: str) -> tuple[np.ndarray, np.ndarray, str]:
    """The MTRDR DE latitude and longitude (NaN where empty) and the IF header."""
    ifStem, deStem, _ = _MTRDR[obs]
    folder = demoData / "data-for-paper" / obs / "mtrdr"
    header = (folder / f"{deStem}.hdr").read_text()
    lines, samples = (int(_number(header, key)) for key in ("lines", "samples"))
    de = np.memmap(
        folder / f"{deStem}.img", dtype="<f4", mode="r", shape=(14, lines, samples)
    )
    lat, lon = (np.array(de[i], dtype=np.float64) for i in (3, 4))
    empty = (lat == 65535) | (lat < -90)
    lat[empty] = np.nan
    lon[empty] = np.nan
    return lat, lon, (folder / f"{ifStem}.hdr").read_text()


def _swath(
    centerLat: float,
    centerLon: float,
    lines: int = 40,
    samples: int = 30,
    step: float = 18.0,
    angleDeg: float = 12.0,
    jitter: float = 0.0,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """A rotated, optionally jittered pixel-center swath around (lat, lon), in
    degrees, with about ``step`` meters between neighbors on the ground."""
    radius = sphereRadius(centerLat)
    rows, cols = np.mgrid[0:lines, 0:samples].astype(np.float64)
    rows -= lines / 2
    cols -= samples / 2
    angle = math.radians(angleDeg)
    east = step * (cols * math.cos(angle) + rows * math.sin(angle))
    north = step * (cols * math.sin(angle) - rows * math.cos(angle))
    if jitter:
        rng = np.random.default_rng(seed)
        east += rng.normal(0, jitter, east.shape)
        north += rng.normal(0, jitter, north.shape)
    lat = centerLat + np.degrees(north / radius)
    lon = centerLon + np.degrees(east / (radius * math.cos(math.radians(centerLat))))
    return lat, lon


def test_radius_table() -> None:
    assert (IAU2000_A, IAU2000_B) == (3396190.0, 3376200.0)
    table = {
        0: 3396190.0,
        5: 3396036.8126024301,
        15: 3394839.8133163122,
        30: 3391159.2284034425,
        60: 3381164.3916799151,
        90: 3376200.0,
    }
    for lat, radius in table.items():
        assert sphereRadius(lat) == pytest.approx(radius, abs=1e-6)
        assert sphereRadius(-lat) == pytest.approx(radius, abs=1e-6)


@pytest.mark.parametrize("obs", ["FRT00005850", "HRL000040FF"])
def test_grid_matches_mtrdr_labels(demoData: Path, obs: str) -> None:
    lat, lon, header = _mtrdrDeLatLon(demoData, obs)
    pixelSize = _MTRDR[obs][2]
    grid = mtrdrGrid(lat, lon, pixelSize)
    mapInfo = _headerValue(header, "map info").split(",")
    assert grid.kind == "equirectangular"
    assert grid.referenceLatDeg == 15
    assert grid.radius == pytest.approx(3394839.813316312, abs=1e-6)
    assert grid.originX == pytest.approx(float(mapInfo[3]), abs=0.05)
    assert grid.originY == pytest.approx(float(mapInfo[4]), abs=0.05)
    assert (grid.width, grid.height) == lat.shape[::-1]
    assert _squeeze(grid.enviMapInfo()) == _squeeze(_headerValue(header, "map info"))
    assert grid.crsWkt() == _headerValue(header, "coordinate system string")
    # The label's WESTERNMOST_LONGITUDE and MAXIMUM_LATITUDE are the first
    # column and row centers: tie = x(W) - s/2, y(N) + s/2 (M1).
    label = (
        demoData / "data-for-paper" / obs / "mtrdr" / f"{_MTRDR[obs][0]}.lbl"
    ).read_text()
    west, north = (
        _number(label, key) for key in ("WESTERNMOST_LONGITUDE", "MAXIMUM_LATITUDE")
    )
    xW, yN = mapCoordinates(grid, np.array([north]), np.array([west]))
    assert grid.originX == pytest.approx(xW[0] - pixelSize / 2, abs=0.05)
    assert grid.originY == pytest.approx(yN[0] + pixelSize / 2, abs=0.05)


def test_grid_from_sensor_ddr_matches_mtrdr(demoData: Path) -> None:
    """FRT00005850's sensor-space DDR gives the MTRDR grid itself: the extreme
    DDR pixel centers lie on the UL corner of the MTRDR grid."""
    ddr = readDdr(demoData / _FRT_DDR)
    grid = mtrdrGrid(ddr.band(DdrBand.LATITUDE), ddr.band(DdrBand.LONGITUDE), 18.0)
    assert (grid.originX, grid.originY) == pytest.approx(
        (4417887.6, 1091944.9), abs=0.05
    )
    assert (grid.width, grid.height) == (990, 1043)


def test_tie_point_rule() -> None:
    lat, lon = _swath(-41.7, 203.2, jitter=3.0)
    for s in (18.0, 36.0):
        grid = mtrdrGrid(lat, lon, s)
        x, y = mapCoordinates(grid, lat, lon)
        # The extreme pixel centers fall on the UL corner (0.1 m rounding);
        # the first column and row centers, x(W) and y(N) in MTRDR labels,
        # are half a pixel inside it.
        assert grid.originX == pytest.approx(x.min(), abs=0.05)
        assert grid.originY == pytest.approx(y.max(), abs=0.05)
        assert round(grid.originX * 10) == pytest.approx(grid.originX * 10, abs=1e-6)
        assert grid.width == math.floor((x.max() - grid.originX) / s) + 1
        assert grid.height == math.floor((grid.originY - y.min()) / s) + 1
        assert grid.transform() * (0.5, 0.5) == pytest.approx(
            (grid.originX + s / 2, grid.originY - s / 2)
        )
        snapped = mtrdrGrid(lat, lon, s, snapToLattice=True)
        assert snapped.originX / s == pytest.approx(round(snapped.originX / s))
        assert snapped.originY / s == pytest.approx(round(snapped.originY / s))
        assert snapped.originX <= x.min() < snapped.originX + s
        assert snapped.originY - s < y.max() <= snapped.originY
        assert snapped.originX + snapped.width * s > x.max()
        assert snapped.originY - snapped.height * s < y.min()


def test_reference_latitude_and_wrap() -> None:
    assert referenceLatitude(-18.3) == -15
    assert referenceLatitude(18.3) == 15
    assert referenceLatitude(66) == 90
    assert referenceLatitude(-66) == -90
    assert referenceLatitude(64.9) == 60
    assert referenceLatitude(-2.0) == 0
    s = 18.0
    # Longitudes in (-180, 180] across the antimeridian, and in [0, 360)
    # across the prime meridian.
    for centerLon, wrap in (
        (179.999, 180),
        (-179.999, 180),
        (180.0, 180),
        (359.999, 360),
        (0.001, 360),
        (0.0, 360),
    ):
        lat, lon = _swath(-18.3, centerLon, angleDeg=0.0)
        lon = (lon + 180) % 360 - 180 if wrap == 180 else lon % 360
        assert np.ptp(lon) > 300  # the block really straddles the seam
        grid = mtrdrGrid(lat, lon, s)
        assert grid.referenceLatDeg == -15
        x, y = mapCoordinates(grid, lat, lon)
        assert np.abs(np.diff(x, axis=1)).max() <= 2 * s
        assert np.abs(np.diff(y, axis=0)).max() <= 2 * s
        assert grid.width <= 40
        assert -180 < grid.centerLongitude() <= 180.05
    # A scene on the antimeridian sits near x = +-pi R cos(phi_P), as in MTRDR.
    grid = mtrdrGrid(*_swath(10.0, -179.99), s)
    assert abs(grid.originX) == pytest.approx(
        math.pi * sphereRadius(10) * math.cos(math.radians(10)), abs=2000
    )
    # Poleward of 65 degrees the grid is polar stereographic on the pole.
    for centerLat, pole in ((80.0, 90), (-72.0, -90)):
        lat, lon = _swath(centerLat, 123.0, jitter=2.0)
        grid = mtrdrGrid(lat, lon, s)
        assert grid.kind == "polar"
        assert grid.referenceLatDeg == pole
        assert grid.radius == IAU2000_B
        x, y = mapCoordinates(grid, lat, lon)
        assert np.abs(np.diff(x, axis=1)).max() <= 2 * s
        assert np.abs(np.diff(y, axis=0)).max() <= 2 * s


def _pyprojXy(grid: MapGrid, lat: np.ndarray, lon: np.ndarray) -> tuple:
    crs = pyproj.CRS.from_wkt(grid.crsWkt())
    transformer = pyproj.Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True)
    return transformer.transform(lon, lat)


@pytest.mark.parametrize(
    "centerLat, centerLon",
    [(18.3, 77.4), (-18.3, -45.0), (80.0, 123.0), (-72.0, -10.0)],
)
def test_coordinates_match_pyproj(centerLat: float, centerLon: float) -> None:
    lat, lon = _swath(centerLat, centerLon)
    grid = mtrdrGrid(lat, lon, 18.0)
    x, y = mapCoordinates(grid, lat, lon)
    px, py = _pyprojXy(grid, lat, lon)
    np.testing.assert_allclose(x, px, atol=1e-6)
    np.testing.assert_allclose(y, py, atol=1e-6)
    hemisphere = "N" if centerLat > 0 else "S"
    kind = "Equirectangular" if abs(centerLat) < 65 else "Polar Stereographic"
    assert grid.crsWkt().startswith(f'PROJCS["MRO Mars {kind} [IAU 2000] [')
    assert f"{hemisphere}; 0.00E]" in grid.enviMapInfo()
    assert grid.enviMapInfo().endswith("units=Meters")


def _interiorHoles(filled: np.ndarray) -> int:
    return int((ndimage.binary_fill_holes(filled) & ~filled).sum())


@pytest.mark.parametrize("factor", [None, 0.75 * math.sqrt(2)])
def test_backplanes_trace_to_sensor(factor: float | None) -> None:
    s = 18.0
    lat, lon = _swath(18.3, 77.4, lines=60, samples=45, step=17.0, jitter=4.0)
    grid = mtrdrGrid(lat, lon, s)
    if factor is None:
        glt = nearestNeighborGlt(grid, lat, lon)
        threshold = _DEFAULT_FACTOR * s
    else:
        glt = nearestNeighborGlt(grid, lat, lon, maxDistanceFactor=factor)
        threshold = factor * s
    assert glt.sourceLine.shape == glt.sourceSample.shape == (grid.height, grid.width)
    assert glt.sourceLine.dtype == glt.sourceSample.dtype == np.int32
    filled = glt.sourceLine >= 0
    np.testing.assert_array_equal(filled, glt.sourceSample >= 0)
    assert 0.4 < filled.mean() < 1.0  # a rotated swath leaves empty corners
    x, y = mapCoordinates(grid, lat, lon)
    rows, cols = np.nonzero(filled)
    centerX = grid.originX + (cols + 0.5) * s
    centerY = grid.originY - (rows + 0.5) * s
    sourceX = x[glt.sourceLine[filled], glt.sourceSample[filled]]
    sourceY = y[glt.sourceLine[filled], glt.sourceSample[filled]]
    distance = np.hypot(sourceX - centerX, sourceY - centerY)
    assert distance.max() <= threshold
    # and the source is the nearest DDR center (brute force)
    points = np.column_stack([x.ravel(), y.ravel()])
    nearest, _ = KDTree(points).query(np.column_stack([centerX, centerY]))
    np.testing.assert_allclose(distance, nearest)
    # an empty pixel has no DDR center within the threshold
    emptyRows, emptyCols = np.nonzero(~filled)
    emptyDistance, _ = KDTree(points).query(
        np.column_stack(
            [
                grid.originX + (emptyCols + 0.5) * s,
                grid.originY - (emptyRows + 0.5) * s,
            ]
        )
    )
    assert emptyDistance.min() >= threshold - 1e-9
    # the GLT carries values and backplanes alike
    cube = np.stack([lat, lon], axis=-1).astype(np.float32)
    projected = applyGlt(cube, glt)
    assert projected.shape == (grid.height, grid.width, 2)
    assert projected.dtype == np.float32
    assert np.isnan(projected[~filled]).all()
    np.testing.assert_array_equal(
        projected[filled, 0],
        lat[glt.sourceLine[filled], glt.sourceSample[filled]].astype(np.float32),
    )
    np.testing.assert_array_equal(
        applyGlt(lat, glt)[filled],
        lat[glt.sourceLine[filled], glt.sourceSample[filled]],
    )


def test_default_threshold_fills_coarse_geometry() -> None:
    """DDR centers coarser than the grid (30 m for 18 m pixels; FRT00005850's
    are 23 x 26 m, sheared) leave no holes at the default threshold, while
    0.75 sqrt(2) leaves many."""
    lat, lon = _swath(18.3, 77.4, lines=60, samples=45, step=30.0, jitter=2.0)
    grid = mtrdrGrid(lat, lon, 18.0)
    assert _interiorHoles(nearestNeighborGlt(grid, lat, lon).sourceLine >= 0) == 0
    tight = nearestNeighborGlt(grid, lat, lon, maxDistanceFactor=0.75 * math.sqrt(2))
    assert _interiorHoles(tight.sourceLine >= 0) > 50


def test_glt_matches_mtrdr_from_sensor_ddr(demoData: Path) -> None:
    """FRT00005850's GLT from its sensor-space DDR against its MTRDR: no
    interior holes, the same empty mask on >= 99% of pixels and the same
    source pixel (the MTRDR IN "IR Sample"/"IR Line", 0-based) on >= 95% of
    the pixels both fill."""
    ddr = readDdr(demoData / _FRT_DDR)
    lat, lon = ddr.band(DdrBand.LATITUDE), ddr.band(DdrBand.LONGITUDE)
    grid = mtrdrGrid(lat, lon, 18.0)
    glt = nearestNeighborGlt(grid, lat, lon)
    backplanes = np.memmap(
        demoData / _FRT_IN, dtype="<f4", mode="r", shape=(9, grid.height, grid.width)
    )
    mtrdrSample, mtrdrLine = np.array(backplanes[5]), np.array(backplanes[6])
    mtrdrFilled = (mtrdrSample != 65535) & (mtrdrLine != 65535)
    filled = glt.sourceLine >= 0
    assert _interiorHoles(filled) == 0
    assert (filled == mtrdrFilled).mean() >= 0.99
    both = filled & mtrdrFilled
    same = (glt.sourceSample[both] == mtrdrSample[both]) & (
        glt.sourceLine[both] == mtrdrLine[both]
    )
    assert same.mean() >= 0.95


def test_glt_skips_invalid_geometry() -> None:
    lat, lon = _swath(18.3, 77.4)
    lat[5, 5] = np.nan
    lon[7, 3] = np.nan
    grid = mtrdrGrid(lat, lon, 18.0)
    glt = nearestNeighborGlt(grid, lat, lon)
    sources = set(
        zip(
            glt.sourceLine[glt.sourceLine >= 0].tolist(),
            glt.sourceSample[glt.sourceLine >= 0].tolist(),
        )
    )
    assert (5, 5) not in sources and (7, 3) not in sources


def _alignedHalfPixelSource(
    s: float, lines: int = 8, samples: int = 6
) -> tuple[MapGrid, np.ndarray, np.ndarray]:
    """Source pixels of s/2 whose edges coincide with the output pixel edges."""
    phiP = 15.0
    radius = sphereRadius(phiP)
    k = math.cos(math.radians(phiP))
    originX, originY = 4417884.0, 1091952.0
    rows, cols = np.mgrid[0:lines, 0:samples].astype(np.float64)
    x = originX + (cols + 0.5) * s / 2
    y = originY - (rows + 0.5) * s / 2
    lat = np.degrees(y / radius)
    lon = np.degrees(x / (radius * k))
    grid = MapGrid(
        kind="equirectangular",
        referenceLatDeg=phiP,
        radius=radius,
        pixelSize=s,
        originX=originX,
        originY=originY,
        width=samples // 2,
        height=lines // 2,
    )
    return grid, lat, lon


def test_area_weights_conserve_mean() -> None:
    s = 18.0
    grid, lat, lon = _alignedHalfPixelSource(s)
    weights = areaWeights(grid, lat, lon)
    assert weights.matrix.shape == (grid.height * grid.width, lat.size)
    np.testing.assert_allclose(np.asarray(weights.matrix.sum(axis=1)).ravel(), 4.0)
    np.testing.assert_allclose(weights.looks, 4.0)
    cube = np.full(lat.shape + (3,), 0.25)
    sigma = np.full(lat.shape + (3,), 0.01)
    mean, meanSigma = applyAreaWeights(cube, sigma, weights)
    assert mean.shape == meanSigma.shape == (grid.height, grid.width, 3)
    np.testing.assert_allclose(mean, 0.25)
    np.testing.assert_allclose(meanSigma, 0.005)
    # A NaN source drops out of the mean and of sigma.
    cube[0, 0, 0] = np.nan
    mean, meanSigma = applyAreaWeights(cube, sigma, weights)
    assert mean[0, 0, 0] == pytest.approx(0.25)
    assert meanSigma[0, 0, 0] == pytest.approx(0.01 * math.sqrt(3) / 3)
    # On an irregular swath a constant stays constant, and the weights
    # cover about the swath area.
    lat, lon = _swath(18.3, 77.4, lines=50, samples=40, step=17.0, jitter=1.0)
    grid = mtrdrGrid(lat, lon, 18.0)
    weights = areaWeights(grid, lat, lon)
    rowSums = np.asarray(weights.matrix.sum(axis=1)).ravel()
    # The tie point sits on the extreme centers, so the outer halves of the
    # western and northern edge pixels fall outside the grid.
    assert lat.size - (lat.shape[0] + lat.shape[1]) < rowSums.sum() <= lat.size
    mean, meanSigma = applyAreaWeights(
        np.full(lat.shape, 0.3, np.float32),
        np.full(lat.shape, 0.02, np.float32),
        weights,
    )
    assert mean.shape == (grid.height, grid.width)
    assert mean.dtype == np.float32
    covered = rowSums.reshape(grid.height, grid.width) > 0
    np.testing.assert_allclose(mean[covered], 0.3, rtol=1e-6)
    assert np.isnan(mean[~covered]).all() and np.isnan(meanSigma[~covered]).all()
    assert (meanSigma[covered] <= 0.02 * (1 + 1e-6)).all()
    assert (weights.looks[~covered] == 0).all()


def test_area_weights_memory_for_a_full_frt() -> None:
    """A full FRT (450 x 640 source pixels) stays far below 2 GB."""
    lat, lon = _swath(18.3, 77.4, lines=450, samples=640, step=18.0, angleDeg=8.0)
    grid = mtrdrGrid(lat, lon, 18.0)
    weights = areaWeights(grid, lat, lon)
    matrix = weights.matrix
    size = matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes
    assert size < 200e6
    glt = nearestNeighborGlt(grid, lat, lon)
    assert glt.sourceLine.nbytes + glt.sourceSample.nbytes < 50e6
