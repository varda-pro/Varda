"""Where rasters land between Mars coordinate systems: a marker image is
warped with ``reprojectBand``, ``onGridOf`` and the display overlay, and
each output pixel is checked against ``transformPoints``.

The source's two bands are its own pixel column and row (at pixel centers),
so a bilinear warp's output says where GDAL sampled the source; the truth is
``transformPoints`` (target → source) of each output pixel's center."""

import math

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from rasterio.enums import Resampling

from varda.common.display_grid import displayChoices, reprojectForDisplay
from varda.common.entities import VardaRaster
from varda.common.grid import onGridOf, placementProblem
from varda.geo.reproject import imageGrid, reprojectBand
from varda.geo.transform import transformPoints, warpOperation
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

SPHERE = "+proj=longlat +R=3396190 +no_defs"  # planetocentric degrees
N_SRC, N_DST = 41, 21  # source and target sizes, pixels
KM_DEGREES = 1000.0 / 59274.7  # 1 km on the Mars sphere, in degrees

# a west-positive geographic source (latitude first, planetographic)
WEST = "IAU_2015:49901"


def _geographic(crs: str) -> bool:
    return not CRS.from_user_input(crs).is_projected


def _raster(crs: str, transform: Affine, values: np.ndarray, name: str):
    return VardaRaster(
        ArrayDataSource(values.astype(np.float32), transform=transform, crs=CRS(crs)),
        name=name,
    )


class _Scene:
    """A 41 × 41 source of about 1 km pixels around (``lon``, ``lat``) on
    ``src`` and a 21 × 21 target grid of matching pixels around the same
    point on ``dst``."""

    def __init__(self, src: str, dst: str, lon: float, lat: float):
        self.src, self.dst = src, dst
        (sx,), (sy,) = transformPoints(SPHERE, src, [lon], [lat])
        px = KM_DEGREES if _geographic(src) else 1000.0
        top = sy + N_SRC / 2 * px
        if _geographic(src):  # keep a polar source inside ±90°
            top = min(90.0, top) if sy > 0 else max(-90.0 + N_SRC * px, top)
        self.srcT = Affine(px, 0, sx - N_SRC / 2 * px, 0, -px, top)
        (dx,), (dy,) = transformPoints(src, dst, [sx], [sy])
        dpx = self._pixelOnTarget(sx, sy, px)
        self.dstT = Affine(dpx, 0, dx - N_DST / 2 * dpx, 0, -dpx, dy + N_DST / 2 * dpx)
        cols, rows = np.meshgrid(np.arange(N_SRC) + 0.5, np.arange(N_SRC) + 0.5)
        self.ramps = np.stack([cols, rows], axis=-1)

    def _pixelOnTarget(self, x: float, y: float, px: float) -> float:
        """The side of a source pixel's area on the target, target units."""
        xs, ys = transformPoints(self.src, self.dst, [x, x + px, x], [y, y, y + px])
        if _geographic(self.dst):
            xs = np.unwrap(xs, period=360.0)
        jacobian = [[xs[1] - xs[0], xs[2] - xs[0]], [ys[1] - ys[0], ys[2] - ys[0]]]
        return math.sqrt(abs(np.linalg.det(jacobian)))

    def truth(self) -> tuple[np.ndarray, np.ndarray]:
        """Each target pixel center's source column and row."""
        cols, rows = np.meshgrid(np.arange(N_DST) + 0.5, np.arange(N_DST) + 0.5)
        xs, ys = transformPoints(
            self.dst, self.src, *(self.dstT * (cols.ravel(), rows.ravel()))
        )
        if _geographic(self.src):  # the turn nearest the source
            center = (self.srcT * (N_SRC / 2, 0))[0]
            xs = xs + 360.0 * np.round((center - xs) / 360.0)
        col, row = ~self.srcT * (xs, ys)
        return col.reshape(cols.shape), row.reshape(cols.shape)

    def reprojected(self) -> np.ndarray:
        grid = imageGrid(self.dst, self.dstT, N_DST, N_DST)
        return np.stack(
            [
                reprojectBand(
                    self.ramps[..., b], self.src, self.srcT, grid, Resampling.bilinear
                )
                for b in (0, 1)
            ],
            axis=-1,
        )

    def sourceRaster(self) -> VardaRaster:
        return _raster(self.src, self.srcT, self.ramps, "source")

    def targetRaster(self) -> VardaRaster:
        return _raster(self.dst, self.dstT, np.zeros((N_DST, N_DST, 1)), "target")

    def onGrid(self) -> np.ndarray:
        return onGridOf(self.sourceRaster(), self.targetRaster(), Resampling.bilinear)

    def display(self) -> np.ndarray:
        source, target = self.sourceRaster(), self.targetRaster()
        return np.stack(
            [reprojectForDisplay(source, target, b) for b in (0, 1)], axis=-1
        )


def _inside(col: np.ndarray, row: np.ndarray, margin: float) -> np.ndarray:
    return (
        np.isfinite(col)
        & (col > margin)
        & (col < N_SRC - margin)
        & (row > margin)
        & (row < N_SRC - margin)
    )


