"""Geometry: resample (resize), rotate or flip, and subset an image, keeping
its georeferencing right (the transform follows the pixels) and, for a
subset, its band metadata."""

from __future__ import annotations


import numpy as np
from affine import Affine
from PyQt6.QtWidgets import QWidget
from scipy import ndimage

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.bandwise import fillGaps, readBand
from varda.analysis.crop_preview import CropPreview
from varda.analysis.rasters import bandMetadataSubset, sameBandsRaster
from varda.common.entities import VardaRaster
from varda.common.parameter import (
    BoolParameter,
    LabeledEnum,
    MultiChoiceParameter,
    EnumParameter,
    FloatParameter,
    IntParameter,
    StringParameter,
    parsePositions,
)
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

CATEGORY = "Geometry"


def readCubeWithGaps(image: VardaRaster) -> np.ndarray:
    """The whole image as float32, NaN where it's no-data."""
    cube = np.ma.filled(np.ma.asarray(image.getData(), dtype=np.float32), np.nan)
    if image.nodata is not None:
        cube[cube == image.nodata] = np.nan
    return cube


class Interpolation(LabeledEnum):
    NEAREST = "Nearest neighbor"
    BILINEAR = "Bilinear"
    CUBIC = "Cubic"


class ResampleAnalysis(Analysis):
    analysisId = "resample"
    software = ("scipy",)
    name = "Resample (Resize)"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Change the number of pixels: a factor above 1 enlarges, below 1 "
        "shrinks (e.g. 0.5 halves each side). Nearest neighbor keeps the "
        "original values (use it for class maps); bilinear and cubic "
        "interpolate (anti-aliased when shrinking, with a Gaussian as in "
        "scikit-image's resize). The georeferencing is updated to the new "
        "pixel size."
    )

    factor = FloatParameter(
        "Scale Factor",
        0.5,
        range=(0.01, 20.0),
        description="New size over old size, both sides",
        step=0.05,
    )
    interpolation = EnumParameter(
        "Interpolation", Interpolation, Interpolation.BILINEAR
    )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        factor = float(self.factor.value)
        rows = max(int(round(image.height * factor)), 1)
        cols = max(int(round(image.width * factor)), 1)
        zoom = (rows / image.height, cols / image.width)
        order = {
            Interpolation.NEAREST: 0,
            Interpolation.BILINEAR: 1,
            Interpolation.CUBIC: 3,
        }[self.interpolation.value]
        # Anti-aliasing when shrinking, as scikit-image's resize: a Gaussian
        # of σ = (old / new − 1) / 2 old pixels along each shrunk axis
        sigma = tuple(max(0.0, (1.0 / z - 1.0) / 2.0) for z in zoom) if order else None
        output = np.empty((rows, cols, image.bandCount), np.float32)
        for index in range(image.bandCount):
            reportProgress(int(100 * index / image.bandCount), f"band {index + 1}")
            filled, missing = fillGaps(readBand(image, index))
            if sigma is not None and any(sigma):
                filled = ndimage.gaussian_filter(filled, sigma, mode="nearest")
            # grid_mode: pixels are areas, so the corners stay where they were
            band = ndimage.zoom(
                filled, zoom, order=order, mode="nearest", grid_mode=True
            )
            gaps = ndimage.zoom(missing, zoom, order=0, mode="nearest", grid_mode=True)
            output[:, :, index] = np.where(gaps, np.nan, band)
        transform = image.transform * Affine.scale(
            image.width / cols, image.height / rows
        )
        return sameBandsRaster(
            image,
            output,
            name=f"{image.name} ×{factor:g}",
            description=(
                f"{image.name} resampled to {rows} × {cols} pixels: rows "
                f"×{zoom[0]:.6g}, columns ×{zoom[1]:.6g} (factor {factor:g} "
                f"asked, pixel counts rounded; "
                f"{self.interpolation.value.value.lower()}"
                + (
                    f", Gaussian anti-aliasing σ {sigma[0]:.3g} × {sigma[1]:.3g} old pixels"
                    if sigma is not None and any(sigma)
                    else ""
                )
                + ")"
            ),
            transform=transform,
        )


class Orientation(LabeledEnum):
    ROTATE_90_CLOCKWISE = "Rotate 90° clockwise"
    ROTATE_180 = "Rotate 180°"
    ROTATE_90_COUNTERCLOCKWISE = "Rotate 90° counterclockwise"
    FLIP_LEFT_RIGHT = "Flip left-right"
    FLIP_UP_DOWN = "Flip up-down"


