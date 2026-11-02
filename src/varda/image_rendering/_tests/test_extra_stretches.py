"""Histogram-equalization, square-root and log stretches."""

import numpy as np
import pytest

from varda.image_rendering.stretch_algorithms import (
    HistogramEqualizationStretch,
    LogStretch,
    SquareRootStretch,
    stretchAlgorithmRegistry,
)


def _skewed(channels=1) -> np.ndarray:
    rng = np.random.default_rng(0)
    data = rng.exponential(1.0, size=(60, 80, channels))  # mostly dark, long tail
    data[0, 0, :] = np.nan
    return data


@pytest.mark.parametrize(
    "cls", [HistogramEqualizationStretch, SquareRootStretch, LogStretch]
)
@pytest.mark.parametrize("channels", [1, 3])
def test_output_is_in_0_1_keeps_nan_and_preserves_order(cls, channels):
    data = _skewed(channels)
    out = cls().apply(data)
    assert out.shape == data.shape
    assert np.isnan(out[0, 0]).all()
    finite = out[np.isfinite(out)]
    assert finite.min() >= 0.0 and finite.max() <= 1.0
    flat, stretched = data[..., 0].ravel(), out[..., 0].ravel()
    keep = np.isfinite(flat)
    order = np.argsort(flat[keep])
    assert np.all(np.diff(stretched[keep][order]) >= -1e-9)  # monotonic


def test_square_root_and_log_brighten_the_dark_end():
    data = _skewed()
    linear = SquareRootStretch().linear(data)
    for cls in (SquareRootStretch, LogStretch):
        out = cls().apply(data)
        assert np.nanmedian(out) > np.nanmedian(linear)


def test_equalization_spreads_values_evenly():
    out = HistogramEqualizationStretch().apply(_skewed())
    deciles = np.nanpercentile(out, [10, 50, 90])
    np.testing.assert_allclose(deciles, [0.1, 0.5, 0.9], atol=0.03)


def test_the_new_stretches_are_offered():
    for name in ("Histogram Equalization", "Square Root", "Logarithmic"):
        assert any(name in key for key in stretchAlgorithmRegistry)


def test_they_report_a_range_for_the_histogram_markers():
    for cls in (HistogramEqualizationStretch, SquareRootStretch, LogStretch):
        stretch = cls()
        stretch.apply(_skewed(3))
        lo, hi = stretch.minMaxVals()
        assert np.all(np.asarray(lo) < np.asarray(hi))
