"""Vertical wavelength markers that span every spectrum on the plot."""

import numpy as np
import pytest

from varda.common.vec2 import Vec2
from varda.plotting.plot import RangeMode, VardaPlotWidget


@pytest.fixture
def widget(qtbot):
    widget = VardaPlotWidget()
    qtbot.addWidget(widget)
    widget.plot(np.linspace(1000.0, 2600.0, 161), np.linspace(0.2, 0.6, 161), name="c")
    return widget


def test_add_marker_at_a_wavelength(widget):
    marker = widget.addMarker(1900.0)

    assert widget.markers == [marker]
    assert marker.value() == 1900.0
    assert marker in widget.plotItem.items  # drawn on the plot
    assert marker.movable  # draggable to fine-tune


def test_marker_label_shows_its_wavelength_and_follows_drags(widget):
    marker = widget.addMarker(1900.0)
    assert "1900" in marker.label.format.format(value=marker.value())

    marker.setValue(2210.0)
    assert marker.value() == 2210.0
    assert "2210" in widget.markerList.item(0).text()


def test_add_marker_defaults_to_the_centre_of_the_visible_wavelengths(widget):
    widget.viewConfig.rangeMode.set(RangeMode.MANUAL)
    widget.rangeConfig.viewRangeX.set(Vec2(1800.0, 2000.0))

    marker = widget.addMarker()

    assert marker.value() == pytest.approx(1900.0)


def test_remove_and_clear_markers(widget):
    first = widget.addMarker(1900.0)
    second = widget.addMarker(2300.0)

    widget.removeMarker(first)
    assert widget.markers == [second]
    assert first not in widget.plotItem.items

    widget.clearMarkers()
    assert widget.markers == []
    assert widget.markerList.count() == 0


def test_markers_survive_clearing_the_curves(widget):
    marker = widget.addMarker(1900.0)
    widget.clearPlots()
    assert widget.markers == [marker]
    assert marker in widget.plotItem.items


def test_markers_section_sits_in_the_sidebar(widget):
    from varda.common.ui import SectionBox

    layout = widget._sidebar
    titles = [
        layout.itemAt(i).widget().title
        for i in range(layout.count())
        if isinstance(layout.itemAt(i).widget(), SectionBox)
    ]
    assert titles == [
        "View",
        "Continuum Removal",
        "Markers",
        "Library Spectra",
        "Selected Curve",
        "Appearance",
    ]
