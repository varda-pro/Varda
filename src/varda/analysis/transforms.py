"""Transforms: PCA, MNF and ICA component images, and the inverse.

Choosing how many components matter is hard up front, so the forward
transforms default to every component and attach what an inverse needs to
the result. Flip through the component bands in a workspace to see where the
spatial coherence stops, then use Inverse Transform with that count to
rebuild a denoised image (or to keep just those components).
"""

from __future__ import annotations

import logging
from typing import ClassVar

import numpy as np
from PyQt6.QtWidgets import QWidget

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.eigenvalue_plot import EigenvaluePlot
from varda.analysis.linear_algebra import (
    FASTICA_TOLERANCE,
    TransformResult,
    describeComponentSelection,
    fastIca,
    flattenValid,
    inverseTransform,
    NoiseEstimate,
    mnf,
    noiseCovariance,
    pca,
    suggestedComponents,
    suggestionRule,
    weightedPca,
    unflatten,
    varianceShareText,
)
from varda.analysis.rasters import goodBands, readCube
from varda.common.entities import VardaRaster
from varda.common.grid import ImageChoices, sameGridChoices
from varda.common.parameter import (
    EnumParameter,
    ImageParameter,
    IntParameter,
    LabeledEnum,
    MultiChoiceParameter,
    Vec2Parameter,
)
from varda.common.spectral_axis import formatWavelength, wavelengthDecimals
from varda.common.vec2 import Vec2
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

logger = logging.getLogger(__name__)

TRANSFORM_METADATA_KEY = "transform"
# Inverse Transform: the components kept, and the count it suggested
SELECTION_METADATA_KEY = "componentSelection"


def _componentsParameter() -> IntParameter:
    # Each analysis declares its own copy: ParameterGroup only picks up
    # Parameters defined directly on the class.
    return IntParameter(
        "Components",
        10,
        range=(1, 1000),
        description=(
            "How many components to compute; all of them by default, so the "
            "number to keep can be chosen afterwards with Inverse Transform."
        ),
    )


class WeightKind(LabeledEnum):
    WEIGHTS = "Weights: larger counts more (e.g. sample counts)"
    INVERSE_VARIANCE = "Uncertainties σ (weight 1/σ²)"


def _wavelengthsParameter() -> Vec2Parameter:
    return Vec2Parameter(
        "Wavelengths",
        Vec2(0.0, 0.0),
        valueNames=("from", "to"),
        description=(
            "The bands that go in (in the image's unit; band numbers when it has "
            "no wavelengths). Bands marked bad in the image are always left out."
        ),
    )


def _weightsParameter() -> ImageParameter:
    return ImageParameter(
        "Weight Image",
        "Optional: an image on the same grid that says how much each value "
        "counts (e.g. how many frames went into it, or its uncertainty), with "
        "one band or the same bands (e.g. frames used, or uncertainties). "
        "Values with no weight (flagged, missing) move neither the components "
        "nor a pixel's scores.",
        optional=True,
        noneLabel="None (unweighted)",
    )


def _weightKindParameter() -> EnumParameter:
    return EnumParameter(
        "Weight Image Holds",
        WeightKind,
        description=(
            "What the weight image's values are: weights, where larger means a "
            "value counts more, or uncertainties σ, where larger means it "
            "counts less (each value is weighted 1/σ²)."
        ),
    )


def transformMetadata(
    result: TransformResult,
    image: VardaRaster,
    kind: str,
    bands: np.ndarray | None = None,
) -> dict:
    """What Inverse Transform needs, as plain JSON-able types; ``bands`` are
    the image's bands that went in (all of them by default). For MNF, also
    the noise covariance it whitened by and each band's noise σ (its
    diagonal's square root), in the input's units."""
    kept = np.arange(image.bandCount) if bands is None else np.asarray(bands)
    wavelengths = list(image.wavelengths)
    names = list(image.bandNames)
    return {
        "kind": kind,
        "sourceName": image.name,
        "sourceWavelengths": [
            float(wavelengths[i])
            if image.wavelengthsType is not str
            else str(wavelengths[i])
            for i in kept
        ],
        "sourceWavelengthUnits": image.wavelengthUnits,
        "sourceBandNames": [names[i] for i in kept],
        "mean": result.mean.tolist(),
        "inverse": result.inverse.tolist(),
        "eigenvalues": result.eigenvalues.tolist(),
        "explainedVariance": result.explainedVariance.tolist(),
        **noiseMetadata(result),
    }


