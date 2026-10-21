"""Pixel selection in the general workspace plots into one persistent dock."""

from PyQt6.QtCore import QPointF, QRect

from varda.image_rendering.raster_view.viewport_tools.crop_tool import CropTool
from varda.image_rendering.raster_view.viewport_tools.pixel_select_tool import (
    PixelSelectTool,
)
from varda.utilities.debug import generate_random_image
from varda.workspaces.general_image_analysis import (
    GeneralImageAnalysisConfig,
    GeneralImageAnalysisWorkflow,
)


def test_pixel_selections_accumulate_in_the_pixel_spectra_dock(qtbot):
    image = generate_random_image((20, 20, 10))
    workflow = GeneralImageAnalysisWorkflow(GeneralImageAnalysisConfig([image]))
    qtbot.addWidget(workflow)  # closed and deleted with the test, not by GC later

    workflow.toolManager1.activateTool(PixelSelectTool)
    tool = workflow.toolManager1.activeTool
    tool.sigPixelSelected.emit(QPointF(2.0, 3.0))
    tool.sigPixelSelected.emit(QPointF(4.0, 5.0))

    assert workflow.pixelPlotDock.widget() is workflow.pixelPlotWidget
    assert len(workflow.pixelPlotWidget.pixelCurves) == 2


def test_closing_the_pixel_dock_then_selecting_a_pixel_reopens_it(qtbot):
    image = generate_random_image((20, 20, 10))
    workflow = GeneralImageAnalysisWorkflow(GeneralImageAnalysisConfig([image]))
    qtbot.addWidget(workflow)  # closed and deleted with the test, not by GC later
    workflow.toolManager1.activateTool(PixelSelectTool)
    workflow.pixelPlotDock.toggleView(False)
    assert workflow.pixelPlotDock.isClosed()

    workflow.toolManager1.activeTool.sigPixelSelected.emit(QPointF(2.0, 3.0))

    assert not workflow.pixelPlotDock.isClosed()
    assert len(workflow.pixelPlotWidget.pixelCurves) == 1


def test_the_tool_uses_the_active_plots_window_and_outlines_it(qtbot):
    image = generate_random_image((20, 20, 10))
    workflow = GeneralImageAnalysisWorkflow(GeneralImageAnalysisConfig([image]))
    qtbot.addWidget(workflow)  # closed and deleted with the test, not by GC later
    workflow.toolManager1.activateTool(PixelSelectTool)
    tool = workflow.toolManager1.activeTool
    workflow.pixelPlotWidget.pixelConfig.windowWidth.set(3)
    workflow.pixelPlotWidget.pixelConfig.windowHeight.set(5)
    assert tool.windowProvider() == (3, 5)

    tool.isDragging = True
    tool._outlineWindow(10, 10)
    assert tool._windowOutline is not None
    tool._hideCrosshairs()
    assert tool._windowOutline is None

    tool.sigPixelSelected.emit(QPointF(10.0, 10.0))
    (curve,) = workflow.pixelPlotWidget.pixelCurves
    assert curve.plotDataItem.name() == "Region 3×5 at (10, 10)"


def test_a_crop_box_adds_a_cropped_image_to_the_project(qtbot):
    image = generate_random_image((20, 20, 10))
    project = [image]
    workflow = GeneralImageAnalysisWorkflow(GeneralImageAnalysisConfig(project))
    qtbot.addWidget(workflow)  # closed and deleted with the test, not by GC later
    workflow.toolManager1.activateTool(CropTool)
    workflow.toolManager1.activeTool.sigCropRequested.emit(QRect(2, 3, 8, 5))
    assert len(project) == 2
    cropped = project[1]
    assert (cropped.width, cropped.height, cropped.bandCount) == (8, 5, 10)
    assert "crop" in cropped.name


def test_the_status_bar_says_what_to_do_next(qtbot):
    """Release QA #22: it said 'General Image Analysis Workflow initialized'."""
    image = generate_random_image((20, 20, 10))
    workflow = GeneralImageAnalysisWorkflow(GeneralImageAnalysisConfig([image]))
    qtbot.addWidget(workflow)
    message = workflow.statusBar().currentMessage()
    assert "Pixel Select" in message and "spectrum" in message
    assert "initialized" not in message


def test_the_status_bar_moves_on_once_pixel_select_is_on_and_used(qtbot):
    """Release QA 0.12.0: it kept saying "Turn on Pixel Select…" with Pixel
    Select on and a spectrum plotted."""
    image = generate_random_image((20, 20, 10))
    workflow = GeneralImageAnalysisWorkflow(GeneralImageAnalysisConfig([image]))
    qtbot.addWidget(workflow)
    statusBar = workflow.statusBar()
    assert statusBar is not None
    workflow.toolManager1.select(PixelSelectTool)
    message = statusBar.currentMessage()
    assert "Turn on" not in message
    assert message.startswith("Click a pixel")

    tool = workflow.toolManager1.activeTool
    assert isinstance(tool, PixelSelectTool)
    tool.sigPixelSelected.emit(QPointF(2.0, 3.0))
    message = statusBar.currentMessage()
    assert "Turn on" not in message and "Click a pixel" not in message
    assert "(2, 3)" in message and "Draw ROI" in message
