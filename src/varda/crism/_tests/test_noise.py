import numpy as np
import pytest

from varda.crism._tests.real_data import realChunk
from varda.crism._tests.synthetic import makeSyntheticCube
from varda.crism.noise import estimateNoise


def _nearInfrared(truth) -> np.ndarray:
    wavelengths = np.append(np.median(truth.columnWavelengths[:, :-1], axis=0), np.nan)
    return (wavelengths >= 1000.0) & (wavelengths <= 2600.0)


def _ratio(window: tuple[float, float] = (1000.0, 2600.0), **kwargs) -> float:
    """median(model σ / injected σ) in a window, both at the observed signal.

    ``truth.surface`` is before the atmosphere, and the injected σ follows the
    signal after it (about 22% lower inside the CO2 band), so the model is
    evaluated on the cube (its noise is 1% of the signal).
    """
    cube, truth = makeSyntheticCube(lines=128, spikeRate=0.0, **kwargs)
    model = estimateNoise(cube)
    wavelengths = np.append(np.median(truth.columnWavelengths[:, :-1], axis=0), np.nan)
    inWindow = (wavelengths >= window[0]) & (wavelengths <= window[1])
    ratio = model.sigma(cube) / truth.noiseSigma
    return float(np.nanmedian(ratio[:, :, inWindow]))


def test_recovers_injected_sigma():
    assert abs(_ratio() - 1.0) < 0.03


def test_stripes_do_not_inflate_sigma():
    assert abs(_ratio(stripeAmplitude=0.02) - 1.0) < 0.03


def test_shot_noise_slope():
    cube, truth = makeSyntheticCube(lines=128, spikeRate=0.0)
    model = estimateNoise(cube)
    band = _nearInfrared(truth)
    # sigma² = a + b·R: recover the injected b from the truth by regression
    # over the noise-free signal.
    r = truth.surface[:, :, band]
    var = truth.noiseSigma[:, :, band] ** 2
    rc = r - r.mean(axis=(0, 1))
    injected = (rc * (var - var.mean(axis=(0, 1)))).sum(axis=(0, 1)) / (rc**2).sum(
        axis=(0, 1)
    )
    assert abs(np.median(model.shot[band]) / np.median(injected) - 1.0) < 0.10


def test_all_nan_band_is_nan_without_warnings():
    cube, _ = makeSyntheticCube(lines=64)
    model = estimateNoise(cube)
    assert np.isnan(model.shot[-1]) and np.isnan(model.floor[:, -1]).all()


def test_sigma_unbiased_inside_co2_band():
    assert abs(_ratio((1950.0, 2070.0)) - 1.0) < 0.05


def test_edges_get_group_floor_and_short_cube_raises():
    cube, _ = makeSyntheticCube(lines=64, spikeRate=0.0)
    model = estimateNoise(cube)
    assert (
        np.isfinite(model.floor[0, :-1]).all()
        and np.isfinite(model.floor[-1, :-1]).all()
    )
    assert np.array_equal(model.floor[0], model.floor[1], equal_nan=True)
    with pytest.raises(ValueError, match="at least 3 lines"):
        estimateNoise(cube[:2])


def test_sparse_column_group_gets_a_finite_sigma():
    original, _ = makeSyntheticCube(lines=64, spikeRate=0.0)
    cube = original.copy()
    cube[:, 32:48] = np.nan  # column group 2 keeps a few pixels only
    cube[::16, 32:48:6] = original[::16, 32:48:6]
    sigma = estimateNoise(cube).sigma(cube)
    assert np.isfinite(sigma[np.isfinite(cube)]).all()
    cube[:, 32:48] = np.nan  # none at all: the nearest group's floor
    assert np.isfinite(estimateNoise(cube).floor[32:48, :-1]).all()


def test_real_chunk_sigma_is_finite_where_data_are(demoData):
    cube, _ = realChunk(demoData)
    sigma = estimateNoise(cube).sigma(cube)
    assert np.isfinite(sigma[np.isfinite(cube)]).all()
