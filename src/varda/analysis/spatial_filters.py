"""Spatial filters, applied band by band: smoothing, sharpening, edge
detection, morphology, speckle (adaptive) filters, frequency-domain filters
and a custom kernel. The counterpart of ENVI's convolution, morphology,
adaptive and FFT filters; each family is one menu item with a Method."""

from __future__ import annotations


import numpy as np
from scipy import ndimage
from skimage import feature, filters, restoration

from varda.analysis.bandwise import BandwiseAnalysis, oddSize
from varda.common.entities import VardaRaster
from varda.common.parameter import (
    LabeledEnum,
    BoolParameter,
    EnumParameter,
    FloatParameter,
    IntParameter,
    StringParameter,
)

CATEGORY = "Filtering/Spatial"


def _sizeParameter(default: int = 3, description: str = "") -> IntParameter:
    return IntParameter(
        "Size",
        default,
        range=(1, 101),
        units="pixels",
        description=description or "Window width in pixels (made odd)",
    )


def _sigmaParameter(default: float = 1.0, description: str = "") -> FloatParameter:
    return FloatParameter(
        "Sigma",
        default,
        range=(0.1, 50.0),
        units="pixels",
        description=description or "Standard deviation of the Gaussian, in pixels",
        step=0.1,
    )


def _standardized(band: np.ndarray) -> tuple[np.ndarray, float, float]:
    """The band scaled to zero mean and unit spread, for filters whose
    strength is in data units (so one setting suits any image)."""
    mean, spread = float(band.mean()), float(band.std()) or 1.0
    return (band - mean) / spread, mean, spread


# --- smoothing ------------------------------------------------------------------


class SmoothMethod(LabeledEnum):
    MEAN = "Mean (box)"
    GAUSSIAN = "Gaussian"
    MEDIAN = "Median"
    BILATERAL = "Bilateral (edge-preserving)"
    TOTAL_VARIATION = "Total variation (edge-preserving)"


class SmoothingAnalysis(BandwiseAnalysis):
    analysisId = "smoothing"
    software = ("scipy", "scikit-image")
    name = "Smoothing"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Spatial smoothing (low pass) of each band: mean (box) and Gaussian "
        "blur, median (removes speckle and spikes, keeps edges), and the "
        "edge-preserving bilateral and total-variation filters."
    )

    method = EnumParameter("Method", SmoothMethod, SmoothMethod.GAUSSIAN)
    size = _sizeParameter(3, "Window width in pixels for Mean and Median (made odd)")
    sigma = _sigmaParameter(
        1.0, "Gaussian and Bilateral: spatial standard deviation in pixels"
    )
    strength = FloatParameter(
        "Strength",
        0.3,
        range=(0.01, 2.0),
        description="Total variation: how strongly to smooth (relative to the band's spread)",
        step=0.01,
    )

    def label(self) -> str:
        method = self.method.value
        detail = {
            SmoothMethod.MEAN: f"{oddSize(self.size.value)}×{oddSize(self.size.value)}",
            SmoothMethod.MEDIAN: f"{oddSize(self.size.value)}×{oddSize(self.size.value)}",
            SmoothMethod.GAUSSIAN: f"σ{self.sigma.value:g}",
            SmoothMethod.BILATERAL: f"σ{self.sigma.value:g}",
            SmoothMethod.TOTAL_VARIATION: f"{self.strength.value:g}",
        }[method]
        return f"{method.name.replace('_', ' ').lower()} smooth {detail}"

    def processBand(self, band: np.ndarray) -> np.ndarray:
        size, sigma = oddSize(self.size.value), float(self.sigma.value)
        match self.method.value:
            case SmoothMethod.MEAN:
                return ndimage.uniform_filter(band, size, mode="reflect")
            case SmoothMethod.GAUSSIAN:
                return ndimage.gaussian_filter(band, sigma, mode="reflect")
            case SmoothMethod.MEDIAN:
                return ndimage.median_filter(band, size, mode="reflect")
            case SmoothMethod.BILATERAL:
                scaled, mean, spread = _standardized(band)
                low = scaled.min()
                smoothed = restoration.denoise_bilateral(
                    scaled - low, sigma_spatial=sigma, channel_axis=None
                )
                return (smoothed + low) * spread + mean
            case SmoothMethod.TOTAL_VARIATION:
                scaled, mean, spread = _standardized(band)
                smoothed = restoration.denoise_tv_chambolle(
                    scaled, weight=float(self.strength.value)
                )
                return smoothed * spread + mean
        raise ValueError(f"Unknown method {self.method.value}")


