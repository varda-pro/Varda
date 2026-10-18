"""Ratio Cube: a CRISM I/F cube divided by one denominator spectrum per sensor
detector column, so it can stand in for the I/F cube in other software.

The column of each pixel comes from the cube's backplane (the IN file's IR
Sample band), or for an unprojected cube from its own image columns
(``crism_geometry.columnGeometryFor``). The backplane is the Geometry File
chosen here, else the image's override, a companion beside it or beside the
file it was made from. No Qt.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.blandness import (
    CHECKS,
    DEFAULT_EXCLUSIONS,
    GENERIC,
    LIMIT_RANGES,
    MIN_WINDOW_BANDS,
    RATIO_CUBE_LIMITS,
    SMOOTHING_BANDS,
    Limits,
    blandMask,
    describeSettings,
    formatExclusions,
    parseExclusions,
    windowBandCounts,
)
from varda.analysis.rasters import bandMetadata, goodBands, readGoodBands
from varda.analysis.ratio_cube import (
    ColumnDenominators,
    applyRatio,
    columnDenominators,
    columnKeys,
    inColumnRunMeans,
    oversamplingEstimate,
)
from varda.common.entities import VardaRaster
from varda.common.parameter import (
    EnumParameter,
    FilePathParameter,
    FloatParameter,
    IntParameter,
    LabeledEnum,
    StringParameter,
)
from varda.image_loading.crism_geometry import (
    COLUMNS,
    IMAGE_COLUMNS,
    ColumnGeometry,
    checkGeometryFile,
    columnGeometryFor,
    geometryDataFile,
    geometryOverride,
    geometryStartFolder,
    setGeometryOverride,
)
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.raster_writer import SIDECAR_WRITERS

CATEGORY = "CRISM"
NO_DATA = "no data"
# CRISM's VNIR (S detector) bands lie below this; the backplane's column is
# the IR (L) detector's, so in a cube with bands on both detectors the VNIR
# bands are only approximately column-matched
VNIR_BELOW_NM = 1000.0
BLANDNESS_GENERIC = GENERIC
RUN_RANGE = (3, 51)
MINIMUM_PIXELS_RANGE = (1, 10000)
NEIGHBOR_RADIUS_RANGE = (0, 10)
_SIGNIFICANT_DIGITS = 6


class RatioCubeMethod(LabeledEnum):
    WHOLE_COLUMN = "Whole column"
    BLAND_PIXELS = "Bland pixels"


# "Generic", then the target presets of blandness.CHECKS
Blandness: type[LabeledEnum] = LabeledEnum(  # ty: ignore[invalid-assignment]
    "Blandness",
    {
        "GENERIC": BLANDNESS_GENERIC,
        **{f"PRESET{i}": name for i, name in enumerate(CHECKS) if name != GENERIC},
    },
)


def _limitsText(record: dict) -> str:
    """The blandness settings of a record, as text."""
    return record["thresholds"]["settings"]


COLUMN_TABLE_SUFFIX = ".columns.csv"
_COLUMN_FIELDS = (
    "strip",
    "column",
    "count",
    "sensorPixels",
    "oversampling",
    "effectiveCount",
    "fallback",
)
_BLANDNESS_FIELDS = ("blandness", "blandnessSettings")
_GEOMETRY_FIELDS = ("geometryFile", "geometryRoute")


def columnTableCsv(record: dict) -> str:
    """A ratio cube record's per-column table as CSV: one row per column with
    its support, fallback, the neighbor columns borrowed (``column:pixels``,
    ``;``-separated), and per good band (``B`` + the image's 1-based band
    number) the denominator mean, its std and the number of valid pixels.
    Every row also names the blandness check and its settings (windows,
    excluded ranges, floor, k and slope; empty for the Whole column method)
    and the geometry file with how it was found, so the table stands on its
    own."""
    bands = [f"B{b}" for b in record["bands"]]
    blandness = (
        (record["blandness"], record["thresholds"]["settings"])
        if record["blandness"]
        else ("", "")
    )
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            *_COLUMN_FIELDS,
            "borrowed",
            *_BLANDNESS_FIELDS,
            *_GEOMETRY_FIELDS,
            *(f"mean_{b}" for b in bands),
            *(f"std_{b}" for b in bands),
            *(f"n_{b}" for b in bands),
        ]
    )
    for entry in record["columns"]:
        writer.writerow(
            [
                *("" if entry[k] is None else entry[k] for k in _COLUMN_FIELDS),
                ";".join(f"{c}:{n}" for c, n in entry["borrowed"]),
                *blandness,
                *(record.get(k, "") for k in _GEOMETRY_FIELDS),
                *("" if v is None else v for v in entry["spectrum"]),
                *("" if v is None else v for v in entry["std"]),
                *entry["validCount"],
            ]
        )
    return out.getvalue()


def writeColumnTable(record: dict, dataFile: Path) -> list[tuple[Path, str]]:
    """Write the record's per-column table beside a saved ratio cube."""
    path = dataFile.with_suffix(COLUMN_TABLE_SUFFIX)
    path.write_text(columnTableCsv(record), encoding="utf-8")
    return [(path, "the per-column denominators, std and counts")]


