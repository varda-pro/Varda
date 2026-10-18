"""Calculated ROI columns.

A column can be filled from each ROI's geometry (area, perimeter, centroid;
areas and perimeters also on the body, geodesically, with
``geo.measure.polygonArea``),
from a statistic of a raster band inside the ROI (zonal statistics), or from
an expression over other columns. ``CalculatedColumns`` keeps them current as
ROIs are drawn or edited. Expressions are parsed and evaluated by a small
whitelisting evaluator, never ``eval``.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from affine import Affine
from pyproj import CRS
from shapely.geometry import MultiPolygon, Polygon

from varda.common.expressions import (
    ExpressionError as ExpressionError,
    evaluate,
    identifier as identifier,
    parse,
    words,
)
from varda.common.entities import VardaRaster
from varda.geo.measure import Measurement, polygonArea
from varda.rois.roi_collection import ROICollection

# kind -> (label, short name used in suggested column names)
GEOMETRY_MEASURES = {
    "area_pixels": ("Area (pixels)", "area_px"),
    "perimeter_pixels": ("Perimeter (pixels)", "perimeter_px"),
    "area_map": ("Area (map units², planar)", "area"),
    "perimeter_map": ("Perimeter (map units, planar)", "perimeter"),
    "area_body": ("Area on the body (km²)", "area_km2"),
    "perimeter_body": ("Perimeter on the body (km)", "perimeter_km"),
    "centroid_x": ("Centroid x (map units)", "centroid_x"),
    "centroid_y": ("Centroid y (map units)", "centroid_y"),
}
# measured on the body, geodesically: kind -> its unit per meter or m²
BODY_MEASURES = {"area_body": 1e-6, "perimeter_body": 1e-3}
MAP_MEASURES = {"area_map", "perimeter_map", "centroid_x", "centroid_y"} | set(
    BODY_MEASURES
)
NO_MAP_COORDINATES = "the image has no map coordinates"


def _finite(values: np.ndarray) -> np.ndarray:
    return values[np.isfinite(values)]


STATISTICS: dict[str, Callable[[np.ndarray], float]] = {
    "mean": lambda v: float(np.mean(v)),
    "median": lambda v: float(np.median(v)),
    "std": lambda v: float(np.std(v)),
    "min": lambda v: float(np.min(v)),
    "max": lambda v: float(np.max(v)),
    "sum": lambda v: float(np.sum(v)),
    "count": lambda v: float(v.size),
}

FUNCTIONS: dict[str, Callable[..., float]] = {
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
}
CONSTANTS = {"pi": math.pi, "e": math.e}


@dataclass(frozen=True)
class GeometryMeasure:
    kind: str  # a key of GEOMETRY_MEASURES


@dataclass(frozen=True)
class ZonalStatistic:
    image: VardaRaster
    band: int
    statistic: str  # a key of STATISTICS


@dataclass(frozen=True)
class Expression:
    text: str


Calculation = GeometryMeasure | ZonalStatistic | Expression


def expressionVariables(collection: ROICollection) -> dict[str, str]:
    """Expression name -> column, for every user column."""
    return {identifier(c): c for c in collection.userColumns}


def parseExpression(text: str, variables: set[str]) -> ast.Expression:
    """Parse and check a column expression; raises ExpressionError with the reason."""
    return parse(
        text,
        variables.__contains__,
        FUNCTIONS,
        CONSTANTS,
        unknownName="unknown column '{}'",
    )


def _evaluate(tree: ast.AST, values: dict[str, float]) -> float:
    return float(evaluate(tree, values, FUNCTIONS, CONSTANTS))


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def mapUnits(collection: ROICollection) -> str | None:
    """The collection's linear unit (e.g. "metre"), or None in pixel space."""
    crs = collection.crs
    if crs is None:
        return None
    axes = CRS.from_user_input(crs).axis_info  # rasterio or pyproj CRS
    return axes[0].unit_name if axes else "units"


