"""Vector files imported as ROIs, points or lines on an image. No Qt.

``readVectorFile`` reads any file pyogrio reads (Shapefile, GeoPackage,
GeoJSON, KML, FlatGeobuf, GPX, ...) or a CSV table of points, and works out
its coordinate system. ``planImport`` places its features on an image: it
reprojects them to the image's CRS and maps them to pixel coordinates (or
takes them as pixel coordinates), keeps the geometry type the target takes,
and counts what it skips. ``addToROIs``, ``addToPoints`` and ``addToLines``
then add the placed features to a collection.

GeoJSON can only declare WGS 84, so planetary GeoJSON says Earth even for
Mars coordinates: on an image of another body, such degrees are taken as
degrees on the image's body (as the find-data footprints and the 3D terrain
do). A GeoJSON whose coordinates can't be degrees has lost its real
coordinate system; Varda then asks.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import attrs
import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError
from shapely.geometry import Point, box
from shapely.geometry.base import BaseGeometry

from varda.catalogs.bodies import bodyOf, geographicCrs
from varda.common.entities import Color, ROIMode, VardaRaster
from varda.common.palette import classColor
from varda.common.parameter import LabeledEnum
from varda.lines.line_collection import LineCollection, LineKind
from varda.points.point_collection import PointCollection
from varda.rois.roi_collection import ROICollection
from varda.rois.roi_drawing_state import CLASS_ALPHA
from varda.utilities.proj_lock import PROJ_LOCK

IMPORT_FILE_FILTER = (
    "Vector files (*.geojson *.json *.gpkg *.shp *.kml *.fgb *.gpx *.csv);;"
    "GeoJSON (*.geojson *.json);;GeoPackage (*.gpkg);;Shapefile (*.shp);;"
    "KML (*.kml);;FlatGeobuf (*.fgb);;GPX (*.gpx);;CSV of points (*.csv);;"
    "All files (*)"
)

# Column names guessed (ignoring case), in order of preference
NAME_COLUMNS = ("name", "label", "id")
CLASS_COLUMNS = ("class", "type", "unit")
LONGITUDE_COLUMNS = ("lon", "longitude", "long", "lng")
LATITUDE_COLUMNS = ("lat", "latitude")
X_COLUMNS = ("x", "easting", "col", "column", "sample")
Y_COLUMNS = ("y", "northing", "row", "line")

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


class Target(LabeledEnum):
    """What the features become; the value names them in messages."""

    ROIS = "ROIs"
    POINTS = "points"
    LINES = "lines"


# The geometry family each target takes, as named in messages
FAMILIES = {Target.ROIS: "polygons", Target.POINTS: "points", Target.LINES: "lines"}
MANAGERS = {
    Target.ROIS: "ROI Manager",
    Target.POINTS: "Points Manager",
    Target.LINES: "Lines Manager",
}
_TARGET_OF_TYPE = {
    "Point": Target.POINTS,
    "LineString": Target.LINES,
    "LinearRing": Target.LINES,
    "Polygon": Target.ROIS,
}
# Columns Varda's own exports derive from the image (or the collection),
# recomputed rather than kept. The add functions read color, roi_type,
# description and kind themselves.
_DERIVED_COLUMNS = {
    Target.ROIS: ("crs", "fid"),
    Target.POINTS: ("image", "pixel_x", "pixel_y"),
    Target.LINES: ("length", "units"),
}


class Coordinates(LabeledEnum):
    """What the file's coordinates are taken to be."""

    FILE = "The file's coordinate system"
    IMAGE = "The image's map coordinates"
    PIXELS = "Image pixels (column, row)"
    OTHER = "Another coordinate system"


class VectorImportError(ValueError):
    """A file Varda can't read, or features it can't place on the image; the
    message says why, for the user."""


