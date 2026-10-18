"""Ratio boxes live in I/F cube pixels; a browse view on another grid or map
projection reaches them through map coordinates. No Qt."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from varda.common.entities import VardaRaster
from varda.workspaces.linked_images.linking import Link, linkedPoints


def viewToData(
    view: VardaRaster, data: VardaRaster, link: Link, xy: npt.ArrayLike
) -> np.ndarray | None:
    """``xy`` (n, 2; x = column) on ``view`` as ``data`` pixel coordinates, or
    None when any point falls outside ``data``."""
    out = linkedPoints([view, data], 0, xy, link)[1]
    inside = (
        np.isfinite(out).all(axis=1)
        & (out[:, 0] >= 0)
        & (out[:, 0] < data.width)
        & (out[:, 1] >= 0)
        & (out[:, 1] < data.height)
    )
    return out if inside.all() else None


def dataToView(
    view: VardaRaster, data: VardaRaster, link: Link, polygon: np.ndarray
) -> np.ndarray:
    """``polygon`` (n, 2) in ``data`` pixels as ``view`` pixel coordinates."""
    return linkedPoints([data, view], 0, polygon, link)[1]
