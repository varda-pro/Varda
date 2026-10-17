"""Mapping endmembers across an image (SAM, linear unmixing, MTMF) and the
pixel purity index, as Processing entries. The spectral hourglass workspace
builds its maps with the same functions.

The Processing versions take their endmembers from the workspace's ROIs:
each chosen ROI's mean spectrum is one endmember.
"""

from __future__ import annotations

import logging
from enum import Enum

import numpy as np

from varda.analysis.analysis import Analysis, ProgressCallback, seedParameter
from varda.analysis.classification import _RoiTrainingAnalysis, _requireRois
from varda.analysis.endmembers import pixelPurityIndex
from varda.analysis.linear_algebra import flattenValid, mnf, noiseCovariance
from varda.analysis.rasters import readGoodBands, singleBandRaster
from varda.analysis.unmixing import (
    Constraint,
    UnmixingReport,
    linearUnmix,
    mixtureTunedMatchedFilter,
    spectralAngles,
)
from varda.common.entities import VardaRaster
from varda.common.parameter import (
    EnumParameter,
    FloatParameter,
    IntParameter,
    MultiChoiceParameter,
)
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

logger = logging.getLogger(__name__)

DEFAULT_SAM_MAX_ANGLE = 0.10  # radians, ENVI's default


class MapMethod(Enum):
    SAM = "Spectral Angle Mapper (SAM)"
    UNMIX = "Linear Spectral Unmixing"
    MTMF = "Mixture Tuned Matched Filtering (MTMF)"


_SUFFIX = {MapMethod.SAM: "SAM", MapMethod.UNMIX: "unmixing", MapMethod.MTMF: "MTMF"}


def mapBands(
    method: MapMethod,
    names: list[str],
    X: np.ndarray | None = None,
    spectra: np.ndarray | None = None,
    *,
    maxAngle: float = DEFAULT_SAM_MAX_ANGLE,
    constraint: Constraint = Constraint.FULLY_CONSTRAINED,
    mnfData: np.ndarray | None = None,
    targets: np.ndarray | None = None,
    progress: ProgressCallback | None = None,
    report: UnmixingReport | None = None,
) -> tuple[np.ndarray, list[str]]:
    """(pixels, bands) map values and band names.

    SAM and unmixing compare ``X`` (pixels, bands) with endmember ``spectra``;
    MTMF compares ``mnfData`` (pixels, components) with ``targets`` in MNF space.
    Unmixing fills in ``report`` (how the abundances were solved).
    """
    if method is MapMethod.SAM:
        assert X is not None and spectra is not None
        angles = spectralAngles(X, spectra)
        classes = np.argmin(np.nan_to_num(angles, nan=np.inf), axis=1).astype(float)
        with np.errstate(invalid="ignore"):
            classes[~(np.nanmin(angles, axis=1) <= maxAngle)] = np.nan
        return np.column_stack([classes, angles]), [
            "Class",
            *[f"Angle({n})" for n in names],
        ]
    if method is MapMethod.UNMIX:
        assert X is not None and spectra is not None
        abundances, rmse = linearUnmix(X, spectra, constraint, progress, report=report)
        return np.column_stack([abundances, rmse]), [*names, "RMS Error"]
    assert mnfData is not None and targets is not None
    mf, infeasibility = mixtureTunedMatchedFilter(mnfData, targets)
    return (
        np.column_stack([mf, infeasibility]),
        [*[f"MF({n})" for n in names], *[f"Infeasibility({n})" for n in names]],
    )


def mapRaster(
    image: VardaRaster,
    validFlat: np.ndarray,
    bands: np.ndarray,
    bandNames: list[str],
    *,
    method: MapMethod,
    names: list[str],
    colors: list[str],
    detail: str,
    metadata: dict | None = None,
) -> VardaRaster:
    """A map result over ``image``'s footprint; ``bands`` holds one row per
    flat index in ``validFlat``. The endmember names and colors go in the
    metadata (``classes``, ``classColors``), with any other ``metadata``."""
    shape = (image.height, image.width)
    out = np.full((shape[0] * shape[1], bands.shape[1]), np.nan, dtype=np.float32)
    out[validFlat] = bands
    source = ArrayDataSource(
        out.reshape(shape + (bands.shape[1],)),
        wavelengths=np.array(bandNames),
        wavelengthUnits="endmembers",
        bandNames=bandNames,
        transform=image.transform,
        crs=image.crs,
        description=f"{method.value} of {image.name} ({detail})",
        extraMetadata={
            "classes": names,
            "classColors": colors,
            "mapMethod": method.name,
            **(metadata or {}),
        },
    )
    return VardaRaster(source, name=f"{image.name} {_SUFFIX[method]}")