SIDECAR_WRITERS["ratioCube"] = writeColumnTable


def _vnirApproximate(centersNm: np.ndarray, geometry: ColumnGeometry) -> bool:
    """Whether the cube has bands on both detectors, with columns that are the
    IR detector's (an IR sample band, or a joined cube's image columns): its
    VNIR bands are then only approximately column-matched."""
    bothDetectors = bool(
        (centersNm < VNIR_BELOW_NM).any() and (centersNm >= VNIR_BELOW_NM).any()
    )
    return bothDetectors and not geometry.sampleBand.lower().startswith("vnir")


def _round(value: float) -> float:
    return float(f"{value:.{_SIGNIFICANT_DIGITS}g}")


def _numbers(values: np.ndarray) -> list[float | None]:
    """JSON-safe numbers (NaN as None), kept to six significant digits."""
    return [_round(v) if np.isfinite(v) else None for v in values]


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
    # the cube in float64, its in-column run means, then the float32 result
    # (the bland mask works in blocks of pixels, so adds little): measured 5.6
    # on the default (Bland, Generic) settings, plus 25 percent
    # (test_ratio_cube_analysis)
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
        description="Which wavelength windows must be free of absorptions: "
        "Generic (broad windows over 1.05-2.65 µm) or a target preset (the "
        "windows of that mineral group's bands)",
    )
    depthFloor = FloatParameter(
        "Depth Floor",
        RATIO_CUBE_LIMITS.floor,
        range=LIMIT_RANGES["floor"],
        decimals=3,
        showSlider=False,
        description="A window fails only if its deepest continuum-removed dip "
        "(a fraction: 0.03 is 3 percent) is deeper than this floor and than "
        "Noise Multiple times the spectrum's own noise",
    )
    noiseMultiple = FloatParameter(
        "Noise Multiple",
        RATIO_CUBE_LIMITS.noiseMultiple,
        range=LIMIT_RANGES["noiseMultiple"],
        decimals=2,
        showSlider=False,
        description="k: a dip within k times the spectrum's per-band noise "
        "(estimated in each window) is not counted as a feature",
    )
    maxSlope = FloatParameter(
        "Maximum Slope",
        RATIO_CUBE_LIMITS.maxSlope,
        range=LIMIT_RANGES["maxSlope"],
        units="per µm",
        decimals=3,
        showSlider=False,
        description="The largest |relative slope| allowed in a window (the "
        "least-squares slope divided by the mean, per micrometer)",
    )
    excludedRanges = StringParameter(
        "Excluded Ranges",
        formatExclusions(DEFAULT_EXCLUSIONS),
        "Wavelength ranges (nm) no window looks at: the VNIR/IR detector join, "
        "the 1950-2100 nm CO2 residual (delete it to include it), and beyond "
        "2650 nm",
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
    geometryFile = FilePathParameter(
        "Geometry File",
        "The CRISM IN file (…_in…) whose IR Sample band gives each pixel's "
        "detector column. Empty: found automatically (the image's chosen "
        "file, a companion beside it, or beside the file it was made from). "
        "A file chosen here is used for this image everywhere",
        fileFilter="CRISM geometry (*.img *.IMG *.hdr *.HDR *.tif *.TIF);;"
        "All files (*)",
        placeholder="Automatic",
    )
    neighborRadius = IntParameter(
        "Neighbor Radius",
        2,
        range=NEIGHBOR_RADIUS_RANGE,
        units="columns",
        description="How far (columns each side, same strip) a column may "
        "borrow bland pixels from; 0 never borrows",
    )

    def __init__(self) -> None:
        super().__init__()
        self._image: VardaRaster | None = None
        self.geometryFile.sigParameterChanged.connect(self._useGeometryFile)

    def prepareFor(self, image: VardaRaster) -> None:
        """Show the image's chosen geometry file, and browse from its folder."""
        self._image = image
        self.geometryFile.startFolder = geometryStartFolder(image)
        with self.geometryFile.sigParameterChanged.blocked():
            self.geometryFile.set(geometryOverride(image))

    def _chosenFile(self) -> str:
        path = self.geometryFile.get().strip()
        return geometryDataFile(path) if path else ""

    def _useGeometryFile(self, _path: str) -> None:
        """A usable file chosen here becomes the image's override (every
        feature then uses it); clearing the field goes back to automatic."""
        image, path = self._image, self._chosenFile()
        if image is None:
            return
        if not path:
            if geometryOverride(image):
                setGeometryOverride(image, None)
        elif path != geometryOverride(image) and not checkGeometryFile(
            path, image, COLUMNS
        ):
            setGeometryOverride(image, path)

    def _check(self):
        return CHECKS[self.blandness.get().value]

    def _limits(self) -> Limits:
        return Limits(
            float(self.depthFloor.get()),
            float(self.noiseMultiple.get()),
            float(self.maxSlope.get()),
        )

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
        chosen = self._chosenFile()
        if chosen:
            problem = checkGeometryFile(chosen, image, COLUMNS)
            if problem:
                return f"Geometry File: {problem}"
        geometry = self._geometry(image)
        if geometry is None:
            where = (
                f"beside {image.filePath} or the file it was made from"
                if image.filePath
                else "beside the file it was made from (the image has no file)"
            )
            return (
                "No CRISM backplane with detector columns (the IN file, with "
                f"an IR Sample band) was found {where}, and the image is not an "
                "unprojected CRISM cube (no map projection, 640, 320, 128 or 64 "
                "columns), so each pixel's detector column is unknown. Choose "
                "the IN file (…_in…) with Geometry File."
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
            exclusions = parseExclusions(self.excludedRanges.get())
        except ValueError as error:
            return str(error)
        for name, value in (
            ("Depth Floor", self.depthFloor.get()),
            ("Noise Multiple", self.noiseMultiple.get()),
            ("Maximum Slope", self.maxSlope.get()),
        ):
            if not (math.isfinite(value) and value >= 0):
                return f"{name} must be 0 or more."
        check = self._check()
        short = [
            f"{name} ({count})"
            for name, count in windowBandCounts(wavelengths, check, exclusions).items()
            if count < MIN_WINDOW_BANDS
        ]
        if short:
            return (
                f"The {check.name} check needs at least {MIN_WINDOW_BANDS} good "
                f"bands in each window, outside the excluded ranges; too few in "
                f"{', '.join(short)}."
            )
        return ""

    def _geometry(self, image: VardaRaster) -> ColumnGeometry | None:
        return columnGeometryFor(image, self._chosenFile())

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
        # missing values (no-data, NaN) stay NaN: never filled, so they are not
        # counted as data, kept out of the denominators and NaN in the ratio
        cube, complete, bands = readGoodBands(image, fill=False)
        centers = image.bandModel.centers
        assert centers is not None
        wavelengths = centers[bands]
        strip, column = columnKeys(geometry)

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
            "geometryRoute": geometry.route,
            "binning": int(geometry.binning),
            "vnirApproximate": _vnirApproximate(centers, geometry),
            "vnirBelowNm": VNIR_BELOW_NM,
            "bands": [int(b) + 1 for b in bands],
        }
        if whole:
            use, minimum, radius = complete, 1, 0
        else:
            reportProgress(15, "finding bland pixels")
            runMeans = inColumnRunMeans(cube, strip, column, self.runLength.get())
            with np.errstate(invalid="ignore"):
                use = self._blandMask(runMeans, wavelengths) & complete
            del runMeans
            minimum, radius = self.minimumPixels.get(), self.neighborRadius.get()
        reportProgress(60, "averaging each column")
        denominators = columnDenominators(
            cube, strip, column, use, minimum, radius, line=geometry.ir_line
        )
        fallback, counts = self._flagMissing(denominators)
        if geometry.ir_line is not None:
            record["oversamplingFrom"] = (
                "image rows" if geometry.source == IMAGE_COLUMNS else "backplane lines"
            )
            oversampling = {
                key: max(counts[key] / n, 1.0) if n else 1.0
                for key, n in denominators.sensorPixels.items()
            }
        else:
            record["oversamplingFrom"] = "estimate: (pixels per row)²"
            oversampling = oversamplingEstimate(strip, column)
        reportProgress(80, "dividing")
        ratio = np.full((*cube.shape[:2], image.bandCount), np.nan, dtype=np.float32)
        ratio[:, :, bands] = applyRatio(cube, strip, column, denominators)

        record["columns"] = [
            {
                "strip": key[0],
                "column": key[1],
                "count": counts[key],
                "validCount": denominators.validCounts[key].tolist(),
                "sensorPixels": denominators.sensorPixels.get(key),
                "oversampling": _round(oversampling.get(key, 1.0)),
                "effectiveCount": _round(counts[key] / oversampling.get(key, 1.0)),
                "fallback": fallback.get(key),
                "borrowed": [list(b) for b in denominators.borrowed.get(key, [])],
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
        check, limits = self._check(), self._limits()
        exclusions = parseExclusions(self.excludedRanges.get())
        return {
            "windows": [
                {"name": w.name, "lowNm": w.lowNm, "highNm": w.highNm}
                for w in check.windows
            ],
            "excludedNm": [list(r) for r in exclusions],
            "depthFloor": limits.floor,
            "noiseMultiple": limits.noiseMultiple,
            "maxSlope": limits.maxSlope,
            "continuum": "upper convex hull of each window",
            "smoothingBands": SMOOTHING_BANDS,
            "noise": "MAD of second differences between neighboring bands",
            "settings": describeSettings(check, limits, exclusions),
        }

    def _blandMask(self, cube: np.ndarray, wavelengths: np.ndarray) -> np.ndarray:
        return blandMask(
            cube,
            wavelengths,
            self._check(),
            self._limits(),
            parseExclusions(self.excludedRanges.get()),
        )

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
        parts = [
            f"Ratio cube of {image.name}: {record['method']}: each pixel divided "
            "by the mean spectrum of its detector column"
        ]
        if record["blandness"]:
            parts[0] += (
                f" (blandness: {_limitsText(record)}, judged on means of "
                f"{record['runLength']} pixels in the column)"
            )
        parts.append(
            f"{total} columns, at least {record['minimumPixels']} pixels each, "
            f"borrowing from columns within Neighbor Radius "
            f"{record['neighborRadius']} of the same strip, else the whole column"
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
        parts.append(
            "missing values (no-data) are kept as NaN and never pooled; the "
            "standard error of a column's denominator is std / sqrt(validCount "
            f"/ oversampling), oversampling from {record['oversamplingFrom']}"
        )
        if record["geometry"] == IMAGE_COLUMNS:
            parts.append(
                "detector columns are the image columns (unprojected cube, no "
                f"backplane; {record['binning']} detector column(s) per image column)"
            )
        else:
            found = record.get("geometryRoute")
            parts.append(
                f"detector columns from {record['geometry']} of "
                f"{record['geometryFile']}" + (f" (found: {found})" if found else "")
            )
        if record["vnirApproximate"]:
            parts.append(
                f"VNIR bands (below {VNIR_BELOW_NM:g} nm) are only approximately "
                "column-matched, since the columns are the IR detector's"
            )
        return "; ".join(parts) + "."
