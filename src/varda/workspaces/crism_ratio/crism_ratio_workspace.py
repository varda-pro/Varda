"""The CRISM Ratio workspace: judge a region fast by ratioing it.

A browse image (usually a CRISM summary-parameter product, or any image
co-registered to the scene) is navigated on the left; the I/F cube is on the
right. Ctrl/Cmd+click a numerator box and a denominator box on either view:
the spectra are always measured on the I/F cube; the numerator and the
denominator are plotted together, the ratio beside them, and the denominator's
blandness verdict in a strip below. Every panel is a dock, and where they are
is kept with the session. With column
lock on, each new numerator moves the denominator into its sensor column.
S saves the pair as ROIs, P the numerator pixel as a point, both named with
the verdict and described with every check's value and limit. Both ask for a
class, prefilled with the reference mineral on the Ratio plot (Enter accepts);
the numerator ROI gets that class, the denominator the Denominator class.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, MutableSequence, Sequence

import numpy as np
import PyQt6Ads as ads
from PyQt6.QtCore import QByteArray, QSize, QTimer
from PyQt6.QtGui import QShowEvent, QStandardItemModel
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from varda.common.entities import Color, VardaRaster
from varda.common.grid import sameGrid
from varda.common.parameter_widgets import (
    IntParameterWidget,
    connectWhileAlive,
    parameterWidget,
)
from varda.common.ui import (
    ElidingComboBox,
    FlowLayout,
    PanelScrollArea,
    VardaDockWidget,
)
from varda.image_loading.crism_geometry import (
    IMAGE_COLUMNS,
    ColumnGeometry,
    columnGeometryFor,
)
from varda.image_loading.crism_geometry_chooser import (
    CHOOSE_TEXT,
    chooseGeometryButton,
)
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
from varda.workspaces.crism_ratio.ratio_plots import PlotShape, RatioPlots
from varda.workspaces.crism_ratio.ratio_classes import (
    DENOMINATOR_CLASS,
    DENOMINATOR_CLASS_COLOR,
    nextClassColor,
    roiColor,
)
from varda.workspaces.crism_ratio.ratio_classes import (
    suggestedClass as suggestedLibraryClass,
)
from varda.workspaces.crism_ratio.save_dialog import SaveRatioDialog
from varda.spectral_libraries.spectrum import LibrarySpectrum
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
KEYS_HINT = (
    "Ctrl/⌘+left-click: numerator box · Ctrl/⌘+right-click: denominator box "
    "(on either view) · S: save the pair as ROIs · P: save the numerator pixel "
    "as a point (both ask for a class: Enter saves; Shift+S and Shift+P always "
    "ask) · Esc: clear"
)


def columnSource(image: VardaRaster) -> str:
    """Where the image's detector columns come from: the backplane file, or
    :data:`IMAGE_COLUMNS` for an unprojected CRISM cube; "" when unknown."""
    return _sourceOf(columnGeometryFor(image), image)


def _sourceOf(geometry: ColumnGeometry | None, image: VardaRaster) -> str:
    if geometry is None or geometry.ir_sample.shape != (image.height, image.width):
        return ""
    return geometry.source or "backplane"


def hasBackplane(image: VardaRaster) -> bool:
    """Whether the image's detector columns are known (a CRISM backplane, or
    an unprojected CRISM cube's own columns)."""
    return bool(columnSource(image))


