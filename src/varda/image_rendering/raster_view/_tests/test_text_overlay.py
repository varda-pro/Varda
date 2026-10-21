"""A tool's hint (a text overlay without a position) stays in the view's
top-left corner, wrapped to the view, whatever the view is panned, zoomed or
resized to: never off the edge, never cut off."""

import numpy as np
import pytest
from PyQt6.QtCore import QPointF

from varda.common.entities import VardaRaster
from varda.common.layout_audit import clippedWidgets
from varda.image_loading.data_sources import ArrayDataSource
from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.image_rendering.raster_view.overlay_handles import PyqtgraphTextOverlay

HINT = (
    "Ctrl/Cmd+left-click: numerator box, Ctrl/Cmd+right-click: denominator box. "
    "S saves both as ROIs, P saves the numerator pixel as a point, Esc clears."
)


def viewport(qtbot) -> ImageViewport:
    values = np.random.default_rng(0).uniform(0, 1, (200, 300, 3)).astype(np.float32)
    raster = VardaRaster(ArrayDataSource(values), name="image")
    view = ImageViewport(ImageRenderer(image=raster))
    qtbot.addWidget(view)
    return view


def assertInside(view: ImageViewport, overlay: PyqtgraphTextOverlay) -> None:
    item = overlay._item
    box = view._vb.sceneBoundingRect()
    text = item.mapRectToScene(
        item.textItem.mapRectToParent(item.textItem.boundingRect())
    )
    assert box.contains(text), f"{text} is not inside {box}"
    # in the top-left corner
    assert text.left() - box.left() < 10 and text.top() - box.top() < 10


@pytest.mark.parametrize("width", [260, 800])
def test_the_hint_is_in_the_corner_and_inside_the_view(qtbot, width):
    view = viewport(qtbot)
    # made before the view has its size, as when a tool starts with its workspace
    overlay = view.addTextOverlay(HINT)
    assert isinstance(overlay, PyqtgraphTextOverlay)
    view.resize(width, 500)
    view.show()
    qtbot.waitExposed(view)
    qtbot.wait(20)
    assertInside(view, overlay)
    # panned and zoomed: it stays in the corner, over the image
    view._vb.translateBy(x=40, y=25)
    view._vb.scaleBy((0.5, 0.5))
    qtbot.wait(20)
    assertInside(view, overlay)
    # narrowed: it wraps
    view.resize(width // 2 + 100, 400)
    qtbot.wait(20)
    assertInside(view, overlay)
    overlay.remove()
    assert overlay._item.scene() is None


def test_a_label_with_a_position_stays_on_the_image(qtbot):
    view = viewport(qtbot)
    view.resize(400, 300)
    view.show()
    qtbot.waitExposed(view)
    overlay = view.addTextOverlay("here", viewPos=QPointF(50, 60))
    assert isinstance(overlay, PyqtgraphTextOverlay)
    assert overlay._item.pos() == QPointF(50, 60)
    assert overlay._item.parentItem() is view._vb.childGroup
    overlay.remove()


def test_a_hint_taller_than_a_short_view_still_fits_it(qtbot):
    """Release QA 0.12.0: in a short overview the Pixel Select hint wrapped
    to three lines and its last words fell off the bottom of the view."""
    view = viewport(qtbot)
    overlay = view.addTextOverlay(HINT)
    assert isinstance(overlay, PyqtgraphTextOverlay)
    view.resize(260, 90)
    view.show()
    qtbot.waitExposed(view)
    qtbot.wait(20)
    assertInside(view, overlay)
    assert clippedWidgets(view) == []
