"""Overlays across projections: an image of the same body laid over another
image is reprojected for display only, onto that image's grid (spec §4.4.1),
and the results are kept in a bounded cache. No Qt.

Nothing reprojected here is written anywhere: analyses, statistics and
exports use each image as it is."""

from __future__ import annotations

import threading
import weakref
from collections import OrderedDict
from collections.abc import Sequence

import numpy as np
from rasterio.enums import Resampling

from varda.common.entities import VardaRaster
from varda.common.grid import (
    NO_OVERLAP,
    Excluded,
    ImageChoices,
    onGridOf,
    placeableChoices,
    sameGrid,
)
from varda.geo.describe import describeCrs
from varda.geo.equivalence import Compatibility, compatibility
from varda.utilities.proj_lock import PROJ_LOCK

# the largest base image (in pixels) an overlay is reprojected onto for display
DISPLAY_MAX_PIXELS = 100_000_000
# the bytes of reprojected overlays kept for display
DISPLAY_CACHE_BYTES = 512 * 2**20

# a base image's pixel grid: its CRS (authority code or WKT), transform and size
type GridKey = tuple[str, tuple[float, ...], int, int]
# a cached array: (band, the base's grid)
type CacheKey = tuple[int, GridKey]


TOO_LARGE = "too large to reproject for display"


def needsReprojection(image: VardaRaster, base: VardaRaster) -> bool:
    """Whether ``image`` must be reprojected to be shown on ``base``."""
    return not sameGrid(image, base)


def displayChoices(images: Sequence[VardaRaster], base: VardaRaster) -> ImageChoices:
    """The images that can be overlaid on ``base``: on its grid, or on the
    same body and overlapping it (``placeableChoices``), except those that
    would be reprojected onto a base of more than ``DISPLAY_MAX_PIXELS``;
    and why the rest can't."""
    choices = placeableChoices(images, base)
    pixels = base.width * base.height
    if pixels <= DISPLAY_MAX_PIXELS:
        return choices
    reason = (
        f"{TOO_LARGE} ({pixels / 1e6:.0f} Mpx); "
        "reproject it with Processing → Geometry → Reproject Image"
    )
    result = ImageChoices(excluded=list(choices.excluded))
    for image in choices.images:
        if needsReprojection(image, base):
            result.excluded.append(Excluded(image, reason))
        else:
            result.images.append(image)
    return result


def _whyNot(excluded: Excluded, base: VardaRaster, count: int) -> tuple[str, str]:
    """Why an image can't be overlaid on ``base``, in a few words, and what
    to do about it (``count``: how many images share the reason)."""
    image = excluded.image
    it, its = ("them", "their") if count > 1 else ("it", "its")
    if not base.hasGeospatialData:
        return (
            "this image has no map coordinates, so only images of its size "
            f"({base.width} × {base.height}) can be",
            "Resample one to that size (Processing → Geometry)",
        )
    if not image.hasGeospatialData:
        return "no map coordinates", f"Open a map-projected version of {it}"
    if compatibility(image.crs, base.crs) == Compatibility.DIFFERENT_BODY:
        return (
            "of another body",
            f"Check {its} coordinate system in Image Properties",
        )
    if excluded.reason == NO_OVERLAP:
        return "no overlap with this image", "Choose an image of the same area"
    if excluded.reason.startswith(TOO_LARGE):
        return (
            "this image is too large to reproject onto",
            f"Reproject {it} first (Processing → Geometry → Reproject Image)",
        )
    return "can't be placed on this image", "Hover for why"


def notOverlaidText(choices: ImageChoices, base: VardaRaster) -> tuple[str, str]:
    """The note beside the overlay list (how many images can't be overlaid,
    why and what to do) and its tooltip (each image and why); "" for both
    when every image can be."""
    if not choices.excluded:
        return "", ""
    kinds: dict[str, list[Excluded]] = {}
    for excluded in choices.excluded:
        kinds.setdefault(_whyNot(excluded, base, 1)[0], []).append(excluded)
    reasons = [_whyNot(group[0], base, len(group)) for group in kinds.values()]
    count = len(choices.excluded)
    images = "1 image" if count == 1 else f"{count} images"
    if len(reasons) == 1:
        ((why, todo),) = reasons
        note = f"{images} can't be overlaid: {why}. {todo}."
    else:
        counted = ", ".join(
            f"{why} ({len(group)})" for why, group in zip(kinds, kinds.values())
        )
        note = (
            f"{images} can't be overlaid: {counted}. Hover for each image and "
            "what to do."
        )
    advice = "\n".join(f"{why}: {todo}." for why, todo in reasons)
    return note, f"{advice}\n\n{choices.note}"