# --- sharpening -----------------------------------------------------------------


class SharpenMethod(LabeledEnum):
    UNSHARP_MASK = "Unsharp mask"
    LAPLACIAN = "Laplacian sharpen"
    HIGH_PASS = "High pass (box)"
    GAUSSIAN_HIGH_PASS = "Gaussian high pass"


class SharpeningAnalysis(BandwiseAnalysis):
    analysisId = "sharpening"
    software = ("scipy", "scikit-image")
    name = "Sharpening"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Sharpen each band, or keep only its fine detail. Unsharp mask and "
        "Laplacian sharpen add detail back to the image; High pass and "
        "Gaussian high pass return just the detail (the image minus a "
        "smoothed copy)."
    )

    method = EnumParameter("Method", SharpenMethod, SharpenMethod.UNSHARP_MASK)
    sigma = _sigmaParameter(
        1.0, "Unsharp mask and Gaussian high pass: blur radius in pixels"
    )
    size = _sizeParameter(3, "High pass: window width in pixels (made odd)")
    amount = FloatParameter(
        "Amount",
        1.0,
        range=(0.0, 10.0),
        description="Unsharp mask and Laplacian sharpen: how much detail to add",
        step=0.1,
    )

    def label(self) -> str:
        return self.method.value.value.split(" (")[0].lower()

    def processBand(self, band: np.ndarray) -> np.ndarray:
        sigma, amount = float(self.sigma.value), float(self.amount.value)
        match self.method.value:
            case SharpenMethod.UNSHARP_MASK:
                return band + amount * (band - ndimage.gaussian_filter(band, sigma))
            case SharpenMethod.LAPLACIAN:
                return band - amount * ndimage.laplace(band, mode="reflect")
            case SharpenMethod.HIGH_PASS:
                return band - ndimage.uniform_filter(band, oddSize(self.size.value))
            case SharpenMethod.GAUSSIAN_HIGH_PASS:
                return band - ndimage.gaussian_filter(band, sigma)
        raise ValueError(f"Unknown method {self.method.value}")


# --- edge detection ---------------------------------------------------------------


class EdgeMethod(LabeledEnum):
    SOBEL = "Sobel"
    PREWITT = "Prewitt"
    SCHARR = "Scharr"
    ROBERTS = "Roberts"
    LAPLACIAN = "Laplacian"
    LAPLACIAN_OF_GAUSSIAN = "Laplacian of Gaussian"
    CANNY = "Canny"
    DIRECTIONAL = "Directional"


class EdgeDetectionAnalysis(BandwiseAnalysis):
    analysisId = "edge_detection"
    software = ("scipy", "scikit-image")
    name = "Edge Detection"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Edges in each band. Sobel, Prewitt, Scharr and Roberts give the "
        "gradient magnitude (band units per pixel); Laplacian and Laplacian of "
        "Gaussian the second derivative; Canny a map of edges (1) and "
        "non-edges (0); Directional the gradient along one compass direction."
    )

    method = EnumParameter("Method", EdgeMethod, EdgeMethod.SOBEL)
    sigma = _sigmaParameter(1.5, "Laplacian of Gaussian and Canny: smoothing in pixels")
    angle = FloatParameter(
        "Direction",
        0.0,
        range=(0.0, 360.0),
        units="°",
        description="Directional: compass direction the gradient is measured toward (0 = up, 90 = right)",
        step=15.0,
    )

    def label(self) -> str:
        method = self.method.value
        if method is EdgeMethod.DIRECTIONAL:
            return f"directional edges {self.angle.value:g}°"
        return f"{method.value.lower()} edges"

    def processBand(self, band: np.ndarray) -> np.ndarray:
        sigma = float(self.sigma.value)
        match self.method.value:
            case EdgeMethod.SOBEL:
                # Sobel kernels sum to 8 across a unit ramp: /8 is per pixel
                return (
                    np.hypot(
                        ndimage.sobel(band, 1, mode="reflect"),
                        ndimage.sobel(band, 0, mode="reflect"),
                    )
                    / 8.0
                )
            case EdgeMethod.PREWITT:
                return (
                    np.hypot(
                        ndimage.prewitt(band, 1, mode="reflect"),
                        ndimage.prewitt(band, 0, mode="reflect"),
                    )
                    / 6.0
                )
            case EdgeMethod.SCHARR:
                # scikit-image's Scharr is the RMS of its two derivatives,
                # each 2 across a unit ramp: /√2 is per pixel
                return filters.scharr(band) / np.sqrt(2.0)
            case EdgeMethod.ROBERTS:
                return filters.roberts(band)
            case EdgeMethod.LAPLACIAN:
                return ndimage.laplace(band, mode="reflect")
            case EdgeMethod.LAPLACIAN_OF_GAUSSIAN:
                return ndimage.gaussian_laplace(band, sigma, mode="reflect")
            case EdgeMethod.CANNY:
                scaled, _, _ = _standardized(band)
                return feature.canny(scaled, sigma=sigma).astype(np.float64)
            case EdgeMethod.DIRECTIONAL:
                east = ndimage.sobel(band, 1, mode="reflect") / 8.0
                north = -ndimage.sobel(band, 0, mode="reflect") / 8.0  # rows run down
                theta = np.radians(float(self.angle.value))
                return np.sin(theta) * east + np.cos(theta) * north
        raise ValueError(f"Unknown method {self.method.value}")


