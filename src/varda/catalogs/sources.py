"""Every collection every catalog offers, as one list per body: what Find
Data shows once the user has chosen a body. A collection is known by its
catalog and its id together (two catalogs may use the same id)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from attrs import evolve, frozen

from varda.catalogs.catalog import Collection

# Shown first, in this order; other bodies follow alphabetically
BODY_ORDER = ("Mars", "Moon", "Mercury", "Venus", "Earth")
OTHER_BODY = "Other"


@frozen
class Source:
    """A collection and the catalog that offers it."""

    catalogId: str
    catalogName: str
    collection: Collection

    @property
    def key(self) -> str:
        return sourceKey(self.catalogId, self.collection.id)

    @property
    def body(self) -> str:
        return self.collection.body or OTHER_BODY


def sourceKey(catalogId: str, collectionId: str) -> str:
    return f"{catalogId}:{collectionId}"


def bodyRank(body: str) -> tuple[int, str]:
    return (
        (BODY_ORDER.index(body), "") if body in BODY_ORDER else (len(BODY_ORDER), body)
    )


def bodies(sources: Iterable[Source]) -> list[tuple[str, int]]:
    """Each body with collections, and how many: the common ones first."""
    counts = Counter(s.body for s in sources)
    return sorted(counts.items(), key=lambda item: bodyRank(item[0]))


def sourcesFor(sources: Sequence[Source], body: str) -> list[Source]:
    """The body's collections, by group (mission and instrument) then title.
    A title shared by two collections of the body gets its catalog's name
    (or, within one catalog, the end of its id) so they can be told apart."""
    mine = [s for s in sources if s.body == body]
    counts = Counter((s.collection.group, s.collection.title) for s in mine)
    perCatalog = Counter(
        (s.catalogId, s.collection.group, s.collection.title) for s in mine
    )

    def told(source: Source) -> Source:
        c = source.collection
        if counts[(c.group, c.title)] < 2:
            return source
        if perCatalog[(source.catalogId, c.group, c.title)] > 1:
            suffix = c.id.rsplit("#", 1)[-1].rsplit(":", 1)[-1]
        else:
            suffix = source.catalogName
        return evolve(source, collection=evolve(c, title=f"{c.title} [{suffix}]"))

    distinct = [told(s) for s in mine]
    return sorted(
        distinct, key=lambda s: (s.collection.group.lower(), s.collection.title.lower())
    )
