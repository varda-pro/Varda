"""Saved pixel points: which pixel of which image, and where that is on the
ground, exportable to GIS files."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path

import attrs
import geopandas as gpd
from psygnal import Signal
from pyproj import CRS
from shapely.geometry import Point

from varda.common.entities import Color, VardaRaster

EXPORT_FILE_FILTER = "GeoJSON (*.geojson);;GeoPackage (*.gpkg);;Shapefile (*.shp)"
_DRIVERS = {
    ".geojson": "GeoJSON",
    ".json": "GeoJSON",
    ".gpkg": "GPKG",
    ".shp": "ESRI Shapefile",
}


@attrs.frozen
class VardaPoint:
    fid: int
    name: str
    color: Color
    image: str  # name of the image the pixel was read from
    x: int
    y: int
    geometry: (
        Point  # CRS coordinates when the image is georeferenced, else pixel center
    )
    description: str = ""
    # Other attributes (an imported file's extra columns), exported as columns
    properties: dict = attrs.field(factory=dict, eq=False)


class PointCollection:
    """Points in insertion order; the CRS is that of the first georeferenced
    image a point was saved from (a workspace's images are co-registered)."""

    sigPointAdded = Signal(int)
    sigPointRemoved = Signal(int)
    sigCollectionChanged = Signal()

    def __init__(self) -> None:
        self._points: dict[int, VardaPoint] = {}
        self._nextFid = 0
        self.crs: CRS | None = None

    def addPixel(
        self,
        image: VardaRaster,
        x: int,
        y: int,
        *,
        name: str | None = None,
        color: Color = Color.white(),
        description: str = "",
        geometry: Point | None = None,
        properties: dict | None = None,
    ) -> int:
        """Save pixel (x, y) of ``image``; returns the point's fid. The point
        is at the pixel's center, or at ``geometry`` (in the image's CRS, or
        in pixel coordinates when it has none) for a point known more
        precisely than its pixel, such as an imported one."""
        if image.hasGeospatialData:
            if geometry is None:
                geometry = Point(*image.pixelToGeo(x, y))
            if self.crs is None:
                self.crs = image.crs
        elif geometry is None:
            geometry = Point(x + 0.5, y + 0.5)
        fid = self._nextFid
        self._nextFid += 1
        self._points[fid] = VardaPoint(
            fid,
            name or f"Point {fid + 1}",
            color,
            image.name,
            int(x),
            int(y),
            geometry,
            description,
            dict(properties or {}),
        )
        self.sigPointAdded.emit(fid)
        self.sigCollectionChanged.emit()
        return fid

    def removePoint(self, fid: int) -> None:
        del self._points[fid]
        self.sigPointRemoved.emit(fid)
        self.sigCollectionChanged.emit()

    def getPoint(self, fid: int) -> VardaPoint:
        return self._points[fid]

    def getAllPoints(self) -> list[VardaPoint]:
        return list(self._points.values())

    @property
    def fids(self) -> list[int]:
        return list(self._points)

    def batch(self) -> AbstractContextManager:
        """Adds and removes made inside report one ``sigCollectionChanged``
        at the end (an import), so views refresh once."""
        return self.sigCollectionChanged.paused(lambda first, _: first)

    def __len__(self) -> int:
        return len(self._points)

    def toFile(self, path: str | Path, driver: str | None = None) -> Path:
        """Write the points as a GIS file (GeoJSON, GeoPackage or Shapefile by
        suffix) with name, image, pixel column/row, color and description as
        attributes, then any other properties the points carry, and the CRS as
        WKT in a crs column (GeoJSON can record only WGS 84)."""
        if not self._points:
            raise ValueError("No points to export.")
        target = Path(path)
        if driver is None:
            driver = _DRIVERS.get(target.suffix.lower())
            if driver is None:
                raise ValueError(
                    f"Cannot determine a GIS format for '{target.suffix}'."
                )
        points = self.getAllPoints()
        frame = gpd.GeoDataFrame(
            {
                "name": [p.name for p in points],
                "image": [p.image for p in points],
                "pixel_x": [p.x for p in points],
                "pixel_y": [p.y for p in points],
                "color": [p.color.toHexString() for p in points],
                "description": [p.description for p in points],
                **{
                    key: [p.properties.get(key, "") for p in points]
                    for key in dict.fromkeys(k for p in points for k in p.properties)
                },
            },
            geometry=[p.geometry for p in points],
            crs=self.crs,
        )
        if self.crs is not None:
            # GeoJSON can only record WGS 84: Varda's import reads this
            frame["crs"] = self.crs.to_wkt()
        frame.to_file(target, driver=driver)
        return target
