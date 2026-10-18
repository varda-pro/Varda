"""Saved pixel points: which pixel of which image, and where that is on the
ground, exportable to GIS files."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from weakref import WeakKeyDictionary

import attrs
import geopandas as gpd
from psygnal import Signal
from pyproj import CRS
from shapely.geometry import Point

from varda.common.entities import Color, VardaRaster
from varda.geo.describe import describeCrs
from varda.geo.equivalence import canTransform, sameCrs
from varda.geo.transform import transformPoints

EXPORT_FILE_FILTER = "GeoJSON (*.geojson);;GeoPackage (*.gpkg);;Shapefile (*.shp)"
_DRIVERS = {
    ".geojson": "GeoJSON",
    ".json": "GeoJSON",
    ".gpkg": "GPKG",
    ".shp": "ESRI Shapefile",
}


@attrs.define
class _Source:
    """An image points were saved from: its CRS when they were, and which."""

    crs: CRS | None
    fids: set[int] = attrs.field(factory=set)


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
    image a point was saved from (forgotten when the last point is removed),
    and points from images on other CRSs (of the same body) are converted
    to it. Points of images without georeferencing (pixel coordinates)
    can't join them, nor the reverse.

    A coordinate system set, fixed or undone on an image later (Image
    Properties) reaches its points (``_onImageMetadataChanged``)."""

    sigPointAdded = Signal(int)
    sigPointRemoved = Signal(int)
    sigCollectionChanged = Signal()
    # why points of an image whose CRS changed kept their old positions
    sigProblem = Signal(str)

    def __init__(self) -> None:
        self._points: dict[int, VardaPoint] = {}
        self._nextFid = 0
        self.crs: CRS | None = None
        # the images points were saved from, held weakly
        self._sources: WeakKeyDictionary[VardaRaster, _Source] = WeakKeyDictionary()

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
        precisely than its pixel, such as an imported one.

        A point from an image on another CRS is converted to the
        collection's (``transformPoints``: the same planetocentric longitude
        and latitude). It raises ValueError, and the point isn't saved, for
        an image of another body (or one Varda can't tell is the same; the
        message names both), and for an image without georeferencing among
        points with a CRS, or the reverse."""
        self._checkGeoreferencing(image)
        if image.hasGeospatialData:
            if geometry is None:
                geometry = Point(*image.pixelToGeo(x, y))
            if self.crs is None:
                self.crs = image.crs
            else:
                # the same planetocentric place; raises for another body
                (geoX,), (geoY,) = transformPoints(
                    image.crs, self.crs, [geometry.x], [geometry.y]
                )
                geometry = Point(geoX, geoY)
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
        self._sourceOf(image).fids.add(fid)
        self.sigPointAdded.emit(fid)
        self.sigCollectionChanged.emit()
        return fid

    def _checkGeoreferencing(self, image: VardaRaster) -> None:
        """Pixel coordinates and map coordinates never share a collection."""
        if image.hasGeospatialData == (self.crs is not None) or not self._points:
            return
        if self.crs is not None:
            raise ValueError(
                f"{image.name} has no georeferencing; the points are in "
                f"{describeCrs(self.crs).text}"
            )
        raise ValueError(
            f"{image.name} is georeferenced; the points are in pixel "
            "coordinates (saved from images without georeferencing)"
        )

    def _sourceOf(self, image: VardaRaster) -> _Source:
        source = self._sources.get(image)
        if source is None:
            source = self._sources[image] = _Source(image.crs)
            # a bound method: the image's signal holds this collection weakly
            image.sigMetadataChanged.connect(self._onImageMetadataChanged)
        return source

    def _onImageMetadataChanged(self) -> None:
        """The image emitting changed its CRS, perhaps: its points' pixels
        keep their map numbers, now read on the new CRS."""
        image = Signal.sender()
        source = self._sources.get(image) if isinstance(image, VardaRaster) else None
        if source is None or image.crs is source.crs:
            return
        old, new = source.crs, image.crs
        source.crs = new
        source.fids &= self._points.keys()
        if not source.fids:
            return
        fids = sorted(source.fids)
        problem = self._reread(image, fids, old, new)
        if problem is not None:
            count = f"{len(fids)} point{'s' if len(fids) != 1 else ''}"
            self.sigProblem.emit(
                f"{image.name}'s coordinate system changed, but its {count} "
                f"can't join the others: {problem}. They were kept where they "
                "were; delete them and save them again from the image."
            )
            return
        self.sigCollectionChanged.emit()

    def _reread(
        self, image: VardaRaster, fids: list[int], old: CRS | None, new: CRS | None
    ) -> str | None:
        """Move points ``fids`` of ``image`` from its ``old`` CRS to its
        ``new`` one, as the same map numbers (or pixels, gaining or losing a
        CRS); None, or why they can't be."""
        mine = [self._points[fid] for fid in fids]
        alone = len(mine) == len(self._points)
        if (old is None) != (new is None) and not alone:
            return "pixel and map coordinates never share the points"
        if old is None or self.crs is None:  # pixels → the image's map numbers
            xy = [image.transform * (p.geometry.x, p.geometry.y) for p in mine]
        else:  # the image's own map numbers, as they were saved
            xs, ys = _converted(self.crs, old, mine)
            xy = list(zip(xs, ys))
        if new is None:  # → pixels (alone: checked above)
            xy = [~image.transform * point for point in xy]
            self.crs = None
        elif alone:
            self.crs = new
        else:
            reason = canTransform(new, self.crs)
            if reason is not None:
                return reason
            xs, ys = transformPoints(new, self.crs, *zip(*xy))
            xy = list(zip(xs, ys))
        for point, (x, y) in zip(mine, xy):
            self._points[point.fid] = attrs.evolve(point, geometry=Point(x, y))
        return None

    def removePoint(self, fid: int) -> None:
        del self._points[fid]
        if not self._points:
            self.crs = None  # the next point sets it again
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


def _converted(
    src: CRS, dst: CRS, points: list[VardaPoint]
) -> tuple[list[float], list[float]]:
    xs = [p.geometry.x for p in points]
    ys = [p.geometry.y for p in points]
    if sameCrs(src, dst):
        return xs, ys
    outX, outY = transformPoints(src, dst, xs, ys)
    return outX.tolist(), outY.tolist()
