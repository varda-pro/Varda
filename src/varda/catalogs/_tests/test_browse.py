"""Standard browse products (``Catalog.browseImages``): ODE's, STAC's and
footprint files', against recorded replies (no network)."""

import json
from pathlib import Path

import geopandas as gpd
import pytest
import shapely

from varda import api
from varda.catalogs import ode, stac
from varda.catalogs.browse import ordered, sentenceCase
from varda.catalogs.catalog import BrowseImage, Catalog, Product, SearchQuery
from varda.catalogs.footprints import FootprintFileCatalog
from varda.catalogs.ode import OdeCatalog, browseKind
from varda.catalogs.stac import StacCatalog

FIXTURES = Path(__file__).parent / "fixtures"
MTRDR = "FRT00005850_07_IF167J_MTR3"
MTRDR_IDS = ("mars", "MRO", "CRISM", "MTRDR")
TC = "TCO_MAP_02_N00E000S03E003SC"  # a Kaguya Terrain Camera ortho map


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakeHttp:
    """Answers fetchJson with one reply and counts the calls."""

    def __init__(self, reply: dict) -> None:
        self.reply, self.calls = reply, []

    def __call__(self, url, params=None, body=None, headers=None):
        self.calls.append(dict(params or {}))
        return self.reply


def _ode(monkeypatch, fixture: str = "ode_files_mtrdr.json"):
    http = FakeHttp(_fixture(fixture))
    monkeypatch.setattr(ode, "fetchJson", http)
    return OdeCatalog(), http


def _product(pdsid: str = MTRDR, collection: str = "mars:MRO:CRISM:MTRDR") -> Product:
    return Product(pdsid, pdsid, collection, shapely.Point())


def _listing(*files: dict) -> dict:
    """An ODE file-list reply of ``files``."""
    return {
        "ODEResults": {
            "Products": {"Product": {"Product_files": {"Product_file": list(files)}}},
            "Status": "Success",
        }
    }


def test_browse_images_are_none_by_default():
    assert Catalog().browseImages(_product()) == []


def test_browse_images_are_part_of_the_plugin_api():
    assert api.BrowseImage is BrowseImage and "BrowseImage" in api.__all__


# --- ODE ------------------------------------------------------------------------


def test_a_crism_mtrdrs_browse_products_composites_first(monkeypatch):
    catalog, _ = _ode(monkeypatch)
    kinds = [b.kind for b in catalog.browseImages(_product())]
    assert kinds == [
        "Infrared false color",
        "True color",
        "RGB display",
        "Carbonates",
        "Carbonates (version 2)",
        "Fe mineralogy",
        "Fe mineralogy (version 2)",
        "IR albedo",
        "Mafic mineralogy",
        "Phyllosilicate with Al",
        "Phyllosilicate with Fe and Mg",
        "VNIR albedo",
    ]


def test_ode_browse_images_are_pictures_with_odes_descriptions(monkeypatch):
    catalog, _ = _ode(monkeypatch)
    images = catalog.browseImages(_product())
    # the browse PDS images, labels, headers and the review spreadsheet aren't
    assert all(b.url.endswith(".png") for b in images)
    falseColor = images[0]
    assert falseColor.url.endswith("frt00005850_07_brfalj_mtr3.png")
    assert falseColor.description == "INFRARED FALSE COLOR BROWSE QUICKLOOK"


def test_ode_lists_a_products_files_once(monkeypatch):
    catalog, http = _ode(monkeypatch)
    product = _product()
    files = catalog.files(product)
    catalog.browseImages(product)
    catalog.files(product)
    assert len(http.calls) == 1
    assert http.calls[0]["results"] == "f" and http.calls[0]["pdsid"] == MTRDR
    # ODE's descriptions come along, without their HTML help links
    kml = next(f for f in files if f.name.endswith("_XML.KML"))
    assert kml.description == "PRODUCT KML FILE"
    cube = next(f for f in files if f.name == f"{MTRDR}.IMG")
    assert cube.description == "PRODUCT DATA FILE"