# --- morphology -------------------------------------------------------------------


class MorphologyOperation(LabeledEnum):
    DILATE = "Dilate (local maximum)"
    ERODE = "Erode (local minimum)"
    OPENING = "Opening (erode, then dilate)"
    CLOSING = "Closing (dilate, then erode)"
    WHITE_TOP_HAT = "White top-hat (bright details)"
    BLACK_TOP_HAT = "Black top-hat (dark details)"
    GRADIENT = "Morphological gradient"


class StructureShape(LabeledEnum):
    SQUARE = "Square"
    DISK = "Disk"


def footprint(shape: StructureShape, size: int) -> np.ndarray:
    size = oddSize(size)
    if shape is StructureShape.SQUARE:
        return np.ones((size, size), bool)
    radius = size // 2
    y, x = np.ogrid[-radius : radius + 1, -radius : radius + 1]
    return x * x + y * y <= radius * radius


class MorphologyAnalysis(BandwiseAnalysis):
    analysisId = "morphology"
    software = ("scipy", "scikit-image")
    name = "Morphology"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Grayscale morphology on each band: dilate and erode (local maximum "
        "and minimum), opening and closing (remove bright or dark features "
        "smaller than the structuring element), top-hats (keep only those "
        "features) and the morphological gradient (max minus min)."
    )

    operation = EnumParameter(
        "Operation", MorphologyOperation, MorphologyOperation.OPENING
    )
    shape = EnumParameter("Shape", StructureShape, StructureShape.DISK)
    size = _sizeParameter(3, "Width of the structuring element in pixels (made odd)")

    def label(self) -> str:
        operation = self.operation.value.value.split(" (")[0].lower()
        return f"{operation} {oddSize(self.size.value)}px"

    def processBand(self, band: np.ndarray) -> np.ndarray:
        element = footprint(self.shape.value, int(self.size.value))
        operations = {
            MorphologyOperation.DILATE: ndimage.grey_dilation,
            MorphologyOperation.ERODE: ndimage.grey_erosion,
            MorphologyOperation.OPENING: ndimage.grey_opening,
            MorphologyOperation.CLOSING: ndimage.grey_closing,
            MorphologyOperation.WHITE_TOP_HAT: ndimage.white_tophat,
            MorphologyOperation.BLACK_TOP_HAT: ndimage.black_tophat,
            MorphologyOperation.GRADIENT: ndimage.morphological_gradient,
        }
        return operations[self.operation.value](band, footprint=element, mode="reflect")


# --- speckle (adaptive) filters ---------------------------------------------------


class SpeckleMethod(LabeledEnum):
    LEE = "Lee"
    KUAN = "Kuan"
    FROST = "Frost"


def _localStatistics(band: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray]:
    mean = ndimage.uniform_filter(band, size, mode="reflect")
    meanSquare = ndimage.uniform_filter(band * band, size, mode="reflect")
    return mean, np.maximum(meanSquare - mean * mean, 0.0)


