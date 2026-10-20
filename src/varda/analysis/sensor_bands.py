"""Convolve to sensor bands: a cube with wavelengths as another sensor would
see it, each of that sensor's bands the mean of the cube's bands weighted by
the band's spectral response function.

A use: aligning a hyperspectral product to a camera's images. Convolve the
reflectance to the camera's band (CRISM to CTX, say), match that scene to
the camera's image, and the alignment applies to every product of the same
observation (3D Terrain overlays' Match using).

The built-in sensors are approximations, boxcars from published band edges
or Gaussians from published centers and widths: close enough to make a scene
to match, while a sensor's own response file (``readResponses``) is the one
for numbers that matter. The response is sampled at the cube's band centers,
each band weighted by its share of the wavelength axis, which suits a cube
whose bands are much narrower than the sensor's. No Qt.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import numpy as np
from attrs import frozen

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.common.entities import VardaRaster
from varda.common.parameter import EnumParameter, LabeledEnum, StringParameter
from varda.common.spectral_axis import (
    MAX_MICROMETRES,
    nanometresFrom,
    unitInText,
)
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.utilities.resources import resource_path

MIN_COVERAGE = 0.9  # share of a band's response the cube must cover
_CHUNK = 65_536  # pixels at a time


@frozen
class Band:
    """A spectral response function: relative response (peak 1) against
    wavelength in nm."""

    name: str
    wavelengths: np.ndarray
    response: np.ndarray

    @property
    def area(self) -> float:
        return float(np.trapezoid(self.response, self.wavelengths))

    @property
    def center(self) -> float:
        """The response-weighted mean wavelength."""
        weighted = np.trapezoid(self.response * self.wavelengths, self.wavelengths)
        return float(weighted / self.area)

    @property
    def width(self) -> float:
        """The width of a box with the same area and peak (≈ the FWHM)."""
        return self.area / float(self.response.max())


@frozen
class Sensor:
    name: str
    bands: tuple[Band, ...]
    note: str = ""  # where the responses come from


def boxcar(name: str, low: float, high: float, edge: float = 10.0) -> Band:
    """A flat band from ``low`` to ``high`` nm (half response there), its
    edges ramping over ``edge`` nm."""
    half = edge / 2
    return Band(
        name,
        np.array([low - half, low + half, high - half, high + half]),
        np.array([0.0, 1.0, 1.0, 0.0]),
    )


def gaussian(name: str, center: float, fwhm: float) -> Band:
    sigma = fwhm / (2 * np.sqrt(2 * np.log(2)))
    wavelengths = np.linspace(center - 3 * fwhm, center + 3 * fwhm, 121)
    return Band(name, wavelengths, np.exp(-0.5 * ((wavelengths - center) / sigma) ** 2))


def parseBands(text: str) -> Sensor:
    """Bands typed as center/FWHM, with optional names: ``Red 650/40;
    NIR: 860/30`` (nm, or µm for small values). Gaussian responses."""
    bands = []
    for i, part in enumerate(p for p in re.split(r"[;\n]", text) if p.strip()):
        match = re.fullmatch(
            r"\s*(?:(?P<name>.*?\D)[\s:]+)?(?P<center>[\d.]+)\s*/\s*(?P<fwhm>[\d.]+)\s*",
            part,
        )
        if match is None:
            raise ValueError(
                f"Couldn't read {part.strip()!r}: write a band as center/FWHM, "
                "e.g. Red 650/40."
            )
        name = (match["name"] or "").strip().rstrip(":") or f"Band {i + 1}"
        center, fwhm = float(match["center"]), float(match["fwhm"])
        if center < MAX_MICROMETRES:  # µm, as Varda reads small wavelengths
            center, fwhm = center * 1000, fwhm * 1000
        bands.append(gaussian(name, center, fwhm))
    if not bands:
        raise ValueError("No bands given.")
    return Sensor("Custom", tuple(bands), "Gaussian responses from typed bands")


def readResponses(path: str | Path) -> Sensor:
    """Response functions from a table: a header row, then wavelength (unit
    from its header, e.g. ``wavelength_um``, or from the values) and one
    column of response per band. Each band is normalized to peak 1."""
    path = Path(path)
    lines = path.read_text().splitlines()
    # "#" lines are notes (the first says where the responses come from)
    notes = [line.lstrip("#").strip() for line in lines if line.startswith("#")]
    lines = [line for line in lines if line.strip() and not line.startswith("#")]
    try:
        dialect = csv.Sniffer().sniff(lines[0], delimiters=",;\t ")
    except csv.Error:
        dialect = csv.excel
    rows = [row for row in csv.reader(lines, dialect) if any(row)]
    header, body = rows[0], np.array(rows[1:], dtype=float)
    wavelengths = nanometresFrom(body[:, 0], unitInText(header[0]))
    order = np.argsort(wavelengths)
    bands = []
    for column, name in enumerate(header[1:], start=1):
        response = np.clip(body[order, column], 0, None)
        if response.max() > 0:
            bands.append(
                Band(name.strip(), wavelengths[order], response / response.max())
            )
    if not bands:
        raise ValueError(f"{path.name} has no response columns.")
    note = notes[0] if notes else f"responses from {path.name}"
    return Sensor(path.stem, tuple(bands), note)


def fromEdges(name: str, points: list[tuple[float, float]]) -> Band:
    """A band through (wavelength nm, response) points, sampled every nm and
    interpolated in log response, as a measured passband falls off."""
    wavelengths, responses = np.array(points, dtype=np.float64).T
    grid = np.arange(wavelengths.min(), wavelengths.max() + 1.0, 1.0)
    floor = 1e-4
    logged = np.interp(grid, wavelengths, np.log(np.maximum(responses, floor)))
    return Band(name, grid, np.where(np.exp(logged) <= floor, 0.0, np.exp(logged)))


def bundled(file: str, name: str) -> Sensor:
    """A sensor whose measured responses ship with Varda."""
    sensor = readResponses(resource_path(f"resources/sensor_bands/{file}"))
    return Sensor(name, sensor.bands, f"measured responses: {sensor.note}")


APPROXIMATE = (
    "approximate responses from published {}; load the instrument's response "
    "file for science-grade values"
)
SENSORS: dict[str, Sensor] = {
    sensor.name: sensor
    for sensor in (
        Sensor(
            "MRO CTX",
            # the measured system throughput (filter, optics and CCD): 50%
            # at 515.5 and 704.7 nm, 1% at 498.2 and 754.7 nm, flat between
            (
                fromEdges(
                    "CTX",
                    [
                        (488.0, 1e-4),
                        (498.2, 0.01),
                        (515.5, 0.5),
                        (532.0, 1.0),
                        (688.0, 1.0),
                        (704.7, 0.5),
                        (754.7, 0.01),
                        (800.0, 1e-3),
                        (812.0, 1e-4),
                    ],
                ),
            ),
            "the measured system passband of Bell et al. (2013, Mars 8, 1-14), "
            "Fig. 6: its 1% and 50% edges, effective wavelength 611 nm, FWHM "
            "189 nm (the curve itself isn't published as a table)",
        ),
        Sensor(
            "MRO HiRISE",
            (
                boxcar("BG", 400, 580, 20),
                boxcar("RED", 570, 830, 20),
                boxcar("NIR", 790, 1000, 20),
            ),
            APPROXIMATE.format(
                "half-maximum band edges (McEwen et al., 2007); the measured "
                "curves are published only as figures"
            ),
        ),
        Sensor(
            "Mars Odyssey THEMIS VIS",
            tuple(
                gaussian(f"Band {i + 1} ({c} nm)", c, 50)
                for i, c in enumerate((425, 540, 654, 749, 860))
            ),
            APPROXIMATE.format("band centers, 50 nm wide (Christensen et al., 2004)"),
        ),
        bundled("landsat8_oli.csv", "Landsat 8 OLI"),
        bundled("landsat9_oli2.csv", "Landsat 9 OLI-2"),
        bundled("sentinel2a_msi.csv", "Sentinel-2A MSI"),
        bundled("sentinel2b_msi.csv", "Sentinel-2B MSI"),
        bundled("sentinel2c_msi.csv", "Sentinel-2C MSI"),
    )
}


def bandWeights(
    wavelengths: np.ndarray, sensor: Sensor, badBands: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """(cube bands, sensor bands) weights, and each sensor band's coverage:
    the share of its response the cube's good bands cover."""
    wavelengths = np.asarray(wavelengths, float)
    order = np.argsort(wavelengths)
    sorted_ = wavelengths[order]
    edges = np.concatenate(
        [
            [sorted_[0] - (sorted_[1] - sorted_[0]) / 2],
            (sorted_[1:] + sorted_[:-1]) / 2,
            [sorted_[-1] + (sorted_[-1] - sorted_[-2]) / 2],
        ]
    )
    widths = np.empty_like(wavelengths)
    widths[order] = np.diff(edges)
    good = np.ones(len(wavelengths), bool) if badBands is None else ~badBands
    weights = np.zeros((len(wavelengths), len(sensor.bands)))
    coverage = np.zeros(len(sensor.bands))
    for k, band in enumerate(sensor.bands):
        response = np.interp(
            wavelengths, band.wavelengths, band.response, left=0, right=0
        )
        weights[:, k] = np.where(good, response * widths, 0)
        coverage[k] = min(weights[:, k].sum() / band.area, 1.0)
    return weights, coverage


