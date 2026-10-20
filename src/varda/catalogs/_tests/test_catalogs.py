"""Varda's catalogs (ODE, STAC, footprint files) against recorded replies,
plus live checks that run with VARDA_LIVE_TESTS=1."""

import json
import os
from datetime import date, datetime, time, timezone
from pathlib import Path

import geopandas as gpd
import pytest
from urllib.parse import parse_qs, urlparse
import shapely

from varda.catalogs import ode, stac
from varda.catalogs.catalog import CatalogError, SearchQuery
from varda.catalogs.bodies import BodyChoice
from varda.catalogs.footprints import FootprintFileCatalog
from varda.catalogs.ode import OdeCatalog, odeLongitudes
from varda.catalogs.registry import availableCatalogs
from varda.catalogs.stac import StacApi, StacCatalog, _checksum
from varda.geo.bodies import BODIES, identifyBody

FIXTURES = Path(__file__).parent / "fixtures"
LIVE = os.environ.get("VARDA_LIVE_TESTS") == "1"
JEZERO = (77.2, 18.3, 77.6, 18.6)


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


class FakeHttp:
    """Answers fetchJson from fixtures, keyed by a test, and records calls."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, url, params=None, body=None, headers=None):
        self.calls.append((url, dict(params or {}), body))
        return self.answer(url, params or {}, body)


def _progress(fraction, message):
    pass


def test_the_shipped_catalogs_are_registered():
    names = [c.catalogId for c in availableCatalogs()]
    assert names[:3] == ["stac", "ode", "footprint_file"]


# --- ODE ------------------------------------------------------------------------


def _ode(monkeypatch):
    def answer(url, params, body):
        if params.get("query") == "iipt":
            return (
                _fixture("ode_iipt_mars.json")
                if params["odemetadb"] == "mars"
                else {"ODEResults": {}}
            )
        if params.get("results") == "f":
            return _fixture("ode_files_mtrdr.json")
        return _fixture("ode_search_mtrdr.json")

    http = FakeHttp(answer)
    monkeypatch.setattr(ode, "fetchJson", http)
    return OdeCatalog(), http


def test_ode_collections_are_its_product_types_with_footprints(monkeypatch):
    catalog, _ = _ode(monkeypatch)
    collections = {c.id: c for c in catalog.collections()}
    mtrdr = collections["mars:MRO:CRISM:MTRDR"]
    assert mtrdr.body == "Mars" and "CRISM" in mtrdr.title
    assert "mars:MRO:CRISM:EDR" not in collections  # no footprints to search
    assert len(catalog.collections()) == len(collections)  # duplicates collapsed


@pytest.mark.parametrize(
    ("west", "east", "expected"),
    [
        (77.2, 77.6, (77.2, 77.6)),
        (-10.0, 10.0, (350.0, 10.0)),
        (-180.0, 180.0, (0.0, 360.0)),
    ],
)
def test_ode_takes_longitudes_0_to_360(west, east, expected):
    assert odeLongitudes(west, east) == expected


def test_ode_search_sends_the_filters_and_reads_products(monkeypatch):
    catalog, http = _ode(monkeypatch)
    catalog.maxIncidence.set(60.0)
    catalog.idContains.set("hrl")
    query = SearchQuery(
        "mars:MRO:CRISM:MTRDR",
        JEZERO,
        datetime(2007, 1, 1),
        datetime(2008, 1, 1),
        limit=2,
    )
    page = catalog.search(query, _progress)
    params = http.calls[-1][1]
    assert (params["ihid"], params["iid"], params["pt"], params["target"]) == (
        "MRO",
        "CRISM",
        "MTRDR",
        "mars",
    )
    assert (params["westernlon"], params["easternlon"]) == (77.2, 77.6)
    assert params["maxinangle"] == 60.0 and "maxemangle" not in params
    assert params["pdsid"] == "*hrl*"
    assert params["minobtime"] == "2007-01-01"
    product = page.products[0]
    assert product.id == "hrl000040ff_07_if183j_mtr3"
    assert product.footprint.intersects(shapely.box(*JEZERO))
    assert product.properties["Incidence (°)"] == pytest.approx(55.6443, abs=1e-3)
    assert product.start.year == 2007
    assert "query=thumbnail" in product.thumbnail
    # the preview is ODE's large browse image (its thumbnails are ~32 px tall)
    preview = parse_qs(urlparse(product.preview).query)
    assert preview == {
        "query": ["lgbrowse"],
        "target": ["mars"],
        "pdsid": ["hrl000040ff_07_if183j_mtr3"],
    }
    assert page.total == 2 and page.nextPage is None


NO_PRODUCTS = {
    "ODEResults": {
        "Products": "No Products Found",
        "QuerySummary": {"query": "PRODUCT"},
        "Status": "Success",
    }
}  # what ODE answers when nothing matches (checked against the live API)


def test_ode_search_with_no_matches_is_empty_not_an_error(monkeypatch):
    monkeypatch.setattr(ode, "fetchJson", FakeHttp(lambda *a: NO_PRODUCTS))
    page = OdeCatalog().search(SearchQuery("mars:MRO:CRISM:MTRDR", JEZERO), _progress)
    assert page.products == () and page.total == 0 and page.nextPage is None


def test_ode_files_with_no_match_say_so(monkeypatch):
    monkeypatch.setattr(ode, "fetchJson", FakeHttp(lambda *a: NO_PRODUCTS))
    with pytest.raises(CatalogError, match="no files listed"):
        OdeCatalog.filesFor(OdeCatalog(), "nothing", "mars", "MRO", "CRISM", "MTRDR")


def test_ode_files_mark_the_products_own_files_essential(monkeypatch):
    catalog, _ = _ode(monkeypatch)
    page = catalog.search(SearchQuery("mars:MRO:CRISM:MTRDR", JEZERO), _progress)
    product = next(p for p in page.products if p.id.startswith("hrl"))
    files = OdeCatalog.filesFor(
        catalog, "frt00005850_07_if167j_mtr3", "mars", "MRO", "CRISM", "MTRDR"
    )
    byName = {f.name.lower(): f for f in files}
    cube = byName["frt00005850_07_if167j_mtr3.img"]
    assert cube.essential and cube.role == "data" and not cube.exactSize
    assert cube.size == 2019707 * 1024
    assert byName["frt00005850_07_if167j_mtr3.xml"].role == "label"
    assert all(not f.essential for f in files if f.role == "browse")
    assert all(
        not f.essential
        for f in files
        if not f.name.lower().startswith("frt00005850_07_if167j_mtr3")
    )
    assert product  # searched first, as the workspace does


# --- STAC ------------------------------------------------------------------------


def _stac(monkeypatch):
    def answer(url, params, body):
        if url.endswith("/collections"):
            return (
                _fixture("stac_collections.json")
                if "astrogeology" in url
                else {"collections": []}
            )
        return _fixture("stac_search_ctx.json")

    http = FakeHttp(answer)
    monkeypatch.setattr(stac, "fetchJson", http)
    return StacCatalog(), http


def test_stac_collections_carry_their_body(monkeypatch):
    catalog, _ = _stac(monkeypatch)
    bodies = {c.id.split("#")[1]: c.body for c in catalog.collections()}
    assert bodies["mro_ctx_controlled_usgs_dtms"] == "Mars"
    assert (
        bodies["galileo_usgs_photogrammetrically_controlled_observations"] == "Europa"
    )


def test_stac_search_posts_the_query_and_follows_pages(monkeypatch):
    catalog, http = _stac(monkeypatch)
    collection = "https://stac.astrogeology.usgs.gov/api#mro_ctx_controlled_usgs_dtms"
    page = catalog.search(
        SearchQuery(collection, (77, 18, 78, 19), datetime(2006, 1, 1), limit=2),
        _progress,
    )
    url, _, body = http.calls[-1]
    assert url.endswith("/search")
    assert body["collections"] == ["mro_ctx_controlled_usgs_dtms"] and body["bbox"] == [
        77,
        18,
        78,
        19,
    ]
    assert body["datetime"].startswith("2006-01-01") and body["datetime"].endswith(
        "/.."
    )
    assert len(page.products) == 2 and page.total == 60
    product = page.products[0]
    assert product.thumbnail.startswith("https://") and "gsd" in product.properties
    assert not any(len(str(v)) > 80 for v in product.properties.values())
    catalog.search(SearchQuery(collection, page=page.nextPage), _progress)
    url, _, body = http.calls[-1]
    assert body["next"] and url == page.nextPage["href"]


def test_stac_previews_are_the_largest_picture(monkeypatch):
    catalog, _ = _stac(monkeypatch)
    page = _fixture("stac_search_ctx.json")
    item = page["features"][0]
    # the CTX items' overview is a GeoTIFF, which the preview can't show
    plain = catalog._product(item, "x#y")
    assert plain.preview == plain.thumbnail
    item["assets"]["browse"] = {
        "href": "https://example.test/browse.png",
        "type": "image/png",
        "roles": ["overview"],
    }
    assert catalog._product(item, "x#y").preview == "https://example.test/browse.png"


def test_stac_files_come_from_the_assets(monkeypatch):
    catalog, _ = _stac(monkeypatch)
    collection = "https://stac.astrogeology.usgs.gov/api#mro_ctx_controlled_usgs_dtms"
    product = catalog.search(SearchQuery(collection), _progress).products[0]
    files = {f.name: f for f in catalog.files(product)}
    roles = {f.role for f in files.values()}
    assert {"data", "browse", "auxiliary"} <= roles
    assert all(f.essential for f in files.values() if f.role == "data")
    assert not any(f.essential for f in files.values() if f.role != "data")


def test_stac_cloud_cover_filters_items(monkeypatch):
    catalog, _ = _stac(monkeypatch)
    page = _fixture("stac_search_ctx.json")
    page["features"][0]["properties"]["eo:cloud_cover"] = 80
    monkeypatch.setattr(stac, "fetchJson", FakeHttp(lambda u, p, b: page))
    catalog.maxCloudCover.set(50.0)
    assert len(catalog.search(SearchQuery("x#y"), _progress).products) == 1


def test_stac_checksums_are_read_from_multihashes():
    assert _checksum("1220" + "ab" * 32) == "sha256:" + "ab" * 32
    assert _checksum("d510" + "cd" * 16) == "md5:" + "cd" * 16
    assert _checksum("") == ""


# --- footprint files -----------------------------------------------------------------


@pytest.fixture
def footprintFile(monkeypatch):
    monkeypatch.setattr(
        FootprintFileCatalog, "paths", [str(FIXTURES / "ode_mtrdr_footprints.gpkg")]
    )
    return FootprintFileCatalog()


def test_the_body_comes_from_the_files_crs(footprintFile):
    (collection,) = footprintFile.collections()
    assert collection.body == "Mars"
    crs = gpd.read_file(FIXTURES / "ode_mtrdr_footprints.gpkg").crs
    assert identifyBody(crs).body == BODIES["Mars"]


def _footprints(tmp_path, monkeypatch, crs, name):
    path = tmp_path / f"{name}.gpkg"
    frame = gpd.GeoDataFrame(
        {"PRODUCT_ID": ["A"]}, geometry=[shapely.box(0, 0, 1000, 1000)], crs=crs
    )
    frame.to_file(path, driver="GPKG")
    monkeypatch.setattr(FootprintFileCatalog, "paths", [str(path)])
    return FootprintFileCatalog()


def test_vesta_footprints_are_on_vesta_not_earth(tmp_path, monkeypatch):
    # Vesta's IAU 2015 sphere, projected: identified as Earth before
    vesta = "+proj=eqc +R=255000 +units=m +no_defs"
    (collection,) = _footprints(tmp_path, monkeypatch, vesta, "vesta").collections()
    assert collection.body == "Vesta"


def test_geographic_footprints_on_an_unknown_body_have_no_body(tmp_path, monkeypatch):
    unknown = "+proj=longlat +R=100000 +no_defs"
    (collection,) = _footprints(tmp_path, monkeypatch, unknown, "x").collections()
    assert collection.body == ""


def test_footprints_on_an_unknown_body_say_so(tmp_path, monkeypatch):
    catalog = _footprints(
        tmp_path, monkeypatch, "+proj=eqc +R=100000 +units=m +no_defs", "unknown"
    )
    with pytest.raises(CatalogError, match="no known body"):
        catalog.collections()


def test_footprint_files_search_by_area_id_and_date(footprintFile):
    path = str(FIXTURES / "ode_mtrdr_footprints.gpkg")
    page = footprintFile.search(SearchQuery(path, JEZERO), _progress)
    assert len(page.products) == 3 and page.nextPage is None
    assert page.products[0].properties["InAngle"]  # the file's columns
    footprintFile.idContains.set("HRL")
    assert [
        p.id for p in footprintFile.search(SearchQuery(path), _progress).products
    ] == ["hrl000040ff_07_if183j_mtr3"]
    footprintFile.idContains.set("")
    late = footprintFile.search(
        SearchQuery(path, start=datetime(2007, 2, 1)), _progress
    )
    assert "hrl000040ff_07_if183j_mtr3" not in [
        p.id for p in late.products
    ]  # observed 2007-01-29


def test_footprint_files_to_date_at_end_of_day_includes_that_day(footprintFile):
    path = str(FIXTURES / "ode_mtrdr_footprints.gpkg")
    endOfDay = datetime.combine(date(2007, 1, 29), time.max, timezone.utc)
    ids = [
        p.id
        for p in footprintFile.search(
            SearchQuery(path, end=endOfDay), _progress
        ).products
    ]
    assert "hrl000040ff_07_if183j_mtr3" in ids  # observed 2007-01-29


def test_ode_is_sent_the_to_day_which_it_takes_to_the_end_of_that_day(monkeypatch):
    # ODE reads maxobtime=2008-01-01 as 2008-01-01T23:59:59.999
    catalog, http = _ode(monkeypatch)
    endOfDay = datetime.combine(date(2008, 1, 1), time.max, timezone.utc)
    catalog.search(SearchQuery("mars:MRO:CRISM:MTRDR", end=endOfDay), _progress)
    assert http.calls[-1][1]["maxobtime"] == "2008-01-01"


def test_ode_footprint_files_get_their_files_from_ode(footprintFile, monkeypatch):
    path = str(FIXTURES / "ode_mtrdr_footprints.gpkg")
    product = footprintFile.search(SearchQuery(path), _progress).products[0]
    asked = []
    monkeypatch.setattr(
        OdeCatalog, "filesFor", lambda self, *args: asked.append(args) or []
    )
    footprintFile.files(product)
    assert asked == [(product.id, "mars", "MRO", "CRISM", "MTRDR")]


def test_other_footprint_files_use_their_url_columns(tmp_path, monkeypatch):
    frame = gpd.GeoDataFrame(
        {
            "id": ["a"],
            "data_url": ["https://example.org/a.tif"],
            "page": ["https://example.org/a.html"],
        },
        geometry=[shapely.box(10, 10, 11, 11)],
        crs="EPSG:4326",
    )
    frame.to_file(tmp_path / "f.geojson", driver="GeoJSON")
    monkeypatch.setattr(FootprintFileCatalog, "paths", [str(tmp_path / "f.geojson")])
    catalog = FootprintFileCatalog()
    assert catalog.collections()[0].body == "Earth"
    (product,) = catalog.search(
        SearchQuery(str(tmp_path / "f.geojson")), _progress
    ).products
    assert [f.url for f in catalog.files(product)] == ["https://example.org/a.tif"]


def test_an_unreadable_footprint_file_says_so(tmp_path, monkeypatch):
    (tmp_path / "bad.geojson").write_text("not json")
    monkeypatch.setattr(FootprintFileCatalog, "paths", [str(tmp_path / "bad.geojson")])
    with pytest.raises(CatalogError, match="couldn't read"):
        FootprintFileCatalog().collections()


# --- live -----------------------------------------------------------------------------


@pytest.mark.skipif(not LIVE, reason="set VARDA_LIVE_TESTS=1 to query ODE and STAC")
def test_live_ode_finds_crism_over_jezero():
    page = OdeCatalog().search(
        SearchQuery("mars:MRO:CRISM:MTRDR", JEZERO, limit=5), _progress
    )
    assert any(p.id.startswith("hrl000040ff") for p in page.products)


@pytest.mark.skipif(not LIVE, reason="set VARDA_LIVE_TESTS=1 to query ODE and STAC")
def test_live_stac_finds_ctx_dtms_over_jezero(monkeypatch):
    monkeypatch.setattr(
        StacCatalog,
        "apis",
        (StacApi("USGS", "https://stac.astrogeology.usgs.gov/api"),),
    )
    catalog = StacCatalog()
    collection = next(
        c.id
        for c in catalog.collections()
        if c.id.endswith("#mro_ctx_controlled_usgs_dtms")
    )
    page = catalog.search(SearchQuery(collection, (77, 18, 78, 19), limit=3), _progress)
    assert page.products and catalog.files(page.products[0])


def test_a_geojson_on_mars_can_say_so(tmp_path, monkeypatch):
    """GeoJSON always declares Earth's coordinates; the Body filter overrides it."""
    frame = gpd.GeoDataFrame(
        {"id": ["a"]}, geometry=[shapely.box(77, 18, 78, 19)], crs="OGC:CRS84"
    )
    frame.to_file(tmp_path / "mars.geojson", driver="GeoJSON")
    monkeypatch.setattr(FootprintFileCatalog, "paths", [str(tmp_path / "mars.geojson")])
    catalog = FootprintFileCatalog()
    assert catalog.collections()[0].body == "Earth"
    catalog.body.set(BodyChoice.MARS)
    assert catalog.collections()[0].body == "Mars"
    (product,) = catalog.search(
        SearchQuery(str(tmp_path / "mars.geojson"), (77.2, 18.2, 77.5, 18.5)), _progress
    ).products
    assert product.footprint.bounds == (77.0, 18.0, 78.0, 19.0)  # degrees as they were
