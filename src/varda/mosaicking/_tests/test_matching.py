"""Sub-pixel matching: phase correlation, then the phase-plane refinement
(Leprince et al. 2007), on planted shifts."""

import numpy as np
import pytest
from scipy import ndimage

from varda.mosaicking.matching import refineShift, tileShift

SIZE = 64


def _texture(seed: int = 0, n: int = 256) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return ndimage.gaussian_filter(rng.normal(size=(n, n)), 2.0)


def _shifted(image: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """The image with its content moved by (dx, dy) pixels."""
    return np.real(np.fft.ifft2(ndimage.fourier_shift(np.fft.fft2(image), (dy, dx))))


def _pair(dx: float, dy: float, noise: float = 0.0, seed: int = 0):
    big = _texture(seed)
    moved = _shifted(big, dx, dy)
    rng = np.random.default_rng(seed + 1)
    window = slice(96, 96 + SIZE)
    ref = big[window, window] + rng.normal(scale=noise, size=(SIZE, SIZE))
    mov = moved[window, window] + rng.normal(scale=noise, size=(SIZE, SIZE))
    return ref, mov


@pytest.mark.parametrize(
    ("dx", "dy"), [(0.0, 0.0), (2.0, -3.0), (1.37, -0.62), (-4.81, 2.25)]
)
def test_phase_correlation_finds_the_shift(dx, dy):
    ref, mov = _pair(dx, dy)
    shift, quality = tileShift(ref, mov)
    assert np.allclose(shift, (dx, dy), atol=0.15)
    assert quality > 0.9


@pytest.mark.parametrize(("dx", "dy"), [(0.31, -0.18), (-0.47, 0.42), (0.05, 0.0)])
def test_the_phase_plane_fit_reaches_a_fiftieth_of_a_pixel(dx, dy):
    ref, mov = _pair(dx, dy)
    shift, snr = refineShift(ref, mov, initial=(0.0, 0.0))
    assert np.allclose(shift, (dx, dy), atol=0.02)
    assert snr > 0.9


def test_the_phase_plane_fit_holds_up_in_noise():
    errors = []
    for seed in range(5):
        ref, mov = _pair(0.27, -0.33, noise=0.02, seed=seed)  # texture std ~0.07
        coarse, _ = tileShift(ref, mov)
        shift, snr = refineShift(ref, mov, initial=coarse)
        errors.append(np.hypot(shift[0] - 0.27, shift[1] + 0.33))
        assert 0.3 < snr < 1
    assert np.mean(errors) < 0.05


def test_unrelated_tiles_score_low():
    ref, _ = _pair(0, 0, seed=1)
    other, _ = _pair(0, 0, seed=7)
    _, quality = tileShift(ref, other)
    _, snr = refineShift(ref, other, initial=(0.0, 0.0))
    assert quality < 0.5
    assert snr < 0.7  # random phases score about 0.5 by construction


def test_a_featureless_tile_has_no_signal():
    """A constant tile (a scene's fill, its black border) has no frequency to
    fit: the refinement says so instead of failing."""
    ref, _ = _pair(0, 0, seed=1)
    shift, snr = refineShift(ref, np.zeros_like(ref), initial=(0.25, 0.0))
    assert snr == 0.0 and tuple(shift) == (0.25, 0.0)
