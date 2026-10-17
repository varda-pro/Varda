"""Continuum removal: each spectrum divided by its upper convex hull, so
absorption features stand out as dips below 1 (the image counterpart of the
continuum removal used when matching spectral libraries)."""

from __future__ import annotations

import sys

import numpy as np
from numba import njit, prange

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.rasters import bandMetadataSubset, readCube
from varda.common.entities import VardaRaster
from varda.common.parameter import FloatParameter
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

_CACHE = not getattr(sys, "frozen", False)  # see stretch_algorithms._CACHE


@njit(parallel=True, cache=_CACHE)
def _removeContinuum(flat: np.ndarray, x: np.ndarray) -> np.ndarray:
    pixels, bands = flat.shape
    out = np.full((pixels, bands), np.nan)
    for p in prange(pixels):  # ty: ignore[not-iterable]
        y = flat[p]
        if np.isnan(y).any():
            continue
        hull = np.empty(bands, dtype=np.int64)
        count = 0
        for i in range(bands):  # upper hull, monotone chain
            while count >= 2:
                a, b = hull[count - 2], hull[count - 1]
                cross = (x[b] - x[a]) * (y[i] - y[a]) - (y[b] - y[a]) * (x[i] - x[a])
                if cross >= 0:
                    count -= 1
                else:
                    break
            hull[count] = i
            count += 1
        segment = 0
        for i in range(bands):
            while segment < count - 2 and x[hull[segment + 1]] < x[i]:
                segment += 1
            a, b = hull[segment], hull[min(segment + 1, count - 1)]
            if b == a:
                continuum = y[a]
            else:
                t = (x[i] - x[a]) / (x[b] - x[a])
                continuum = y[a] + t * (y[b] - y[a])
            out[p, i] = y[i] / continuum if continuum != 0 else np.nan
    return out


def continuumRemovedCube(cube: np.ndarray, wavelengthsNm: np.ndarray) -> np.ndarray:
    """(rows, cols, bands) continuum-removed spectra; pixels with any NaN stay NaN."""
    x = np.asarray(wavelengthsNm, dtype=np.float64)
    order = np.argsort(x)  # wavenumber axes run backwards in nm
    flat = np.ascontiguousarray(
        cube.reshape(-1, cube.shape[2])[:, order], dtype=np.float64
    )
    removed = _removeContinuum(flat, x[order])
    out = np.empty_like(removed)
    out[:, order] = removed
    return out.reshape(cube.shape)


class ContinuumRemovalAnalysis(Analysis):
    analysisId = "continuum_removal"
    name = "Continuum Removal"
    memoryFactor = 6
    memoryFixed = 20 * 2**20
    category = "Spectral Parameters"
    description = (
        "Divides each spectrum by its upper convex hull within a wavelength "
        "window, so absorption bands show as dips below 1 (e.g. 2000-2400 nm "
        "for Al-OH and Mg-OH features)."
    )

    startWavelength = FloatParameter(
        "Start Wavelength", 1000.0, units="nm", description="Start of the window"
    )
    endWavelength = FloatParameter(
        "End Wavelength", 2500.0, units="nm", description="End of the window"
    )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        model = image.bandModel
        if model.centers is None:
            raise ValueError(
                f"{image.name} has no numeric wavelengths with units to remove a "
                "continuum over."
            )
        low = min(float(self.startWavelength.value), float(self.endWavelength.value))
        high = max(float(self.startWavelength.value), float(self.endWavelength.value))
        keep = np.flatnonzero(
            (model.centers >= low) & (model.centers <= high) & ~model.badBands
        )
        if keep.size < 3:
            raise ValueError(
                f"Fewer than three bands fall between {low:g} and {high:g} nm."
            )

        reportProgress(0, "reading image")
        cube, valid = readCube(image)
        reportProgress(30, "removing the continuum")
        removed = continuumRemovedCube(cube[:, :, keep], model.centers[keep])
        removed[~valid] = np.nan

        names = [image.bandNames[i] for i in keep]
        source = ArrayDataSource(
            removed.astype(np.float32),
            wavelengths=np.asarray(image.wavelengths)[keep],
            wavelengthUnits=image.wavelengthUnits,
            bandNames=names,
            transform=image.transform,
            crs=image.crs,
            extraMetadata=bandMetadataSubset(image, keep),
            description=f"Continuum removed {low:g}-{high:g} nm of {image.name}",
        )
        return VardaRaster(source, name=f"{image.name} continuum removed")
