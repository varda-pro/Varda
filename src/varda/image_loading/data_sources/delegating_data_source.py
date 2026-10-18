"""DelegatingDataSource: a wrapper that passes metadata and coordinates through
to another DataSource; subclasses decide how pixel data is read."""

from __future__ import annotations

import numpy as np
from affine import Affine
from pyproj import CRS

from .data_source import DataSource


class DelegatingDataSource(DataSource):
    def __init__(self, source: DataSource):
        self._source = source

    @property
    def source(self) -> DataSource:
        return self._source

    def getPixelSpectrum(self, x: int, y: int) -> np.ndarray:
        return self._source.getPixelSpectrum(x, y)

    def readAllBands(self) -> np.ndarray:
        return self._source.readAllBands()

    # --- Metadata properties ---

    @property
    def filePath(self) -> str | None:
        return self._source.filePath

    @property
    def width(self) -> int:
        return self._source.width

    @property
    def height(self) -> int:
        return self._source.height

    @property
    def bandCount(self) -> int:
        return self._source.bandCount

    @property
    def dtype(self) -> np.dtype:
        return self._source.dtype

    @property
    def nodata(self) -> float | None:
        return self._source.nodata

    @property
    def wavelengths(self) -> np.ndarray:
        return self._source.wavelengths

    @property
    def wavelengthsType(self) -> type:
        return self._source.wavelengthsType

    @property
    def wavelengthUnits(self) -> str:
        return self._source.wavelengthUnits

    @property
    def bandNames(self) -> list[str]:
        return self._source.bandNames

    @property
    def transform(self) -> Affine:
        return self._source.transform

    @property
    def crs(self) -> CRS | None:
        return self._source.crs

    @property
    def rawCrsText(self) -> str | None:
        return self._source.rawCrsText

    @property
    def crsError(self) -> str | None:
        return self._source.crsError

    def gdalMetadata(self) -> dict[str, dict[str, str]]:
        return self._source.gdalMetadata()

    @property
    def driver(self) -> str:
        return self._source.driver

    @property
    def defaultBands(self) -> np.ndarray:
        return self._source.defaultBands

    @property
    def isParameterImage(self) -> bool:
        return self._source.isParameterImage

    @property
    def extraMetadata(self) -> dict:
        return self._source.extraMetadata

    @property
    def description(self) -> str:
        return self._source.description

    # --- Coordinate transformation ---

    def pixelToGeo(self, col: int, row: int) -> tuple[float, float]:
        return self._source.pixelToGeo(col, row)

    def geoToPixel(self, x: float, y: float) -> tuple[int, int]:
        return self._source.geoToPixel(x, y)

    # --- Lifecycle ---

    def close(self) -> None:
        self._source.close()
