"""Menu text a first-time user can follow literally (release QA #14, #15, #18, #21)."""

import re

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


# Terms of art that keep "cube": CRISM ratio cubes and ISIS cube files
CUBE_IS_A_TERM_OF_ART = {"varda.processing.ratio_cube", "varda.processing.cam2map"}


def test_menus_and_tips_say_image_and_band():
    """Release QA #21: image/raster/cube/scene for one thing."""
    for action in ALL_ACTIONS:
        text = f"{action.title} {action.tooltip or ''}"
        assert not re.search(r"\b(raster|scene)s?\b", text, re.I), action.id
        if action.id not in CUBE_IS_A_TERM_OF_ART:
            assert not re.search(r"\bcubes?\b", text, re.I), action.id


# Release QA 0.12.0 #14: the menu said "Import Image(s)", the start page
# "Import Image(s)…"; both open a dialog
OPENS_A_DIALOG = {
    "varda.file.import_image",
    "varda.workspace.new_dual_image",
    "varda.workspace.new_general_analysis",
}


def test_items_that_open_a_dialog_end_in_an_ellipsis():
    for actionId in OPENS_A_DIALOG:
        assert _action(actionId).title.endswith("…"), actionId


def test_ellipses_are_one_character_and_never_doubled():
    for action in ALL_ACTIONS:
        assert "..." not in action.title, action.id
        assert "……" not in action.title, action.id
