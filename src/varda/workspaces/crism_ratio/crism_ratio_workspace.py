"""The CRISM Ratio workspace: judge a region fast by ratioing it.

A browse image (usually a CRISM summary-parameter product, or any image
co-registered to the scene) is navigated on the left; the I/F cube is on the
right. Ctrl/Cmd+click a numerator box and a denominator box on either view:
the spectra are always measured on the I/F cube, and the numerator, the
denominator (with a blandness verdict) and the ratio are plotted. With column
lock on, each new numerator moves the denominator into its sensor column.
S saves the pair as ROIs, P the numerator pixel as a point, both named with
the verdict and described with every check's value and limit.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import MutableSequence, Sequence

import PyQt6Ads as ads
from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from varda.common.entities import VardaRaster
from varda.common.grid import sameGrid
from varda.common.parameter_widgets import groupWidget
from varda.common.ui import ElidingComboBox, MutedLabel, VardaDockWidget
from varda.image_loading.crism_geometry import IMAGE_COLUMNS, columnGeometryFor
from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.new_histogram_view import NewHistogramView
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.image_rendering.raster_view.viewport_tools.crop_tool import CropTool
from varda.image_rendering.raster_view.viewport_tools.pixel_select_tool import (
    PixelSelectTool,
)
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    RatioExplorerTool,
    RatioSelection,
)
from varda.image_rendering.raster_view.viewport_tools.tool_manager import ToolManager
from varda.plotting.plot import VardaPlotWidget
from varda.points.point_collection import PointCollection
from varda.points.point_manager_widget import PointManagerWidget
from varda.rois.roi_collection import ROICollection
from varda.rois.roi_manager_widget import ROIManagerWidget
from varda.workspaces.crism_ratio.mapping import dataToView, viewToData
from varda.workspaces.crism_ratio.ratio_plots import RatioPlotsPanel
from varda.workspaces.linked_images.linked_images_workspace import ViewGroupLink
from varda.workspaces.linked_images.linking import Link, linkWarnings
from varda.workspaces.plot_libraries import plotLibrariesState, restorePlotLibraries
from varda.workspaces.ratio_explorer import (
    RatioExplorerConfig,
    RatioExplorerController,
    centerPixel,
)

logger = logging.getLogger(__name__)

LINK_LABELS = {Link.PIXEL: "Pixel match", Link.MAP: "Map link"}


def columnSource(image: VardaRaster) -> str:
    """Where the image's detector columns come from: the backplane file, or
    :data:`IMAGE_COLUMNS` for an unprojected CRISM cube; "" when unknown."""
    geometry = columnGeometryFor(
        image.filePath, (image.height, image.width), image.crs is not None
    )
    if geometry is None or geometry.ir_sample.shape != (image.height, image.width):
        return ""
    return geometry.source or "backplane"


def hasBackplane(image: VardaRaster) -> bool:
    """Whether the image's detector columns are known (a CRISM backplane, or
    an unprojected CRISM cube's own columns)."""
    return bool(columnSource(image))


class _NoSpectrumPlots:
    """The ratio plots show the spectra; nothing goes to a pixel plot."""

    def addSpectra(self, entries: Sequence[tuple]) -> None:
        pass


class _ColumnLock:
    """What the controller needs of the denominator: the lock toggle, and the
    ROI manager's ratio reference."""

    def __init__(self, check: QCheckBox, roiManager: ROIManagerWidget) -> None:
        self._check, self._roiManager = check, roiManager

    @property
    def lockColumn(self) -> bool:
        return self._check.isChecked()

    def setDenominator(self, fid: int | None) -> None:
        self._roiManager.setDenominator(fid)