@attrs.frozen(eq=False)
class VectorSource:
    """A layer of a vector file, or a CSV table of points, as read.

    ``crs`` is the coordinate system Varda found for it (None when unknown),
    and ``crsNote`` says how it was found."""

    path: Path
    frame: gpd.GeoDataFrame
    crs: CRS | None
    crsNote: str
    layers: tuple[str, ...] = ()
    layer: str | None = None
    xColumn: str | None = None  # a CSV's coordinate columns
    yColumn: str | None = None

    @property
    def isCsv(self) -> bool:
        return self.xColumn is not None

    @property
    def columns(self) -> list[str]:
        """The attribute columns (everything but the geometry)."""
        return [str(c) for c in self.frame.columns if c != self.frame.geometry.name]

    def countsByType(self) -> dict[str, int]:
        """Features by geometry family ("points", "lines", "polygons",
        "other"), multi-part features counted once."""
        counts: Counter[str] = Counter()
        for geometry in self.frame.geometry:
            if geometry is None or geometry.is_empty:
                counts["without geometry"] += 1
                continue
            kind = geometry.geom_type.removeprefix("Multi")
            target = _TARGET_OF_TYPE.get(kind)
            counts[FAMILIES[target] if target else "other"] += 1
        return dict(counts)


@attrs.frozen
class ImportOptions:
    coordinates: Coordinates = Coordinates.FILE
    otherCrs: CRS | None = None  # for Coordinates.OTHER
    nameColumn: str | None = None
    classColumn: str | None = None  # ROIs only: ROIs that share a name are a class


@attrs.frozen
class PlacedFeature:
    """One part of a feature, in image pixel coordinates (x the column, y the
    row, whole numbers on pixel edges), with its row's other attributes."""

    geometry: BaseGeometry
    name: str | None
    attributes: dict


@attrs.frozen
class ImportPlan:
    target: Target
    features: tuple[PlacedFeature, ...]
    outside: int  # parts entirely outside the image
    skipped: dict[str, int]  # parts of other geometry families, by family
    empty: int  # features without a geometry
    notes: tuple[str, ...]
    defaultName: str  # for an ROI without a name: the file's name

    def summary(self) -> str:
        """What the import does (or did), for the user."""
        lines = [f"Imported {len(self.features)} {self.target.value}."]
        if self.outside:
            lines.append(f"{self.outside} skipped: entirely outside the image.")
        for family, count in self.skipped.items():
            elsewhere = next((t for t, f in FAMILIES.items() if f == family), None)
            lines.append(
                f"{count} {family} skipped: use the {MANAGERS[elsewhere]} "
                "to import them."
                if elsewhere
                else f"{count} {family} skipped: Varda can't import them."
            )
        if self.empty:
            lines.append(f"{self.empty} features without a geometry skipped.")
        return "\n".join([*lines, *self.notes])


# --- Reading -------------------------------------------------------------------


def listLayers(path: str | Path) -> tuple[str, ...]:
    """The file's layers (one for most formats; a GeoPackage can have many)."""
    if Path(path).suffix.lower() == ".csv":
        return (Path(path).stem,)
    return tuple(str(name) for name in pyogrio.list_layers(path)[:, 0])


