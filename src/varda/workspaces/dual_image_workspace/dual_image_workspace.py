# standard library
import functools
import logging
from collections.abc import Sequence
from enum import Enum

# third party imports
from PyQt6.QtCore import QPointF, QRect
from PyQt6.QtWidgets import QMainWindow
import PyQt6Ads as ads

# local imports
from varda.common.parameter import (
    ImageParameter,
    ParameterGroup,
    EnumParameter,
)
from varda.common.entities import VardaRaster
from varda.image_rendering.raster_view import (
    ImageViewport,
    ROIDisplayController,
    ViewportLinkController,
    LinkMode,
)
from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.new_histogram_view import NewHistogramView
from varda.image_rendering.raster_view.viewport_context_menu_controller import (
    ViewportContextMenuController,
)
from varda.common.ui import SectionBox, VardaDockWidget
from varda.image_rendering.raster_view.viewport_tools.pixel_select_tool import (
    PixelSelectTool,
)
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    RatioExplorerTool,
)
from varda.image_rendering.raster_view.viewport_tools.crop_tool import CropTool
from varda.image_rendering.raster_view.viewport_tools.tool_manager import ToolManager
from varda.rois.roi_collection import ROICollection
from varda.rois.roi_drawing_state import ROIDrawingState
from varda.rois.roi_drawing_widgets import ROIDrawingController
from varda.rois.roi_manager_widget import ROIManagerWidget
from varda.plotting.pixel_spectra_plot import PixelSpectraPlotWidget
from varda.plotting.plot import VardaPlotWidget
from varda.points.point_collection import PointCollection
from varda.points.point_manager_widget import PointManagerWidget
from varda.workspaces.raster_overlay import OverlayBar
from varda.workspaces.cropping import addCrop, cropMessage
from varda.workspaces.pixel_markers import PixelMarkerController
from varda.workspaces.pixel_spectra_docks import PixelSpectraDocks
from varda.workspaces.plot_libraries import plotLibrariesState, restorePlotLibraries
from varda.workspaces.saved_point_markers import SavedPointMarkers
from varda.workspaces.ratio_explorer import (
    RatioExplorerConfig,
    RatioExplorerController,
)
from varda.common.parameter_widgets import groupWidget

logger = logging.getLogger(__name__)


# Lines and profiles aren't offered here yet (the two images may be on
# different pixel grids)
NO_LINES = ("Line Drawing",)


class DisplayMode(Enum):
    SIDE_BY_SIDE = 1
    OVERLAY = 2


class PixelSpectrumSource(Enum):
    """Which image(s) a pixel selection plots, regardless of the viewport clicked."""

    CLICKED_VIEWPORT = 1
    PRIMARY = 2
    SECONDARY = 3
    BOTH = 4


class PixelSourceConfig(ParameterGroup):
    source = EnumParameter(
        "Spectrum Source",
        PixelSpectrumSource,
        PixelSpectrumSource.CLICKED_VIEWPORT,
        "Which image's spectrum a Ctrl+click plots. The images are assumed to be "
        "co-registered, so the clicked pixel is looked up in both.",
    )


class DualImageWorkspaceConfig(ParameterGroup):
    imageList: Sequence[VardaRaster]

    image1Param: ImageParameter = ImageParameter(
        "Primary Image",
        "Primary Image for Workspace (Usually a Spectral Image)",
    )
    image2Param: ImageParameter = ImageParameter(
        "Secondary Image",
        "Secondary Image for Workspace (Usually a Band Parameter Image)",
    )
    displayModeParam: EnumParameter = EnumParameter(
        "Display Mode",
        DisplayMode,
        DisplayMode.SIDE_BY_SIDE,
        "Display Mode for Dual Image Workspace",
    )
    linkModeParam: EnumParameter = EnumParameter(
        "Sync Mode",
        LinkMode,
        LinkMode.PIXEL,
        "Whether to link the images by pixel or geographic coordinates.",
    )

    def __init__(self, imageList: Sequence[VardaRaster]) -> None:
        super().__init__()
        self.imageList = imageList
        self.image1Param.setProvider(lambda: self.imageList)
        self.image2Param.setProvider(lambda: self.imageList)