def test_ode_keeps_the_latest_file_lists(monkeypatch):
    catalog, http = _ode(monkeypatch)
    monkeypatch.setattr(ode, "LISTINGS_KEPT", 2)
    for pdsid in ("a", "b", "a", "c", "a", "b"):
        catalog.filesFor(pdsid, *MTRDR_IDS)
    # a, b fetched; a kept; c pushes out b; a kept; b fetched again
    assert [c["pdsid"] for c in http.calls] == ["a", "b", "c", "b"]


def test_a_sibling_products_browse_images_are_not_the_products(monkeypatch):
    # A HiRISE DTM's file list includes its orthoimages (other products) and
    # their browse images, and annotated copies and thumbnails of its own
    catalog, _ = _ode(monkeypatch, "ode_files_hirise_dtm.json")
    dtm = "DTEEC_023247_1985_022957_1985_U01"
    images = catalog.browseImages(_product(dtm, "mars:MRO:HIRISE:DTM"))
    assert [(b.kind, b.url.rsplit("/", 1)[1]) for b in images] == [
        ("Browse", f"{dtm}.br.jpg")
    ]


def test_ode_thumbnails_only_when_there_is_nothing_larger(monkeypatch):
    # Kaguya's browse image and thumbnail have one name, in two folders
    def entry(description: str, folder: str) -> dict:
        return {
            "Description": description,
            "FileName": f"{TC}.JPG",
            "Type": "Browse",
            "URL": f"https://example.test/{folder}/{TC}.jpg",
        }

    large, small = entry("BROWSE", "large"), entry("THUMBNAIL", "small")
    monkeypatch.setattr(ode, "fetchJson", FakeHttp(_listing(large, small)))
    images = OdeCatalog().browseImagesFor(TC, "moon", "SLN", "TC", "TCORT")
    assert [b.url for b in images] == [large["URL"]]
    monkeypatch.setattr(ode, "fetchJson", FakeHttp(_listing(small)))
    images = OdeCatalog().browseImagesFor(TC, "moon", "SLN", "TC", "TCORT")
    assert [(b.kind, b.url) for b in images] == [("Thumbnail", small["URL"])]


@pytest.mark.parametrize(
    ("description", "name", "kind"),
    [
        ("INFRARED FALSE COLOR BROWSE QUICKLOOK", "a.png", "Infrared false color"),
        ("PHYLLOSILICATE WITH FE AND MG BROWSE QUICKLOOK", "a.png", "Phyllosilicate with Fe and Mg"),
        ("VERSION 2 CARBONATES BROWSE QUICKLOOK", "a.png", "Carbonates (version 2)"),
        ("VERSION 2 FE MINERALOGY BROWSE QUICKLOOK", "a.png", "Fe mineralogy (version 2)"),
        ("VNIR ALBEDO BROWSE QUICKLOOK", "a.png", "VNIR albedo"),
        ("RGB DISPLAY FOR MTRDR SPECTRAL PRODUCT", "a.png", "RGB display"),
        ("MERGED IRB BROWSE", "a.jpg", "Merged IRB"),
        ("GREYSCALE BROWSE", "X_RED.NOMAP.browse.jpg", "Grayscale (unprojected)"),
        ("BROWSE IMAGE", "a.jpeg", "Browse"),
        ("BROWSE BR", "a.br.jpg", "Browse"),
        ("", "FRT0001_BRFALJ.PNG", "FRT0001_BRFALJ.PNG"),
    ],
)  # fmt: skip
def test_browse_kinds_are_odes_descriptions_in_sentence_case(description, name, kind):
    assert browseKind(description, name) == kind


# --- labels and order ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("HYDRATED SILICA", "Hydrated silica"),
        ("TIO2 ABUNDANCE", "TiO2 abundance"),
        ("HIRISE COLOR", "HiRISE color"),
        ("True Color Image (10m)", "True color image (10m)"),
        ("NDVI of Sentinel-2", "NDVI of sentinel-2"),
        ("thumbnail", "Thumbnail"),
    ],
)
def test_sentence_case_keeps_acronyms_and_element_symbols(text, expected):
    assert sentenceCase(text) == expected


