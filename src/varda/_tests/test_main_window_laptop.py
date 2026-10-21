"""A General Analysis workspace in the main window, at the window's
smallest size and on laptop screens: nothing is cut off, inside its views
too (release QA 0.12.0: the ROI Manager's hint lost its second line, the
Pixel Select hint ran past its view, the color map's handles hung off the
bottom of Render Settings)."""

import pytest
from app_model import Application

from varda.common.di_types import ProjectImages
from varda.common.layout_audit import assertNothingClipped
from varda.image_rendering.raster_view.viewport_tools.pixel_select_tool import (
    PixelSelectTool,
)
from varda.maingui import MainGUI
from varda.utilities.debug import generate_random_image
from varda.workspaces.general_image_analysis import GeneralImageAnalysisWorkflow
from varda.workspaces.workspace_kinds import buildWorkspace

APP_NAME = "varda-test-main-window-laptop"
SMALLEST = (0, 0)  # the window's minimum size
SIZES = [SMALLEST, (1024, 700), (1280, 800)]


@pytest.fixture
def gui(qtbot):
    app = Application(APP_NAME)
    app.images = ProjectImages([generate_random_image((60, 80, 10))])
    gui = MainGUI(app)
    qtbot.addWidget(gui)
    yield gui
    Application.destroy(APP_NAME)


def _workspace(qtbot, gui: MainGUI, size: tuple[int, int]):
    gui.resize(*size)  # clamped to the window's minimum
    gui.show()
    qtbot.waitExposed(gui)
    workspace = buildWorkspace("general", list(gui.app.images), gui.app.images)
    assert isinstance(workspace, GeneralImageAnalysisWorkflow)
    gui.addTab(workspace)
    qtbot.wait(100)
    return workspace


@pytest.mark.parametrize("size", SIZES)
def test_a_workspace_in_the_window_cuts_nothing_off(qtbot, gui, size):
    _workspace(qtbot, gui, size)
    assertNothingClipped(gui)


@pytest.mark.parametrize("size", SIZES)
def test_the_pixel_select_hint_fits_the_overview(qtbot, gui, size):
    workspace = _workspace(qtbot, gui, size)
    workspace.toolManager1.select(PixelSelectTool)  # the overview, top right
    qtbot.wait(50)
    assertNothingClipped(gui)
