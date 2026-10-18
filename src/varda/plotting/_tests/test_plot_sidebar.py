"""Layout and behavior of the spectral plot's settings sidebar."""

import numpy as np
import pytest
from PyQt6.QtWidgets import QLabel

from varda.common.layout_audit import assertNothingClipped
from varda.common.ui import SectionBox
from varda.common.vec2 import Vec2
from varda.plotting.pixel_spectra_plot import PixelSpectraPlotWidget
from varda.plotting.plot import (
    DEFAULT_SIDEBAR_WIDTH,
    PLOT_MIN_WIDTH,
    RangeMode,
    VardaPlotWidget,
)


def _sectionTitles(widget: VardaPlotWidget) -> list[str]:
    layout = widget._sidebar
    sections = (layout.itemAt(i).widget() for i in range(layout.count()))
    return [s.title for s in sections if isinstance(s, SectionBox)]


def _placeholder(widget: VardaPlotWidget) -> QLabel | None:
    for label in widget.curveSettingsBox.frame.findChildren(QLabel):
        if "Click a curve" in label.text():
            return label
    return None


def test_sidebar_leads_with_view_and_ends_with_appearance(qtbot):
    widget = VardaPlotWidget()
    qtbot.addWidget(widget)
    assert _sectionTitles(widget) == [
        "View",
        "Markers",
        "Library Spectra",
        "Selected Curve",
        "Appearance",
    ]


def test_pixel_plot_sections_follow_the_view_section(qtbot):
    widget = PixelSpectraPlotWidget()
    qtbot.addWidget(widget)
    assert _sectionTitles(widget)[:2] == ["View", "Pixel Spectra"]


def test_selected_curve_section_shows_placeholder_until_a_curve_is_selected(qtbot):
    widget = VardaPlotWidget()
    qtbot.addWidget(widget)
    curve = widget.plot(np.arange(10.0), np.linspace(0.0, 1.0, 10), name="c")
    assert _placeholder(widget) is not None

    widget.selectPlot(curve)
    assert _placeholder(widget) is None

    widget.deselectPlot()
    assert _placeholder(widget) is not None


def test_fit_to_data_frames_the_curves_and_leaves_auto_range_off(qtbot):
    widget = VardaPlotWidget()
    qtbot.addWidget(widget)
    widget.plot(np.arange(10.0), np.linspace(0.0, 1.0, 10), name="c")
    widget.viewConfig.rangeMode.set(RangeMode.MANUAL)
    widget.rangeConfig.viewRangeX.set(Vec2(4.0, 5.0))
    widget.rangeConfig.viewRangeY.set(Vec2(0.4, 0.5))

    widget.fitToData()

    (xMin, xMax), (yMin, yMax) = widget.viewBox.viewRange()
    assert xMin <= 0.0 and xMax >= 9.0
    assert yMin <= 0.0 and yMax >= 1.0
    assert widget.viewConfig.rangeMode.value is RangeMode.MANUAL
    # manual range boxes reflect the fitted view
    assert widget.rangeConfig.viewRangeX.value.x <= 0.0


def test_sidebar_can_be_collapsed_with_its_toggle(qtbot):
    widget = VardaPlotWidget()
    qtbot.addWidget(widget)
    assert widget.isSidebarVisible()
    widget.sidebarToggle.click()
    assert not widget.isSidebarVisible()
    widget.setSidebarVisible(True)
    assert widget.isSidebarVisible() and widget.sidebarToggle.isChecked()


@pytest.fixture(params=[VardaPlotWidget, PixelSpectraPlotWidget])
def shown(request, qtbot) -> VardaPlotWidget:
    widget = request.param()
    qtbot.addWidget(widget)
    widget.resize(900, 600)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def _dragSidebarTo(widget: VardaPlotWidget, width: int) -> None:
    """Drag the splitter handle as the user does, leaving the sidebar
    ``width`` px wide (within the limits)."""
    splitter = widget.splitter
    handle = splitter.handleWidth()
    splitter.moveSplitter(splitter.width() - width - handle, 1)


def _sidebarWidth(widget: VardaPlotWidget) -> int:
    return widget.splitter.sizes()[1]


def test_the_sidebar_can_be_dragged_narrower_and_wider(shown):
    _dragSidebarTo(shown, 260)
    assert _sidebarWidth(shown) == 260
    _dragSidebarTo(shown, 500)
    assert _sidebarWidth(shown) == 500


def test_the_dragged_width_is_remembered_per_plot_kind(qtbot, shown):
    _dragSidebarTo(shown, 410)

    shown.setSidebarVisible(False)
    assert _sidebarWidth(shown) < 40  # just the arrow
    shown.setSidebarVisible(True)
    assert _sidebarWidth(shown) == 410

    again = type(shown)()
    qtbot.addWidget(again)
    again.resize(900, 600)
    again.show()
    qtbot.waitExposed(again)
    assert _sidebarWidth(again) == 410

    other = (
        VardaPlotWidget
        if isinstance(shown, PixelSpectraPlotWidget)
        else PixelSpectraPlotWidget
    )()
    qtbot.addWidget(other)
    other.resize(900, 600)
    other.show()
    qtbot.waitExposed(other)
    assert _sidebarWidth(other) == DEFAULT_SIDEBAR_WIDTH


def test_a_narrow_sidebar_wraps_rather_than_cuts_off(shown):
    shown.plot(np.arange(10.0), np.linspace(0.0, 1.0, 10), name="a curve")
    shown.selectPlot(shown.plots[0])  # its settings show in the sidebar too
    _dragSidebarTo(shown, 220)
    assert _sidebarWidth(shown) <= 240  # as narrow as asked, or nearly
    assertNothingClipped(shown.sidePanel)
    bar = shown.sidebarArea.horizontalScrollBar()
    assert bar is not None and bar.maximum() == 0  # never scrolls sideways


def test_the_plot_keeps_its_minimum_width(shown):
    _dragSidebarTo(shown, 5000)
    assert shown.gv.width() >= PLOT_MIN_WIDTH
    assert _sidebarWidth(shown) > 600  # the sidebar took the rest
