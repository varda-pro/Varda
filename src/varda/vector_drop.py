"""Files dropped on the window: images join the project (as File > Import
Image(s)), and vector files go where they can be used. In Find Data a vector
file is drawn on the map as an overlay; in a workspace with an ROI, Points or
Lines manager it goes to that manager's Import… (the same dialog), the one its
first layer's geometry suggests: polygons as ROIs, points as points, lines as
lines. Vectors are always placed on an image, so without such a workspace
Varda says where to drop them."""

from __future__ import annotations

import PyQt6Ads as ads
from PyQt6.QtWidgets import QMessageBox, QWidget

from varda._actions._file_actions import importImagePaths
from varda.common.di_types import ProjectImages
from varda.maingui import MainGUI
from varda.vector_import import Target, isDroppedVectorFile, suggestedTarget
from varda.vector_import_dialog import VectorImporter
from varda.workspaces.find_data.find_data_workspace import FindDataWorkspace

NO_WORKSPACE = (
    "Vector files are placed on an image: open a workspace for one (General "
    "Image Analysis, Dual, CRISM Ratio, …) and drop the file on it, or use "
    "Import… in its ROI Manager, Points Manager or Lines panel. In Find Data a "
    "dropped vector file is drawn on the map."
)


def importDroppedPaths(
    paths: list[str], images: ProjectImages, mainGui: MainGUI
) -> None:
    vectors = [path for path in paths if isDroppedVectorFile(path)]
    others = [path for path in paths if path not in vectors]
    if others:
        importImagePaths(others, images)
    for path in vectors:
        openVectorFile(path, mainGui.currentWorkspace(), mainGui)


def workspaceImporters(workspace: QWidget) -> list[VectorImporter]:
    """Every manager's importer in ``workspace``, its docks' too (a dock
    need not sit under the workspace in Qt's parent tree)."""
    found = list(workspace.findChildren(VectorImporter))
    for manager in workspace.findChildren(ads.CDockManager):
        for dock in manager.dockWidgetsMap().values():
            panel = dock.widget()
            if panel is not None:
                found += panel.findChildren(VectorImporter)
    return list(dict.fromkeys(found))


def importerFor(workspace: QWidget, target: Target) -> VectorImporter | None:
    """The workspace's importer for ``target``, else its first importer (it
    keeps what its target takes and counts what it skips), else None."""
    importers = workspaceImporters(workspace)
    return next((i for i in importers if i.target is target), None) or next(
        iter(importers), None
    )


def openVectorFile(path: str, workspace: QWidget | None, parent: QWidget) -> None:
    if isinstance(workspace, FindDataWorkspace):
        workspace.addOverlay(path)
        return
    importer = (
        importerFor(workspace, suggestedTarget(path)) if workspace is not None else None
    )
    if importer is None:
        QMessageBox.information(parent, "Open a vector file", NO_WORKSPACE)
        return
    importer.openFile(path)
