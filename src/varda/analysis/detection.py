"""Target and anomaly detection.

- RX (global): squared Mahalanobis distance of each pixel from the scene's
  background statistics (SPy's RX).
- RX (local): distance from a local background mean (an outer window, with an
  inner guard window left out), using the scene's covariance. Varda's own: a
  box-filter mean keeps it fast on full cubes, where SPy's windowed RX loops
  pixel by pixel; the covariance is global, not per window.
- ACE and matched filter: response to the mean spectrum of each chosen ROI
  class (SPy's ACE and MatchedFilter), one band per target.

All run on the valid pixels in row blocks, so progress keeps being reported.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from scipy.ndimage import uniform_filter

from varda.analysis import _spy
from varda.analysis.analysis import ProgressCallback
from varda.analysis.classification import _requireRois, _RoiTrainingAnalysis
from varda.analysis.rasters import readGoodBands, singleBandRaster
from varda.analysis.analysis import Analysis
from varda.common.entities import VardaRaster
from varda.common.parameter import IntParameter, MultiChoiceParameter
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

CATEGORY = "Target Detection"
BLOCK_PIXELS = 65_536
MAX_OUTER_WINDOW = 101


def _inBlocks(
    X: np.ndarray,
    score: Callable[[np.ndarray], np.ndarray],
    progress: ProgressCallback,
    start: int,
    span: int,
) -> np.ndarray:
    """``score`` applied to (pixels, bands) ``X`` in blocks, reporting progress."""
    out = np.empty(X.shape[0], dtype=np.float64)
    for first in range(0, X.shape[0], BLOCK_PIXELS):
        block = X[first : first + BLOCK_PIXELS]
        out[first : first + block.shape[0]] = np.asarray(
            score(block[:, None, :])
        ).ravel()
        done = (first + block.shape[0]) / max(X.shape[0], 1)
        progress(
            start + int(span * done), f"{first + block.shape[0]}/{X.shape[0]} pixels"
        )
    return out


def _scoreImage(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    out = np.full(valid.shape, np.nan, dtype=np.float32)
    out[valid] = values
    return out


class RxAnalysis(Analysis):
    analysisId = "rx_global"
    software = ("spectral",)
    name = "RX Anomaly (Global)"
    memoryFactor = 5.5
    category = CATEGORY
    description = (
        "Squared Mahalanobis distance of each pixel from the scene's mean and "
        "covariance: pixels unlike the rest of the scene score high."
    )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        reportProgress(0, "reading image")
        cube, valid, _ = readGoodBands(image)
        X = cube[valid]
        reportProgress(15, "background statistics")
        detector = _spy.RX(background=_spy.backgroundStats(X))
        scores = _inBlocks(X, detector, reportProgress, 20, 80)
        return singleBandRaster(
            image,
            _scoreImage(scores, valid),
            name=f"{image.name} RX",
            bandName="RX score",
        )


class LocalRxAnalysis(Analysis):
    analysisId = "rx_local"
    software = ("spectral", "scipy")
    name = "RX Anomaly (Local)"
    memoryFactor = 9
    category = CATEGORY
    description = (
        "Distance of each pixel from the mean of its surroundings (an outer "
        "window minus an inner guard window), with the scene's covariance: "
        "finds small targets that differ from their neighborhood."
    )

    # Odd sizes only (step 2), and the outer window at least two pixels wider
    # than the inner one: the values recorded are the values used.
    innerWindow = IntParameter(
        "Inner Window",
        3,
        range=(1, 31),
        description="Guard window (pixels, odd)",
        step=2,
    )
    outerWindow = IntParameter(
        "Outer Window",
        15,
        range=(3, MAX_OUTER_WINDOW),
        description="Background window (pixels, odd, at least inner + 2)",
        step=2,
    )

    def __init__(self) -> None:
        super().__init__()
        self.innerWindow.sigParameterChanged.connect(self._nestWindows)
        self._nestWindows(self.innerWindow.get())

    def _nestWindows(self, inner: int) -> None:
        self.outerWindow.setRange((int(inner) + 2, MAX_OUTER_WINDOW))

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        inner = int(self.innerWindow.value)
        outer = int(self.outerWindow.value)
        reportProgress(0, "reading image")
        cube, valid, _ = readGoodBands(image)
        filled = np.where(valid[:, :, None], cube, np.nanmean(cube[valid], axis=0))
        reportProgress(20, "local background")
        sumOuter = (
            uniform_filter(filled, size=(outer, outer, 1), mode="reflect") * outer**2
        )
        sumInner = (
            uniform_filter(filled, size=(inner, inner, 1), mode="reflect") * inner**2
        )
        localMean = (sumOuter - sumInner) / (outer**2 - inner**2)
        stats = _spy.backgroundStats(cube[valid])
        inverse = np.asarray(stats.inv_cov)
        difference = (cube - localMean)[valid]
        reportProgress(60, "distances")
        scores = np.einsum("ij,jk,ik->i", difference, inverse, difference)
        return singleBandRaster(
            image,
            _scoreImage(scores, valid),
            name=f"{image.name} local RX {inner}/{outer}",
            bandName=f"Local RX {inner}/{outer}",
        )


def _targetParameter() -> MultiChoiceParameter:
    return MultiChoiceParameter(
        "Targets (ROI classes)",
        description="Each ticked class's mean spectrum is one target (one band each).",
    )


class _TargetDetection(_RoiTrainingAnalysis):
    category = CATEGORY
    detectorLabel = ""

    def _detector(self, target: np.ndarray, stats):
        raise NotImplementedError

    def unavailableReason(self, image: VardaRaster) -> str:
        collection = self.context.rois
        if collection is None or not collection.classes():
            return "Draw an ROI on the target material first; its mean spectrum is the target."
        if self.rois.choices and not self.rois.get():
            return "Tick at least one target ROI class."
        return ""

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        rois = _requireRois(self)
        names = self._chosenClasses(rois)
        if not names:
            raise ValueError("Tick at least one target ROI class.")
        reportProgress(0, "reading image")
        cube, valid, _ = readGoodBands(image)
        X = cube[valid]
        stats = _spy.backgroundStats(X)
        bands = []
        for k, name in enumerate(names):
            mask = rois.classMask(name, image) & valid
            if not mask.any():
                raise ValueError(
                    f"ROI class {name} has no valid pixels in {image.name}."
                )
            detector = self._detector(cube[mask].mean(axis=0), stats)
            span = 80 // len(names)
            scores = _inBlocks(X, detector, reportProgress, 20 + k * span, span)
            bands.append(_scoreImage(scores, valid))
        source = ArrayDataSource(
            np.stack(bands, axis=2),
            wavelengths=np.array(names),
            wavelengthUnits="targets",
            bandNames=names,
            transform=image.transform,
            crs=image.crs,
            description=f"{self.detectorLabel} of {image.name} for {', '.join(names)}",
        )
        return VardaRaster(source, name=f"{image.name} {self.detectorLabel}")


class AceAnalysis(_TargetDetection):
    analysisId = "ace"
    software = ("spectral",)
    name = "ACE Target Detection"
    memoryFactor = 7
    detectorLabel = "ACE"
    description = (
        "Adaptive coherence estimator: how closely each pixel's spectrum, "
        "whitened by the scene's statistics, points the way of the target's "
        "(0 to 1; insensitive to brightness)."
    )
    rois = _targetParameter()

    def _detector(self, target, stats):
        ace = _spy.ACE(target, background=stats)

        def score(X: np.ndarray) -> np.ndarray:
            # A pixel at the background mean has no direction (0/0): it is
            # nothing like the target, so it scores 0.
            with np.errstate(invalid="ignore"):
                return np.nan_to_num(np.asarray(ace(X)), nan=0.0)

        return score


class MatchedFilterAnalysis(_TargetDetection):
    analysisId = "matched_filter"
    software = ("spectral",)
    name = "Matched Filter"
    memoryFactor = 4
    detectorLabel = "matched filter"
    description = (
        "Matched filter: an estimate of how much of the target is in each "
        "pixel (1 = pure target, 0 = background), suppressing the background."
    )
    rois = _targetParameter()

    def _detector(self, target, stats):
        return _spy.MatchedFilter(stats, target)
