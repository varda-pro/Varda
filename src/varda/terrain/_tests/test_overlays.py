"""Overlays: brought into the DEM's projection, aligned with the mosaic core,
and draped as a texture over their footprint."""

import math

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS

from varda.geo._tests.fixtures import MARS_EQC_PDS3_WKT
from varda.mosaicking.model import MosaicProject
from varda.mosaicking.transforms import TransformModel
from varda.terrain.overlays import (
    MIN_TIE_POINTS,
    Alignment,
    AlignmentError,
    align,
    asRaster,
    bandLabel,
    bestBand,
    drape,
    hillshade,
    onExtent,
    sunLike,
    toProjection,
    windowOver,
)
from varda.utilities.debug import generate_perlin_noise_3d

MARS = CRS.from_user_input("+proj=eqc +R=3389500 +units=m +no_defs")


def _scene() -> np.ndarray:
    return generate_perlin_noise_3d((400, 400, 2), (10, 10, 2))[:, :, 0].astype(
        np.float32
    )


def test_auto_align_recovers_a_georeferencing_error():
    scene = _scene()
    reference = asRaster(
        scene[..., None], Affine(10, 0, 0, 0, -10, 4000), MARS, "ortho"
    )
    # The overlay shows the same ground, but its georeferencing puts it
    # 60 m east and 40 m south of where it belongs
    patch = scene[50:300, 80:330]
    moving = asRaster(
        patch[..., None], Affine(10, 0, 800 + 60, 0, -10, 3500 - 40), MARS, "crism band"
    )
    alignment = align(reference, moving, TransformModel.TRANSLATION)
    corrected = alignment.correct(np.array([[1000.0, 2000.0]]))[0]
    assert corrected == pytest.approx([1000 - 60, 2000 + 40], abs=1.0)
    assert alignment.points >= 3 and alignment.rms < 2
    assert 0 < alignment.phaseSnr <= 1  # the median of the refined points


def test_nothing_in_common_is_an_error_for_the_user():
    reference = asRaster(_scene()[..., None], Affine(10, 0, 0, 0, -10, 4000), MARS)
    elsewhere = asRaster(
        _scene()[:100, :100, None], Affine(10, 0, 90000, 0, -10, 90000), MARS
    )
    with pytest.raises(AlignmentError):
        align(reference, elsewhere)


def test_alignment_round_trips_and_adds_the_manual_shift():
    alignment = Alignment(TransformModel.SIMILARITY, shift=(5.0, -2.0))
    restored = Alignment.fromState(alignment.state())
    assert restored.correct(np.array([[0.0, 0.0]]))[0] == pytest.approx([5, -2])


def test_reprojection_is_skipped_within_one_projection_and_done_across():
    data = np.ones((20, 30, 1), np.float32)
    same, transform = toProjection(
        data, Affine(10, 0, 0, 0, -10, 0), MARS.to_wkt(), MARS.to_wkt()
    )
    assert same is data
    geographic = CRS.from_user_input("+proj=longlat +R=3389500 +no_defs")
    moved, transform = toProjection(
        data, Affine(0.001, 0, 10, 0, -0.001, 5), geographic.to_wkt(), MARS.to_wkt()
    )
    assert moved.shape[2] == 1 and np.isfinite(moved).any()
    assert transform.c == pytest.approx(10 * np.pi * 3389500 / 180, rel=1e-3)


def test_equivalent_projections_are_not_reprojected():
    data = np.ones((20, 30, 1), np.float32)
    iau = CRS.from_user_input("IAU_2015:49910").to_wkt()
    same, _ = toProjection(data, Affine(10, 0, 0, 0, -10, 0), MARS_EQC_PDS3_WKT, iau)
    assert same is data


def test_the_drape_covers_the_footprint_with_a_clear_border():
    rgba = np.full((50, 80, 4), 200, np.uint8)
    draped = drape(
        rgba,
        Affine(10, 0, 1000, 0, -10, 2000),
        MARS,
        Alignment(TransformModel.TRANSLATION),
        demBounds=(0.0, 0.0, 4000.0, 4000.0),
    )
    assert draped is not None
    west, south, east, north = draped.uvBounds
    assert west == pytest.approx((1000 - 10) / 4000) and north == pytest.approx(
        (2000 + 10) / 4000
    )
    assert draped.rgba[0, :, 3].max() == 0 and draped.rgba[25, 40, 3] == 200
    missing = drape(
        rgba, Affine(10, 0, 9000, 0, -10, 9000), MARS, Alignment(), (0, 0, 4000, 4000)
    )
    assert missing is None


def test_a_hillshade_lights_slopes_facing_the_sun():
    yy, xx = np.mgrid[0:50, 0:50]
    ramp = xx.astype(float)  # rises to the east: faces west
    shade = hillshade(ramp, 1.0, azimuth=270, altitude=45)
    facing = hillshade(-ramp, 1.0, azimuth=270, altitude=45)
    assert shade.mean() > facing.mean()


