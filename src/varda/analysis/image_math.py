"""Image math: images combined band by band (add, subtract, multiply,
divide, minimum, maximum) with another image on the same pixel grid or a
number; masks made by thresholds; and masks applied, keeping where the mask
is set, where it isn't, or multiplying by it. No Qt.

A second image is on the same pixel grid, with the image's bands or one
band (applied to every band). A mask may be on another grid: any
georeferenced mask overlapping the image is resampled onto its grid by
nearest neighbor. A mask with the image's band count is applied band by
band; any other mask's first band is applied to every band. No data in
either is no data in the result; so is division by zero. A mask is set where
it's nonzero, and has no say where it has no data (the pixel is dropped).
"""

from __future__ import annotations

import numpy as np
from rasterio.enums import Resampling

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.rasters import sameBandsRaster, singleBandRaster
from varda.common.entities import VardaRaster
from varda.common.grid import (
    ImageChoices,
    onGridOf,
    placeableChoices,
    resamplingRecord,
    resamplingText,
    sameGridChoices,
)
from varda.common.parameter import (
    EnumParameter,
    FloatParameter,
    ImageParameter,
    IntParameter,
    LabeledEnum,
    MultiChoiceParameter,
    Vec2Parameter,
)
from varda.common.vec2 import Vec2

CATEGORY = "Image Math"


def readWithGaps(image: VardaRaster, bands: list[int] | None = None) -> np.ndarray:
    """Bands as float64 (rows, cols, bands), NaN where there's no data."""
    data = np.ma.filled(np.ma.asarray(image.getData(bands), dtype=np.float64), np.nan)
    nodata = image.bandModel.noData
    if nodata is not None and np.isfinite(nodata):
        data[data == nodata] = np.nan
    return data


def partners(images, image: VardaRaster) -> ImageChoices:
    """Images on ``image``'s grid with its bands or one band (and why the
    others aren't)."""
    return sameGridChoices(list(images), image, bandCounts=(1, image.bandCount))


def maskChoices(images, image: VardaRaster) -> ImageChoices:
    """Masks for ``image``: on its grid, or georeferenced and overlapping it
    (resampled by ``maskOnGrid``), with any number of bands."""
    return placeableChoices(list(images), image)


def maskOnGrid(mask: VardaRaster, image: VardaRaster) -> np.ndarray:
    """``mask`` on ``image``'s grid (rows, cols, bands), NaN where it has no
    data: its bands when it has the image's band count, else its first band.
    Resampled by nearest neighbor (values stay as they are, e.g. 0 and 1)."""
    bands = None if mask.bandCount == image.bandCount else [0]
    return onGridOf(mask, image, Resampling.nearest, bands)


class Operation(LabeledEnum):
    ADD = "Add (+)"
    SUBTRACT = "Subtract (−)"
    MULTIPLY = "Multiply (×)"
    DIVIDE = "Divide (÷)"
    MINIMUM = "Minimum"
    MAXIMUM = "Maximum"


OPERATIONS = {
    Operation.ADD: np.add,
    Operation.SUBTRACT: np.subtract,
    Operation.MULTIPLY: np.multiply,
    Operation.DIVIDE: np.divide,
    Operation.MINIMUM: np.minimum,
    Operation.MAXIMUM: np.maximum,
}


class ImageArithmeticAnalysis(Analysis):
    analysisId = "image_arithmetic"
    name = "Image Arithmetic"
    memoryFactor = 3.5
    category = CATEGORY
    description = (
        "Combine the image with another image on the same pixel grid (with the "
        "same bands, or one band applied to every band), or with a number: add, "
        "subtract, multiply, divide, minimum or maximum, band by band. No data "
        "in either, and division by zero, are no data in the result."
    )

    operation = EnumParameter("Operation", Operation)
    second = ImageParameter(
        "Second Image",
        "Another image on the same grid; none to use the number",
        optional=True,
        noneLabel="None (use the number)",
    )
    number = FloatParameter(
        "Number", 1.0, description="Used when there's no second image"
    )

    def prepareFor(self, image: VardaRaster) -> None:
        current = self.second.get()
        self.second.setChoices(lambda: partners(self.context.images, image))
        self.second.setOrClear(
            current if current in partners(self.context.images, image).images else None
        )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        reportProgress(0, "reading")
        first = readWithGaps(image)
        other = self.second.get()
        if (
            other is not None
            and other not in partners(self.context.images, image).images
        ):
            raise ValueError(
                f"{other.name} isn't on {image.name}'s grid with its bands."
            )
        operand = readWithGaps(other) if other is not None else float(self.number.value)
        operation = self.operation.get()
        reportProgress(50, "combining")
        with np.errstate(all="ignore"):
            values = OPERATIONS[operation](first, operand)
        values[~np.isfinite(values)] = np.nan
        what = other.name if other is not None else f"{float(self.number.value):g}"
        label = operation.value.split(" (")[0].lower()
        return sameBandsRaster(
            image,
            values,
            name=f"{image.name} {label} {what}",
            description=f"{image.name}, {label} {what}, band by band",
        )


class ThresholdSide(LabeledEnum):
    INSIDE = "Inside the range"
    OUTSIDE = "Outside the range"


