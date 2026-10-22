"""Small previews of images for the image list, read from a sample of the
image's pixels, never the whole image."""

from __future__ import annotations

import math

import numpy as np

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering.image_renderer import ImageRenderer

THUMBNAIL_SIZE = 64  # pixels on the longer side


def sampleStep(image: VardaRaster, size: int = THUMBNAIL_SIZE) -> int:
    """Every how many rows and columns a thumbnail takes a pixel."""
    return max(1, math.ceil(max(image.height, image.width) / size))


def _floatRow(image: VardaRaster, bands: list[int], row: int, step: int) -> np.ndarray:
    """One sampled row as floats, masked samples NaN. Float64 data stays float64:
    in float32 a value on a rounding edge lands a level away from the view's."""
    data = np.ma.asarray(image.getData(bands, window=(row, 0, 1, image.width)))
    floats = data[:, ::step].astype(np.promote_types(data.dtype, np.float32))
    return np.ma.filled(floats, np.nan)


def thumbnailRgba(image: VardaRaster, size: int = THUMBNAIL_SIZE) -> np.ndarray:
    """The image as a new view first shows it (its default bands and stretch),
    at most ``size`` pixels on its longer side, as (rows, cols, 4) uint8 RGBA.

    Reads one row in every ``sampleStep`` rows, and keeps every
    ``sampleStep``-th column of it: the stretch's statistics come from that
    sample, not the whole image."""
    step = sampleStep(image, size)
    defaults = [int(b) for b in image.defaultBands[:3]]
    bands = list(dict.fromkeys(defaults))  # each band read once
    rows = [_floatRow(image, bands, row, step) for row in range(0, image.height, step)]
    sample = VardaRaster(
        ArrayDataSource(
            np.concatenate(rows, axis=0),
            defaultBands=np.array([bands.index(b) for b in defaults]),
        )
    )
    return ImageRenderer(sample).render()