def lee(band: np.ndarray, size: int, looks: float, kuan: bool = False) -> np.ndarray:
    """Lee (or Kuan) filter for multiplicative speckle: each pixel moves
    toward its local mean by how little the local variation exceeds the
    noise's (``looks`` sets the noise level, 1/sqrt(looks))."""
    mean, variance = _localStatistics(band, size)
    noise = 1.0 / looks  # squared coefficient of variation of the speckle
    with np.errstate(divide="ignore", invalid="ignore"):
        local = np.where(mean != 0, variance / (mean * mean), 0.0)
        weight = 1.0 - noise / local
        if kuan:
            weight = weight / (1.0 + noise)
    weight = np.clip(np.nan_to_num(weight, nan=0.0), 0.0, 1.0)
    return mean + weight * (band - mean)


def frost(band: np.ndarray, size: int, damping: float) -> np.ndarray:
    """Frost filter: an exponentially damped average of the window, damped
    more where the local variation is high (edges), so they keep their shape."""
    size = oddSize(size)
    mean, variance = _localStatistics(band, size)
    with np.errstate(divide="ignore", invalid="ignore"):
        variation = np.nan_to_num(np.where(mean != 0, variance / (mean * mean), 0.0))
    radius = size // 2
    padded = np.pad(band, radius, mode="reflect")
    total = np.zeros_like(band)
    weights = np.zeros_like(band)
    rows, cols = band.shape
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            weight = np.exp(-damping * variation * np.hypot(dy, dx))
            shifted = padded[
                radius + dy : radius + dy + rows, radius + dx : radius + dx + cols
            ]
            total += weight * shifted
            weights += weight
    return total / weights


class SpeckleFilterAnalysis(BandwiseAnalysis):
    analysisId = "speckle_filter"
    software = ("scipy", "scikit-image")
    name = "Speckle Filters (Lee, Kuan, Frost)"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Adaptive filters that suppress multiplicative noise (speckle) while "
        "keeping edges and texture: they smooth where the image is uniform "
        "and leave it alone where it varies. Lee and Kuan use the number of "
        "looks to estimate the noise; Frost a damping factor."
    )

    method = EnumParameter("Method", SpeckleMethod, SpeckleMethod.LEE)
    size = _sizeParameter(5, "Window width in pixels (made odd)")
    looks = FloatParameter(
        "Looks",
        1.0,
        range=(0.1, 100.0),
        description="Lee and Kuan: number of looks; the noise's variance is 1/looks",
        step=0.5,
    )
    damping = FloatParameter(
        "Damping",
        1.0,
        range=(0.0, 20.0),
        description="Frost: higher keeps edges sharper",
        step=0.1,
    )

    def label(self) -> str:
        return f"{self.method.value.value.lower()} {oddSize(self.size.value)}px"

    def processBand(self, band: np.ndarray) -> np.ndarray:
        size = oddSize(self.size.value)
        match self.method.value:
            case SpeckleMethod.LEE:
                return lee(band, size, float(self.looks.value))
            case SpeckleMethod.KUAN:
                return lee(band, size, float(self.looks.value), kuan=True)
            case SpeckleMethod.FROST:
                return frost(band, size, float(self.damping.value))
        raise ValueError(f"Unknown method {self.method.value}")


# --- frequency-domain filters -----------------------------------------------------


class PassBand(LabeledEnum):
    LOW_PASS = "Low pass"
    HIGH_PASS = "High pass"
    BAND_PASS = "Band pass"


class FilterShape(LabeledEnum):
    GAUSSIAN = "Gaussian"
    BUTTERWORTH = "Butterworth"
    IDEAL = "Ideal (sharp cutoff)"


def _lowPass(radius: np.ndarray, cutoff: float, shape: FilterShape) -> np.ndarray:
    match shape:
        case FilterShape.GAUSSIAN:
            return np.exp(-0.5 * (radius / cutoff) ** 2)
        case FilterShape.BUTTERWORTH:
            return 1.0 / (1.0 + (radius / cutoff) ** 4)  # order 2
        case FilterShape.IDEAL:
            return (radius <= cutoff).astype(np.float64)
    raise ValueError(f"Unknown shape {shape}")


