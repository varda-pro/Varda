"""PDS Orbital Data Explorer (ODE): products of many missions to Mars, the
Moon, Mercury and Venus, searched through ODE's REST interface (manual:
https://oderest.rsl.wustl.edu/ODE_REST_V2.1.pdf).

Collections are ODE's instrument host / instrument / product type sets with
footprints. ODE writes longitudes 0-360; its "C0" footprints are already
-180 to 180 and split at the antimeridian, as Varda wants.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import urlencode

import shapely
from shapely import wkt

from varda.catalogs.browse import ordered, sentenceCase
from varda.catalogs.catalog import (
    BrowseImage,
    Catalog,
    CatalogError,
    CatalogProgress,
    Collection,
    Product,
    RemoteFile,
    SearchPage,
    SearchQuery,
    pageOffset,
)
from varda.catalogs.http import fetchJson
from varda.common.parameter import FloatParameter, StringParameter

ODE_URL = "https://oderest.rsl.wustl.edu/live2/"
# ODE's meta-databases, by the body Varda shows them on
DATABASES = {"Mars": "mars", "Moon": "moon", "Mercury": "mercury", "Venus": "venus"}
# ODE's mission abbreviations (IHID), spelled out so a search for the
# mission's name finds its collections
MISSIONS = {
    "EM16TGO": "ExoMars Trace Gas Orbiter",
    "MEX": "Mars Express",
    "MGS": "Mars Global Surveyor",
    "MRO": "Mars Reconnaissance Orbiter",
    "ODY": "Mars Odyssey",
    "VO": "Viking Orbiter",
    "MESSENGER": "MESSENGER",
    "ARCB-NRAO": "Arecibo and NRAO radar",
    "CE": "Chang'e",
    "CH1-ORB": "Chandrayaan-1",
    "CLEM": "Clementine",
    "GRAIL": "GRAIL",
    "KPLO": "Korea Pathfinder Lunar Orbiter (Danuri)",
    "LO": "Lunar Orbiter",
    "LP": "Lunar Prospector",
    "LRO": "Lunar Reconnaissance Orbiter",
    "SLN": "SELENE (Kaguya)",
    "MGN": "Magellan",
    "VEX": "Venus Express",
}
# What the collection list calls a mission: the name people use, where ODE's
# code isn't it ("SLN" is Kaguya, "CH1-ORB" Chandrayaan-1)
SHORT_MISSIONS = {
    "EM16TGO": "ExoMars TGO",
    "MEX": "Mars Express",
    "ODY": "Mars Odyssey",
    "VO": "Viking Orbiter",
    "ARCB-NRAO": "Arecibo/NRAO",
    "CE": "Chang'e",
    "CH1-ORB": "Chandrayaan-1",
    "CLEM": "Clementine",
    "KPLO": "Danuri",
    "LO": "Lunar Orbiter",
    "LP": "Lunar Prospector",
    "SLN": "Kaguya",
    "MGN": "Magellan",
    "VEX": "Venus Express",
}


def missionName(ihid: str) -> str:
    return SHORT_MISSIONS.get(ihid, ihid)


FILE_ROLES = {"Browse": "browse", "Derived": "auxiliary", "Referenced": "auxiliary"}
LABELS = (".lbl", ".xml")
# Browse images Varda can show (not JPEG 2000 or TIFF, which ODE also lists)
PICTURES = (".png", ".jpg", ".jpeg", ".gif")
# HiRISE's names for thumbnails (.thumb.jpg, a DTM's .th.jpg) and annotated
# browse images (.abrowse.jpg, .ab.jpg), besides ODE's descriptions
THUMBNAIL_NAME = re.compile(r"\.(thumb|th)\.|_small\.", re.IGNORECASE)
ANNOTATED_NAME = re.compile(r"\.(abrowse|ab)\.", re.IGNORECASE)
# The words of a description that say it is a browse image, not what of
BROWSE_WORDS = re.compile(r"\bBROWSE(\s+(IMAGE|QUICKLOOK|BR)\b)?|\bQUICKLOOK\b")
LISTINGS_KEPT = 64  # products whose file lists are kept (one request each)
# ODE metadata shown (and sortable) in the results: (ODE name, Varda label)
PROPERTIES = (
    ("Observation_type", "Observation type"),
    ("Incidence_angle", "Incidence (°)"),
    ("Emission_angle", "Emission (°)"),
    ("Phase_angle", "Phase (°)"),
    ("Solar_longitude", "Solar longitude (°)"),
    ("Map_resolution", "Map resolution (px/°)"),
    ("Comment", "Comment"),
)


def odeLongitudes(west: float, east: float) -> tuple[float, float]:
    """A box's edges as ODE takes them (0-360 east; west > east crosses the
    meridian), from Varda's -180 to 180."""
    if east - west >= 360:
        return 0.0, 360.0
    return round(west % 360, 6), round(east % 360, 6)


