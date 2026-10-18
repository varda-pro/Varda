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


def thumbnailRgba(image: VardaRaster, size: int = THUMBNAIL_SIZE) -> np.ndarray:
    """The image as a new view first shows it (its default bands and stretch),
    at most ``size`` pixels on its longer side, as (rows, cols, 4) uint8 RGBA.

    Reads one row in every ``sampleStep`` rows, and keeps every
    ``sampleStep``-th column of it: the stretch's statistics come from that
    sample, not the whole image."""
    step = sampleStep(image, size)
    defaults = [int(b) for b in image.defaultBands[:3]]
    bands = list(dict.fromkeys(defaults))  # each band read once
    rows = [
        np.ma.filled(
            np.ma.asarray(
                image.getData(bands, window=(row, 0, 1, image.width))[:, ::step]
            ).astype(np.float32),
            np.nan,
        )
        for row in range(0, image.height, step)
    ]
    sample = VardaRaster(
        ArrayDataSource(
            np.concatenate(rows, axis=0),
            defaultBands=np.array([bands.index(b) for b in defaults]),
        )
    )
    return ImageRenderer(sample).render()
