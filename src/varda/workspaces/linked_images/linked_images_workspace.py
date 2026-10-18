"""The Linked Images workspace: any number of images side by side, linked by
pixel (products of one pipeline on one grid: a result, its sample counts,
its flags) or by map location (images on different grids). The views pan
and zoom together; one cursor (Ctrl+click or Ctrl+drag in any view) shows
every image's spectrum there, stacked; a line drawn on any view gives every
image's profile along it. Every view and panel is a dock that can be
dragged anywhere, tabbed or floated.

Lines are kept in the first image's coordinates and placed on each image
through the link."""

from __future__ import annotations

import functools
import math
from collections.abc import MutableSequence, Sequence

import numpy as np
from PyQt6.QtCore import QObject, QPointF, QRect, QRectF, Qt
import PyQt6Ads as ads
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from varda.common.entities import VardaRaster
from varda.common.grid import compatibleImages
from varda.common.ui import ElidingComboBox, PanelScrollArea, VardaDockWidget
from varda.geo.equivalence import canTransform
from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.new_histogram_view import NewHistogramView
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.image_rendering.raster_view.viewport_tools.crop_tool import CropTool
from varda.image_rendering.raster_view.viewport_tools.line_tools import LineDrawTool
from varda.image_rendering.raster_view.viewport_tools.pixel_select_tool import (
    PixelSelectTool,
)
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    RatioExplorerTool,
)
from varda.image_rendering.raster_view.viewport_tools.tool_manager import ToolManager
from varda.image_rendering.render_state import restoreSettings, settingsState
from varda.lines.line_collection import LineCollection, LineKind
from varda.lines.line_manager_widget import LineManagerWidget
from varda.plotting.profile_plot import ProfilePlotWidget
from varda.workspaces.cropping import addCrop, cropMessage
from varda.workspaces.line_display import LineDisplay
from varda.workspaces.linked_images.linking import (
    Link,
    linkedPoints,
    linkWarnings,
)
from varda.workspaces.linked_images.spectra_panel import LinkedSpectraPanel

CURSOR_COLOR = QColor("#ffd400")