class ThresholdToMaskAnalysis(Analysis):
    analysisId = "threshold_to_mask"
    name = "Threshold to Mask"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "A mask from one band: 1 where its value is inside (or outside) a range, "
        "0 elsewhere, no data where the band has none. Apply it with Apply Mask, "
        "or combine masks with Image Arithmetic (minimum is and, maximum is or)."
    )

    band = IntParameter("Band", 1, range=(1, 10_000), description="1-based")
    limits = Vec2Parameter(
        "Range", Vec2(0.0, 1.0), valueNames=("from", "to"), description="Inclusive"
    )
    side = EnumParameter("Mask Is", ThresholdSide)

    def prepareFor(self, image: VardaRaster) -> None:
        self.band.setRange((1, image.bandCount))
        # start from the band's values (a range of 0-1 means nothing for DN)
        values = readWithGaps(image, [int(self.band.value) - 1])
        if np.isfinite(values).any():
            low, high = float(np.nanmin(values)), float(np.nanmax(values))
            self.limits.range = (Vec2(low, high), Vec2(low, high))
            self.limits.set(Vec2(low, high))

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        band = int(self.band.value) - 1
        reportProgress(0, "reading")
        values = readWithGaps(image, [band])[..., 0]
        low, high = sorted((self.limits.get().x, self.limits.get().y))
        inside = (values >= low) & (values <= high)
        outside = self.side.get() is ThresholdSide.OUTSIDE
        mask = np.where(~inside if outside else inside, 1.0, 0.0)
        mask[np.isnan(values)] = np.nan
        where = "outside" if outside else "inside"
        bandName = (
            image.bandNames[band] if band < len(image.bandNames) else f"band {band + 1}"
        )
        return singleBandRaster(
            image,
            mask,
            name=f"{image.name} mask ({bandName} {where} {low:g}-{high:g})",
            bandName="mask",
            description=f"1 where {bandName} of {image.name} is {where} {low:g}-{high:g}",
        )


class MaskMode(LabeledEnum):
    KEEP_SET = "Keep where the mask is set"
    KEEP_UNSET = "Keep where the mask isn't set (inverse)"
    MULTIPLY = "Multiply by the mask"


class ApplyMaskAnalysis(Analysis):
    analysisId = "apply_mask"
    name = "Apply Mask"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Apply a mask (its first band, or band by band when it has the image's "
        "bands; on another grid it's resampled by nearest neighbor): keep the "
        "pixels where it's set (nonzero) and make the rest no data, keep where "
        "it isn't set, or multiply by it (a weight or a 0/1 mask)."
    )

    mask = ImageParameter(
        "Mask",
        "A mask, e.g. from Threshold to Mask or a Shadow Mask (its first band); "
        "on another grid it's resampled by nearest neighbor",
    )
    mode = EnumParameter("Mode", MaskMode)

    def prepareFor(self, image: VardaRaster) -> None:
        current = self.mask.get()
        candidates = maskChoices(self.context.images, image).images
        self.mask.setChoices(lambda: maskChoices(self.context.images, image))
        if current in candidates:
            self.mask.set(current)

    def unavailableReason(self, image: VardaRaster) -> str:
        if not maskChoices(self.context.images, image).images:
            return (
                "Make or open a mask covering this image first (Threshold to "
                "Mask, Band Math, ROI to Mask, or Shadow Mask)."
            )
        return ""

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        mask = self.mask.get()
        if mask is None or mask not in maskChoices(self.context.images, image).images:
            raise ValueError(self.unavailableReason(image) or "Choose the mask.")
        reportProgress(0, "reading")
        values = readWithGaps(image)
        weights = maskOnGrid(mask, image)
        mode = self.mode.get()
        if mode is MaskMode.MULTIPLY:
            out = values * weights
        else:
            isSet = np.where(np.isnan(weights), False, weights != 0)
            keep = isSet if mode is MaskMode.KEEP_SET else ~isSet
            keep &= ~np.isnan(weights)  # a mask without data has no say
            out = np.where(keep, values, np.nan)
        how = {
            MaskMode.KEEP_SET: "masked by",
            MaskMode.KEEP_UNSET: "masked outside",
            MaskMode.MULTIPLY: "times",
        }[mode]
        resampled = resamplingText(mask, image, Resampling.nearest)
        record = resamplingRecord(mask, image, Resampling.nearest)
        return sameBandsRaster(
            image,
            out,
            name=f"{image.name} {how} {mask.name}",
            description=f"{image.name} {how} {mask.name} ({mode.value})"
            + (f"; {resampled}" if resampled else ""),
            extraMetadata={"maskResampling": record} if record else None,
        )


class RoiToMaskAnalysis(Analysis):
    analysisId = "roi_to_mask"
    name = "ROI to Mask"
    memoryFactor = 1.5
    category = CATEGORY
    needsRois = True
    description = (
        "A mask from the workspace's ROIs: 1 inside the ROIs of the classes you "
        "tick, 0 elsewhere. Apply it with Apply Mask, or use it in Band Math."
    )

    classes = MultiChoiceParameter(
        "ROI Classes", description="The classes inside the mask"
    )

    def prepareFor(self, image: VardaRaster) -> None:
        rois = self.context.rois
        names = list(rois.classes()) if rois is not None else []
        kept = [n for n in self.classes.value if n in names]
        self.classes.setChoices(names, selected=kept or names)

    def unavailableReason(self, image: VardaRaster) -> str:
        rois = self.context.rois
        if rois is None or not len(rois):
            return "Draw an ROI first (or make one from a threshold or a mask)."
        if not self.classes.value:
            return "Tick at least one ROI class."
        return ""

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        rois = self.context.rois
        if rois is None:
            raise ValueError("ROI to Mask needs the workspace's ROIs.")
        names = list(self.classes.value)
        inside = np.zeros((image.height, image.width), bool)
        for name in names:
            inside |= rois.classMask(name, image)
        return singleBandRaster(
            image,
            inside.astype(np.float32),
            name=f"{image.name} mask ({' + '.join(names)})",
            bandName="mask",
            description=f"1 inside the ROIs of {', '.join(names)} on {image.name}",
        )
