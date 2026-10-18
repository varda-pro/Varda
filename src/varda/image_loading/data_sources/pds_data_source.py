"""Planetary Data System products, opened through their labels.

A PDS4 product (an ``.xml`` label) or a PDS3 one with a detached ``.lbl``
keeps its pixels in a raw file (``.dat``, ``.img``, ...) with no header of its
own: only the label says how large the image is and what its pixels are, so
the label is the file to open. GDAL reads the label and finds the data.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np
import numpy.typing as npt
import rasterio

from .rasterio_data_source import RasterioDataSource
from .registry import register_data_source

# GDAL drivers that read planetary labels
PDS_DRIVERS = frozenset({"PDS", "PDS4", "ISIS2", "ISIS3", "VICAR"})


def validRange(label: str) -> tuple[float, float]:
    """A PDS4 label's valid_minimum and valid_maximum (±inf where absent).
    GDAL's mask already covers the special constants (missing, saturation,
    ...), but not values outside this range."""
    low, high = -np.inf, np.inf
    for element in ET.fromstring(label.encode()).iter():
        name = element.tag.rpartition("}")[2]
        if name == "valid_minimum" and element.text:
            low = max(low, float(element.text))
        elif name == "valid_maximum" and element.text:
            high = min(high, float(element.text))
    return low, high


@register_data_source("PDS label", (".xml", ".lbl"))
class PdsLabelDataSource(RasterioDataSource):
    """A PDS3 or PDS4 product, read by GDAL through its label. For PDS4,
    values outside the label's valid range are no-data too."""

    def __init__(self, filePath: str):
        super().__init__(filePath)
        label = self._src.tags(ns="xml:PDS4").get("xml:PDS4")
        self._validRange = validRange(label) if label else (-np.inf, np.inf)

    def getData(
        self,
        bandIndices: npt.ArrayLike | None = None,
        window: tuple[int, int, int, int] | None = None,
        masked=True,
    ) -> np.ndarray:
        data = super().getData(bandIndices, window, masked)
        low, high = self._validRange
        if masked and data.dtype.kind == "f" and (low > -np.inf or high < np.inf):
            with np.errstate(invalid="ignore"):
                for band in np.moveaxis(data, -1, 0):  # in place, a band at a time
                    np.putmask(band, (band < low) | (band > high), np.nan)
        return data

    @classmethod
    def canOpen(cls, filePath: str) -> bool:
        """Only labels GDAL reads as planetary images, not any XML file
        (such as GDAL's own ``.aux.xml`` sidecars)."""
        try:
            with rasterio.open(filePath) as src:
                return src.driver in PDS_DRIVERS
        except rasterio.errors.RasterioError:
            return False
