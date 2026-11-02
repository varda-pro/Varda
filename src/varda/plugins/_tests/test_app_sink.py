"""Plugin analyses appear in the Processing menu, and leave it again."""

import pytest
from app_model import Application

from varda.catalogs.catalog import Catalog
from varda.catalogs.registry import availableCatalogs
from varda._actions._menu_ids import MenuId
from varda._actions._processing_actions import categoryMenuId, processingActions
from varda.api import Analysis
from varda.image_loading.data_sources.registry import datasource_registry
from varda.plugins.app_sink import AppContributionSink
from varda.plugins.plugin_manager import Contributions


class _NewCategory(Analysis):
    analysisId = "sink_test"
    name = "Sink Test"
    category = "My Plugin Tools"


class _BuiltinCategory(Analysis):
    analysisId = "sink_test_builtin"
    name = "Sink Test Builtin"
    category = "Spectral Parameters"


class _FakeSource:
    pass


@pytest.fixture
def app():
    app = Application("varda-test-sink")
    yield app
    Application.destroy("varda-test-sink")


def _menuItems(app, menuId):
    try:
        return list(app.menus.get_menu(menuId))
    except KeyError:  # a menu with nothing left in it
        return []


def test_analyses_become_menu_items_and_go_away(app):
    sink = AppContributionSink(app)
    remove = sink.add(
        "my-plugin", Contributions(analyses=(_NewCategory, _BuiltinCategory))
    )
    assert "varda.plugin.my_plugin.sink_test" in app.commands
    submenu = categoryMenuId(MenuId.PROCESSING, "My Plugin Tools")
    assert any(
        getattr(i, "submenu", None) == submenu
        for i in _menuItems(app, MenuId.PROCESSING)
    )
    remove()
    assert "varda.plugin.my_plugin.sink_test" not in app.commands
    assert not any(
        getattr(i, "submenu", None) == submenu
        for i in _menuItems(app, MenuId.PROCESSING)
    )


def test_a_shared_category_stays_until_its_last_plugin_goes(app):
    sink = AppContributionSink(app)
    first = sink.add("a", Contributions(analyses=(_NewCategory,)))
    second = sink.add("b", Contributions(analyses=(_NewCategory,)))
    submenu = categoryMenuId(MenuId.PROCESSING, "My Plugin Tools")

    def count() -> int:
        return sum(
            getattr(i, "submenu", None) == submenu
            for i in _menuItems(app, MenuId.PROCESSING)
        )  # noqa: E731

    assert count() == 1
    first()
    assert count() == 1
    second()
    assert count() == 0


def test_file_formats_join_the_registry_and_leave_it(app):
    sink = AppContributionSink(app)
    before = len(datasource_registry)
    remove = sink.add(
        "fmt", Contributions(dataSources=(("Test Format", (".tst",), _FakeSource),))
    )
    assert datasource_registry[-1].formatName == "Test Format"
    remove()
    assert len(datasource_registry) == before


def _commandIds(app, menuId) -> list[str]:
    return [i.command.id for i in _menuItems(app, menuId) if hasattr(i, "command")]


def test_plugin_analyses_are_listed_in_the_plugins_menu_too(app):
    sink = AppContributionSink(app)
    remove = sink.add("my-plugin", Contributions(analyses=(_NewCategory,)))
    action = "varda.plugin.my_plugin.sink_test"
    submenu = categoryMenuId(MenuId.PROCESSING, "My Plugin Tools")
    assert action in _commandIds(app, submenu)
    assert action in _commandIds(app, MenuId.PLUGINS)
    remove()
    assert action not in _commandIds(app, MenuId.PLUGINS)


class _Ellipsis(Analysis):
    analysisId = "ellipsis"
    name = "Spectral Smoothing..."


def test_a_name_ending_in_an_ellipsis_gets_only_one():
    (action,) = processingActions(
        MenuId.PROCESSING, "x", [_Ellipsis], lambda a: lambda: None
    )
    assert action.title == "Spectral Smoothing…"


class _Nested(Analysis):
    analysisId = "nested"
    name = "Nested"
    category = "Filtering/Spectral"


class _NewNested(Analysis):
    analysisId = "new_nested"
    name = "New Nested"
    category = "Mosaics/Seams"


def _submenuIds(app, menuId) -> list[str]:
    return [getattr(i, "submenu", None) for i in _menuItems(app, menuId)]


def test_a_plugin_can_join_an_existing_submenu_without_duplicating_it(app):
    sink = AppContributionSink(app)
    remove = sink.add("p", Contributions(analyses=(_Nested,)))
    spectral = categoryMenuId(MenuId.PROCESSING, "Filtering/Spectral")
    assert "varda.plugin.p.nested" in _commandIds(app, spectral)
    filtering = categoryMenuId(MenuId.PROCESSING, "Filtering")
    assert filtering not in _submenuIds(
        app, MenuId.PROCESSING
    )  # Varda's own, not added again
    remove()


def test_a_plugin_s_new_nested_menus_are_added_and_removed(app):
    sink = AppContributionSink(app)
    remove = sink.add("p", Contributions(analyses=(_NewNested,)))
    mosaics = categoryMenuId(MenuId.PROCESSING, "Mosaics")
    seams = categoryMenuId(MenuId.PROCESSING, "Mosaics/Seams")
    assert mosaics in _submenuIds(app, MenuId.PROCESSING)
    assert seams in _submenuIds(app, mosaics)
    assert "varda.plugin.p.new_nested" in _commandIds(app, seams)
    remove()
    assert mosaics not in _submenuIds(app, MenuId.PROCESSING)
    assert seams not in _submenuIds(app, mosaics)


def test_catalogs_join_the_registry_and_leave_it(app):
    class _Catalog(Catalog):
        catalogId = "test"

    sink = AppContributionSink(app)
    remove = sink.add("p", Contributions(catalogs=(_Catalog,)))
    assert _Catalog in availableCatalogs()
    remove()
    assert _Catalog not in availableCatalogs()
