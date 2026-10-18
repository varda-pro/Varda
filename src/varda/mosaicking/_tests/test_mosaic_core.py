"""Mosaic core: transform fits, the recipe, the grid and rendering."""

import numpy as np
import pytest
from affine import Affine
from rasterio.crs import CRS

from varda.analysis.geometry import crop
from varda.common.entities import VardaRaster
from varda.geo._tests.fixtures import (
    CRISM_MTRDR_SOUTH_WKT,
    CRISM_MTRDR_WKT,
    MARS_EQC_PDS3_WKT,
)
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.mosaicking.grid import defaultGrid
from varda.mosaicking.model import MosaicError, MosaicProject
from varda.mosaicking.render import NO_SOURCE, Resampling, renderMosaic
from varda.mosaicking.transforms import (
    WARPS,
    FitError,
    TransformModel,
    applyMatrix,
    fitTransform,
)

PIXEL = 18.0
TRANSFORM = Affine(PIXEL, 0.0, 500000.0, 0.0, -PIXEL, 4000000.0)
RNG = np.random.default_rng(0)


def _scene(bands=4) -> VardaRaster:
    cube = RNG.uniform(0.1, 0.5, (40, 60, bands)).astype(np.float32)
    return VardaRaster(
        ArrayDataSource(
            cube,
            wavelengths=np.linspace(1000, 2000, bands),
            transform=TRANSFORM,
            crs=CRS.from_epsg(32612),
        ),
        name="scene",
    )


def _halves(scene):
    left = crop(scene, 0, 0, 40, 36, name="left")
    right = crop(scene, 0, 24, 40, 36, name="right")  # overlaps columns 24-35
    return left, right


def _shifted(image: VardaRaster, dx: float, dy: float) -> VardaRaster:
    """The same pixels, georeferenced (dx, dy) map units off: misregistered."""
    data = np.asarray(image.getData(), np.float32)
    t = image.transform
    return VardaRaster(
        ArrayDataSource(
            data,
            wavelengths=image.wavelengths,
            transform=Affine(t.a, t.b, t.c + dx, t.d, t.e, t.f + dy),
            crs=image.crs,
        ),
        name=image.name,
    )


# --- transforms ---------------------------------------------------------------


@pytest.mark.parametrize("model", [m for m in TransformModel if m not in WARPS])
def test_fits_recover_a_planted_transform(model):
    src = RNG.uniform(5e5, 5.1e5, (12, 2))
    planted = {
        TransformModel.TRANSLATION: np.array([[1, 0, 30.0], [0, 1, -12.0], [0, 0, 1]]),
        TransformModel.RIGID: _rigid(0.01, 30, -12, src),
        TransformModel.SIMILARITY: _rigid(0.01, 30, -12, src, scale=1.002),
        TransformModel.AFFINE: _affine(src),
        TransformModel.PROJECTIVE: _affine(src),
    }[model]
    dst = applyMatrix(planted, src)
    matrix, residuals = fitTransform(model, src, dst)
    assert np.allclose(applyMatrix(matrix, src), dst, atol=1e-6)
    assert residuals.max() < 1e-6


def _rigid(angle, dx, dy, src, scale=1.0):
    c = src.mean(axis=0)
    cos, sin = scale * np.cos(angle), scale * np.sin(angle)
    rot = np.array([[cos, -sin, 0], [sin, cos, 0], [0, 0, 1]])
    to, back = (
        np.array([[1, 0, -c[0]], [0, 1, -c[1]], [0, 0, 1]]),
        np.array([[1, 0, c[0] + dx], [0, 1, c[1] + dy], [0, 0, 1]]),
    )
    return back @ rot @ to


def _affine(src):
    c = src.mean(axis=0)
    a = np.array([[1.001, 0.002, 0], [-0.001, 0.999, 0], [0, 0, 1]])
    return (
        np.array([[1, 0, c[0] + 5], [0, 1, c[1] - 3], [0, 0, 1]])
        @ a
        @ np.array([[1, 0, -c[0]], [0, 1, -c[1]], [0, 0, 1]])
    )


def test_too_few_tie_points_are_refused():
    with pytest.raises(FitError, match="at least 3"):
        fitTransform(TransformModel.AFFINE, np.zeros((2, 2)), np.zeros((2, 2)))


def test_collinear_points_cant_determine_an_affine():
    src = np.column_stack([np.arange(4.0), np.arange(4.0)])
    with pytest.raises(FitError, match="line"):
        fitTransform(TransformModel.AFFINE, src, src + 1)


# --- the recipe -------------------------------------------------------------------


def test_inputs_must_share_projection_and_bands():
    scene = _scene()
    other = VardaRaster(
        ArrayDataSource(
            np.zeros((4, 4, 4), np.float32),
            transform=TRANSFORM,
            crs=CRS.from_epsg(4326),
        ),
        name="o",
    )
    with pytest.raises(MosaicError, match="projections") as error:
        MosaicProject([scene, other])
    assert "Transverse Mercator" in str(error.value)  # each one named
    assert "Geographic (lon/lat)" in str(error.value)
    with pytest.raises(MosaicError, match="bands"):
        MosaicProject([scene, _scene(bands=3)])
    with pytest.raises(MosaicError, match="two images"):
        MosaicProject([scene])


