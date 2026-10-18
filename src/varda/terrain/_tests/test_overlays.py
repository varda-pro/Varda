"""Overlays: brought into the DEM's projection, aligned with the mosaic core,
and draped as a texture over their footprint."""

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS

from varda.geo._tests.fixtures import MARS_EQC_PDS3_WKT
from varda.mosaicking.transforms import TransformModel
from varda.terrain.overlays import (
    Alignment,
    AlignmentError,
    align,
    asRaster,
    bandLabel,
    bestBand,
    drape,
    hillshade,
    toProjection,
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
