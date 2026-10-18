import numpy as np
import pyqtgraph as pg
import pytest
from PyQt6.QtWidgets import QTableWidgetItem

from varda.common.entities import VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    RatioSelection,
)
from varda.workspaces.crism_ratio.blandness_readout import BlandnessReadout
from varda.workspaces.crism_ratio.ratio_plots import PlotShape, RatioPlots
from varda.workspaces.ratio_explorer import RatioComponents

WAVELENGTHS = np.linspace(1000, 2500, 60)


def image(wavelengths=WAVELENGTHS, **metadata) -> VardaRaster:
    bands = len(WAVELENGTHS)
    source = ArrayDataSource(
        np.full((4, 4, bands), 0.3, np.float32),
        wavelengths=wavelengths,
        wavelengthUnits="nm" if wavelengths is not None else "",
        extraMetadata=metadata,
    )
    return VardaRaster(source, name="cube")


def components(
    level: float, slope: float = 0.0, denominator=None, cube=None
) -> RatioComponents:
    denominator = (
        np.full(WAVELENGTHS.shape, 0.3) if denominator is None else denominator
    )
    numerator = level * (1 + slope * (WAVELENGTHS - 1000) / 1000)
    return RatioComponents(
        image() if cube is None else cube,
        WAVELENGTHS,
        numerator,
        denominator,
        numerator / denominator,
        RatioSelection(None, None),
    )


def cell(readout: BlandnessReadout, row: int, column: int) -> QTableWidgetItem:
    item = readout.table.item(row, column)
    assert item is not None
    return item


def curveY(plot) -> np.ndarray:
    assert len(plot.plots) == 1
    return plot.plots[0].data()[1]


def named(plot, name: str) -> np.ndarray:
    """The y values of the plot's curve with legend name ``name``."""
    (curve,) = [c for c in plot.plots if c.plotDataItem.name() == name]
    return curve.data()[1]


def makePlots(qtbot) -> RatioPlots:
    plots = RatioPlots()
    for panel in (plots.spectraPanel, plots.ratioPanel, plots.readout):
        qtbot.addWidget(panel)
    return plots


def test_show_components_replaces_curves_and_refits(qtbot):
    plots = makePlots(qtbot)
    plots.showComponents(components(0.2))
    plots.showComponents(components(5.0))
    assert len(plots.spectraPlot.plots) == 2
    np.testing.assert_allclose(named(plots.spectraPlot, "Numerator").max(), 5.0)
    np.testing.assert_allclose(named(plots.spectraPlot, "Denominator").max(), 0.3)
    np.testing.assert_allclose(curveY(plots.ratioPlot).max(), 5.0 / 0.3)
    for plot in (plots.spectraPlot, plots.ratioPlot):
        ys = np.concatenate([c.data()[1] for c in plot.plots])
        low, high = plot.viewBox.viewRange()[1]
        assert low <= ys.min() and ys.max() <= high
        # refit: not zoomed out to the old, much smaller or larger scale
        assert high < 3 * ys.max() + 1


def test_x_axes_are_linked(qtbot):
    plots = makePlots(qtbot)
    plots.showComponents(components(1.0))
    plots.spectraPlot.viewBox.setXRange(1200, 1500, padding=0)
    for plot in (plots.numeratorPlot, plots.ratioPlot):
        np.testing.assert_allclose(plot.viewBox.viewRange()[0], [1200, 1500])


def test_separate_splits_the_spectra_into_two_plots_and_back(qtbot):
    plots = makePlots(qtbot)
    plots.spectraPanel.resize(400, 800)
    plots.spectraPanel.show()
    qtbot.waitExposed(plots.spectraPanel)
    plots.showComponents(components(2.0))
    plots.spectraPlot.viewBox.setXRange(1200, 1500, padding=0)
    assert plots.numeratorBox.isHidden()

    plots.setSeparate(True)
    assert plots.numeratorBox.isVisible()
    np.testing.assert_allclose(curveY(plots.numeratorPlot).max(), 2.0)
    np.testing.assert_allclose(named(plots.spectraPlot, "Denominator").max(), 0.3)
    assert len(plots.spectraPlot.plots) == 1
    # the zoom is kept
    np.testing.assert_allclose(plots.numeratorPlot.viewBox.viewRange()[0], [1200, 1500])

    plots.setSeparate(False)
    assert plots.numeratorBox.isHidden()
    assert plots.numeratorPlot.plots == []
    assert len(plots.spectraPlot.plots) == 2


@pytest.mark.parametrize(
    ("shape", "aspect"), [(PlotShape.SQUARE, 1.0), (PlotShape.TALL, 0.75)]
)
def test_a_shape_keeps_the_plots_aspect_as_its_panel_is_resized(qtbot, shape, aspect):
    plots = makePlots(qtbot)
    panel = plots.ratioPanel
    panel.show()
    plots.ratioShape.setShape(shape)
    for width, height in ((600, 400), (300, 700), (500, 500)):
        panel.resize(width, height)
        qtbot.wait(1)
        plot = plots.ratioPlot
        assert plot.width() / plot.height() == pytest.approx(aspect, abs=0.01)
        assert plot.width() <= width and plot.height() <= height
        # as large as fits, centered
        assert plot.width() == width or plot.height() == height
        assert plot.x() == (width - plot.width()) // 2
    plots.ratioShape.setShape(PlotShape.FREE)
    assert plots.ratioPlot.size() == panel.size()


