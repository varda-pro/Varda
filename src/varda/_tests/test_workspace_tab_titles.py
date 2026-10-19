"""Workspace tabs are named after the image(s) they show."""

import pytest
from app_model import Application
from PyQt6.QtWidgets import QWidget

from varda.common.di_types import ProjectImages
from varda.maingui import MainGUI, elidedTabTitle
from varda.utilities.debug import generate_random_image
from varda.workspaces.dual_image_workspace.dual_image_workspace import (
    DualImageWorkspace,
    DualImageWorkspaceConfig,
)
from varda.workspaces.general_image_analysis import (
    GeneralImageAnalysisConfig,
    GeneralImageAnalysisWorkflow,
)

APP_NAME = "varda-test-tab-titles"


@pytest.fixture
def gui(qtbot):
    app = Application(APP_NAME)
    app.images = ProjectImages()
    gui = MainGUI(app)
    qtbot.addWidget(gui)
    yield gui
    Application.destroy(APP_NAME)


def test_general_workspace_is_titled_by_its_image(qtbot):
    image = generate_random_image((20, 20, 10))
    config = GeneralImageAnalysisConfig([image])
    config.image.set(image)
    workspace = GeneralImageAnalysisWorkflow(config)
    qtbot.addWidget(workspace)

    assert workspace.windowTitle() == image.name


def test_dual_workspace_is_titled_by_both_images(qtbot):
    a, b = generate_random_image((20, 20, 10)), generate_random_image((20, 20, 10))
    config = DualImageWorkspaceConfig([a, b])
    config.image1Param.set(a)
    config.image2Param.set(b)
    workspace = DualImageWorkspace(config)
    qtbot.addWidget(workspace)

    assert workspace.windowTitle() == f"{a.name} | {b.name}"


def test_tab_defaults_to_the_workspace_title(gui):
    widget = QWidget()
    widget.setWindowTitle("frt0000a09c_07_if166l_trr3")
    gui.addTab(widget)

    index = gui.centralTabs.indexOf(widget)
    assert gui.centralTabs.tabText(index) == "frt0000a09c_07_if166l_trr3"
    assert gui.centralTabs.tabToolTip(index) == "frt0000a09c_07_if166l_trr3"


def test_long_tab_names_are_shortened_in_the_middle_with_the_full_name_as_tooltip(gui):
    widget = QWidget()
    widget.setWindowTitle("EMIT_L2A_RFL_001_20250117T041624_2501702_022_ortho.img")
    gui.addTab(widget)

    index = gui.centralTabs.indexOf(widget)
    assert gui.centralTabs.tabText(index) == elidedTabTitle(widget.windowTitle())
    assert gui.centralTabs.tabToolTip(index) == widget.windowTitle()


def test_elided_tab_title_keeps_both_ends():
    assert elidedTabTitle("short") == "short"
    long = "EMIT_L2A_RFL_001_20250117T041624_2501702_022_ortho.img"
    short = elidedTabTitle(long)
    assert len(short) <= 40
    assert short.startswith("EMIT_L2A_RFL_001") and short.endswith("_022_ortho.img")
    assert "…" in short


def test_a_new_workspace_tab_comes_to_the_front(gui):
    """Release QA #3: a new tab opened behind the current one."""
    first, second = QWidget(), QWidget()
    gui.addTab(first, "first")
    gui.addTab(second, "second")

    assert gui.currentWorkspace() is second
