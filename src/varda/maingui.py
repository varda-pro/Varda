from __future__ import annotations

import logging
import weakref
from typing import TYPE_CHECKING

from PyQt6 import QtWidgets
from PyQt6.QtCore import QMimeData, Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget
from app_model.backends.qt import QModelMainWindow, QModelMenu
from PyQt6.QtCore import QPoint
from varda.branding.title_mark import addTitleMarkBar
from varda.common.ui import DetachableTabWidget
from varda.all_images_view_list.imageview_list import ImageListWidget
from varda.common.entities import VardaRaster
from varda.context_keys import IMAGE_LIST_VISIBLE, WORKSPACE_COUNT
from varda.geo.health import Severity, headline
from varda.start_panel import StartPanel

if TYPE_CHECKING:
    from varda.app import VardaApplication

logger = logging.getLogger(__name__)

MAX_TAB_TITLE_CHARS = 40
CRS_MESSAGE_MS = 15000  # how long a coordinate-system problem stays in the status bar


def elidedTabTitle(title: str, maxChars: int = MAX_TAB_TITLE_CHARS) -> str:
    """Shorten a long title in the middle (image file names carry their
    distinguishing parts at both ends)."""
    if len(title) <= maxChars:
        return title
    keep = (maxChars - 1) // 2
    return f"{title[:keep]}…{title[-keep:]}"


# Right-click menu on a workspace's tab; actions add themselves to it
WORKSPACE_TAB_MENU_ID = "varda/workspace_tab"


