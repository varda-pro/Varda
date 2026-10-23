"""At laptop sizes, nothing in the Find Data workspace is cut off: panels
wrap, elide (with the full text in a tooltip) or scroll instead."""

import pytest
import shapely
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton

from varda.catalogs import registry
from varda.catalogs.catalog import Catalog, Collection, Product
from varda.common.layout_audit import assertNothingClipped
from varda.workspaces.find_data.find_data_workspace import FindDataWorkspace

TITLE = "Lunar Orbiter Laser Altimeter (LOLA) Shot Observations (USGS Astrogeology)"


class LongCatalog(Catalog):
    catalogId = "long"
    name = "STAC catalogs"
    description = (
        "Any STAC API: USGS Astrogeology (Mars, the Moon, Europa, …) and Earth "
        "Search (Sentinel-2, Landsat, …) by default; add others with the STAC "
        "APIs… button."
    )

    def collections(self):
        return [Collection("lola", TITLE, "Moon")]

    def search(self, query, progress):
        raise NotImplementedError

    def files(self, product: Product):
        return []


@pytest.fixture
def workspace(qtbot, monkeypatch):
    monkeypatch.setattr(registry, "catalogRegistry", [LongCatalog])
    widget = FindDataWorkspace([])
    qtbot.addWidget(widget)
    qtbot.waitUntil(lambda: widget.source() is not None, timeout=5000)
    return widget


# a 1440×900 and a 1280×800 laptop screen, less the menu bar and tabs
@pytest.mark.parametrize("size", [(1440, 800), (1280, 700)])
def test_nothing_is_cut_off(workspace, qtbot, size):
    workspace.resize(*size)
    workspace.show()
    qtbot.waitExposed(workspace)
    qtbot.wait(50)
    assertNothingClipped(workspace)


def test_a_selected_product_fits_the_preview(workspace, qtbot):
    workspace.resize(1280, 700)
    workspace.show()
    qtbot.waitExposed(workspace)
    product = Product("p", "P", "lola", shapely.box(0, 0, 1, 1))
    workspace.results.setProducts([product])
    workspace.selectProducts(["p"])
    qtbot.wait(50)
    assertNothingClipped(workspace)


def test_the_map_panel_fits_at_1280_by_800(workspace, qtbot):
    """The map's bar (body, projection, basemap, names, Go to, overlays)
    wraps, and the imagery credit wraps under the map."""
    workspace.resize(1280, 800)
    workspace.show()
    qtbot.waitExposed(workspace)
    qtbot.wait(50)
    assert workspace.basemapBox.currentData() is not None  # the Moon's imagery
    assert workspace.map.credit.isVisible()
    assertNothingClipped(workspace.mapPanel)


# What a 1024×700 window leaves the workspace beside the Image List
SMALL = (760, 620)


def test_the_map_keeps_room_on_a_small_screen(workspace, qtbot):
    """Release QA 0.12.2: at 1024×700 the map's settings wrapped onto five
    rows and left the map a strip about 60 pixels tall."""
    workspace.resize(*SMALL)
    workspace.show()
    qtbot.waitExposed(workspace)
    qtbot.wait(100)
    assert workspace.map.plot.height() >= 180
    assertNothingClipped(workspace.mapPanel)


def test_the_map_settings_fold_away_and_open_on_request(workspace, qtbot):
    workspace.resize(*SMALL)
    workspace.show()
    qtbot.waitExposed(workspace)
    qtbot.wait(100)
    section = workspace.mapSettings
    assert not section.isExpanded()  # folded: the panel is short
    qtbot.mouseClick(section.findChild(QPushButton), Qt.MouseButton.LeftButton)
    qtbot.wait(400)  # the section opens (animated): it stays open
    workspace.resize(SMALL[0], SMALL[1] - 20)
    qtbot.wait(100)
    assert section.isExpanded()
    assert workspace.previewBox.isVisible()  # Map preview, in the section


def test_the_map_settings_are_open_on_a_large_screen(workspace, qtbot):
    workspace.resize(1440, 800)
    workspace.show()
    qtbot.waitExposed(workspace)
    qtbot.wait(100)
    assert workspace.mapSettings.isExpanded()
