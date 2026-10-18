"""Shadow masks: where an image is in shadow, from its brightness, from the
terrain and the sun (a co-registered DEM), or both. No Qt.

The DEM may be on another grid (e.g. a 6 m CTX DEM under an 18 m CRISM
image): it is resampled onto the image's grid first (``demOnGrid``), so the
slopes and shadows are computed at the image's pixels.

Brightness: the mean over a wavelength range (or one band) below a
threshold, a percentile of the scene or a value in data units.

Topographic: the local illumination cosine from the DEM's slope and aspect
(Horn's method, ``topography``) and the sun's azimuth and elevation,

    cos(i) = cos(s) sin(e) + sin(s) cos(e) cos(azimuth - aspect),

is self-shadow where it's at or below a threshold; cast shadow is found by
marching from each pixel toward the sun across the DEM: the pixel is shadowed
when the terrain rises above the ray.
"""

from __future__ import annotations

import re
import sys

import attrs
import numpy as np
from numba import njit, prange
from scipy import ndimage

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.bandwise import fillGaps
from varda.analysis.image_math import CATEGORY, readWithGaps
from varda.analysis.rasters import goodBands
from varda.analysis.topography import (
    gradients,
    gridDirections,
    hasPixelSize,
    illuminationCosine,
    pixelSize,
    slopeAspect,
)
from varda.common.entities import VardaRaster
from varda.common.grid import (
    ImageChoices,
    demResampling,
    onGridOf,
    placeableChoices,
    readPixels,
    resamplingRecord,
)
from varda.common.parameter import (
    EnumParameter,
    FloatParameter,
    ImageParameter,
    IntParameter,
    LabeledEnum,
)
from varda.image_loading.crism_geometry import sceneIncidence
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

_CACHE = not getattr(sys, "frozen", False)  # see stretch_algorithms._CACHE

# CRISM's IR albedo window: the R1330 summary parameter (Viviano-Beck et al.
# 2014), a continuum region free of strong mineral absorptions
DEFAULT_BRIGHTNESS_NM = (1280.0, 1380.0)
DEFAULT_PERCENTILE = 5.0
# bands read at once for the brightness mean (bounds the memory)
_BAND_CHUNK = 8


class ShadowMethod(LabeledEnum):
    BRIGHTNESS = "Brightness"
    TOPOGRAPHIC = "Topographic (needs a DEM)"
    BOTH = "Both (union)"


class ThresholdKind(LabeledEnum):
    PERCENTILE = "Percentile of the scene"
    ABSOLUTE = "Absolute value (data units)"


# --- brightness ----------------------------------------------------------------


def brightnessBands(image: VardaRaster, low: float, high: float) -> np.ndarray:
    """The good bands with centers in [low, high] nm; the good band nearest
    the range's center when none is in it; every good band for an image
    without wavelengths."""
    good = goodBands(image)
    centers = image.bandModel.centers
    if centers is None or not np.size(centers):
        return good
    centers = np.asarray(centers, dtype=np.float64)[good]
    inside = good[(centers >= low) & (centers <= high)]
    if inside.size:
        return inside
    return good[[int(np.nanargmin(np.abs(centers - (low + high) / 2)))]]


def defaultBrightnessRange(image: VardaRaster) -> tuple[float, float]:
    """1280-1380 nm when the image has a good band there, else its whole
    wavelength range (every good band)."""
    centers = image.bandModel.centers
    if centers is None or not np.size(centers):
        return DEFAULT_BRIGHTNESS_NM
    good = np.asarray(centers, dtype=np.float64)[goodBands(image)]
    low, high = DEFAULT_BRIGHTNESS_NM
    if ((good >= low) & (good <= high)).any():
        return DEFAULT_BRIGHTNESS_NM
    return float(np.nanmin(good)), float(np.nanmax(good))