class CrismRatioWorkspace(QMainWindow):
    def __init__(
        self,
        browse: VardaRaster,
        data: VardaRaster,
        projectImages: MutableSequence[VardaRaster],
        link: Link,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.browse, self.data = browse, data
        self.projectImages = projectImages
        self.link = link
        self.setWindowTitle(f"{browse.name} | {data.name}")

        self.browseRenderer = ImageRenderer(image=browse)
        self.dataRenderer = ImageRenderer(image=data)
        self.browseView = ImageViewport(self.browseRenderer, parent=self)
        self.dataView = ImageViewport(self.dataRenderer, parent=self)
        self.viewLink = ViewGroupLink(
            [self.browseView, self.dataView], [browse, data], link, self
        )

        # ROIs and points live on the I/F cube: the boxes are in its pixels
        self.roiCollection = ROICollection.fromImage(data)
        self.pointCollection = PointCollection()
        self.roiPlot = VardaPlotWidget(parent=self)
        self.roiManager = ROIManagerWidget(
            self.roiCollection,
            data,
            self.roiPlot,
            parent=self,
            images=projectImages,
        )
        self.pointManager = PointManagerWidget(self.pointCollection, parent=self)

        self.config = RatioExplorerConfig()
        self.plots = RatioPlotsPanel()
        self.lockCheck = QCheckBox("Lock denominator to the numerator's sensor column")
        self.lockStatus = QLabel()
        self._initLock()
        self.controller = RatioExplorerController(
            self.roiCollection,
            _ColumnLock(self.lockCheck, self.roiManager),
            _NoSpectrumPlots(),
            self.config,
            parent=self,
            imagesFor=lambda _clicked: [data],  # always the I/F cube
            nameSuffix=self._verdictSuffix,
            describe=self.plots.readout.describe,
        )
        self.controller.sigComponents.connect(self.plots.showComponents)

        self.toolManagers = [
            self._toolManager(0, self.browseView),
            self._toolManager(1, self.dataView),
        ]
        self._arrange()
        self._initLinkNotice()
        for manager in self.toolManagers:
            manager.select(RatioExplorerTool)

    # --- construction --------------------------------------------------------------

    def _initLock(self) -> None:
        self.columnSource = columnSource(self.data)
        available = bool(self.columnSource)
        self.lockCheck.setEnabled(available)
        self.lockCheck.setChecked(available)
        self.lockCheck.toggled.connect(self._showLockStatus)
        if not available:
            self.lockCheck.setToolTip(
                f"No CRISM backplane with detector columns (an IN or DDR file "
                f"with an IR Sample band) was found next to {self.data.name}, and "
                "it is not an unprojected CRISM cube (no map projection, 640, 320 "
                "or 160 columns), so the denominator can't be placed by sensor column."
            )
        else:
            self.lockCheck.setToolTip(f"Detector columns from {self.columnSource}")
        status = self.statusBar()
        assert status is not None
        status.addPermanentWidget(self.lockStatus)
        self._showLockStatus()

    def _showLockStatus(self) -> None:
        if not self.lockCheck.isEnabled():
            text = f"Column lock unavailable: no detector columns for {self.data.name}"
        elif self.lockCheck.isChecked():
            text = (
                "Column lock on (image columns)"
                if self.columnSource == IMAGE_COLUMNS
                else "Column lock on"
            )
        else:
            text = "Column lock off"
        self.lockStatus.setText(text)

    def _initLinkNotice(self) -> None:
        """What isn't linked as asked: a notice across the top, not a dialog."""
        warnings = linkWarnings([self.browse, self.data], self.link)
        self.linkNotice = QLabel(" ".join(warnings))
        self.linkNotice.setWordWrap(True)
        self.linkNotice.setContentsMargins(8, 4, 8, 4)
        self.linkNotice.setStyleSheet(
            "background: rgba(255, 196, 0, 0.18); border-bottom: 1px solid "
            "rgba(255, 196, 0, 0.6);"
        )
        self.linkNotice.setVisible(bool(warnings))
        self.setMenuWidget(self.linkNotice)

    def _toolManager(self, index: int, view: ImageViewport) -> ToolManager:
        manager = ToolManager(
            view,
            self,
            excludeCategories=("ROI Drawing", "Line Drawing"),
            excludeTools=(PixelSelectTool, CropTool),
        )
        view.addToolBar(manager.getToolbar())
        manager.sigToolActivated.connect(
            functools.partial(self._onToolActivated, index)
        )
        return manager

    def _controls(self) -> QWidget:
        hint = MutedLabel(
            "Ctrl/⌘+left-click: numerator · Ctrl/⌘+right-click: denominator · "
            "S: save as ROIs · P: save numerator pixel as a point · Esc: clear"
        )
        hint.setWordWrap(True)
        controls = QWidget()
        layout = QVBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(groupWidget(self.config))
        layout.addWidget(self.lockCheck)
        layout.addWidget(hint)
        layout.addWidget(self.plots, 1)
        return controls

    def _arrange(self) -> None:
        self.dockManager = ads.CDockManager(self)
        self.dockManager.setAutoHideConfigFlags(
            ads.CDockManager.eAutoHideFlag.DefaultAutoHideConfig
        )
        self.browseDock = VardaDockWidget(f"Browse: {self.browse.name}").withWidget(
            self.browseView
        )
        self.dataDock = VardaDockWidget(f"I/F: {self.data.name}").withWidget(
            self.dataView
        )
        self.plotsDock = VardaDockWidget("Ratio Plots").withWidget(self._controls())
        for dock in (self.browseDock, self.dataDock):  # the views stay in the window
            dock.setFeature(ads.CDockWidget.DockWidgetFeature.DockWidgetClosable, False)
        browseArea = self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea, self.browseDock
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea, self.dataDock, browseArea
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea, self.plotsDock
        )
        self.dockManager.setSplitterSizes(browseArea, [3, 3])
        root = self.dockManager.rootSplitter()
        if root is not None and root.count() == 2:
            root.setSizes([700, 500])

        # The managers and the display settings stay out of the way until wanted
        self.roiDock = VardaDockWidget("ROI Manager").withWidget(self.roiManager)
        self.pointsDock = VardaDockWidget("Points Manager").withWidget(
            self.pointManager
        )
        self.roiPlotDock = VardaDockWidget("ROI Plots").withWidget(self.roiPlot)
        for dock in (self.roiDock, self.pointsDock, self.roiPlotDock):
            self.dockManager.addAutoHideDockWidget(
                ads.SideBarLocation.SideBarRight, dock
            )
        for name, renderer in (
            ("Browse", self.browseRenderer),
            ("I/F", self.dataRenderer),
        ):
            for title, widget in (
                (f"{name} Render Settings", renderer.getSettingsPanel()),
                (f"{name} Histogram", NewHistogramView(renderer, self)),
            ):
                self.dockManager.addAutoHideDockWidget(
                    ads.SideBarLocation.SideBarBottom,
                    VardaDockWidget(title).withWidget(widget),
                )

    # --- the click loop ------------------------------------------------------------

    def _onToolActivated(self, index: int, tool: object) -> None:
        if not isinstance(tool, RatioExplorerTool):
            return
        self.controller.bindTool(tool, self.data)
        if index == 0:  # the browse view: its clicks and boxes go through the link
            tool.setClickMapper(self._browseToData)
            tool.setBoxProjector(
                lambda polygon: dataToView(self.browse, self.data, self.link, polygon)
            )
        # both views show the one pair, so a click on either continues it
        tool.sigSelectionChanged.connect(functools.partial(self._syncViews, tool))
        tool.sigSavePointRequested.connect(functools.partial(self._savePoint, tool))

    def _browseToData(self, x: float, y: float) -> tuple[float, float] | None:
        mapped = viewToData(self.browse, self.data, self.link, [[x, y]])
        return None if mapped is None else (float(mapped[0, 0]), float(mapped[0, 1]))

    def _syncViews(self, source: object, selection: RatioSelection) -> None:
        for manager in self.toolManagers:
            other = manager.activeTool
            if isinstance(other, RatioExplorerTool) and other is not source:
                other.showSelection(selection)

    def _verdictSuffix(self, denominator: object = True) -> str:
        """The denominator's verdict, to put in the names of what is saved
        (none when there is no denominator: the readout would be stale)."""
        readout = self.plots.readout
        if denominator is None or not readout.lastResult.checks:
            return ""
        mark = "✓" if readout.lastResult.passed else "✗"
        return f" (denominator {mark} {readout.currentCheckName()})"

    def _savePoint(self, tool: RatioExplorerTool) -> None:
        numerator = tool.selection.numerator
        if numerator is None:
            return
        col, row = centerPixel(numerator)
        denominator = tool.selection.denominator
        self.pointCollection.addPixel(
            self.data,
            col,
            row,
            name=f"Ratio numerator ({col}, {row}){self._verdictSuffix(denominator)}",
            description="" if denominator is None else self.plots.readout.describe(),
        )

    # --- sessions --------------------------------------------------------------------

    def sessionState(self) -> dict:
        return {
            "link": self.link.name,
            "boxSize": [
                int(self.config.boxWidth.value),
                int(self.config.boxHeight.value),
            ],
            "lockColumn": self.lockCheck.isChecked(),
            "blandness": self.plots.readout.currentCheckName(),
            "blandnessLimits": self.plots.readout.limitOverrides(),
            **plotLibrariesState(self.plots.ratioPlot),
        }

    def restoreSessionState(self, state: dict) -> None:
        width, height = state.get("boxSize", (None, None))
        if width is not None and height is not None:
            self.config.boxWidth.set(int(width))
            self.config.boxHeight.set(int(height))
        if self.lockCheck.isEnabled():
            self.lockCheck.setChecked(bool(state.get("lockColumn", True)))
        self.plots.readout.checkCombo.setCurrentText(
            state.get("blandness", self.plots.readout.currentCheckName())
        )
        limits = state.get("blandnessLimits", {})
        if isinstance(limits, dict):
            self.plots.readout.setLimitOverrides(limits)
        restorePlotLibraries(state, self.plots.ratioPlot)


