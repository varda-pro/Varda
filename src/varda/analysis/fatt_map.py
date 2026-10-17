"""FATT detection map: where in the image library targets fit (DAFA/TT).

Run from the Factor Analysis Target Transformation window with the targets
ticked there; see ``fatt.windowedNrmse`` for the method.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.fatt import (
    DEFAULT_RANGE_NM,
    DEFAULT_WINDOW_FACTORS,
    DEFAULT_WINDOW_SHAPES,
    MIN_BANDS,
    NRMSE_THRESHOLD,
    WINDOW_MEMORY_BUDGET,
    windowedNrmse,
)
from varda.analysis.image_math import readWithGaps
from varda.analysis.rasters import readCube
from varda.analysis.spectral_library import LibrarySpectrum, resampleTo
from varda.common.entities import VardaRaster
from varda.common.grid import compatibleImages
from varda.common.parameter import FloatParameter, ImageParameter, IntParameter
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

# The threshold is set in units of 10⁻⁴ so the spin box reads 1.5, not 0.00015
THRESHOLD_UNIT = 1e-4


def bandsInRange(
    image: VardaRaster, low: float, high: float
) -> tuple[np.ndarray, np.ndarray | None]:
    """Indices of the image's good bands within [low, high] nm, and their FWHM."""
    model = image.bandModel
    if model.centers is None:
        raise ValueError("FATT needs an image with wavelengths.")
    good = ~model.badBands if model.badBands.size else np.ones(model.bandCount, bool)
    bands = np.flatnonzero(good & (model.centers >= low) & (model.centers <= high))
    fwhm = None if model.fwhm is None else model.fwhm[bands]
    return bands, fwhm


def defaultRange(image: VardaRaster) -> tuple[float, float]:
    """1.7-2.6 µm (Lin, Tarnas et al. 2021) when the image covers it with five
    or more bands, else the image's whole range."""
    centers = image.bandModel.centers
    if centers is None or not centers.size:
        return DEFAULT_RANGE_NM
    low, high = DEFAULT_RANGE_NM
    if ((centers >= low) & (centers <= high)).sum() >= MIN_BANDS:
        return DEFAULT_RANGE_NM
    return float(np.min(centers)), float(np.max(centers))


