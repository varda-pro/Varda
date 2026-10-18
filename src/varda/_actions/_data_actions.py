from app_model.types import Action, MenuRule

from varda._actions._menu_ids import MenuGroup, MenuId
from varda.common.di_types import ProjectImages
from varda.maingui import MainGUI
from varda.workspaces.find_data.find_data_workspace import FindDataWorkspace


def newFindDataWorkspace(images: ProjectImages, mainGui: MainGUI) -> None:
    mainGui.addTab(FindDataWorkspace(images))


DATA_ACTIONS: list[Action] = [
    Action(
        id="varda.data.find_data",
        title="Find Data…",
        tooltip=(
            "Search catalogs (STAC, PDS ODE, footprint files, or plugins') for "
            "observations of a place, see their footprints on a map, and "
            "download them"
        ),
        callback=newFindDataWorkspace,
        menus=[MenuRule(id=MenuId.DATA, group=MenuGroup.DATA_FIND, order=0)],
    ),
]
