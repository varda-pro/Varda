import math

import numpy as np

from varda.image_rendering.stretch_algorithms import (
    mono_hist_percentiles_numba,
    rgb_hist_percentiles_numba,
)


def at_or_below(values: np.ndarray, p: float) -> float:
    """The smallest value with at least p % of the values at or below it."""
    ordered = np.sort(values.ravel())
    return float(ordered[max(math.ceil(p / 100 * ordered.size) - 1, 0)])


def test_histogram_percentiles_are_low_by_at_most_one_bin():
    rng = np.random.default_rng(0)
    band = rng.normal(100.0, 20.0, (200, 300, 1))
    binWidth = (band.max() - band.min()) / 1024
    lo, hi = mono_hist_percentiles_numba(band, 1, 99)
    for got, p in ((lo, 1), (hi, 99)):
        exact = at_or_below(band, p)
        assert exact - binWidth <= got <= exact


def test_rgb_histogram_bins_span_all_three_bands():
    rng = np.random.default_rng(1)
    rgb = np.stack([rng.uniform(0, s, (100, 100)) for s in (1, 10, 100)], axis=2)
    binWidth = (rgb.max() - rgb.min()) / 1024
    lows, highs = rgb_hist_percentiles_numba(rgb, 1, 99)
    for c in range(3):
        for got, p in ((lows[c], 1), (highs[c], 99)):
            exact = at_or_below(rgb[..., c], p)
            assert exact - binWidth <= got <= exact


def test_one_value_throughout_is_every_percentile():
    assert mono_hist_percentiles_numba(np.full((5, 5, 1), 7.0), 2, 98) == (7.0, 7.0)
    lows, highs = rgb_hist_percentiles_numba(np.ones((5, 5, 3)), 2, 98)
    assert list(lows) == [1, 1, 1] and list(highs) == [1, 1, 1]
