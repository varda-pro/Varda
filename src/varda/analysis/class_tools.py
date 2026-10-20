"""Class maps: making them from a band (density slice) and cleaning them up
afterwards (ENVI's post-classification majority/minority analysis, sieve and
clump). A class map is a "Class" band of 0-based class numbers (NaN where
unclassified) with the class names and colors in its metadata, as Varda's
classifiers write."""

from __future__ import annotations

import csv
import io
import logging

import numpy as np
from affine import Affine
from attrs import frozen
from matplotlib import colormaps
from matplotlib.colors import to_hex
from scipy import ndimage

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.bandwise import oddSize, readBand
from varda.analysis.rasters import bandIndex
from varda.common.entities import VardaRaster
from varda.common.parameter import (
    LabeledEnum,
    BoolParameter,
    EnumParameter,
    FloatParameter,
    IntParameter,
)
from varda.geo.measure import imagePixelAreas, pixelAreaMethod
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.version import vardaVersion

logger = logging.getLogger(__name__)

CATEGORY = "Classification"


def isClassMap(image: VardaRaster) -> bool:
    return bool(image.extraMetadata.get("classes")) and image.bandNames[:1] == ["Class"]


def classRaster(
    image: VardaRaster,
    classes: np.ndarray,
    names: list[str],
    colors: list[str],
    *,
    name: str,
    description: str,
) -> VardaRaster:
    """A class map over ``image``'s footprint."""
    source = ArrayDataSource(
        np.asarray(classes, dtype=np.float32)[:, :, None],
        wavelengths=np.array(["Class"]),
        wavelengthUnits="classes",
        bandNames=["Class"],
        transform=image.transform,
        crs=image.crs,
        description=description,
        extraMetadata={"classes": list(names), "classColors": list(colors)},
    )
    return VardaRaster(source, name=name)


UNREFERENCED_METHOD = "pixel counts only: the image has no map coordinates"
# pixels summed at once by classAreas (bounds its temporaries)
_BLOCK_PIXELS = 1_000_000
# pixels whose ground areas classAreas holds at once (float64, 64 MB);
# imagePixelAreas evaluates up to 4,000,000 of them exactly and interpolates
# the rest
_AREA_BLOCK_PIXELS = 8_000_000


@frozen
class ClassArea:
    """One class of a class map: its pixels and their ground area."""

    index: int  # the 0-based class number in the Class band
    name: str
    pixels: int
    areaKm2: float  # NaN without map coordinates (or where PROJ can't invert)
    fraction: float  # of the classified pixels


@frozen
class ClassAreas:
    """Every class's area, and how the areas were measured."""

    rows: tuple[ClassArea, ...]
    method: str  # pixelAreaMethod's sentence, or why there are only counts
    totalKm2: float  # of all classified pixels; NaN as the areas
    # NaN, no-data, non-integer and out-of-range class values
    unclassifiedPixels: int

    @property
    def classifiedPixels(self) -> int:
        """The fractions' denominator."""
        return sum(row.pixels for row in self.rows)


def _countsOnly(error: Exception) -> str:
    logger.warning("Class areas: pixel counts only", exc_info=error)
    return f"pixel counts only: {error}"


def _classRows(image: VardaRaster, top: int, rows: int) -> np.ndarray:
    """``rows`` rows of the Class band from ``top``, as float32 (class
    numbers are small integers), NaN where they're no-data."""
    data = image.getData(bandIndices=[0], window=(top, 0, rows, image.width))
    band = np.ma.filled(np.ma.asarray(data, dtype=np.float32)[:, :, 0], np.nan)
    if image.nodata is not None:  # not in place: band may be the source's
        band = np.where(band == image.nodata, np.float32(np.nan), band)
    return band


