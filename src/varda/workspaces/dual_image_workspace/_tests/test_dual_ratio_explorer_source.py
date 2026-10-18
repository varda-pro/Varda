"""The Ratio Explorer honors the dual workspace's Spectrum Source like Pixel Select."""

import numpy as np
from PyQt6.QtCore import QPointF, Qt

from varda.image_rendering.raster_view.pointer_event import PointerAction, PointerEvent
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    RatioExplorerTool,
)
from varda.rois.region_statistics import boxPolygonPixels, computeRegionStatistics
from varda.utilities.debug import generate_random_image
from varda.workspaces.dual_image_workspace.dual_image_workspace import (
    DualImageWorkspace,
    DualImageWorkspaceConfig,
    PixelSpectrumSource,
)

CTRL = Qt.KeyboardModifier.ControlModifier  # boxes are placed with Ctrl/Cmd held


def _press(button, x, y):
    pos = QPointF(x, y)
    return PointerEvent(PointerAction.PRESS, pos, pos, button, CTRL)


def test_boxes_on_the_secondary_ratio_the_primary_when_source_is_primary(qtbot):
    primary = generate_random_image((40, 40, 10))
    secondary = generate_random_image((40, 40, 10))
    config = DualImageWorkspaceConfig([primary, secondary])
    config.image2Param.set(secondary)
    workspace = DualImageWorkspace(config)
    qtbot.addWidget(workspace)
    workspace.pixelSourceConfig.source.set(PixelSpectrumSource.PRIMARY)

    workspace.toolManager2.activateTool(RatioExplorerTool)
    tool = workspace.toolManager2.activeTool
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))

    # Ratios land in their own plot, not the pixel-spectra plot
    ratioPlot = workspace.pixelSpectraDocks.plots[-1]
    assert workspace.pixelSpectraDocks.dockFor(ratioPlot).windowTitle() == (
        "Ratio Spectra"
    )
    assert workspace.pixelPlotWidget.pixelCurves == []
    # the ratio, then the numerator and denominator spectra
    curve, numeratorCurve, denominatorCurve = ratioPlot.pixelCurves
    assert numeratorCurve.plotDataItem.name().startswith(primary.name)
    assert denominatorCurve.plotDataItem.name().startswith(primary.name)
    _x, values = curve.plotDataItem.getData()
    numerator = computeRegionStatistics(boxPolygonPixels(11, 21, 5, 5), primary)["mean"]
    denominator = computeRegionStatistics(boxPolygonPixels(31, 5, 5, 5), primary)
    np.testing.assert_allclose(values, numerator / denominator["mean"])
    assert curve.plotDataItem.name().startswith(primary.name)
