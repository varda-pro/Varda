"""Bridges a viewport's right-click to the app_model viewport context menu,
and to the workspace's panels: the image's own (its render settings, its
histogram) and, under Panels, every panel of the workspace, so one closed by
mistake can be opened again."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import PyQt6Ads as ads
from app_model import Application
from app_model.backends.qt import QModelMenu
from PyQt6.QtCore import QObject, QPoint
from PyQt6.QtWidgets import QMenu, QWidget

from varda.common.ui import dockManagerOf, panelsMenu, showDock
from varda.image_rendering.raster_view.viewport_actions import (
    VIEWPORT_CONTEXT_MENU_ID,
    ViewportClickContext,
    setCurrentClickContext,
)

if TYPE_CHECKING:
    from varda.image_rendering.raster_view.image_viewport import ImageViewport
    from varda.rois.roi_manager_widget import ROIManagerWidget

logger = logging.getLogger(__name__)


class ViewportContextMenuController(QObject):
    """Shows the viewport context menu: the app_model actions, then the
    viewport's own panels and the workspace's Panels (``attach``)."""

    def __init__(
        self, roiManager: ROIManagerWidget, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._roiManager = roiManager
        self._panels: dict[QWidget, tuple[ads.CDockWidget, ...]] = {}  # by viewport

    def attach(
        self, viewport: ImageViewport, panels: Sequence[ads.CDockWidget] = ()
    ) -> None:
        """Show the menu on ``viewport``'s right-click, with ``panels`` (the
        image's render settings and histogram, say) offered first."""
        self._panels[viewport] = tuple(panels)
        viewport.sigContextMenuRequested.connect(self.onContextMenuRequested)

    def onContextMenuRequested(
        self, imageCol: float, imageRow: float, globalPos: QPoint
    ) -> None:
        app = Application.get_app("varda")
        if app is None:
            logger.warning("No 'varda' app instance; cannot show viewport menu")
            return

        def _place() -> None:
            self._roiManager.placeTemplate(
                clickRow=int(round(imageRow)),
                clickCol=int(round(imageCol)),
            )

        setCurrentClickContext(ViewportClickContext(placeTemplate=_place))
        try:
            menu = QModelMenu(VIEWPORT_CONTEXT_MENU_ID, app)
            viewport = self.sender()
            if isinstance(viewport, QWidget):
                self.addPanels(menu, viewport)
            menu.exec(globalPos)
        finally:
            setCurrentClickContext(None)

    def addPanels(self, menu: QMenu, viewport: QWidget) -> None:
        """The viewport's own panels, then Panels: every panel of its workspace."""
        panels = self._panels.get(viewport, ())
        manager = dockManagerOf(viewport)
        if not panels and manager is None:
            return
        menu.addSeparator()
        for dock in panels:
            action = menu.addAction(dock.windowTitle())
            assert action is not None
            action.triggered.connect(lambda _checked, d=dock: showDock(d))
        if manager is not None:
            menu.addMenu(panelsMenu(manager, menu))
