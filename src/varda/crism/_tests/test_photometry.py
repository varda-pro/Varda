import numpy as np
import pytest

from varda.crism.photometry import fitIncidence, lambert


def _truth(lines: int, samples: int) -> np.ndarray:
    t = np.arange(lines, dtype=float)[:, None]
    x = np.arange(samples, dtype=float)[None, :]
    return 50 + 0.01 * x + 2e-5 * x**2 + 0.02 * t


def test_polynomial_incidence():
    truth = _truth(200, 300)
    quantized = np.round(truth / 0.25) * 0.25
    fit = fitIncidence(quantized)
    lines, samples = np.arange(200)[:, None], np.arange(300)[None, :]
    assert np.abs(fit.evaluate(lines, samples) - truth).max() < 0.02
    assert 0.05 < fit.rmsDeg < 0.1


def test_nan_pixels_are_ignored():
    truth = _truth(100, 100)
    noisy = truth.copy()
    noisy[10:20, 5:50] = np.nan
    fit = fitIncidence(noisy)
    assert (
        np.abs(
            fit.evaluate(np.arange(100)[:, None], np.arange(100)[None, :]) - truth
        ).max()
        < 1e-6
    )


def test_lambert():
    cube, sigma = lambert(np.ones((2, 3, 4)), np.ones((2, 3, 4)), np.full((2, 3), 60.0))
    np.testing.assert_allclose(cube, 2.0)
    np.testing.assert_allclose(sigma, 2.0)


def test_lambert_invalid_incidence_is_nan():
    incidence = np.array([[60.0, np.nan, 90.0, 95.0]])
    cube, sigma = lambert(np.ones((1, 4, 2)), np.ones((1, 4, 2)), incidence)
    assert np.isfinite(cube[0, 0]).all()
    assert np.isnan(cube[0, 1:]).all() and np.isnan(sigma[0, 1:]).all()


def test_chunk_consistency():
    fit = fitIncidence(np.round(_truth(128, 80) / 0.25) * 0.25)
    full = fit.evaluate(np.arange(128)[:, None], np.arange(80)[None, :])
    chunk = fit.evaluate(np.arange(32, 64)[:, None], np.arange(80)[None, :])
    np.testing.assert_array_equal(chunk, full[32:64])


def test_too_few_valid_incidences_raise():
    incidence = np.full((10, 10), np.nan)
    incidence[0, :4] = 40.0  # four pixels for five coefficients
    with pytest.raises(ValueError, match="valid"):
        fitIncidence(incidence)