UNMIXING_METADATA_KEY = "unmixing"


def unmixingMetadata(constraint: Constraint, report: UnmixingReport) -> dict:
    """How an unmixing result was solved, for its metadata."""
    return {"constraint": constraint.name, **report.asMetadata()}


def unmixingDetail(constraint: Constraint, report: UnmixingReport) -> str:
    """The constraint for the description, with a warning when ADMM left
    pixels short of its tolerance."""
    detail = constraint.value
    if report.pixelsConverged < report.pixels:
        short = report.pixels - report.pixelsConverged
        detail += (
            f"; {short:,} of {report.pixels:,} pixels did not converge in "
            f"{report.maxIterations} ADMM iterations"
        )
        logger.warning("Linear unmixing: %s", detail)
    return detail


# --- Processing entries ------------------------------------------------------------


class PixelPurityAnalysis(Analysis):
    analysisId = "pixel_purity_index"
    name = "Pixel Purity Index (PPI)"
    memoryFactor = 18
    memoryFixed = 80 * 2**20
    category = "Endmembers"
    description = (
        "Counts how often each pixel lands at an extreme of random projections "
        "of the image's leading bands (run it on an MNF result). High counts "
        "mark candidate pure pixels; the Spectral Hourglass workspace does this "
        "and the rest of the endmember workflow interactively."
    )

    components = IntParameter(
        "Bands Used",
        10,
        range=(2, 1000),
        description="Leading bands (e.g. MNF components)",
    )
    iterations = IntParameter("Iterations", 10_000, range=(100, 1_000_000))
    threshold = FloatParameter(
        "Threshold",
        2.5,
        range=(0.01, 100.0),
        step=0.5,
        description="Data units (MNF: noise sigmas)",
    )
    seed = seedParameter(
        "Picks the random skewers; the same seed, iterations and bands give "
        "the same counts."
    )

    def prepareFor(self, image: VardaRaster) -> None:
        self.components.setRange(
            (2, max(2, image.bandCount)), value=min(10, image.bandCount)
        )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        reportProgress(0, "reading image")
        cube, _, _ = readGoodBands(image)
        k = min(int(self.components.value), cube.shape[2])
        X, valid = flattenValid(cube[:, :, :k], None)
        iterations = int(self.iterations.value)
        threshold = float(self.threshold.value)
        seed = int(self.seed.value)
        counts = pixelPurityIndex(
            X,
            iterations,
            threshold,
            seed=seed,
            progress=lambda p, m: reportProgress(5 + int(0.9 * p), m),
        )
        values = np.full(valid.shape, np.nan)
        values[valid] = counts
        return singleBandRaster(
            image,
            values,
            name=f"{image.name} PPI",
            bandName="PPI",
            units="count",
            description=(
                f"PPI of {image.name}: {iterations} iterations, seed {seed}, "
                f"threshold {threshold:g}, {k} bands"
            ),
        )


def _roiEndmemberParameter() -> MultiChoiceParameter:
    return MultiChoiceParameter(
        "ROI classes (one endmember each)",
        description="Each ticked class's mean spectrum (over all its ROIs) is one endmember.",
    )


class _RoiEndmemberMapping(_RoiTrainingAnalysis):
    """Maps the mean spectra of the chosen ROIs (two or more)."""

    category = "Endmembers"
    method: MapMethod

    def _endmembers(self, image: VardaRaster, cube: np.ndarray, valid: np.ndarray):
        rois = _requireRois(self)
        names = self._chosenClasses(rois)
        if len(names) < 2:
            raise ValueError(
                "Mapping needs at least two ROI classes (one endmember each)."
            )
        spectra, masks = [], []
        for name in names:
            mask = rois.classMask(name, image) & valid
            if not mask.any():
                raise ValueError(
                    f"ROI class {name} has no valid pixels in {image.name}."
                )
            spectra.append(cube[mask].mean(axis=0))
            masks.append(mask)
        return names, self._classColors(rois, names), np.stack(spectra), masks

    def _mapKwargs(self) -> dict:
        return {}

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        reportProgress(0, "reading image")
        cube, valid, _ = readGoodBands(image)
        names, colors, spectra, masks = self._endmembers(image, cube, valid)
        validFlat = np.flatnonzero(valid.ravel())
        X = cube.reshape(-1, cube.shape[2])[validFlat]
        reportProgress(20, "mapping")
        self._metadata: dict = {}
        bands, bandNames, detail = self._map(
            cube, valid, X, spectra, masks, names, reportProgress
        )
        reportProgress(95, "building image")
        return mapRaster(
            image,
            validFlat,
            bands,
            bandNames,
            method=self.method,
            names=names,
            colors=colors,
            detail=detail,
            metadata=self._metadata,
        )

    def _map(self, cube, valid, X, spectra, masks, names, reportProgress):
        """(bands, band names, detail); may add to ``self._metadata``."""
        raise NotImplementedError


