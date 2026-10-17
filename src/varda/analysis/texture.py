"""Texture: local statistics in a moving window (ENVI's occurrence measures)
and gray-level co-occurrence measures (GLCM: contrast, homogeneity, entropy
and the rest), band by band."""

from __future__ import annotations

import sys

import numpy as np
from numba import njit
from scipy import ndimage

from varda.analysis.bandwise import BandScope, BandwiseAnalysis, oddSize
from varda.common.parameter import LabeledEnum, EnumParameter, IntParameter

CATEGORY = "Statistics"
_CACHE = not getattr(sys, "frozen", False)  # see stretch_algorithms._CACHE


def _windowParameter(default: int = 7) -> IntParameter:
    return IntParameter(
        "Window",
        default,
        range=(3, 99),
        units="pixels",
        description="Window width (made odd)",
    )


class LocalMeasure(LabeledEnum):
    MEAN = "Mean"
    STANDARD_DEVIATION = "Standard deviation"
    VARIANCE = "Variance"
    MINIMUM = "Minimum"
    MAXIMUM = "Maximum"
    DATA_RANGE = "Data range"
    SKEWNESS = "Skewness"


def localStatistic(band: np.ndarray, measure: LocalMeasure, size: int) -> np.ndarray:
    match measure:
        case LocalMeasure.MINIMUM:
            return ndimage.minimum_filter(band, size, mode="reflect")
        case LocalMeasure.MAXIMUM:
            return ndimage.maximum_filter(band, size, mode="reflect")
        case LocalMeasure.DATA_RANGE:
            return ndimage.maximum_filter(
                band, size, mode="reflect"
            ) - ndimage.minimum_filter(band, size, mode="reflect")
    mean = ndimage.uniform_filter(band, size, mode="reflect")
    if measure is LocalMeasure.MEAN:
        return mean
    centered = band - band.mean()  # moments about a constant keep precision
    m1 = ndimage.uniform_filter(centered, size, mode="reflect")
    m2 = ndimage.uniform_filter(centered**2, size, mode="reflect")
    variance = np.maximum(m2 - m1**2, 0.0)
    match measure:
        case LocalMeasure.VARIANCE:
            return variance
        case LocalMeasure.STANDARD_DEVIATION:
            return np.sqrt(variance)
        case LocalMeasure.SKEWNESS:
            m3 = ndimage.uniform_filter(centered**3, size, mode="reflect")
            third = m3 - 3 * m1 * variance - m1**3
            with np.errstate(divide="ignore", invalid="ignore"):
                skew = third / variance**1.5
            return np.where(variance > 1e-12 * max(band.var(), 1e-300), skew, 0.0)
    raise ValueError(f"Unknown measure {measure}")


class LocalStatisticsAnalysis(BandwiseAnalysis):
    analysisId = "local_statistics"
    software = ("scipy",)
    name = "Local Statistics (Texture)"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "A statistic of the values in a window around each pixel: mean, "
        "standard deviation, variance, minimum, maximum, data range or "
        "skewness. Standard deviation and range bring out texture and "
        "boundaries; see also Spatial Entropy."
    )

    measure = EnumParameter("Measure", LocalMeasure, LocalMeasure.STANDARD_DEVIATION)
    window = _windowParameter(7)
    scope = EnumParameter(
        "Bands", BandScope, BandScope.ONE_BAND, "Process every band or just one."
    )

    def label(self) -> str:
        size = oddSize(self.window.value)
        return f"local {self.measure.value.value.lower()} {size}×{size}"

    def processBand(self, band: np.ndarray) -> np.ndarray:
        return localStatistic(band, self.measure.value, oddSize(self.window.value))


# --- co-occurrence (GLCM) -----------------------------------------------------------


class GlcmMeasure(LabeledEnum):
    CONTRAST = "Contrast"
    DISSIMILARITY = "Dissimilarity"
    HOMOGENEITY = "Homogeneity"
    SECOND_MOMENT = "Second moment (energy)"
    ENTROPY = "Entropy"
    CORRELATION = "Correlation"
    MEAN = "Mean"
    VARIANCE = "Variance"


class GlcmDirection(LabeledEnum):
    ALL = "All directions (average)"
    EAST = "0° (horizontal)"
    NORTHEAST = "45°"
    NORTH = "90° (vertical)"
    NORTHWEST = "135°"


_OFFSETS = {  # (row, column) step for distance 1
    GlcmDirection.EAST: (0, 1),
    GlcmDirection.NORTHEAST: (-1, 1),
    GlcmDirection.NORTH: (-1, 0),
    GlcmDirection.NORTHWEST: (-1, -1),
}


