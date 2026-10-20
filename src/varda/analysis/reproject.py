"""Processing → Geometry → Reproject Image (spec §4.3): an image onto a
recommended projection, another image's grid, or a coordinate system the
user names, with GDAL's warper (``geo.reproject``), a companion quality
image and a ``reprojection`` record. No Qt."""

from __future__ import annotations

import math
import warnings

import numpy as np
from attrs import frozen
from pyproj import CRS
from pyproj.exceptions import CRSError
from rasterio.enums import Resampling

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.bandwise import readBand
from varda.analysis.class_tools import isClassMap
from varda.analysis.rasters import bandMetadata
from varda.common.entities import VardaRaster
from varda.common.grid import placeableChoices
from varda.common.parameter import (
    BoolParameter,
    EnumParameter,
    FloatParameter,
    ImageParameter,
    LabeledEnum,
    StringParameter,
)
from varda.geo.describe import describeCrs
from varda.geo.equivalence import canTransform, sameCrs
from varda.geo.recommend import Purpose, footprintLonLat, recommendProjection
from varda.geo.reproject import (
    TargetGrid,
    gridFor,
    imageGrid,
    reprojectBand,
    reprojectionRecord,
    scaleChange,
    sourcePixelDistance,
    sourceResolution,
)
from varda.geo.transform import transformBounds
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.utilities.proj_lock import PROJ_LOCK

NO_MAP_COORDINATES = (
    "It has no map coordinates, so it can't be reprojected; set its coordinate "
    "system in Image Properties first."
)
QUALITY_BANDS = ["Source pixel distance", "Valid"]
# sourcePixelDistance's temporaries: at most 262,144 output pixels at once,
# about 16 float64 arrays of that size
_CHUNK_BYTES = 262_144 * 8 * 16
# one-time costs that don't grow with the image (PROJ, GDAL's warper)
_FIXED_BYTES = 8 * 2**20


class ReprojectTarget(LabeledEnum):
    RECOMMENDED = "Recommended projection"
    IMAGE = "Another image's grid"
    CRS = "Coordinate system"


class ReprojectExtent(LabeledEnum):
    WHOLE = "Whole image"
    OTHER = "Another image's extent"


class ResamplingMethod(LabeledEnum):
    NEAREST = "Nearest neighbor"
    BILINEAR = "Bilinear"
    CUBIC = "Cubic"
    AVERAGE = "Average"
    MODE = "Mode"


GDAL_RESAMPLING = {
    ResamplingMethod.NEAREST: Resampling.nearest,
    ResamplingMethod.BILINEAR: Resampling.bilinear,
    ResamplingMethod.CUBIC: Resampling.cubic,
    ResamplingMethod.AVERAGE: Resampling.average,
    ResamplingMethod.MODE: Resampling.mode,
}


def defaultResampling(image: VardaRaster) -> ResamplingMethod:
    """Nearest neighbor for class maps and single-band integer images (masks,
    bit masks), whose values mustn't be mixed; bilinear otherwise."""
    if isClassMap(image) or "classes" in image.extraMetadata:
        return ResamplingMethod.NEAREST
    if image.bandCount == 1 and image.dtype.kind in "iub":
        return ResamplingMethod.NEAREST
    return ResamplingMethod.BILINEAR


@frozen
class _Plan:
    """Where a run puts the image."""

    grid: TargetGrid
    resolutionM: float  # ground meters per output pixel at its center
    note: str  # why this target ("Polar stereographic: …")
    copy: bool  # the target is the image's own grid: nothing resampled


