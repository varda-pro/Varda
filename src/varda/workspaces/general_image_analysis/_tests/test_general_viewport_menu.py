"""A view's right-click reopens the workspace's docks, a closed one too."""

from PyQt6.QtWidgets import QMenu

from varda.utilities.debug import generate_random_image
from varda.workspaces.general_image_analysis import (
    GeneralImageAnalysisConfig,
    GeneralImageAnalysisWorkflow,
)


def test_a_closed_render_settings_dock_comes_back_from_any_view(qtbot):
    workflow = GeneralImageAnalysisWorkflow(
        GeneralImageAnalysisConfig([generate_random_image((20, 20, 10))])
    )
    qtbot.addWidget(workflow)
    workflow.settingsDock.closeDockWidget()
    assert workflow.settingsDock.isClosed()
    for view in (
        workflow.tripleRasterView.viewport1,
        workflow.tripleRasterView.viewport3,
    ):
        menu = QMenu()
        workflow.viewportContextMenuController.addPanels(menu, view)
        actions = {a.text(): a for a in menu.actions() if a.text()}
        assert {"Render Settings", "Histogram"} <= set(actions)
        assert any(a.menu() is not None for a in menu.actions())  # Panels
    actions["Render Settings"].trigger()
    assert not workflow.settingsDock.isClosed()
