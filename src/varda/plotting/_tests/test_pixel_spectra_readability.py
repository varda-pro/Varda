"""The pixel spectra plot says what it shows (release QA 0.12.0): its y axis
is named (#5), its legend doesn't hide the curves (#5), and a curve picked
again by clicking its pixel says it's selected (#9)."""

import numpy as np
import pytest

from varda.common.entities import Color, VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.plotting.pixel_spectra_plot import PixelSpectraPlotWidget, valueAxisLabel
from varda.utilities.debug import generate_random_image


@pytest.fixture
def widget(qtbot):
    widget = PixelSpectraPlotWidget()
    qtbot.addWidget(widget)
    return widget


def _image(extra: dict, name: str = "scene") -> VardaRaster:
    data = np.random.default_rng(0).random((5, 5, 4)).astype(np.float32)
    source = ArrayDataSource(
        data, wavelengths=np.array([500.0, 600.0, 700.0, 800.0]), extraMetadata=extra
    )
    return VardaRaster(source, name=name)


def _leftLabel(widget: PixelSpectraPlotWidget) -> str:
    return widget.plotItem.getAxis("left").labelText


@pytest.mark.parametrize(
    ("extra", "label"),
    [
        ({"data_units": "I/F"}, "Value (I/F)"),
        ({"data units": "{W/m^2/sr/um}"}, "Value (W/m^2/sr/um)"),
        ({"BUNIT": "Jy"}, "Value (Jy)"),
        ({"reflectance_scale_factor": "10000"}, "Reflectance"),
        ({}, "Value"),
    ],
)
def test_the_value_axis_is_named_from_the_images_metadata(extra, label):
    assert valueAxisLabel(_image(extra)) == label


def test_the_y_axis_is_labeled_before_and_after_a_spectrum(widget):
    assert _leftLabel(widget) == "Value"
    widget.addPixelSpectrum(_image({"data_units": "I/F"}), 1, 1)
    assert _leftLabel(widget) == "Value (I/F)"


def test_images_with_different_values_fall_back_to_value(widget):
    widget.addPixelSpectrum(_image({"data_units": "I/F"}), 1, 1)
    widget.addPixelSpectrum(_image({"data_units": "DN"}, "raw"), 1, 1)
    assert _leftLabel(widget) == "Value"
    widget.clearPixelSpectra()
    widget.addPixelSpectrum(_image({"data_units": "DN"}, "raw"), 2, 2)
    assert _leftLabel(widget) == "Value (DN)"


def test_the_legend_has_a_translucent_background_in_the_plots_color(widget):
    brush = widget.legend.opts["brush"]
    assert brush.style() != 0  # it has one
    assert 0 < brush.color().alpha() < 255  # the curves show through
    widget.appearanceConfig.backgroundColor.set(Color.fromHexString("#ffffff"))
    color = widget.legend.opts["brush"].color()
    assert (color.red(), color.green(), color.blue()) == (255, 255, 255)
    assert 0 < color.alpha() < 255


def _legendText(widget: PixelSpectraPlotWidget, curve) -> str:
    return widget.legend.getLabel(curve.plotDataItem).text


def test_clicking_a_plotted_pixel_again_says_its_curve_is_selected(widget):
    image = generate_random_image((20, 20, 10))
    curve = widget.addPixelSpectrum(image, 3, 4)
    assert _legendText(widget, curve) == "Pixel (3, 4)"
    again = widget.addPixelSpectrum(image, 3, 4)  # e.g. a Ctrl/⌘-click
    assert again is curve and len(widget.pixelCurves) == 1
    assert widget.selectedCurve is curve
    assert _legendText(widget, curve) == "Selected: Pixel (3, 4)"
    assert curve.plotDataItem.name() == "Pixel (3, 4)"  # exports keep the name
    widget.deselectPlot()
    assert _legendText(widget, curve) == "Pixel (3, 4)"


def test_selecting_another_curve_moves_the_selected_note(widget):
    image = generate_random_image((20, 20, 10))
    first = widget.addPixelSpectrum(image, 1, 1)
    second = widget.addPixelSpectrum(image, 2, 2)
    widget.selectPlot(first)
    widget.selectPlot(second)
    assert _legendText(widget, first) == "Pixel (1, 1)"
    assert _legendText(widget, second) == "Selected: Pixel (2, 2)"
