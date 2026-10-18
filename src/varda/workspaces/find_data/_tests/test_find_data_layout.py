"""At laptop sizes, nothing in the Find Data workspace is cut off: panels
wrap, elide (with the full text in a tooltip) or scroll instead."""

import pytest
import shapely

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
    qtbot.waitUntil(lambda: widget.collectionBox.count() == 1, timeout=5000)
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
