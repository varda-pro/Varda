"""Terrain from an elevation band (a DTM): slope, aspect, hillshade,
curvature and roughness, using the image's pixel size from its
georeferencing (ENVI's topographic measures, ArcGIS-style conventions)."""

from __future__ import annotations


import numpy as np
from scipy import ndimage

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.bandwise import fillGaps, readBand
from varda.analysis.rasters import bandIndex, singleBandRaster
from varda.common.entities import VardaRaster
from varda.common.parameter import (
    LabeledEnum,
    EnumParameter,
    FloatParameter,
    IntParameter,
)


class TerrainOutput(LabeledEnum):
    SLOPE = "Slope (degrees)"
    ASPECT = "Aspect (degrees clockwise from north)"
    HILLSHADE = "Hillshade (0-1)"
    CURVATURE = "Curvature (positive = convex)"
    ROUGHNESS = "Roughness (mean height difference to neighbors)"


def hasPixelSize(image: VardaRaster) -> bool:
    """Whether the image's georeferencing gives a pixel size (otherwise
    distances are in pixels)."""
    return not image.transform.is_identity


def pixelSize(image: VardaRaster) -> tuple[float, float]:
    """Ground size of a pixel along columns and rows (1, 1 if not georeferenced)."""
    transform = image.transform
    return float(np.hypot(transform.a, transform.d)) or 1.0, float(
        np.hypot(transform.b, transform.e)
    ) or 1.0


def gridDirections(image: VardaRaster) -> tuple[float, float]:
    """Which way east and north lie on the grid: +1 when columns run east
    and rows run south (up is north), -1 for an axis a map projection flips
    (a south-up or mirrored transform)."""
    if image.crs is None:
        return 1.0, 1.0
    transform = image.transform
    return float(np.sign(transform.a) or 1.0), float(-np.sign(transform.e) or 1.0)


