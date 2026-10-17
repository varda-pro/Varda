"""Color: transforms between RGB and hue/saturation spaces, the
decorrelation stretch and the saturation stretch, on three bands of the
image (its display bands by default)."""

from __future__ import annotations


import numpy as np
from matplotlib.colors import hsv_to_rgb, rgb_to_hsv
from skimage import exposure

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.bandwise import fillGaps, readBand
from varda.analysis.rasters import bandIndex, namedBandsRaster
from varda.common.entities import VardaRaster
from varda.common.parameter import (
    LabeledEnum,
    BoolParameter,
    EnumParameter,
    IntParameter,
)

CATEGORY = "Transforms"


def stretchUnit(values: np.ndarray, low: float = 2.0, high: float = 98.0) -> np.ndarray:
    """Linear stretch of ``low``-``high`` percent to 0-1, clipped."""
    lo, hi = np.percentile(values, [low, high])
    if hi <= lo:
        return np.zeros_like(values)
    return np.clip((values - lo) / (hi - lo), 0.0, 1.0)


def rgbToHls(rgb: np.ndarray) -> np.ndarray:
    hsv = rgb_to_hsv(rgb)
    value, saturationV = hsv[..., 2], hsv[..., 1]
    lightness = value * (1 - saturationV / 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        saturation = np.where(
            (lightness > 0) & (lightness < 1),
            (value - lightness) / np.minimum(lightness, 1 - lightness),
            0.0,
        )
    return np.stack([hsv[..., 0], lightness, np.nan_to_num(saturation)], axis=-1)


def hlsToRgb(hls: np.ndarray) -> np.ndarray:
    hue, lightness, saturation = (np.clip(hls[..., k], 0, 1) for k in range(3))
    value = lightness + saturation * np.minimum(lightness, 1 - lightness)
    with np.errstate(divide="ignore", invalid="ignore"):
        saturationV = np.where(value > 0, 2 * (1 - lightness / value), 0.0)
    return hsv_to_rgb(np.stack([hue, np.nan_to_num(saturationV), value], axis=-1))


class ThreeBandAnalysis(Analysis):
    """Works on a red, green and blue band (1-based), defaulting to the
    image's display bands; subclasses implement ``transform`` on a
    (rows, cols, 3) gap-free float array."""

    red = IntParameter(
        "Red Band", 1, range=(1, 10000), description="Band shown as red (1-based)"
    )
    green = IntParameter(
        "Green Band", 2, range=(1, 10000), description="Band shown as green"
    )
    blue = IntParameter(
        "Blue Band", 3, range=(1, 10000), description="Band shown as blue"
    )

    def prepareFor(self, image: VardaRaster) -> None:
        bands = np.asarray(image.defaultBands).ravel()
        if bands.size >= 3:
            for parameter, index in zip((self.red, self.green, self.blue), bands[:3]):
                parameter.set(int(index) + 1)

    def unavailableReason(self, image: VardaRaster) -> str:
        if image.bandCount < 3:
            return f"{self.name} needs at least three bands."
        return ""

    def label(self) -> str:
        return self.name.lower()

    def outputBandNames(self) -> list[str]:
        return ["Red", "Green", "Blue"]

    def transform(self, rgb: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        indices = [
            bandIndex(image, int(p.value)) for p in (self.red, self.green, self.blue)
        ]
        reportProgress(0, "reading bands")
        bands, missing = [], np.zeros((image.height, image.width), bool)
        for index in indices:
            filled, gaps = fillGaps(readBand(image, index))
            bands.append(filled)
            missing |= gaps
        reportProgress(40, "transforming")
        result = np.asarray(self.transform(np.stack(bands, axis=-1)), dtype=np.float64)
        result[missing] = np.nan
        used = ", ".join(str(i + 1) for i in indices)
        return namedBandsRaster(
            image,
            result,
            self.outputBandNames(),
            name=f"{image.name} {self.label()}",
            description=f"{self.name} of bands {used} of {image.name}",
        )


class ColorSpace(LabeledEnum):
    RGB_TO_HSV = "RGB to HSV"
    RGB_TO_HLS = "RGB to HLS"
    HSV_TO_RGB = "HSV to RGB"
    HLS_TO_RGB = "HLS to RGB"


class ColorTransformAnalysis(ThreeBandAnalysis):
    analysisId = "color_transform"
    name = "Color Transform"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Convert three bands between RGB and hue-saturation spaces. RGB to HSV "
        "or HLS (hue, lightness, saturation) stretches each band to 0-1 first "
        "(2-98 %), like the display; HSV or HLS to RGB expects 0-1 bands, such "
        "as the output of the forward transform."
    )

    direction = EnumParameter("Transform", ColorSpace, ColorSpace.RGB_TO_HSV)

    def label(self) -> str:
        return self.direction.value.value

    def outputBandNames(self) -> list[str]:
        if self.direction.value is ColorSpace.RGB_TO_HSV:
            return ["Hue", "Saturation", "Value"]
        if self.direction.value is ColorSpace.RGB_TO_HLS:
            return ["Hue", "Lightness", "Saturation"]
        return ["Red", "Green", "Blue"]

    def transform(self, rgb: np.ndarray) -> np.ndarray:
        match self.direction.value:
            case ColorSpace.RGB_TO_HSV:
                return rgb_to_hsv(
                    np.stack([stretchUnit(rgb[..., k]) for k in range(3)], -1)
                )
            case ColorSpace.RGB_TO_HLS:
                return rgbToHls(
                    np.stack([stretchUnit(rgb[..., k]) for k in range(3)], -1)
                )
            case ColorSpace.HSV_TO_RGB:
                return hsv_to_rgb(np.clip(rgb, 0.0, 1.0))
            case ColorSpace.HLS_TO_RGB:
                return hlsToRgb(rgb)
        raise ValueError(f"Unknown transform {self.direction.value}")


def decorrelationStretch(rgb: np.ndarray) -> np.ndarray:
    """Remove the correlation between three bands, keeping each band's mean
    and spread: rotate to principal components, equalize their variances,
    rotate back (Gillespie et al. 1986)."""
    pixels = rgb.reshape(-1, 3)
    mean = pixels.mean(axis=0)
    covariance = np.cov(pixels, rowvar=False)
    eigenvalues, vectors = np.linalg.eigh(covariance)
    eigenvalues = np.maximum(eigenvalues, 1e-12 * max(eigenvalues.max(), 1e-300))
    spread = np.sqrt(np.diag(covariance))
    transform = np.diag(spread) @ vectors @ np.diag(eigenvalues**-0.5) @ vectors.T
    return ((pixels - mean) @ transform.T + mean).reshape(rgb.shape)


class DecorrelationStretchAnalysis(ThreeBandAnalysis):
    analysisId = "decorrelation_stretch"
    name = "Decorrelation Stretch"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Exaggerate the color differences between three correlated bands "
        "(Gillespie et al. 1986): their common brightness is suppressed so "
        "subtle spectral differences show as strong colors. Each band keeps "
        "its mean and spread; display the result as RGB."
    )

    def transform(self, rgb: np.ndarray) -> np.ndarray:
        return decorrelationStretch(rgb)


class SaturationStretchAnalysis(ThreeBandAnalysis):
    analysisId = "saturation_stretch"
    software = ("scikit-image",)
    name = "Saturation Stretch"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Boost the color saturation of three bands: they're scaled together "
        "to 0-1, converted to hue, saturation and value, the saturation "
        "equalized to fill its range, and converted back to RGB (0-1)."
    )

    stretchValue = BoolParameter(
        "Stretch Brightness Too",
        False,
        "Also stretch the value (brightness) to fill 0-1",
    )

    def transform(self, rgb: np.ndarray) -> np.ndarray:
        # One stretch for all three bands, so their color differences survive
        hsv = rgb_to_hsv(stretchUnit(rgb, 0.0, 100.0))  # min-max: no clipping
        # Equalized: a linear stretch can lower most pixels' saturation when a
        # few (dark) pixels are very saturated
        hsv[..., 1] = exposure.equalize_hist(hsv[..., 1])
        if self.stretchValue.value:
            hsv[..., 2] = stretchUnit(hsv[..., 2])
        return hsv_to_rgb(hsv)
