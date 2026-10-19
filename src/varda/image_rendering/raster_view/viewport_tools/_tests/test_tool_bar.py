"""The tool row under a view: Pixel Select first, and every tool reachable at
any width (release QA #2: it hid behind the » button at laptop sizes)."""

import pytest

from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.image_rendering.raster_view.viewport_tools.pixel_select_tool import (
    PixelSelectTool,
)
from varda.image_rendering.raster_view.viewport_tools.tool_manager import ToolManager
from varda.utilities.debug import generate_random_image


@pytest.fixture
def viewport(qtbot):
    viewport = ImageViewport(ImageRenderer(image=generate_random_image((20, 20, 10))))
    qtbot.addWidget(viewport)
    return viewport


def test_pixel_select_comes_first(viewport):
    toolbar = ToolManager(viewport).getToolbar()
    assert toolbar.actions()[0].data() is PixelSelectTool


@pytest.mark.parametrize("width", [180, 320, 900])
def test_every_tool_shows_at_any_width(qtbot, viewport, width):
    manager = ToolManager(viewport)
    viewport.addToolBar(manager.getToolbar())
    viewport.resize(width, 400)
    viewport.show()
    qtbot.waitExposed(viewport)
    toolbar = manager.getToolbar()

    for action in toolbar.actions():
        button = toolbar.widgetForAction(action)
        assert button is not None and button.isVisible(), action.text()
        assert toolbar.rect().contains(button.geometry()), action.text()
