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
import warnings
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import attrs
import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
from affine import Affine
from pyproj import CRS, Transformer
from pyproj.enums import TransformDirection
from pyproj.exceptions import CRSError, ProjError
from shapely.geometry import Point, box
from shapely.geometry.base import BaseGeometry

from varda.common.entities import Color, ROIMode, VardaRaster
from varda.common.palette import classColor
from varda.common.parameter import LabeledEnum
from varda.geo.bodies import Body, identifyBody
from varda.geo.equivalence import sameCrs
from varda.geo.transform import (
    eastLonLat,
    geodeticLonLat,
    planetocentricLonLat,
    transformPoints,
)
from varda.lines.line_collection import LineCollection, LineKind
from varda.points.point_collection import PointCollection
from varda.rois.roi_collection import NewROI, ROICollection
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
X_COLUMNS = ("x", "easting", "col", "column")
Y_COLUMNS = ("y", "northing", "row")
# ISIS's (campt, qview): 1-based, whole numbers at pixel centers
ISIS_COLUMNS = ("sample", "line")

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
    Target.POINTS: ("crs", "image", "pixel_x", "pixel_y"),
    Target.LINES: ("crs", "length", "units"),
}


def counted(count: int, noun: str) -> str:
    """ "1 point", "2 points" (``noun`` is the plural)."""
    if count == 1 and noun.endswith("s"):
        noun = noun[:-1]
    return f"{count} {noun}"


class Coordinates(LabeledEnum):
    """What the file's coordinates are taken to be."""

    FILE = "The file's coordinate system"
    BODY = "Degrees on the image's body"
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
    unplaced: int = 0  # parts PROJ couldn't convert (e.g. latitude 95)

    def summary(self) -> str:
        """What the import does (or did), for the user."""
        lines = [f"Imported {counted(len(self.features), self.target.value)}."]
        if self.outside:
            lines.append(f"{self.outside} skipped: entirely outside the image.")
        if self.unplaced:
            lines.append(
                f"{self.unplaced} skipped: their coordinates couldn't be converted "
                "to the image's coordinate system."
            )
        for family, count in self.skipped.items():
            elsewhere = next((t for t, f in FAMILIES.items() if f == family), None)
            lines.append(
                f"{counted(count, family)} skipped: use the {MANAGERS[elsewhere]} "
                "to import them."
                if elsewhere
                else f"{counted(count, family)} skipped: Varda can't import them."
            )
        if self.empty:
            lines.append(
                f"{counted(self.empty, 'features')} without a geometry skipped."
            )
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
    pairs = (
        (LONGITUDE_COLUMNS, LATITUDE_COLUMNS),
        (ISIS_COLUMNS[:1], ISIS_COLUMNS[1:]),
        (X_COLUMNS, Y_COLUMNS),
    )
    for xs, ys in pairs:
        x, y = guessColumn(columns, xs), guessColumn(columns, ys)
        if x and y:
            return x, y
    numeric = [str(c) for c in frame.select_dtypes("number").columns]
    return (numeric[0], numeric[1]) if len(numeric) >= 2 else None


def isLongitudeLatitude(xColumn: str, yColumn: str) -> bool:
    return xColumn.lower() in LONGITUDE_COLUMNS and yColumn.lower() in LATITUDE_COLUMNS


def isIsisSampleLine(xColumn: str, yColumn: str) -> bool:
    return (xColumn.lower(), yColumn.lower()) == ISIS_COLUMNS