def test_ordered_keeps_one_image_of_each_kind():
    images = [
        BrowseImage("Mafic", "https://x/1.png"),
        BrowseImage("Browse", "https://x/2.png"),
        BrowseImage("mafic", "https://x/3.png"),
        BrowseImage("True color", "https://x/2.png"),
        BrowseImage("Grayscale (unprojected)", "https://x/4.png"),
        BrowseImage("Merged RGB", "https://x/5.png"),
        BrowseImage("Infrared false color", "https://x/6.png"),
    ]
    assert [b.kind for b in ordered(images)] == [
        "Infrared false color",
        "Merged RGB",
        "Browse",
        "Mafic",
        "Grayscale (unprojected)",
    ]


# --- STAC ------------------------------------------------------------------------


def _stacItem() -> dict:
    return _fixture("stac_search_ctx.json")["features"][0]


def _stacCatalog(monkeypatch, item: dict) -> tuple[StacCatalog, Product]:
    page = {"features": [item], "links": []}
    monkeypatch.setattr(stac, "fetchJson", lambda *a, **k: page)
    catalog = StacCatalog()
    (product,) = catalog.search(SearchQuery("x#y"), lambda *a: None).products
    return catalog, product


def test_stac_browse_images_are_the_picture_assets(monkeypatch):
    item = _stacItem()
    item["assets"].update(
        {
            "browse": {
                "href": "https://example.test/browse.png",
                "type": "image/png",
                "title": "FALSE COLOR BROWSE",
                "roles": ["overview"],
            },
            "visual": {"href": "https://example.test/visual.webp", "roles": ["visual"]},
            "s3": {
                "href": "s3://bucket/x.png",
                "type": "image/png",
                "roles": ["visual"],
            },
        }
    )
    catalog, product = _stacCatalog(monkeypatch, item)
    images = catalog.browseImages(product)
    # the GeoTIFF overview (the hillshade), the s3 asset and the thumbnail
    # (there are larger pictures) aren't offered
    assert [(b.kind, b.url) for b in images] == [
        ("False color browse", "https://example.test/browse.png"),
        ("Visual", "https://example.test/visual.webp"),
    ]
    assert images[0].description == "FALSE COLOR BROWSE"


def test_stac_offers_the_thumbnail_when_there_is_nothing_larger(monkeypatch):
    catalog, product = _stacCatalog(monkeypatch, _stacItem())
    (image,) = catalog.browseImages(product)
    assert image.kind == "Thumbnail" and image.url == product.thumbnail


# --- footprint files ------------------------------------------------------------


def test_ode_footprint_files_get_their_browse_images_from_ode(monkeypatch):
    monkeypatch.setattr(
        FootprintFileCatalog, "paths", [str(FIXTURES / "ode_mtrdr_footprints.gpkg")]
    )
    catalog = FootprintFileCatalog()
    path = str(FIXTURES / "ode_mtrdr_footprints.gpkg")
    product = catalog.search(SearchQuery(path), lambda *a: None).products[0]
    asked = []
    monkeypatch.setattr(
        OdeCatalog,
        "browseImagesFor",
        lambda self, *args: asked.append(args) or [BrowseImage("X", "https://x")],
    )
    assert [b.kind for b in catalog.browseImages(product)] == ["X"]
    assert asked == [(product.id, "mars", "MRO", "CRISM", "MTRDR")]


def test_other_footprint_files_have_no_browse_images(tmp_path, monkeypatch):
    frame = gpd.GeoDataFrame(
        {"id": ["a"]}, geometry=[shapely.box(10, 10, 11, 11)], crs="EPSG:4326"
    )
    frame.to_file(tmp_path / "f.geojson", driver="GeoJSON")
    monkeypatch.setattr(FootprintFileCatalog, "paths", [str(tmp_path / "f.geojson")])
    catalog = FootprintFileCatalog()
    (product,) = catalog.search(
        SearchQuery(str(tmp_path / "f.geojson")), lambda *a: None
    ).products
    assert catalog.browseImages(product) == []
