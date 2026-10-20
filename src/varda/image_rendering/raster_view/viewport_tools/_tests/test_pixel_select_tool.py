"""Pixel Select: a plain click selects, a plain drag still pans (release QA #5)."""

import pytest
from PyQt6.QtCore import QPoint, Qt

from varda.common.keys import controlClick
from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.image_rendering.raster_view.viewport_tools.pixel_select_tool import (
    PixelSelectTool,
)
from varda.utilities.debug import generate_random_image

LEFT = Qt.MouseButton.LeftButton
NONE = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier


@pytest.fixture
def viewport(qtbot):
    viewport = ImageViewport(ImageRenderer(image=generate_random_image((40, 60, 10))))
    qtbot.addWidget(viewport)
    viewport.resize(600, 400)
    viewport.show()
    qtbot.waitExposed(viewport)
    return viewport


@pytest.fixture
def selected(viewport):
    tool = PixelSelectTool(viewport)
    picks = []
    tool.sigPixelSelected.connect(picks.append)
    yield picks
    tool.deactivate()


def _surface(viewport):
    widget = viewport._gv.viewport()
    return widget, widget.rect().center()


def test_a_plain_click_selects_the_pixel(qtbot, viewport, selected):
    widget, center = _surface(viewport)
    qtbot.mouseClick(widget, LEFT, NONE, center)
    qtbot.wait(20)
    assert len(selected) == 1


def test_a_control_click_still_selects(qtbot, viewport, selected):
    widget, center = _surface(viewport)
    qtbot.mouseClick(widget, LEFT, CTRL, center)
    qtbot.wait(20)
    assert len(selected) == 1


def test_a_plain_drag_pans_and_selects_nothing(qtbot, viewport, selected):
    widget, center = _surface(viewport)
    before = viewport._vb.viewRange()
    qtbot.mousePress(widget, LEFT, NONE, center)
    for step in range(1, 6):
        qtbot.mouseMove(widget, center + QPoint(10 * step, 5 * step))
        qtbot.wait(5)
    qtbot.mouseRelease(widget, LEFT, NONE, center + QPoint(50, 25))
    qtbot.wait(20)
    assert selected == []
    assert viewport._vb.viewRange() != before


def test_the_hint_names_a_plain_click_and_this_platforms_key():
    hint = PixelSelectTool.toolDescription
    assert hint.startswith("Click a pixel")
    assert controlClick("drag") in hint
    assert "Ctrl/" not in hint and "Cmd" not in hint


def test_turning_it_on_shows_how_to_use_it(viewport):
    tool = PixelSelectTool(viewport)
    assert tool._textOverlay is not None
    tool.deactivate()
