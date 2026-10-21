"""Ratio boxes live in I/F cube pixels; a browse view on another grid or map
projection reaches them through map coordinates, and sensor coordinates (the
detector sample and line of the cube's geometry) through its backplane. No
Qt."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

import attrs

from varda.common.entities import VardaRaster
from varda.image_loading.crism_geometry import ColumnGeometry
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


@attrs.frozen
class SensorPixel:
    """The image pixel nearest a sensor position, and how far off it is."""

    col: int
    row: int
    sample: float  # the pixel's own detector sample and line
    line: float
    offset: float  # from the position asked for, in detector pixels


def sensorToImage(
    geometry: ColumnGeometry, sample: float, line: float
) -> SensorPixel | None:
    """The image pixel whose detector (sample, line) is nearest (``sample``,
    ``line``), in the geometry's own units (an IN file's IR Sample and IR
    Line, 0-based; an unprojected cube's columns and rows). None when the
    geometry has no lines or no pixel with both."""
    if geometry.ir_line is None:
        return None
    samples, lines = geometry.ir_sample, geometry.ir_line
    with np.errstate(invalid="ignore"):
        distance = np.hypot(samples - sample, lines - line)
    if not np.isfinite(distance).any():
        return None
    row, col = np.unravel_index(int(np.nanargmin(distance)), distance.shape)
    return SensorPixel(
        int(col),
        int(row),
        float(samples[row, col]),
        float(lines[row, col]),
        float(distance[row, col]),
    )


def imageToSensor(
    geometry: ColumnGeometry, col: int, row: int
) -> tuple[float, float] | None:
    """The detector (sample, line) of the image pixel (``col``, ``row``);
    None where the geometry has no lines or no value there."""
    if geometry.ir_line is None:
        return None
    rows, cols = geometry.ir_sample.shape
    if not (0 <= row < rows and 0 <= col < cols):
        return None
    sample, line = geometry.ir_sample[row, col], geometry.ir_line[row, col]
    if not (np.isfinite(sample) and np.isfinite(line)):
        return None
    return float(sample), float(line)