class ReprojectAnalysis(Analysis):
    analysisId = "reproject"
    name = "Reproject Image"
    category = "Geometry"
    software = ("rasterio", "pyproj")
    # the estimate is computed from the output grid (estimateMemory)
    memoryFactor = 6.0
    description = (
        "Put the image on another map projection with GDAL's warper: the "
        "projection Varda recommends for the scene, exactly another image's "
        "grid, or a coordinate system you type. Positions between surfaces "
        "of one body keep their planetocentric longitude and latitude. A "
        "companion quality image gives each output pixel's distance to the "
        "nearest source pixel and where every band has data. Purpose applies "
        "to Recommended projection; Coordinate System to Coordinate system; "
        "Image to Another image's grid and Another image's extent; "
        "Resolution and Extent don't apply to Another image's grid."
    )

    target = EnumParameter(
        "Target",
        ReprojectTarget,
        ReprojectTarget.RECOMMENDED,
        "Where the image goes: Varda's recommendation for the scene, another "
        "image's grid (its CRS, transform and size), or a coordinate system",
    )
    purpose = EnumParameter(
        "Purpose",
        Purpose,
        Purpose.MEASUREMENT,
        "Recommended projection only: what the result is for (see the guide's "
        "recommendation rules)",
    )
    grid = ImageParameter(
        "Image",
        "Another image's grid, or Another image's extent: the image to match "
        "(georeferenced, same body, overlapping)",
        optional=True,
    )
    crsText = StringParameter(
        "Coordinate System",
        "",
        "Coordinate system only: an EPSG or IAU_2015 code, WKT or a PROJ "
        "string (starts as the recommended projection)",
    )
    resampling = EnumParameter(
        "Resampling",
        ResamplingMethod,
        ResamplingMethod.BILINEAR,
        "GDAL's kernel: nearest keeps values (class maps, masks); average or "
        "mode when coarsening",
    )
    resolution = FloatParameter(
        "Resolution",
        0.0,
        range=(0.0, 1e7),
        units="m",
        description="Ground meters per output pixel at the output's center; "
        "0 keeps the source's pixel area",
        showSlider=False,
    )
    extent = EnumParameter(
        "Extent",
        ReprojectExtent,
        ReprojectExtent.WHOLE,
        "The whole image's footprint, or the chosen Image's",
    )
    sourceNoData = StringParameter(
        "Source No-Data",
        "",
        "One more source value to leave out (besides the image's no-data and "
        "NaN); empty for none",
    )
    qualityBands = BoolParameter(
        "Quality Bands",
        True,
        "Also make the quality image (source pixel distance, valid)",
    )

    def prepareFor(self, image: VardaRaster) -> None:
        self.grid.setChoices(lambda: placeableChoices(self.context.images, image))
        self.resampling.set(defaultResampling(image))
        if image.hasGeospatialData:
            self.crsText.set(_recommendedText(image))

    def unavailableReason(self, image: VardaRaster) -> str:
        if not image.hasGeospatialData:
            return NO_MAP_COORDINATES
        return ""

    def estimateMemory(self, image: VardaRaster) -> int:
        """Two source bands as float64, the float32 output cube, and three
        float64 output planes for the quality image (spec §4.3)."""
        cube = image.height * image.width * image.bandCount * 8
        try:
            plan = self._plan(image)
        except ValueError:
            # the run reports the real error
            return cube * 6
        pixels = plan.grid.width * plan.grid.height
        return (
            2 * image.height * image.width * 8
            + pixels * image.bandCount * 4
            + 3 * pixels * 8
            + min(_CHUNK_BYTES, pixels * 8 * 16)
            + _FIXED_BYTES
        )

    def runAll(
        self, image: VardaRaster, reportProgress: ProgressCallback
    ) -> list[VardaRaster]:
        result = self.run(image, reportProgress)
        if not self.qualityBands.value:
            return [result]
        return [result, self._quality(image, result, reportProgress)]

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        if not image.hasGeospatialData:
            raise ValueError(NO_MAP_COORDINATES)
        plan = self._plan(image)
        grid, crs = plan.grid, image.crs
        assert crs is not None  # hasGeospatialData
        extra = self._extraNoData()
        method = self.resampling.value
        cube = np.empty((grid.height, grid.width, image.bandCount), np.float32)
        for index in range(image.bandCount):
            reportProgress(int(90 * index / image.bandCount), f"band {index + 1}")
            band = readBand(image, index)
            if extra is not None:
                band[band == extra] = np.nan
            cube[:, :, index] = (
                band
                if plan.copy
                else reprojectBand(
                    band, crs, image.transform, grid, GDAL_RESAMPLING[method]
                )
            )
        reportProgress(90, "recording")
        scale = scaleChange(crs, image.transform, image.width, image.height, grid)
        record = reprojectionRecord(
            crs, grid, GDAL_RESAMPLING[method], plan.resolutionM, scale=scale
        )
        target = describeCrs(grid.crs).text
        if plan.copy:
            what = f"{image.name} already on this grid; nothing resampled ({target})"
        else:
            what = (
                f"{image.name} reprojected to {target} at "
                f"{plan.resolutionM:.6g} m/px ({method.value.lower()})"
            )
        metadata: dict = {**bandMetadata(image), "reprojection": record}
        for key in ("classes", "classColors"):
            if key in image.extraMetadata:
                metadata[key] = image.extraMetadata[key]
        source = ArrayDataSource(
            cube,
            wavelengths=np.asarray(image.wavelengths),
            wavelengthUnits=image.wavelengthUnits,
            bandNames=list(image.bandNames),
            transform=grid.affine,
            crs=_parsed(grid.crs),
            defaultBands=np.asarray(image.defaultBands),
            extraMetadata=metadata,
            description=f"{what}; {plan.note}" if plan.note else what,
        )
        return VardaRaster(source, name=f"{image.name} reprojected")

    # --- the target ---------------------------------------------------------------

    def _plan(self, image: VardaRaster) -> _Plan:
        """The output grid for the current settings. Raises ValueError with
        a user-facing message."""
        crs = image.crs
        if crs is None or not image.hasGeospatialData:
            raise ValueError(NO_MAP_COORDINATES)
        transform, width, height = image.transform, image.width, image.height
        own = imageGrid(crs, transform, width, height)
        if self.target.value is ReprojectTarget.IMAGE:
            other = self._otherImage(image, "Another image's grid")
            assert other.crs is not None  # _otherImage checked it
            reason = canTransform(crs, other.crs)
            if reason is not None:
                raise ValueError(reason)
            grid = imageGrid(other.crs, other.transform, other.width, other.height)
            return _Plan(
                grid,
                _resolutionOf(grid),
                f"matched to the grid of {other.name}",
                _isGrid(own, grid),
            )
        if self.target.value is ReprojectTarget.RECOMMENDED:
            target, note = _recommended(image, self.purpose.value)
        else:
            target, note = _parsed(self._typedText()), "the coordinate system chosen"
        reason = canTransform(crs, target)
        if reason is not None:
            raise ValueError(reason)
        resolution = float(self.resolution.value)
        if self.extent.value is ReprojectExtent.OTHER:
            other = self._otherImage(image, "Another image's extent")
            reason = canTransform(other.crs, target)
            if reason is not None:
                raise ValueError(reason)
            assert other.crs is not None  # canTransform found its body
            extent = transformBounds(
                other.crs, target, other.transform, other.width, other.height
            )
        else:
            extent = None
            if resolution == 0 and sameCrs(crs, target):
                # already there: keep the image's own grid (not one snapped
                # outward, which would resample it by a fraction of a pixel)
                return _Plan(
                    own, sourceResolution(crs, transform, width, height), note, True
                )
        grid = gridFor(
            crs, transform, width, height, target, resolution=resolution, extent=extent
        )
        effective = resolution or sourceResolution(crs, transform, width, height)
        return _Plan(grid, effective, note, _isGrid(own, grid))

    def _otherImage(self, image: VardaRaster, use: str) -> VardaRaster:
        other = self.grid.value
        if other is None:
            raise ValueError(f"Choose the Image whose grid or extent to use ({use}).")
        if not other.hasGeospatialData:
            raise ValueError(f"{other.name} has no map coordinates.")
        return other

    def _typedText(self) -> str:
        text = self.crsText.value.strip()
        if not text:
            raise ValueError(
                "Enter a coordinate system: an EPSG or IAU_2015 code, WKT or a "
                "PROJ string."
            )
        return text

    def _extraNoData(self) -> float | None:
        text = self.sourceNoData.value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            raise ValueError(
                f"Source No-Data must be a number or empty, not “{text}”."
            ) from None

    # --- quality -------------------------------------------------------------------

    def _quality(
        self, image: VardaRaster, result: VardaRaster, reportProgress: ProgressCallback
    ) -> VardaRaster:
        """``<name> reprojection quality``: each output pixel's distance in
        source pixels to the nearest source pixel center, and 1 where every
        band has data."""
        reportProgress(95, "quality bands")
        crs, resultCrs = image.crs, result.crs
        assert crs is not None and resultCrs is not None  # run checked them
        grid = imageGrid(resultCrs, result.transform, result.width, result.height)
        distance = sourcePixelDistance(
            crs, image.transform, image.width, image.height, grid
        )
        cube = np.asarray(result.getData(), dtype=np.float32)
        valid = np.isfinite(cube).all(axis=2)
        source = ArrayDataSource(
            np.stack([distance, valid.astype(np.float32)], axis=2),
            wavelengths=np.array(QUALITY_BANDS),
            wavelengthUnits="",
            bandNames=list(QUALITY_BANDS),
            transform=result.transform,
            crs=result.crs,
            description=(
                f"Reprojection quality of {result.name}: Source pixel distance "
                "(source pixels from each output pixel's center to the nearest "
                "source pixel center; NaN outside the source) and Valid (1 where "
                "every band has data)"
            ),
        )
        return VardaRaster(source, name=f"{image.name} reprojection quality")


