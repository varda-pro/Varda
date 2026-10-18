"""The Find Data workspace with a stand-in catalog: search, map, selection,
basemap, overlays and sessions."""

import io
from datetime import datetime, timezone

import geopandas as gpd
import numpy as np
import pytest
import shapely
from affine import Affine
from PIL import Image

from varda.catalogs import basemaps, registry
from varda.catalogs.basemaps import BasemapLayer
from varda.catalogs.bodies import geographicCrs
from varda.catalogs.catalog import Catalog, Collection, Product, SearchPage
from varda.common.di_types import ProjectImages
from varda.common.entities import VardaRaster
from varda.common.parameter import FloatParameter
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from PyQt6.QtCore import QDate, QRectF

from varda.workspaces.find_data.find_data_workspace import AREA_BOX, FindDataWorkspace
from varda.workspaces.find_data.map_view import ADD, REMOVE, REPLACE
from varda.workspaces.find_data.projection import MapProjection

PRODUCTS = [
    Product(
        "obs_a",
        "Observation A",
        "mars_obs",
        shapely.box(77.0, 18.0, 77.5, 18.5),
        datetime(2007, 1, 29, tzinfo=timezone.utc),
        properties={"Incidence (°)": 55.0},
    ),
    Product(
        "obs_b",
        "Observation B",
        "mars_obs",
        shapely.box(77.6, 18.2, 78.0, 18.7),
        properties={"Incidence (°)": 40.0},
    ),
]


class FakeCatalog(Catalog):
    catalogId = "fake"
    name = "Fake catalog"
    description = "Two observations on Mars."
    maxIncidence = FloatParameter("Max. incidence", 90.0, range=(0.0, 90.0))
    queries: list = []

    def collections(self):
        return [Collection("mars_obs", "Mars observations", "Mars")]

    def search(self, query, progress):
        FakeCatalog.queries.append(query)
        found = [
            p
            for p in PRODUCTS
            if query.bbox is None or p.footprint.intersects(shapely.box(*query.bbox))
        ]
        found = [
            p for p in found if p.properties["Incidence (°)"] <= self.maxIncidence.get()
        ]
        if query.page is None and len(found) > 1:
            return SearchPage(tuple(found[:1]), nextPage=1, total=len(found))
        return SearchPage(tuple(found[1:] if query.page else found), total=len(found))

    def files(self, product):
        return []


@pytest.fixture
def workspace(qtbot, monkeypatch):
    monkeypatch.setattr(registry, "catalogRegistry", [FakeCatalog])
    FakeCatalog.queries = []
    widget = FindDataWorkspace([])
    qtbot.addWidget(widget)
    qtbot.waitUntil(lambda: widget.collectionBox.count() == 1, timeout=5000)
    return widget


def _search(workspace, qtbot):
    workspace.searchButton.click()
    qtbot.waitUntil(lambda: workspace.searchButton.isEnabled(), timeout=5000)


def test_the_catalogs_collections_and_filters_are_offered(workspace):
    assert workspace.catalogBox.currentText() == "Fake catalog"
    assert workspace.collectionBox.currentText() == "Mars: Mars observations"
    assert workspace.bodyLabel.text() == "<b>Mars</b>"
    assert workspace.filters.layout().count() == 1  # the catalog's own filters


def test_a_search_fills_the_table_and_the_map_and_pages(workspace, qtbot):
    workspace.map.zoomTo((76.5, 17.5, 78.5, 19.0), padding=0)
    _search(workspace, qtbot)
    assert workspace.results.rowCount() == 1
    assert "1 of 2 products" in workspace.searchStatus.text()
    assert workspace.moreButton.isEnabled()
    x, _ = workspace.map.footprints.getData()
    assert np.nanmin(x) == pytest.approx(77.0)
    workspace.moreButton.click()
    qtbot.waitUntil(lambda: workspace.results.rowCount() == 2, timeout=5000)
    assert not workspace.moreButton.isEnabled()
    assert workspace.results.headers[3] == "Incidence (°)"


def test_the_filters_and_dates_reach_the_catalog(workspace, qtbot):
    workspace.catalog().maxIncidence.set(45.0)
    workspace.fromCheck.setChecked(True)
    workspace.areaBox.setCurrentText(AREA_BOX)
    workspace.map.setArea((77.5, 18.0, 78.5, 19.0))
    _search(workspace, qtbot)
    query = FakeCatalog.queries[-1]
    assert query.bbox == pytest.approx((77.5, 18.0, 78.5, 19.0))
    assert query.start is not None and query.end is None
    assert [p.id for p in workspace.results.products] == ["obs_b"]