def _assertPlaced(out: np.ndarray, scene: _Scene, nearest: bool = False) -> None:
    """Every target pixel whose truth is inside the source got data, within
    ½ source pixel of the truth (bilinear), or from the right source pixel
    (nearest, away from pixel edges, where the warper's 0.125 px
    approximation decides)."""
    col, row = scene.truth()
    inside = _inside(col, row, 1.5)
    assert inside.sum() > 50, "the scene has too few pixels inside the source"
    got = out[..., 0][inside], out[..., 1][inside]
    assert np.isfinite(got[0]).all(), (
        f"{np.isfinite(got[0]).mean():.0%} of the pixels inside the source got data"
    )
    if nearest:
        fc, fr = col - np.floor(col), row - np.floor(row)
        away = inside & (fc > 0.15) & (fc < 0.85) & (fr > 0.15) & (fr < 0.85)
        assert (np.floor(out[..., 0][away]) == np.floor(col[away])).all()
        assert (np.floor(out[..., 1][away]) == np.floor(row[away])).all()
    else:
        error = np.hypot(got[0] - col[inside], got[1] - row[inside])
        assert error.max() <= 0.5


# --- a west-positive geographic source --------------------------------------------

WEST_TARGETS = [
    "IAU_2015:49900",  # the sphere, degrees
    "IAU_2015:49911",  # west-x equirectangular, ellipsoid
    "IAU_2015:49910",  # equirectangular, sphere
    "IAU_2015:49930",  # north polar stereographic
]
# east longitudes where GDAL lost all of an IAU_2015:49901 source (its west
# longitude past about 180° − latitude), and straddling ±180°
WEST_MARKERS = [(150.0, 45.0), (170.0, 45.0), (179.95, 45.0), (-179.95, 45.0)]


@pytest.mark.parametrize("target", WEST_TARGETS)
@pytest.mark.parametrize("lon, lat", WEST_MARKERS)
def test_west_positive_source_reprojects_everywhere(target, lon, lat):
    scene = _Scene(WEST, target, lon, lat)
    _assertPlaced(scene.reprojected(), scene)


@pytest.mark.parametrize("target", WEST_TARGETS)
@pytest.mark.parametrize("lon, lat", WEST_MARKERS)
def test_west_positive_source_on_grid_of_everywhere(target, lon, lat):
    scene = _Scene(WEST, target, lon, lat)
    _assertPlaced(scene.onGrid(), scene)
    _assertPlaced(scene.display(), scene, nearest=True)


# --- a planetocentric ellipsoid with +lon_wrap or +over ---------------------------

OCENTRIC = "IAU_2015:49902"  # latitude first, spherical coordinate system
WRAPPED = [
    "+proj=longlat +R=3396190 +lon_wrap=180 +no_defs",
    "+proj=longlat +a=3396190 +b=3376200 +lon_wrap=180 +no_defs",
    "+proj=eqc +R=3396190 +over +units=m +no_defs",
    "+proj=eqc +a=3396190 +b=3376200 +over +units=m +no_defs",
]


@pytest.mark.parametrize("other", WRAPPED)
def test_ocentric_ellipsoid_and_wrapped_crs_transform_points(other):
    """PROJ can't build its own operation between these ("not supported on
    spherical planetocentric"); the explicit route can."""
    lons, lats = [-160.0, 10.0, 179.95], [30.0, -60.0, 45.0]
    want = transformPoints(SPHERE, other, lons, lats)
    there = transformPoints(OCENTRIC, other, lons, lats)  # 49902's x is east
    np.testing.assert_allclose(there, want, atol=1e-6)
    back = transformPoints(other, OCENTRIC, *want)
    np.testing.assert_allclose(back, (lons, lats), atol=1e-9)


@pytest.mark.parametrize("other", WRAPPED)
def test_ocentric_ellipsoid_and_wrapped_crs_warp(other):
    assert warpOperation(other, OCENTRIC) is not None
    assert warpOperation(OCENTRIC, other) is not None
    for src, dst in ((OCENTRIC, other), (other, OCENTRIC)):
        scene = _Scene(src, dst, 200.0, 30.0)
        _assertPlaced(scene.reprojected(), scene)
        _assertPlaced(scene.onGrid(), scene)


def test_ocentric_ellipsoid_and_wrapped_crs_placement_and_display():
    scene = _Scene(WRAPPED[0], OCENTRIC, 10.0, 45.0)
    wrapped, ocentric = scene.sourceRaster(), scene.targetRaster()
    assert placementProblem(wrapped, ocentric) is None
    assert placementProblem(ocentric, wrapped) is None
    assert displayChoices([wrapped, ocentric], ocentric).excluded == []
    assert displayChoices([wrapped, ocentric], wrapped).excluded == []
    assert wrapped in displayChoices([wrapped, ocentric], ocentric).images
    assert ocentric in displayChoices([wrapped, ocentric], wrapped).images
