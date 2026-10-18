"""Lines drawn on an image: straight lines, polylines and freehand lines, in
image pixel coordinates, with a name and color. No Qt.

Workspaces share their lines (``shareLines``) so that others can use them:
the 3D Terrain workspace drapes them over a DEM."""

from __future__ import annotations

import weakref
from collections.abc import Iterable
from pathlib import Path

import attrs
import geopandas as gpd
import numpy as np
import numpy.typing as npt
from psygnal import Signal
from shapely.geometry import LineString

from varda.common.entities import VardaRaster
from varda.common.parameter import LabeledEnum
from varda.common.palette import classColor
from varda.lines.profile import lineLength

EXPORT_FILE_FILTER = "GeoJSON (*.geojson);;GeoPackage (*.gpkg);;Shapefile (*.shp)"
_DRIVERS = {
    ".geojson": "GeoJSON",
    ".json": "GeoJSON",
    ".gpkg": "GPKG",
    ".shp": "ESRI Shapefile",
}


class LineKind(LabeledEnum):
    STRAIGHT = "Straight"
    POLYLINE = "Polyline"
    FREEHAND = "Freehand"


@attrs.frozen(eq=False)
class VardaLine:
    fid: int
    name: str
    color: str  # "#rrggbb"
    kind: LineKind
    vertices: np.ndarray  # (n, 2) image pixel coordinates: x (column), y (row)
    # Other attributes (an imported file's extra columns), exported as columns
    properties: dict = attrs.field(factory=dict)

    def length(self, image: VardaRaster | None) -> tuple[float, str]:
        """Its length and units (meters when ``image`` is georeferenced)."""
        if image is None or not image.hasGeospatialData:
            return lineLength(self.vertices, None, None)
        return lineLength(self.vertices, image.transform, image.crs)


class LineCollection:
    """Lines in the order they were drawn."""

    sigLineAdded = Signal(int)
    sigLineRemoved = Signal(int)
    sigLineChanged = Signal(int)
    sigCollectionChanged = Signal()

    def __init__(self) -> None:
        self._lines: dict[int, VardaLine] = {}
        self._nextFid = 0

    def addLine(
        self,
        vertices: npt.ArrayLike,
        kind: LineKind = LineKind.POLYLINE,
        *,
        name: str | None = None,
        color: str | None = None,
        properties: dict | None = None,
    ) -> int:
        vertices = np.asarray(vertices, dtype=float).reshape(-1, 2)
        if len(vertices) < 2:
            raise ValueError("A line needs at least two points.")
        fid = self._nextFid
        self._nextFid += 1
        self._lines[fid] = VardaLine(
            fid,
            name or f"Line {fid + 1}",
            color or classColor(fid),
            kind,
            vertices,
            dict(properties or {}),
        )
        self.sigLineAdded.emit(fid)
        self.sigCollectionChanged.emit()
        return fid

    def removeLine(self, fid: int) -> None:
        del self._lines[fid]
        self.sigLineRemoved.emit(fid)
        self.sigCollectionChanged.emit()

    def updateLine(self, fid: int, **changes) -> None:
        """Rename or recolor a line (``name=``, ``color=``)."""
        self._lines[fid] = attrs.evolve(self._lines[fid], **changes)
        self.sigLineChanged.emit(fid)
        self.sigCollectionChanged.emit()

    def get(self, fid: int) -> VardaLine:
        return self._lines[fid]

    def lines(self) -> list[VardaLine]:
        return list(self._lines.values())

    def __len__(self) -> int:
        return len(self._lines)

    def clear(self) -> None:
        for fid in list(self._lines):
            self.removeLine(fid)

    # --- sessions and files -------------------------------------------------------

    def toJson(self) -> list[dict]:
        return [
            {
                "name": line.name,
                "color": line.color,
                "kind": line.kind.name,
                "vertices": line.vertices.tolist(),
                **({"properties": line.properties} if line.properties else {}),
            }
            for line in self._lines.values()
        ]

    def addFromJson(self, entries: list[dict]) -> None:
        for entry in entries:
            self.addLine(
                np.asarray(entry["vertices"], dtype=float),
                LineKind[entry.get("kind", "POLYLINE")],
                name=entry.get("name"),
                color=entry.get("color"),
                properties=entry.get("properties"),
            )

    def toFrame(
        self, image: VardaRaster, fids: Iterable[int] | None = None
    ) -> gpd.GeoDataFrame:
        """The lines (all, or ``fids``) as GIS lines: map coordinates (in the
        image's CRS) when it is georeferenced, pixel coordinates otherwise,
        with any other properties the lines carry as columns."""
        lines = (
            list(self._lines.values())
            if fids is None
            else [self._lines[fid] for fid in fids]
        )
        georeferenced = image.hasGeospatialData
        geometries = [
            LineString(
                image.imageToGeo(line.vertices.tolist())
                if georeferenced
                else line.vertices
            )
            for line in lines
        ]
        lengths = [line.length(image) for line in lines]
        return gpd.GeoDataFrame(
            {
                "name": [line.name for line in lines],
                "kind": [line.kind.value for line in lines],
                "color": [line.color for line in lines],
                "length": [length for length, _ in lengths],
                "units": [units for _, units in lengths],
                **{
                    key: [line.properties.get(key, "") for line in lines]
                    for key in dict.fromkeys(k for ln in lines for k in ln.properties)
                },
            },
            geometry=geometries,
            crs=image.crs if georeferenced else None,
        )

    def toFile(self, path: str | Path, image: VardaRaster) -> None:
        """The lines as a GIS file (see ``toFrame``)."""
        path = Path(path)
        driver = _DRIVERS.get(path.suffix.lower())
        if driver is None:
            raise ValueError(f"Unsupported file type: {path.suffix}")
        self.toFrame(image).to_file(path, driver=driver)


# Lines that workspaces offer to others, with the image they're drawn on
_shared: weakref.WeakKeyDictionary[LineCollection, VardaRaster] = (
    weakref.WeakKeyDictionary()
)


def shareLines(collection: LineCollection, image: VardaRaster) -> None:
    _shared[collection] = image


def unshareLines(collection: LineCollection) -> None:
    _shared.pop(collection, None)


def sharedLines() -> list[tuple[VardaRaster, LineCollection]]:
    """Shared collections that have lines, with their images."""
    return [(image, lines) for lines, image in _shared.items() if len(lines)]
