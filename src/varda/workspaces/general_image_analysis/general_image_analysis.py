"""
General Image Analysis Workflow

A comprehensive workflow for performing general image analysis with integrated
ROI drawing, band selection, stretch controls, and metadata management.
"""

import functools
import logging
from collections.abc import Sequence

from PyQt6.QtCore import QPointF, QRect
from PyQt6.QtWidgets import (
    QMainWindow,
    QStatusBar,
)

from varda.common.entities import VardaRaster
from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.new_histogram_view import (
    NewHistogramView,
)
import PyQt6Ads as ads


from varda.image_rendering.raster_view import TripleRasterView, ROIDisplayController
from varda.image_rendering.raster_view.viewport_context_menu_controller import (
    ViewportContextMenuController,
)
from varda.image_rendering.raster_view.viewport_tools.pixel_select_tool import (
    PixelSelectTool,
)
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    RatioExplorerTool,
)
from varda.image_rendering.raster_view.viewport_tools.crop_tool import CropTool
from varda.image_rendering.raster_view.viewport_tools.line_tools import LineDrawTool
from varda.image_rendering.raster_view.viewport_tools.tool_manager import ToolManager
from varda.common.parameter import ImageParameter, ParameterGroup
from varda.plotting.pixel_spectra_plot import PixelSpectraPlotWidget
from varda.plotting.plot import VardaPlotWidget
from varda.points.point_collection import PointCollection
from varda.points.point_manager_widget import PointManagerWidget
from varda.lines.line_collection import LineCollection, shareLines, unshareLines
from varda.lines.line_manager_widget import LineManagerWidget
from varda.plotting.profile_plot import ProfilePlotWidget
from varda.workspaces.line_display import LineDisplay
from varda.workspaces.raster_overlay import OverlayBar
from varda.workspaces.pixel_markers import PixelMarkerController
from varda.workspaces.pixel_spectra_docks import PixelSpectraDocks
from varda.workspaces.plot_libraries import plotLibrariesState, restorePlotLibraries
from varda.workspaces.cropping import addCrop, cropMessage
from varda.workspaces.saved_point_markers import SavedPointMarkers
from varda.workspaces.ratio_explorer import (
    RatioExplorerConfig,
    RatioExplorerController,
)
from varda.rois.roi_collection import ROICollection
from varda.rois.roi_drawing_state import ROIDrawingState
from varda.rois.roi_drawing_widgets import ROIDrawingController
from varda.rois.roi_manager_widget import ROIManagerWidget
from varda.common.ui import VardaDockWidget

logger = logging.getLogger(__name__)


class GeneralImageAnalysisConfig(ParameterGroup):
    image = ImageParameter(
        "Image",
        "The image to view.",
    )

    def __init__(self, imageList: Sequence[VardaRaster]) -> None:
        super().__init__()
        self.imageList = imageList
        self.image.setProvider(lambda: self.imageList)


