from app_model.types import Action, KeyBindingRule, KeyCode, MenuRule

import varda
from varda.utilities.resources import bundledIcon
from varda._actions._menu_ids import MenuId, MenuGroup
from varda.common.di_types import ProjectImages


def loadDummyImage(images: ProjectImages) -> None:
    images.append(varda.utilities.debug.generate_random_image())


DEBUG_ACTIONS: list[Action] = [
    Action(
        id="varda.debug.load_dummy_image",
        title="Load Dummy Image",
        icon=bundledIcon("bug"),
        callback=loadDummyImage,
        menus=[MenuRule(id=MenuId.DEBUG, group=MenuGroup.DEBUG_TESTING, order=1)],
        keybindings=[KeyBindingRule(primary=KeyCode.F11)],
    ),
]