def classAreas(image: VardaRaster) -> ClassAreas:
    """The pixel count, ground area and fraction of each class of a class
    map: each classified pixel's ground area (``geo.measure.imagePixelAreas``,
    on the CRS's own surface) summed per class. A pixel counts whole for its
    class; NaN, no-data, non-integer and out-of-range class values are
    unclassified. Without map coordinates (or a CRS Varda can't measure on)
    the areas are NaN and ``method`` says why. Reads the Class band in row
    blocks and holds the areas of at most ``_AREA_BLOCK_PIXELS`` pixels at
    once. Never raises for a class map but for a failed read."""
    names = [str(name) for name in image.extraMetadata.get("classes", [])]
    count = len(names)
    width, height = image.width, image.height
    crs, transform = image.crs, image.transform
    method = UNREFERENCED_METHOD
    measuring = False
    if crs is not None and image.hasGeospatialData:
        try:
            method = pixelAreaMethod(crs, transform)
            measuring = True
        except Exception as error:  # anything PROJ can raise
            method = _countsOnly(error)
    pixels = np.zeros(count, np.int64)
    sums = np.zeros(count)
    areaRows = max(1, _AREA_BLOCK_PIXELS // max(width, 1))
    sumRows = max(1, min(areaRows, _BLOCK_PIXELS // max(width, 1)))
    for top in range(0, height, areaRows):
        bottom = min(top + areaRows, height)
        areas: np.ndarray | None = None
        if measuring and crs is not None:
            try:
                # the block's own grid: its first row is the image's top
                shifted = transform * Affine.translation(0, top)
                areas = imagePixelAreas(crs, shifted, width, bottom - top)
            except Exception as error:  # anything PROJ can raise
                measuring, method = False, _countsOnly(error)
        for start in range(top, bottom, sumRows):
            block = _classRows(image, start, min(sumRows, bottom - start))
            valid = np.isfinite(block) & (block >= 0) & (block < count)
            valid &= block == np.round(block)
            index = block[valid].astype(np.int64)
            pixels += np.bincount(index, minlength=count)
            if areas is not None:
                weights = areas[start - top : start - top + len(block)][valid]
                sums += np.bincount(index, weights=weights, minlength=count)
    if not measuring:
        sums[:] = np.nan
    km2 = sums / 1e6
    total = int(pixels.sum())
    rows = tuple(
        ClassArea(
            index=k,
            name=names[k],
            pixels=int(pixels[k]),
            areaKm2=float(km2[k]),
            fraction=float(pixels[k] / total) if total else 0.0,
        )
        for k in range(count)
    )
    return ClassAreas(
        rows=rows,
        method=method,
        totalKm2=float(km2.sum()),
        unclassifiedPixels=width * height - total,
    )


def _number(value: float) -> str:
    return "" if np.isnan(value) else repr(value)


def classAreasCsv(areas: ClassAreas, imageName: str) -> str:
    """``areas`` as CSV: ``#`` lines naming the image, the method, Varda's
    version, the classified and unclassified pixel counts and the classified
    area, then one row per class (blank areas where there are none)."""
    total = _number(areas.totalKm2) or "unavailable"
    out = io.StringIO()
    out.write(f"# image: {imageName}\n")
    out.write(f"# method: {areas.method}\n")
    out.write(f"# Varda version: {vardaVersion()}\n")
    out.write(
        f"# classified pixels: {areas.classifiedPixels} (the fractions' denominator)\n"
    )
    out.write(f"# unclassified pixels: {areas.unclassifiedPixels}\n")
    out.write(f"# classified area_km2: {total}\n")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["class", "name", "pixels", "area_km2", "fraction"])
    for row in areas.rows:
        writer.writerow(
            [row.index, row.name, row.pixels, _number(row.areaKm2), repr(row.fraction)]
        )
    return out.getvalue()


class ClassMapAnalysis(Analysis):
    """Cleans up a class map: subclasses implement ``clean`` on the class
    band (float, NaN where unclassified) given the number of classes."""

    def unavailableReason(self, image: VardaRaster) -> str:
        if not isClassMap(image):
            return (
                "Choose a class map: the result of a classification or a density slice."
            )
        return ""

    def label(self) -> str:
        return self.name.lower()

    def clean(self, classes: np.ndarray, count: int) -> np.ndarray:
        raise NotImplementedError

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        names = list(image.extraMetadata["classes"])
        colors = list(image.extraMetadata.get("classColors", []))
        reportProgress(0, "reading classes")
        classes = readBand(image, 0)
        reportProgress(20, self.label())
        cleaned = self.clean(classes, len(names))
        return classRaster(
            image,
            cleaned,
            names,
            colors,
            name=f"{image.name} {self.label()}",
            description=f"{self.name} of {image.name}",
        )


def _masks(classes: np.ndarray, count: int) -> list[np.ndarray]:
    return [classes == k for k in range(count)]


class MajorityRule(LabeledEnum):
    MAJORITY = "Majority (most common class)"
    MINORITY = "Minority (least common class)"


class MajorityFilterAnalysis(ClassMapAnalysis):
    analysisId = "majority_filter"
    software = ("scipy",)
    name = "Majority/Minority Filter"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Replace each classified pixel with the most (or least) common class "
        "in a window around it: majority smooths speckle out of a class map, "
        "minority brings out rare classes. Unclassified pixels stay so."
    )

    rule = EnumParameter("Rule", MajorityRule, MajorityRule.MAJORITY)
    size = IntParameter(
        "Window",
        3,
        range=(3, 51),
        units="pixels",
        description="Window width (made odd)",
    )
    centerWeight = IntParameter(
        "Center Weight",
        1,
        range=(1, 50),
        description="How many times the center pixel counts (higher keeps more of the original)",
    )

    def label(self) -> str:
        return f"{self.rule.value.name.lower()} {oddSize(self.size.value)}px"

    def clean(self, classes: np.ndarray, count: int) -> np.ndarray:
        size = oddSize(self.size.value)
        masks = _masks(classes, count)
        counts = np.stack(
            [
                ndimage.uniform_filter(m.astype(np.float64), size, mode="constant")
                * size
                * size
                + (int(self.centerWeight.value) - 1) * m
                for m in masks
            ]
        )
        counts = np.round(counts, 6)
        current = np.nan_to_num(classes, nan=0).astype(int)
        own = np.take_along_axis(counts, current[None], axis=0)[0]
        if self.rule.value is MajorityRule.MAJORITY:
            best = counts.max(axis=0)
            chosen = np.where(own >= best, current, counts.argmax(axis=0))
        else:
            present = np.where(counts > 0, counts, np.inf)
            fewest = present.min(axis=0)
            chosen = np.where(own <= fewest, current, present.argmin(axis=0))
        return np.where(np.isnan(classes), np.nan, chosen)


class Connectivity(LabeledEnum):
    FOUR = "4 (edges)"
    EIGHT = "8 (edges and corners)"


def _structure(connectivity: Connectivity) -> np.ndarray:
    return ndimage.generate_binary_structure(
        2, 1 if connectivity is Connectivity.FOUR else 2
    )


def fillFromNeighbors(classes: np.ndarray, holes: np.ndarray) -> np.ndarray:
    """Give each pixel in ``holes`` the class of its nearest classified pixel."""
    known = ~np.isnan(classes) & ~holes
    if not known.any():
        return classes
    nearest = ndimage.distance_transform_edt(
        ~known, return_distances=False, return_indices=True
    )
    filled = classes.copy()
    filled[holes] = classes[tuple(nearest)][holes]
    return filled


class SieveAnalysis(ClassMapAnalysis):
    analysisId = "sieve"
    software = ("scipy",)
    name = "Sieve Classes"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Remove isolated groups of pixels smaller than a minimum size from a "
        "class map. Removed pixels become unclassified, or take the class of "
        "their nearest neighbor."
    )

    minimum = IntParameter(
        "Minimum Size",
        4,
        range=(2, 100000),
        units="pixels",
        description="Smaller groups are removed",
    )
    connectivity = EnumParameter("Connectivity", Connectivity, Connectivity.EIGHT)
    fill = BoolParameter(
        "Fill With Neighbors",
        False,
        "Give removed pixels their nearest neighbor's class",
    )

    def label(self) -> str:
        return f"sieve {int(self.minimum.value)}px"

    def clean(self, classes: np.ndarray, count: int) -> np.ndarray:
        structure = _structure(self.connectivity.value)
        small = np.zeros(classes.shape, bool)
        for mask in _masks(classes, count):
            labels, found = ndimage.label(mask, structure)
            if not found:
                continue
            sizes = np.bincount(labels.ravel())
            tooSmall = sizes < int(self.minimum.value)
            tooSmall[0] = False  # the background
            small |= tooSmall[labels]
        if self.fill.value:
            return fillFromNeighbors(classes, small)
        return np.where(small, np.nan, classes)