class NewCrismRatioDialog(QDialog):
    """Choose the browse image, the I/F image and how to link them."""

    def __init__(self, images: Sequence[VardaRaster], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("New CRISM Ratio Workspace")
        self.images = list(images)
        intro = QLabel(
            "Navigate on a browse image (for example a CRISM summary-parameter "
            "product) and ratio spectra measured on the I/F cube."
        )
        intro.setWordWrap(True)
        browse = next(
            (i for i in self.images if "_sr" in i.name.lower()),
            self.images[0] if self.images else None,
        )
        others = [i for i in self.images if i is not browse]
        data = next(
            (i for i in others if hasBackplane(i)), others[0] if others else None
        )
        self.browseBox, self.dataBox = ElidingComboBox(), ElidingComboBox()
        for box, default in ((self.browseBox, browse), (self.dataBox, data)):
            for image in self.images:
                box.addItem(image.name, image)
            box.setCurrentIndex(self.images.index(default) if default else 0)
            box.currentIndexChanged.connect(self._refresh)
        self.linkBox = ElidingComboBox()
        for link, label in LINK_LABELS.items():
            self.linkBox.addItem(label, link)
        self.linkNote = QLabel()
        self.linkNote.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow("Browse image:", self.browseBox)
        form.addRow("I/F image:", self.dataBox)
        form.addRow("Link:", self.linkBox)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.linkNote)
        layout.addWidget(buttons)
        self._refresh()

    def browse(self) -> VardaRaster:
        return self.browseBox.currentData()

    def data(self) -> VardaRaster:
        return self.dataBox.currentData()

    def link(self) -> Link:
        return self.linkBox.currentData()

    def pixelAvailable(self) -> bool:
        """Pixel match pairs the same pixel, so it needs one grid."""
        return sameGrid(self.browse(), self.data())

    def mapAvailable(self) -> bool:
        """Map link needs both images georeferenced: without it the mapping
        would quietly fall back to pixels."""
        return self.browse().hasGeospatialData and self.data().hasGeospatialData

    def _refresh(self) -> None:
        available = {Link.PIXEL: self.pixelAvailable(), Link.MAP: self.mapAvailable()}
        model = self.linkBox.model()
        assert isinstance(model, QStandardItemModel)
        for row, link in enumerate(available):
            item = model.item(row)
            assert item is not None
            item.setEnabled(available[link])
        if not available[self.link()]:
            usable = [link for link, ok in available.items() if ok]
            if usable:
                self.linkBox.setCurrentIndex(list(LINK_LABELS).index(usable[0]))
        browse, data = self.browse(), self.data()
        plain = [i.name for i in (browse, data) if not i.hasGeospatialData]
        if not any(available.values()):
            note = (
                "These images are on different grids and "
                f"{' and '.join(plain)} {'has' if len(plain) == 1 else 'have'} no "
                "georeferencing, so they can't be linked. Choose images on one "
                "grid, or georeferenced ones."
            )
        elif not available[Link.MAP]:
            note = (
                f"Map link needs georeferencing, which {' and '.join(plain)} "
                f"{'lacks' if len(plain) == 1 else 'lack'}: linked by pixel."
            )
        elif not available[Link.PIXEL]:
            note = (
                "The images are on different grids, so they are linked by map location."
            )
        else:
            note = ""
        self.linkNote.setText(note)
        self.linkNote.setVisible(bool(note))

    def _accept(self) -> None:
        problem = None
        if self.browse() is self.data():
            problem = "Choose two different images."
        elif not (self.pixelAvailable() or self.mapAvailable()):
            problem = self.linkNote.text()
        if problem:
            QMessageBox.information(self, self.windowTitle(), problem)
            return
        self.accept()
