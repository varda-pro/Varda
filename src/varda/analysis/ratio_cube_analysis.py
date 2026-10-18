"""Ratio Cube: a CRISM I/F cube divided by one denominator spectrum per sensor
detector column, so it can stand in for the I/F cube in other software.

The column of each pixel comes from the cube's backplane (the IN file's IR
Sample band), or for an unprojected cube from its own image columns
(``crism_geometry.columnGeometryFor``). No Qt.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

import numpy as np

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.blandness import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_SLOPE,
    PRESETS,
    ParameterLimit,
    Preset,
    genericBlandMask,
    presetBlandMask,
)
from varda.analysis.hypyrameter_adapter import (
    HYPYRAMETER_AVAILABLE,
    computeParameterCube,
    validParameterNames,
)
from varda.analysis.rasters import bandMetadata, goodBands, readGoodBands
from varda.analysis.ratio_cube import (
    ColumnDenominators,
    applyRatio,
    columnDenominators,
    columnKeys,
    inColumnRunMeans,
)
from varda.common.entities import VardaRaster
from varda.common.parameter import (
    EnumParameter,
    FloatParameter,
    IntParameter,
    LabeledEnum,
    StringParameter,
)
from varda.image_loading.crism_geometry import (
    IMAGE_COLUMNS,
    ColumnGeometry,
    columnGeometryFor,
)
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

CATEGORY = "CRISM"
NO_DATA = "no data"
# CRISM's VNIR (S detector) bands lie below this; the backplane's column is
# the IR (L) detector's, so in a cube with bands on both detectors the VNIR
# bands are only approximately column-matched
VNIR_BELOW_NM = 1000.0
MIN_BANDS = 3  # the continuum and the slope need at least three bands
BLANDNESS_GENERIC = "Generic"
RUN_RANGE = (3, 51)
MINIMUM_PIXELS_RANGE = (1, 10000)
NEIGHBOR_RADIUS_RANGE = (0, 10)
_SIGNIFICANT_DIGITS = 6


class RatioCubeMethod(LabeledEnum):
    WHOLE_COLUMN = "Whole column"
    BLAND_PIXELS = "Bland pixels"


# "Generic", then the target presets of blandness.PRESETS
Blandness: type[LabeledEnum] = LabeledEnum(  # ty: ignore[invalid-assignment]
    "Blandness",
    {
        "GENERIC": BLANDNESS_GENERIC,
        **{f"PRESET{i}": name for i, name in enumerate(PRESETS)},
    },
)


# What a preset's parameter cube comes from; a test can replace it (HyPyRameter
# is an optional extra), as presetBlandMask's own parameterCube argument.
parameterCube = computeParameterCube

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_LIMIT = re.compile(rf"^\s*(\w+)\s*(?:≤|<=)\s*({_NUMBER})\s*$")
_SLOPE = re.compile(
    rf"^\s*slope\s*(?:≤|<=)\s*({_NUMBER})\s*(?:/\s*µm|per\s*µm)?\s*over\s*"
    rf"({_NUMBER})\s*(?:–|-|to)\s*({_NUMBER})\s*nm\s*$",
    re.IGNORECASE,
)


def formatPresetLimits(preset: Preset) -> str:
    """A preset's limits as the editable text, e.g.
    ``LCPINDEX2 ≤ 0.02, HCPINDEX2 ≤ 0.02; slope ≤ 0.1 /µm over 1000–1700 nm``."""
    limits = ", ".join(f"{p.name} ≤ {p.maximum:g}" for p in preset.limits)
    low, high = preset.slopeWindowNm
    return f"{limits}; slope ≤ {preset.maxSlope:g} /µm over {low:g}–{high:g} nm"


def parsePresetLimits(text: str, preset: Preset) -> Preset:
    """``preset`` with the limits written in ``text`` (the format of
    :func:`formatPresetLimits`); ValueError says what is wrong. Every one of the
    preset's parameters must appear once: only the numbers are edited."""
    limitsText, separator, slopeText = text.partition(";")
    slope = _SLOPE.match(slopeText) if separator else None
    if slope is None:
        raise ValueError(
            f"Preset limits need a slope part after ';', like "
            f"'slope ≤ 0.10 /µm over 1000–1700 nm'; got '{text.strip()}'."
        )
    maximum, low, high = (float(g) for g in slope.groups())
    if not (math.isfinite(maximum) and maximum >= 0 and 0 <= low < high):
        raise ValueError(
            "The slope limit must be 0 or more over a window with start < end."
        )
    found: dict[str, float] = {}
    for part in limitsText.split(","):
        match = _LIMIT.match(part)
        if match is None:
            raise ValueError(
                f"Preset limits: '{part.strip()}' is not like 'NAME ≤ 0.02'."
            )
        name, value = match.group(1), float(match.group(2))
        if name in found or name not in {p.name for p in preset.limits}:
            raise ValueError(
                f"Preset limits: {name} is repeated or not a parameter of the "
                f"{preset.name} preset ({', '.join(p.name for p in preset.limits)})."
            )
        if not (math.isfinite(value) and value >= 0):
            raise ValueError(f"Preset limits: {name} must be 0 or more.")
        found[name] = value
    missing = [p.name for p in preset.limits if p.name not in found]
    if missing:
        raise ValueError(f"Preset limits are missing {', '.join(missing)}.")
    return Preset(
        preset.name,
        tuple(ParameterLimit(p.name, found[p.name]) for p in preset.limits),
        (low, high),
        maximum,
    )