def meanBrightness(image: VardaRaster, bands: np.ndarray) -> np.ndarray:
    """Each pixel's mean over ``bands`` (its no-data bands left out); NaN
    where it has none. Read a few bands at a time."""
    total = np.zeros((image.height, image.width))
    count = np.zeros((image.height, image.width))
    for start in range(0, len(bands), _BAND_CHUNK):
        chunk = readWithGaps(
            image, [int(b) for b in bands[start : start + _BAND_CHUNK]]
        )
        finite = np.isfinite(chunk)
        total += np.where(finite, chunk, 0.0).sum(axis=2)
        count += finite.sum(axis=2)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / count, np.nan)


# --- topography ------------------------------------------------------------------


def sunStep(
    azimuth: float, dx: float, dy: float, eastSign: float = 1.0, northSign: float = 1.0
) -> tuple[float, float, float]:
    """One step toward the sun on the grid: (columns, rows, ground distance),
    scaled so the larger of the column and row steps is one pixel."""
    east, north = np.sin(np.radians(azimuth)), np.cos(np.radians(azimuth))
    col = east * eastSign / dx
    row = -north * northSign / dy
    scale = max(abs(col), abs(row))
    # rounded so a sun along a row, column or diagonal steps exactly through
    # cell centers (cos 90° is 6e-17, not 0)
    col, row = round(col / scale, 12), round(row / scale, 12)
    return float(col), float(row), float(np.hypot(col * dx, row * dy))


@njit(cache=_CACHE)
def _bilinear(z: np.ndarray, y: float, x: float) -> float:
    rows, cols = z.shape
    r0, c0 = min(int(y), rows - 1), min(int(x), cols - 1)
    r1, c1 = min(r0 + 1, rows - 1), min(c0 + 1, cols - 1)
    fy, fx = y - r0, x - c0
    top = z[r0, c0] * (1 - fx) + z[r0, c1] * fx
    bottom = z[r1, c0] * (1 - fx) + z[r1, c1] * fx
    return top * (1 - fy) + bottom * fy


@njit(parallel=True, cache=_CACHE)
def _castShadow(
    z: np.ndarray, stepCol: float, stepRow: float, rise: float
) -> np.ndarray:
    rows, cols = z.shape
    top = z.max()
    out = np.zeros((rows, cols), np.bool_)
    for r in prange(rows):  # ty: ignore[not-iterable]
        for c in range(cols):
            base = z[r, c]
            k = 1
            while True:
                height = base + k * rise
                if height >= top:
                    break  # the ray is above every point of the DEM
                y, x = r + k * stepRow, c + k * stepCol
                if y < 0 or x < 0 or y > rows - 1 or x > cols - 1:
                    break  # off the DEM: lit
                if _bilinear(z, y, x) > height:
                    out[r, c] = True
                    break
                k += 1
    return out


def castShadow(
    elevation: np.ndarray,
    azimuth: float,
    sunElevation: float,
    dx: float = 1.0,
    dy: float = 1.0,
    eastSign: float = 1.0,
    northSign: float = 1.0,
) -> np.ndarray:
    """Where the terrain hides the sun: from each pixel, step toward the sun
    one pixel at a time along the larger of the column and row directions
    (the other interpolated linearly), and mark the pixel when the DEM rises
    strictly above the ray to the sun. Heights and pixel sizes in the same
    units. Terrain beyond the DEM's edge can't shadow anything."""
    if sunElevation >= 90.0:
        return np.zeros(elevation.shape, bool)
    stepCol, stepRow, length = sunStep(azimuth, dx, dy, eastSign, northSign)
    rise = length * np.tan(np.radians(sunElevation))
    return _castShadow(
        np.ascontiguousarray(elevation, dtype=np.float64), stepCol, stepRow, rise
    )


def growMask(mask: np.ndarray, pixels: int) -> np.ndarray:
    """``mask`` grown by ``pixels`` in every direction (a square: chessboard
    distance, as the DAFA/TT windows are rectangles)."""
    if pixels <= 0:
        return mask
    return ndimage.maximum_filter(mask, size=2 * pixels + 1, mode="constant")


