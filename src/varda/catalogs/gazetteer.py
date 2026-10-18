"""Named features from the IAU Gazetteer of Planetary Nomenclature (WGPSN,
USGS), for orientation on the find-data map: which names to label for a view
(``selectLabels``), finding one by name (``search``) and the view that shows
it (``featureView``).

Varda bundles one table per body, ``resources/basemaps/names/<body>.csv.gz``,
made by ``scripts/build_basemaps.py`` from USGS's nomenclature center-point
shapefiles (retrieval date in the tables' README). Only names adopted by the
IAU are kept. Longitudes are converted to the map's convention, -180° to 180°
east positive (WGPSN publishes 0–360° east for these bodies), latitudes are
planetocentric as published. No Qt.
"""

from __future__ import annotations

import csv
import functools
import gzip
import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from varda.catalogs.bodies import RADII_M, bodyName, normalizeLongitude
from varda.utilities.resources import resource_path

Bounds = tuple[float, float, float, float]  # west, south, east, north

NAMES_FOLDER = "resources/basemaps/names"
GAZETTEER_CREDIT = (
    "Names: Gazetteer of Planetary Nomenclature, IAU Working Group for "
    "Planetary System Nomenclature (WGPSN) and USGS Astrogeology"
)
# A label shows once its feature spans this many screen pixels
MIN_FEATURE_PX = 28.0
MAX_LABELS = 60
# Go to: the view is this many times the feature's diameter across
GO_TO_FACTOR = 2.0


@dataclass(frozen=True)
class Gazetteer:
    """One body's named features, as parallel arrays."""

    body: str
    names: tuple[str, ...]
    latitude: np.ndarray  # degrees, planetocentric
    longitude: np.ndarray  # degrees east, -180 to 180
    diameter: np.ndarray  # km; 0 where WGPSN gives none
    kinds: tuple[str, ...]  # WGPSN's descriptor term, e.g. "Crater, craters"
    approval: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.names)

    @property
    def radiusKm(self) -> float:
        return RADII_M[self.body] / 1000.0

    @property
    def kmPerDegree(self) -> float:
        return 2 * math.pi * self.radiusKm / 360.0

    def index(self, name: str) -> int:
        """The feature called exactly ``name`` (any case)."""
        folded = fold(name)
        return next(i for i, n in enumerate(self.folded) if n == folded)

    @functools.cached_property
    def folded(self) -> tuple[str, ...]:
        """The names as ``fold`` makes them, for matching."""
        return tuple(fold(n) for n in self.names)

    def displayDiameter(self) -> np.ndarray:
        """Diameters for sizing labels: a feature WGPSN gives no size (an
        eruptive center, say) counts as 1/400 of the body's circumference."""
        unknown = 2 * math.pi * self.radiusKm / 400.0
        return np.where(self.diameter > 0, self.diameter, unknown)


def fold(text: str) -> str:
    """Lower case without accents, for matching names as typed."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def eastLongitude(longitude: float, positiveWest: bool = False) -> float:
    """A WGPSN longitude (0–360°, east or west positive) as the map's: -180°
    to 180°, east positive."""
    return normalizeLongitude(-longitude if positiveWest else longitude)


def namesPath(body: str) -> Path:
    return Path(resource_path(f"{NAMES_FOLDER}/{bodyName(body).lower()}.csv.gz"))


@functools.cache
def loadGazetteer(body: str) -> Gazetteer | None:
    """The body's bundled names (None when Varda has none, as for Earth)."""
    body = bodyName(body) if body else ""
    path = namesPath(body) if body else None
    if path is None or not path.exists() or body not in RADII_M:
        return None
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(row for row in f if not row.startswith("#")))
    names = tuple(r["name"] for r in rows)
    return Gazetteer(
        body,
        names,
        np.array([float(r["lat"]) for r in rows]),
        np.array([float(r["lon"]) for r in rows]),
        np.array([float(r["diameter_km"] or 0) for r in rows]),
        tuple(r["type"] for r in rows),
        tuple(r["approval"] for r in rows),
    )


def selectLabels(
    gazetteer: Gazetteer,
    x: np.ndarray,
    y: np.ndarray,
    view: Bounds,
    pixelsPerUnit: float,
    kmPerUnit: float,
    charPx: float = 7.0,
    linePx: float = 16.0,
    minFeaturePx: float = MIN_FEATURE_PX,
    maxLabels: int = MAX_LABELS,
) -> list[int]:
    """The features to label in a view, largest first, none overlapping.

    ``x`` and ``y`` are the features' positions in map coordinates (NaN for
    one off the map), ``view`` the map's view in those coordinates,
    ``pixelsPerUnit`` the screen's scale and ``kmPerUnit`` the map's (km per
    degree for equirectangular, 0.001 for meters). A feature is a candidate
    once its diameter spans ``minFeaturePx`` on screen, so large features
    show zoomed out and smaller ones appear as you zoom in. Candidates are
    placed largest first; one whose label box (about ``charPx`` per
    character by ``linePx``, centered on the feature) overlaps a placed
    label is dropped."""
    x0, y0, x1, y1 = view
    sizePx = gazetteer.displayDiameter() / kmPerUnit * pixelsPerUnit
    inside = (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)  # NaN is never inside
    candidates = np.flatnonzero(inside & (sizePx >= minFeaturePx))
    candidates = candidates[np.argsort(-sizePx[candidates], kind="stable")]
    placed: list[tuple[float, float, float, float]] = []
    chosen: list[int] = []
    for i in candidates:
        px, py = (x[i] - x0) * pixelsPerUnit, (y[i] - y0) * pixelsPerUnit
        halfWidth = len(gazetteer.names[i]) * charPx / 2 + 2
        halfHeight = linePx / 2
        box = (px - halfWidth, py - halfHeight, px + halfWidth, py + halfHeight)
        if any(
            box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3]
            for b in placed
        ):
            continue
        placed.append(box)
        chosen.append(int(i))
        if len(chosen) >= maxLabels:
            break
    return chosen


def search(gazetteer: Gazetteer, text: str, limit: int = 20) -> list[int]:
    """Features whose names match ``text`` (any case, accents ignored): an
    exact match first, then names starting with it, then names containing
    it, each group largest first."""
    query = fold(text.strip())
    if not query:
        return []
    names = gazetteer.folded
    size = gazetteer.diameter
    exact = [i for i, n in enumerate(names) if n == query]
    prefix = [i for i, n in enumerate(names) if n.startswith(query) and n != query]
    contains = [
        i for i, n in enumerate(names) if query in n and not n.startswith(query)
    ]
    found: list[int] = []
    for group in (exact, prefix, contains):
        found += sorted(group, key=lambda i: -size[i])
    return found[:limit]


def featureView(
    gazetteer: Gazetteer, index: int, factor: float = GO_TO_FACTOR
) -> Bounds:
    """Longitude-latitude bounds about ``factor`` times the feature's
    diameter across, centered on it (wider in longitude away from the
    equator, so the feature keeps its shape on an equirectangular map)."""
    diameter = float(gazetteer.displayDiameter()[index])
    halfLat = factor * diameter / 2 / gazetteer.kmPerDegree
    latitude = float(gazetteer.latitude[index])
    longitude = float(gazetteer.longitude[index])
    halfLon = halfLat / max(math.cos(math.radians(latitude)), 0.05)
    return (
        max(longitude - halfLon, -180.0),
        max(latitude - halfLat, -90.0),
        min(longitude + halfLon, 180.0),
        min(latitude + halfLat, 90.0),
    )