def _vnirApproximate(centersNm: np.ndarray, geometry: ColumnGeometry) -> bool:
    """Whether the cube has bands on both detectors, with columns that are the
    IR detector's (an IR sample band, or a joined cube's image columns): its
    VNIR bands are then only approximately column-matched."""
    bothDetectors = bool(
        (centersNm < VNIR_BELOW_NM).any() and (centersNm >= VNIR_BELOW_NM).any()
    )
    return bothDetectors and not geometry.sampleBand.lower().startswith("vnir")


def _numbers(values: np.ndarray) -> list[float | None]:
    """JSON-safe numbers (NaN as None), kept to six significant digits."""
    return [
        float(f"{v:.{_SIGNIFICANT_DIGITS}g}") if np.isfinite(v) else None
        for v in values
    ]


def _perPixel(
    strip: np.ndarray, column: np.ndarray, perKey: Mapping[tuple[int, int], float]
) -> np.ndarray:
    """A (rows, cols) float32 plane holding each pixel's key's value; NaN
    where the pixel has no key."""
    width = int(column.max()) + 1 if column.size else 1
    codes = np.where((strip >= 0) & (column >= 0), strip * width + column, -1)
    unique, inverse = np.unique(codes.reshape(-1), return_inverse=True)
    values = np.array(
        [
            perKey.get((int(u) // width, int(u) % width), np.nan) if u >= 0 else np.nan
            for u in unique
        ],
        dtype=np.float32,
    )
    return values[inverse].reshape(codes.shape)


class RatioCubeAnalysis(Analysis):
    analysisId = "ratio_cube"
    name = "Ratio Cube (Column Denominators)"
    category = CATEGORY
    # the cube in float64, its in-column run means and the bland mask's work
    # copies, then the float32 result: measured 5.6 on the default (Bland, Generic)
    # settings, plus 25 percent (test_ratio_cube_analysis)
    memoryFactor = 7.0
    description = (
        "Divides a CRISM I/F cube by one denominator spectrum per sensor "
        "detector column (read from the backplane next to the cube, or the "
        "image columns of an unprojected cube), "
        "which removes column-dependent artifacts such as striping. The "
        "denominator is the mean of the whole column or of its bland "
        "(featureless) pixels. The result can replace the I/F cube in other "
        "software; a second image records which pixels made each denominator "
        "and how many, and the metadata keeps each column's spectrum and its "
        "spread."
    )

    method = EnumParameter(
        "Denominator",
        RatioCubeMethod,
        RatioCubeMethod.BLAND_PIXELS,
        "Average the whole column, or only the pixels whose spectrum is bland",
    )
    blandness = EnumParameter(
        "Blandness",
        Blandness,
        description="Generic: shallow absorptions and a flat slope. A target "
        "preset keeps only pixels free of that mineral's spectral parameters "
        "(needs HyPyRameter)",
    )
    maxDepth = FloatParameter(
        "Generic Maximum Depth",
        DEFAULT_MAX_DEPTH,
        range=(0.0, 1.0),
        decimals=3,
        showSlider=False,
        description="Generic check: the deepest continuum-removed absorption "
        "allowed (1 minus the continuum-removed value)",
    )
    maxSlope = FloatParameter(
        "Generic Maximum Slope",
        DEFAULT_MAX_SLOPE,
        range=(0.0, 10.0),
        units="per µm",
        decimals=3,
        showSlider=False,
        description="Generic check: the largest |relative slope| allowed "
        "(slope divided by the mean, per micrometer)",
    )
    runLength = IntParameter(
        "Run Length",
        9,
        range=RUN_RANGE,
        units="px",
        step=2,
        description="Blandness is judged on the mean spectrum of this many "
        "neighboring pixels of the same column (odd), to beat the noise",
    )
    minimumPixels = IntParameter(
        "Minimum Pixels",
        20,
        range=MINIMUM_PIXELS_RANGE,
        units="px",
        description="A column with fewer bland pixels than this borrows from "
        "neighboring columns, then uses its whole column",
    )
    neighborRadius = IntParameter(
        "Neighbor Radius",
        2,
        range=NEIGHBOR_RADIUS_RANGE,
        units="columns",
        description="How far (columns each side, same strip) a column may "
        "borrow bland pixels from; 0 never borrows",
    )

    presetLimits = StringParameter(
        "Preset Limits",
        "",
        "The chosen preset's maxima and slope limit, editable (refilled when "
        "the preset changes). Maxima are the largest parameter values a bland "
        "pixel may have",
    )

    def __init__(self) -> None:
        super().__init__()
        self.blandness.sigParameterChanged.connect(self._fillPresetLimits)

    def _fillPresetLimits(self, _=None) -> None:
        name = self.blandness.get().value
        self.presetLimits.set(
            "" if name == BLANDNESS_GENERIC else formatPresetLimits(PRESETS[name])
        )

    def _preset(self) -> Preset | None:
        """The chosen preset with the limits as edited (None for Generic);
        ValueError when the text is malformed."""
        name = self.blandness.get().value
        if name == BLANDNESS_GENERIC:
            return None
        text = self.presetLimits.get().strip()
        return parsePresetLimits(text, PRESETS[name]) if text else PRESETS[name]

    def _problem(self, image: VardaRaster) -> str:
        """Why this cannot run on ``image`` ("" when it can)."""
        if not RUN_RANGE[0] <= self.runLength.get() <= RUN_RANGE[1] or (
            self.runLength.get() % 2 == 0
        ):
            return f"Run Length must be odd, {RUN_RANGE[0]} to {RUN_RANGE[1]}."
        if (
            not MINIMUM_PIXELS_RANGE[0]
            <= self.minimumPixels.get()
            <= MINIMUM_PIXELS_RANGE[1]
        ):
            return "Minimum Pixels must be at least 1."
        if (
            not NEIGHBOR_RADIUS_RANGE[0]
            <= self.neighborRadius.get()
            <= NEIGHBOR_RADIUS_RANGE[1]
        ):
            return "Neighbor Radius must be 0 or more."
        geometry = self._geometry(image)
        if geometry is None:
            where = (
                f"next to {image.filePath}"
                if image.filePath
                else "(the image has no file)"
            )
            return (
                "No CRISM backplane with detector columns (an IN or DDR file with "
                f"an IR Sample band) was found {where}, and the image is not an "
                "unprojected CRISM cube (no map projection, 640, 320 or 160 "
                "columns), so each pixel's detector column is unknown."
            )
        if geometry.ir_sample.shape != (image.height, image.width):
            return (
                f"The backplane's size {geometry.ir_sample.shape[::-1]} differs "
                f"from the image's {(image.width, image.height)}."
            )
        centers = image.bandModel.centers
        if centers is None:
            return (
                "The image needs wavelengths (nm, µm or cm⁻¹) for the blandness check."
            )
        wavelengths = centers[goodBands(image)]
        if self.method.get() is RatioCubeMethod.WHOLE_COLUMN:
            return ""
        try:
            preset = self._preset()
        except ValueError as error:
            return str(error)
        if preset is None:
            if wavelengths.size < MIN_BANDS:
                return "The blandness check needs at least three good bands."
            return ""
        if not HYPYRAMETER_AVAILABLE:
            return (
                f"The {preset.name} preset needs HyPyRameter (an optional "
                "feature, see Optional Features); the Generic check works without it."
            )
        low, high = sorted(preset.slopeWindowNm)
        if ((wavelengths >= low) & (wavelengths <= high)).sum() < MIN_BANDS:
            return (
                f"Fewer than three good bands fall in the {preset.name} "
                f"preset's {low:g}-{high:g} nm slope window."
            )
        missing = {limit.name for limit in preset.limits} - set(
            validParameterNames(wavelengths)
        )
        if missing:
            return (
                f"The image's wavelengths can't support the {preset.name} "
                f"preset's parameters: {', '.join(sorted(missing))}."
            )
        return ""

    @staticmethod
    def _geometry(image: VardaRaster) -> ColumnGeometry | None:
        return columnGeometryFor(
            image.filePath, (image.height, image.width), image.crs is not None
        )

    def unavailableReason(self, image: VardaRaster) -> str:
        return self._problem(image)

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        return self.runAll(image, reportProgress)[0]

    def runAll(
        self, image: VardaRaster, reportProgress: ProgressCallback
    ) -> list[VardaRaster]:
        reason = self._problem(image)
        if reason:
            raise ValueError(reason)
        geometry = self._geometry(image)
        assert geometry is not None
        reportProgress(0, "reading the image")
        cube, valid, bands = readGoodBands(image)
        centers = image.bandModel.centers
        assert centers is not None
        wavelengths = centers[bands]
        strip, column = columnKeys(geometry)
        finite = valid & np.isfinite(cube).all(axis=2)

        whole = self.method.get() is RatioCubeMethod.WHOLE_COLUMN
        record: dict = {
            "method": self.method.get().value,
            "blandness": "" if whole else self.blandness.get().value,
            "thresholds": {} if whole else self._thresholds(),
            "runLength": int(self.runLength.get()),
            "minimumPixels": int(self.minimumPixels.get()),
            "neighborRadius": int(self.neighborRadius.get()),
            "geometry": (
                IMAGE_COLUMNS
                if geometry.source == IMAGE_COLUMNS
                else f"backplane band '{geometry.sampleBand}'"
            ),
            "geometryFile": "" if geometry.source == IMAGE_COLUMNS else geometry.source,
            "binning": int(geometry.binning),
            "vnirApproximate": _vnirApproximate(centers, geometry),
            "vnirBelowNm": VNIR_BELOW_NM,
            "bands": [int(b) + 1 for b in bands],
        }
        if whole:
            use, minimum, radius = finite, 1, 0
        else:
            reportProgress(15, "finding bland pixels")
            runMeans = inColumnRunMeans(cube, strip, column, self.runLength.get())
            use = self._blandMask(runMeans, wavelengths) & finite
            del runMeans
            minimum, radius = self.minimumPixels.get(), self.neighborRadius.get()
        reportProgress(60, "averaging each column")
        denominators = columnDenominators(cube, strip, column, use, minimum, radius)
        fallback, counts = self._flagMissing(denominators)
        reportProgress(80, "dividing")
        ratio = np.full((*cube.shape[:2], image.bandCount), np.nan, dtype=np.float32)
        ratio[:, :, bands] = applyRatio(cube, strip, column, denominators)

        record["columns"] = [
            {
                "strip": key[0],
                "column": key[1],
                "count": counts[key],
                "fallback": fallback.get(key),
                "spectrum": _numbers(denominators.spectra[key]),
                "std": _numbers(denominators.std[key]),
            }
            for key in sorted(denominators.spectra)
        ]
        description = self._describe(image, record, fallback)
        result = ArrayDataSource(
            ratio,
            wavelengths=np.asarray(image.wavelengths),
            wavelengthUnits=image.wavelengthUnits,
            bandNames=list(image.bandNames),
            transform=image.transform,
            crs=image.crs,
            defaultBands=np.asarray(image.defaultBands),
            extraMetadata={**bandMetadata(image), "ratioCube": record},
            description=description,
        )
        used = np.where(strip >= 0, denominators.pooled, np.nan).astype(np.float32)
        companion = ArrayDataSource(
            np.stack([used, _perPixel(strip, column, counts)], axis=2),
            wavelengths=np.array(["Used as denominator", "Column denominator count"]),
            wavelengthUnits="",
            bandNames=["Used as denominator", "Column denominator count"],
            transform=image.transform,
            crs=image.crs,
            defaultBands=np.array([0, 0, 0]),
            description=f"Pixels that made {image.name}'s column denominators, "
            "and each column's pixel count.",
        )
        return [
            VardaRaster(result, name=f"{image.name} ratio cube"),
            VardaRaster(companion, name=f"{image.name} ratio denominators"),
        ]

    def _thresholds(self) -> dict:
        preset = self._preset()
        if preset is None:
            return {
                "maxDepth": float(self.maxDepth.get()),
                "maxSlope": float(self.maxSlope.get()),
            }
        return {
            "limits": {limit.name: limit.maximum for limit in preset.limits},
            "maxSlope": preset.maxSlope,
            "slopeWindowNm": list(preset.slopeWindowNm),
        }

    def _blandMask(self, cube: np.ndarray, wavelengths: np.ndarray) -> np.ndarray:
        preset = self._preset()
        if preset is None:
            return genericBlandMask(
                cube, wavelengths, self.maxDepth.get(), self.maxSlope.get()
            )
        return presetBlandMask(cube, wavelengths, preset, parameterCube)

    @staticmethod
    def _flagMissing(
        denominators: ColumnDenominators,
    ) -> tuple[dict[tuple[int, int], str], dict[tuple[int, int], int]]:
        """The fallbacks, with columns that have no finite pixel at all flagged
        "no data" (their ratio pixels are NaN) and counted as 0."""
        fallback = dict(denominators.fallback)
        counts = dict(denominators.counts)
        for key, spectrum in denominators.spectra.items():
            if not np.isfinite(spectrum).any():
                fallback[key] = NO_DATA
                counts[key] = 0
        return fallback, counts

    @staticmethod
    def _describe(
        image: VardaRaster, record: dict, fallback: dict[tuple[int, int], str]
    ) -> str:
        total = len(record["columns"])
        parts = [f"Ratio cube of {image.name}: {record['method']}"]
        if record["blandness"]:
            parts[0] += (
                f" ({record['blandness']} blandness, run of {record['runLength']} px)"
            )
        parts.append(
            f"{total} columns, at least {record['minimumPixels']} pixels each"
            if record["method"] == RatioCubeMethod.BLAND_PIXELS.value
            else f"{total} columns"
        )
        for kind, label in (
            ("neighbors", "borrowed from neighboring columns"),
            ("whole column", "used the whole column"),
            (NO_DATA, "had no data (ratio is NaN)"),
        ):
            n = sum(1 for how in fallback.values() if how == kind)
            if n:
                parts.append(f"{n} of {total} columns {label}")
        if not fallback:
            parts.append("0 columns fell back")
        if record["geometry"] == IMAGE_COLUMNS:
            parts.append(
                "detector columns are the image columns (unprojected cube, no "
                f"backplane; {record['binning']} detector column(s) per image column)"
            )
        else:
            parts.append(
                f"detector columns from {record['geometry']} of "
                f"{record['geometryFile']}"
            )
        if record["vnirApproximate"]:
            parts.append(
                f"VNIR bands (below {VNIR_BELOW_NM:g} nm) are only approximately "
                "column-matched, since the columns are the IR detector's"
            )
        return "; ".join(parts) + "."
