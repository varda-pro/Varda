"""Linked images: where a point or a line on one image falls on the others,
either the same pixel (products of one pipeline on one grid) or the same
place on the map (images on different grids or map projections), and what
each image holds there. No Qt.

An image without georeferencing can only be linked by pixel, so map links
fall back to the pixel for it; and linking by pixel pairs different places
on images that aren't on one grid. ``linkWarnings`` says when either
happens, for the workspace to show. Images of different bodies are never
linked by map location: their points are NaN.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt
from attrs import frozen

from varda.common.entities import VardaRaster
from varda.common.grid import sameGrid
from varda.geo.equivalence import bodyLabel, canTransform
from varda.geo.transform import transformPoints
from varda.common.parameter import LabeledEnum


class Link(LabeledEnum):
    PIXEL = "By pixel"
    MAP = "By map location"


def linkedPoints(
    images: Sequence[VardaRaster],
    source: int,
    points: npt.ArrayLike,
    link: Link,
) -> list[np.ndarray]:
    """``points`` (n, 2; image coordinates, x = column) on ``images[source]``
    in each image's own coordinates; NaN on an image of another body (or
    one whose body can't be told), which has no such place."""
    xy = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    origin = images[source]
    if link is Link.PIXEL or not origin.hasGeospatialData:
        return [xy.copy() for _ in images]
    mapped = origin.imageToGeo(xy)
    out = []
    for image in images:
        if not image.hasGeospatialData:
            out.append(xy.copy())
        elif canTransform(origin.crs, image.crs) is not None:
            # another body: no place there is this one
            out.append(np.full_like(xy, np.nan))
        else:
            there = np.column_stack(
                transformPoints(origin.crs, image.crs, mapped[:, 0], mapped[:, 1])
            )
            out.append(image.geoToImage(there))
    return out


def _names(images: Sequence[VardaRaster]) -> str:
    return ", ".join(image.name for image in images)


def linkWarnings(images: Sequence[VardaRaster], link: Link) -> list[str]:
    """What isn't linked the way ``link`` says: images without
    georeferencing (linked by pixel, even by map location), images on
    another body than the first georeferenced image (not linked by map
    location), or images not on the first image's grid (linked by pixel,
    so the same pixel is a different place)."""
    if link is Link.MAP:
        return _plainWarnings(images) + _bodyWarnings(images)
    first = images[0]
    other = [image for image in images[1:] if not sameGrid(image, first)]
    if not other:
        return []
    return [
        f"Not on {first.name}'s grid: {_names(other)} (size or georeferencing "
        "differ). Linked by pixel, the same pixel there is a different place; "
        "link by map location instead."
    ]


def _plainWarnings(images: Sequence[VardaRaster]) -> list[str]:
    plain = [image for image in images if not image.hasGeospatialData]
    if not plain:
        return []
    if len(plain) == len(images):
        return ["No image has georeferencing, so they're linked by pixel."]
    return [
        f"Linked by pixel, not map location: {_names(plain)} "
        "(no georeferencing). Placing the cursor on one of them links "
        "every image by pixel."
    ]


def _bodyWarnings(images: Sequence[VardaRaster]) -> list[str]:
    """Georeferenced images that can't be put on the first georeferenced
    image's body (another body, or one Varda can't tell), by body."""
    mapped = [image for image in images if image.hasGeospatialData]
    if not mapped:
        return []
    origin = mapped[0]
    byBody: dict[str, list[VardaRaster]] = {}
    for image in mapped[1:]:
        if canTransform(origin.crs, image.crs) is not None:
            byBody.setdefault(bodyLabel(image.crs), []).append(image)
    originBody = bodyLabel(origin.crs)
    return [
        f"{_names(others)}: on {body}, not {originBody}; not linked"
        for body, others in byBody.items()
    ]


@frozen
class LinkedSpectrum:
    axis: np.ndarray  # nm, or band numbers when the bands aren't wavelengths
    displayAxis: np.ndarray  # in the image's own unit, for plotting
    values: np.ndarray  # NaN for no data and bad bands
    axisLabel: str
    axisUnit: str = "band"  # displayAxis's unit (e.g. nm, µm), or "band" numbers


def spectrumAt(image: VardaRaster, x: float, y: float) -> LinkedSpectrum | None:
    """The spectrum of the pixel containing image point (``x``, ``y``), or
    None off the image."""
    if not (np.isfinite(x) and np.isfinite(y)):
        return None  # not linked (another body)
    col, row = int(np.floor(x)), int(np.floor(y))
    if not (0 <= col < image.width and 0 <= row < image.height):
        return None
    values = np.array(image.getSpectrum(col, row).values, dtype=np.float64)
    model = image.bandModel
    if model.noData is not None:
        values[values == model.noData] = np.nan
    if model.badBands.size == values.size:
        values[model.badBands] = np.nan
    if model.centers is None:
        bands = np.arange(1, values.size + 1, dtype=np.float64)
        return LinkedSpectrum(bands, bands, values, "Band")
    return LinkedSpectrum(
        model.centers,
        model.native(model.centers),
        values,
        f"Wavelength ({model.label})",
        model.label,
    )


def _asUnsigned(values: np.ndarray, dtype: np.dtype | None) -> np.ndarray:
    """Whole-number ``values`` as the bits the image stores, in uint64: a
    signed value is read as the unsigned integer of the same width (int16 -1
    is sixteen bits set, as Bit Mask Decode reads it), not sign-extended.
    Without an integer ``dtype`` they are read as int64."""
    stored = np.dtype(np.int64) if dtype is None or dtype.kind not in "iu" else dtype
    native = stored.newbyteorder("=")
    return values.astype(native).view(f"u{native.itemsize}").astype(np.uint64)


def bitPlanes(
    values: np.ndarray, width: int, dtype: np.dtype | None = None
) -> np.ndarray:
    """A spectrum of bit masks as (``width`` bits, bands): 1 where a bit is
    set, 0 where not, NaN for missing values (no data, bad bands). ``dtype``:
    the image's stored type, so negative signed values show their own bits."""
    values = np.asarray(values, dtype=np.float64)
    missing = ~np.isfinite(values)
    masks = _asUnsigned(np.where(missing, 0, values), dtype)
    planes = (
        (masks[None, :] >> np.arange(width, dtype=np.uint64)[:, None]) & 1
    ).astype(np.float64)
    planes[:, missing] = np.nan
    return planes


def highestBit(values: np.ndarray, dtype: np.dtype | None = None) -> int:
    """How many bits the finite values need (at least 1); a negative value
    of a signed ``dtype`` needs all of them."""
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if not finite.size:
        return 1
    largest = int(_asUnsigned(finite, dtype).max())
    return max(largest.bit_length(), 1)