def readVectorFile(
    path: str | Path,
    layer: str | None = None,
    xColumn: str | None = None,
    yColumn: str | None = None,
) -> VectorSource:
    """Read a layer of a vector file (the first unless ``layer``), or a CSV
    of points (its coordinate columns guessed unless given)."""
    path = Path(path)
    # GDAL reads a layer's coordinate system with PROJ, and geopandas makes
    # a pyproj CRS of it: never while another thread uses PROJ
    with PROJ_LOCK:
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
            raise VectorImportError(
                f"Varda couldn't read {path.name}: {error}"
            ) from error
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
    if isIsisSampleLine(xColumn, yColumn):
        # ISIS sample 1, line 1 is the center of the first pixel: Varda's
        # (0.5, 0.5), as Varda's pixel coordinates have whole numbers on edges
        x, y = x - 0.5, y - 0.5
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
    elif isIsisSampleLine(xColumn, yColumn):
        crs = None
        note = (
            f"ISIS {xColumn} and {yColumn}: 1-based, whole numbers at pixel "
            "centers. Varda subtracted 0.5 from each (sample 1, line 1 is the "
            "first pixel's center); read them as image pixels."
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


def _body(crs: CRS | None) -> Body | None:
    """The body ``crs`` is on (None when it's on no known body)."""
    return identifyBody(crs).body


def bodyLabel(crs: CRS | None) -> str:
    """The name of the body ``crs`` is on, for messages and labels."""
    body = _body(crs)
    return body.name if body is not None else "the image's body"


def _isEarth(body: Body | None) -> bool:
    return body is not None and body.name == "Earth"


def _isEarthDegrees(crs: CRS) -> bool:
    return crs.is_geographic and _isEarth(_body(crs))


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
        # GDAL may rewrite a CRS it stores (a polar stereographic's k=1 as
        # lat_ts=90), shifting coordinates by micrometers: Varda's exact
        # record is used then, but not once the file was reprojected
        if declared is None or assumed or _sameCoordinates(declared, recorded, frame):
            return (
                recorded,
                f"{recorded.name}, from the crs column Varda writes with its exports.",
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
            "WGS 84, as GeoJSON always declares (also for degrees on another body).",
            frame,
        )
    return declared, declared.name, frame


def _sameCoordinates(first: CRS, second: CRS, frame: gpd.GeoDataFrame) -> bool:
    """Whether the frame's coordinates are the same (to 1e-5 of their
    size) in both coordinate systems."""
    if sameCrs(first, second):
        return True
    xy = shapely.get_coordinates(np.asarray(frame.geometry, dtype=object))[:100]
    if not len(xy):
        return False
    try:
        x, y = transformPoints(first, second, xy[:, 0], xy[:, 1])
    except (ValueError, ProjError):  # different or unknown bodies
        return False
    return bool(np.allclose(np.column_stack([x, y]), xy, rtol=1e-5, atol=1e-6))


def _recordedCrs(frame: gpd.GeoDataFrame) -> CRS | None:
    """The CRS in the crs column Varda's exports write, if there is one."""
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
        with PROJ_LOCK:
            # an unknown body is not Earth: identifyBody always knows Earth
            otherBody = _isEarthDegrees(crs) and not _isEarth(_body(image.crs))
        return Coordinates.BODY if otherBody else Coordinates.FILE
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
    toPixels = _pixelMapping(source, image, options)
    frame = source.frame
    with PROJ_LOCK:
        placed = shapely.transform(
            shapely.force_2d(np.asarray(frame.geometry, dtype=object)), toPixels
        )
    notes = list(toPixels.notes)
    if toPixels.shifted:
        notes.append(
            f"{counted(toPixels.shifted, 'longitudes')} shifted by a multiple of "
            "360° into the image's longitude range."
        )
    # (a frame of geometry alone gives no records at all)
    records = frame.drop(columns=frame.geometry.name).to_dict("records") or [
        {} for _ in range(len(frame))
    ]
    extent = box(0, 0, image.width, image.height)
    features: list[PlacedFeature] = []
    skipped: Counter[str] = Counter()
    outside = empty = unplaced = invalid = 0
    for geometry, record in zip(placed, records, strict=True):
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
            if not np.isfinite(shapely.get_coordinates(part)).all():
                unplaced += 1
                continue
            if not _inside(part, extent, image):
                outside += 1
                continue
            invalid += not part.is_valid
            partName = name
            if name is not None and len(parts) > 1 and target is not Target.ROIS:
                partName = f"{name} {number}"  # ROIs share theirs: one class
            features.append(PlacedFeature(part, partName, attributes))
    if invalid:
        notes.append(
            f"{counted(invalid, 'polygons')} invalid (e.g. self-intersecting), "
            "imported as they are: their pixels and statistics may be wrong."
        )
    return ImportPlan(
        target,
        tuple(features),
        outside,
        dict(skipped),
        empty,
        tuple(notes),
        source.path.stem,
        unplaced,
    )


@attrs.frozen
class _Conversion:
    """From one CRS to another of the same body (``transformPoints``: x the
    east–west coordinate, whatever the axis order; the same planetocentric
    place between two surfaces). Infinite where PROJ can't convert."""

    source: CRS
    target: CRS

    def transform(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return transformPoints(self.source, self.target, x, y)


class _PixelMapping:
    """From the file's (n, 2) coordinates to image pixel coordinates, for
    ``shapely.transform``: ``transformer`` (to the image's CRS, or to its
    geographic one when ``projector`` follows), then longitudes wrapped to
    within 180° of ``center``, then ``projector``, then the image's inverse
    geotransform. ``notes`` say how, and ``shifted`` counts the longitudes
    moved by 360° into the image's range."""

    def __init__(
        self,
        notes: list[str],
        inverse: Affine | None = None,
        transformer: _Conversion | None = None,
        center: float | None = None,
        projector: Transformer | None = None,
    ) -> None:
        self.notes = notes
        self.shifted = 0
        self._inverse = inverse
        self._transformer = transformer
        self._center = center
        self._projector = projector

    def _wrap(self, longitude: np.ndarray, center: float) -> np.ndarray:
        wrapped = center + (longitude - center + 180.0) % 360.0 - 180.0
        moved = np.isfinite(longitude) & (np.abs(wrapped - longitude) > 1e-9)
        self.shifted += int(np.count_nonzero(moved))
        return np.where(moved, wrapped, longitude)

    def __call__(self, xy: np.ndarray) -> np.ndarray:
        if self._inverse is None:
            return xy  # pixels already
        x, y = xy[:, 0], xy[:, 1]
        if self._transformer is not None:
            x, y = self._transformer.transform(x, y)
        for step in ("wrap", self._projector):
            if step == "wrap":
                if self._center is not None:
                    x = self._wrap(x, self._center)
            elif isinstance(step, Transformer):
                # (pyproj 3.7 takes a one-element array for a scalar, deprecated)
                x, y = step.transform(x.tolist(), y.tolist(), errcheck=False)
                x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        col, row = self._inverse * (x, y)
        return np.column_stack([col, row])


def _pixelMapping(
    source: VectorSource, image: VardaRaster, options: ImportOptions
) -> _PixelMapping:
    """How to place the file's coordinates on the image's pixels."""
    mode = options.coordinates
    if mode is Coordinates.PIXELS:
        return _PixelMapping(
            ["Coordinates read as image pixels: x the column, y the row."]
        )
    if not image.hasGeospatialData:
        raise VectorImportError(
            "This image has no map coordinates, so features in map coordinates "
            "can't be placed on it. Only files in image pixel coordinates can be "
            "imported: choose “Image pixels” if this file's are."
        )
    imageCrs = image.crs
    assert imageCrs is not None  # hasGeospatialData
    notes = []
    with PROJ_LOCK:
        imageBody = _body(imageCrs)
        body = imageBody.name if imageBody is not None else "the image's body"
        if mode is Coordinates.BODY:
            if imageCrs.geodetic_crs is not None:
                # longitude east first, whatever the image's axes
                sourceCrs = eastLonLat(imageCrs)
            elif imageBody is not None:
                sourceCrs = imageBody.sphereCrs()
            else:
                raise VectorImportError(
                    "The image's coordinate system has no longitude and latitude, "
                    "and its body is unknown: choose another coordinate system."
                )
            notes.append(
                f"Coordinates read as degrees of longitude and latitude on {body}, "
                f"in the image's own geographic coordinate system "
                f"({sourceCrs.name})."
            )
        elif mode is Coordinates.IMAGE:
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
        sourceBody = _body(sourceCrs)
        # two unknown bodies can't be told apart; Earth is never unknown
        if sourceBody != imageBody and (
            None not in (sourceBody, imageBody)
            or _isEarth(sourceBody)
            or _isEarth(imageBody)
        ):
            hint = (
                f"GeoJSON always says WGS 84: choose “Degrees on {body}” if they "
                f"are degrees on {body}."
                if _isEarthDegrees(sourceCrs)
                else "Choose the coordinate system the file is in."
            )
            fileOn = sourceBody.name if sourceBody else "an unknown body"
            imageOf = imageBody.name if imageBody else "an unknown body"
            raise VectorImportError(
                f"The file's coordinates are on {fileOn} and the image is of "
                f"{imageOf}. {hint}"
            )
        center, projector = None, None
        target = imageCrs
        if sourceCrs.is_geographic:
            center, projector = _longitudeRange(image, imageCrs)
            if projector is not None:  # takes longitude east, geodetic latitude
                target = geodeticLonLat(imageCrs)
        transformer = None
        if not sameCrs(sourceCrs, target):
            transformer = _Conversion(sourceCrs, target)
            try:  # builds PROJ's operation, or says why there is none
                transformer.transform(np.zeros(0), np.zeros(0))
            except (ValueError, ProjError) as error:
                raise VectorImportError(
                    f"Varda can't convert {sourceCrs.name} to the image's "
                    f"{imageCrs.name}: {error}"
                ) from error
    if mode is not Coordinates.BODY and (transformer or projector) is not None:
        notes.append(f"Reprojected from {sourceCrs.name} to {imageCrs.name}.")
    return _PixelMapping(notes, ~image.transform, transformer, center, projector)


def _longitudeRange(
    image: VardaRaster, imageCrs: CRS
) -> tuple[float | None, Transformer | None]:
    """Where geographic coordinates' longitudes go on the image: the
    longitude they are wrapped to within 180° of (None to leave them), and,
    for an image that spans more than 180° from its projection's central
    meridian (a mosaic over 180° E in a projection centered on 0°), the
    projection run with PROJ's +over, which keeps such longitudes as they
    are instead of wrapping them around the central meridian.

    A geographic image's longitudes take its own convention (0 to 360 or
    -180 to 180), around its center. A projected image's projection wraps
    longitudes itself; only one past 180° needs +over."""
    width, height = image.width, image.height
    if imageCrs.is_geographic:
        centerX, _ = image.transform * (width / 2, height / 2)
        return float(centerX), None
    with warnings.catch_warnings():  # "you will likely lose ... information"
        warnings.simplefilter("ignore", UserWarning)
        proj4 = imageCrs.to_proj4()
    geodetic = imageCrs.geodetic_crs
    if proj4 is None or geodetic is None:
        return None, None
    edges = image.imageToGeo(
        [[u * width, v * height] for u in (0, 0.5, 1) for v in (0, 0.5, 1)]
    )
    x, y = edges[:, 0].tolist(), edges[:, 1].tolist()
    directions = [axis.direction for axis in imageCrs.axis_info[:2]]
    if directions not in (["east", "north"], ["west", "north"]):
        return None, None  # a PROJ string can't say these axes
    # a westing (IAU_2015 west-positive projections) is the easting negated
    westing = " +step +proj=axisswap +order=-1,2" if directions[0] == "west" else ""
    try:
        projection = proj4.replace("+type=crs", "").strip()
        projector = Transformer.from_pipeline(
            "+proj=pipeline +step +proj=unitconvert +xy_in=deg +xy_out=rad "
            f"+step {projection} +over{westing}"
        )
        overLon = np.asarray(
            projector.transform(x, y, direction=TransformDirection.INVERSE)[0]
        )
        plainLon, _ = planetocentricLonLat(imageCrs, x, y)  # east, any axes
    except (CRSError, ProjError, ValueError):
        return None, None
    known = np.isfinite(plainLon) & np.isfinite(overLon)
    center = float(overLon[4])  # the middle of the image
    if not np.isfinite(center) or not np.any(
        np.abs(plainLon[known] - overLon[known]) > 1e-6
    ):
        return None, None
    return center, projector


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
    """Add the plan's polygons as ROIs, all at once; returns their fids. ROIs
    are named by their class (the file's name when they have none); a class
    without a color takes the color of the collection's class of that name,
    or the next palette color."""
    colors = {name: collection.classColor(name) for name in collection.classes()}
    transform = collection.transform
    rois = []
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
            geometry = shapely.transform(
                geometry,
                lambda xy: np.column_stack(transform * (xy[:, 0], xy[:, 1])),
            )
        rois.append(NewROI(geometry, name, color, roiType, attributes))
    return collection.addROIs(rois)


def addToPoints(
    collection: PointCollection, image: VardaRaster, plan: ImportPlan
) -> list[int]:
    """Add the plan's points; returns their fids. Each point is saved on the
    pixel it falls in, at its exact position."""
    fids = []
    with collection.batch():
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
    with collection.batch():
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