def reorient(cube: np.ndarray, how: Orientation) -> tuple[np.ndarray, Affine]:
    """The rotated or flipped cube, and the affine from its pixel corners to
    the original's (so ``old_transform * affine`` georeferences it)."""
    rows, cols = cube.shape[:2]
    match how:
        case Orientation.ROTATE_90_CLOCKWISE:
            return np.rot90(cube, -1), Affine(0, 1, 0, -1, 0, rows)
        case Orientation.ROTATE_180:
            return np.rot90(cube, 2), Affine(-1, 0, cols, 0, -1, rows)
        case Orientation.ROTATE_90_COUNTERCLOCKWISE:
            return np.rot90(cube, 1), Affine(0, -1, cols, 1, 0, 0)
        case Orientation.FLIP_LEFT_RIGHT:
            return cube[:, ::-1], Affine(-1, 0, cols, 0, 1, 0)
        case Orientation.FLIP_UP_DOWN:
            return cube[::-1], Affine(1, 0, 0, 0, -1, rows)
    raise ValueError(f"Unknown orientation {how}")


class RotateFlipAnalysis(Analysis):
    analysisId = "rotate_flip"
    software = ("scipy",)
    name = "Rotate / Flip"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Rotate the image by quarter turns or mirror it. The georeferencing "
        "follows, so every pixel keeps its place on the map."
    )

    orientation = EnumParameter(
        "Operation", Orientation, Orientation.ROTATE_90_CLOCKWISE
    )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        reportProgress(0, "reading image")
        cube, toOriginal = reorient(readCubeWithGaps(image), self.orientation.value)
        how = self.orientation.value.value
        return sameBandsRaster(
            image,
            np.ascontiguousarray(cube),
            name=f"{image.name} {how.lower()}",
            description=f"{image.name}, {how.lower()}",
            transform=image.transform * toOriginal,
        )


def crop(
    image: VardaRaster,
    top: int,
    left: int,
    height: int,
    width: int,
    *,
    bands: list[int] | None = None,
    outside: np.ndarray | None = None,
    name: str = "",
    description: str = "",
) -> VardaRaster:
    """Rows ``top``.. and columns ``left``.. (0-based) of ``image``, optionally
    only some ``bands``; pixels where ``outside`` (a mask over the crop) is
    True become no-data (NaN). Wavelengths, band metadata, display bands and
    the georeferencing follow."""
    bands = list(range(image.bandCount)) if bands is None else bands
    data = image.getData(bandIndices=bands, window=(top, left, height, width))
    cube = np.ma.filled(np.ma.asarray(data, dtype=np.float32), np.nan)
    if image.nodata is not None:
        cube[cube == image.nodata] = np.nan
    if outside is not None:
        cube[outside] = np.nan
    default = [
        bands.index(b) for b in np.asarray(image.defaultBands).ravel() if b in bands
    ]
    source = ArrayDataSource(
        cube,
        wavelengths=np.asarray(image.wavelengths)[bands],
        wavelengthUnits=image.wavelengthUnits,
        bandNames=[image.bandNames[b] for b in bands],
        transform=image.transform * Affine.translation(left, top),
        crs=image.crs,
        defaultBands=np.array(default[:3]) if len(default) >= 3 else None,
        extraMetadata=bandMetadataSubset(image, bands),
        description=description
        or f"Crop of {image.name}: rows {top + 1}-{top + height}, columns {left + 1}-{left + width}",
    )
    return VardaRaster(source, name=name or f"{image.name} crop")


