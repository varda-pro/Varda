import numpy as np
import pytest
from PyQt6.QtWidgets import QTableWidgetItem

from varda.common.entities import VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    RatioSelection,
)
from varda.workspaces.crism_ratio.blandness_readout import BlandnessReadout
from varda.workspaces.crism_ratio.ratio_plots import RatioPlotsPanel
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


def test_show_components_replaces_curves_and_refits(qtbot):
    panel = RatioPlotsPanel()
    qtbot.addWidget(panel)
    panel.showComponents(components(0.2))
    panel.showComponents(components(5.0))
    for plot, expected in (
        (panel.numeratorPlot, 5.0),
        (panel.denominatorPlot, 0.3),
        (panel.ratioPlot, 5.0 / 0.3),
    ):
        np.testing.assert_allclose(curveY(plot).max(), expected)
        low, high = plot.viewBox.viewRange()[1]
        assert low <= curveY(plot).min() and curveY(plot).max() <= high
        # refit: not zoomed out to the old, much smaller or larger scale
        assert high < 3 * curveY(plot).max() + 1


def test_x_axes_are_linked(qtbot):
    panel = RatioPlotsPanel()
    qtbot.addWidget(panel)
    panel.showComponents(components(1.0))
    panel.numeratorPlot.viewBox.setXRange(1200, 1500, padding=0)
    for plot in (panel.denominatorPlot, panel.ratioPlot):
        np.testing.assert_allclose(plot.viewBox.viewRange()[0], [1200, 1500])


def test_flat_spectrum_passes_every_check(qtbot):
    readout = BlandnessReadout()
    qtbot.addWidget(readout)
    result = readout.setSpectrum(WAVELENGTHS, np.full(WAVELENGTHS.shape, 0.3))
    assert result.passed
    assert readout.table.rowCount() == 2
    assert all(cell(readout, r, 3).text() == "✓" for r in range(2))
    assert "bland" in readout.verdictLabel.text().lower()


def test_editing_a_limit_reevaluates(qtbot):
    readout = BlandnessReadout()
    qtbot.addWidget(readout)
    sloped = 0.3 * (1 + 0.5 * (WAVELENGTHS - 1000) / 1000)
    readout.setSpectrum(WAVELENGTHS, sloped)
    verdicts = []
    readout.sigVerdictChanged.connect(verdicts.append)
    slopeRow = 1
    assert cell(readout, slopeRow, 3).text() == "✗"
    cell(readout, slopeRow, 2).setText("1.0")
    assert cell(readout, slopeRow, 3).text() == "✓"
    assert verdicts and verdicts[-1].passed


def test_bad_limit_text_is_restored(qtbot):
    readout = BlandnessReadout()
    qtbot.addWidget(readout)
    readout.setSpectrum(WAVELENGTHS, np.full(WAVELENGTHS.shape, 0.3))
    before = cell(readout, 0, 2).text()
    cell(readout, 0, 2).setText("lots")
    assert cell(readout, 0, 2).text() == before


def test_preset_without_hypyrameter_says_so(qtbot):
    def missing(spectrum, wavelengths, names):
        raise ImportError("no HyPyRameter")

    readout = BlandnessReadout(parameters=missing)
    qtbot.addWidget(readout)
    readout.setSpectrum(WAVELENGTHS, np.full(WAVELENGTHS.shape, 0.3))
    readout.checkCombo.setCurrentText("Olivine")
    assert readout.currentCheckName() == "Olivine"
    assert "needs HyPyRameter" in readout.verdictLabel.text()
    assert readout.table.rowCount() == 0


def test_preset_limits_are_editable(qtbot):
    values = {"OLINDEX3": 0.05, "LCPINDEX2": 0.0, "HCPINDEX2": 0.0}
    readout = BlandnessReadout(
        parameters=lambda s, w, names: np.array([values[n] for n in names])
    )
    qtbot.addWidget(readout)
    readout.setSpectrum(WAVELENGTHS, np.full(WAVELENGTHS.shape, 0.3))
    readout.checkCombo.setCurrentText("Olivine")
    assert not readout.lastResult.passed
    cell(readout, 0, 2).setText("0.1")
    assert readout.lastResult.passed


@pytest.mark.parametrize("width", [640, 1280])
def test_panel_fits_a_laptop_screen(qtbot, width):
    panel = RatioPlotsPanel()
    qtbot.addWidget(panel)
    panel.resize(width, 800)
    panel.show()
    qtbot.waitExposed(panel)
    panel.showComponents(components(1.0))
    assertNothingClipped(panel)
    for plot in (panel.numeratorPlot, panel.denominatorPlot, panel.ratioPlot):
        assert plot.height() >= 120


def test_bad_bands_are_left_out_of_the_readout_and_the_plots(qtbot):
    bad = 30
    bbl = [0 if i == bad else 1 for i in range(len(WAVELENGTHS))]
    denominator = np.full(WAVELENGTHS.shape, 0.3)
    denominator[bad] = 0.01  # a deep spike in the bad band only
    panel = RatioPlotsPanel()
    qtbot.addWidget(panel)
    panel.showComponents(components(1.0, denominator=denominator, cube=image(bbl=bbl)))
    assert panel.readout.lastResult.passed
    for plot in (panel.numeratorPlot, panel.denominatorPlot, panel.ratioPlot):
        y = curveY(plot)
        assert np.isnan(y[bad]) and np.isfinite(np.delete(y, bad)).all()


def test_without_wavelengths_the_readout_says_so(qtbot):
    panel = RatioPlotsPanel()
    qtbot.addWidget(panel)
    panel.showComponents(components(1.0))  # a verdict first, to see it replaced
    panel.showComponents(components(1.0, cube=image(wavelengths=None)))
    assert panel.readout.lastResult.checks == ()
    assert "wavelengths" in panel.readout.verdictLabel.text()
    assert panel.readout.table.rowCount() == 0
    np.testing.assert_allclose(
        panel.numeratorPlot.plots[0].data()[0], np.arange(1, len(WAVELENGTHS) + 1)
    )


def test_the_ratio_plot_starts_as_wide_as_the_others(qtbot):
    panel = RatioPlotsPanel()
    qtbot.addWidget(panel)
    panel.resize(500, 800)
    panel.show()
    qtbot.waitExposed(panel)
    assert not panel.ratioPlot.isSidebarVisible()
    panel.showComponents(components(1.0))
    widths = [p.viewBox.width() for p in (panel.numeratorPlot, panel.ratioPlot)]
    assert widths[1] == pytest.approx(widths[0], rel=0.1)