# --- solar geometry --------------------------------------------------------------


@attrs.frozen
class SunAngles:
    """The sun's azimuth (degrees clockwise from north) and elevation
    (degrees above the horizon) where Varda knows them, and where from."""

    azimuth: float | None = None
    elevation: float | None = None
    azimuthSource: str = ""
    elevationSource: str = ""


_AZIMUTH_KEYS = (
    "sun_azimuth",
    "solar_azimuth",
    "sun_azimuth_angle",
    "solar_azimuth_angle",
)
_ELEVATION_KEYS = (
    "sun_elevation",
    "solar_elevation",
    "sun_elevation_angle",
    "solar_elevation_angle",
)
_INCIDENCE_KEYS = ("incidence_angle", "solar_incidence_angle", "incidence")
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?")


def _metadataAngle(metadata: dict, keys: tuple[str, ...]) -> tuple[float, str] | None:
    normalized = {
        re.sub(r"[\s\-]+", "_", str(k).strip().lower()): (k, v)
        for k, v in metadata.items()
    }
    for key in keys:
        if key in normalized:
            original, value = normalized[key]
            match = _NUMBER.search(str(value))
            if match:
                return float(match.group()), f"image metadata ({original})"
    return None


def solarGeometry(image: VardaRaster) -> SunAngles:
    """The sun's azimuth and elevation from the image's metadata (keys such as
    ``sun_azimuth``, ``sun_elevation`` or ``incidence_angle``), or the
    elevation from a CRISM product's DDR (90° minus the scene's mean
    incidence at the areoid). What isn't known stays None."""
    metadata = dict(image.extraMetadata)
    azimuth = _metadataAngle(metadata, _AZIMUTH_KEYS)
    elevation = _metadataAngle(metadata, _ELEVATION_KEYS)
    if elevation is None:
        incidence = _metadataAngle(metadata, _INCIDENCE_KEYS)
        if incidence is not None:
            elevation = (90.0 - incidence[0], f"90° − incidence, {incidence[1]}")
    if elevation is None and image.filePath:
        ddr = sceneIncidence(image.filePath)
        if ddr is not None:
            elevation = (
                90.0 - ddr.mean,
                f"90° − the mean incidence at the areoid in the CRISM DDR "
                f"{ddr.path} ({ddr.low:.2f}–{ddr.high:.2f}° across the scene)",
            )
    return SunAngles(
        azimuth=None if azimuth is None else azimuth[0] % 360.0,
        elevation=None if elevation is None else float(np.clip(elevation[0], 0, 90)),
        azimuthSource="" if azimuth is None else azimuth[1],
        elevationSource="" if elevation is None else elevation[1],
    )


def demCandidates(images, image: VardaRaster) -> ImageChoices:
    """Single-band images that could be ``image``'s DEM: on its grid, or
    georeferenced and overlapping it (resampled onto its grid, see
    ``demOnGrid``); the image itself too when it has one band. And why the
    others can't."""
    return placeableChoices(list(images), image, bandCounts=(1,), includeBase=True)


def demOnGrid(dem: VardaRaster, image: VardaRaster) -> np.ndarray:
    """The DEM's first band on ``image``'s grid, NaN where it has no data:
    as it is on the same grid, else averaged when its pixels are finer than
    the image's and bilinearly interpolated otherwise (``grid.demResampling``)."""
    return onGridOf(dem, image, demResampling(dem, image), [0])[:, :, 0]


# --- the analysis ----------------------------------------------------------------


