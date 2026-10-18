"""Lines in the general workspace: drawn with Draw Line, listed, profiled,
marked on every view, and kept with the session."""

import numpy as np
from shapely.geometry import box

from varda.common.entities import Color, ROIMode
from varda.geo._tests.fixtures import makeImage
from varda.image_rendering.raster_view.viewport_tools.line_tools import LineDrawTool
from varda.lines.line_collection import LineKind, sharedLines
from varda.rois.roi_calculations import GeometryMeasure
from varda.utilities.debug import generate_random_image
from varda.workspaces.general_image_analysis import (
    GeneralImageAnalysisConfig,
    GeneralImageAnalysisWorkflow,
)
from varda.workspaces.workspace_kinds import buildWorkspace

RED = Color(1.0, 0.0, 0.0, 0.5)


def _workflow(qtbot, image):
    workflow = GeneralImageAnalysisWorkflow(GeneralImageAnalysisConfig([image]))
    qtbot.addWidget(workflow)
    return workflow


def test_a_drawn_line_is_listed_profiled_and_shown_on_every_view(qtbot):
    image = generate_random_image((30, 40, 10))
    workflow = _workflow(qtbot, image)
    workflow.toolManager2.activateTool(LineDrawTool)
    tool = workflow.toolManager2.activeTool
    tool.sigLineDrawn.emit(np.array([[2.0, 2.0], [30.0, 20.0]]), LineKind.STRAIGHT)
    (line,) = workflow.lineCollection.lines()
    assert workflow.lineManagerWidget.selectedFids() == [line.fid]
    assert list(workflow.profilePlot.profiles) == [line.fid]
    assert len(workflow.lineDisplay._handles[line.fid]) == 3  # all three views
    workflow.lineManagerWidget.deleteSelected()
    assert workflow.profilePlot.profiles == {} and workflow.lineDisplay._handles == {}


def test_the_kind_comes_from_the_lines_manager(qtbot):
    workflow = _workflow(qtbot, generate_random_image((20, 20, 10)))
    polyline = next(
        b
        for b in workflow.lineManagerWidget.kindButtons.buttons()
        if b.text() == "Polyline"
    )
    polyline.click()
    workflow.toolManager1.activateTool(LineDrawTool)
    assert workflow.toolManager1.activeTool._kind() == LineKind.POLYLINE


def test_lines_come_back_with_the_session(qtbot):
    image = generate_random_image((20, 20, 10))
    workflow = _workflow(qtbot, image)
    workflow.lineCollection.addLine([[1, 1], [10, 10]], name="Rim")
    state = workflow.sessionState()
    restored = buildWorkspace("general", [image], [image], state)
    qtbot.addWidget(restored)
    assert [line.name for line in restored.lineCollection.lines()] == ["Rim"]


def test_its_lines_are_offered_to_other_workspaces_until_it_closes(qtbot):
    image = generate_random_image((20, 20, 10))
    workflow = _workflow(qtbot, image)
    workflow.lineCollection.addLine([[1, 1], [10, 10]], name="Rim")
    assert (image, workflow.lineCollection) in sharedLines()
    workflow.close()
    assert all(lines is not workflow.lineCollection for _, lines in sharedLines())


def test_a_crs_set_after_the_workspace_opens_reaches_its_rois_lines_and_points(
    qtbot,
):
    """A WGS 84 image of Mars, measured, then Set coordinate system… → Mars:
    the ROI's body area, the line's length and the points follow."""
    image = makeImage(
        "EPSG:4326", centerLonLat=(10.0, 0.0), pixelSize=0.01, width=40, height=30
    )
    workflow = _workflow(qtbot, image)
    rois = workflow.roiCollection
    fid = rois.addROI(box(9.9, -0.1, 10.0, 0.0), "box", RED, ROIMode.RECTANGLE)
    workflow.roiManagerWidget.calculatedColumns.add(
        "area_km2", GeometryMeasure("area_body")
    )
    workflow.lineCollection.addLine([[0.0, 0.0], [30.0, 0.0]])
    lengths = workflow.lineManagerWidget.model
    pointFid = workflow.pointManagerWidget.addPixel(image, 3, 4)
    onEarth = (rois.getProperty(fid, "area_km2"), lengths.data(lengths.index(0, 2)))

    image.setMetadataOverrides({"crs": "IAU_2015:49900"})

    assert rois.getProperty(fid, "area_km2") < onEarth[0] * 0.3
    assert lengths.data(lengths.index(0, 2)) != onEarth[1]
    assert pointFid is not None
    assert workflow.pointCollection.crs == image.crs
    assert workflow.pointManagerWidget.addPixel(image, 5, 6) is not None
