"""Declarative app_model actions for the image list's right-click menu.

Mirrors ``viewport_actions``: the actions operate on a transient
``ImageListClickContext`` set just before the menu is shown (or a command is
run directly), supplied to callbacks via the app's injection store.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

import attrs
from app_model.types import Action, MenuRule
from PyQt6.QtWidgets import QMessageBox

from varda.common.di_types import ProjectImages
from varda.common.entities import VardaRaster
from varda.common.observable_list import ObservableList
from varda.common.ui import openOwnedDialog
from varda.all_images_view_list.image_properties_dialog import ImagePropertiesDialog
from varda.image_loading.export_image_dialog import ExportImageDialog
from varda.maingui import MainGUI
from varda.workspaces.dual_image_workspace import NewDualImageWorkspaceDialog
from varda.workspaces.general_image_analysis import (
    GeneralImageAnalysisConfig,
    GeneralImageAnalysisWorkflow,
)
from varda.workspaces.workspace_kinds import describeWorkspace

IMAGE_LIST_CONTEXT_MENU_ID = "varda/image_list/context"
OPEN_GENERAL_ANALYSIS_ID = "varda.image_list.open_general_analysis"
OPEN_DUAL_IMAGE_ID = "varda.image_list.open_dual_image"


@attrs.define
class ImageListClickContext:
    """Transient state for the current interaction with the image list.

    ``images`` holds the clicked image first, followed by any other selected images.
    """

    images: list[VardaRaster]


_current: ImageListClickContext | None = None


def setCurrentClickContext(ctx: ImageListClickContext | None) -> None:
    global _current
    _current = ctx


def getCurrentClickContext() -> ImageListClickContext | None:
    return _current


def _openInGeneralAnalysis(
    ctx: ImageListClickContext, images: ProjectImages, mainGui: MainGUI
) -> None:
    # The live list, so results added later (e.g. a classification) can be
    # chosen as overlays
    config = GeneralImageAnalysisConfig(images)
    config.image.set(ctx.images[0])
    mainGui.addTab(GeneralImageAnalysisWorkflow(config))


def _openInDualImage(
    ctx: ImageListClickContext, images: ProjectImages, mainGui: MainGUI
) -> None:
    # With exactly two images selected, fill both slots; otherwise the user picks
    # the secondary image in the dialog.
    secondary = ctx.images[1] if len(ctx.images) == 2 else None
    openOwnedDialog(
        NewDualImageWorkspaceDialog(
            images, parent=mainGui, primary=ctx.images[0], secondary=secondary
        ).connectOnAccept(mainGui.addTab)
    )


def _exportImage(
    ctx: ImageListClickContext, images: ProjectImages, mainGui: MainGUI
) -> None:
    ExportImageDialog(list(images), image=ctx.images[0], parent=mainGui).open()


def _showProperties(
    ctx: ImageListClickContext, images: ProjectImages, mainGui: MainGUI
) -> None:
    dialog = ImagePropertiesDialog(ctx.images[0], parent=mainGui)
    if isinstance(images, ObservableList):  # a new name shows in the list
        dialog.accepted.connect(images.refresh)
    openOwnedDialog(dialog)


EXPORT_IMAGE_ID = "varda.image_list.export_image"
PROPERTIES_ID = "varda.image_list.properties"
REMOVE_IMAGES_ID = "varda.image_list.remove_images"


def _askToConfirm(text: str) -> bool:
    answer = QMessageBox.question(
        None,
        "Remove from Project",
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    return answer == QMessageBox.StandardButton.Yes


class _Workspaces(Protocol):
    """What removing images needs from the main window."""

    @property
    def childWindows(self) -> Sequence[object]: ...

    def closeWorkspace(self, widget) -> None: ...


def removeImages(
    toRemove: Sequence[VardaRaster],
    images: ProjectImages,
    mainGui: _Workspaces,
    confirm: Callable[[str], bool] = _askToConfirm,
) -> bool:
    """Remove images from the project, closing the workspaces that show them
    and releasing their files. Asks first only if something would be lost:
    open workspaces, or results never saved to disk. Returns whether they
    were removed."""
    removing = [image for image in toRemove if image in images]
    if not removing:
        return False
    affected = [
        workspace
        for workspace in list(mainGui.childWindows)
        if (described := describeWorkspace(workspace)) is not None
        and any(shown in removing for shown in described[1])
    ]
    unsaved = [image.name for image in removing if not image.filePath]
    warnings = []
    if affected:
        count = len(affected)
        warnings.append(
            f"{count} workspace{'s' if count != 1 else ''} showing "
            f"{'them' if len(removing) > 1 else 'it'} will close."
        )
    if unsaved:
        warnings.append(
            "Not saved to disk, and lost once removed: " + ", ".join(unsaved) + "."
        )
    if warnings:
        names = ", ".join(image.name for image in removing)
        if not confirm(f"Remove {names} from the project?\n\n" + "\n".join(warnings)):
            return False
    for workspace in affected:
        mainGui.closeWorkspace(workspace)
    for image in removing:
        images.remove(image)  # overlays elsewhere drop it as the list changes
        image.close()
    return True


def _removeImages(
    ctx: ImageListClickContext, images: ProjectImages, mainGui: MainGUI
) -> None:
    removeImages(ctx.images, images, mainGui)


IMAGE_LIST_ACTIONS: list[Action] = [
    Action(
        id=PROPERTIES_ID,
        title="Properties…",
        tooltip="The image's metadata: name, no-data value, wavelengths, band names, coordinate system",
        callback=_showProperties,
        menus=[MenuRule(id=IMAGE_LIST_CONTEXT_MENU_ID, group="8_properties", order=1)],
    ),
    Action(
        id=EXPORT_IMAGE_ID,
        title="Export Image…",
        callback=_exportImage,
        menus=[MenuRule(id=IMAGE_LIST_CONTEXT_MENU_ID, order=3)],
    ),
    Action(
        id=OPEN_GENERAL_ANALYSIS_ID,
        title="Open in New General Analysis Workspace",
        callback=_openInGeneralAnalysis,
        menus=[MenuRule(id=IMAGE_LIST_CONTEXT_MENU_ID, order=1)],
    ),
    Action(
        id=REMOVE_IMAGES_ID,
        title="Remove from Project…",
        callback=_removeImages,
        menus=[MenuRule(id=IMAGE_LIST_CONTEXT_MENU_ID, group="9_remove", order=1)],
    ),
    Action(
        id=OPEN_DUAL_IMAGE_ID,
        title="Open in New Dual Image Workspace…",
        callback=_openInDualImage,
        menus=[MenuRule(id=IMAGE_LIST_CONTEXT_MENU_ID, order=2)],
    ),
]
