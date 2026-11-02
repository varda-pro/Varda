"""Duplicating a workspace: same images, its own copies of ROIs and display."""

from shapely.geometry import box

from varda._actions._workspace_actions import duplicateCurrentWorkspace
from varda.common.di_types import ProjectImages
from varda.common.entities import Color, ROIMode
from varda.image_rendering.image_renderer import RenderMode
from varda.utilities.debug import generate_random_image
from varda.workspaces.dual_image_workspace.dual_image_workspace import (
    DualImageWorkspace,
    DualImageWorkspaceConfig,
)
from varda.workspaces.general_image_analysis import (
    GeneralImageAnalysisConfig,
    GeneralImageAnalysisWorkflow,
)
from varda.workspaces.registry import duplicateWorkspace

RED = Color(1.0, 0.0, 0.0, 0.5)


def _general(qtbot, images: ProjectImages) -> GeneralImageAnalysisWorkflow:
    config = GeneralImageAnalysisConfig(images)
    config.image.set(images[1])
    workspace = GeneralImageAnalysisWorkflow(config)
    qtbot.addWidget(workspace)
    return workspace


def test_a_duplicate_shows_the_same_image_with_its_own_rois_and_display(qtbot):
    images = ProjectImages([generate_random_image((20, 20, 10)) for _ in range(2)])
    source = _general(qtbot, images)
    source.roiCollection.addROI(box(1, 1, 4, 4), "r1", RED, ROIMode.RECTANGLE)
    settings = source.imageRenderer.settings
    settings.mode.set(RenderMode.RGB)
    settings.rgb.red.set(6)

    copy = duplicateWorkspace(source, images)
    assert isinstance(copy, GeneralImageAnalysisWorkflow)
    qtbot.addWidget(copy)

    assert copy.config.image.get() is images[1]
    assert copy.config.imageList is images  # later results reach it too
    assert [roi.name for roi in copy.roiCollection.getAllROIs()] == ["r1"]
    assert copy.imageRenderer.settings.mode.get() is RenderMode.RGB
    assert copy.imageRenderer.settings.rgb.red.get() == 6

    copy.roiCollection.addROI(box(5, 5, 8, 8), "r2", RED, ROIMode.RECTANGLE)
    copy.imageRenderer.settings.rgb.red.set(2)
    assert len(source.roiCollection) == 1  # the original is untouched
    assert settings.rgb.red.get() == 6


def test_a_dual_workspace_keeps_both_images_and_both_displays(qtbot):
    images = ProjectImages([generate_random_image((20, 20, 10)) for _ in range(2)])
    config = DualImageWorkspaceConfig(images)
    config.image1Param.set(images[0])
    config.image2Param.set(images[1])
    source = DualImageWorkspace(config)
    qtbot.addWidget(source)
    source.secondaryRenderer.settings.mono.band.set(4)

    copy = duplicateWorkspace(source, images)
    assert isinstance(copy, DualImageWorkspace)
    qtbot.addWidget(copy)

    assert (copy.image1, copy.image2) == (images[0], images[1])
    assert copy.secondaryRenderer.settings.mono.band.get() == 4


class _FakeMainGui:
    def __init__(self, current) -> None:
        self.current = current
        self.tabs: list[tuple[object, str | None]] = []

    def currentWorkspace(self):
        return self.current

    def addTab(self, widget, title=None) -> None:
        self.tabs.append((widget, title))


def test_the_menu_action_opens_the_copy_in_a_new_tab(qtbot):
    images = ProjectImages([generate_random_image((20, 20, 10)) for _ in range(2)])
    source = _general(qtbot, images)
    mainGui = _FakeMainGui(source)

    duplicateCurrentWorkspace(images, mainGui)  # ty: ignore[invalid-argument-type]

    ((copy, title),) = mainGui.tabs
    qtbot.addWidget(copy)
    assert isinstance(copy, GeneralImageAnalysisWorkflow) and copy is not source
    assert title == f"{source.windowTitle()} (copy)"