def suggestName(calculation: Calculation, units: str | None = None) -> str:
    if isinstance(calculation, GeometryMeasure):
        short = GEOMETRY_MEASURES[calculation.kind][1]
        if calculation.kind in ("area_map", "perimeter_map") and units in (
            "metre",
            "meter",
        ):
            return f"{short}_m2" if calculation.kind == "area_map" else f"{short}_m"
        return short
    if isinstance(calculation, ZonalStatistic):
        band = words(str(calculation.image.bandNames[calculation.band])).lower()
        return f"{calculation.statistic}_{band[:20]}"
    return "calc"


def pixelSize(transform: Affine) -> float:
    """The smaller side of a pixel, in map units: the densify step."""
    return min(
        math.hypot(transform.a, transform.d), math.hypot(transform.b, transform.e)
    )


@dataclass(frozen=True)
class BodyValue:
    """One ROI's area or perimeter on the body, and why it is blank."""

    value: float  # km² or km; NaN when it can't be measured
    measurement: Measurement | None
    reason: str | None  # why it is blank


def measureOnBody(collection: ROICollection, kind: str, fid: int) -> BodyValue:
    """The ROI's area (km²) or perimeter (km) on the body, geodesic on the
    CRS's own surface, its edges densified to one pixel (``polygonArea``)."""
    crs = collection.crs
    if crs is None:
        return BodyValue(math.nan, None, NO_MAP_COORDINATES)
    shape = collection.getROI(fid).geometry
    if not isinstance(shape, Polygon | MultiPolygon):
        return BodyValue(math.nan, None, "it is not a polygon")
    try:
        # points PROJ can't invert become NaN, with NumPy's invalid-value warnings
        with np.errstate(invalid="ignore"):
            area, perimeter = polygonArea(crs, shape, pixelSize(collection.transform))
    except ValueError as error:
        return BodyValue(math.nan, None, str(error))
    measurement = area if kind == "area_body" else perimeter
    value = measurement.value * BODY_MEASURES[kind]
    if not math.isfinite(value):
        return BodyValue(
            math.nan,
            None,
            "some of its points are outside where the projection can be converted",
        )
    return BodyValue(value, measurement, None)


def describeOnBody(kind: str, measured: Sequence[tuple[str, BodyValue]]) -> str:
    """How a column of ``kind`` was measured, from its ROIs' (name, value)."""
    found = [v.measurement for _, v in measured if v.measurement is not None]
    parts = [GEOMETRY_MEASURES[kind][0]]
    if found:
        method = found[0].method
        parts.append(f"{method[:1].upper()}{method[1:]}, edges densified to 1 pixel")
        differences = [m.bodyModelDifference for m in found if m.reference]
        if differences:
            parts.append(
                "On the body's IAU 2015 reference ellipsoid: up to "
                f"{max(differences) * 100:.2g}% different"
            )
    reasons: dict[str, list[str]] = {}
    for name, value in measured:
        if value.reason is not None:
            reasons.setdefault(value.reason, []).append(name)
    for reason, names in reasons.items():
        parts.append(f"Blank for {', '.join(names)}: {reason}")
    return ". ".join(parts)


def describe(calculation: Calculation) -> str:
    if isinstance(calculation, GeometryMeasure):
        return GEOMETRY_MEASURES[calculation.kind][0]
    if isinstance(calculation, ZonalStatistic):
        band = calculation.image.bandNames[calculation.band]
        return f"{calculation.statistic} of {band} in {calculation.image.name}"
    return f"= {calculation.text}"


