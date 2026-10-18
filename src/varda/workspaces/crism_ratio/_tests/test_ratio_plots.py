import numpy as np
import pytest
from PyQt6.QtWidgets import QTableWidgetItem

from varda.common.layout_audit import assertNothingClipped
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    RatioSelection,
)
from varda.utilities.debug import generate_random_image
from varda.workspaces.crism_ratio.blandness_readout import BlandnessReadout
from varda.workspaces.crism_ratio.ratio_plots import RatioPlotsPanel
from varda.workspaces.ratio_explorer import RatioComponents

WAVELENGTHS = np.linspace(1000, 2500, 60)


def components(level: float, slope: float = 0.0) -> RatioComponents:
    denominator = np.full(WAVELENGTHS.shape, 0.3)
    numerator = level * (1 + slope * (WAVELENGTHS - 1000) / 1000)
    return RatioComponents(
        generate_random_image((10, 10, 10)),
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