class SubsetAnalysis(Analysis):
    analysisId = "subset"
    name = "Subset (Crop Rows, Columns, Bands)"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Keep part of the image: drag the box on the preview (or type the "
        "rows and columns, 1-based and inclusive) and drag ranges on its "
        "spectrum (or type bands like 1-100, 150-200). Wavelengths, band "
        "names, bad bands and the georeferencing follow."
    )

    firstRow = IntParameter("First Row", 1, range=(1, 1_000_000), description="1-based")
    lastRow = IntParameter(
        "Last Row", 1, range=(1, 1_000_000), description="1-based, inclusive"
    )
    firstColumn = IntParameter(
        "First Column", 1, range=(1, 1_000_000), description="1-based"
    )
    lastColumn = IntParameter(
        "Last Column", 1, range=(1, 1_000_000), description="1-based, inclusive"
    )
    bands = StringParameter("Bands", "", "e.g. 1-100, 150-200 (empty keeps all)")

    def prepareFor(self, image: VardaRaster) -> None:
        # the whole image to start, with ranges (and sliders) that fit it
        self.firstRow.setRange((1, image.height), value=1)
        self.lastRow.setRange((1, image.height), value=image.height)
        self.firstColumn.setRange((1, image.width), value=1)
        self.lastColumn.setRange((1, image.width), value=image.width)

    def createPreviewWidget(self, image: VardaRaster) -> QWidget | None:
        """The image with a box for the rows and columns, and the box's
        mean spectrum with ranges for the bands."""
        return CropPreview(image, self)

    def bandList(self, image: VardaRaster) -> list[int]:
        text = self.bands.value.strip()
        return (
            parsePositions(text, image.bandCount)
            if text
            else list(range(image.bandCount))
        )

    def unavailableReason(self, image: VardaRaster) -> str:
        if not (1 <= self.firstRow.value <= self.lastRow.value <= image.height):
            return f"Rows must run from 1 to {image.height}, first before last."
        if not (1 <= self.firstColumn.value <= self.lastColumn.value <= image.width):
            return f"Columns must run from 1 to {image.width}, first before last."
        if not self.bandList(image):
            return f"No bands between 1 and {image.bandCount} in {self.bands.value!r}."
        return ""

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        top, bottom = int(self.firstRow.value) - 1, int(self.lastRow.value)
        left, right = int(self.firstColumn.value) - 1, int(self.lastColumn.value)
        bands = self.bandList(image)
        reportProgress(0, "reading subset")
        return crop(
            image,
            top,
            left,
            bottom - top,
            right - left,
            bands=bands,
            name=f"{image.name} subset",
            description=(
                f"Subset of {image.name}: rows {top + 1}-{bottom}, columns "
                f"{left + 1}-{right}, {len(bands)} bands"
            ),
        )


class CropToRoiAnalysis(Analysis):
    analysisId = "crop_to_roi"
    name = "Crop to ROI"
    memoryFactor = 1.5
    category = CATEGORY
    needsRois = True
    description = (
        "Crop the image to the ROIs of the classes you tick (their bounding "
        "box), so later processing, a Spectral Hourglass workspace for "
        "instance, runs on just that part. Pixels outside the ROIs can become "
        "no-data, so only the ROIs themselves are processed."
    )

    classes = MultiChoiceParameter(
        "ROI Classes", description="Crop to the ROIs of these classes (all their ROIs)"
    )
    maskOutside = BoolParameter(
        "Outside the ROIs Becomes No-Data",
        True,
        "Keep only the ROIs' pixels; untick to keep the whole bounding box",
    )

    def prepareFor(self, image: VardaRaster) -> None:
        rois = self.context.rois
        names = list(rois.classes()) if rois is not None else []
        kept = [n for n in self.classes.value if n in names]
        self.classes.setChoices(names, selected=kept or names[:1])

    def unavailableReason(self, image: VardaRaster) -> str:
        rois = self.context.rois
        if rois is None or not len(rois):
            return "Draw an ROI on the image first."
        if not self.classes.value:
            return "Tick at least one ROI class."
        return ""

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        rois = self.context.rois
        if rois is None:
            raise ValueError("Crop to ROI needs the workspace's ROIs.")
        names = list(self.classes.value)
        inside = np.zeros((image.height, image.width), bool)
        for name in names:
            inside |= rois.classMask(name, image)
        if not inside.any():
            raise ValueError(
                f"The ROIs of {', '.join(names)} don't cover any of {image.name}."
            )
        rows, cols = (
            np.flatnonzero(inside.any(axis=1)),
            np.flatnonzero(inside.any(axis=0)),
        )
        top, left = int(rows[0]), int(cols[0])
        height, width = int(rows[-1]) - top + 1, int(cols[-1]) - left + 1
        window = inside[top : top + height, left : left + width]
        reportProgress(10, "cropping")
        return crop(
            image,
            top,
            left,
            height,
            width,
            outside=~window if self.maskOutside.value else None,
            name=f"{image.name} {' + '.join(names)}",
            description=f"{image.name} cropped to ROI class {', '.join(names)}",
        )