class DualImageWorkspace(QMainWindow):
    def __init__(self, config: DualImageWorkspaceConfig, parent=None):
        super().__init__(parent)
        self.projectImages = config.imageList
        self.image1 = config.image1Param.get()
        self.image2 = config.image2Param.get()
        self.displayMode = config.displayModeParam.get()
        self.linkMode = config.linkModeParam.get()
        # The tab is named after the images so several workspaces stay tellable apart
        self.setWindowTitle(f"{self.image1.name} | {self.image2.name}")

        self.viewportLinkController = None

        self._initComponents()
        self._initUI()
        self._connectSignals()

    def _initComponents(self):
        self.primaryRenderer = ImageRenderer(image=self.image1)
        self.secondaryRenderer = ImageRenderer(image=self.image2)

        # Histograms self-wire to their renderer's sigShouldRefresh.
        self.primaryHistogram = NewHistogramView(self.primaryRenderer, self)
        self.secondaryHistogram = NewHistogramView(self.secondaryRenderer, self)

        # ROI system — collection uses secondary image's CRS/transform
        # (both images assumed to share the same transform)
        self.roiCollection = ROICollection.fromImage(self.image2)
        self.roiDisplayController = ROIDisplayController(
            self.roiCollection, parent=self
        )
        self.plotWidget = VardaPlotWidget(parent=self)
        self.pixelSourceConfig = PixelSourceConfig()
        self.ratioExplorerConfig = RatioExplorerConfig()
        # Shape and class of the next drawn ROIs (see _connectSignals)
        self.roiDrawingState = ROIDrawingState(self.roiCollection)
        self.roiManagerWidget = ROIManagerWidget(
            self.roiCollection,
            self.image1,
            self.plotWidget,
            parent=self,
            drawingState=self.roiDrawingState,
            images=self.projectImages,
        )
        # Saved pixel points (from the pixel-spectra plots' Save Point)
        self.pointCollection = PointCollection()
        self.pointManagerWidget = PointManagerWidget(self.pointCollection, parent=self)

    def _initUI(self):
        if self.displayMode == DisplayMode.SIDE_BY_SIDE:
            self._initSideBySide()
        elif self.displayMode == DisplayMode.OVERLAY:
            self._initOverlay()

    def _setupDocks(self):
        self.dockManager = ads.CDockManager(self)
        self.dockManager.setAutoHideConfigFlags(
            ads.CDockManager.eAutoHideFlag.DefaultAutoHideConfig
        )

        self.roiDock = VardaDockWidget("ROI Manager")
        self.roiDock.setWidget(self.roiManagerWidget)
        self.pointsDock = VardaDockWidget("Points Manager")
        self.pointsDock.setWidget(self.pointManagerWidget)

        self.plotDock = VardaDockWidget("ROI Plots")
        self.plotDock.setWidget(self.plotWidget)

        self.primarySettingsDock = VardaDockWidget("Primary Render Settings")
        self.primarySettingsDock.setWidget(self.primaryRenderer.getSettingsPanel())

        self.secondarySettingsDock = VardaDockWidget("Secondary Render Settings")
        self.secondarySettingsDock.setWidget(self.secondaryRenderer.getSettingsPanel())

        self.primaryHistogramDock = VardaDockWidget("Primary Histogram")
        self.primaryHistogramDock.setWidget(self.primaryHistogram)

        self.secondaryHistogramDock = VardaDockWidget("Secondary Histogram")
        self.secondaryHistogramDock.setWidget(self.secondaryHistogram)

    def _initSideBySide(self):
        self.viewport1 = ImageViewport(self.primaryRenderer, parent=self)
        self.viewport2 = ImageViewport(self.secondaryRenderer, parent=self)

        self.toolManager1 = ToolManager(
            self.viewport1, self, excludeCategories=NO_LINES
        )
        self.toolManager2 = ToolManager(
            self.viewport2, self, excludeCategories=NO_LINES
        )
        self.viewport1.addToolBar(self.toolManager1.getToolbar())
        self.viewport2.addToolBar(self.toolManager2.getToolbar())
        # Each side can have its own result laid over it
        self.overlayBar1 = OverlayBar(
            self.projectImages, self.image1, [self.viewport1], self
        )
        self.overlayBar2 = OverlayBar(
            self.projectImages, self.image2, [self.viewport2], self
        )
        self.viewport1.addToolBar(self.overlayBar1)
        self.viewport2.addToolBar(self.overlayBar2)

        self.roiDisplayController.registerViewport("primary", self.viewport1)
        self.roiDisplayController.registerViewport("secondary", self.viewport2)

        self.viewportLinkController = ViewportLinkController(
            self.viewport1, self.viewport2, self.linkMode, parent=self
        )

        # Drawing on either viewport creates ROIs (the images are co-registered,
        # so an ROI drawn on the primary maps into the shared collection too).
        self._drawingToolManagers = [self.toolManager1, self.toolManager2]

        self._setupDocks()

        self.viewport1Dock = VardaDockWidget(f"Primary: {self.image1.name}")
        self.viewport1Dock.setWidget(self.viewport1)

        self.viewport2Dock = VardaDockWidget(f"Secondary: {self.image2.name}")
        self.viewport2Dock.setWidget(self.viewport2)

        # Top row: two viewports side by side
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea,
            self.viewport1Dock,
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea,
            self.viewport2Dock,
            self.viewport1Dock.dockAreaWidget(),
        )

        # Settings panels as auto-hide on the bottom sidebar
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.BottomDockWidgetArea,
            self.primarySettingsDock,
            self.viewport1Dock.dockAreaWidget(),
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.BottomDockWidgetArea,
            self.secondarySettingsDock,
            self.viewport2Dock.dockAreaWidget(),
        )

        # Tab each histogram alongside its matching render-settings panel
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea,
            self.primaryHistogramDock,
            self.primarySettingsDock.dockAreaWidget(),
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea,
            self.secondaryHistogramDock,
            self.secondarySettingsDock.dockAreaWidget(),
        )

        # Bottom row: ROI manager and plot side by side
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.BottomDockWidgetArea, self.roiDock
        )
        # Points Manager tabbed with the ROI Manager
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea,
            self.pointsDock,
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
        # Every plotted pixel spectrum is marked on both views in its color;
        # saved points stay marked as circles
        self.pixelMarkers = PixelMarkerController(
            self.pixelSpectraDocks, self._allViewports(), parent=self
        )
        self.savedPointMarkers = SavedPointMarkers(
            self.pointCollection, self._allViewports(), parent=self
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
            imagesFor=self._imagesForSource,  # honors Spectrum Source
            labelWithImageName=True,
        )
        # The images are co-registered: show the boxes on both views
        self.ratioExplorer.setMirrorViewports(self._allViewports())
        self.pixelSpectraDocks.newPlot()
        self.dockManager.setSplitterSizes(self.viewport1Dock.dockAreaWidget(), [4, 1])
        # Within each viewport column, give viewport more space than its settings
        viewport1Splitter = self.viewport1Dock.dockAreaWidget().parentSplitter()
        if viewport1Splitter:
            viewport1Splitter.setSizes([500, 150])
        viewport2Splitter = self.viewport2Dock.dockAreaWidget().parentSplitter()
        if viewport2Splitter:
            viewport2Splitter.setSizes([500, 150])

    def _initOverlay(self):
        self.viewport1 = ImageViewport(self.primaryRenderer, self)
        self.viewport1.overlayImage(self.secondaryRenderer)

        self.toolManager1 = ToolManager(
            self.viewport1, self, excludeCategories=NO_LINES
        )
        self.viewport1.addToolBar(self.toolManager1.getToolbar())

        self.roiDisplayController.registerViewport("overlay", self.viewport1)

        self._drawingToolManagers = [self.toolManager1]

        self._setupDocks()

        self.viewport1Dock = VardaDockWidget(
            f"Overlay: {self.image1.name} + {self.image2.name}"
        )
        self.viewport1Dock.setWidget(self.viewport1)

        # Viewport as the main area
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea, self.viewport1Dock
        )

        # Both settings panels and histograms as auto-hide on the bottom sidebar
        self.dockManager.addAutoHideDockWidget(
            ads.SideBarLocation.SideBarBottom, self.primarySettingsDock
        )
        self.dockManager.addAutoHideDockWidget(
            ads.SideBarLocation.SideBarBottom, self.secondarySettingsDock
        )
        self.dockManager.addAutoHideDockWidget(
            ads.SideBarLocation.SideBarBottom, self.primaryHistogramDock
        )
        self.dockManager.addAutoHideDockWidget(
            ads.SideBarLocation.SideBarBottom, self.secondaryHistogramDock
        )

        # Bottom row: ROI manager and plot side by side
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.BottomDockWidgetArea, self.roiDock
        )
        # Points Manager tabbed with the ROI Manager
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea,
            self.pointsDock,
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
        # Every plotted pixel spectrum is marked on both views in its color;
        # saved points stay marked as circles
        self.pixelMarkers = PixelMarkerController(
            self.pixelSpectraDocks, self._allViewports(), parent=self
        )
        self.savedPointMarkers = SavedPointMarkers(
            self.pointCollection, self._allViewports(), parent=self
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
            imagesFor=self._imagesForSource,  # honors Spectrum Source
            labelWithImageName=True,
        )
        # The images are co-registered: show the boxes on both views
        self.ratioExplorer.setMirrorViewports(self._allViewports())
        self.pixelSpectraDocks.newPlot()

        # Give viewport most vertical space, settings and ROI/plot less
        rootSplitter = self.dockManager.rootSplitter()
        rootSplitter.setSizes([600, 200])

    def _connectSignals(self):
        # Draw ROI on either viewport (or the single overlay viewport); pixel
        # and ratio tools are wired in _onToolActivated
        self.roiDrawingController = ROIDrawingController(
            self.roiDrawingState, self._drawingToolManagers, parent=self
        )
        for tm in self._drawingToolManagers:
            tm.sigToolActivated.connect(self._onToolActivated)

        # Wire table selection to highlight
        self.roiManagerWidget.sigSelectionChanged.connect(
            self.roiDisplayController.highlightROI
        )

        # Wire viewport right-click -> template-placement context menu. The
        # collection and viewports share a transform (co-registered images), so
        # a click on either pane maps to the same ROI pixel space.
        self.viewportContextMenuController = ViewportContextMenuController(
            self.roiManagerWidget, parent=self
        )
        for vp in self._allViewports():
            vp.sigContextMenuRequested.connect(
                self.viewportContextMenuController.onContextMenuRequested
            )

    def _allViewports(self) -> list[ImageViewport]:
        """Both viewports side by side, or the single overlay viewport."""
        viewports = [self.viewport1]
        if hasattr(self, "viewport2"):
            viewports.append(self.viewport2)
        return viewports

    def _onToolActivated(self, tool) -> None:
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
        elif isinstance(tool, CropTool):
            tool.sigCropRequested.connect(
                functools.partial(self._onCropRequested, tool.viewport.imageEntity)
            )

    def _onCropRequested(self, image: VardaRaster, box: QRect) -> None:
        cropped = addCrop(image, box, self.projectImages)
        self.statusBar().showMessage(cropMessage(cropped))

    def _configurePixelPlot(self, plot: PixelSpectraPlotWidget) -> None:
        # After the plot's "View" and "Pixel Spectra" sections
        plot.insertSidebarSection(
            2, SectionBox("Dual Image", groupWidget(self.pixelSourceConfig))
        )
        plot.insertSidebarSection(3, self.ratioExplorer.createSidebarSection())
        plot.sigSavePointRequested.connect(functools.partial(self._savePoint, plot))

    def _savePoint(self, plot: PixelSpectraPlotWidget, curve) -> None:
        """Save a pixel curve's pixel to the Points Manager, in the curve's color."""
        origin = plot.pixelOrigins.get(curve)
        if origin is not None:
            self.pointCollection.addPixel(
                origin.image,
                origin.x,
                origin.y,
                name=curve.plotDataItem.name(),
                color=curve.config.color.value,
            )

    def _imagesForSource(self, clickedImage: VardaRaster) -> list[VardaRaster]:
        """The image(s) a selection on ``clickedImage`` reads, per Spectrum Source.
        Shared by Pixel Select and the Ratio Explorer."""
        source = self.pixelSourceConfig.source.value
        assert isinstance(source, PixelSpectrumSource)
        return {
            PixelSpectrumSource.CLICKED_VIEWPORT: [clickedImage],
            PixelSpectrumSource.PRIMARY: [self.image1],
            PixelSpectrumSource.SECONDARY: [self.image2],
            PixelSpectrumSource.BOTH: [self.image1, self.image2],
        }[source]

    def _onPixelSelected(self, clickedImage: VardaRaster, pos: QPointF) -> None:
        self.pixelSpectraDocks.addPixelSpectra(
            self._imagesForSource(clickedImage),
            int(pos.x()),
            int(pos.y()),
            labelWithImageName=True,
        )

    @property
    def pixelPlotWidget(self) -> PixelSpectraPlotWidget:
        """The pixel-spectra plot currently receiving selections."""
        return self.pixelSpectraDocks.active

    @property
    def pixelPlotDock(self) -> VardaDockWidget:
        return self.pixelSpectraDocks.activeDock

    # --- sessions --------------------------------------------------------------------

    def sessionState(self) -> dict:
        return plotLibrariesState(self.plotWidget, self.pixelSpectraDocks)

    def restoreSessionState(self, state: dict) -> None:
        restorePlotLibraries(state, self.plotWidget, self.pixelSpectraDocks)

    def closeEvent(self, event):
        if self.viewportLinkController is not None:
            self.viewportLinkController.cleanup()
        self.roiDisplayController.cleanup()
        super().closeEvent(event)


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    from varda.utilities import debug

    app = QApplication(sys.argv)
    imageList = [
        debug.generate_random_image(),
        debug.generate_random_image(),
    ]
    config = DualImageWorkspaceConfig(imageList)
    workspace = DualImageWorkspace(config)
    workspace.setMaximumSize(1200, 800)
    workspace.show()
    sys.exit(app.exec())
