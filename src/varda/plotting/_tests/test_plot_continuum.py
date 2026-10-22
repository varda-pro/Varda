"""Continuum removal as a plot option: every curve, its band and new curves
are shown divided by their upper convex hull, over the whole curve or a
window; the spectra themselves, and what's added to a library, don't
change; the CSV records it."""

import csv

import numpy as np
import pyqtgraph as pg
import pytest

from varda.analysis.spectrum_continuum import spectrumContinuum
from varda.common.spectral_axis import SpectralUnit
from varda.common.vec2 import Vec2
from varda.plotting.plot import RangeMode, VardaPlotWidget
from varda.plotting.plot_export import exportPlot

X = np.arange(1000.0, 2601.0, 20.0)
SLOPE = 0.2 + 0.0001 * (X - 1000)
Y = SLOPE * (1 - 0.3 * np.exp(-(((X - 1900) / 60) ** 2)))


@pytest.fixture
def plot(qtbot):
    widget = VardaPlotWidget()
    qtbot.addWidget(widget)
    widget.resize(800, 400)
    widget.setSpectralUnit(SpectralUnit.NANOMETRE)
    widget.setValueLabel("Value (I/F)")
    return widget


def _shown(curve) -> tuple[np.ndarray, np.ndarray]:
    x, y = curve.plotDataItem.getData()
    return np.asarray(x), np.asarray(y)


def test_turning_it_on_shows_every_curve_divided_by_its_hull(plot):
    curve = plot.plot(X, Y, name="pixel")
    plot.continuumConfig.enabled.set(True)
    x, y = _shown(curve)
    np.testing.assert_array_equal(x, X)
    np.testing.assert_allclose(y, Y / spectrumContinuum(X, Y)[1])
    assert y.max() == pytest.approx(1.0)
    np.testing.assert_array_equal(curve.data()[1], Y)  # its own data stays
    assert plot.plotItem.getAxis("left").labelText == "Value (I/F), continuum removed"

    plot.continuumConfig.enabled.set(False)
    np.testing.assert_array_equal(_shown(curve)[1], Y)
    assert plot.plotItem.getAxis("left").labelText == "Value (I/F)"


def test_curves_added_later_and_a_bands_edges_follow(plot):
    plot.continuumConfig.enabled.set(True)
    std = np.full(X.size, 0.01)
    curve = plot.plotWithFill(
        X, Y, Y - std, Y + std, pg.mkBrush("#80808080"), std=std, name="box"
    )
    continuum = spectrumContinuum(X, Y)[1]
    np.testing.assert_allclose(_shown(curve)[1], Y / continuum)
    upper, lower = curve.bandItems[:2]
    np.testing.assert_allclose(upper.getData()[1], (Y + std) / continuum)
    np.testing.assert_allclose(lower.getData()[1], (Y - std) / continuum)
    np.testing.assert_allclose(curve.shownSpread()[0], std / continuum)


def test_a_window_shows_only_its_samples(plot):
    curve = plot.plot(X, Y, name="pixel")
    plot.plotItem.setXRange(1800, 2000, padding=0)
    plot.removeContinuumInView()
    config = plot.continuumConfig
    assert config.enabled.value and config.limited.value
    low, high = plot.continuumWindow()
    x, y = _shown(curve)
    assert x.min() >= low and x.max() <= high
    assert y[0] == pytest.approx(1.0) and y[-1] == pytest.approx(1.0)

    config.limited.set(False)  # the whole curve again
    np.testing.assert_array_equal(_shown(curve)[0], X)


def test_the_library_gets_the_spectrum_not_the_removed_one(plot):
    curve = plot.plot(X, Y, name="pixel")
    plot.continuumConfig.enabled.set(True)
    spectrum = plot.librarySpectrumFor(curve)
    assert spectrum is not None
    np.testing.assert_allclose(spectrum.reflectance, Y)


def test_a_manual_range_is_refit_to_the_new_values(plot):
    plot.plot(X, Y, name="pixel")
    plot.viewConfig.rangeMode.set(RangeMode.MANUAL)
    plot.rangeConfig.viewRangeY.set(Vec2(0.0, 0.1))
    plot.continuumConfig.enabled.set(True)
    low, high = plot.viewBox.viewRange()[1]
    assert low < 0.7 and high > 1.0


def test_the_csv_writes_the_removed_values_and_the_continuum(plot, tmp_path):
    std = np.full(X.size, 0.01)
    plot.plotWithFill(
        X, Y, Y - std, Y + std, pg.mkBrush("#80808080"), std=std, name="box"
    )
    plot.continuumConfig.window.set(Vec2(1800.0, 2000.0))
    plot.continuumConfig.limited.set(True)
    plot.continuumConfig.enabled.set(True)
    lines = exportPlot(plot, tmp_path / "s.csv").read_text().splitlines()
    assert lines[0].startswith("# Continuum removed:")
    assert "from 1800 to 2000 (wavelength_nm)" in lines[0]
    rows = list(csv.reader(lines[1:]))
    assert rows[0][1:] == ["box", "box continuum", "box std"]
    values = np.array(rows[1:], dtype=float)
    inside = (X >= 1800) & (X <= 2000)
    np.testing.assert_allclose(values[:, 0], X[inside])
    np.testing.assert_allclose(values[:, 1] * values[:, 2], Y[inside], rtol=1e-9)


def test_a_library_spectrum_overlays_the_removed_curve(plot):
    plot.plot(X, Y, name="pixel")
    plot.continuumConfig.enabled.set(True)
    reference = plot.addReferenceSpectrum("mineral", X, 3 * Y)  # same shape
    _, shown = reference.displayedData()
    _, target = plot.plots[0].displayedData()
    np.testing.assert_allclose(shown, target, atol=1e-9)