class ClumpAnalysis(ClassMapAnalysis):
    analysisId = "clump"
    software = ("scipy",)
    name = "Clump Classes"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Make class regions spatially coherent: each class is closed "
        "(dilated, then eroded), which fills holes and specks inside it, but "
        "only over unclassified pixels and pixels not part of a solid region "
        "of another class."
    )

    size = IntParameter(
        "Size",
        3,
        range=(3, 51),
        units="pixels",
        description="Operator width (made odd)",
    )

    def label(self) -> str:
        return f"clump {oddSize(self.size.value)}px"

    def clean(self, classes: np.ndarray, count: int) -> np.ndarray:
        element = np.ones((oddSize(self.size.value),) * 2, bool)
        masks = _masks(classes, count)
        solid = np.zeros(classes.shape, bool)
        for mask in masks:
            solid |= ndimage.binary_opening(mask, element)
        result = classes.copy()
        for k, mask in enumerate(masks):
            padded = np.pad(mask, element.shape[0], mode="edge")
            closed = ndimage.binary_closing(padded, element)[
                element.shape[0] : -element.shape[0],
                element.shape[0] : -element.shape[0],
            ]
            result[closed & ~mask & ~solid] = k
        return result


class SliceMode(LabeledEnum):
    EQUAL_INTERVAL = "Equal intervals"
    QUANTILE = "Quantiles (equal pixel counts)"


