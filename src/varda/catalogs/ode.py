"""PDS Orbital Data Explorer (ODE): products of many missions to Mars, the
Moon, Mercury and Venus, searched through ODE's REST interface (manual:
https://oderest.rsl.wustl.edu/ODE_REST_V2.1.pdf).

Collections are ODE's instrument host / instrument / product type sets with
footprints. ODE writes longitudes 0-360; its "C0" footprints are already
-180 to 180 and split at the antimeridian, as Varda wants.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import urlencode

import shapely
from shapely import wkt

from varda.catalogs.catalog import (
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
FILE_ROLES = {"Browse": "browse", "Derived": "auxiliary", "Referenced": "auxiliary"}
LABELS = (".lbl", ".xml")
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


def _imageUrl(query: str, target: str, pdsid: str, **extra: str) -> str:
    """ODE's thumbnail or browse image of a product."""
    return f"{ODE_URL}?{urlencode({'query': query, 'target': target, 'pdsid': pdsid, **extra})}"


class OdeCatalog(Catalog):
    catalogId = "ode"
    name = "PDS Orbital Data Explorer (ODE)"
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
                            f"{s['IHID']} {s['IID']} {s['PTName']}",
                            body,
                            ", ".join(
                                n
                                for n in (
                                    MISSIONS.get(s["IHID"], ""),
                                    s.get("IName", ""),
                                )
                                if n
                            ),
                            f"{s['IHID']} {s['IID']}",
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

    def filesFor(
        self, pdsid: str, target: str, ihid: str, iid: str, pt: str
    ) -> list[RemoteFile]:
        """A product's files by its PDS ID and IIPT (footprint files use this)."""
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
        files = []
        for f in _items(products[0], "Product_files", "Product_file"):
            name = f.get("FileName") or PurePosixPath(f.get("URL", "")).name
            if not f.get("URL", "").startswith("http"):
                continue
            kind = f.get("Type", "Product")
            own = name.lower().startswith(pdsid.lower())  # not a sibling product's
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
                )
            )
        return files
