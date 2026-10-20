"""Filtering: noise removal that keeps the image's bands."""

from __future__ import annotations

import numpy as np

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.iovf import iovfFilter
from varda.analysis.rasters import (
    goodBands,
    readCube,
    sameBandsRaster,
    singleBandRaster,
)
from varda.common.entities import VardaRaster
from varda.common.parameter import BoolParameter, FloatParameter, IntParameter

# Per-round and total replacement counts, in both IOVF results' metadata
IOVF_METADATA_KEY = "iovf"


class IovfAnalysis(Analysis):
    analysisId = "iovf"
    name = "Iterative Outlier Voting Filter (IOVF)"
    memoryFactor = 22
    category = "Filtering/Spectral"
    description = (
        "Removes isolated spikes without smoothing the spectra (Phillips et al. "
        "2024): Grubbs outlier tests vote on every voxel of the noise left after "
        "a smooth reference spectrum, and voxels most windows flag are replaced "
        "by their neighbors, round after round. Slow on large images."
    )

    windowRows = IntParameter(
        "Window Rows", 7, range=(3, 15), description="Odd number of pixels or bands"
    )
    windowColumns = IntParameter(
        "Window Columns", 5, range=(3, 15), description="Odd number of pixels or bands"
    )
    windowBands = IntParameter(
        "Window Bands", 5, range=(3, 15), description="Odd number of pixels or bands"
    )
    stopPercent = FloatParameter(
        "Stop Below",
        1.0,
        range=(0.0, 50.0),
        units="% of round 1",
        description=(
            "Stop once a round replaces fewer voxels than this share of the "
            "first round's; 0 runs until nothing is flagged (slower, with very "
            "little further change)."
        ),
        step=0.5,
    )
    replacedMap = BoolParameter(
        "Replaced Count Image",
        True,
        description=(
            "Also add a one-band image of how many bands were replaced at "
            "each pixel, to map and report what the filter changed"
        ),
    )

    def unavailableReason(self, image: VardaRaster) -> str:
        window = self._window()
        if any(w % 2 == 0 for w in window):
            return "Window sizes must be odd."
        if (
            image.height < window[0]
            or image.width < window[1]
            or image.bandCount < window[2]
        ):
            return "The image is smaller than the filter window."
        return ""

    def _window(self) -> tuple[int, int, int]:
        return (
            int(self.windowRows.value),
            int(self.windowColumns.value),
            int(self.windowBands.value),
        )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        return self._filter(image, reportProgress, withMap=False)[0]

    def runAll(
        self, image: VardaRaster, reportProgress: ProgressCallback
    ) -> list[VardaRaster]:
        return self._filter(image, reportProgress, withMap=self.replacedMap.get())

    def _filter(
        self, image: VardaRaster, reportProgress: ProgressCallback, withMap: bool
    ) -> list[VardaRaster]:
        reportProgress(0, "reading image")
        cube, valid = readCube(image)  # missing bands filled; unusable pixels NaN
        result = iovfFilter(
            cube,
            self._window(),
            stopFraction=float(self.stopPercent.value) / 100.0,
            progress=lambda percent, message: reportProgress(
                5 + int(0.9 * percent), message
            ),
        )
        filtered = result.filtered
        bad = np.setdiff1d(np.arange(image.bandCount), goodBands(image))
        if bad.size:  # filled to filter the cube: give back the input's values
            original = np.array(image.getData(bandIndices=list(bad)), np.float64)
            if image.nodata is not None:
                original[original == image.nodata] = np.nan
            filtered[:, :, bad] = original
        filtered[~valid] = np.nan
        record = result.record()
        perRound = ", ".join(str(n) for n in result.replaced)
        filteredImage = sameBandsRaster(
            image,
            filtered,
            name=f"{image.name} IOVF",
            description=(
                f"IOVF of {image.name}: {record['replacedTotal']} voxels replaced "
                f"in {result.rounds} rounds ({perRound}; stopped: "
                f"{result.stopReason}), at {record['pixelsWithReplacements']} pixels"
            ),
        )
        filteredImage.dataSource.extraMetadata[IOVF_METADATA_KEY] = record
        if not withMap:
            return [filteredImage]
        counts = result.replacedBands.astype(np.float32)
        counts[~valid] = np.nan
        countImage = singleBandRaster(
            image,
            counts,
            name=f"{image.name} IOVF replaced",
            bandName="Bands replaced",
            units="count",
            description=(
                f"Bands IOVF replaced at each pixel of {image.name} "
                f"({record['voxelsReplaced']} voxels at "
                f"{record['pixelsWithReplacements']} pixels)"
            ),
        )
        countImage.dataSource.extraMetadata[IOVF_METADATA_KEY] = record
        return [filteredImage, countImage]