def _list(value) -> list:
    """ODE returns one item bare and several as a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _items(reply: dict, *path: str) -> list:
    """The items at ``path`` in an ODE reply. Where nothing matches, ODE puts
    a message in place of the container (``"Products": "No Products
    Found"``), so anything that isn't a dict on the way means no items."""
    value: object = reply
    for key in path:
        if not isinstance(value, dict):
            return []
        value = value.get(key)
    return _list(value)


def _time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _number(value):
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return value


def _description(text: str) -> str:
    """ODE's description of a file without its HTML (a "HELP" link)."""
    text = re.sub(r"<a\b[^>]*>.*?</a>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


def _name(entry: dict) -> str:
    return entry.get("FileName") or PurePosixPath(entry.get("URL", "")).name


def _stem(name: str) -> str:
    """A file's name before its first dot, in lowercase."""
    return name.split(".")[0].lower()


def _siblingStems(entries: list[dict], pdsid: str) -> set[str]:
    """The stems of other products' data files in a product's file list (a
    HiRISE DTM lists its orthoimages, a CRISM MTRDR its summary parameters),
    so their browse images aren't taken for the product's. Empty when no data
    file is named for the product (an LROC CDR's ID isn't its file name)."""
    products = {_name(e) for e in entries if e.get("Type") == "Product"}
    own = {_stem(n) for n in products if n.lower().startswith(pdsid.lower())}
    return {_stem(n) for n in products} - own if own else set()


def browseKind(description: str, name: str) -> str:
    """A browse image's kind, from ODE's description of it: "INFRARED FALSE
    COLOR BROWSE QUICKLOOK" is "Infrared false color", "VERSION 2
    CARBONATES BROWSE QUICKLOOK" "Carbonates (version 2)". The file's name
    when there is no description; "Browse" when it says no more."""
    if not description:
        return name
    text = re.sub(r"\s+FOR\s+.*\bPRODUCT$", "", description.upper())
    text = " ".join(BROWSE_WORDS.sub(" ", text).split())
    kind = sentenceCase(text) if text else "Browse"
    if version := re.fullmatch(r"Version (\d+) (.+)", kind):
        kind = f"{version[2][:1].upper()}{version[2][1:]} (version {version[1]})"
    if ".nomap." in name.lower():  # HiRISE's browse images in sensor geometry
        kind += " (unprojected)"
    return kind


def _ownPrefix(pdsid: str, listing: list[dict]) -> str:
    """How the product's own files' names start: its PDS ID, else the ID's
    last part (an LROC NAC CDR "nac.m1418637109lc" names its files
    "M1418637109LC.*"); "" (every file) when no product file starts with either."""
    products = [
        _name(f).lower() for f in listing if f.get("Type", "Product") == "Product"
    ]
    for prefix in (pdsid.lower(), pdsid.lower().rsplit(".", 1)[-1]):
        if any(name.startswith(prefix) for name in products):
            return prefix
    return ""


def _imageUrl(query: str, target: str, pdsid: str, **extra: str) -> str:
    """ODE's thumbnail or browse image of a product."""
    return f"{ODE_URL}?{urlencode({'query': query, 'target': target, 'pdsid': pdsid, **extra})}"


class OdeCatalog(Catalog):
    catalogId = "ode"
    name = "PDS Orbital Data Explorer (ODE)"
    shortName = "PDS ODE"
    description = (
        "NASA PDS products of many missions to Mars, the Moon, Mercury and "
        "Venus, with footprints and viewing geometry."
    )
    maxIncidence = FloatParameter(
        "Max. incidence angle", 90.0, range=(0.0, 90.0), units="°", showSlider=False
    )
    maxEmission = FloatParameter(
        "Max. emission angle", 90.0, range=(0.0, 90.0), units="°", showSlider=False
    )
    idContains = StringParameter(
        "Product ID contains", "", description="e.g. frt or _if (case doesn't matter)"
    )

    def __init__(self) -> None:
        super().__init__()
        self._collections: list[Collection] | None = None
        # (target, ihid, iid, pt, pdsid) -> ODE's file list, the latest last
        self._listings: OrderedDict[tuple[str, ...], list[dict]] = OrderedDict()
        self._lock = threading.Lock()  # files and browse images run in threads

    def collections(self) -> list[Collection]:
        if self._collections is None:
            found: dict[str, Collection] = {}
            for body, database in DATABASES.items():
                reply = fetchJson(
                    ODE_URL, {"query": "iipt", "odemetadb": database, "output": "JSON"}
                )
                for s in _items(reply, "ODEResults", "IIPTSets", "IIPTSet"):
                    if s.get("ValidFootprints") != "T":
                        continue
                    key = f"{database}:{s['IHID']}:{s['IID']}:{s['PT']}"
                    found.setdefault(
                        key,
                        Collection(
                            key,
                            f"{missionName(s['IHID'])} {s['IID']} {s['PTName']}",
                            body,
                            ", ".join(
                                n
                                for n in (
                                    MISSIONS.get(s["IHID"], ""),
                                    s.get("IName", ""),
                                )
                                if n
                            ),
                            f"{missionName(s['IHID'])} {s['IID']}",
                        ),
                    )
            self._collections = sorted(found.values(), key=lambda c: (c.body, c.title))
        return self._collections

    def _ids(self, collection: str) -> dict[str, str]:
        database, ihid, iid, pt = collection.split(":")
        return {"target": database, "ihid": ihid, "iid": iid, "pt": pt}

    def search(self, query: SearchQuery, progress: CatalogProgress) -> SearchPage:
        offset = pageOffset(query.page)
        params: dict[str, object] = {
            **self._ids(query.collection),
            "query": "product",
            "results": "cpm",
            "output": "JSON",
            "limit": query.limit,
            "offset": offset,
        }
        if query.bbox is not None:
            west, south, east, north = query.bbox
            westernlon, easternlon = odeLongitudes(west, east)
            params.update(
                westernlon=westernlon, easternlon=easternlon, minlat=south, maxlat=north
            )
        if query.start is not None:
            params["minobtime"] = query.start.strftime("%Y-%m-%d")
        if query.end is not None:
            params["maxobtime"] = query.end.strftime("%Y-%m-%d")
        if self.maxIncidence.get() < 90:
            params["maxinangle"] = self.maxIncidence.get()
        if self.maxEmission.get() < 90:
            params["maxemangle"] = self.maxEmission.get()
        if text := self.idContains.get().strip():
            params["pdsid"] = f"*{text}*"
        progress(0.1, "searching ODE")
        reply = fetchJson(ODE_URL, params).get("ODEResults", {})
        if reply.get("Status") not in (None, "Success"):
            raise CatalogError(f"ODE: {reply.get('Error') or reply.get('Status')}")
        total = int(reply.get("Count") or 0)
        products = tuple(
            self._product(p, query.collection)
            for p in _items(reply, "Products", "Product")
            if isinstance(p, dict)
        )
        more = offset + len(products) < total and products
        return SearchPage(products, offset + len(products) if more else None, total)

    def _product(self, p: dict, collection: str) -> Product:
        geometry = p.get("Footprint_C0_geometry") or p.get("Footprint_geometry") or ""
        try:
            footprint = wkt.loads(geometry) if geometry else shapely.Point()
        except shapely.errors.ShapelyError:
            footprint = shapely.Point()
        target = self._ids(collection)["target"]
        thumbnail = _imageUrl("thumbnail", target, p["pdsid"], results="opm")
        # ODE's thumbnails are about 32 px tall; its large browse image (the
        # normal one when there is none larger) is the sharpest
        preview = _imageUrl("lgbrowse", target, p["pdsid"])
        return Product(
            p["pdsid"],
            p.get("Product_title") or p["pdsid"],
            collection,
            footprint,
            _time(p.get("UTC_start_time")),
            _time(p.get("UTC_stop_time")),
            {
                label: _number(p[name])
                for name, label in PROPERTIES
                if p.get(name) not in (None, "")
            },
            thumbnail,
            preview,
        )

    def files(self, product: Product) -> list[RemoteFile]:
        return self.filesFor(product.id, **self._ids(product.collection))

    def browseImages(self, product: Product) -> list[BrowseImage]:
        return self.browseImagesFor(product.id, **self._ids(product.collection))

    def _listing(
        self, pdsid: str, target: str, ihid: str, iid: str, pt: str
    ) -> list[dict]:
        """ODE's list of a product's files (with web URLs), fetched once and
        kept for the latest ``LISTINGS_KEPT`` products."""
        key = (target, ihid, iid, pt, pdsid.lower())
        with self._lock:
            if key in self._listings:
                self._listings.move_to_end(key)
                return self._listings[key]
        reply = fetchJson(
            ODE_URL,
            {
                "query": "product",
                "results": "f",
                "output": "JSON",
                "target": target,
                "pdsid": pdsid,
                "ihid": ihid,
                "iid": iid,
                "pt": pt,
            },
        ).get("ODEResults", {})
        products = _items(reply, "Products", "Product")
        if not products:
            raise CatalogError(f"ODE has no files listed for {pdsid}.")
        entries = [
            f
            for f in _items(products[0], "Product_files", "Product_file")
            if isinstance(f, dict) and f.get("URL", "").startswith("http")
        ]
        with self._lock:
            self._listings[key] = entries
            while len(self._listings) > LISTINGS_KEPT:
                self._listings.popitem(last=False)
        return entries

    def filesFor(
        self, pdsid: str, target: str, ihid: str, iid: str, pt: str
    ) -> list[RemoteFile]:
        """A product's files by its PDS ID and IIPT (footprint files use this)."""
        listing = self._listing(pdsid, target, ihid, iid, pt)
        prefix = _ownPrefix(pdsid, listing)
        files = []
        for f in listing:
            name = _name(f)
            kind = f.get("Type", "Product")
            own = name.lower().startswith(prefix)  # not a sibling product's
            role = FILE_ROLES.get(
                kind, "label" if name.lower().endswith(LABELS) else "data"
            )
            size = int(float(f["KBytes"]) * 1024) if f.get("KBytes") else None
            files.append(
                RemoteFile(
                    name,
                    f["URL"],
                    size,
                    role=role,
                    essential=kind == "Product" and own,
                    exactSize=False,
                    description=_description(f.get("Description", "")),
                )
            )
        return files

    def browseImagesFor(
        self, pdsid: str, target: str, ihid: str, iid: str, pt: str
    ) -> list[BrowseImage]:
        """A product's browse images by its PDS ID and IIPT: the pictures of
        its file list's Browse files, other products' excepted. Annotated
        copies are left out, and thumbnails unless there is nothing larger."""
        entries = self._listing(pdsid, target, ihid, iid, pt)
        siblings = _siblingStems(entries, pdsid)
        images: list[BrowseImage] = []
        thumbnails: list[BrowseImage] = []
        for f in entries:
            name = _name(f)
            if f.get("Type") != "Browse" or not name.lower().endswith(PICTURES):
                continue
            if _stem(name) in siblings:
                continue
            description = _description(f.get("Description", ""))
            words = description.upper().split()
            if "ANNOTATED" in words or ANNOTATED_NAME.search(name):
                continue
            image = BrowseImage(browseKind(description, name), f["URL"], description)
            if "THUMBNAIL" in words or THUMBNAIL_NAME.search(name):
                thumbnails.append(image)
            else:
                images.append(image)
        return ordered(images or thumbnails)