def test_choosing_a_shape_from_the_menu(qtbot):
    plots = makePlots(qtbot)
    (tall,) = [
        a for a in plots.spectraShape._group.actions() if a.text() == "Tall (3:4)"
    ]
    tall.trigger()
    assert plots.spectraShape.shape is PlotShape.TALL
    assert plots.numeratorBox.shape is PlotShape.TALL  # both of the dock's plots
    assert plots.spectraShape.text().startswith("Shape: Tall")


def test_flat_spectrum_passes_every_window(qtbot):
    readout = BlandnessReadout()
    qtbot.addWidget(readout)
    result = readout.setSpectrum(WAVELENGTHS, np.full(WAVELENGTHS.shape, 0.3))
    assert result.passed
    assert readout.table.rowCount() == 2  # Generic's two windows
    assert all(cell(readout, r, 5).text() == "✓" for r in range(2))
    assert cell(readout, 0, 0).text() == "1.05–1.7 µm"
    assert cell(readout, 0, 1).text() == "1050–1700"
    assert "bland" in readout.verdictLabel.text().lower()


def _withBand(depth: float, center: float = 2310) -> np.ndarray:
    return 0.3 * (1 - depth * np.exp(-0.5 * ((WAVELENGTHS - center) / 40) ** 2))


def test_the_readout_shows_where_the_dip_is_and_how_deep(qtbot):
    readout = BlandnessReadout()
    qtbot.addWidget(readout)
    readout.checkCombo.setCurrentText("Fe/Mg phyllosilicates")
    result = readout.setSpectrum(WAVELENGTHS, _withBand(0.08))
    assert not result.passed
    rows = [cell(readout, r, 0).text() for r in range(readout.table.rowCount())]
    row = rows.index("2.3 µm Fe/Mg-OH")
    window = result.windows[row]
    assert window.dipNm == pytest.approx(2310, abs=30)
    assert (
        cell(readout, row, 2).text()
        == f"{100 * window.depth:.1f} % at {window.dipNm:.0f} nm"
    )
    assert cell(readout, row, 3).text() == "3.0 %"  # the floor: a quiet spectrum
    assert cell(readout, row, 5).text() == "✗"
    assert "2.3 µm Fe/Mg-OH" in readout.verdictLabel.text()


def test_editing_a_limit_reevaluates(qtbot):
    readout = BlandnessReadout()
    qtbot.addWidget(readout)
    sloped = 0.3 * (1 + 0.5 * (WAVELENGTHS - 1000) / 1000)
    readout.setSpectrum(WAVELENGTHS, sloped)
    assert readout.lastResult.passed
    verdicts = []
    readout.sigVerdictChanged.connect(verdicts.append)
    readout.editors["maxSlope"].setValue(0.2)
    assert verdicts and not verdicts[-1].passed
    assert readout.limitOverrides() == {"maxSlope": pytest.approx(0.2)}
    readout.editors["floor"].setValue(5.0)  # percent
    assert readout.limits().floor == pytest.approx(0.05)
    readout.resetLimits()
    assert readout.limitOverrides() == {}
    assert readout.editors["maxSlope"].value() == pytest.approx(0.75)
    assert readout.lastResult.passed


def test_limits_from_a_session_out_of_range_are_ignored(qtbot):
    readout = BlandnessReadout()
    qtbot.addWidget(readout)
    readout.setLimitOverrides({"floor": 0.02, "maxSlope": -1, "Slope": 0.5})
    assert readout.limitOverrides() == {"floor": 0.02}
    assert readout.editors["floor"].value() == pytest.approx(2.0)


def test_limits_are_kept_when_the_check_changes(qtbot):
    readout = BlandnessReadout()
    qtbot.addWidget(readout)
    readout.setSpectrum(WAVELENGTHS, np.full(WAVELENGTHS.shape, 0.3))
    readout.editors["noiseMultiple"].setValue(5.0)
    readout.checkCombo.setCurrentText("Olivine")
    assert readout.limits().noiseMultiple == 5.0
    assert [w.window.name for w in readout.lastResult.windows] == [
        "1 µm olivine",
        "2 µm pyroxene",
    ]


def test_the_co2_range_can_be_included(qtbot):
    readout = BlandnessReadout()
    qtbot.addWidget(readout)
    co2 = _withBand(0.1, center=2030)
    assert readout.setSpectrum(WAVELENGTHS, co2).passed
    readout.co2Check.setChecked(False)
    assert not readout.lastResult.passed
    assert "1950–2100" not in readout.describe()


