"""The histogram keeps the user's zoom when only the stretch changes."""

import numpy as np
import pytest
from PyQt6.QtCore import QRectF

from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.new_histogram_view import NewHistogramView
from varda.utilities.debug import generate_random_image


@pytest.fixture
def view(qtbot):
    renderer = ImageRenderer(image=generate_random_image((40, 40, 10)))
    widget = NewHistogramView(renderer)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def _plot(view):
    return next(iter(view._values))


def _zoomIn(viewBox):
    (x0, x1), (y0, y1) = viewBox.viewRange()
    viewBox.showAxRect(QRectF(x0 + 0.3 * (x1 - x0), y0, 0.2 * (x1 - x0), (y1 - y0) / 3))
    return viewBox.viewRange()[0]


def test_a_box_zoom_zooms_x_and_rebins(view):
    viewBox = _plot(view).getViewBox()
    before = viewBox.viewRange()[0]
    zoomed = _zoomIn(viewBox)
    assert zoomed[1] - zoomed[0] < 0.5 * (before[1] - before[0])
    xs, _ys = view._curves[_plot(view)].getData()
    assert xs.min() >= zoomed[0] - 1e-9 and xs.max() <= zoomed[1] + 1e-9


def test_changing_the_stretch_keeps_the_zoom(view):
    viewBox = _plot(view).getViewBox()
    zoomed = _zoomIn(viewBox)
    lo, hi = zoomed
    view.imageRenderer.setStretchMinMax(
        0, lo, (lo + hi) / 2
    )  # e.g. dragging the region
    assert np.allclose(viewBox.viewRange()[0], zoomed)
    region = view.monoRegion if view.monoRegion is not None else view.rRegion
    assert np.allclose(region.getRegion(), (lo, (lo + hi) / 2))


def test_showing_other_bands_resets_the_view(view):
    viewBox = _plot(view).getViewBox()
    _zoomIn(viewBox)
    settings = view.imageRenderer.settings
    settings.mono.band.set((int(settings.mono.band.get()) + 3) % 10)
    full = view._values[_plot(view)]
    x0, x1 = _plot(view).getViewBox().viewRange()[0]
    assert x0 <= full.min() and x1 >= full.max()
