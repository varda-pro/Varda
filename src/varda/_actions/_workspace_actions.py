from app_model.types import Action, MenuRule, StandardKeyBinding

from varda._actions._menu_ids import MenuGroup, MenuId
from varda.context_keys import EXPR_HAS_IMAGES, EXPR_HAS_WORKSPACE
from varda.common.di_types import ProjectImages
from varda.common.ui import openOwnedDialog
from varda.maingui import WORKSPACE_TAB_MENU_ID, MainGUI
from varda.workspaces.dual_image_workspace import NewDualImageWorkspaceDialog
from varda.workspaces.general_image_analysis import (
    NewGeneralImageAnalysisWorkspaceDialog,
)
from varda.workspaces.find_data.find_data_workspace import FindDataWorkspace
from varda.workspaces.mosaic.new_mosaic_dialog import NewMosaicDialog
from varda.workspaces.terrain.terrain_workspace import TerrainWorkspace
from varda.workspaces.crism_ratio.crism_ratio_workspace import (
    CrismRatioWorkspace,
    NewCrismRatioDialog,
)
from varda.workspaces.linked_images.linked_images_workspace import (
    LinkedImagesWorkspace,
    NewLinkedImagesDialog,
)
from varda.workspaces.spectral_hourglass.new_hourglass_dialog import (
    NewSpectralHourglassDialog,
)
from varda.workspaces.workspace_kinds import duplicateWorkspace


def newDualImageWorkspace(images: ProjectImages, mainGui: MainGUI) -> None:
    openOwnedDialog(
        NewDualImageWorkspaceDialog(images, parent=mainGui).connectOnAccept(
            mainGui.addTab
        )
    )


def newGeneralAnalysisWorkspace(images: ProjectImages, mainGui: MainGUI) -> None:
    openOwnedDialog(
        NewGeneralImageAnalysisWorkspaceDialog(images, parent=mainGui).connectOnAccept(
            mainGui.addTab
        )
    )


def newSpectralHourglassWorkspace(images: ProjectImages, mainGui: MainGUI) -> None:
    NewSpectralHourglassDialog(images, parent=mainGui).connectOnAccept(
        mainGui.addTab
    ).open()


def newMosaicWorkspace(images: ProjectImages, mainGui: MainGUI) -> None:
    openOwnedDialog(
        NewMosaicDialog(images, parent=mainGui).connectOnAccept(mainGui.addTab)
    )


def newFindDataWorkspace(images: ProjectImages, mainGui: MainGUI) -> None:
    mainGui.addTab(FindDataWorkspace(images))


def newTerrainWorkspace(images: ProjectImages, mainGui: MainGUI) -> None:
    mainGui.addTab(TerrainWorkspace(images))


def newLinkedImagesWorkspace(images: ProjectImages, mainGui: MainGUI) -> None:
    dialog = NewLinkedImagesDialog(images, parent=mainGui)
    dialog.accepted.connect(
        lambda: mainGui.addTab(
            LinkedImagesWorkspace(dialog.chosen(), images, dialog.link())
        )
    )
    openOwnedDialog(dialog)


def newCrismRatioWorkspace(images: ProjectImages, mainGui: MainGUI) -> None:
    dialog = NewCrismRatioDialog(images, parent=mainGui)
    dialog.accepted.connect(
        lambda: mainGui.addTab(
            CrismRatioWorkspace(dialog.browse(), dialog.data(), images, dialog.link())
        )
    )
    openOwnedDialog(dialog)


def duplicateCurrentWorkspace(images: ProjectImages, mainGui: MainGUI) -> None:
    """Open a copy of the current workspace in a new tab."""
    source = mainGui.currentWorkspace()
    copy = duplicateWorkspace(source, images) if source is not None else None
    if source is not None and copy is not None:
        mainGui.addTab(copy, f"{source.windowTitle()} (copy)")


def closeCurrentWorkspace(mainGui: MainGUI) -> None:
    mainGui.closeWorkspace(mainGui.currentWorkspace())