def frequencyFilter(
    band: np.ndarray, passBand: PassBand, shape: FilterShape, low: float, high: float
) -> np.ndarray:
    """Filter in the Fourier domain. Cutoffs are fractions of the Nyquist
    frequency (1 = 2 pixels per cycle); the band is mirror-padded so its
    edges don't wrap around."""
    rows, cols = band.shape
    padded = np.pad(band, ((0, rows), (0, cols)), mode="symmetric")
    fy = np.fft.fftfreq(padded.shape[0])[:, None]
    fx = np.fft.fftfreq(padded.shape[1])[None, :]
    radius = np.hypot(fy, fx) / 0.5  # fraction of Nyquist
    match passBand:
        case PassBand.LOW_PASS:
            response = _lowPass(radius, low, shape)
        case PassBand.HIGH_PASS:
            response = 1.0 - _lowPass(radius, low, shape)
        case PassBand.BAND_PASS:
            response = _lowPass(radius, high, shape) * (
                1.0 - _lowPass(radius, low, shape)
            )
    filtered = np.fft.ifft2(np.fft.fft2(padded) * response).real
    return filtered[:rows, :cols]


class FrequencyFilterAnalysis(BandwiseAnalysis):
    analysisId = "frequency_filter"
    software = ("scipy", "scikit-image")
    name = "Frequency Filter (FFT)"
    memoryFactor = 2.5
    category = CATEGORY
    description = (
        "Filter each band in the Fourier (frequency) domain: low pass keeps "
        "broad variations, high pass keeps fine detail, band pass keeps the "
        "scales between two cutoffs (e.g. to isolate periodic striping). "
        "Cutoffs are fractions of the highest frequency, 2 pixels per cycle."
    )

    passBand = EnumParameter("Type", PassBand, PassBand.LOW_PASS)
    shape = EnumParameter("Shape", FilterShape, FilterShape.GAUSSIAN)
    cutoff = FloatParameter(
        "Cutoff",
        0.25,
        range=(0.005, 1.5),
        description="Low and high pass cutoff, or band pass lower cutoff (fraction of Nyquist)",
        step=0.01,
    )
    upperCutoff = FloatParameter(
        "Upper Cutoff",
        0.5,
        range=(0.005, 1.5),
        description="Band pass only: upper cutoff (fraction of Nyquist)",
        step=0.01,
    )

    def unavailableReason(self, image: VardaRaster) -> str:
        if self.passBand.value is PassBand.BAND_PASS and (
            self.upperCutoff.value <= self.cutoff.value
        ):
            return "The upper cutoff must be above the cutoff."
        return ""

    def label(self) -> str:
        return f"FFT {self.passBand.value.value.lower()}"

    def processBand(self, band: np.ndarray) -> np.ndarray:
        return frequencyFilter(
            band,
            self.passBand.value,
            self.shape.value,
            float(self.cutoff.value),
            float(self.upperCutoff.value),
        )


# --- custom kernel ----------------------------------------------------------------


def parseKernel(text: str) -> np.ndarray:
    """A kernel written as rows separated by ";" or new lines, numbers by
    spaces or commas, e.g. "0 -1 0; -1 5 -1; 0 -1 0". Raises ValueError."""
    rows = [r for r in text.replace(";", "\n").splitlines() if r.strip()]
    try:
        values = [[float(v) for v in r.replace(",", " ").split()] for r in rows]
    except ValueError as error:
        raise ValueError(
            f"The kernel has something that isn't a number: {error}"
        ) from error
    if not values or len({len(r) for r in values}) != 1 or not values[0]:
        raise ValueError("The kernel's rows must all have the same number of values.")
    return np.array(values)


class CustomKernelAnalysis(BandwiseAnalysis):
    analysisId = "custom_kernel"
    software = ("scipy", "scikit-image")
    name = "Custom Kernel"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Apply a kernel you write to each band, as written (each output pixel "
        "is the weighted sum of its neighborhood). Rows are separated by ; and "
        "values by spaces, e.g. 0 -1 0; -1 5 -1; 0 -1 0 sharpens."
    )

    kernel = StringParameter(
        "Kernel", "1 2 1; 2 4 2; 1 2 1", "Rows separated by ; values by spaces"
    )
    normalize = BoolParameter(
        "Normalize", True, "Divide by the sum of the weights (when it isn't zero)"
    )

    def unavailableReason(self, image: VardaRaster) -> str:
        try:
            parseKernel(self.kernel.value)
        except ValueError as error:
            return str(error)
        return ""

    def label(self) -> str:
        return "custom kernel"

    def processBand(self, band: np.ndarray) -> np.ndarray:
        weights = parseKernel(self.kernel.value)
        total = weights.sum()
        if self.normalize.value and total != 0:
            weights = weights / total
        return ndimage.correlate(band, weights, mode="reflect")
