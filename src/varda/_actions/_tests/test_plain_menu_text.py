"""Menu text a first-time user can follow literally (release QA #14, #15, #18)."""

from varda._actions import ALL_ACTIONS
from varda._actions._menu_ids import MenuId, menuBar


def _action(actionId: str):
    (action,) = [a for a in ALL_ACTIONS if a.id == actionId]
    return action


def test_find_data_spells_out_its_catalogs():
    tip = _action("varda.data.find_data").tooltip
    assert "SpatioTemporal Asset Catalogs (STAC)" in tip
    assert "PDS Orbital Data Explorer (ODE)" in tip


def test_kindle_a_feature_says_what_it_does():
    assert "plugin with AI" in _action("varda.plugins.kindle").title


def test_the_mosaic_tip_has_no_figure_of_speech():
    assert "nudge" not in _action("varda.workspace.new_mosaic").tooltip


def test_the_debug_menu_is_only_for_development():
    assert (MenuId.DEBUG, "Debug") not in menuBar(debug=False)
    assert (MenuId.DEBUG, "Debug") in menuBar(debug=True)
    assert (MenuId.HELP, "Help") in menuBar(debug=False)