def test_the_matched_band_is_named_and_recorded():
    rng = np.random.default_rng(0)
    cube = np.stack(
        [_scene() + rng.normal(scale=s, size=(400, 400)) for s in (0.5, 0.01, 0.3)],
        axis=2,
    )
    image = asRaster(cube, Affine(10, 0, 0, 0, -10, 4000), MARS, "cube")
    band, snr = bestBand(image)
    assert band == 1 and snr > 1
    assert bandLabel(image, band) == "band 2"
    alignment = Alignment(
        band=band, bandLabel="band 2", bandSnr=snr, matchedOn="cube", phaseSnr=0.9
    )
    restored = Alignment.fromState(alignment.state())
    assert (restored.band, restored.bandLabel, restored.matchedOn) == (
        1,
        "band 2",
        "cube",
    )
    assert restored.bandSnr == pytest.approx(snr) and restored.phaseSnr == 0.9
    assert np.isnan(Alignment.fromState(Alignment().state()).bandSnr)


def _window(*args) -> tuple[slice, slice]:
    window = windowOver(*args)
    assert window is not None
    return window


def _halves(rows, cols) -> np.ndarray:
    """RGBA: black west half, white east half, opaque."""
    rgba = np.zeros((rows, cols, 4), np.uint8)
    rgba[:, cols // 2 :, :3] = 255
    rgba[..., 3] = 255
    return rgba


def test_an_image_is_placed_on_the_dems_extent_by_map_location():
    wkt = MARS.to_wkt()
    # 10 m pixels from x 0 to 2000, y 1000 down to 0: the DEM covers its
    # eastern half (x 1000 to 2000), where the image is white from x 1000
    rgba = _halves(100, 200)
    transform = Affine(10, 0, 0, 0, -10, 1000)
    texture = onExtent(rgba, transform, wkt, wkt, (1000, 0, 2000, 1000), 64)
    assert texture.shape == (64, 64, 4)
    assert (texture[..., 3] == 255).all()
    assert (texture[:, 1:, 0] > 250).all()  # the first column blends a little
    # west of the image's footprint, nothing: transparent
    texture = onExtent(rgba, transform, wkt, wkt, (-1000, 0, 1000, 1000), 64)
    assert (texture[:, :28, 3] == 0).all() and (texture[:, 36:, 3] == 255).all()


def test_a_texture_isnt_finer_than_the_image():
    wkt = MARS.to_wkt()
    texture = onExtent(
        _halves(10, 20), Affine(100, 0, 0, 0, -100, 1000), wkt, wkt,
        (0, 0, 2000, 1000), 8192,
    )  # fmt: skip
    assert texture.shape == (10, 20, 4)


def test_the_window_over_the_dem_has_a_margin_and_stays_in_the_image():
    wkt = MARS.to_wkt()
    transform = Affine(10, 0, 0, 0, -10, 1000)  # 100 rows, 200 columns
    rows, cols = _window(transform, 200, 100, wkt, wkt, (1000, 200, 1500, 600))
    # 50 × 40 pixels, and 10 % of that on each side
    assert (cols.start, cols.stop) == (95, 155)
    assert (rows.start, rows.stop) == (36, 84)
    rows, cols = _window(transform, 200, 100, wkt, wkt, (1800, -500, 9000, 500))
    assert (cols.stop, rows.stop) == (200, 100)
    assert windowOver(transform, 200, 100, wkt, wkt, (5000, 0, 6000, 900)) is None


def test_the_window_comes_through_another_projection():
    geographic = CRS.from_user_input("+proj=longlat +R=3389500 +no_defs").to_wkt()
    metersPerDegree = math.pi * 3389500 / 180
    transform = Affine(0.01, 0, 0, 0, -0.01, 1)  # 1° × 2°, 0.01° pixels
    side = 0.5 * metersPerDegree
    rows, cols = _window(
        transform, 200, 100, geographic, MARS.to_wkt(), (0, 0, side, side), 0
    )
    assert (cols.start, cols.stop) == (0, 50)
    assert (rows.start, rows.stop) == (50, 100)


def test_a_hillshade_is_lit_like_the_overlay():
    """The sun whose hillshade of the DEM looks most like the overlay (its
    Pearson r), from the azimuths and elevations tried."""
    heights = _scene() * 300  # meters
    transform = Affine(10, 0, 0, 0, -10, 4000)
    lit = hillshade(heights, 10, azimuth=120, altitude=35)
    azimuth, elevation, r = sunLike(
        heights, 10, transform, lit, transform, MARS.to_wkt()
    )
    assert (azimuth, elevation) == (120, 35) and r > 0.99


def test_too_few_tie_points_arent_trusted(monkeypatch):
    """Two points fit a shift exactly, so their RMS says nothing: fewer than
    MIN_TIE_POINTS is an error for the user."""
    scene = _scene()
    reference = asRaster(scene[..., None], Affine(10, 0, 0, 0, -10, 4000), MARS)
    moving = asRaster(
        scene[50:300, 80:330, None], Affine(10, 0, 860, 0, -10, 3460), MARS
    )
    assert align(reference, moving).points >= MIN_TIE_POINTS
    tiePointsOf = MosaicProject.tiePointsOf
    monkeypatch.setattr(
        MosaicProject,
        "tiePointsOf",
        lambda self, key: tiePointsOf(self, key)[: MIN_TIE_POINTS - 1],
    )
    with pytest.raises(AlignmentError, match="too few to trust"):
        align(reference, moving)


def test_a_hillshades_sun_is_kept_with_the_alignment():
    alignment = Alignment(sun=(270.0, 35.0, 0.79))
    assert Alignment.fromState(alignment.state()).sun == (270.0, 35.0, 0.79)
    assert Alignment.fromState(Alignment().state()).sun is None
    assert Alignment.fromState({}).sun is None  # a session from before