def _row(*widgets: QWidget) -> QWidget:
    """``widgets`` side by side, kept together."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    for widget in widgets:
        layout.addWidget(widget)
    return row


class _StripScrollArea(PanelScrollArea):
    """A strip a row or two tall (the controls, the blandness readout): its
    dock can be as short as its contents, not a scroll area's usual
    minimum; it scrolls when squeezed further."""

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(hint.width(), min(hint.height(), self.neededHeight(self.width())))

    def neededHeight(self, width: int) -> int:
        """The panel's height at ``width`` (its minimum width when 0)."""
        panel = self.panel
        width = max(width, panel.minimumSizeHint().width())
        needed = panel.heightForWidth(width) if panel.hasHeightForWidth() else -1
        return max(needed, panel.minimumSizeHint().height()) + 2 * self.frameWidth()


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
        self.pointManager = PointManagerWidget(
            self.pointCollection, parent=self, image=data
        )

        self.config = RatioExplorerConfig()
        self.plots = RatioPlots(self)
        self.lockCheck = QCheckBox("Lock to sensor column")
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
            onSaveRequested=self._requestSavePair,
        )
        # what S and P save as: asked each time, or the suggested class at once
        self.askClass = True
        self.lastClass = ""
        self.saveDialog: SaveRatioDialog | None = None
        self._saveChosen: Callable[[str, str], None] | None = None
        self._pendingPoint: tuple[int, int, str] | None = None
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
        self.columnSource = ""
        self.chooseGeometryButton = chooseGeometryButton(self.data, parent=self)
        self.lockCheck.toggled.connect(self._showLockStatus)
        status = self.statusBar()
        assert status is not None
        status.addPermanentWidget(self.lockStatus)
        self._refreshLock()
        # a geometry file chosen here or anywhere else enables the lock at once
        # the image outlives the workspace
        connectWhileAlive(self.data.sigMetadataChanged, self._refreshLock, self)

    def _refreshLock(self) -> None:
        """Enable the lock when the I/F cube's detector columns are known
        (and turn it on when it becomes available); otherwise say why and
        offer to choose the geometry file."""
        wasAvailable = bool(self.columnSource)
        geometry = columnGeometryFor(self.data)
        self.columnSource = _sourceOf(geometry, self.data)
        available = bool(self.columnSource)
        self.lockCheck.setEnabled(available)
        if available != wasAvailable:
            self.lockCheck.setChecked(available)
        if not available:
            self.lockCheck.setToolTip(
                "Lock the denominator to the numerator's sensor column: "
                "unavailable. No CRISM geometry file with detector columns (the IN file, "
                f"with an IR Sample band) was found beside {self.data.name} or "
                "the file it was made from, and it is not an unprojected CRISM "
                "cube (no map projection, 640, 320, 128 or 64 columns), so the "
                "denominator can't be placed by sensor column. Choose the IN "
                "file with “Choose Geometry File…”."
            )
        else:
            route = f" ({geometry.route})" if geometry and geometry.route else ""
            self.lockCheck.setToolTip(
                "Lock the denominator to the numerator's sensor column. "
                f"Detector columns from {self.columnSource}{route}"
            )
        self.chooseGeometryButton.setText(
            "Change Geometry File…" if available else CHOOSE_TEXT
        )
        self._showLockStatus()

    def _showLockStatus(self) -> None:
        if not self.lockCheck.isEnabled():
            text = (
                f"Column lock unavailable: no detector columns for "
                f"{self.data.name} (choose its geometry file)"
            )
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
        """One wrapping row: the box size, the column lock, the geometry
        file, and the keys behind a "?"."""
        box = []
        for param in (self.config.boxWidth, self.config.boxHeight):
            editor = parameterWidget(param)
            assert isinstance(editor, IntParameterWidget)
            if editor.slider is not None:
                editor.slider.hide()
            editor.spinBox.setToolTip(param.description)
            box.append(editor)
        if box[0].unitLabel is not None:
            box[0].unitLabel.setText("×")  # Box [w] × [h] px
        self.boxWidthBox, self.boxHeightBox = box[0].spinBox, box[1].spinBox
        self.chooseGeometryButton.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.keysButton = QToolButton()
        self.keysButton.setText("?")
        self.keysButton.setToolTip(KEYS_HINT)
        self.keysButton.clicked.connect(self._showKeys)
        controls = QWidget()
        row = FlowLayout(controls, margins=4)
        for widget in (
            _row(QLabel("Box"), *box),
            # a check box in a plain row: a flow layout gives a macOS check
            # box 2 px less than its text needs
            _row(self.lockCheck),
            self.chooseGeometryButton,
            self.keysButton,
        ):
            row.addWidget(widget)
        return controls

    def _showKeys(self) -> None:
        QToolTip.showText(
            self.keysButton.mapToGlobal(self.keysButton.rect().bottomLeft()),
            KEYS_HINT,
            self.keysButton,
        )

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
        self.controlsDock = VardaDockWidget("Ratio Controls").withWidget(
            _StripScrollArea(self._controls())
        )
        self.spectraDock = VardaDockWidget("Spectra").withWidget(
            self.plots.spectraPanel
        )
        self.spectraDock.setTitleBarActions(self.plots.spectraActions)
        self.ratioDock = VardaDockWidget("Ratio").withWidget(self.plots.ratioPanel)
        self.ratioDock.setTitleBarActions(self.plots.ratioActions)
        self.blandnessDock = VardaDockWidget("Denominator Blandness").withWidget(
            _StripScrollArea(self.plots.readout)
        )
        self.plots.readout.detailsButton.toggled.connect(self._fitBlandness)
        # nothing here can be reopened once closed: fold or float it instead
        for dock in (
            self.browseDock,
            self.dataDock,
            self.controlsDock,
            self.spectraDock,
            self.ratioDock,
            self.blandnessDock,
        ):
            dock.setFeature(ads.CDockWidget.DockWidgetFeature.DockWidgetClosable, False)
        browseArea = self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea, self.browseDock
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea, self.dataDock, browseArea
        )
        # the right side: the controls, the two plots side by side under them,
        # and the blandness strip across both
        controlsArea = self.dockManager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea, self.controlsDock
        )
        spectraArea = self.dockManager.addDockWidget(
            ads.DockWidgetArea.BottomDockWidgetArea, self.spectraDock, controlsArea
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.BottomDockWidgetArea, self.blandnessDock, spectraArea
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea, self.ratioDock, spectraArea
        )
        self._areas = (browseArea, controlsArea, spectraArea)
        self._restoredLayout: str | None = None  # from a session
        self._shown = False

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

    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        if not self._shown:
            self._shown = True
            if self._restoredLayout is None:
                # once the window has its size (a resize right after show counts)
                QTimer.singleShot(0, self._defaultSizes)

    def _defaultSizes(self) -> None:
        """Half the width for the images, half for two plots side by side;
        the controls and the blandness strip as short as their contents, the
        plots the rest of the height."""
        if self._restoredLayout is not None:
            return
        browseArea, controlsArea, spectraArea = self._areas
        root = self.dockManager.rootSplitter()
        if root is None or root.count() != 2:
            return  # rearranged before it was shown
        width = root.width()
        right = width // 2
        root.setSizes([width - right, right])
        self.dockManager.setSplitterSizes(browseArea, [1, 1])
        self.dockManager.setSplitterSizes(spectraArea, [1, 1])
        column = self._rightColumn()
        if column is not None:
            heights = [self._stripHeight(d, right) for d in self._strips()]
            plots = max(column.height() - sum(heights), 0)
            column.setSizes([heights[0], plots, heights[1]])

    def _strips(self) -> tuple[VardaDockWidget, VardaDockWidget]:
        return self.controlsDock, self.blandnessDock

    def _rightColumn(self) -> QSplitter | None:
        """The splitter of the default right side (the controls, the plots,
        the blandness strip), unless the docks were moved."""
        controls, blandness = (d.dockAreaWidget() for d in self._strips())
        column = controls.parentWidget() if controls is not None else None
        if (
            isinstance(column, QSplitter)
            and column.count() == 3
            and column.widget(0) is controls
            and column.widget(2) is blandness
        ):
            return column
        return None

    @staticmethod
    def _stripHeight(dock: VardaDockWidget, width: int) -> int:
        """The height ``dock``'s area needs to show its strip whole at ``width``."""
        area, strip = dock.dockAreaWidget(), dock.widget()
        if area is None or not isinstance(strip, _StripScrollArea):
            return 0
        titleBar = area.titleBar()
        title = titleBar.sizeHint().height() if titleBar is not None else 0
        return title + strip.neededHeight(width) + 4

    def _fitBlandness(self) -> None:
        """Make room for the blandness details (or give it back to the
        plots) when they are shown (or folded) in the default layout."""
        column = self._rightColumn()
        if column is None or not self._shown:
            return
        sizes = column.sizes()
        wanted = self._stripHeight(self.blandnessDock, column.width())
        sizes[1] = max(sizes[1] + sizes[2] - wanted, 0)
        sizes[2] = wanted
        column.setSizes(sizes)

    # --- the click loop ------------------------------------------------------------

    def _onToolActivated(self, index: int, tool: object) -> None:
        if not isinstance(tool, RatioExplorerTool):
            return
        # the clicks and keys are behind "?": no hint over the images (the
        # tool's later messages, such as a refused click, still show)
        tool.hideText()
        self.controller.bindTool(tool, self.data)
        if index == 0:  # the browse view: its clicks and boxes go through the link
            tool.setClickMapper(self._browseToData)
            tool.setBoxProjector(
                lambda polygon: dataToView(self.browse, self.data, self.link, polygon)
            )
        # both views show the one pair, so a click on either continues it
        # bound methods (not partials), so Qt drops the connections with the tool
        tool.sigSelectionChanged.connect(self._syncViews)
        tool.sigSavePointRequested.connect(self._requestSavePoint)

    def _browseToData(self, x: float, y: float) -> tuple[float, float] | None:
        mapped = viewToData(self.browse, self.data, self.link, [[x, y]])
        return None if mapped is None else (float(mapped[0, 0]), float(mapped[0, 1]))

    def _syncViews(self, selection: RatioSelection) -> None:
        source = self.sender()
        for manager in self.toolManagers:
            other = manager.activeTool
            if isinstance(other, RatioExplorerTool) and other is not source:
                other.showSelection(selection)

    def _verdictSuffix(self, denominator: object = True) -> str:
        """The denominator's verdict, to put in the names of what is saved
        (none when there is no denominator: the readout would be stale)."""
        readout = self.plots.readout
        if denominator is None or not readout.lastResult.windows:
            return ""
        mark = "✓" if readout.lastResult.passed else "✗"
        return f" (denominator {mark} {readout.currentCheckName()})"

    # --- saving ----------------------------------------------------------------------

    def referenceSpectra(self) -> list[LibrarySpectrum]:
        """The library spectra overlaid on the Ratio plot, most recently
        added first."""
        spectra = [
            curve.librarySpectrum
            for curve in self.plots.ratioPlot.plots
            if curve.librarySpectrum is not None
        ]
        return list(reversed(spectra))

    def suggestedClass(self) -> str:
        """The reference mineral on the Ratio plot (its metadata's mineral or
        class, else its name's short form), else the last class used."""
        spectra = self.referenceSpectra()
        return suggestedLibraryClass(spectra[0]) if spectra else self.lastClass

    def classChoices(self) -> list[str]:
        """The Class dropdown: each reference mineral's class and full name
        (newest first), the last class, and the ROI classes already here."""
        overlaid = [
            choice
            for spectrum in self.referenceSpectra()
            for choice in (suggestedLibraryClass(spectrum), spectrum.name)
        ]
        existing = [n for n in self.roiCollection.classes() if n != DENOMINATOR_CLASS]
        return list(dict.fromkeys([*overlaid, self.lastClass, *existing]))

    def classColor(self, className: str) -> Color:
        """An existing class's color, else the next unused class color."""
        existing = self.roiCollection.classColor(className)
        if existing is not None:
            return existing
        used = [
            c.toHexString()
            for c in map(self.roiCollection.classColor, self.roiCollection.classes())
            if c is not None
        ]
        return roiColor(nextClassColor(used))

    def _summary(self, numerator: np.ndarray, denominator: np.ndarray | None) -> str:
        nc, nr = centerPixel(numerator)
        parts = [f"Numerator ({nc}, {nr})"]
        readout = self.plots.readout
        if denominator is not None:
            dc, dr = centerPixel(denominator)
            parts.append(f"denominator ({dc}, {dr})")
        if denominator is None or not readout.lastResult.windows:
            parts.append("no blandness verdict")
        else:
            verdict = "✓ bland" if readout.lastResult.passed else "✗ not bland"
            parts.append(f"{verdict} ({readout.currentCheckName()} check)")
        return " · ".join(parts)

    def _ask(
        self, title: str, name: str, summary: str, save: Callable[[str, str], None]
    ) -> None:
        """Ask for the class and the name; ``save(class, name)`` on Save."""
        if self.saveDialog is not None:
            self.saveDialog.deleteLater()
        self.saveDialog = SaveRatioDialog(
            title,
            self.classChoices(),
            self.suggestedClass(),
            name,
            summary,
            dontAsk=not self.askClass,
            parent=self,
        )
        self._saveChosen = save
        self.saveDialog.accepted.connect(self._onSaveAccepted)
        self.saveDialog.open()

    def _onSaveAccepted(self) -> None:
        dialog, save = self.saveDialog, self._saveChosen
        assert dialog is not None and save is not None
        self.askClass = not dialog.dontAsk()
        save(dialog.className(), dialog.itemName())

    def _requestSavePair(self, shift: bool) -> None:
        selection = self.controller.currentPair()
        if selection is None:
            return
        name = f"Ratio {self.controller.saveCount + 1}{self._verdictSuffix()}"
        if self.askClass or shift:
            assert selection.numerator is not None
            summary = self._summary(selection.numerator, selection.denominator)
            self._ask("Save ratio pair as ROIs", name, summary, self._savePair)
        else:
            self._savePair(self.suggestedClass(), name)

    def _savePair(self, className: str, name: str) -> None:
        """The numerator ROI's class is ``className`` (the pair's name when
        empty), the denominator's :data:`DENOMINATOR_CLASS`; both record the
        pair's name, their role and the target class in their own columns."""
        if className:
            self.lastClass = className
        numeratorClass = className or name
        self.controller.saveCurrentBoxes(
            (numeratorClass, DENOMINATOR_CLASS),
            (
                {"pair": name, "role": "numerator", "target": className},
                {"pair": name, "role": "denominator", "target": className},
            ),
            (self.classColor(numeratorClass), roiColor(DENOMINATOR_CLASS_COLOR)),
        )

    def _requestSavePoint(self, shift: bool) -> None:
        tool = self.sender()
        if not isinstance(tool, RatioExplorerTool):
            return
        numerator = tool.selection.numerator
        if numerator is None:
            return
        col, row = centerPixel(numerator)
        denominator = tool.selection.denominator
        name = f"Ratio numerator ({col}, {row}){self._verdictSuffix(denominator)}"
        description = "" if denominator is None else self.plots.readout.describe()
        self._pendingPoint = (col, row, description)
        if self.askClass or shift:
            summary = self._summary(numerator, denominator)
            self._ask("Save numerator pixel as a point", name, summary, self._savePoint)
        else:
            self._savePoint(self.suggestedClass(), name)

    def _savePoint(self, className: str, name: str) -> None:
        """Points have no class: it opens the point's description."""
        if self._pendingPoint is None:
            return
        col, row, description = self._pendingPoint
        self._pendingPoint = None
        if className:
            self.lastClass = className
            description = " ".join(
                d for d in (f"Class: {className}.", description) if d
            )
        self.pointManager.addPixel(
            self.data, col, row, name=name, description=description
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
            "blandnessExcludeCo2": self.plots.readout.co2Check.isChecked(),
            "blandnessDetails": self.plots.readout.detailsVisible(),
            "separateSpectra": self.plots.isSeparate(),
            "plotShapes": {
                "spectra": self.plots.spectraShape.shape.name,
                "ratio": self.plots.ratioShape.shape.name,
            },
            "dockLayout": self._dockLayout(),
            "askClass": self.askClass,
            "lastClass": self.lastClass,
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
        self.plots.readout.co2Check.setChecked(
            bool(state.get("blandnessExcludeCo2", True))
        )
        restorePlotLibraries(state, self.plots.ratioPlot)
        self.askClass = bool(state.get("askClass", True))
        self.lastClass = str(state.get("lastClass", ""))
        self.plots.readout.setDetailsVisible(bool(state.get("blandnessDetails", False)))
        self.plots.setSeparate(bool(state.get("separateSpectra", False)))
        shapes = state.get("plotShapes", {})
        if isinstance(shapes, dict):
            for key, action in (
                ("spectra", self.plots.spectraShape),
                ("ratio", self.plots.ratioShape),
            ):
                if shapes.get(key) in PlotShape.__members__:
                    action.setShape(PlotShape[shapes[key]])
        layout = state.get("dockLayout")
        # a layout from another version of this workspace is refused by ADS
        if isinstance(layout, str) and self.dockManager.restoreState(
            QByteArray.fromBase64(layout.encode())
        ):
            self._restoredLayout = layout

    def _dockLayout(self) -> str | None:
        """Where the docks are (ADS's own state, base64 for the JSON file);
        a workspace never shown has no layout of its own yet, so it keeps
        the one it was restored with."""
        if not self._shown:
            return self._restoredLayout
        return bytes(self.dockManager.saveState().toBase64().data()).decode()


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