def _format(value: float) -> str:
    return f"{value:.3g}"


class DensitySliceAnalysis(Analysis):
    analysisId = "density_slice"
    name = "Density Slice"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Turn one band into a class map of value ranges: equal intervals "
        "between a low and a high value (by default its 2-98 % range; values "
        "outside go to the end classes) or quantiles with equal pixel counts. "
        "Classes are colored in order, dark to bright."
    )

    band = IntParameter(
        "Band", 1, range=(1, 10000), description="Band to slice (1-based)"
    )
    slices = IntParameter("Classes", 5, range=(2, 50), description="Number of ranges")
    mode = EnumParameter("Slicing", SliceMode, SliceMode.EQUAL_INTERVAL)
    low = FloatParameter(
        "Low",
        0.0,
        description="Equal intervals: lowest value (with High, 0 and 0 use the 2-98 % range)",
    )
    high = FloatParameter("High", 0.0, description="Equal intervals: highest value")

    def edges(self, values: np.ndarray) -> np.ndarray:
        count = int(self.slices.value)
        if self.mode.value is SliceMode.QUANTILE:
            return np.quantile(values, np.linspace(0, 1, count + 1))
        low, high = float(self.low.value), float(self.high.value)
        if high <= low:
            low, high = np.percentile(values, [2, 98])
        return np.linspace(low, high, count + 1)

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        index = bandIndex(image, int(self.band.value))
        band = readBand(image, index)
        valid = np.isfinite(band)
        if not valid.any():
            raise ValueError(f"Band {index + 1} has no valid values.")
        edges = self.edges(band[valid])
        count = edges.size - 1
        classes = np.full(band.shape, np.nan)
        classes[valid] = np.clip(
            np.searchsorted(edges, band[valid], side="right") - 1, 0, count - 1
        )
        names = [f"{_format(edges[k])} – {_format(edges[k + 1])}" for k in range(count)]
        ramp = colormaps["viridis"]
        colors = [to_hex(ramp(k / max(count - 1, 1))) for k in range(count)]
        return classRaster(
            image,
            classes,
            names,
            colors,
            name=f"{image.name} density slice (band {index + 1})",
            description=f"Density slice of band {index + 1} of {image.name} into {count} ranges",
        )