def test_equivalent_projections_written_differently_mosaic():
    # GDAL's PDS3 form and the IAU code (as ASP writes it) of one projection
    def image(crs: CRS) -> VardaRaster:
        data = np.ones((4, 4, 1), np.float32)
        return VardaRaster(ArrayDataSource(data, transform=TRANSFORM, crs=crs), "m")

    MosaicProject(
        [
            image(CRS.from_wkt(MARS_EQC_PDS3_WKT)),
            image(CRS.from_user_input("IAU_2015:49910")),
        ]
    )


def test_two_crism_scenes_are_different_projections():
    # each MTRDR's sphere is Mars's local radius under its own scene
    def image(wkt: str) -> VardaRaster:
        data = np.ones((4, 4, 1), np.float32)
        crs = CRS.from_wkt(wkt)
        return VardaRaster(ArrayDataSource(data, transform=TRANSFORM, crs=crs), "m")

    with pytest.raises(MosaicError, match="different map projections") as error:
        MosaicProject([image(CRISM_MTRDR_WKT), image(CRISM_MTRDR_SOUTH_WKT)])
    assert "3394.84 km" in str(error.value) and "3392.50 km" in str(error.value)


def test_two_crops_rebuild_the_scene_exactly():
    scene = _scene()
    project = MosaicProject(list(_halves(scene)))
    grid = defaultGrid(project)
    assert (grid.width, grid.height) == (60, 40) and grid.transform == TRANSFORM
    result = renderMosaic(project, grid)
    assert np.array_equal(
        result.cube, np.asarray(scene.getData())
    )  # every spectrum exact
    assert (result.sources[:, :36] == 0).all() and (result.sources[:, 36:] == 1).all()


def test_the_top_input_wins_and_nodata_shows_the_one_beneath():
    scene = _scene()
    left, right = _halves(scene)
    project = MosaicProject([left, right])
    project.move(1, -1)  # right on top
    assert [i.key for i in project.inputs] == [1, 0]
    result = renderMosaic(project, defaultGrid(project))
    assert (
        result.sources[:, 24:36] == 0
    ).all()  # the overlap comes from the top (right)

    holey = np.asarray(right.getData(), np.float32).copy()
    holey[5:10, 0:5] = np.nan  # no data in the top image's overlap
    rightWithHole = VardaRaster(
        ArrayDataSource(
            holey,
            wavelengths=right.wavelengths,
            transform=right.transform,
            crs=right.crs,
        ),
        name="r",
    )
    project = MosaicProject([rightWithHole, left])
    result = renderMosaic(project, defaultGrid(project))
    assert (result.sources[5:10, 24:29] == 1).all()  # the left image fills the hole
    assert np.array_equal(result.cube, np.asarray(scene.getData()))


def test_tie_points_put_a_misregistered_image_back():
    scene = _scene()
    left, right = _halves(scene)
    project = MosaicProject([left, _shifted(right, 3 * PIXEL, -2 * PIXEL)])
    for col, row in [(26, 5), (30, 20), (34, 35)]:  # the same features in the overlap
        project.addTiePoint(1, (col - 24 + 0.5, row + 0.5), 0, (col + 0.5, row + 0.5))
    residuals = project.fit(1)
    assert residuals.max() < 1e-6
    result = renderMosaic(project, defaultGrid(project))
    assert np.array_equal(result.cube, np.asarray(scene.getData()))


def test_nudging_moves_an_input_by_map_units():
    scene = _scene()
    left, right = _halves(scene)
    project = MosaicProject([left, _shifted(right, 3 * PIXEL, 0.0)])
    project.nudge(1, -3 * PIXEL, 0.0)
    assert np.array_equal(
        renderMosaic(project, defaultGrid(project)).cube, np.asarray(scene.getData())
    )
    project.resetAlignment(1)
    assert np.allclose(project.input(1).correction, np.eye(3))


def test_bilinear_reads_a_smooth_ramp_accurately():
    ramp = np.tile(np.arange(60.0, dtype=np.float32), (40, 1))[:, :, None]
    scene = VardaRaster(
        ArrayDataSource(ramp, transform=TRANSFORM, crs=CRS.from_epsg(32612)),
        name="ramp",
    )
    project = MosaicProject(list(_halves(scene)))
    project.nudge(1, 0.5 * PIXEL, 0.0)  # half a pixel: bilinear interpolates
    result = renderMosaic(project, defaultGrid(project), Resampling.BILINEAR)
    assert np.isfinite(result.cube[:, 40:50]).all()


def test_the_recipe_round_trips():
    scene = _scene()
    left, right = _halves(scene)
    project = MosaicProject([left, right])
    project.move(1, -1)
    project.nudge(1, 5.0, -2.0)
    project.input(0).model = TransformModel.AFFINE
    project.addTiePoint(1, (1, 2), 0, (3, 4))
    state = project.state()
    restored = MosaicProject([left, right])
    restored.restore(state)
    assert [i.key for i in restored.inputs] == [1, 0]
    assert np.allclose(restored.input(1).correction, project.input(1).correction)
    assert restored.input(0).model is TransformModel.AFFINE
    assert len(restored.tiePoints) == 1 and restored.tiePoints[0].referencePixel == (
        3.0,
        4.0,
    )


def test_an_area_no_input_covers_is_empty():
    scene = _scene()
    left = crop(scene, 0, 0, 20, 20, name="a")
    right = crop(scene, 20, 40, 20, 20, name="b")  # diagonal, with gaps
    result = renderMosaic(
        MosaicProject([left, right]), defaultGrid(MosaicProject([left, right]))
    )
    assert (result.sources[:20, 20:] == NO_SOURCE).all() and np.isnan(
        result.cube[:20, 20:]
    ).all()