def gradients(
    elevation: np.ndarray,
    dx: float,
    dy: float,
    eastSign: float = 1.0,
    northSign: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Elevation change per ground unit toward east and north (Horn's
    method: the Sobel kernels, which weigh the nearest neighbors double)."""
    east = eastSign * ndimage.sobel(elevation, 1, mode="nearest") / (8.0 * dx)
    north = northSign * -ndimage.sobel(elevation, 0, mode="nearest") / (8.0 * dy)
    return east, north


def slopeAspect(east: np.ndarray, north: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Slope in degrees and aspect, the compass direction the slope faces
    (downhill), in degrees clockwise from north; NaN where flat."""
    slope = np.degrees(np.arctan(np.hypot(east, north)))
    aspect = np.degrees(np.arctan2(-east, -north)) % 360.0
    return slope, np.where((east == 0) & (north == 0), np.nan, aspect)


def illuminationCosine(
    slope: np.ndarray, aspect: np.ndarray, azimuth: float, elevation: float
) -> np.ndarray:
    """cos(i), i the angle between the sun and each slope's normal:
    cos(s) sin(e) + sin(s) cos(e) cos(azimuth - aspect), for slope s and the
    sun's elevation e (all in degrees). Zero or less where a slope faces
    away from the sun (self-shadow); no cast shadows."""
    s, a = np.radians(slope), np.radians(np.nan_to_num(aspect))
    e = np.radians(elevation)
    return np.cos(s) * np.sin(e) + np.sin(s) * np.cos(e) * np.cos(
        np.radians(azimuth) - a
    )


def hillshade(
    slope: np.ndarray, aspect: np.ndarray, azimuth: float, elevation: float
) -> np.ndarray:
    return np.clip(illuminationCosine(slope, aspect, azimuth, elevation), 0.0, 1.0)


class TerrainAnalysis(Analysis):
    analysisId = "terrain"
    software = ("scipy",)
    name = "Terrain (Slope, Aspect, Hillshade)"
    memoryFactor = 1.5
    category = "Topography"
    description = (
        "Measures of the surface from an elevation band (a DTM): slope, "
        "aspect (the compass direction a slope faces), hillshade (lit from "
        "the sun direction given), curvature and roughness. Distances come "
        "from the image's pixel size, so elevations and pixels should share "
        "units (use the Z Factor otherwise)."
    )

    output = EnumParameter("Output", TerrainOutput, TerrainOutput.SLOPE)
    band = IntParameter(
        "Elevation Band",
        1,
        range=(1, 10000),
        description="The band holding elevation (1-based)",
    )
    zFactor = FloatParameter(
        "Z Factor",
        1.0,
        range=(1e-6, 1e6),
        description="Multiplies elevations to put them in the pixel size's units (e.g. 1000 for km over m)",
        step=0.1,
    )
    azimuth = FloatParameter(
        "Sun Azimuth",
        315.0,
        range=(0.0, 360.0),
        units="°",
        description="Hillshade: compass direction of the sun",
        step=15.0,
    )
    sunElevation = FloatParameter(
        "Sun Elevation",
        45.0,
        range=(0.0, 90.0),
        units="°",
        description="Hillshade: sun height above the horizon",
        step=5.0,
    )

    def unavailableReason(self, image: VardaRaster) -> str:
        if image.crs is not None and image.crs.is_geographic:
            return "Its pixels are in degrees: terrain needs a projected image (e.g. meters per pixel)."
        return ""

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        index = bandIndex(image, int(self.band.value))
        reportProgress(0, "reading elevation")
        elevation, missing = fillGaps(readBand(image, index))
        zFactor = float(self.zFactor.value)
        elevation = elevation * zFactor
        zUnits = "elevation units" + (
            f" × {zFactor:g} (Z Factor)" if zFactor != 1.0 else ""
        )
        dx, dy = pixelSize(image)
        perPixel = not hasPixelSize(image)
        eastSign, northSign = gridDirections(image)
        east, north = gradients(elevation, dx, dy, eastSign, northSign)
        reportProgress(50, "computing terrain")
        output = self.output.value
        slope, aspect = slopeAspect(east, north)
        match output:
            case TerrainOutput.SLOPE:
                values = slope
                units = "degrees (rise per pixel)" if perPixel else "degrees"
            case TerrainOutput.ASPECT:
                values, units = aspect, "degrees"
            case TerrainOutput.HILLSHADE:
                values = hillshade(
                    slope,
                    aspect,
                    float(self.azimuth.value),
                    float(self.sunElevation.value),
                )
                units = ""
            case TerrainOutput.CURVATURE:
                d2x = (
                    np.roll(elevation, -1, 1) - 2 * elevation + np.roll(elevation, 1, 1)
                ) / dx**2
                d2y = (
                    np.roll(elevation, -1, 0) - 2 * elevation + np.roll(elevation, 1, 0)
                ) / dy**2
                values = -(d2x + d2y)
                units = f"{zUnits} per pixel²" if perPixel else "per unit"
                values[[0, -1], :] = values[:, [0, -1]] = (
                    np.nan
                )  # no neighbors past the edge
            case TerrainOutput.ROUGHNESS:
                padded = np.pad(elevation, 1, mode="edge")
                rows, cols = elevation.shape
                differences = [
                    np.abs(
                        padded[1 + y : 1 + y + rows, 1 + x : 1 + x + cols] - elevation
                    )
                    for y in (-1, 0, 1)
                    for x in (-1, 0, 1)
                    if (y, x) != (0, 0)
                ]
                values, units = np.mean(differences, axis=0), zUnits
        values = np.asarray(values, dtype=np.float64)
        values[missing] = np.nan
        label = output.value.split(" (")[0]
        return singleBandRaster(
            image,
            values,
            name=f"{image.name} {label.lower()}",
            bandName=label,
            units=units,
            description=f"{label} from band {index + 1} of {image.name}"
            + (f" (Z Factor {zFactor:g})" if zFactor != 1.0 else "")
            + (
                ". The image has no georeferencing, so distances are in "
                "pixels: gradients are per pixel, not per map unit, and "
                "slope treats one pixel as one unit of elevation (× Z Factor)."
                if perPixel
                else ""
            ),
        )