def convolve(
    cube: np.ndarray,
    wavelengths: np.ndarray,
    sensor: Sensor,
    badBands: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """The cube (rows, cols, bands; NaN for no data) in the sensor's bands,
    and each band's coverage. Bands covered less than ``MIN_COVERAGE`` are
    NaN, as are pixels missing that much of a band's response."""
    rows, cols, count = cube.shape
    weights, coverage = bandWeights(wavelengths, sensor, badBands)
    weights[:, coverage < MIN_COVERAGE] = 0
    totals = weights.sum(axis=0)
    flat = cube.reshape(-1, count)
    out = np.full((flat.shape[0], len(sensor.bands)), np.nan)
    for start in range(0, flat.shape[0], _CHUNK):
        block = flat[start : start + _CHUNK]
        finite = np.isfinite(block)
        weighted = np.where(finite, block, 0) @ weights
        present = finite.astype(float) @ weights
        enough = (present >= MIN_COVERAGE * totals) & (totals > 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[start : start + _CHUNK] = np.where(enough, weighted / present, np.nan)
    return out.reshape(rows, cols, len(sensor.bands)), coverage


class SensorChoice(LabeledEnum):
    CTX = "MRO CTX"
    HIRISE = "MRO HiRISE"
    THEMIS_VIS = "Mars Odyssey THEMIS VIS"
    LANDSAT8_OLI = "Landsat 8 OLI"
    LANDSAT9_OLI2 = "Landsat 9 OLI-2"
    SENTINEL2A_MSI = "Sentinel-2A MSI"
    SENTINEL2B_MSI = "Sentinel-2B MSI"
    SENTINEL2C_MSI = "Sentinel-2C MSI"
    CUSTOM = "Custom bands"


class SensorBandsAnalysis(Analysis):
    analysisId = "sensor_bands"
    name = "Convolve to Sensor Bands"
    category = "Spectral Parameters"
    description = (
        "The image as another sensor would see it: each of that sensor's bands "
        "is the mean of the image's bands weighted by its spectral response "
        "(e.g. CRISM reflectance as a CTX image, to align to CTX). Built-in "
        "sensors are approximations from published band edges or widths; for "
        "precise values, choose Custom bands and give the sensor's response "
        "file."
    )

    sensor = EnumParameter("Sensor", SensorChoice)
    custom = StringParameter(
        "Custom Bands",
        "",
        "For Custom bands: a response file (CSV: wavelength, then one column "
        "per band) or bands as center/FWHM in nm, e.g. Red 650/40; NIR 860/30",
    )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        model = image.bandModel
        if model.centers is None or len(model.centers) < 2:
            raise ValueError(f"{image.name} has no wavelengths to convolve.")
        sensor = self._sensor()
        reportProgress(0, "reading image")
        cube = np.array(image.getData(), dtype=np.float64)
        if model.noData is not None:
            cube[cube == model.noData] = np.nan
        reportProgress(40, f"convolving to {sensor.name}")
        values, coverage = convolve(cube, model.centers, sensor, model.badBands)
        kept = np.flatnonzero(coverage >= MIN_COVERAGE)
        if not kept.size:
            raise ValueError(
                f"{image.name} ({model.centers.min():.0f}-{model.centers.max():.0f} "
                f"nm) doesn't cover any of {sensor.name}'s bands."
            )
        bands = [sensor.bands[k] for k in kept]
        skipped = [b.name for b, c in zip(sensor.bands, coverage) if c < MIN_COVERAGE]
        covered = ", ".join(
            f"{b.name} {100 * coverage[k]:.0f}%" for b, k in zip(bands, kept)
        )
        notes = [sensor.note, f"coverage {covered}"] + (
            [f"not covered: {', '.join(skipped)}"] if skipped else []
        )
        source = ArrayDataSource(
            values[:, :, kept].astype(np.float32),
            wavelengths=np.array([b.center for b in bands]),
            wavelengthUnits="nm",
            bandNames=[b.name for b in bands],
            transform=image.transform,
            crs=image.crs,
            extraMetadata={
                "fwhm": [round(b.width, 2) for b in bands],
                # share of each band's response the cube's good bands cover
                "sensorBands": {
                    "sensor": sensor.name,
                    "coverage": {
                        b.name: float(c) for b, c in zip(sensor.bands, coverage)
                    },
                    "minCoverage": MIN_COVERAGE,
                    "skipped": skipped,
                },
            },
            description=f"{image.name} in {sensor.name} bands ({'; '.join(notes)})",
        )
        return VardaRaster(source, name=f"{image.name} as {sensor.name}")

    def _sensor(self) -> Sensor:
        choice = self.sensor.value
        if choice is not SensorChoice.CUSTOM:
            return SENSORS[str(choice.value)]
        text = str(self.custom.value).strip()
        if not text:
            raise ValueError("Give the Custom Bands: a response file or center/FWHM.")
        path = Path(text).expanduser()
        return readResponses(path) if path.is_file() else parseBands(text)