def _parsed(text: str) -> CRS:
    """The CRS ``text`` names. Raises ValueError with a user-facing message."""
    try:
        with PROJ_LOCK:
            return CRS.from_user_input(text)
    except CRSError as error:
        raise ValueError(
            f"Varda can't read the coordinate system “{text[:80]}” ({error}). "
            "Enter an EPSG or IAU_2015 code, WKT or a PROJ string."
        ) from None


def _recommended(image: VardaRaster, purpose: Purpose) -> tuple[CRS, str]:
    """The recommended projection for ``image`` and why. Raises ValueError
    (a CRS on no body) with a user-facing message."""
    crs = image.crs
    assert crs is not None
    try:
        footprint = footprintLonLat(crs, image.transform, image.width, image.height)
        recommendation = recommendProjection(crs, footprint, purpose, [crs])
    except ValueError as error:
        raise ValueError(
            f"Varda can't recommend a projection for {image.name} ({error}). "
            "Choose Coordinate system or Another image's grid as the Target."
        ) from None
    return _parsed(recommendation.wkt), recommendation.reason


def _recommendedText(image: VardaRaster) -> str:
    """The Measurement recommendation as a PROJ string (as WKT when it keeps
    the image's own CRS, which a PROJ string could lose), or "" when there is
    none."""
    crs = image.crs
    assert crs is not None
    try:
        footprint = footprintLonLat(crs, image.transform, image.width, image.height)
        recommendation = recommendProjection(crs, footprint, Purpose.MEASUREMENT, [crs])
    except ValueError:
        # a CRS on no body: run explains it for this target
        return ""
    if recommendation.keptExisting:
        return recommendation.wkt
    with PROJ_LOCK, warnings.catch_warnings():
        # the recommendation was made from a PROJ string: nothing is lost
        warnings.simplefilter("ignore", UserWarning)
        return CRS.from_wkt(recommendation.wkt).to_proj4()


def _resolutionOf(grid: TargetGrid) -> float:
    try:
        return sourceResolution(grid.crs, grid.affine, grid.width, grid.height)
    except ValueError:
        return math.nan


def _isGrid(own: TargetGrid, grid: TargetGrid) -> bool:
    """Whether ``grid`` is the image's own grid (``own``)."""
    return (
        (own.width, own.height) == (grid.width, grid.height)
        and np.allclose(own.transform, grid.transform, rtol=1e-12, atol=1e-9)
        and sameCrs(own.crs, grid.crs)
    )