class CalculatedColumns:
    """The collection's calculated columns, recalculated as ROIs change."""

    def __init__(self, collection: ROICollection):
        self.collection = collection
        self._columns: dict[str, Calculation] = {}
        # body measures' last values per kind and ROI, for the descriptions
        self._onBody: dict[str, dict[int, BodyValue]] = {}
        self._updating = False
        collection.sigROIAdded.connect(self._onROIChanged)
        collection.sigROIsAdded.connect(self._onROIsAdded)
        collection.sigROIUpdated.connect(self._onROIChanged)
        collection.sigColumnsChanged.connect(self._onColumnsChanged)
        collection.sigGeoreferenceChanged.connect(self._onGeoreferenceChanged)

    def add(self, name: str, calculation: Calculation) -> str:
        """Add a calculated column; returns its name as stored."""
        if isinstance(calculation, Expression):
            parseExpression(calculation.text, set(expressionVariables(self.collection)))
        self.collection.addColumn(name)
        column = name.strip().lower()
        self._columns[column] = calculation
        self.recalculate(columns=[column])
        return column

    def isCalculated(self, column: str) -> bool:
        return column in self._columns

    def describe(self, column: str) -> str | None:
        calculation = self._columns.get(column)
        if calculation is None:
            return None
        if isinstance(calculation, GeometryMeasure) and calculation.kind in (
            BODY_MEASURES
        ):
            known = self._onBody.get(calculation.kind, {})
            measured = [
                (self.collection.getROI(fid).name, known[fid])
                for fid in self.collection.fids
                if fid in known
            ]
            return describeOnBody(calculation.kind, measured)
        return describe(calculation)

    def preview(self, calculation: Calculation) -> float | None:
        """The value for the first ROI, without adding a column."""
        fids = self.collection.fids
        return self._values(calculation, fids[:1])[0] if fids else None

    def recalculate(
        self, columns: Sequence[str] | None = None, fids: Sequence[int] | None = None
    ) -> None:
        fids = list(self.collection.fids if fids is None else fids)
        names = [c for c in self._columns if columns is None or c in columns]
        self._updating = True
        try:
            for column in names:  # in the order added, so expressions see earlier ones
                for fid, value in zip(fids, self._values(self._columns[column], fids)):
                    self.collection.setProperty(fid, column, value)
        finally:
            self._updating = False

    # --- internals -----------------------------------------------------------

    def _values(self, calculation: Calculation, fids: Sequence[int]) -> list[float]:
        if isinstance(calculation, GeometryMeasure):
            return [self._geometry(calculation.kind, fid) for fid in fids]
        if isinstance(calculation, ZonalStatistic):
            return self._zonal(calculation, fids)
        tree = parseExpression(
            calculation.text, set(expressionVariables(self.collection))
        )
        variables = expressionVariables(self.collection)
        results = []
        for fid in fids:
            values = {
                name: _number(self.collection.getProperty(fid, column))
                for name, column in variables.items()
            }
            try:
                results.append(float(_evaluate(tree, values)))
            except (ArithmeticError, ValueError):
                results.append(math.nan)
        return results

    def _geometry(self, kind: str, fid: int) -> float:
        if kind in BODY_MEASURES:
            measured = measureOnBody(self.collection, kind, fid)
            self._onBody.setdefault(kind, {})[fid] = measured
            return measured.value
        if kind in MAP_MEASURES and self.collection.crs is not None:
            shape = self.collection.getROI(fid).geometry
        else:
            shape = Polygon(self.collection.getPixelCoordinates(fid))
        if kind.startswith("area"):
            return float(shape.area)
        if kind.startswith("perimeter"):
            return float(shape.length)
        centroid = shape.centroid
        return float(centroid.x if kind == "centroid_x" else centroid.y)

    def _zonal(self, calculation: ZonalStatistic, fids: Sequence[int]) -> list[float]:
        raw = calculation.image.getBands([calculation.band])
        band = np.ma.filled(np.ma.asarray(raw, dtype=np.float64), np.nan)[:, :, 0]
        statistic = STATISTICS[calculation.statistic]
        results = []
        for fid in fids:
            values = _finite(band[self.collection.getMask(fid, calculation.image)])
            empty = values.size == 0 and calculation.statistic != "count"
            results.append(math.nan if empty else statistic(values))
        return results

    def _onROIChanged(self, fid: int) -> None:
        if not self._updating and self._columns:
            self.recalculate(fids=[fid])

    def _onROIsAdded(self, fids: list[int]) -> None:
        if not self._updating and self._columns:
            self.recalculate(fids=fids)

    def _onGeoreferenceChanged(self) -> None:
        """A new CRS: every map and body measure is read anew."""
        if self._columns:
            self._onBody.clear()
            self.recalculate()

    def _onColumnsChanged(self) -> None:
        present = set(self.collection.userColumns)
        self._columns = {c: k for c, k in self._columns.items() if c in present}