WORKSPACE_ACTIONS: list[Action] = [
    Action(
        id="varda.workspace.new_dual_image",
        title="New Dual Image Workspace",
        callback=newDualImageWorkspace,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=MenuId.WORKSPACE, group=MenuGroup.WORKSPACE_NEW, order=1)],
    ),
    Action(
        id="varda.workspace.new_general_analysis",
        title="New General Image Analysis Workspace",
        callback=newGeneralAnalysisWorkspace,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=MenuId.WORKSPACE, group=MenuGroup.WORKSPACE_NEW, order=2)],
    ),
    Action(
        id="varda.workspace.new_spectral_hourglass",
        title="New Spectral Hourglass Workspace…",
        tooltip=(
            "Find endmembers (MNF, pixel purity, n-D visualizer), identify them "
            "against a spectral library, and map them across the scene"
        ),
        callback=newSpectralHourglassWorkspace,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=MenuId.WORKSPACE, group=MenuGroup.WORKSPACE_NEW, order=3)],
    ),
    Action(
        id="varda.workspace.new_mosaic",
        title="New Mosaic Workspace…",
        tooltip=(
            "Combine overlapping images into one: align them (nudge or tie "
            "points), order them, and export the mosaic"
        ),
        callback=newMosaicWorkspace,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=MenuId.WORKSPACE, group=MenuGroup.WORKSPACE_NEW, order=4)],
    ),
    Action(
        id="varda.workspace.find_data",
        title="Find and Fetch Data…",
        tooltip=(
            "Search catalogs (STAC, PDS ODE, footprint files, or plugins') for "
            "observations of a place, see their footprints on a map, and "
            "download them"
        ),
        callback=newFindDataWorkspace,
        menus=[MenuRule(id=MenuId.WORKSPACE, group=MenuGroup.WORKSPACE_NEW, order=5)],
    ),
    Action(
        id="varda.workspace.new_terrain",
        title="New 3D Terrain Workspace",
        tooltip=(
            "Show a DEM in 3D with shaded relief, elevation colors or an image "
            "draped on it; export high-resolution stills and interactive web pages"
        ),
        callback=newTerrainWorkspace,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=MenuId.WORKSPACE, group=MenuGroup.WORKSPACE_NEW, order=6)],
    ),
    Action(
        id="varda.workspace.new_linked",
        title="New Linked Images Workspace…",
        tooltip=(
            "View any number of images together, linked by pixel or map location: "
            "one cursor shows every image's spectrum, a line every image's profile"
        ),
        callback=newLinkedImagesWorkspace,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=MenuId.WORKSPACE, group=MenuGroup.WORKSPACE_NEW, order=7)],
    ),
    Action(
        id="varda.workspace.new_crism_ratio",
        title="New CRISM Ratio Workspace…",
        tooltip=(
            "Navigate on a browse image and ratio I/F spectra by clicking boxes: "
            "numerator, denominator and ratio plots, a blandness check of the "
            "denominator, and a column lock that follows the numerator"
        ),
        callback=newCrismRatioWorkspace,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=MenuId.WORKSPACE, group=MenuGroup.WORKSPACE_NEW, order=8)],
    ),
    Action(
        id="varda.workspace.duplicate",
        title="Duplicate Workspace",
        tooltip=(
            "Open a copy of this workspace: the same images, with its own copies "
            "of the ROIs and display settings"
        ),
        callback=duplicateCurrentWorkspace,
        enablement=EXPR_HAS_WORKSPACE,
        menus=[
            MenuRule(id=MenuId.WORKSPACE, group=MenuGroup.WORKSPACE_MANAGE, order=0),
            MenuRule(id=WORKSPACE_TAB_MENU_ID, order=0),
        ],
    ),
    Action(
        id="varda.workspace.close",
        title="Close Workspace",
        callback=closeCurrentWorkspace,
        enablement=EXPR_HAS_WORKSPACE,
        menus=[
            MenuRule(id=MenuId.WORKSPACE, group=MenuGroup.WORKSPACE_MANAGE, order=1),
            MenuRule(id=WORKSPACE_TAB_MENU_ID, order=1),
        ],
        keybindings=[StandardKeyBinding.Close.to_keybinding_rule()],
    ),
]