class SamMappingAnalysis(_RoiEndmemberMapping):
    analysisId = "sam_roi_endmembers"
    name = "Spectral Angle Mapper (ROI Endmembers)"
    memoryFactor = 5
    description = (
        "Classifies every pixel by the smallest spectral angle to the mean "
        "spectra of the chosen ROIs (brightness does not matter), leaving it "
        "unclassified beyond the maximum angle. Bands: Class, then one angle "
        "per endmember."
    )
    method = MapMethod.SAM
    rois = _roiEndmemberParameter()
    maxAngle = FloatParameter(
        "Max. Angle",
        DEFAULT_SAM_MAX_ANGLE,
        range=(0.001, 1.5),
        units="rad",
        step=0.01,
        decimals=3,
    )

    def _map(self, cube, valid, X, spectra, masks, names, reportProgress):
        angle = float(self.maxAngle.value)
        bands, bandNames = mapBands(MapMethod.SAM, names, X, spectra, maxAngle=angle)
        return bands, bandNames, f"max angle {angle:g} rad"


class UnmixingAnalysis(_RoiEndmemberMapping):
    analysisId = "unmixing_roi_endmembers"
    name = "Linear Spectral Unmixing (ROI Endmembers)"
    memoryFactor = 6
    description = (
        "Abundance of each chosen ROI's mean spectrum in every pixel, plus the "
        "RMS error of the fit (structure in it points to a missing endmember). "
        "Fully constrained by default: abundances are non-negative and sum to one."
    )
    method = MapMethod.UNMIX
    rois = _roiEndmemberParameter()
    constraint = EnumParameter("Constraint", Constraint, Constraint.FULLY_CONSTRAINED)

    def _map(self, cube, valid, X, spectra, masks, names, reportProgress):
        constraint = Constraint(self.constraint.value)
        report = UnmixingReport()
        bands, bandNames = mapBands(
            MapMethod.UNMIX,
            names,
            X,
            spectra,
            constraint=constraint,
            progress=lambda p, m: reportProgress(20 + int(0.7 * p), m),
            report=report,
        )
        self._metadata[UNMIXING_METADATA_KEY] = unmixingMetadata(constraint, report)
        return bands, bandNames, unmixingDetail(constraint, report)


class MtmfAnalysis(_RoiEndmemberMapping):
    analysisId = "mtmf_roi_endmembers"
    name = "Mixture Tuned Matched Filtering (ROI Endmembers)"
    memoryFactor = 7
    description = (
        "Matched-filter score (≈ abundance) and infeasibility for each chosen "
        "ROI, computed in the image's MNF space. Trust high scores only where "
        "infeasibility is low."
    )
    method = MapMethod.MTMF
    rois = _roiEndmemberParameter()
    components = IntParameter("MNF Components", 10, range=(2, 1000))

    def prepareFor(self, image: VardaRaster) -> None:
        super().prepareFor(image)
        self.components.setRange(
            (2, max(2, image.bandCount)), value=min(10, image.bandCount)
        )

    def _map(self, cube, valid, X, spectra, masks, names, reportProgress):
        reportProgress(25, "MNF transform")
        k = min(int(self.components.value), cube.shape[2])
        Xv, validMask = flattenValid(cube, None)
        result = mnf(Xv, noiseCovariance(cube, validMask), k)
        scores = result.scores
        validFlat = np.flatnonzero(validMask.ravel())
        targets = []
        for mask in masks:
            rows = np.searchsorted(validFlat, np.flatnonzero(mask.ravel()))
            targets.append(scores[rows].mean(axis=0))
        reportProgress(70, "matched filtering")
        bands, bandNames = mapBands(
            MapMethod.MTMF, names, mnfData=scores, targets=np.stack(targets)
        )
        return bands, bandNames, f"{k} MNF components"
