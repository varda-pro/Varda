"""Derivatives: spectral derivative, spectral slope, spatial gradient."""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.analysis.rasters import (
    bandIndex,
    bandMetadataSubset,
    namedBandsRaster,
    readCube,
    readGoodBands,
    singleBandRaster,
)
from varda.common.entities import VardaRaster
from varda.common.parameter import BoolParameter, FloatParameter, IntParameter
from varda.image_loading.data_sources.array_data_source import ArrayDataSource


class SpectralDerivativeAnalysis(Analysis):
    analysisId = "spectral_derivative"
    name = "Spectral Derivative"
    memoryFactor = 5
    category = "Derivatives"
    description = (
        "Derivative of each spectrum with respect to wavelength, band by band "
        "(or the second derivative): highlights absorption edges and band "
        "centers. Without numeric wavelengths, band index is the x axis. "
        "Bad bands are left out: the result has the image's good bands."
    )

    order = IntParameter("Order", 1, range=(1, 2), description="1 = dR/dλ, 2 = d²R/dλ²")

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        reportProgress(0, "reading image")
        # unusable pixels are NaN throughout; bad bands left out
        cube, _valid, bands = readGoodBands(image)
        centers = image.bandModel.centers  # nm, whatever the image's own unit
        x = centers[bands] if centers is not None else bands.astype(float)

        reportProgress(40, "differentiating")
        derivative = cube
        for _ in range(int(self.order.value)):
            derivative = np.gradient(derivative, x, axis=2)

        suffix = "d/dλ" if int(self.order.value) == 1 else "d²/dλ²"
        per = "nm" if centers is not None else "band"
        if int(self.order.value) == 2:
            per += "²"
        # The source's good bands: their spectral axis (and FWHM) carries over
        source = ArrayDataSource(
            derivative.astype(np.float32),
            wavelengths=np.asarray(image.wavelengths)[bands],
            wavelengthUnits=image.wavelengthUnits,
            bandNames=[image.bandNames[b] for b in bands],
            transform=image.transform,
            crs=image.crs,
            extraMetadata=bandMetadataSubset(image, bands),
            description=f"{suffix} of {image.name} (per {per})",
        )
        return VardaRaster(source, name=f"{image.name} {suffix}")


class SpectralSlopeAnalysis(Analysis):
    analysisId = "spectral_slope"
    name = "Spectral Slope"
    memoryFactor = 3.5
    category = "Derivatives"
    description = (
        "Least-squares slope of each spectrum between two wavelengths "
        "(reflectance per nm), 1000-2000 nm by default; set the range to the "
        "slope you need. With Fit Statistics, two more bands give each "
        "slope's standard error and the fit's R²."
    )

    startWavelength = FloatParameter(
        "Start Wavelength", 1000.0, units="nm", description="Start of the fitted range"
    )
    endWavelength = FloatParameter(
        "End Wavelength", 2000.0, units="nm", description="End of the fitted range"
    )
    fitStatistics = BoolParameter(
        "Fit Statistics",
        True,
        description=(
            "Also output the slope's standard error (per nm, from the fit's "
            "residuals) and R² of the fit; off: the slope band alone"
        ),
    )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        model = image.bandModel
        wavelengths = model.centers  # nm
        if wavelengths is None:
            raise ValueError(
                f"{image.name} has no numeric wavelengths with units to fit a slope over."
            )
        low = min(float(self.startWavelength.value), float(self.endWavelength.value))
        high = max(float(self.startWavelength.value), float(self.endWavelength.value))
        selected = np.where(
            (wavelengths >= low) & (wavelengths <= high) & ~model.badBands
        )[0]
        if selected.size < 2:
            raise ValueError(
                f"Fewer than two bands fall in the wavelength range {low:g}-{high:g}."
            )

        reportProgress(0, "reading bands")
        cube, valid = readCube(image)
        cube = cube[:, :, selected]

        reportProgress(50, "fitting slopes")
        slope, error, rSquared = _lineFit(wavelengths[selected], cube)
        for band in (slope, error, rSquared):
            band[~valid] = np.nan

        name = f"{image.name} slope {low:g}-{high:g}"
        if not self.fitStatistics.get():
            return singleBandRaster(
                image,
                slope,
                name=name,
                bandName=f"Slope {low:g}-{high:g} nm",
                units="per nm",
            )
        return namedBandsRaster(
            image,
            np.stack([slope, error, rSquared], axis=2),
            [
                f"Slope {low:g}-{high:g} nm (per nm)",
                "Slope standard error (per nm)",
                "R² (unitless)",
            ],
            name=name,
            description=(
                f"Least-squares slope of {image.name} over {selected.size} bands "
                f"in {low:g}-{high:g} nm, its standard error and the fit's R²"
            ),
        )


def _lineFit(
    x: np.ndarray, cube: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per pixel, the least-squares slope of ``cube`` (rows, cols, n) against
    ``x``, its standard error sqrt(SSE / (n - 2) / Sxx) (NaN for n = 2: no
    degrees of freedom left) and R² = 1 - SSE / SST (NaN for a flat
    spectrum)."""
    xCentred = x - x.mean()
    sxx = np.sum(xCentred**2)
    yCentred = cube - cube.mean(axis=2, keepdims=True)
    slope = np.tensordot(yCentred, xCentred, axes=([2], [0])) / sxx
    residual = yCentred - slope[:, :, None] * xCentred
    sse = np.sum(residual**2, axis=2)
    sst = np.sum(yCentred**2, axis=2)
    dof = x.size - 2
    with np.errstate(invalid="ignore", divide="ignore"):
        error = np.sqrt(sse / dof / sxx) if dof > 0 else np.full_like(slope, np.nan)
        rSquared = np.where(sst > 0, 1.0 - sse / sst, np.nan)
    return slope, error, rSquared


class SpatialGradientAnalysis(Analysis):
    analysisId = "spatial_gradient"
    name = "Spatial Gradient"
    memoryFactor = 1.5
    category = "Derivatives"
    description = (
        "Magnitude of the spatial gradient (Sobel) of one band, in band units "
        "per pixel: bright where the band changes quickly, e.g. at unit "
        "boundaries."
    )

    band = IntParameter("Band", 1, range=(1, 10000), description="Band (1-based)")

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        reportProgress(0, "reading band")
        index = bandIndex(image, int(self.band.value))
        data = np.asarray(image.getData(bandIndices=[index]), dtype=np.float64)[:, :, 0]
        if image.nodata is not None:
            data[data == image.nodata] = np.nan

        reportProgress(50, "computing gradient")
        # Sobel kernels sum to 8 across a unit ramp, so /8 gives the per-pixel slope
        gx = ndimage.sobel(data, axis=1, mode="nearest") / 8.0
        gy = ndimage.sobel(data, axis=0, mode="nearest") / 8.0
        magnitude = np.hypot(gx, gy)

        return singleBandRaster(
            image,
            magnitude,
            name=f"{image.name} gradient (band {index + 1})",
            bandName="Gradient magnitude",
            units="per pixel",
        )
