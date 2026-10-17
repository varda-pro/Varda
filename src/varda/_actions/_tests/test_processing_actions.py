"""The Processing menu names each operation, grouped by category."""

from app_model.types import SubmenuItem

from varda._actions import ALL_ACTIONS
from varda._actions._menu_ids import MENUBAR, MenuId
from varda._actions._processing_actions import (
    OPTIONAL_FEATURES_ACTION_ID,
    SPECTRAL_LIBRARIES_ACTION_ID,
    categoryMenuId,
    processingActions,
    PROCESSING_SUBMENUS,
    processingSubmenus,
)
from varda.analysis.analysis import Analysis


class Pca(Analysis):
    analysisId = "pca"
    name = "PCA"
    category = "Transforms"

    def run(self, image, reportProgress):
        return image


class Mnf(Analysis):
    analysisId = "mnf"
    name = "MNF"
    category = "Transforms"

    def run(self, image, reportProgress):
        return image


class NeedsThing(Analysis):
    analysisId = "needs_thing"
    name = "Needs Thing"
    category = "Statistics"
    requirement = "Thing"

    @classmethod
    def isAvailable(cls):
        return False

    def run(self, image, reportProgress):
        return image


class NeedsInstallable(NeedsThing):
    analysisId = "needs_installable"
    name = "Needs Installable"
    requirement = "PyTorch"
    installable = True


def _noCallback(analysisClass):
    return lambda: None


def test_processing_replaces_the_generic_analysis_menu():
    assert (MenuId.PROCESSING, "Processing") in MENUBAR
    assert not any(a.id == "varda.analysis.run" for a in ALL_ACTIONS)


def test_one_submenu_per_category_in_first_appearance_order():
    submenus = processingSubmenus("root", [Pca, Mnf, NeedsThing])
    assert [(parent, item.title, item.submenu) for parent, item in submenus] == [
        ("root", "Transforms", "root/transforms"),
        ("root", "Statistics", "root/statistics"),
    ]
    assert all(isinstance(item, SubmenuItem) for _p, item in submenus)


def test_one_action_per_analysis_under_its_category():
    actions = processingActions("root", "prefix", [Pca, Mnf], _noCallback)
    assert [a.id for a in actions] == ["prefix.pca", "prefix.mnf"]
    assert [a.title for a in actions] == ["PCA…", "MNF…"]
    assert all(a.menus[0].id == categoryMenuId("root", "Transforms") for a in actions)
    assert all(a.enablement.eval({"image_count": 1}) for a in actions)
    assert not any(a.enablement.eval({"image_count": 0}) for a in actions)


def test_unavailable_analysis_is_shown_disabled_and_says_what_it_needs():
    (action,) = processingActions("root", "prefix", [NeedsThing], _noCallback)
    assert action.title == "Needs Thing… (needs Thing)"
    assert not action.enablement.eval({"image_count": 5})


def test_an_installable_requirement_keeps_the_entry_enabled_to_offer_the_install():
    (action,) = processingActions("root", "prefix", [NeedsInstallable], _noCallback)
    assert action.title == "Needs Installable…"
    assert action.enablement.eval({"image_count": 1})
    assert "PyTorch" in action.tooltip


def test_optional_features_is_at_the_end_of_the_processing_menu():
    (action,) = [a for a in ALL_ACTIONS if a.id == OPTIONAL_FEATURES_ACTION_ID]
    assert action.title == "Optional Features…"
    assert action.menus[0].id == MenuId.PROCESSING
    assert action.enablement is None  # usable before any image is open


def test_spectral_libraries_is_in_the_processing_menu_without_an_image():
    (action,) = [a for a in ALL_ACTIONS if a.id == SPECTRAL_LIBRARIES_ACTION_ID]
    assert action.title == "Spectral Libraries…"
    assert action.menus[0].id == MenuId.PROCESSING
    assert action.enablement is None


def test_band_parameters_is_offered_under_spectral_parameters():
    (action,) = [a for a in ALL_ACTIONS if a.id == "varda.processing.band_parameters"]
    assert action.title.startswith("Band Parameters (HyPyRameter)…")
    assert action.menus[0].id == "varda/processing/spectral_parameters"
    assert ("varda/processing", "Spectral Parameters") in [
        (parent, item.title) for parent, item in PROCESSING_SUBMENUS
    ]


class _Spatial(Analysis):
    analysisId = "spatial"
    name = "Spatial"
    category = "Filtering/Spatial"


class _Spectral(Analysis):
    analysisId = "spectral"
    name = "Spectral"
    category = "Filtering/Spectral"


def test_a_category_path_nests_submenus_once_per_level():
    submenus = processingSubmenus("root", [_Spatial, _Spectral])
    placed = [(parent, item.submenu, item.title) for parent, item in submenus]
    assert placed == [
        ("root", "root/filtering", "Filtering"),
        ("root/filtering", "root/filtering_spatial", "Spatial"),
        ("root/filtering", "root/filtering_spectral", "Spectral"),
    ]


def test_the_menu_puts_spatial_before_spectral_filters():
    titles = [
        item.title
        for parent, item in PROCESSING_SUBMENUS
        if parent.endswith("/filtering")
    ]
    assert titles == ["Spatial", "Spectral"]