def reprojectForDisplay(
    overlay: VardaRaster, base: VardaRaster, band: int
) -> np.ndarray:
    """``overlay``'s ``band`` on ``base``'s grid (rows, cols), float32, by
    nearest neighbor: values are never blended, so a class map stays a class
    map. NaN where the overlay has no data. Raises ValueError when it can't
    be placed (see ``grid.placementProblem``)."""
    return onGridOf(overlay, base, Resampling.nearest, [band])[:, :, 0].astype(
        np.float32
    )


def displayNote(overlay: VardaRaster, base: VardaRaster) -> str:
    """How a reprojected overlay is shown, for its tooltip."""
    return (
        f"Reprojected for display from {describeCrs(overlay.crs).text} to "
        f"{describeCrs(base.crs).text}; nearest neighbor; for display only: "
        "analyses, statistics and exports use each image as it is."
    )


def gridKey(base: VardaRaster) -> GridKey:
    """``base``'s pixel grid, so a base with a new ``crs`` or ``transform``
    override is a different key (PROJJSON, which keeps every axis direction,
    when the CRS has no authority code)."""
    with PROJ_LOCK:
        crs = base.crs
        name = describeCrs(crs).authority or ("" if crs is None else crs.to_json())
    return name, tuple(base.transform)[:6], base.width, base.height


class DisplayCache:
    """Reprojected overlays, keyed by (overlay, band, base grid), within a
    byte budget: the least recently used array goes first. Overlays are held
    weakly, and an overlay's entries go when it does."""

    def __init__(self, budget: int = DISPLAY_CACHE_BYTES):
        self.budget = budget
        self._arrays: weakref.WeakKeyDictionary[
            VardaRaster, dict[CacheKey, np.ndarray]
        ] = weakref.WeakKeyDictionary()
        # every entry, least recently used first: (id(overlay), key) -> bytes
        self._order: OrderedDict[tuple[int, CacheKey], int] = OrderedDict()
        self._overlays: dict[int, weakref.ref[VardaRaster]] = {}
        self._finalizers: dict[int, weakref.finalize] = {}
        self._lock = threading.RLock()  # a dead overlay is purged by the GC
        self.nbytes = 0

    def __len__(self) -> int:
        return len(self._order)

    def get(
        self, overlay: VardaRaster, base: VardaRaster, band: int
    ) -> np.ndarray | None:
        key = (band, gridKey(base))
        with self._lock:
            array = self._arrays.get(overlay, {}).get(key)
            if array is not None:
                self._order.move_to_end((id(overlay), key))
            return array

    def put(
        self, overlay: VardaRaster, base: VardaRaster, band: int, array: np.ndarray
    ) -> None:
        """Keep ``array``; one larger than the whole budget isn't kept."""
        key = (band, gridKey(base))
        with self._lock:
            self._remove(id(overlay), key)
            if array.nbytes > self.budget:
                return
            if overlay not in self._arrays:
                self._arrays[overlay] = {}
                self._overlays[id(overlay)] = weakref.ref(overlay)
                self._finalizers[id(overlay)] = weakref.finalize(
                    overlay, self._purge, id(overlay)
                )
            self._arrays[overlay][key] = array
            self._order[(id(overlay), key)] = array.nbytes
            self.nbytes += array.nbytes
            while self.nbytes > self.budget:
                oldest, oldestKey = next(iter(self._order))
                self._remove(oldest, oldestKey)

    def forget(self, overlay: VardaRaster) -> None:
        """Drop ``overlay``'s entries (its data or metadata changed)."""
        with self._lock:
            finalizer = self._finalizers.get(id(overlay))
            if finalizer is not None:
                finalizer.detach()
            self._purge(id(overlay))

    def _purge(self, overlayId: int) -> None:
        with self._lock:
            for owner, key in [k for k in self._order if k[0] == overlayId]:
                self._remove(owner, key)
            self._overlays.pop(overlayId, None)
            self._finalizers.pop(overlayId, None)

    def _remove(self, overlayId: int, key: CacheKey) -> None:
        nbytes = self._order.pop((overlayId, key), None)
        if nbytes is None:
            return
        self.nbytes -= nbytes
        overlay = self._overlays[overlayId]()
        if overlay is None:  # dying: the weak dictionary drops it
            return
        arrays = self._arrays.get(overlay, {})
        arrays.pop(key, None)
        if not arrays:
            self._arrays.pop(overlay, None)
            self._overlays.pop(overlayId, None)
            finalizer = self._finalizers.pop(overlayId, None)
            if finalizer is not None:
                finalizer.detach()