def quantize(band: np.ndarray, levels: int) -> np.ndarray:
    """Gray levels 0..levels-1 over the band's 2-98 % range."""
    low, high = np.percentile(band, [2, 98])
    if high <= low:
        return np.zeros(band.shape, np.int64)
    scaled = np.floor((band - low) / (high - low) * levels)
    return np.clip(scaled, 0, levels - 1).astype(np.int64)


@njit(cache=_CACHE)
def _glcmMeasure(
    gray: np.ndarray, levels: int, radius: int, dy: int, dx: int, measure: int
) -> np.ndarray:
    rows, cols = gray.shape
    out = np.zeros((rows, cols))
    counts = np.zeros((levels, levels))
    touched = np.zeros((4 * (2 * radius + 1) ** 2, 2), np.int64)
    for r in range(rows):
        for c in range(cols):
            n = 0
            total = 0.0
            top, bottom = max(r - radius, 0), min(r + radius, rows - 1)
            left, right = max(c - radius, 0), min(c + radius, cols - 1)
            for y in range(top, bottom + 1):
                for x in range(left, right + 1):
                    y2, x2 = y + dy, x + dx
                    if y2 < top or y2 > bottom or x2 < left or x2 > right:
                        continue
                    i, j = gray[y, x], gray[y2, x2]
                    for a, b in ((i, j), (j, i)):  # symmetric
                        if counts[a, b] == 0.0:
                            touched[n, 0], touched[n, 1] = a, b
                            n += 1
                        counts[a, b] += 1.0
                        total += 1.0
            if total == 0.0:
                continue
            mean = 0.0
            for k in range(n):
                a, b = touched[k, 0], touched[k, 1]
                mean += a * counts[a, b] / total
            variance = 0.0
            for k in range(n):
                a, b = touched[k, 0], touched[k, 1]
                variance += (a - mean) ** 2 * counts[a, b] / total
            value = 0.0
            for k in range(n):
                a, b = touched[k, 0], touched[k, 1]
                p = counts[a, b] / total
                d = a - b
                if measure == 0:
                    value += p * d * d
                elif measure == 1:
                    value += p * abs(d)
                elif measure == 2:
                    value += p / (1.0 + d * d)
                elif measure == 3:
                    value += p * p
                elif measure == 4:
                    value -= p * np.log(p)
                elif measure == 5 and variance > 0.0:
                    value += p * (a - mean) * (b - mean) / variance
                counts[a, b] = 0.0
            if measure == 5 and variance == 0.0:
                value = 1.0  # a uniform window is perfectly correlated
            elif measure == 6:
                value = mean
            elif measure == 7:
                value = variance
            out[r, c] = value
    return out


def glcmTexture(
    band: np.ndarray,
    measure: GlcmMeasure,
    window: int,
    levels: int = 32,
    direction: GlcmDirection = GlcmDirection.ALL,
    distance: int = 1,
) -> np.ndarray:
    gray = quantize(band, levels)
    code = list(GlcmMeasure).index(measure)
    directions = list(_OFFSETS) if direction is GlcmDirection.ALL else [direction]
    results = [
        _glcmMeasure(
            gray,
            levels,
            window // 2,
            _OFFSETS[d][0] * distance,
            _OFFSETS[d][1] * distance,
            code,
        )
        for d in directions
    ]
    return np.mean(results, axis=0)


class CooccurrenceTextureAnalysis(BandwiseAnalysis):
    analysisId = "cooccurrence_texture"
    software = ("scipy",)
    name = "Co-occurrence Texture (GLCM)"
    memoryFactor = 1.5
    category = CATEGORY
    description = (
        "Gray-level co-occurrence texture: how often pairs of gray levels sit "
        "side by side in a window around each pixel (Haralick et al. 1973). "
        "Contrast and dissimilarity are high where neighbors differ; "
        "homogeneity and second moment where they're alike; entropy measures "
        "disorder. The band is quantized to the gray levels over its 2-98 % range."
    )

    measure = EnumParameter("Measure", GlcmMeasure, GlcmMeasure.CONTRAST)
    window = _windowParameter(7)
    levels = IntParameter(
        "Gray Levels", 32, range=(4, 128), description="Levels the band is quantized to"
    )
    direction = EnumParameter("Direction", GlcmDirection, GlcmDirection.ALL)
    distance = IntParameter(
        "Distance",
        1,
        range=(1, 20),
        units="pixels",
        description="Distance between the pixels of a pair",
    )
    scope = EnumParameter(
        "Bands", BandScope, BandScope.ONE_BAND, "Process every band (slow) or just one."
    )

    def label(self) -> str:
        return f"GLCM {self.measure.value.value.split(' (')[0].lower()}"

    def processBand(self, band: np.ndarray) -> np.ndarray:
        return glcmTexture(
            band,
            self.measure.value,
            oddSize(self.window.value),
            int(self.levels.value),
            self.direction.value,
            int(self.distance.value),
        )
