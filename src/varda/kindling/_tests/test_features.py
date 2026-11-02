"""The model sees every menu command with where it is, the file formats and
the catalogs."""

from app_model import Application
from app_model.types import Action, MenuRule, SubmenuItem

from varda._actions._menu_ids import MenuId, menuBar
from varda.all_images_view_list.image_list_actions import IMAGE_LIST_CONTEXT_MENU_ID
from varda.image_rendering.raster_view.viewport_actions import (
    VIEWPORT_CONTEXT_MENU_ID,
)
from varda.kindling.features import MENUS, featureInventory


def test_every_menu_a_user_reaches_is_listed():
    menus = [str(menuId) for menuId, _ in menuBar(debug=False)]
    expected = [*menus, VIEWPORT_CONTEXT_MENU_ID, IMAGE_LIST_CONTEXT_MENU_ID]
    assert list(MENUS) == expected
    assert MenuId.DEBUG not in MENUS


def test_menus_formats_and_catalogs_are_listed():
    app = Application("kindling-features-test")
    try:
        app.menus.append_menu_items(
            [
                (
                    "varda/processing",
                    SubmenuItem(
                        submenu="varda/processing/filtering", title="&Filtering"
                    ),
                )
            ]
        )
        app.register_action(
            Action(
                id="test.smoothing",
                title="Smoothing…",
                tooltip="Blur each band",
                callback=lambda: None,
                menus=[MenuRule(id="varda/processing/filtering")],
            )
        )
        text = featureInventory(app)
    finally:
        Application.destroy("kindling-features-test")
    assert "- Processing → Filtering → Smoothing: Blur each band" in text
    assert "ENVI Image (.hdr" in text
    assert "PDS Orbital Data Explorer (ODE)" in text


def test_without_the_app_formats_and_catalogs_still_show():
    text = featureInventory(None)
    assert text.startswith("- Opens these file formats:")
