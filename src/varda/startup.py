"""Builds the application behind the splash: logging, plugins, the main
window and its wiring. ``varda.main`` imports this once the splash is up."""

import os
from pathlib import Path

from PyQt6.QtCore import QStandardPaths
from PyQt6.QtWidgets import QApplication

import varda
from varda.common.keys import NativeKeyTranslator
from varda._actions._menu_ids import menuBar
from varda.workspaces.terrain.terrain_view import TerrainView
from varda._actions._file_actions import cleanOldExtractions
from varda.all_images_view_list.image_list_menu_controller import (
    ImageListMenuController,
)
from varda.app import VardaApplication
from varda.maingui import MainGUI
from varda.session_autosave import SessionAutosaver
from varda.spectral_libraries.store import LibraryStore, setDefaultStore
from varda.update_check import checkForUpdatesLater
from varda.vector_drop import importDroppedPaths


def defaultLibrariesFolder() -> Path:
    """``libraries`` in Varda's app-data folder (beside ``plugins``): your
    spectral libraries, and the list of library files you loaded."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    return Path(base) / "libraries"


def createVarda(q_app: QApplication, checkForUpdates: bool = True) -> VardaApplication:
    """Build the application and show its main window. The splash animates
    while this runs on the GUI thread, so it repaints between stages."""
    varda.log._initializeFullLogging()
    q_app.processEvents()
    # Libraries' "Ctrl" (e.g. the docks' pin button) reads ⌘ on a Mac
    q_app.installTranslator(NativeKeyTranslator(q_app))

    # Every library list draws from one store: bundled, yours, and loaded files
    setDefaultStore(LibraryStore(defaultLibrariesFolder()))
    app = VardaApplication()
    # 3D views must go before WebEngine's profile does, or the exit crashes
    q_app.aboutToQuit.connect(TerrainView.releaseAll)
    app.pluginManager.hook.onLoad(app=app)
    q_app.processEvents()

    app.maingui = MainGUI(app=app)
    q_app.processEvents()
    # Debug is for development; set VARDA_DEBUG=1 to show it
    app.maingui.setModelMenuBar(menuBar(debug=os.environ.get("VARDA_DEBUG") == "1"))
    imageListController = ImageListMenuController(app, parent=app.maingui)
    app.maingui.imageList.sigImagesActivated.connect(
        imageListController.onImagesActivated
    )
    app.maingui.imageList.sigContextMenuRequested.connect(
        imageListController.onContextMenuRequested
    )
    app.maingui.imageList.sigRemoveRequested.connect(
        imageListController.onRemoveRequested
    )
    # Files dragged onto the window: images import like File > Import
    # Image(s), vector files go to the current workspace (vector_drop)
    app.maingui.sigFilesDropped.connect(
        lambda paths: importDroppedPaths(paths, app.images, app.maingui)
    )
    app.context.changed.connect(
        lambda _keys: app.maingui.menuBar().update_from_context(app.context)
    )
    # Autosave the session every few minutes and on quit (File > Restore Last Session)
    app.autosaver = SessionAutosaver(app.images, app.maingui, parent=app.maingui)
    cleanOldExtractions(app.images)
    app.maingui.showMaximized()
    q_app.processEvents()
    if checkForUpdates:
        checkForUpdatesLater(app.maingui)

    varda.log.info("Varda initialized successfully!")
    return app