class ViewGroupLink(QObject):
    """Pans and zooms every view with the one the user moves."""

    def __init__(
        self,
        viewports: Sequence[ImageViewport],
        images: Sequence[VardaRaster],
        link: Link,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.viewports, self.images, self.link = list(viewports), list(images), link
        for index, viewport in enumerate(self.viewports):
            # only user gestures: setting the others' ranges doesn't echo back
            viewport.sigViewRangeChangedManually.connect(
                functools.partial(self.follow, index)
            )

    def follow(self, source: int) -> None:
        rect = self.viewports[source].viewRect()
        corners = [[rect.left(), rect.top()], [rect.right(), rect.bottom()]]
        placed = linkedPoints(self.images, source, corners, self.link)
        for index, ((x1, y1), (x2, y2)) in enumerate(placed):
            # an image of another body isn't linked: it stays where it is
            if index != source and np.isfinite([x1, y1, x2, y2]).all():
                self.viewports[index].setViewRange(
                    QRectF(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
                )


class LinkedImagesWorkspace(QMainWindow):
    def __init__(
        self,
        images: Sequence[VardaRaster],
        projectImages: MutableSequence[VardaRaster],
        link: Link = Link.PIXEL,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.images = list(images)
        self.projectImages = projectImages
        self.link = link
        self.cursorAt: tuple[int, float, float] | None = None  # (image, x, y)
        self.cursorShown: list[bool] = []  # per image: is the cursor on it
        self.setWindowTitle(" | ".join(image.name for image in self.images))

        self.renderers = [ImageRenderer(image=image) for image in self.images]
        self.viewports = [ImageViewport(r, parent=self) for r in self.renderers]
        self.toolManagers = []
        self.crosshairs = []
        for index, viewport in enumerate(self.viewports):
            manager = ToolManager(
                viewport,
                self,
                excludeCategories=("ROI Drawing",),
                excludeTools=(RatioExplorerTool,),
            )
            manager.sigToolActivated.connect(functools.partial(self._onTool, index))
            viewport.addToolBar(manager.getToolbar())
            manager.select(PixelSelectTool)  # Ctrl+click reads every image at once
            self.toolManagers.append(manager)
            crosshair = viewport.addCrosshair(CURSOR_COLOR)
            crosshair.setVisible(False)
            self.crosshairs.append(crosshair)
        self.viewLink = ViewGroupLink(self.viewports, self.images, link, self)

        # The cursor's spectra, every image's
        self.spectra = LinkedSpectraPanel(self.images)

        # Each image's display settings, with its histograms
        self.displayChoice = ElidingComboBox()
        self.displayStack = QStackedWidget()
        self.histograms = []
        for image, renderer in zip(self.images, self.renderers):
            self.displayChoice.addItem(image.name)
            settings = PanelScrollArea(renderer.getSettingsPanel())
            histogram = NewHistogramView(renderer)
            self.histograms.append(histogram)
            page = QSplitter(Qt.Orientation.Vertical)
            page.addWidget(settings)
            page.addWidget(histogram)
            page.setSizes([320, 240])
            self.displayStack.addWidget(page)
        self.displayChoice.currentIndexChanged.connect(
            self.displayStack.setCurrentIndex
        )
        display = QWidget()
        displayLayout = QVBoxLayout(display)
        displayLayout.addWidget(self.displayChoice)
        displayLayout.addWidget(self.displayStack, 1)

        # Lines: kept on the first image, placed on each through the link
        self.lines = LineCollection()
        self.imageLines = [LineCollection() for _ in self.images]
        self.lineManager = LineManagerWidget(self.lines, self.images[0], self)
        self.lineDisplays = [
            LineDisplay(lines, [viewport], parent=self)
            for lines, viewport in zip(self.imageLines, self.viewports)
        ]
        self.profilePlots = [
            ProfilePlotWidget(
                image, emptyText="Draw a line in any view to see the profiles."
            )
            for image in self.images
        ]
        profiles = QSplitter(Qt.Orientation.Vertical)
        for image, plot in zip(self.images, self.profilePlots):
            plot.plotItem.setTitle(image.name, size="9pt")
            profiles.addWidget(plot)

        self._arrange(display, profiles)
        # What isn't linked as asked: a notice across the top, not a dialog
        warnings = linkWarnings(self.images, link)
        self.linkNotice = QLabel(" ".join(warnings))
        self.linkNotice.setWordWrap(True)
        self.linkNotice.setContentsMargins(8, 4, 8, 4)
        self.linkNotice.setStyleSheet(
            "background: rgba(255, 196, 0, 0.18); border-bottom: 1px solid "
            "rgba(255, 196, 0, 0.6);"
        )
        self.linkNotice.setVisible(bool(warnings))
        self.setMenuWidget(self.linkNotice)
        self.lines.sigCollectionChanged.connect(self._placeLines)
        self.lineManager.sigSelectionChanged.connect(lambda _: self._showProfiles())

    def _arrange(self, display: QWidget, profiles: QWidget) -> None:
        """Every view and panel is a dock: drag one by its title to put it
        anywhere, tab it with another or float it. They start with the views
        in a grid, Display and Lines on the left, Spectra and Profiles on the
        right."""
        self.dockManager = ads.CDockManager(self)
        self.viewDocks: list[VardaDockWidget] = []
        columns = math.ceil(math.sqrt(len(self.images)))
        areas = []
        for index, (image, viewport) in enumerate(zip(self.images, self.viewports)):
            dock = VardaDockWidget(image.name).withWidget(viewport)
            # a view stays in the workspace (moved or floated, not closed)
            dock.setFeature(ads.CDockWidget.DockWidgetFeature.DockWidgetClosable, False)
            self.viewDocks.append(dock)
            if index == 0:
                area = self.dockManager.addDockWidget(
                    ads.DockWidgetArea.CenterDockWidgetArea, dock
                )
            elif index < columns:  # along the first row
                area = self.dockManager.addDockWidget(
                    ads.DockWidgetArea.RightDockWidgetArea, dock, areas[index - 1]
                )
            else:  # below the view above
                area = self.dockManager.addDockWidget(
                    ads.DockWidgetArea.BottomDockWidgetArea,
                    dock,
                    areas[index - columns],
                )
            areas.append(area)
        self.displayDock = VardaDockWidget("Display").withWidget(display)
        self.linesDock = VardaDockWidget("Lines").withWidget(self.lineManager)
        self.spectraDock = VardaDockWidget("Linked Spectra").withWidget(self.spectra)
        self.profilesDock = VardaDockWidget("Linked Profiles").withWidget(profiles)
        left = self.dockManager.addDockWidget(
            ads.DockWidgetArea.LeftDockWidgetArea, self.displayDock
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea, self.linesDock, left
        )
        right = self.dockManager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea, self.spectraDock
        )
        self.dockManager.addDockWidget(
            ads.DockWidgetArea.CenterDockWidgetArea, self.profilesDock, right
        )
        self.displayDock.setAsCurrentTab()
        self.spectraDock.setAsCurrentTab()
        root = self.dockManager.rootSplitter()
        if root is not None and root.count() == 3:
            root.setSizes([280, 1000, 420])  # the views get the most room

    # --- the cursor -----------------------------------------------------------------

    def _onTool(self, index: int, tool) -> None:
        if isinstance(tool, PixelSelectTool):
            tool.sigPixelSelected.connect(functools.partial(self._onPixel, index))
            tool.sigPixelDragged.connect(functools.partial(self._onPixel, index))
        elif isinstance(tool, LineDrawTool):
            tool.setKindProvider(self.lineManager.kind)
            tool.sigLineDrawn.connect(functools.partial(self.addLine, index))
        elif isinstance(tool, CropTool):
            tool.sigCropRequested.connect(
                functools.partial(self._onCrop, self.images[index])
            )

    def _onPixel(self, index: int, position: QPointF) -> None:
        self.placeCursor(index, position.x(), position.y())

    def placeCursor(self, source: int, x: float, y: float) -> None:
        """Put the cursor at image point (``x``, ``y``) of image ``source``."""
        self.cursorAt = (source, x, y)
        positions = linkedPoints(self.images, source, [[x, y]], self.link)
        self.cursorShown = []
        for image, crosshair, point in zip(self.images, self.crosshairs, positions):
            col, row = np.floor(point[0])
            inside = bool(0 <= col < image.width and 0 <= row < image.height)
            crosshair.setVisible(inside)
            self.cursorShown.append(inside)
            if inside:
                crosshair.setPos(QPointF(col + 0.5, row + 0.5))
        self.spectra.showAt(positions)

    # --- lines and profiles --------------------------------------------------------

    def addLine(self, source: int, vertices: np.ndarray, kind: LineKind) -> int | None:
        """A line drawn on image ``source``, kept in the first image's
        coordinates; its profiles show for every image. None, said in the
        status bar, when it can't be put on the first image (another body)."""
        onFirst = linkedPoints(
            [self.images[source], self.images[0]], 0, vertices, self.link
        )[1]
        if not np.isfinite(onFirst).all():
            reason = canTransform(self.images[source].crs, self.images[0].crs)
            bar = self.statusBar()
            if bar is not None:
                bar.showMessage(
                    f"Line not added: {self.images[source].name} isn't linked "
                    f"to {self.images[0].name} ({reason or 'off its map'})."
                )
            return None
        fid = self.lines.addLine(onFirst, kind)
        self.lineManager.select([fid])
        self.profilesDock.toggleView(True)
        self.profilesDock.setAsCurrentTab()
        return fid

    def _placeLines(self) -> None:
        for index, lines in enumerate(self.imageLines):
            lines.clear()
            placed = [
                linkedPoints(
                    [self.images[0], self.images[index]], 0, line.vertices, self.link
                )[1]
                for line in self.lines.lines()
            ]
            # all or none, so profiles stay in the lines' order; none on an
            # image of another body
            if not all(np.isfinite(vertices).all() for vertices in placed):
                continue
            for line, vertices in zip(self.lines.lines(), placed):
                lines.addLine(vertices, line.kind, name=line.name, color=line.color)
        self._showProfiles()

    def _showProfiles(self) -> None:
        """The selected lines' profiles (every line's when none is selected)."""
        order = [line.fid for line in self.lines.lines()]
        selected = {
            order.index(f) for f in self.lineManager.selectedFids() if f in order
        }
        for lines, plot in zip(self.imageLines, self.profilePlots):
            placed = lines.lines()
            plot.setLines(
                [line for i, line in enumerate(placed) if i in selected]
                if selected
                else placed
            )

    def _onCrop(self, image: VardaRaster, box: QRect) -> None:
        self.statusBar().showMessage(
            cropMessage(addCrop(image, box, self.projectImages))
        )

    # --- sessions --------------------------------------------------------------------

    def sessionState(self) -> dict:
        return {
            "link": self.link.name,
            "cursor": list(self.cursorAt) if self.cursorAt is not None else None,
            "lines": self.lines.toJson(),
            "display": [settingsState(r.settings) for r in self.renderers],
        }

    def restoreSessionState(self, state: dict) -> None:
        for renderer, saved in zip(self.renderers, state.get("display", [])):
            restoreSettings(renderer.settings, saved)
        self.lines.addFromJson(state.get("lines", []))
        cursor = state.get("cursor")
        if cursor is not None and 0 <= int(cursor[0]) < len(self.images):
            self.placeCursor(int(cursor[0]), float(cursor[1]), float(cursor[2]))


class NewLinkedImagesDialog(QDialog):
    """Choose the images to link (those on the first image's grid start
    ticked) and how to link them."""

    def __init__(self, images: Sequence[VardaRaster], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("New Linked Images Workspace")
        self.images = list(images)
        intro = QLabel(
            "Choose the images to view together: products of one pipeline on one "
            "grid link by pixel; images on different grids, by map location."
        )
        intro.setWordWrap(True)
        sameGrid = (
            {id(self.images[0])}
            | {id(i) for i in compatibleImages(self.images, self.images[0])}
            if self.images
            else set()
        )
        self.list = QListWidget()
        for image in self.images:
            item = QListWidgetItem(image.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if id(image) in sameGrid
                else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        self.linkBox = QComboBox()
        for link in Link:
            self.linkBox.addItem(link.value, link)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow("Link:", self.linkBox)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.list, 1)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def chosen(self) -> list[VardaRaster]:
        items = [self.list.item(row) for row in range(self.list.count())]
        return [
            image
            for image, item in zip(self.images, items)
            if item is not None and item.checkState() == Qt.CheckState.Checked
        ]

    def link(self) -> Link:
        return self.linkBox.currentData()

    def _accept(self) -> None:
        if len(self.chosen()) < 2:
            QMessageBox.information(
                self, self.windowTitle(), "Choose two or more images."
            )
            return
        self.accept()