def test_the_verdict_names_each_failing_windows_dip_and_limit(qtbot):
    readout = BlandnessReadout()
    qtbot.addWidget(readout)
    readout.checkCombo.setCurrentText("Fe/Mg phyllosilicates")
    result = readout.setSpectrum(WAVELENGTHS, _withBand(0.08))
    (w,) = [w for w in result.windows if not w.passed]
    assert readout.verdictLabel.text() == (
        f"✗ Not bland: 2.3 µm Fe/Mg-OH, {100 * w.depth:.1f} % dip at "
        f"{w.dipNm:.0f} nm (limit 3.0 %)"
    )
    sloped = 0.3 * (1 + 0.5 * (WAVELENGTHS - 1000) / 1000)
    readout.checkCombo.setCurrentText("Generic")
    readout.editors["maxSlope"].setValue(0.2)
    result = readout.setSpectrum(WAVELENGTHS, sloped)
    first = result.windows[0]
    assert readout.verdictLabel.text().startswith(
        f"✗ Not bland: 1.05–1.7 µm, slope {first.slope:.2f} /µm (limit 0.2); "
    )


def test_the_details_start_folded(qtbot):
    readout = BlandnessReadout()
    qtbot.addWidget(readout)
    readout.show()
    readout.setSpectrum(WAVELENGTHS, np.full(WAVELENGTHS.shape, 0.3))
    assert not readout.detailsVisible() and readout.table.isHidden()
    readout.detailsButton.click()
    assert readout.detailsVisible() and readout.table.isVisible()


def test_the_denominator_plot_marks_each_failing_windows_dip(qtbot):
    panel = makePlots(qtbot)
    panel.readout.checkCombo.setCurrentText("Fe/Mg phyllosilicates")
    panel.showComponents(components(1.0, denominator=_withBand(0.08)))
    failing = [w for w in panel.readout.lastResult.windows if not w.passed]
    assert [w.window.name for w in failing] == ["2.3 µm Fe/Mg-OH"]
    regions = [i for i in panel.dipMarkers if isinstance(i, pg.LinearRegionItem)]
    markers = [i for i in panel.dipMarkers if isinstance(i, pg.ScatterPlotItem)]
    labels = [i for i in panel.dipMarkers if isinstance(i, pg.TextItem)]
    assert len(regions) == len(markers) == len(labels) == 1
    assert regions[0].getRegion() == (2120, 2400)
    x, _ = markers[0].getData()
    assert x[0] == pytest.approx(failing[0].dipNm)
    assert labels[0].toPlainText().startswith("2.3 µm Fe/Mg-OH: ")
    for item in panel.dipMarkers:
        assert item in panel.spectraPlot.plotItem.items
    # they stay on the denominator's plot when the spectra are separate
    panel.setSeparate(True)
    assert all(i in panel.spectraPlot.plotItem.items for i in panel.dipMarkers)
    # a bland denominator clears them
    panel.showComponents(components(1.0))
    assert panel.dipMarkers == []


@pytest.mark.parametrize("width", [320, 700])
def test_the_blandness_strip_fits_a_laptop(qtbot, width):
    readout = BlandnessReadout()
    qtbot.addWidget(readout)
    readout.checkCombo.setCurrentText("Fe/Mg phyllosilicates")
    readout.setSpectrum(WAVELENGTHS, _withBand(0.08))
    readout.setDetailsVisible(True)
    readout.resize(width, readout.heightForWidth(width))
    readout.show()
    qtbot.waitExposed(readout)
    assertNothingClipped(readout)


def test_bad_bands_are_left_out_of_the_readout_and_the_plots(qtbot):
    bad = 30
    bbl = [0 if i == bad else 1 for i in range(len(WAVELENGTHS))]
    denominator = np.full(WAVELENGTHS.shape, 0.3)
    denominator[bad] = 0.01  # a deep spike in the bad band only
    panel = makePlots(qtbot)
    panel.showComponents(components(1.0, denominator=denominator, cube=image(bbl=bbl)))
    assert panel.readout.lastResult.passed
    for y in (
        named(panel.spectraPlot, "Numerator"),
        named(panel.spectraPlot, "Denominator"),
        curveY(panel.ratioPlot),
    ):
        assert np.isnan(y[bad]) and np.isfinite(np.delete(y, bad)).all()


def test_without_wavelengths_the_readout_says_so(qtbot):
    panel = makePlots(qtbot)
    panel.showComponents(components(1.0))  # a verdict first, to see it replaced
    panel.showComponents(components(1.0, cube=image(wavelengths=None)))
    assert panel.readout.lastResult.windows == ()
    assert "wavelengths" in panel.readout.verdictLabel.text()
    assert panel.readout.table.rowCount() == 0
    np.testing.assert_allclose(
        panel.spectraPlot.plots[0].data()[0], np.arange(1, len(WAVELENGTHS) + 1)
    )


def test_the_plot_sidebars_start_folded(qtbot):
    panel = makePlots(qtbot)
    for plot in (panel.spectraPlot, panel.numeratorPlot, panel.ratioPlot):
        assert not plot.isSidebarVisible()