class ShadowMaskAnalysis(Analysis):
    analysisId = "shadow_mask"
    name = "Shadow Mask"
    software = ("scipy", "numba")
    category = CATEGORY
    # the brightness bands are read a few at a time; the rest is per pixel
    memoryFactor = 1.0
    description = (
        "A mask of shadow (1) and lit ground (0) from the image's brightness "
        "(dark pixels), the terrain (a co-registered DEM and the sun's "
        "position: slopes facing away and cast shadows), or both. Use it to "
        "leave shadows out of a FATT map or apply it with Apply Mask. Later "
        "bands: the brightness and the local illumination cos(i) used."
    )

    method = EnumParameter("Method", ShadowMethod)
    minWavelength = FloatParameter(
        "Brightness From",
        DEFAULT_BRIGHTNESS_NM[0],
        range=(0.0, 100000.0),
        units="nm",
        showSlider=False,
        description="Brightness is the mean of the good bands in this range "
        "(the band nearest its center when none is in it)",
    )
    maxWavelength = FloatParameter(
        "Brightness To",
        DEFAULT_BRIGHTNESS_NM[1],
        range=(0.0, 100000.0),
        units="nm",
        showSlider=False,
    )
    thresholdKind = EnumParameter("Threshold", ThresholdKind)
    percentile = FloatParameter(
        "Percentile",
        DEFAULT_PERCENTILE,
        range=(0.0, 100.0),
        units="%",
        decimals=2,
        showSlider=False,
        description="Shadow below this percentile of the scene's brightness",
    )
    absolute = FloatParameter(
        "Absolute Threshold",
        0.0,
        range=(-1e12, 1e12),
        units="data units",
        decimals=5,
        showSlider=False,
        description="Shadow below this brightness (e.g. I/F)",
    )
    dem = ImageParameter(
        "DEM",
        "A single-band elevation image co-registered with this image; on "
        "another grid it's resampled onto this image's (averaged when finer, "
        "bilinear when coarser)",
        optional=True,
    )
    zFactor = FloatParameter(
        "Z Factor",
        1.0,
        range=(1e-6, 1e6),
        showSlider=False,
        description="Multiplies elevations to put them in the pixel size's "
        "units (e.g. 1000 for km over m)",
    )
    sunAzimuth = FloatParameter(
        "Sun Azimuth",
        0.0,
        range=(0.0, 360.0),
        units="°",
        showSlider=False,
        description="Compass direction of the sun, clockwise from north",
    )
    sunElevation = FloatParameter(
        "Sun Elevation",
        45.0,
        range=(0.0, 90.0),
        units="°",
        showSlider=False,
        description="Height of the sun above the horizon (90° − incidence)",
    )
    cosineThreshold = FloatParameter(
        "Self-Shadow cos(i) ≤",
        0.0,
        range=(-1.0, 1.0),
        decimals=3,
        showSlider=False,
        description="Self-shadow where the local illumination cosine is at or "
        "below this (0: slopes facing away from the sun)",
    )
    buffer = IntParameter(
        "Buffer",
        0,
        range=(0, 100),
        units="px",
        description="Grow the shadow by this many pixels (3 drops DAFA/TT "
        "windows straddling a shadow's edge)",
    )

    def __init__(self) -> None:
        super().__init__()
        # Where each sun angle came from: the image's metadata, "entered" by
        # the user, or "" while unknown (the topographic method needs both)
        self.sunSources = {"azimuth": "", "elevation": ""}
        self.sunAzimuth.sigParameterChanged.connect(lambda _: self._entered("azimuth"))
        self.sunElevation.sigParameterChanged.connect(
            lambda _: self._entered("elevation")
        )

    def _entered(self, angle: str) -> None:
        self.sunSources[angle] = "entered"

    def usesBrightness(self) -> bool:
        return self.method.get() is not ShadowMethod.TOPOGRAPHIC

    def usesTerrain(self) -> bool:
        return self.method.get() is not ShadowMethod.BRIGHTNESS

    def prepareFor(self, image: VardaRaster) -> None:
        low, high = defaultBrightnessRange(image)
        self.minWavelength.set(low)
        self.maxWavelength.set(high)
        current = self.dem.get()
        self.dem.setChoices(lambda: demCandidates(self.context.images, image))
        candidates = demCandidates(self.context.images, image).images
        self.dem.setOrClear(current if current in candidates else None)
        angles = solarGeometry(image)
        for angle, param, value, source in (
            ("azimuth", self.sunAzimuth, angles.azimuth, angles.azimuthSource),
            ("elevation", self.sunElevation, angles.elevation, angles.elevationSource),
        ):
            if value is not None:
                with param.sigParameterChanged.blocked():
                    param.set(value)
                self.sunSources[angle] = source
            elif self.sunSources[angle] != "entered":
                self.sunSources[angle] = ""

    def unavailableReason(self, image: VardaRaster) -> str:
        if not self.usesTerrain():
            return ""
        dem = self.dem.get()
        if dem is None:
            if not demCandidates(self.context.images, image).images:
                return (
                    "The topographic method needs a single-band DEM covering "
                    "this image: open one, co-registered (see the Mosaic "
                    "workspace)."
                )
            return "Choose the DEM."
        if image.crs is not None and image.crs.is_geographic:
            return (
                "The DEM is used on this image's grid, whose pixels are in "
                "degrees: use a projected image (meters per pixel)."
            )
        unknown = [angle for angle, source in self.sunSources.items() if not source]
        if unknown:
            return (
                f"Varda doesn't know this image's sun {' or '.join(unknown)}: "
                "enter it (from the product's label or geometry)."
            )
        return ""

    def estimateMemory(self, image: VardaRaster) -> int:
        # plus about a dozen per-pixel planes for the DEM and its products,
        # and the DEM's pixels read for resampling (read and copied)
        dem = self.dem.get() if self.usesTerrain() else None
        demRead = 0 if dem is None else 2 * readPixels(dem, image) * 8
        return (
            super().estimateMemory(image)
            + 16 * image.height * image.width * 8
            + demRead
        )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        reason = self.unavailableReason(image)
        if reason:
            raise ValueError(reason)
        shape = (image.height, image.width)
        shadow = np.zeros(shape, bool)
        missing = np.zeros(shape, bool)
        bands: list[np.ndarray] = []
        bandNames: list[str] = []
        record: dict = {"method": self.method.get().value}
        if self.usesBrightness():
            reportProgress(0, "reading the brightness")
            brightness, info = self._brightness(image)
            dark = np.isfinite(brightness) & (brightness < info["threshold"])
            info["shadowPixels"] = int(dark.sum())
            shadow |= dark
            missing |= ~np.isfinite(brightness)
            bands.append(brightness)
            bandNames.append("Brightness")
            record["brightness"] = info
        if self.usesTerrain():
            reportProgress(30, "tracing shadows across the DEM")
            cosine, selfShadow, cast, info = self._terrain(image)
            shadow |= selfShadow | cast
            missing |= np.isnan(cosine)
            bands.append(cosine)
            bandNames.append("cos(i)")
            record["topographic"] = info
        reportProgress(90, "buffering")
        missing &= ~shadow  # a shadow either rule finds stands
        buffered = growMask(shadow, int(self.buffer.get()))
        record["buffer"] = int(self.buffer.get())
        record["bufferAddedPixels"] = int((buffered & ~shadow & ~missing).sum())
        mask = np.where(missing, np.nan, buffered.astype(np.float64))
        record["shadowPixels"] = int(np.nansum(mask))
        record["validPixels"] = int(np.isfinite(mask).sum())
        source = ArrayDataSource(
            np.stack([mask, *bands], axis=2).astype(np.float32),
            wavelengths=np.array(["Shadow mask", *bandNames]),
            wavelengthUnits="",
            bandNames=["Shadow mask", *bandNames],
            transform=image.transform,
            crs=image.crs,
            defaultBands=np.array([0, 0, 0]),
            extraMetadata={"shadowMask": record},
            description=self._describe(image, record),
        )
        return VardaRaster(source, name=f"{image.name} shadow mask")

    def _brightness(self, image: VardaRaster) -> tuple[np.ndarray, dict]:
        low, high = sorted((self.minWavelength.get(), self.maxWavelength.get()))
        chosen = brightnessBands(image, low, high)
        brightness = meanBrightness(image, chosen)
        if not np.isfinite(brightness).any():
            raise ValueError(f"{image.name} has no data in the brightness bands.")
        centers = image.bandModel.centers
        info: dict = {
            "bands": [int(b) + 1 for b in chosen],
            "wavelengths": (
                [float(centers[b]) for b in chosen] if centers is not None else []
            ),
        }
        if self.thresholdKind.get() is ThresholdKind.PERCENTILE:
            percentile = float(self.percentile.get())
            info["percentile"] = percentile
            info["threshold"] = float(np.nanpercentile(brightness, percentile))
        else:
            info["threshold"] = float(self.absolute.get())
        return brightness, info

    def _terrain(
        self, image: VardaRaster
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
        """The DEM on the image's grid (see ``demOnGrid``): its illumination
        cosine, self and cast shadow, and their record."""
        dem = self.dem.get()
        assert dem is not None
        elevation, gaps = fillGaps(demOnGrid(dem, image))
        zFactor = float(self.zFactor.get())
        elevation = elevation * zFactor
        dx, dy = pixelSize(image)
        eastSign, northSign = gridDirections(image)
        azimuth, sun = float(self.sunAzimuth.get()), float(self.sunElevation.get())
        slope, aspect = slopeAspect(*gradients(elevation, dx, dy, eastSign, northSign))
        cosine = illuminationCosine(slope, aspect, azimuth, sun)
        cosine[gaps] = np.nan
        limit = float(self.cosineThreshold.get())
        selfShadow = ~gaps & (cosine <= limit)
        cast = ~gaps & castShadow(elevation, azimuth, sun, dx, dy, eastSign, northSign)
        info = {
            "dem": dem.name,
            "demResampling": resamplingRecord(dem, image, demResampling(dem, image)),
            "sunAzimuth": azimuth,
            "sunElevation": sun,
            "sunAzimuthSource": self.sunSources["azimuth"],
            "sunElevationSource": self.sunSources["elevation"],
            "zFactor": zFactor,
            "pixelSize": [dx, dy],
            "pixelSizeKnown": hasPixelSize(image),
            "cosineThreshold": limit,
            "selfShadowPixels": int(selfShadow.sum()),
            "castShadowPixels": int(cast.sum()),
            "demGapPixels": int(gaps.sum()),
        }
        return cosine, selfShadow, cast, info

    @staticmethod
    def _describe(image: VardaRaster, record: dict) -> str:
        parts = [f"Shadow mask of {image.name} ({record['method']})"]
        if "brightness" in record:
            b = record["brightness"]
            how = (
                f"the {b['percentile']:g}th percentile, {b['threshold']:.6g}"
                if "percentile" in b
                else f"{b['threshold']:.6g}"
            )
            parts.append(
                f"brightness (mean of {len(b['bands'])} bands) below {how}: "
                f"{b['shadowPixels']} px"
            )
        if "topographic" in record:
            t = record["topographic"]
            parts.append(
                f"DEM {t['dem']}, sun azimuth {t['sunAzimuth']:g}° "
                f"({t['sunAzimuthSource']}), elevation {t['sunElevation']:g}° "
                f"({t['sunElevationSource']}): self-shadow (cos(i) ≤ "
                f"{t['cosineThreshold']:g}) {t['selfShadowPixels']} px, cast "
                f"shadow {t['castShadowPixels']} px"
                + (
                    f" ({t['demResampling']['description']})"
                    if t.get("demResampling")
                    else ""
                )
                + (
                    ""
                    if t["pixelSizeKnown"]
                    else "; the DEM has no georeferencing, so pixels count as 1 unit of elevation"
                )
            )
        parts.append(
            f"buffer {record['buffer']} px (+{record['bufferAddedPixels']}); "
            f"{record['shadowPixels']} of {record['validPixels']} px in shadow"
        )
        return "; ".join(parts) + "."
