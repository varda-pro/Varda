"""The VNIR/IR join near 1 µm: found from the band centers, and the VNIR
bands scaled to the IR's level there."""

import numpy as np
import pytest

from varda.analysis.detector_join import (
    MIN_FIT_BANDS,
    findJoin,
    joinScale,
    scaleVnir,
)

# an MTRDR-like band set: VNIR every 6.55 nm to 1010 nm, IR from 1047 nm
VNIR = np.arange(905.5, 1011.0, 6.55)
IR = np.arange(1047.2, 1300.0, 6.55)
CENTERS = np.concatenate([VNIR, IR])


def _spectrum(step: float, slope: float = -2e-4, noise: float = 0.0, seed: int = 0):
    """A straight spectrum through the join with the IR side ``step`` times
    the VNIR side's level."""
    level = 0.25 + slope * (CENTERS - 1028.7)
    level = np.where(CENTERS > 1020, level * step, level)
    return level + np.random.default_rng(seed).normal(0, noise, CENTERS.size)


def test_the_join_is_the_gap_between_the_detectors():
    join = findJoin(CENTERS)
    assert join is not None
    assert join.vnirEndNm == pytest.approx(VNIR[-1])
    assert join.irStartNm == pytest.approx(IR[0])
    assert join.atNm == pytest.approx((VNIR[-1] + IR[0]) / 2)
    assert np.array_equal(join.vnir, CENTERS <= VNIR[-1])
    assert join.vnirFit.sum() >= MIN_FIT_BANDS and join.irFit.sum() >= MIN_FIT_BANDS


def test_bands_in_any_order_are_found():
    order = np.random.default_rng(1).permutation(CENTERS.size)
    join = findJoin(CENTERS[order])
    assert join is not None
    assert np.array_equal(join.vnir, CENTERS[order] <= VNIR[-1])


@pytest.mark.parametrize(
    "centers",
    [IR, VNIR, np.arange(400.0, 2600.0, 6.55)],
    ids=["IR only", "VNIR only", "no gap"],
)
def test_a_cube_on_one_detector_has_no_join(centers):
    assert findJoin(centers) is None


def test_too_few_good_bands_beside_the_gap_means_no_join():
    good = ~((CENTERS > 920) & (CENTERS < 1011))  # the VNIR side's bands bad
    assert findJoin(CENTERS, good) is None


def test_the_step_is_measured_and_removed():
    spectrum = _spectrum(0.98)
    join = findJoin(CENTERS)
    assert join is not None
    scale = joinScale(spectrum, CENTERS, join)
    assert float(scale.factor) == pytest.approx(0.98, abs=1e-12)
    assert float(scale.sigma) == pytest.approx(0.0, abs=1e-12)  # exact lines
    fixed = scaleVnir(spectrum, join, scale.factor)
    assert np.allclose(fixed[~join.vnir], spectrum[~join.vnir])  # IR unchanged
    assert np.allclose(fixed[join.vnir], 0.98 * spectrum[join.vnir])
    assert float(joinScale(fixed, CENTERS, join).factor) == pytest.approx(1.0)


def test_the_standard_error_matches_the_scatter_of_noisy_spectra():
    join = findJoin(CENTERS)
    assert join is not None
    spectra = np.array([_spectrum(0.98, noise=0.002, seed=s) for s in range(400)])
    scale = joinScale(spectra, CENTERS, join)
    assert np.mean(scale.factor) == pytest.approx(0.98, abs=0.002)
    # the reported 1-sigma is the spread of the estimates, within 15 percent
    assert np.median(scale.sigma) == pytest.approx(np.std(scale.factor), rel=0.15)


def test_a_spectrum_without_data_beside_the_join_is_left_as_it_is():
    join = findJoin(CENTERS)
    assert join is not None
    spectrum = _spectrum(0.98)
    spectrum[join.irFit] = np.nan
    scale = joinScale(spectrum, CENTERS, join)
    assert np.isnan(scale.factor) and np.isnan(scale.sigma)
    fixed = scaleVnir(spectrum, join, scale.factor)
    assert np.array_equal(fixed, spectrum, equal_nan=True)


def test_a_cube_of_spectra_is_scaled_pixel_by_pixel():
    join = findJoin(CENTERS)
    assert join is not None
    cube = np.stack([_spectrum(s) for s in (0.97, 0.99, 1.02, 1.0)]).reshape(2, 2, -1)
    scale = joinScale(cube, CENTERS, join)
    assert scale.factor.shape == (2, 2)
    assert np.allclose(scale.factor.ravel(), [0.97, 0.99, 1.02, 1.0])
    fixed = scaleVnir(cube, join, scale.factor)
    assert np.allclose(joinScale(fixed, CENTERS, join).factor, 1.0)