class MainGUI(QModelMainWindow):
    # Local file paths dragged onto the window; main.py routes them to the importer.
    sigFilesDropped = pyqtSignal(list)

    def __init__(self, app: VardaApplication):
        super().__init__(app)
        self.setWindowTitle("Varda")
        self.setAcceptDrops(True)

        self.app = app
        self.childWindows: list[QWidget] = []
        # images whose coordinate-system problems the status bar has shown:
        # once per image per session
        self._crsAnnounced: weakref.WeakSet[VardaRaster] = weakref.WeakSet()

        self.initUI()

        logger.info("MainGUI Initialized")

    def initUI(self):
        addTitleMarkBar(self)
        self.setTabPosition(
            Qt.DockWidgetArea.AllDockWidgetAreas,
            QtWidgets.QTabWidget.TabPosition.North,
        )

        self.imageList = ImageListWidget(self.app.images, self)
        self.imageListDock = self.newDock(
            "Image List", self.imageList, Qt.DockWidgetArea.LeftDockWidgetArea
        )
        # View > Image List shows it checked while the dock is open
        showing = self.imageListDock.toggleViewAction()
        assert showing is not None
        showing.toggled.connect(self._updateImageListVisible)
        self._updateImageListVisible(showing.isChecked())

        self.centralTabs = DetachableTabWidget(self)
        self.centralTabs.tabCloseRequested.connect(
            lambda index: self.closeWorkspace(self.centralTabs.widget(index))
        )
        # Right-clicking a tab makes it current and offers Duplicate / Close
        tabBar = self.centralTabs.tabBar()
        if tabBar is not None:
            tabBar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tabBar.customContextMenuRequested.connect(self._showTabMenu)
            tabBar.tabMoved.connect(lambda *_: self._followTabOrder())
        # Until a workspace opens, the window says how to start
        self.startPanel = StartPanel(self.app, self)
        central = QtWidgets.QStackedWidget(self)
        central.addWidget(self.startPanel)
        central.addWidget(self.centralTabs)
        self.setCentralWidget(central)
        self.centralTabs.currentChanged.connect(self._showStartPanelWhenEmpty)
        self._showStartPanelWhenEmpty()
        self._updateWorkspaceCount()
        self.app.images.sigDataChanged.connect(self._announceCrsProblems)

    def _announceCrsProblems(self, images: list) -> None:
        """One status-bar message for the coordinate-system problems (the
        automatic fix included) of images just added; never twice for one
        image. Images not checked yet wait for a later change."""
        new = [
            image
            for image in images
            if isinstance(image, VardaRaster)
            and image not in self._crsAnnounced
            and image.crsHealth is not None
        ]
        self._crsAnnounced.update(new)
        problems = [
            image
            for image in new
            if image.crsHealth is not None
            and any(i.severity is Severity.PROBLEM for i in image.crsHealth.issues)
        ]
        if not problems:
            return
        if len(problems) == 1:
            finding = headline(problems[0].crsHealth)
            message = f"{problems[0].name} — {finding} Details in Image Properties."
        else:
            message = (
                f"{len(problems)} images have coordinate-system problems (a dot "
                "on their thumbnails). Details in Image Properties."
            )
        statusBar = self.statusBar()
        assert statusBar is not None
        statusBar.showMessage(message, CRS_MESSAGE_MS)

    def newDock(self, title, widget, dockArea):
        dock = QtWidgets.QDockWidget(title, self)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setWidget(widget)
        self.addDockWidget(dockArea, dock)
        return dock

    def addTab(self, widget: QWidget, title: str | None = None):
        """Add a workspace tab, named after the widget's window title unless
        ``title`` is given, and bring it to the front (what the user opened is
        what they look at next). Long names are shortened in the middle; the
        tooltip carries the full name."""
        if title is None:
            title = widget.windowTitle()
        self.childWindows.append(widget)
        index = self.centralTabs.addTab(widget, elidedTabTitle(title))
        self.centralTabs.setTabToolTip(index, title)
        self.centralTabs.setCurrentIndex(index)
        self._updateWorkspaceCount()

    def _showStartPanelWhenEmpty(self, *_) -> None:
        central = self.centralWidget()
        if isinstance(central, QtWidgets.QStackedWidget):
            empty = self.centralTabs.count() == 0
            central.setCurrentWidget(self.startPanel if empty else self.centralTabs)

    def _showTabMenu(self, pos: QPoint) -> None:
        tabBar = self.centralTabs.tabBar()
        index = tabBar.tabAt(pos) if tabBar is not None else -1
        if tabBar is None or index < 0:
            return
        self.centralTabs.setCurrentIndex(index)
        QModelMenu(WORKSPACE_TAB_MENU_ID, self.app).exec(tabBar.mapToGlobal(pos))

    def _followTabOrder(self) -> None:
        """Keep the workspaces in the tabs' order (sessions save them so);
        detached ones follow."""
        docked = [self.centralTabs.widget(i) for i in range(self.centralTabs.count())]
        self.childWindows[:] = [w for w in docked if w in self.childWindows] + [
            w for w in self.childWindows if w not in docked
        ]

    def currentWorkspace(self) -> QWidget | None:
        return self.centralTabs.currentWidget()

    def closeWorkspace(self, widget: QWidget | None) -> None:
        """Remove a workspace from the tabs (or its detached window), run its
        close handler so it releases controllers, and destroy it."""
        if widget is None or widget not in self.childWindows:
            return
        self.centralTabs.discardTab(widget)
        self.childWindows.remove(widget)
        widget.close()
        widget.deleteLater()
        self._updateWorkspaceCount()

    def toggleImageList(self) -> None:
        """Close the image list, or bring it back where it was."""
        dock = self.imageListDock
        dock.setVisible(dock.isHidden())
        if dock.isVisible():
            dock.raise_()

    def _updateImageListVisible(self, visible: bool) -> None:
        self.app.context[IMAGE_LIST_VISIBLE] = visible

    def _updateWorkspaceCount(self) -> None:
        # drives enablement of workspace actions such as "Close Workspace"
        self.app.context[WORKSPACE_COUNT] = len(self.childWindows)

    def closeAllChildWindows(self):
        """Close all child windows before shutting down."""
        for window in self.childWindows[:]:
            if window and window.isVisible():
                window.close()

        self.childWindows.clear()

        logger.info("All child windows closed")

    # --- Drag and drop of image files ---

    def dragEnterEvent(self, event):
        paths = self._localFiles(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.startPanel.setDropCount(len(paths))

    def dragMoveEvent(self, event):
        if self._localFiles(event.mimeData()):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        super().dragLeaveEvent(event)
        self.startPanel.setDropCount(0)

    def dropEvent(self, event):
        self.startPanel.setDropCount(0)
        paths = self._localFiles(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.sigFilesDropped.emit(paths)

    @staticmethod
    def _localFiles(mime: QMimeData | None) -> list[str]:
        if mime is None or not mime.hasUrls():
            return []
        return [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]

    def closeEvent(self, event):
        """Handle the window close event to ensure proper cleanup."""
        logger.info("Main window close event triggered")
        self.closeAllChildWindows()
        event.accept()
