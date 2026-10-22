"""One spectrum's continuum: the upper convex hull of its finite samples,
within a window if given, agreeing with the image analysis."""

import numpy as np
import pytest

from varda.analysis.continuum import continuumRemovedCube
from varda.analysis.spectrum_continuum import spectrumContinuum

X = np.arange(1000.0, 2601.0, 10.0)
SLOPE = 0.2 + 0.0001 * (X - 1000)  # a sloped continuum
DIP = 1 - 0.3 * np.exp(-(((X - 1900) / 60) ** 2))  # a 30 % deep band
Y = SLOPE * DIP


def test_a_band_on_a_sloped_continuum_comes_out_at_its_depth():
    samples, continuum = spectrumContinuum(X, Y)
    np.testing.assert_array_equal(samples, np.arange(X.size))
    np.testing.assert_allclose(continuum, SLOPE, rtol=1e-3)
    removed = Y / continuum
    assert removed.max() == pytest.approx(1.0)
    assert np.argmin(removed) == np.flatnonzero(X == 1900)[0]
    assert 1 - removed.min() == pytest.approx(0.3, abs=0.005)


def test_a_window_keeps_only_its_samples_and_fits_them_alone():
    samples, continuum = spectrumContinuum(X, Y, window=(2000.0, 1800.0))
    np.testing.assert_array_equal(X[samples], X[(X >= 1800) & (X <= 2000)])
    # the hull runs between the window's ends, not the whole curve's
    ends = Y[samples][[0, -1]]
    np.testing.assert_allclose(continuum[[0, -1]], ends)


def test_missing_samples_stay_out_of_the_hull():
    y = Y.copy()
    y[X == 1500] = np.nan  # gaps
    y[X == 2200] = np.nan
    samples, continuum = spectrumContinuum(X, y)
    assert samples.size == X.size  # still shown, as gaps
    np.testing.assert_allclose(continuum, SLOPE, rtol=1e-3)
    assert np.isnan(spectrumContinuum(X, np.full(X.size, np.nan))[1]).all()


def test_the_hull_can_be_drawn_against_another_axis():
    wavenumbers = 1e7 / X  # cm⁻¹, descending
    samples, continuum = spectrumContinuum(wavenumbers, Y, hullX=X)
    np.testing.assert_allclose(continuum, spectrumContinuum(X, Y)[1])


def test_it_agrees_with_the_continuum_removal_analysis():
    rng = np.random.default_rng(4)
    y = Y + rng.normal(0, 0.002, X.size)
    _, continuum = spectrumContinuum(X, y)
    cube = continuumRemovedCube(y.reshape(1, 1, -1), X)
    np.testing.assert_allclose(y / continuum, cube[0, 0], rtol=1e-12)
