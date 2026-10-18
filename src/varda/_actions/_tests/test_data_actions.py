"""Find Data has its own top-level Data menu, not the Workspace menu."""

from varda._actions import ALL_ACTIONS, MENUBAR
from varda._actions._data_actions import DATA_ACTIONS
from varda._actions._menu_ids import MenuId


def test_find_data_is_in_the_data_menu_and_not_the_workspace_menu():
    (action,) = [a for a in ALL_ACTIONS if a.id == "varda.data.find_data"]
    assert action.title == "Find Data…"
    assert [rule.id for rule in action.menus] == [MenuId.DATA]
    in_workspace_menu = [
        a.id for a in ALL_ACTIONS if any(r.id == MenuId.WORKSPACE for r in a.menus)
    ]
    assert "varda.data.find_data" not in in_workspace_menu
    assert action in DATA_ACTIONS


def test_data_menu_follows_file_in_the_menu_bar():
    ids = [menuId for menuId, _ in MENUBAR]
    assert ids.index(MenuId.DATA) == ids.index(MenuId.FILE) + 1