def noiseMetadata(result: TransformResult) -> dict:
    noise = result.noiseCovariance
    if noise is None:
        return {}
    return {
        "noiseCovariance": noise.tolist(),
        "noiseSigma": np.sqrt(np.clip(np.diag(noise), 0, None)).tolist(),
    }


class _ComponentAnalysis(Analysis):
    """Shared plumbing: choose the bands, read them (and the weights), flatten
    the usable pixels, transform, rebuild a raster."""

    category = "Transforms"
    suffix: ClassVar[str] = ""
    units: ClassVar[str] = "components"
    # Declared (not assigned) here: each subclass supplies its own Parameters.
    components: IntParameter
    wavelengths: Vec2Parameter
    weights: ImageParameter
    weightKind: EnumParameter
    _image: VardaRaster | None = None  # the image being run on

    def prepareFor(self, image: VardaRaster) -> None:
        self.components.setRange((1, image.bandCount), value=image.bandCount)
        axis = self._bandAxis(image)
        lo, hi = float(axis.min()), float(axis.max())
        spectral = image.bandModel.isSpectral
        # Shown as the image's band centers read (its unit, rounded so each
        # band reads differently); band numbers are whole
        self.wavelengths.decimals = wavelengthDecimals(axis) if spectral else 0
        self.wavelengths.suffix = f" {image.bandModel.label}" if spectral else ""
        self.wavelengths.range = (Vec2(lo, hi), Vec2(lo, hi))
        self.wavelengths.set(Vec2(lo, hi))
        current = self.weights.get()
        self.weights.setChoices(lambda: self._weightChoices(image))
        self.weights.setOrClear(
            current if current in self._weightChoices(image).images else None
        )

    @staticmethod
    def _bandAxis(image: VardaRaster) -> np.ndarray:
        """Band centers in the image's own unit, or band numbers."""
        model = image.bandModel
        if model.centers is None:
            return np.arange(1, image.bandCount + 1, dtype=np.float64)
        return model.native(model.centers)

    def _weightChoices(self, image: VardaRaster) -> ImageChoices:
        """Images on ``image``'s grid with one band or its bands."""
        return sameGridChoices(
            list(self.context.images), image, bandCounts=(1, image.bandCount)
        )

    def keptBands(self, image: VardaRaster) -> np.ndarray:
        """The bands that go in: inside the wavelength range, not bad. Only an
        unset range (0 to 0, before the dialog prepares it) means every band;
        a range of one wavelength holds the band(s) centered there. The range
        is compared at the precision it is shown with, so rounding it for
        display never drops an edge band."""
        axis = self._bandAxis(image)
        lo, hi = sorted((self.wavelengths.get().x, self.wavelengths.get().y))
        if lo == hi == 0:
            keep = np.ones(axis.size, bool)
        else:
            decimals = self.wavelengths.decimals
            slack = 0.5 * 10.0 ** -(2 if decimals is None else decimals)
            keep = (axis >= lo - slack) & (axis <= hi + slack)
        return np.intersect1d(np.flatnonzero(keep), goodBands(image))

    def _rangeText(self, image: VardaRaster, lo: float, hi: float) -> str:
        if not image.bandModel.isSpectral:
            return f"bands {lo:.0f}–{hi:.0f}"
        decimals = wavelengthDecimals(self._bandAxis(image))
        return (
            f"{lo:.{decimals}f}–{formatWavelength(hi, image.bandModel.label, decimals)}"
        )

    def unavailableReason(self, image: VardaRaster) -> str:
        count = self.keptBands(image).size
        if count >= 2:
            return ""
        chosen = self.wavelengths.get()
        lo, hi = sorted((chosen.x, chosen.y))
        holds = "no usable band" if count == 0 else "1 usable band"
        return (
            f"{self._rangeText(image, lo, hi)} holds {holds}; "
            f"{self.suffix or self.name} needs at least two. Widen the range."
        )

    def inputSummary(self, image: VardaRaster) -> str:
        bands = self.keptBands(image)
        if bands.size < 2:
            return ""
        axis = self._bandAxis(image)
        return (
            f"Uses {bands.size} of {image.bandCount} bands, "
            f"{self._rangeText(image, axis[bands].min(), axis[bands].max())}."
        )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        self._image = image  # for transforms that need more than the cube
        bands = self.keptBands(image)
        if bands.size < 2:
            raise ValueError(self.unavailableReason(image))
        weightsImage = self.weights.get()
        if (
            weightsImage is not None
            and weightsImage not in self._weightChoices(image).images
        ):
            raise ValueError(
                f"{weightsImage.name} can't weight {image.name}: it must be on the "
                "same grid with one band or the same bands."
            )
        reportProgress(0, "reading image")
        weights = None
        if weightsImage is None:
            cube, _ = readCube(image)  # unusable pixels NaN
            cube = np.ascontiguousarray(cube[:, :, bands])
            X, valid = flattenValid(cube, None)
            W = None
        else:
            cube, weights = self._weightedCube(image, weightsImage, bands)
            valid = (weights > 0).any(axis=2)
            X, W = cube[valid], weights[valid]
        components = min(int(self.components.value), bands.size)

        reportProgress(20, "computing components")
        result = self._transform(cube, X, valid, components, W, weights)

        reportProgress(90, "building image")
        return self._resultRaster(image, result, valid, bands)

    def _weightedCube(
        self, image: VardaRaster, weightsImage: VardaRaster, bands: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """The image's bands (NaN for no data) and a weight per value (0 for
        missing values and missing weights)."""
        cube = np.array(image.getData(), dtype=np.float64)[:, :, bands]
        if image.bandModel.noData is not None:
            cube[cube == image.bandModel.noData] = np.nan
        weights = np.array(weightsImage.getData(), dtype=np.float64)
        if weightsImage.nodata is not None:
            weights[weights == weightsImage.nodata] = 0
        if weightsImage.bandCount > 1:
            weights = weights[:, :, bands]
        if self.weightKind.get() is WeightKind.INVERSE_VARIANCE:
            with np.errstate(divide="ignore"):
                weights = 1 / weights**2  # σ = 0: no weight, below
        weights = np.broadcast_to(weights, cube.shape)
        usable = np.isfinite(cube) & np.isfinite(weights) & (weights > 0)
        return cube, np.where(usable, weights, 0.0)

    def _resultRaster(
        self,
        image: VardaRaster,
        result: TransformResult,
        valid: np.ndarray,
        bands: np.ndarray | None = None,
    ) -> VardaRaster:
        """The component images, with what Inverse Transform needs."""
        names = self._bandNames(result)
        data = unflatten(result.scores, valid).astype(np.float32)
        description = "; ".join([f"{self.name} of {image.name}", *self._notes(result)])
        source = ArrayDataSource(
            data,
            wavelengths=np.array(names),
            wavelengthUnits=self.units,
            bandNames=names,
            transform=image.transform,
            crs=image.crs,
            description=description,
            extraMetadata={
                "explainedVariance": result.explainedVariance.tolist(),
                TRANSFORM_METADATA_KEY: {
                    **transformMetadata(result, image, self.suffix, bands),
                    **self._quality(result),
                },
            },
        )
        return VardaRaster(source, name=f"{image.name} {self.suffix}")

    def _transform(
        self,
        cube: np.ndarray,
        X: np.ndarray,
        valid: np.ndarray,
        components: int,
        W: np.ndarray | None,
        weights: np.ndarray | None,
    ) -> TransformResult:
        """``W``: X's weights (None unweighted); ``weights``: the cube's."""
        raise NotImplementedError

    def _bandNames(self, result: TransformResult) -> list[str]:
        raise NotImplementedError

    def _quality(self, result: TransformResult) -> dict:
        """What a user needs to report on the transform (noise model,
        convergence), kept with the transform's metadata."""
        return {}

    def _notes(self, result: TransformResult) -> list[str]:
        """Warnings for the result's description."""
        return []


class PcaAnalysis(_ComponentAnalysis):
    analysisId = "pca"
    name = "Principal Component Analysis (PCA)"
    memoryFactor = 7
    description = (
        "Rotates the bands onto uncorrelated axes ordered by variance. Band "
        "names carry each component's share of the total variance."
    )
    suffix = "PCA"
    components = _componentsParameter()
    wavelengths = _wavelengthsParameter()
    weights = _weightsParameter()
    weightKind = _weightKindParameter()

    def _transform(self, cube, X, valid, components, W, weights):
        return pca(X, components) if W is None else weightedPca(X, W, components)

    def _bandNames(self, result):
        return [
            f"PC {i + 1} ({varianceShareText(share)})"
            for i, share in enumerate(result.explainedVariance)
        ]


class MnfAnalysis(_ComponentAnalysis):
    analysisId = "mnf"
    name = "Minimum Noise Fraction (MNF)"
    memoryFactor = 8
    description = (
        "Like PCA but ordered by signal-to-noise: the noise covariance is "
        "estimated from differences between neighboring pixels and whitened "
        "away first, so the leading components hold coherent image structure."
    )
    suffix = "MNF"
    components = _componentsParameter()
    wavelengths = _wavelengthsParameter()
    weights = _weightsParameter()
    weightKind = _weightKindParameter()
    noiseEstimate = EnumParameter(
        "Noise Estimate",
        NoiseEstimate,
        NoiseEstimate.HORIZONTAL,
        description=(
            "Neighbor differences along a direction (match the sensor's noise, "
            "e.g. vertical for along-track striping), or the spread within a "
            "homogeneous ROI class"
        ),
    )
    noiseRoi = MultiChoiceParameter(
        "Noise ROI Classes",
        description=(
            "For a homogeneous-region estimate: uniform areas (e.g. dark or "
            "flat); the pixels of every ticked class are used together"
        ),
    )

    def __init__(self) -> None:
        super().__init__()
        self.noiseEstimate.sigParameterChanged.connect(self._offerNoiseRoi)

    def _usesRegion(self) -> bool:
        return self.noiseEstimate.get() is NoiseEstimate.HOMOGENEOUS_REGION

    def _offerNoiseRoi(self, _estimate=None) -> None:
        """Tick a noise ROI class to start from only while the estimate uses
        one, so a neighbor-difference result records none."""
        if not self._usesRegion():
            self.noiseRoi.set([])
        elif not self.noiseRoi.get():
            self.noiseRoi.set(self.noiseRoi.choices[:1])

    def prepareFor(self, image: VardaRaster) -> None:
        super().prepareFor(image)
        rois = self.context.rois
        names = list(rois.classes()) if rois else []
        kept = [n for n in self.noiseRoi.get() if n in names]
        self.noiseRoi.setChoices(names, selected=kept)
        self._offerNoiseRoi()

    def unavailableReason(self, image: VardaRaster) -> str:
        if self._usesRegion() and not self._noiseClasses():
            return "Draw an ROI over a uniform area and tick it as a noise ROI class."
        return super().unavailableReason(image)

    def _noiseClasses(self) -> list[str]:
        rois = self.context.rois
        return [
            n for n in self.noiseRoi.get() if rois is not None and n in rois.classes()
        ]

    def _transform(self, cube, X, valid, components, W, weights):
        method = NoiseEstimate(self.noiseEstimate.get())
        region = None
        if method is NoiseEstimate.HOMOGENEOUS_REGION:
            names, rois, image = self._noiseClasses(), self.context.rois, self._image
            if not names or rois is None or image is None:
                raise ValueError(
                    "Pick a noise ROI class for the homogeneous-region estimate."
                )
            region = np.logical_or.reduce([rois.classMask(n, image) for n in names])
        noise = noiseCovariance(cube, valid, method, region, weights)
        return mnf(X, noise, components, W)

    def _bandNames(self, result):
        return [f"MNF {i + 1}" for i in range(result.scores.shape[1])]

    def _quality(self, result):
        region = self._usesRegion()
        return {
            "noiseEstimate": NoiseEstimate(self.noiseEstimate.get()).name,
            "noiseRoiClasses": self._noiseClasses() if region else [],
        }


class IcaAnalysis(_ComponentAnalysis):
    analysisId = "ica"
    name = "Independent Component Analysis (ICA)"
    memoryFactor = 10
    description = (
        "FastICA on the leading principal components: finds statistically "
        "independent, non-Gaussian source images, e.g. to separate mixed "
        "spectral end-members."
    )
    suffix = "ICA"
    components = _componentsParameter()
    wavelengths = _wavelengthsParameter()
    weights = _weightsParameter()
    weightKind = _weightKindParameter()
    maxIterations = IntParameter(
        "Max Iterations",
        200,
        range=(10, 5000),
        description="Iteration cap for the FastICA fixed-point updates.",
    )

    def _transform(self, cube, X, valid, components, W, weights):
        return fastIca(
            X, components, maxIterations=int(self.maxIterations.value), weights=W
        )

    def _quality(self, result):
        return {
            "iterations": result.iterations,
            "maxIterations": int(self.maxIterations.value),
            "converged": result.converged,
            "tolerance": FASTICA_TOLERANCE,
        }

    def _notes(self, result):
        if result.converged:
            return []
        note = (
            f"did not converge in {result.iterations} iterations (raise Max "
            "Iterations; the components may not be independent)"
        )
        logger.warning("%s: %s", self.name, note)
        return [note]

    def _bandNames(self, result):
        return [f"IC {i + 1}" for i in range(result.scores.shape[1])]


class InverseOutput(LabeledEnum):
    DENOISED_IMAGE = "Denoised image (inverse transform to the original bands)"
    FIRST_COMPONENTS = "Only the chosen components (as component bands)"


class InverseTransformAnalysis(Analysis):
    analysisId = "inverse_transform"
    name = "Inverse Transform (Keep N Components)"
    category = "Transforms"
    description = (
        "Decide how many PCA / MNF / ICA components carry signal, then either "
        "rebuild the image in its original bands from just those (dropping the "
        "noise in the rest) or keep only those component bands. The plot "
        "shows the eigenvalues: drag the cutoff to where they flatten out, and "
        "check the component images themselves for where spatial coherence "
        "stops."
    )

    components = IntParameter(
        "Components to keep",
        10,
        range=(1, 1000),
        description="Keep the first N components (ticks them in the list below).",
    )
    keep = MultiChoiceParameter(
        "Components",
        description=(
            "Exactly which components to use — untick e.g. component 1 to drop "
            "albedo, or type a range such as 3-19."
        ),
    )
    output = EnumParameter("Output", InverseOutput)

    def __init__(self) -> None:
        super().__init__()
        self.components.sigParameterChanged.connect(self._reselectFirst)

    def _reselectFirst(self, count) -> None:
        if self.keep.choices:
            self.keep.set(self.keep.choices[: int(count)])

    @staticmethod
    def _info(image: VardaRaster) -> dict | None:
        return image.extraMetadata.get(TRANSFORM_METADATA_KEY)

    def unavailableReason(self, image: VardaRaster) -> str:
        if self._info(image) is None:
            return (
                "Inverse Transform works on the result of a PCA, MNF or ICA "
                "transform; run one first (keeping all components)."
            )
        if self.keep.choices and not self.keep.get():
            return "Tick at least one component to keep."
        return ""

    def prepareFor(self, image: VardaRaster) -> None:
        info = self._info(image)
        if info is None:
            return
        eigenvalues = np.asarray(info["eigenvalues"])
        suggested = suggestedComponents(eigenvalues, info["kind"])
        # Offer every component; the count below ticks the first N of them
        self.keep.setChoices(list(image.bandNames), selected=[])
        self.components.setRange((1, eigenvalues.size), value=suggested)
        self._reselectFirst(self.components.get())

    def createPreviewWidget(self, image: VardaRaster) -> QWidget | None:
        info = self._info(image)
        if info is None:
            return None
        return EigenvaluePlot(
            info["eigenvalues"], self.components, logScale=info["kind"] != "MNF"
        )

    def _selectedIndices(self, image: VardaRaster, total: int) -> list[int]:
        """0-based components to use: the ticked ones once prepared for an
        image, else the first N."""
        if self.keep.choices:
            chosen = set(self.keep.get())
            return [i for i, name in enumerate(self.keep.choices) if name in chosen]
        return list(range(min(int(self.components.value), total)))

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        info = self._info(image)
        if info is None:
            raise ValueError(self.unavailableReason(image))
        kind, sourceName = info["kind"], info["sourceName"]
        indices = self._selectedIndices(image, len(info["eigenvalues"]))
        if not indices:
            raise ValueError("Tick at least one component to keep.")
        which = describeComponentSelection(indices)

        selection = {
            "kind": kind,
            "suggested": suggestedComponents(np.asarray(info["eigenvalues"]), kind),
            "rule": suggestionRule(kind),
            "kept": [i + 1 for i in indices],
        }

        reportProgress(0, "reading components")
        cube, _ = readCube(image)
        if self.output.value is InverseOutput.FIRST_COMPONENTS:
            kept = {
                **info,
                "inverse": [info["inverse"][i] for i in indices],
                "eigenvalues": [info["eigenvalues"][i] for i in indices],
                "explainedVariance": [info["explainedVariance"][i] for i in indices],
            }
            source = ArrayDataSource(
                cube[:, :, indices].astype(np.float32),
                wavelengths=np.array([image.wavelengths[i] for i in indices]),
                wavelengthUnits=image.wavelengthUnits,
                bandNames=[image.bandNames[i] for i in indices],
                transform=image.transform,
                crs=image.crs,
                nodata=image.nodata,
                description=f"{which.capitalize()} of {image.name}",
                extraMetadata={
                    **image.extraMetadata,
                    TRANSFORM_METADATA_KEY: kept,
                    SELECTION_METADATA_KEY: selection,
                },
            )
            return VardaRaster(source, name=f"{sourceName} {kind} ({which})")

        scores, valid = flattenValid(cube, None)
        reportProgress(30, f"rebuilding from {which}")
        rebuilt = inverseTransform(
            scores, np.asarray(info["inverse"]), np.asarray(info["mean"]), indices
        )
        reportProgress(85, "building image")
        source = ArrayDataSource(
            unflatten(rebuilt, valid).astype(np.float32),
            wavelengths=np.array(info["sourceWavelengths"]),
            wavelengthUnits=info["sourceWavelengthUnits"],
            bandNames=list(info["sourceBandNames"]),
            transform=image.transform,
            crs=image.crs,
            description=f"{sourceName} rebuilt from {kind} {which}",
            extraMetadata={SELECTION_METADATA_KEY: selection},
        )
        return VardaRaster(source, name=f"{sourceName} {kind} inverse ({which})")