class GeneralImageAnalysisWorkflow(QMainWindow):
    """
    A workflow for performing general image analysis with integrated ROI functionality.

    This workflow orchestrates:
    - Raster image display with navigation
    - ROI drawing and management
    - Band selection controls
    - Stretch/contrast controls
    - Metadata editing
    """

    def __init__(self, config: GeneralImageAnalysisConfig, parent=None):
        super().__init__(parent)
        self.config = config

        # Initialize core components
        self.rasterView = None
        self.bandManager = None
        self.stretchManager = None

        # Initialize UI and connections
        self._initComponents()
        self._initUI()
        self._connectSignals()

        self.setStatusMessage(
            "Turn on Pixel Select under the image and click a pixel to see its "
            "spectrum. Drag to pan, scroll to zoom."
        )

    def _initComponents(self):
        """Initialize all workflow components"""

        # Initialize raster view
        self.imageRenderer = ImageRenderer(image=self.config.image.value)

        self.rendererSettingsPanel = self.imageRenderer.getSettingsPanel(
            stretchFrom=True
        )

        self.tripleRasterView = TripleRasterView(self.imageRenderer, self)

        # Initialize tool management for each viewport
        self.toolManager1 = ToolManager(self.tripleRasterView.viewport1, self)
        self.toolManager2 = ToolManager(self.tripleRasterView.viewport2, self)
        self.toolManager3 = ToolManager(self.tripleRasterView.viewport3, self)

        # Create toolbars for each viewport
        self.tripleRasterView.viewport1.addToolBar(self.toolManager1.getToolbar())
        self.tripleRasterView.viewport2.addToolBar(self.toolManager2.getToolbar())
        self.tripleRasterView.viewport3.addToolBar(self.toolManager3.getToolbar())

        # Lay a result of this scene (e.g. a classification) over all three views
        view = self.tripleRasterView
        self.overlayBar = OverlayBar(
            self.config.imageList,
            self.config.image.value,
            [view.viewport1, view.viewport2, view.viewport3],
            self,
        )
        view.viewport1.addToolBar(self.overlayBar)

        # initialize histogram view
        self.histogram = NewHistogramView(self.imageRenderer, self)

        # --- ROI system ---
        image = self.config.image.value
        self.roiCollection = ROICollection.fromImage(image)

        self.roiDisplayController = ROIDisplayController(
            self.roiCollection, parent=self
        )
        self.roiDisplayController.registerViewport(
            "viewport1", self.tripleRasterView.viewport1
        )
        self.roiDisplayController.registerViewport(
            "viewport2", self.tripleRasterView.viewport2
        )
        self.roiDisplayController.registerViewport(
            "viewport3", self.tripleRasterView.viewport3
        )

        # --- Spectral plot (pixel-spectra plots are created with the docks) ---
        self.plotWidget = VardaPlotWidget(parent=self)

        # Shape and class of the next drawn ROIs, shared by the ROI manager
        # and the Draw ROI tool under each view
        self.roiDrawingState = ROIDrawingState(self.roiCollection)
        self.roiDrawingController = ROIDrawingController(
            self.roiDrawingState,
            [self.toolManager1, self.toolManager2, self.toolManager3],
            parent=self,
        )
        self.roiManagerWidget = ROIManagerWidget(
            self.roiCollection,
            image,
            self.plotWidget,
            parent=self,
            drawingState=self.roiDrawingState,
            images=self.config.imageList,
        )
        # Saved pixel points (from the pixel-spectra plots' Save Point)
        self.pointCollection = PointCollection()
        self.pointManagerWidget = PointManagerWidget(
            self.pointCollection, parent=self, image=image
        )
        # Lines (Draw Line) and their profiles
        self.lineCollection = LineCollection()
        shareLines(self.lineCollection, image)  # 3D Terrain can drape them
        self.lineManagerWidget = LineManagerWidget(self.lineCollection, image, self)
        self.profilePlot = ProfilePlotWidget(image, self)
        self.ratioExplorerConfig = RatioExplorerConfig()

        # --- Viewport context menu (place template) ---
        self.viewportContextMenuController = ViewportContextMenuController(
            self.roiManagerWidget, parent=self
        )
        for vp in (
            self.tripleRasterView.viewport1,
            self.tripleRasterView.viewport2,
            self.tripleRasterView.viewport3,
        ):
            vp.sigContextMenuRequested.connect(
                self.viewportContextMenuController.onContextMenuRequested
            )

    def _initUI(self):
        """Initialize the user interface for the workflow"""
        # The tab is named after the image so several workspaces stay tellable apart
        self.setWindowTitle(self.config.image.value.name)

        self._setupDocks()
        # Set the raster view as the central widget
        # self.setCentralWidget(self.tripleRasterView)

        self.setStatusBar(QStatusBar(self))

    def _setupDocks(self):
        """Setup all of the dock widgets for the workflow. This is most of the viewport_tools"""

        self.dockManager = ads.CDockManager(self)

        # dockArea = DockArea(self)
        # self.setCentralWidget(dockArea)
        # docks = []

        self.rasterDock = VardaDockWidget("Image View")
        # self.rasterDock.setFeature(
        #     ads.CDockWidget.DockWidgetFeature.DockWidgetClosable, False
        # )
        self.rasterDock.setWidget(self.tripleRasterView)

        # rasterDock = Dock("Raster Dock", widget=self.tripleRasterView, size=(800, 800))
        # docks.append(rasterDock)

        self.settingsDock = VardaDockWidget("Render Settings")
        self.settingsDock.setWidget(self.rendererSettingsPanel)

        # settingsDock = Dock(
        #     "Render Settings", widget=self.rendererSettingsPanel, size=(100, 100)
        # )
        # docks.append(settingsDock)

        self.roiDock = VardaDockWidget("ROI Manager")
        self.roiDock.setWidget(self.roiManagerWidget)
        self.pointsDock = VardaDockWidget("Points Manager")
        self.pointsDock.setWidget(self.pointManagerWidget)
        self.linesDock = VardaDockWidget("Lines")
        self.linesDock.setWidget(self.lineManagerWidget)
        self.profilesDock = VardaDockWidget("Profiles")
        self.profilesDock.setWidget(self.profilePlot)

        # roiDockNew = Dock("ROI Dock", widget=self.roiManagerWidget, size=(100, 100))
        # docks.append(roiDockNew)

        self.histogramDock = VardaDockWidget("Histogram")
        self.histogramDock.setWidget(self.histogram)

        # histogramDock = Dock("Histogram Dock", widget=self.histogram)
        # docks.append(histogramDock)

        self.plotDock = VardaDockWidget("ROI Plots")
        self.plotDock.setWidget(self.plotWidget)

        self.dockManager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea, self.rasterDock
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.LeftDockWidgetArea,
            self.settingsDock,
            self.rasterDock.dockAreaWidget(),
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.BottomDockWidgetArea,
            self.histogramDock,
            self.settingsDock.dockAreaWidget(),
        )

        self.dockManager.addDockWidget(
            ads.DockWidgetArea.BottomDockWidgetArea, self.roiDock
        )
        # Points Manager tabbed with the ROI Manager
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea,
            self.pointsDock,
            self.roiDock.dockAreaWidget(),
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea,
            self.linesDock,
            self.roiDock.dockAreaWidget(),
        )
        self.roiDock.setAsCurrentTab()

        self.dockManager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea,
            self.plotDock,
            self.roiDock.dockAreaWidget(),
        )
        # Pixel-spectra plots are tabbed alongside the ROI plot
        self.pixelSpectraDocks = PixelSpectraDocks(
            self.dockManager,
            self.plotDock,
            configurePlot=self._configurePixelPlot,
            parent=self,
        )
        viewports = [
            self.tripleRasterView.viewport1,
            self.tripleRasterView.viewport2,
            self.tripleRasterView.viewport3,
        ]
        # Every plotted pixel spectrum is marked on the image in its color;
        # saved points stay marked as circles
        self.pixelMarkers = PixelMarkerController(
            self.pixelSpectraDocks, viewports, parent=self
        )
        self.savedPointMarkers = SavedPointMarkers(
            self.pointCollection, viewports, parent=self
        )
        self.pointManagerWidget.sigSelectionChanged.connect(
            self.savedPointMarkers.highlight
        )
        self.ratioExplorer = RatioExplorerController(
            self.roiCollection,
            self.roiManagerWidget,
            self.pixelSpectraDocks.dedicated("Ratio Spectra"),
            self.ratioExplorerConfig,
            parent=self,
            showComponents=True,
        )
        # All three views show the same image: show the boxes in each
        self.ratioExplorer.setMirrorViewports(viewports)
        self.pixelSpectraDocks.newPlot()
        # Profiles tabbed with the ROI and pixel plots
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea,
            self.profilesDock,
            self.plotDock.dockAreaWidget(),
        )
        self.plotDock.setAsCurrentTab()
        self.lineDisplay = LineDisplay(self.lineCollection, viewports, parent=self)
        self.lineManagerWidget.sigSelectionChanged.connect(self._onLinesSelected)
        self.lineCollection.sigCollectionChanged.connect(self._updateProfiles)
        self.profilePlot.sigCursor.connect(self.lineDisplay.setCursor)

    def _configurePixelPlot(self, plot: PixelSpectraPlotWidget) -> None:
        # After the plot's "View" and "Pixel Spectra" sections
        plot.insertSidebarSection(2, self.ratioExplorer.createSidebarSection())
        plot.sigSavePointRequested.connect(functools.partial(self._savePoint, plot))

    def _savePoint(self, plot: PixelSpectraPlotWidget, curve) -> None:
        """Save a pixel curve's pixel to the Points Manager, in the curve's color."""
        origin = plot.pixelOrigins.get(curve)
        if origin is not None:
            self.pointManagerWidget.addPixel(
                origin.image,
                origin.x,
                origin.y,
                name=curve.plotDataItem.name(),
                color=curve.config.color.value,
            )

        # plotDock = Dock("Spectral Plot", widget=self.plotWidget, size=(400, 300))
        # docks.append(plotDock)

        # dockArea.addDock(rasterDock, "right")
        # dockArea.addDock(settingsDock, "left")
        # dockArea.addDock(roiDockNew, "bottom", settingsDock)
        # dockArea.addDock(histogramDock, "bottom", roiDockNew)
        # dockArea.addDock(plotDock, "bottom", rasterDock)

    def _connectSignals(self):
        """Connect signals between workflow components"""

        # Pixel and ratio tools (ROI drawing is wired by roiDrawingController)
        for tm in (self.toolManager1, self.toolManager2, self.toolManager3):
            tm.sigToolActivated.connect(self._onToolActivated)

        # Wire table selection to display controller highlight
        self.roiManagerWidget.sigSelectionChanged.connect(
            self.roiDisplayController.highlightROI
        )

    def _onToolActivated(self, tool) -> None:
        """Connect a pixel or ratio tool's signals when it is activated."""
        if isinstance(tool, PixelSelectTool):
            tool.sigPixelSelected.connect(
                functools.partial(self._onPixelSelected, tool.viewport.imageEntity)
            )
            # The window around the pixel is the active plot's
            tool.setWindowProvider(
                lambda: self.pixelSpectraDocks.active.pixelConfig.window()
            )
        elif isinstance(tool, RatioExplorerTool):
            self.ratioExplorer.bindTool(tool)
        elif isinstance(tool, LineDrawTool):
            tool.setKindProvider(self.lineManagerWidget.kind)
            tool.sigLineDrawn.connect(self._onLineDrawn)
        elif isinstance(tool, CropTool):
            tool.sigCropRequested.connect(
                functools.partial(self._onCropRequested, tool.viewport.imageEntity)
            )

    def _onLineDrawn(self, vertices, kind) -> None:
        fid = self.lineCollection.addLine(vertices, kind)
        self.lineManagerWidget.select([fid])  # its profile shows
        self.profilesDock.toggleView(True)
        self.profilesDock.setAsCurrentTab()

    def _onLinesSelected(self, fids: list[int]) -> None:
        self.lineDisplay.highlight(fids)
        self._updateProfiles()

    def _updateProfiles(self) -> None:
        """The selected lines' profiles, or every line's when none is selected."""
        selected = set(self.lineManagerWidget.selectedFids())
        lines = self.lineCollection.lines()
        self.profilePlot.setLines(
            [line for line in lines if line.fid in selected] if selected else lines
        )

    # --- sessions ---------------------------------------------------------------

    def sessionState(self) -> dict:
        return {
            "lines": self.lineCollection.toJson(),
            **plotLibrariesState(self.plotWidget, self.pixelSpectraDocks),
        }

    def restoreSessionState(self, state: dict) -> None:
        self.lineCollection.addFromJson(state.get("lines", []))
        restorePlotLibraries(state, self.plotWidget, self.pixelSpectraDocks)

    def _onCropRequested(self, image: VardaRaster, box: QRect) -> None:
        cropped = addCrop(image, box, self.config.imageList)
        self.setStatusMessage(cropMessage(cropped))

    def _onPixelSelected(self, image: VardaRaster, pos: QPointF) -> None:
        self.pixelSpectraDocks.addPixelSpectra([image], int(pos.x()), int(pos.y()))

    @property
    def pixelPlotWidget(self) -> PixelSpectraPlotWidget:
        """The pixel-spectra plot currently receiving selections."""
        return self.pixelSpectraDocks.active

    @property
    def pixelPlotDock(self) -> VardaDockWidget:
        return self.pixelSpectraDocks.activeDock

    def setStatusMessage(self, message):
        """Set a status message in the status bar"""
        self.statusBar().showMessage(message)

    def closeEvent(self, event):
        """Handle workflow closure"""
        self.roiDisplayController.cleanup()
        unshareLines(self.lineCollection)
        super().closeEvent(event)
