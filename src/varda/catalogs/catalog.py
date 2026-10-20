"""The catalog interface: how Varda finds and fetches data without knowing any
mission. A catalog (STAC, PDS ODE, a footprint file, or one a plugin adds)
lists collections, searches them by area, time and its own filters, and
lists each product's files. The core does the rest: the map, the results,
the downloads.

Catalogs are registered by plugins' ``registerCatalogs`` hook and exported
from ``varda.api``. See docs/superpowers/specs/2026-10-15-find-and-fetch-design.md.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import ClassVar

import shapely
from attrs import field, frozen

from varda.common.parameter import ParameterGroup

# (fraction 0-1, message)
CatalogProgress = Callable[[float, str], None]
ROLES = ("data", "label", "browse", "auxiliary")


class CatalogError(RuntimeError):
    """A catalog couldn't be searched or a file fetched; the message is for
    the user."""


@frozen
class Collection:
    """A dataset within a catalog (CRISM MTRDR, a STAC collection, …)."""

    id: str
    title: str
    body: str  # "Mars", "Moon", "Europa", "Earth", … (picks the map)
    description: str = ""
    # What the collection picker groups it under within its body, e.g. the
    # mission and instrument ("MRO CRISM"); "" lists it on its own
    group: str = ""


@frozen
class SearchQuery:
    collection: str  # a Collection's id
    bbox: tuple[float, float, float, float] | None = None  # west, south, east, north
    start: datetime | None = None
    end: datetime | None = None
    limit: int = 100
    page: object | None = None  # a SearchPage's nextPage, to continue


@frozen(eq=False)
class Product:
    id: str
    title: str
    collection: str
    footprint: shapely.Geometry  # longitude, latitude (degrees, -180 to 180 east)
    start: datetime | None = None
    end: datetime | None = None
    properties: dict = field(factory=dict)  # flat, displayable values
    thumbnail: str = ""  # a URL
    # a URL: a larger browse image (PNG, JPEG or GIF) for the Preview panel
    # and the map; the thumbnail when the catalog has none larger
    preview: str = ""


@frozen
class SearchPage:
    products: tuple[Product, ...]
    nextPage: object | None = None  # opaque: a token, an offset or a URL
    total: int | None = None


@frozen
class RemoteFile:
    name: str
    url: str
    size: int | None = None  # bytes
    checksum: str = ""  # "md5:…" or "sha256:…"
    role: str = "data"  # one of ROLES
    essential: bool = True  # selected by default
    exactSize: bool = True  # False when ``size`` is the archive's rounded figure


def pageOffset(page: object) -> int:
    """A SearchPage.nextPage that is a row offset (the first page's is None)."""
    return page if isinstance(page, int) else 0


class Catalog(ParameterGroup):
    """A source of data. Subclasses declare their filters as class-level
    Parameters (the core builds the search form from them) and implement
    ``collections``, ``search`` and ``files``. ``search`` and ``files`` run
    in the background and may block on the network."""

    catalogId: ClassVar[str] = "catalog"  # stable, snake_case
    name: ClassVar[str] = "Catalog"
    description: ClassVar[str] = ""
    # Login fields the catalog needs (e.g. ("username", "password")); the core
    # asks for them once, keeps them in the keychain and sets ``credentials``
    credentialFields: ClassVar[tuple[str, ...]] = ()
    # "<name> <version>" of the plugin that added it ("" for Varda's own)
    plugin: ClassVar[str] = ""

    def __init__(self) -> None:
        super().__init__()
        self.credentials: dict[str, str] = {}

    def collections(self) -> list[Collection]:
        raise NotImplementedError

    def search(self, query: SearchQuery, progress: CatalogProgress) -> SearchPage:
        raise NotImplementedError

    def files(self, product: Product) -> list[RemoteFile]:
        raise NotImplementedError

    def requestHeaders(self) -> dict[str, str]:
        """Headers downloads from this catalog need (an access token, say)."""
        return {}