class FattMapAnalysis(Analysis):
    analysisId = "fatt_map"
    software = ("spectral",)
    name = "FATT Detection Map"
    # the image read and its band subset (float64) and the per-target
    # results, plus the windows being fitted (WINDOW_MEMORY_BUDGET)
    memoryFactor = 4
    memoryFixed = WINDOW_MEMORY_BUDGET
    description = (
        "Target transformation in small moving windows of several shapes "
        "(DAFA/TT, Lin, Tarnas et al. 2021). Bands: how many targets fit each "
        "pixel in every window shape, each target's normalized RMSE, and the "
        "mean signal. Exclude shadows (a Shadow Mask) or dim pixels: dark "
        "windows fit almost anything. "
        "Detections are candidates: confirm them, e.g. with ratioed spectra."
    )
    category = "Endmembers"

    factors = IntParameter(
        "Factors per window",
        DEFAULT_WINDOW_FACTORS,
        range=(1, 49),
        description="The dimension of each window's factor space (15 in recent work)",
    )
    threshold = FloatParameter(
        "Threshold",
        NRMSE_THRESHOLD / THRESHOLD_UNIT,
        range=(0.001, 1000.0),
        units="× 10⁻⁴ nRMSE",
        decimals=3,
        showSlider=False,
        description="A target fits where its normalized RMSE is below this "
        "(1.5 for CRISM at 1.7–2.6 µm; it scales with the number of bands)",
    )
    minWavelength = FloatParameter(
        "From", DEFAULT_RANGE_NM[0], range=(0.0, 100000.0), units="nm", showSlider=False
    )
    maxWavelength = FloatParameter(
        "To", DEFAULT_RANGE_NM[1], range=(0.0, 100000.0), units="nm", showSlider=False
    )
    exclude = ImageParameter(
        "Exclude",
        "A mask on the image's grid (its first band): where it's set (nonzero) "
        "is left out, with every window touching it, e.g. a Shadow Mask",
        optional=True,
    )
    minimumSignal = FloatParameter(
        "Minimum Signal",
        0.0,
        range=(0.0, 1e12),
        units="data units",
        decimals=5,
        showSlider=False,
        description="Leave out pixels whose mean over the fitted bands is below "
        "this (e.g. I/F), with every window touching them; 0 is off",
    )

    def __init__(self, targets: Sequence[LibrarySpectrum] = ()) -> None:
        super().__init__()
        self.targets = list(targets)
        self.libraryName = ""

    def prepareFor(self, image: VardaRaster) -> None:
        current = self.exclude.get()
        self.exclude.setProvider(
            lambda: compatibleImages(list(self.context.images), image)
        )
        candidates = compatibleImages(list(self.context.images), image)
        self.exclude.setOrClear(current if current in candidates else None)

    def unavailableReason(self, image: VardaRaster) -> str:
        if not self.targets:
            return "Tick the targets to map in the FATT window."
        return ""

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        if not self.targets:
            raise ValueError("No targets to map.")
        reportProgress(0, "reading the image")
        bands, fwhm = bandsInRange(
            image, self.minWavelength.get(), self.maxWavelength.get()
        )
        centers = np.asarray(image.bandModel.centers)[bands]
        targets = np.stack([resampleTo(t, centers, fwhm) for t in self.targets])
        covered = np.isfinite(targets)  # (targets, bands)
        tooFew = [
            target.name
            for target, count in zip(self.targets, covered.sum(axis=1), strict=True)
            if count < MIN_BANDS
        ]
        if tooFew:
            raise ValueError(
                f"{', '.join(tooFew)}: fewer than {MIN_BANDS} of the image's bands "
                "in the wavelength range are covered."
            )
        cube, valid = readCube(image)
        cube[~valid] = np.nan
        # each pixel's mean over the fitted bands, summed band by band (no copy)
        signal = np.zeros(valid.shape)
        for band in bands:
            signal += cube[:, :, band]
        signal /= max(len(bands), 1)
        excluded, counts = self._exclusions(image, signal, valid)
        # no data before windowing: every window touching them is skipped
        cube[excluded] = np.nan
        # Each target is fitted over the bands it covers (as the ranking table
        # does), so its map never depends on which other targets are ticked;
        # targets covering the same bands are fitted together.
        nrmse = np.empty((len(targets),) + valid.shape)
        bandSets = np.unique(covered, axis=0)
        for k, bandSet in enumerate(bandSets):
            members = (covered == bandSet).all(axis=1)
            nrmse[members] = windowedNrmse(
                np.ascontiguousarray(cube[:, :, bands[bandSet]]),
                targets[members][:, bandSet],
                int(self.factors.get()),
                DEFAULT_WINDOW_SHAPES,
                progress=lambda done, k=k: reportProgress(
                    int(5 + 90 * (k + done) / len(bandSets)), "fitting the windows"
                ),
            )
        threshold = float(self.threshold.get()) * THRESHOLD_UNIT
        missing = np.isnan(nrmse).all(axis=0)
        detected = np.where(missing, np.nan, (nrmse < threshold).sum(axis=0))
        names = [t.name for t in self.targets]
        source = ArrayDataSource(
            np.concatenate([detected[None], nrmse, signal[None]])
            .transpose(1, 2, 0)
            .astype(np.float32),
            wavelengths=np.array(["Targets detected", *names, "Mean signal"]),
            wavelengthUnits="",
            bandNames=[
                "Targets detected",
                *(f"nRMSE: {n}" for n in names),
                "Mean signal",
            ],
            transform=image.transform,
            crs=image.crs,
            defaultBands=np.array([0, 0, 0]),
            extraMetadata={
                "fatt": {
                    "targets": names,
                    "library": self.libraryName,
                    "threshold": threshold,
                    "factors": int(self.factors.get()),
                    "windows": [list(shape) for shape in DEFAULT_WINDOW_SHAPES],
                    "bands": [int(n) for n in covered.sum(axis=1)],
                    **counts,
                }
            },
            description=f"FATT detections of {len(names)} targets in {image.name}"
            + self._exclusionText(counts),
        )
        return VardaRaster(source, name=f"{image.name} FATT")

    def _exclusions(
        self, image: VardaRaster, signal: np.ndarray, valid: np.ndarray
    ) -> tuple[np.ndarray, dict]:
        """The pixels to leave out (set in the Exclude mask, or with a mean
        signal below the minimum), and how many of the image's valid pixels
        each rule left out (a pixel can count for both)."""
        excluded = np.zeros(valid.shape, bool)
        counts: dict = {"exclude": None, "excludedByMask": 0}
        mask = self.exclude.get()
        if mask is not None:
            if mask not in compatibleImages(list(self.context.images), image):
                raise ValueError(f"{mask.name} isn't on {image.name}'s grid.")
            values = readWithGaps(mask, [0])[:, :, 0]
            isSet = np.isfinite(values) & (values != 0)  # no data has no say
            excluded |= isSet
            counts["exclude"] = mask.name
            counts["excludedByMask"] = int((isSet & valid).sum())
        minimum = float(self.minimumSignal.get())
        counts["minimumSignal"] = minimum
        dark = valid & (signal < minimum) if minimum > 0 else np.zeros_like(valid)
        excluded |= dark
        counts["excludedBySignal"] = int(dark.sum())
        counts["excludedPixels"] = int((excluded & valid).sum())
        return excluded, counts

    @staticmethod
    def _exclusionText(counts: dict) -> str:
        parts = []
        if counts["exclude"] is not None:
            parts.append(f"{counts['excludedByMask']} px set in {counts['exclude']}")
        if counts["minimumSignal"] > 0:
            parts.append(
                f"{counts['excludedBySignal']} px with a mean signal below "
                f"{counts['minimumSignal']:g}"
            )
        return f"; left out: {', '.join(parts)}" if parts else ""
