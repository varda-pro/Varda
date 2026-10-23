"""The main and zoom views' boxes are free-form rectangles: a box starts
with the image's shape (a tall rectangle for a HiRISE strip), and dragging
its corner changes its width and height independently, so it can be made
square or any rectangle."""

import numpy as np
import pytest
from PyQt6.QtCore import QPointF

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources import ArrayDataSource
from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.raster_view.triple_raster_view import TripleRasterView


@pytest.fixture
def view(qtbot):
    tall = VardaRaster(  # a HiRISE-like strip: five times taller than wide
        ArrayDataSource(np.random.default_rng(0).random((500, 100, 3))), name="strip"
    )
    widget = TripleRasterView(ImageRenderer(tall))
    qtbot.addWidget(widget)
    widget.resize(900, 600)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def _dragCorner(roi, to: QPointF) -> None:
    """Drag the box's corner handle to ``to`` (in its view's coordinates)."""
    handle = roi.getHandles()[0]
    roi.movePoint(handle, roi.mapToParent(roi.mapFromParent(to)), finish=True)


@pytest.mark.parametrize("which", ["roi1", "roi2"])
def test_a_box_starts_with_the_image_s_shape_and_resizes_freely(view, which):
    roi = getattr(view, which)
    width, height = roi.size().x(), roi.size().y()
    assert height > 3 * width  # tall, like the image
    assert not roi.aspectLocked

    pos = roi.pos()
    # wider only: the height stays
    _dragCorner(roi, QPointF(pos.x() + 2 * width, pos.y() + height))
    assert roi.size().x() == pytest.approx(2 * width, rel=0.02)
    assert roi.size().y() == pytest.approx(height, rel=0.02)
    # then shorter only, to a square: the width stays
    _dragCorner(roi, QPointF(pos.x() + 2 * width, pos.y() + 2 * width))
    assert roi.size().x() == pytest.approx(2 * width, rel=0.02)
    assert roi.size().y() == pytest.approx(2 * width, rel=0.02)