def guessColumn(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    """The first column named like one of ``candidates`` (ignoring case)."""
    for candidate in candidates:
        for column in columns:
            if column.lower() == candidate:
                return column
    return None


def csvCoordinateColumns(frame: pd.DataFrame) -> tuple[str, str] | None:
    """A table's (x, y) columns: longitude and latitude, else x and y (or
    easting and northing, column and row), else its first two numeric ones."""
    columns = [str(c) for c in frame.columns]
    for xs, ys in ((LONGITUDE_COLUMNS, LATITUDE_COLUMNS), (X_COLUMNS, Y_COLUMNS)):
        x, y = guessColumn(columns, xs), guessColumn(columns, ys)
        if x and y:
            return x, y
    numeric = [str(c) for c in frame.select_dtypes("number").columns]
    return (numeric[0], numeric[1]) if len(numeric) >= 2 else None


def isLongitudeLatitude(xColumn: str, yColumn: str) -> bool:
    return xColumn.lower() in LONGITUDE_COLUMNS and yColumn.lower() in LATITUDE_COLUMNS


def readVectorFile(
    path: str | Path,
    layer: str | None = None,
    xColumn: str | None = None,
    yColumn: str | None = None,
) -> VectorSource:
    """Read a layer of a vector file (the first unless ``layer``), or a CSV
    of points (its coordinate columns guessed unless given)."""
    path = Path(path)
    try:
        if path.suffix.lower() == ".csv":
            return _readCsv(path, xColumn, yColumn)
        layers = listLayers(path)
        layer = layer or (layers[0] if layers else None)
        frame = gpd.read_file(path, layer=layer)
        driver = str(pyogrio.read_info(path, layer=layer)["driver"])
    except VectorImportError:
        raise
    except Exception as error:  # pyogrio and pandas raise many kinds
        raise VectorImportError(f"Varda couldn't read {path.name}: {error}") from error
    with PROJ_LOCK:
        crs, note, frame = _fileCrs(frame, driver == "GeoJSON")
    return VectorSource(path, frame, crs, note, layers, layer)


def _readCsv(path: Path, xColumn: str | None, yColumn: str | None) -> VectorSource:
    table = pd.read_csv(path)
    if xColumn is None or yColumn is None:
        guessed = csvCoordinateColumns(table)
        if guessed is None:
            raise VectorImportError(
                f"{path.name} has no coordinate columns: Varda looks for "
                "longitude and latitude, or x and y, or two numeric columns."
            )
        xColumn, yColumn = guessed
    x = pd.to_numeric(table[xColumn], errors="coerce").to_numpy(float)
    y = pd.to_numeric(table[yColumn], errors="coerce").to_numpy(float)
    geometry = [
        Point(a, b) if math.isfinite(a) and math.isfinite(b) else None
        for a, b in zip(x, y)
    ]
    frame = gpd.GeoDataFrame(table.drop(columns=[xColumn, yColumn]), geometry=geometry)
    if isLongitudeLatitude(xColumn, yColumn):
        with PROJ_LOCK:
            crs = CRS.from_epsg(4326)
        note = (
            f"Longitude ({xColumn}) and latitude ({yColumn}) in degrees: WGS 84 "
            "on Earth, degrees on the image's body elsewhere."
        )
    else:
        crs = None
        note = (
            f"The table doesn't say what {xColumn} and {yColumn} are: choose the "
            "image's map coordinates, image pixels or a coordinate system."
        )
    return VectorSource(
        path, frame, crs, note, (path.stem,), path.stem, xColumn, yColumn
    )


def _looksLikeDegrees(frame: gpd.GeoDataFrame) -> bool:
    west, south, east, north = frame.total_bounds
    if not np.isfinite([west, south, east, north]).all():
        return True  # nothing to judge by
    return max(abs(west), abs(east)) <= 360 and max(abs(south), abs(north)) <= 90


def _isEarthDegrees(crs: CRS) -> bool:
    return crs.is_geographic and bodyOf(crs) == "Earth"


def _fileCrs(
    frame: gpd.GeoDataFrame, geojson: bool
) -> tuple[CRS | None, str, gpd.GeoDataFrame]:
    """The coordinate system the file's coordinates are in, a note on how it
    was found, and the frame (without the crs column Varda's ROI export
    writes, once read)."""
    declared = CRS(frame.crs) if frame.crs is not None else None
    # GeoJSON says WGS 84 whatever the coordinates are (and when it says nothing)
    assumed = geojson and declared is not None and _isEarthDegrees(declared)
    recorded = _recordedCrs(frame)
    if recorded is not None:
        frame = gpd.GeoDataFrame(frame.drop(columns="crs"))
        if declared is None or assumed:
            return (
                recorded,
                f"{recorded.name}, from the crs column Varda writes with its ROIs.",
                frame,
            )
    if declared is None:
        return None, "The file doesn't say what its coordinates are.", frame
    if assumed:
        if not _looksLikeDegrees(frame):
            return (
                None,
                "GeoJSON always declares WGS 84, but these coordinates can't be "
                "degrees: the file's real coordinate system was lost.",
                frame,
            )
        return (
            declared,
            "WGS 84, as GeoJSON always declares: degrees on the image's body "
            "when the image isn't of Earth.",
            frame,
        )
    return declared, declared.name, frame


def _recordedCrs(frame: gpd.GeoDataFrame) -> CRS | None:
    """The CRS in the crs column Varda's ROI export writes, if there is one."""
    if "crs" not in frame.columns:
        return None
    values = frame["crs"].dropna().unique()
    if len(values) != 1:
        return None
    try:
        return CRS.from_user_input(str(values[0]))
    except CRSError:
        return None


# --- Placing ---------------------------------------------------------------------


def defaultOptions(
    source: VectorSource, image: VardaRaster, target: Target
) -> ImportOptions:
    """The options the import dialog starts with: the coordinates as the
    file says (pixels when it can't say and they fit the image), and name
    and class columns guessed from their names."""
    columns = source.columns
    return ImportOptions(
        _defaultCoordinates(source, image),
        nameColumn=guessColumn(columns, NAME_COLUMNS),
        classColumn=guessColumn(columns, CLASS_COLUMNS)
        if target is Target.ROIS
        else None,
    )


def _defaultCoordinates(source: VectorSource, image: VardaRaster) -> Coordinates:
    crs = source.crs
    if not image.hasGeospatialData:
        # GeoJSON's WGS 84 says nothing, so pixels; a real CRS is refused
        geojsonDegrees = crs is not None and _isEarthDegrees(crs)
        return Coordinates.PIXELS if crs is None or geojsonDegrees else Coordinates.FILE
    if crs is not None:
        return Coordinates.FILE
    west, south, east, north = source.frame.total_bounds
    fits = (
        np.isfinite([west, south, east, north]).all()
        and west >= 0
        and south >= 0
        and east <= image.width
        and north <= image.height
    )
    return Coordinates.PIXELS if fits else Coordinates.IMAGE


def planImport(
    source: VectorSource,
    image: VardaRaster,
    options: ImportOptions,
    target: Target,
) -> ImportPlan:
    """Place the source's features of the target's geometry type on the
    image (see the module docstring). Raises VectorImportError when they
    can't be placed (no CRS on the file or the image, different bodies)."""
    toPixels, notes = _pixelMapping(source, image, options)
    frame = source.frame
    with PROJ_LOCK:
        placed = shapely.transform(
            shapely.force_2d(np.asarray(frame.geometry, dtype=object)), toPixels
        )
    records = frame.drop(columns=frame.geometry.name).to_dict("records")
    extent = box(0, 0, image.width, image.height)
    features: list[PlacedFeature] = []
    skipped: Counter[str] = Counter()
    outside = empty = 0
    for geometry, record in zip(placed, records):
        if geometry is None or geometry.is_empty:
            empty += 1
            continue
        parts = []
        for part in _parts(geometry):
            kind = _TARGET_OF_TYPE.get(part.geom_type)
            if kind is target:
                parts.append(part)
            else:
                skipped[FAMILIES[kind] if kind else "other"] += 1
        name, attributes = _nameAndAttributes(record, options, target)
        for number, part in enumerate(parts, 1):
            if not _inside(part, extent, image):
                outside += 1
                continue
            partName = name
            if name is not None and len(parts) > 1 and target is not Target.ROIS:
                partName = f"{name} {number}"  # ROIs share theirs: one class
            features.append(PlacedFeature(part, partName, attributes))
    return ImportPlan(
        target,
        tuple(features),
        outside,
        dict(skipped),
        empty,
        tuple(notes),
        source.path.stem,
    )


def _pixelMapping(
    source: VectorSource, image: VardaRaster, options: ImportOptions
) -> tuple[Callable[[np.ndarray], np.ndarray], list[str]]:
    """A function from the file's (n, 2) coordinates to image pixel
    coordinates, and notes on how it was chosen."""
    mode = options.coordinates
    if mode is Coordinates.PIXELS:
        return (lambda xy: xy), [
            "Coordinates read as image pixels: x the column, y the row."
        ]
    if not image.hasGeospatialData:
        raise VectorImportError(
            "This image has no map coordinates, so features in map coordinates "
            "can't be placed on it. Only files in image pixel coordinates can be "
            "imported: choose “Image pixels” if this file's are."
        )
    imageCrs = image.crs
    assert imageCrs is not None  # hasGeospatialData
    if mode is Coordinates.IMAGE:
        sourceCrs = imageCrs
    elif mode is Coordinates.OTHER:
        if options.otherCrs is None:
            raise VectorImportError(
                "Enter the coordinate system of the file's coordinates "
                "(e.g. EPSG:4326, or a WKT or PROJ string)."
            )
        sourceCrs = options.otherCrs
    elif source.crs is None:
        raise VectorImportError(
            "The file doesn't say what its coordinates are: choose the image's "
            "map coordinates, image pixels or another coordinate system."
        )
    else:
        sourceCrs = source.crs
    notes = []
    with PROJ_LOCK:
        body = bodyOf(imageCrs)
        if _isEarthDegrees(sourceCrs) and body != "Earth":
            sourceCrs = imageCrs.geodetic_crs or CRS(geographicCrs(body))
            notes.append(
                "The file's coordinates say WGS 84 (Earth), as GeoJSON always "
                f"does; Varda read them as degrees of longitude and latitude on "
                f"{body}, in the image's own geographic coordinates."
            )
        elif bodyOf(sourceCrs) != body:
            raise VectorImportError(
                f"The file's coordinates are on {bodyOf(sourceCrs)} and the image "
                f"is of {body}: choose the coordinate system the file is in."
            )
        try:
            transformer = (
                None
                if sourceCrs == imageCrs
                else Transformer.from_crs(sourceCrs, imageCrs, always_xy=True)
            )
        except ProjError as error:
            raise VectorImportError(
                f"Varda can't convert {sourceCrs.name} to the image's "
                f"{imageCrs.name}: {error}"
            ) from error
    if transformer is not None:
        notes.append(f"Reprojected from {sourceCrs.name} to {imageCrs.name}.")
    inverse = ~image.transform

    def toPixels(xy: np.ndarray) -> np.ndarray:
        x, y = xy[:, 0], xy[:, 1]
        if transformer is not None:
            # (pyproj 3.7 takes a one-element array for a scalar, deprecated)
            x, y = transformer.transform(x.tolist(), y.tolist(), errcheck=False)
        col, row = inverse * (np.asarray(x), np.asarray(y))
        return np.column_stack([col, row])

    return toPixels, notes


def _parts(geometry: BaseGeometry) -> list[BaseGeometry]:
    """A geometry's single parts (multi-part and collections taken apart)."""
    if hasattr(geometry, "geoms"):
        return [part for g in geometry.geoms for part in _parts(g)]
    return [geometry]


def _inside(part: BaseGeometry, extent: BaseGeometry, image: VardaRaster) -> bool:
    """Whether any of the part is on the image (a point on one of its pixels)."""
    if isinstance(part, Point):
        return 0 <= part.x < image.width and 0 <= part.y < image.height
    return part.intersects(extent) and not part.touches(extent)


def _nameAndAttributes(
    record: dict, options: ImportOptions, target: Target
) -> tuple[str | None, dict]:
    """A row's name (for ROIs, its class when a class column is chosen), and
    its other columns as plain values."""
    name = _text(record.get(options.nameColumn)) if options.nameColumn else None
    used = {options.nameColumn}
    if options.classColumn:
        className = _text(record.get(options.classColumn))
        used = {options.classColumn}  # the name column stays as a column
        name = className or name
    attributes = {}
    for column, value in record.items():
        if column in used or column in _DERIVED_COLUMNS[target]:
            continue
        # an unused "name" column is kept, beside the name Varda gives
        key = "source_name" if column == "name" else str(column)
        attributes[key] = _plain(value)
    return name or None, attributes


def _text(value: object) -> str:
    plain = _plain(value)
    return "" if plain == "" else str(plain)


def _plain(value: object) -> object:
    """A cell as a plain Python value; missing ones as ""."""
    if value is None or value is pd.NaT:
        return ""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# --- Adding ------------------------------------------------------------------------


def _hexColor(value: object) -> str | None:
    return value if isinstance(value, str) and _HEX_COLOR.match(value) else None


def addToROIs(collection: ROICollection, plan: ImportPlan) -> list[int]:
    """Add the plan's polygons as ROIs; returns their fids. ROIs are named
    by their class (the file's name when they have none); a class without a
    color takes the color of the collection's class of that name, or the
    next palette color."""
    colors = {name: collection.classColor(name) for name in collection.classes()}
    fids = []
    for feature in plan.features:
        attributes = dict(feature.attributes)
        name = feature.name or plan.defaultName
        hexColor = _hexColor(attributes.pop("color", None))
        color = Color.fromHexString(hexColor) if hexColor else colors.get(name)
        if color is None:
            color = Color.fromHexString(classColor(len(colors)) + CLASS_ALPHA)
        colors.setdefault(name, color)
        try:
            roiType = ROIMode[str(attributes.pop("roi_type", "POLYGON"))]
        except KeyError:
            roiType = ROIMode.POLYGON
        geometry = feature.geometry
        if collection.crs is not None:  # stored in map coordinates
            transform = collection.transform
            geometry = shapely.transform(
                geometry,
                lambda xy: np.column_stack(transform * (xy[:, 0], xy[:, 1])),
            )
        fids.append(collection.addROI(geometry, name, color, roiType, **attributes))
    return fids


def addToPoints(
    collection: PointCollection, image: VardaRaster, plan: ImportPlan
) -> list[int]:
    """Add the plan's points; returns their fids. Each point is saved on the
    pixel it falls in, at its exact position."""
    fids = []
    for feature in plan.features:
        point = feature.geometry
        assert isinstance(point, Point)
        attributes = dict(feature.attributes)
        hexColor = _hexColor(attributes.pop("color", None))
        # a shapefile cuts column names to 10 characters
        description = _text(
            attributes.pop("description", attributes.pop("descriptio", ""))
        )
        exact = (
            Point(image.imageToGeo([[point.x, point.y]])[0])
            if image.hasGeospatialData
            else point
        )
        fids.append(
            collection.addPixel(
                image,
                math.floor(point.x),
                math.floor(point.y),
                name=feature.name,
                color=Color.fromHexString(hexColor) if hexColor else Color.white(),
                description=description,
                geometry=exact,
                properties=attributes,
            )
        )
    return fids


def addToLines(collection: LineCollection, plan: ImportPlan) -> list[int]:
    """Add the plan's lines; returns their fids."""
    kinds: dict[str, LineKind] = {kind.value.lower(): kind for kind in LineKind}
    kinds.update({kind.name.lower(): kind for kind in LineKind})
    fids = []
    for feature in plan.features:
        attributes = dict(feature.attributes)
        vertices = np.asarray(feature.geometry.coords, dtype=float)
        hexColor = _hexColor(attributes.pop("color", None))
        kind = kinds.get(_text(attributes.pop("kind", "")).lower())
        if kind is None:
            kind = LineKind.STRAIGHT if len(vertices) == 2 else LineKind.POLYLINE
        fids.append(
            collection.addLine(
                vertices,
                kind,
                name=feature.name,
                color=hexColor[:7] if hexColor else None,
                properties=attributes,
            )
        )
    return fids