def test_the_to_date_includes_the_whole_day(workspace, qtbot):
    workspace.fromCheck.setChecked(True)
    workspace.fromDate.setDate(QDate(2010, 1, 5))
    workspace.toCheck.setChecked(True)
    workspace.toDate.setDate(QDate(2010, 1, 5))
    query = workspace.query()
    assert query is not None
    assert query.start == datetime(2010, 1, 5, tzinfo=timezone.utc)
    assert query.end == datetime(2010, 1, 5, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_clicking_a_footprint_selects_it_and_shows_it(workspace, qtbot):
    workspace.map.zoomTo((76.5, 17.5, 78.5, 19.0), padding=0)
    _search(workspace, qtbot)
    hits = workspace.map.productsIn(shapely.Point(77.2, 18.2))
    workspace._onMapSelect(hits, REPLACE)
    assert [p.id for p in workspace.selectedProducts()] == ["obs_a"]
    assert (
        "obs_a" in workspace.details.text() and "Incidence" in workspace.details.text()
    )
    x, _ = workspace.map.selected.getData()
    assert len(x) > 0


def test_a_mars_image_becomes_the_basemap(workspace, qtbot, monkeypatch):
    crs = geographicCrs("Mars")
    image = VardaRaster(
        ArrayDataSource(
            np.random.default_rng(0).random((50, 60, 3)).astype(np.float32),
            wavelengths=np.array([400.0, 500.0, 600.0]),
            transform=Affine(0.05, 0, 76.5, 0, -0.05, 19.5),
            crs=crs,
        ),
        name="mars image",
    )
    workspace.projectImages.append(image)
    workspace._onCollection()
    # None, Mars's two imagery layers, the image
    assert workspace.basemapBox.count() == 4
    workspace.basemapBox.setCurrentIndex(0)
    assert workspace.map.basemapItem.image is None
    workspace.basemapBox.setCurrentIndex(3)
    qtbot.waitUntil(lambda: workspace.map.basemapItem.image is not None, timeout=10000)
    rect = workspace.map.basemapItem.boundingRect()
    assert rect.width() > 0
    assert workspace.streamer.layer is None  # no tiles over a loaded image


def test_overlays_are_drawn_and_saved(workspace, tmp_path):
    traverse = gpd.GeoDataFrame(
        geometry=[shapely.LineString([(77.4, 18.4), (77.5, 18.5)])],
        crs=geographicCrs("Mars"),
    )
    traverse.to_file(tmp_path / "traverse.gpkg", driver="GPKG")
    workspace.addOverlay(str(tmp_path / "traverse.gpkg"))
    assert workspace.map._overlays and workspace.overlayPaths == [
        str(tmp_path / "traverse.gpkg")
    ]


def test_the_workspace_comes_back_from_a_session(workspace, qtbot, tmp_path):
    workspace.areaBox.setCurrentText(AREA_BOX)
    workspace.map.setArea((77.0, 18.0, 78.0, 19.0))
    state = workspace.sessionState()
    reopened = FindDataWorkspace([])
    qtbot.addWidget(reopened)
    reopened.restoreSessionState(state)
    qtbot.waitUntil(lambda: reopened.collectionBox.count() == 1, timeout=5000)
    assert reopened.areaBox.currentText() == AREA_BOX
    assert reopened.map.areaBounds() == pytest.approx((77.0, 18.0, 78.0, 19.0))


def test_checked_products_go_to_the_downloads(workspace, qtbot):
    _search(workspace, qtbot)
    workspace.results.setChecked(["obs_a"])
    workspace.addToDownloads()
    assert [q.product.id for q in workspace.downloads.queue] == ["obs_a"]
    assert workspace.downloads.queue[0].catalog is workspace.catalog()


def test_a_catalog_that_needs_a_login_asks_before_searching(workspace, monkeypatch):
    FakeCatalog.credentialFields = ("token",)
    asked = []
    monkeypatch.setattr(
        "varda.workspaces.find_data.find_data_workspace.CredentialsDialog.exec",
        lambda dialog: asked.append(dialog.catalog) or 0,
    )
    try:
        workspace.search()
    finally:
        FakeCatalog.credentialFields = ()
    assert asked == [workspace.catalog()]
    assert workspace.searchStatus.text() == "This catalog needs a login."
    assert FakeCatalog.queries == []


def _selected(workspace):
    return sorted(p.id for p in workspace.selectedProducts())


def test_shift_adds_cmd_removes_and_a_box_adds_footprints(workspace, qtbot):
    workspace.map.zoomTo((76.5, 17.5, 78.5, 19.0), padding=0)
    _search(workspace, qtbot)
    workspace.more()
    qtbot.waitUntil(lambda: len(workspace.results.products) == 2, timeout=5000)
    workspace._onMapSelect(["obs_a"], REPLACE)
    workspace._onMapSelect(["obs_b"], ADD)
    assert _selected(workspace) == ["obs_a", "obs_b"]
    workspace._onMapSelect(["obs_a"], REMOVE)
    assert _selected(workspace) == ["obs_b"]
    picked = []
    workspace.map.sigSelect.connect(lambda ids, mode: picked.append((ids, mode)))
    workspace.map._onBox(QRectF(77.4, 18.1, 0.3, 0.2))  # touches both
    assert picked == [(["obs_a", "obs_b"], ADD)]


def test_show_only_selected_filters_the_table(workspace, qtbot):
    workspace.map.zoomTo((76.5, 17.5, 78.5, 19.0), padding=0)
    _search(workspace, qtbot)
    workspace.more()
    qtbot.waitUntil(lambda: len(workspace.results.products) == 2, timeout=5000)
    workspace._onMapSelect(["obs_b"], REPLACE)
    workspace.onlySelected.setChecked(True)
    assert workspace.proxy.rowCount() == 1 and _selected(workspace) == ["obs_b"]
    workspace._onMapSelect(["obs_a"], ADD)  # selecting on the map changes the list
    assert workspace.proxy.rowCount() == 2
    workspace.onlySelected.setChecked(False)
    assert workspace.proxy.rowCount() == 2 and _selected(workspace) == [
        "obs_a",
        "obs_b",
    ]


def test_the_polar_map_draws_footprints_and_searches_around_the_pole(workspace, qtbot):
    _search(workspace, qtbot)
    north = workspace.projectionBox.findData(MapProjection.NORTH_POLAR)
    workspace.projectionBox.setCurrentIndex(north)
    assert workspace.map.projector.polar and workspace.map.projector.body == "Mars"
    x, _ = workspace.map.footprints.getData()
    assert len(x) > 0 and abs(np.nanmax(np.abs(x))) > 1000  # meters, not degrees
    workspace.map.showWhole()
    west, south, east, north_ = workspace.searchArea()
    assert (west, east, north_) == (-180.0, 180.0, 90.0) and south == pytest.approx(
        0.0, abs=0.5
    )
    assert workspace.sessionState()["projection"] == "NORTH_POLAR"


def _marsImage(name="mars image", data=None):
    if data is None:
        data = np.random.default_rng(0).random((50, 60, 3)).astype(np.float32)
    return VardaRaster(
        ArrayDataSource(
            data,
            wavelengths=np.array([400.0, 500.0, 600.0]),
            transform=Affine(0.05, 0, 76.5, 0, -0.05, 19.5),
            crs=geographicCrs("Mars"),
        ),
        name=name,
    )


def test_images_loaded_later_become_basemaps_and_keep_the_choice(qtbot, monkeypatch):
    monkeypatch.setattr(registry, "catalogRegistry", [FakeCatalog])
    images = ProjectImages()
    workspace = FindDataWorkspace(images)
    qtbot.addWidget(workspace)
    qtbot.waitUntil(lambda: workspace.collectionBox.count() == 1, timeout=5000)
    assert workspace.basemapBox.count() == 3  # None and Mars's two layers
    first = _marsImage("first")
    images.append(first)  # e.g. a download that opened
    assert workspace.basemapBox.count() == 4 and workspace.areaImage.count() == 1
    workspace.basemapBox.setCurrentIndex(3)
    images.append(_marsImage("second"))
    assert workspace.basemapBox.count() == 5
    assert workspace.basemapBox.currentData() is first  # the choice stays
    images.remove(first)
    assert workspace.basemapBox.currentData() is None


def test_the_basemap_is_drawn_north_up_and_not_mirrored(workspace):
    rgba = np.zeros((4, 6, 4), np.uint8)
    rgba[0, 0] = (255, 0, 0, 255)  # the northwest corner (rows run north to south)
    workspace.map.setBasemap(rgba, (10.0, 20.0, 16.0, 24.0))
    item = workspace.map.basemapItem
    image = item.image
    assert image is not None and image.shape[:2] == (4, 6)  # rows are y
    # pyqtgraph puts the item's first row at the bottom (south) of its rect
    assert tuple(image[-1, 0]) == (255, 0, 0, 255)
    rect = item.mapRectToParent(item.boundingRect())
    assert (rect.left(), rect.top(), rect.width(), rect.height()) == pytest.approx(
        (10, 20, 6, 4)
    )


# --- global imagery, names and Go to ------------------------------------------------


def test_the_bodys_global_imagery_is_the_default_basemap(workspace, qtbot):
    layer = workspace.basemapBox.currentData()
    assert isinstance(layer, BasemapLayer) and layer.body == "Mars"
    qtbot.waitUntil(lambda: workspace.map.basemapItem.image is not None, timeout=10000)
    rect = workspace.map.basemapItem.mapRectToParent(
        workspace.map.basemapItem.boundingRect()
    )
    assert (rect.left(), rect.top(), rect.width(), rect.height()) == pytest.approx(
        (-180, -90, 360, 180)
    )
    credit = workspace.map.credit.text()
    assert "Solar System Treks" in credit and "MOLA" in credit
    assert "Planetary Nomenclature" in credit


def test_a_polar_view_reprojects_the_bundled_image(workspace, qtbot):
    workspace.projectionBox.setCurrentIndex(
        workspace.projectionBox.findData(MapProjection.SOUTH_POLAR)
    )
    qtbot.waitUntil(lambda: workspace.map.basemapItem.image is not None, timeout=10000)
    image = workspace.map.basemapItem.image
    assert image is not None and image.shape[:2] == (1500, 1500)
    assert workspace.map.tilesItem.image is None  # no tiles off equirectangular


def test_names_appear_by_size_and_can_be_hidden(workspace, qtbot):
    workspace.map.showWhole()
    qtbot.wait(50)
    zoomedOut = workspace.map.shownNames()
    assert "Gale" not in zoomedOut and zoomedOut  # large features only
    assert len(zoomedOut) <= 60
    workspace.map.zoomTo((134.0, -9.0, 142.0, -2.0), padding=0)
    qtbot.wait(50)
    assert "Gale" in workspace.map.shownNames()
    workspace.namesCheck.setChecked(False)
    assert workspace.map.shownNames() == []
    assert "Planetary Nomenclature" not in workspace.map.credit.text()


def test_go_to_zooms_to_a_feature(workspace, qtbot):
    workspace.goTo.setText("gale")
    workspace.goTo.returnPressed.emit()
    west, south, east, north = workspace.map.viewBounds()
    assert west < 137.8 < east and south < -5.4 < north
    assert north - south < 10  # about twice Gale's 154 km
    assert workspace.goToFeature("Olympus Mons")
    west, _, east, _ = workspace.map.viewBounds()
    assert west < -133.8 < east
    assert not workspace.goToFeature("No Such Place")
    names = workspace.goToNames.stringList()
    assert "Gale" in names and "Isidis Planitia" in names


def test_zooming_in_streams_tiles_over_the_bundled_image(workspace, qtbot, monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (256, 256), (0, 255, 0)).save(buffer, "PNG")
    fetched = []

    def fetch(url: str, *args, **kwargs) -> bytes:
        fetched.append(url)
        return buffer.getvalue()

    monkeypatch.setattr(basemaps, "fetchBytes", fetch)
    workspace.goToFeature("Gale")
    workspace.streamer.refresh()
    qtbot.waitUntil(lambda: workspace.map.tilesItem.image is not None, timeout=10000)
    assert fetched and all("Mars_MGS_MOLA" in url for url in fetched)
    workspace.map.showWhole()
    workspace.streamer.refresh()
    assert workspace.map.tilesItem.image is None  # the bundled image is enough


def test_offline_tiles_leave_the_bundled_image_and_a_note(workspace, qtbot):
    workspace.goToFeature("Gale")
    workspace.streamer.refresh()
    qtbot.waitUntil(
        lambda: "couldn't be fetched" in workspace.status.currentMessage(),
        timeout=10000,
    )
    image = workspace.map.tilesItem.image
    assert image is None or (image[..., 3] == 0).all()
    assert workspace.map.basemapItem.image is not None


def test_the_basemap_choice_comes_back_from_a_session(workspace, qtbot):
    workspace.basemapBox.setCurrentIndex(2)  # Viking color
    workspace.namesCheck.setChecked(False)
    state = workspace.sessionState()
    assert state["basemapLayer"].startswith("Mars_Viking") and not state["names"]
    reopened = FindDataWorkspace([])
    qtbot.addWidget(reopened)
    reopened.restoreSessionState(state)
    qtbot.waitUntil(lambda: reopened.collectionBox.count() == 1, timeout=5000)
    qtbot.waitUntil(lambda: reopened.basemapBox.currentIndex() == 2, timeout=5000)
    assert not reopened.namesCheck.isChecked()
