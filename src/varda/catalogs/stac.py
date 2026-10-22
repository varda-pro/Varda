"""STAC (SpatioTemporal Asset Catalog) APIs: the open standard most Earth
imagery and a growing share of planetary data use. One client serves any
STAC API; the list of APIs is a setting, so adding one needs no code.

Bodies come from each collection's ``ssys:targets`` (the Solar System
extension); collections without one are Earth's.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import ClassVar
from urllib.parse import urlparse

import shapely
from attrs import frozen

from varda.catalogs.catalog import (
    Catalog,
    CatalogProgress,
    Collection,
    Product,
    RemoteFile,
    SearchPage,
    SearchQuery,
)
from varda.catalogs.http import fetchJson
from varda.common.parameter import FloatParameter
from varda.geo.bodies import bodyNamed

BROWSE_ROLES = {"thumbnail", "overview", "visual"}
PICTURE_TYPES = {"image/png", "image/jpeg", "image/gif"}
MAX_PROPERTY_LENGTH = 80  # longer text (WKT, JSON) isn't shown in the table


@frozen
class StacApi:
    name: str
    url: str  # the API's root (where /collections and /search are)


DEFAULT_APIS = (
    StacApi("USGS Astrogeology", "https://stac.astrogeology.usgs.gov/api"),
    StacApi("Earth Search", "https://earth-search.aws.element84.com/v1"),
)


def _time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _checksum(multihash: str) -> str:
    """STAC's file:checksum (a hex multihash) as Varda writes checksums."""
    codes = {"1220": "sha256", "d510": "md5"}
    kind = codes.get(multihash[:4].lower())
    return f"{kind}:{multihash[4:].lower()}" if kind else ""


def _nextLink(page: object) -> dict[str, object] | None:
    """A search page's "next" link (as SearchPage.nextPage carries it)."""
    return page if isinstance(page, dict) else None


def _body(collection: dict) -> str:
    targets = collection.get("summaries", {}).get("ssys:targets") or collection.get(
        "ssys:targets"
    )
    if isinstance(targets, list) and targets:
        target = str(targets[0]).strip()
        body = bodyNamed(target)
        return body.name if body is not None else target.title()
    return "Earth"  # STAC without solar-system targets is Earth's


class StacCatalog(Catalog):
    catalogId = "stac"
    name = "STAC catalogs"
    shortName = "STAC"
    description = (
        "Any STAC API: USGS Astrogeology (Mars, the Moon, Europa, …) and Earth "
        "Search (Sentinel-2, Landsat, …) by default; add others with the STAC APIs… button."
    )
    # The APIs searched; Varda's settings replace this with the user's list
    apis: ClassVar[tuple[StacApi, ...]] = DEFAULT_APIS
    maxCloudCover = FloatParameter(
        "Max. cloud cover",
        100.0,
        range=(0.0, 100.0),
        units="%",
        showSlider=False,
        description="For collections that report it (eo:cloud_cover)",
    )

    def __init__(self) -> None:
        super().__init__()
        self._collections: list[Collection] | None = None
        self._items: dict[str, dict] = {}  # product id -> its STAC item

    def collections(self) -> list[Collection]:
        if self._collections is None:
            found = []
            for api in self.apis:
                url, seen = f"{api.url.rstrip('/')}/collections", set()
                while url and url not in seen:  # follow "next" pages
                    seen.add(url)
                    reply = fetchJson(url)
                    for c in reply.get("collections", []):
                        found.append(
                            Collection(
                                f"{api.url}#{c['id']}",
                                f"{c.get('title') or c['id']} ({api.name})",
                                _body(c),
                                c.get("description", ""),
                            )
                        )
                    url = next(
                        (
                            link["href"]
                            for link in reply.get("links", [])
                            if link.get("rel") == "next"
                        ),
                        "",
                    )
            self._collections = found
        return self._collections

    def search(self, query: SearchQuery, progress: CatalogProgress) -> SearchPage:
        root, _, collection = query.collection.partition("#")
        link = _nextLink(query.page)
        if link is not None:
            if str(link.get("method", "GET")).upper() == "POST":
                reply = fetchJson(str(link["href"]), body=link.get("body"))
            else:
                reply = fetchJson(str(link["href"]))
        else:
            body: dict = {"collections": [collection], "limit": query.limit}
            if query.bbox is not None:
                body["bbox"] = list(query.bbox)
            if query.start is not None or query.end is not None:
                start = query.start.isoformat() if query.start else ".."
                end = query.end.isoformat() if query.end else ".."
                body["datetime"] = f"{start}/{end}"
            progress(0.1, "searching")
            reply = fetchJson(f"{root.rstrip('/')}/search", body=body)
        products = []
        for item in reply.get("features", []):
            cloud = item.get("properties", {}).get("eo:cloud_cover")
            if cloud is not None and cloud > self.maxCloudCover.get():
                continue
            self._items[item["id"]] = item
            products.append(self._product(item, query.collection))
        nextLink = next(
            (link for link in reply.get("links", []) if link.get("rel") == "next"), None
        )
        total = reply.get("numberMatched") or reply.get("context", {}).get("matched")
        return SearchPage(tuple(products), nextLink, total)

    def _product(self, item: dict, collection: str) -> Product:
        properties = item.get("properties", {})
        shown = {
            key: value
            for key, value in properties.items()
            if isinstance(value, (int, float, bool, str))
            and not (isinstance(value, str) and len(value) > MAX_PROPERTY_LENGTH)
            and key not in ("datetime", "start_datetime", "end_datetime")
        }
        assets = [
            a
            for a in item.get("assets", {}).values()
            if a.get("href", "").startswith("http")
        ]
        thumbnail = next(
            (a["href"] for a in assets if "thumbnail" in a.get("roles", [])), ""
        )
        # an overview or visual asset is larger than the thumbnail, when it is
        # a picture (not a GeoTIFF, which the preview can't show)
        preview = next(
            (
                a["href"]
                for a in assets
                if {"overview", "visual"} & set(a.get("roles", []))
                and a.get("type", "").split(";")[0].strip() in PICTURE_TYPES
            ),
            thumbnail,
        )
        geometry = item.get("geometry")
        return Product(
            item["id"],
            properties.get("title") or item["id"],
            collection,
            shapely.geometry.shape(geometry) if geometry else shapely.Point(),
            _time(properties.get("start_datetime") or properties.get("datetime")),
            _time(properties.get("end_datetime")),
            shown,
            thumbnail,
            preview,
        )

    def files(self, product: Product) -> list[RemoteFile]:
        item = self._items.get(product.id)
        if item is None:
            root, _, collection = product.collection.partition("#")
            item = fetchJson(
                f"{root.rstrip('/')}/collections/{collection}/items/{product.id}"
            )
        files, names = [], set()
        for key, asset in item.get("assets", {}).items():
            href = asset.get("href", "")
            if not href.startswith("http"):
                continue  # s3:// and relative assets need other tools
            name = PurePosixPath(urlparse(href).path).name or key
            if name in names:
                name = f"{key}_{name}"
            names.add(name)
            roles = set(asset.get("roles", []))
            if "data" in roles:  # data first: some data assets are also overviews
                role = "data"
            elif roles & BROWSE_ROLES:
                role = "browse"
            elif "metadata" in roles:
                role = "auxiliary"
            else:
                role = "data"
            essential = "data" in roles or (
                not roles and "image/tiff" in asset.get("type", "")
            )
            files.append(
                RemoteFile(
                    name,
                    href,
                    asset.get("file:size"),
                    _checksum(asset.get("file:checksum", "")),
                    role,
                    essential,
                )
            )
        return files
