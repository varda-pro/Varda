"""Statistics: Shannon entropy of spectra and of local neighborhoods."""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.rasters import bandIndex, readGoodBands, singleBandRaster
from varda.common.entities import VardaRaster
from varda.common.parameter import IntParameter


class SpectralEntropyAnalysis(Analysis):
    analysisId = "spectral_entropy"
    name = "Spectral Entropy"
    memoryFactor = 7
    category = "Statistics"
    description = (
        "Shannon entropy (bits) of each pixel's spectrum treated as a "
        "distribution over bands: flat spectra score high, spectra dominated "
        "by a few bands score low."
    )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        reportProgress(0, "reading image")
        cube, valid, _ = readGoodBands(image)

        reportProgress(40, "computing entropy")
        weights = np.clip(np.nan_to_num(cube), 0.0, None)
        total = weights.sum(axis=2, keepdims=True)
        with np.errstate(divide="ignore", invalid="ignore"):
            p = np.where(total > 0, weights / total, 0.0)
            terms = np.where(p > 0, p * np.log2(p), 0.0)
        entropy = -terms.sum(axis=2)
        entropy[~valid] = np.nan

        return singleBandRaster(
            image,
            entropy,
            name=f"{image.name} spectral entropy",
            bandName="Spectral entropy",
            units="bits",
        )


class SpatialEntropyAnalysis(Analysis):
    analysisId = "spatial_entropy"
    name = "Spatial Entropy"
    memoryFactor = 1.5
    category = "Statistics"
    description = (
        "Shannon entropy (bits) of one band's values in a square window around "
        "each pixel, after quantizing the band into equal-width bins: textured "
        "areas score high, uniform areas low."
    )

    band = IntParameter("Band", 1, range=(1, 10000), description="Band (1-based)")
    window = IntParameter(
        "Window",
        7,
        range=(3, 99),
        description="Window size in pixels (odd sizes only)",
        step=2,
    )
    bins = IntParameter(
        "Bins", 64, range=(2, 256), description="Gray levels the band is quantized into"
    )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        reportProgress(0, "reading band")
        index = bandIndex(image, int(self.band.value))
        data = np.asarray(image.getData(bandIndices=[index]), dtype=np.float64)[:, :, 0]
        valid = np.isfinite(data)
        if image.nodata is not None:
            valid &= data != image.nodata
        window = int(self.window.value)  # odd (step 2)
        bins = int(self.bins.value)

        quantised = _quantise(data, valid, bins)
        # Local entropy from per-level box filters: the box filter of a level's
        # indicator is that level's share of the window (of valid pixels).
        count = ndimage.uniform_filter(
            valid.astype(np.float64), window, mode="constant"
        )
        entropy = np.zeros_like(data)
        levels = np.unique(quantised[valid])
        for i, level in enumerate(levels):
            share = ndimage.uniform_filter(
                (quantised == level).astype(np.float64), window, mode="constant"
            )
            with np.errstate(divide="ignore", invalid="ignore"):
                p = np.where(count > 0, share / count, 0.0)
                entropy -= np.where(p > 0, p * np.log2(p), 0.0)
            reportProgress(
                int(100 * (i + 1) / len(levels)), f"level {i + 1}/{len(levels)}"
            )
        entropy[~valid] = np.nan

        return singleBandRaster(
            image,
            entropy,
            name=f"{image.name} spatial entropy (band {index + 1})",
            bandName="Spatial entropy",
            units="bits",
        )


def _quantise(data: np.ndarray, valid: np.ndarray, bins: int) -> np.ndarray:
    """Equal-width bins over the valid range; invalid pixels get -1."""
    quantised = np.full(data.shape, -1, dtype=np.int64)
    if not valid.any():
        return quantised
    low, high = float(np.min(data[valid])), float(np.max(data[valid]))
    if high > low:
        levels = np.floor((data - low) / (high - low) * bins)
        quantised[valid] = np.clip(levels[valid], 0, bins - 1).astype(np.int64)
    else:
        quantised[valid] = 0
    return quantised
