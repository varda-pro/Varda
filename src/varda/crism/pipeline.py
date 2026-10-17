"""The CRISM pipeline (spec "Data flow"): one observation's TRR3 IR I/F and
DDR to MTRDR-consistent products with σ, backplanes and provenance.

Four passes over the lines:

- **A, statistics** on the central ``SELECTION_LINES`` usable lines (all of
  them in a shorter scene). The noise model is estimated on the *raw* I/F of
  those lines: shot noise follows the signal the detector saw, so σ is the
  model's σ of the raw sample, divided by cos i with the data in the
  photometry (S2). With the atmosphere on, the lines are then corrected
  photometrically and despiked (spikes bias the column means), their column
  spectra choose the transmission (S4.A, without the artifact basis), the
  artifact basis is looked up for the chosen key and scaled, and a free
  per-pixel fit gives the elevation model (S4.C).
- **B, per chunk** of ``CHUNK_LINES`` lines, read with
  ``despike.ALONG_TRACK_HALO`` lines on each side: the sample mask (invalid
  samples, columns without a wavelength, bad pixels, slew lines), σ,
  photometry, despike and the atmospheric fit and correction, into a float32
  work memmap of I/F and σ under ``.work/``.
- **C**: the destripe frame (estimated from the work memmap, off by default),
  resampling to the MTRDR vector, and the ESC frame (estimated from the
  resampled memmap's bland pixels), into a second memmap; ``bbl`` (S7); the
  sensor-space product when asked for, or when the projection is off.
- **D**: the projection (the nearest-neighbor GLT, or area weights) of the
  I/F and σ, the 14 DDR backplanes (DE, always through the GLT, also when the
  I/F is area weighted: averaging longitudes or azimuths across their wrap is
  meaningless), and the IN backplanes.

Products are written into ``.work/out`` and moved into the output folder
only when everything succeeded, so a failure or a cancel leaves the folder as
it was, a previous run's products included. ``.work/`` is removed in every
case (two runs must not share an output folder at the same time).

``fitPixels`` is called one line at a time. It weighs a column's complete
lines by their mean σ, which would make β depend on how the lines are
chunked; per line the weights are the pixel's own and nothing depends on
``CHUNK_LINES`` (``test_chunking_invariance``).

The systematic uncertainty is not in σ (N2). The provenance reports it per
output band, relative to I/F, as the median over columns of: the scan choice
(the column spectra corrected with the chosen transmission against the
runner-up), the artifact amplitude (the scale's σ times β times the basis)
and the destripe frame's σ. The spec's Monte Carlo over δ, the scan and the
amplitude on 1,000 pixels is not done here.
"""

import math
import os
import shutil
import time
import warnings
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

import attrs
import numpy as np
from attrs import frozen

from varda.common.provenance import PROVENANCE_KEY
from varda.crism.atmosphere import (
    CONTINUUM_DEGREE,
    AtmosphereModel,
    ElevationModel,
    PixelFit,
    SelectionResult,
    artifactScale,
    bundledArtifactBasis,
    columnSpectra,
    correct,
    fitElevationModel,
    fitPixels,
    selectTransmission,
    shiftedLogTransmission,
)
from varda.crism.brightness import segmentIds
from varda.crism.calibration import (
    CalibrationFileStatus,
    CalibrationLibrary,
    Transmission,
)
from varda.crism.despike import ALONG_TRACK_HALO, SpikeReport, despike
from varda.crism.destripe import StripeFrame, applyStripes, blandMask, estimateStripes
from varda.crism.noise import NoiseModel, estimateNoise
from varda.crism.observation import CrismObservation
from varda.crism.photometry import IncidenceFit, fitIncidence, lambert
from varda.crism.projection import (
    AreaWeights,
    Glt,
    MapGrid,
    applyAreaWeights,
    applyGlt,
    areaWeights,
    mtrdrGrid,
    nearestNeighborGlt,
)
from varda.crism.readers import Ddr, DdrBand, usableLines
from varda.crism.recipe import (
    BetaMode,
    IncidenceSource,
    Recipe,
    Resampling,
    SmileMethod,
    recipeToDict,
)
from varda.crism.smile import (
    EscFrame,
    ResampleWeights,
    StandardVector,
    applyEsc,
    escOffsets,
    estimateEsc,
    linearWeights,
    resample,
    sourceSegments,
    standardVector,
)
from varda.crism.writers import (
    EnviBands,
    EnviCubeWriter,
    historyString,
    sha256File,
    writeCog,
    writeSidecars,
)
from varda.version import vardaVersion

CHUNK_LINES = 64
SELECTION_LINES = 64
WORK_FOLDER = ".work"
PIXEL_SIZE_M = 18.0  # per unit of PIXEL_AVERAGING_WIDTH (36 m for HRL/HRS)
VALID_BAND_FRACTION = 0.5  # S7: a band valid in fewer of the scene's pixels is bad
SIGMA_KIND = "instrument-noise"
IN_BAND_NAMES = (
    "Source sample",
    "Source line",
    "Beta",
    "Beta sigma (empirical)",
    "ATM residual RMS",
    "Artifact amplitude",
    "Spike count",
    "Leask count",
    "Effective looks",
)

type ProgressCallback = Callable[[float, str], None]

_ESC_BIN_NM = {"HRL": 0.25, "HRS": 0.25}  # binned classes; 0.125 nm otherwise
_DEFAULT_ESC_BIN_NM = 0.125
_DEFAULT_BANDS_NM = (2529.0, 1506.0, 1080.0)  # the IR false-color browse
_COLUMN_BLOCK = 32
_MAP_ROWS = 64
_BAND_BLOCK = 32
_PLANES = ("beta", "betaSigma", "residualRms", "artifactAmplitude")
_SIDECARS = (".recipe.json", ".provenance.json", ".diagnostics.npz")
# the share of the run each stage reports, in order
_STAGES = {
    "calibration": (0.0, 0.03),
    "passA": (0.03, 0.15),
    "passB": (0.15, 0.55),
    "passC": (0.55, 0.8),
    "passD": (0.8, 0.97),
    "sidecars": (0.97, 1.0),
}


class ProcessCancelled(Exception):
    """The caller's ``cancelled()`` returned True; nothing was written."""


@frozen
class ProcessResult:
    """The products of one run (the ENVI ``.img`` of each; its ``.hdr`` and,
    for I/F, its ``_sigma`` companion sit beside it). Products the recipe
    does not ask for are None."""

    mapProjected: Path | None
    mapSigma: Path | None
    geometry: Path | None  # DE
    info: Path | None  # IN
    sensorSpace: Path | None
    recipe: Path
    provenance: Path
    diagnostics: Path


class _Progress:
    """Monotonic progress within stages, and the cancel check."""

    def __init__(
        self, callback: ProgressCallback | None, cancelled: Callable[[], bool] | None
    ):
        self._callback = callback
        self._cancelled = cancelled
        self._last = 0.0

    def report(self, stage: str, within: float, message: str) -> None:
        low, high = _STAGES[stage]
        fraction = min(max(low + (high - low) * within, self._last), 1.0)
        self._last = fraction
        if self._callback is not None:
            self._callback(fraction, message)

    def check(self) -> None:
        if self._cancelled is not None and self._cancelled():
            raise ProcessCancelled("CRISM processing was canceled")


def _checkRecipe(recipe: Recipe) -> None:
    """Options the recipe offers that Phase 1 does not implement."""
    if recipe.smile.method.get() is SmileMethod.CUBIC:
        raise ValueError(
            "Cubic spline resampling is not available yet; use linear resampling."
        )
    if recipe.atmosphere.continuumDegree.get() != CONTINUUM_DEGREE:
        raise ValueError(
            f"The atmospheric fit's continuum degree is fixed at {CONTINUUM_DEGREE} "
            f"for now, not {recipe.atmosphere.continuumDegree.get()}."
        )


def _nanMedian(values: np.ndarray, axis: int) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices
        return np.nanmedian(values, axis=axis)


def _chunks(lines: int, size: int) -> Iterator[tuple[int, int]]:
    for start in range(0, lines, size):
        yield start, min(start + size, lines)


def _mergeFits(fits: list[PixelFit]) -> PixelFit:
    """Per-line fits stacked into one; their fitted bands depend only on the
    wavelengths and the model, so they agree."""
    if any(not np.array_equal(f.fitBands, fits[0].fitBands) for f in fits):
        raise RuntimeError("per-line atmospheric fits used different bands")

    def stacked(name: str) -> np.ndarray:
        return np.concatenate([getattr(f, name) for f in fits])

    return PixelFit(
        beta=stacked("beta"),
        betaSigma=stacked("betaSigma"),
        betaSigmaEmpirical=stacked("betaSigmaEmpirical"),
        artifactAmplitude=stacked("artifactAmplitude"),
        residualRms=stacked("residualRms"),
        valid=stacked("valid"),
        fitBands=fits[0].fitBands,
        gain=stacked("gain"),
        covariance=stacked("covariance"),
    )


def _bandNames(ddr: Ddr) -> tuple[str, ...]:
    """The DDR's band names, with MTRDR's ';' for ',' (ENVI lists split on ',')."""
    names = ddr.label.bandNames or tuple(band.name for band in DdrBand)
    return tuple(name.replace(",", ";") for name in names)


def _defaultBands(vector: StandardVector) -> tuple[int, int, int]:
    first, second, third = (
        int(np.argmin(np.abs(vector.wavelengths - nm))) + 1 for nm in _DEFAULT_BANDS_NM
    )
    return first, second, third


@frozen(eq=False)
class _Atmosphere:
    """What Pass A decided for the atmospheric correction."""

    selection: SelectionResult
    candidates: tuple[Transmission, ...]
    model: AtmosphereModel
    artifact: dict[str, object]
    priorUsed: bool


class _Run:
    """One run of ``process``: the observation's state between the passes."""

    def __init__(
        self,
        observation: CrismObservation,
        recipe: Recipe,
        work: Path,
        library: CalibrationLibrary,
        progress: _Progress,
    ):
        self.recipe = recipe
        self.work = work
        self.staging = work / "out"
        self.library = library
        self.progress = progress
        self.trr3, self.ddr = observation.readIr()
        self.observation = observation
        self.lines, self.samples, self.bands = self.trr3.shape
        self.vector = standardVector()
        self.created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.wallTimes: dict[str, float] = {}
        self.steps: dict[str, dict[str, object]] = {}
        self.diagnostics: dict[str, np.ndarray] = {}
        self.maskCounts = {
            "invalidSamples": 0,
            "noWavelength": 0,
            "badPixel": 0,
            "slewLine": 0,
        }
        self.atmosphere: _Atmosphere | None = None
        self.stripes: StripeFrame | None = None
        self.esc: EscFrame | None = None
        self.escBinNm: float | None = None
        self.outputs: dict[str, Path] = {}
        product = self.trr3.id
        prefix = f"{product.observationName.lower()}_{product.counter:02d}"
        suffix = f"{product.macro:03d}{product.sensor.lower()}"
        self.names = {
            "map": f"{prefix}_{product.activity.lower()}{suffix}_vmtr",
            "geometry": f"{prefix}_de{suffix}_vmtr",
            "info": f"{prefix}_in{suffix}_vmtr",
            "sensor": f"{prefix}_{product.activity.lower()}{suffix}_vter",
        }

    # --- running -------------------------------------------------------------

    def run(self) -> ProcessResult:
        self._timed("calibration", self._calibration)
        self._timed("passA", self._passA)
        self.progress.check()
        self._timed("passB", self._passB)
        self._timed("passC", self._passC)
        projected = self.recipe.projection.enabled.get() and self._writing()
        if projected:
            self._timed("passD", self._passD)
        else:
            self.steps["projection"] = {"enabled": False}
        return self._timed("sidecars", self._finish)

    def _timed[T](self, stage: str, step: Callable[[], T]) -> T:
        start = time.perf_counter()
        result = step()
        self.wallTimes[stage] = time.perf_counter() - start
        return result

    def _writing(self) -> bool:
        return bool(self.recipe.output.enabled.get())

    # --- calibration and masks -----------------------------------------------

    def _calibration(self) -> None:
        self.progress.report("calibration", 0.0, "Checking the calibration files")
        self.status: list[CalibrationFileStatus] = self.library.ensure(self.trr3)
        self.wavelengths = self.library.wavelengths(self.trr3)  # (s, b)
        self.bandWavelengths = _nanMedian(self.wavelengths, axis=0)
        self.badPixels = self.library.badPixels(self.trr3)
        self.usable = usableLines(self.ddr)
        self.incidenceFit: IncidenceFit | None = None
        if self.recipe.photometry.incidence.get() is IncidenceSource.AREOID_POLYNOMIAL:
            incidence = self.ddr.band(DdrBand.INCIDENCE_AREOID)
            incidence[~self.usable] = np.nan
            self.incidenceFit = fitIncidence(incidence)
        self.progress.report("calibration", 1.0, "Calibration files found")

    def _read(self, lo: int, hi: int, tally: tuple[int, int] | None) -> np.ndarray:
        """TRR3 lines [lo, hi) with every masked sample NaN; ``tally`` (start,
        stop) counts each mask reason over those lines."""
        cube = self.trr3.lines(slice(lo, hi))
        invalid = np.isnan(cube)
        inner = slice(tally[0] - lo, tally[1] - lo) if tally is not None else None
        if inner is not None:
            self.maskCounts["invalidSamples"] += int(invalid[inner].sum())
        shape = cube.shape
        masks = [
            ("noWavelength", np.broadcast_to(~np.isfinite(self.wavelengths), shape))
        ]
        if self.badPixels is not None:
            masks.append(("badPixel", np.broadcast_to(self.badPixels, shape)))
        slew = ~self.usable[lo:hi]
        masks.append(("slewLine", np.broadcast_to(slew[:, None, None], shape)))
        for reason, mask in masks:
            if inner is not None:
                self.maskCounts[reason] += int((mask & ~invalid)[inner].sum())
            invalid |= mask
        cube[invalid] = np.nan
        return cube

    def _incidence(self, lo: int, hi: int) -> np.ndarray:
        if self.incidenceFit is not None:
            return self.incidenceFit.evaluate(
                np.arange(lo, hi)[:, None], np.arange(self.samples)[None, :]
            )
        return self.ddr.band(DdrBand.INCIDENCE_MOLA, slice(lo, hi))

    def _prepare(
        self, start: int, stop: int, tally: bool
    ) -> tuple[np.ndarray, np.ndarray, SpikeReport | None]:
        """Lines [start, stop) masked, with σ, photometry and despike; the
        despike halo is read around them."""
        recipe = self.recipe
        halo = ALONG_TRACK_HALO if recipe.despike.enabled.get() else 0
        lo, hi = max(start - halo, 0), min(stop + halo, self.lines)
        cube = self._read(lo, hi, (start, stop) if tally else None)
        sigma = self.noise.sigma(cube)
        if recipe.photometry.enabled.get():
            cube, sigma = lambert(cube, sigma, self._incidence(lo, hi))
        if not recipe.despike.enabled.get():
            return cube, sigma, None
        return despike(
            cube,
            sigma,
            self.wavelengths,
            recipe.despike.threshold.get(),
            firstLine=start,
            halo=start - lo,
            haloBelow=hi - stop,
        )

    def _fitLines(
        self,
        cube: np.ndarray,
        sigma: np.ndarray,
        model: AtmosphereModel,
        elevationM: np.ndarray | None,
    ) -> PixelFit:
        """``fitPixels`` one line at a time (see the module docstring)."""
        return _mergeFits(
            [
                fitPixels(
                    cube[i : i + 1],
                    sigma[i : i + 1],
                    self.wavelengths,
                    model,
                    None if elevationM is None else elevationM[i : i + 1],
                )
                for i in range(cube.shape[0])
            ]
        )

    # --- pass A --------------------------------------------------------------

    def _selectionLines(self) -> slice:
        """The central ``SELECTION_LINES`` of the usable lines (one run)."""
        usable = np.flatnonzero(self.usable)
        if usable.size < 3:
            raise ValueError(
                f"{self.trr3.id.stem} has {usable.size} usable lines; at least 3 "
                "are needed"
            )
        count = min(SELECTION_LINES, usable.size)
        start = int(usable[0]) + (usable.size - count) // 2
        return slice(start, start + count)

    def _passA(self) -> None:
        central = self._selectionLines()
        span = [central.start, central.stop - 1]
        self.progress.report("passA", 0.0, "Pass A: the noise model")
        raw = self._read(central.start, central.stop, None)
        self.noise: NoiseModel = estimateNoise(raw, wavelengths=self.bandWavelengths)
        noiseSpectrum = _nanMedian(self.noise.sigma(raw).reshape(-1, self.bands), 0)
        self.diagnostics |= {
            "noiseFloor": self.noise.floor,
            "noiseShot": self.noise.shot,
            "noiseAchromaticFraction": self.noise.achromaticFraction,
            "noiseSpectrum": noiseSpectrum,
            "bandWavelengths": self.bandWavelengths,
        }
        self.steps["noise"] = {
            "estimatedOn": "raw I/F (before photometry); σ follows the data "
            "through every step",
            "lines": span,
            "columnGroup": 16,
            "quietFraction": 0.5,
            "medianSigma": float(np.nanmedian(noiseSpectrum)),
            "achromaticFraction": self.noise.achromaticFraction,
        }
        self.steps["photometry"] = self._photometryRecord()
        if not self.recipe.atmosphere.enabled.get():
            self.steps["atmosphere"] = {"enabled": False}
            self._systematicNative(None, None, None)
            return
        self.progress.report("passA", 0.3, "Pass A: choosing the transmission")
        cube, sigma, _ = self._prepare(central.start, central.stop, tally=False)
        spectra, spread = columnSpectra(cube, sigma, self.wavelengths)
        candidates = self._candidates()
        fitShift = bool(self.recipe.atmosphere.fitShift.get())
        selection = selectTransmission(
            spectra, spread, self.wavelengths, candidates, fitShift=fitShift
        )
        shift = selection.fit.shiftNm
        artifact, scale, record = self._artifactModel(selection, spectra, spread)
        free = AtmosphereModel(selection.chosen, shift, artifact, None, scale)
        self.progress.report("passA", 0.7, "Pass A: the elevation model")
        fit = self._fitLines(cube, sigma, free, None)
        elevation = fitElevationModel(
            fit.beta, fit.betaSigma, self.ddr.band(DdrBand.ELEVATION, central)
        )
        withPrior = self.recipe.atmosphere.betaMode.get() is BetaMode.ELEVATION_PRIOR
        model = attrs.evolve(free, elevation=elevation) if withPrior else free
        self.atmosphere = _Atmosphere(
            selection,
            tuple(candidates),
            model,
            record,
            withPrior and elevation.accepted,
        )
        self._systematicNative(selection, spectra, spread)
        self.steps["atmosphere"] = self._atmosphereRecord(span, elevation)
        fitted = selection.fit
        self.diagnostics |= {
            "columnBeta": fitted.beta,
            "columnBetaSigma": fitted.betaSigma,
            "columnShiftNm": fitted.shiftNm,
            "columnResidualRms": fitted.residualRms,
            "columnReducedChiSquare": fitted.reducedChiSquare,
            "columnResiduals": fitted.residuals,
        }
        self.progress.report("passA", 1.0, "Pass A: done")

    def _candidates(self) -> list[Transmission]:
        atmosphere = self.recipe.atmosphere
        found = self.library.transmissions(self.trr3, atmosphere.source.get())
        forced = atmosphere.scan.get().strip().upper()
        if forced:
            chosen = [c for c in found if forced in (c.key.upper(), c.scanId.upper())]
            if not chosen:
                keys = ", ".join(c.key for c in found) or "none"
                raise ValueError(
                    f"The recipe's scan {forced!r} is not among the local "
                    f"transmissions ({keys})."
                )
            found = chosen
        if not found:
            raise ValueError(f"No transmission is available for {self.trr3.id.stem}.")
        return found

    def _artifactModel(
        self, selection: SelectionResult, spectra: np.ndarray, spread: np.ndarray
    ) -> tuple[np.ndarray | None, float | None, dict[str, object]]:
        """The artifact spectra and scale c for the chosen key, or None and why."""
        if not self.recipe.atmosphere.artifactModel.get():
            return None, None, {"used": False, "reason": "off in the recipe"}
        key = selection.chosen.key
        lookup = bundledArtifactBasis(key, self.samples, self.bands, self.trr3.sclk)
        if lookup.basis is None:
            return None, None, {"used": False, "reason": lookup.reason}
        basis = lookup.basis
        probe = AtmosphereModel(selection.chosen, selection.fit.shiftNm, basis.spectra)
        scale, scaleSigma = artifactScale(spectra, spread, self.wavelengths, probe)
        if not math.isfinite(scale):
            return (
                None,
                None,
                {
                    "used": False,
                    "reason": f"{lookup.reason}: its scale is undefined (fewer "
                    "than 3 valid columns)",
                },
            )
        return (
            basis.spectra,
            scale,
            {
                "used": True,
                "reason": lookup.reason,
                "scale": scale,
                "scaleSigma": scaleSigma,
                "sourceObservations": list(basis.sourceObservations),
                "sclkSpan": list(basis.sclkSpan),
                "createdWith": basis.createdWith,
            },
        )

    def _systematicNative(
        self,
        selection: SelectionResult | None,
        spectra: np.ndarray | None,
        spread: np.ndarray | None,
    ) -> None:
        """The scan-choice and artifact-amplitude terms ``(s, b)``, relative."""
        shape = (self.samples, self.bands)
        self.scanChoice = np.zeros(shape)
        self.artifactTerm = np.zeros(shape)
        if selection is None or spectra is None or spread is None:
            return
        wavelengths = self.wavelengths
        finite = {k: v for k, v in selection.scores.items() if math.isfinite(v)}
        ranked = sorted(finite, key=finite.__getitem__)
        if len(ranked) > 1:
            rival = next(c for c in self._candidatesUsed if c.key == ranked[1])
            rivalFit = selectTransmission(
                spectra,
                spread,
                wavelengths,
                [rival],
                fitShift=bool(self.recipe.atmosphere.fitShift.get()),
            ).fit
            best = selection.fit.beta[:, None] * shiftedLogTransmission(
                wavelengths, selection.chosen.values, selection.fit.shiftNm
            )
            other = rivalFit.beta[:, None] * shiftedLogTransmission(
                wavelengths, rival.values, rivalFit.shiftNm
            )
            self.scanChoice = np.abs(best - other)
        record = self.atmosphere.artifact if self.atmosphere is not None else {}
        scaleSigma = record.get("scaleSigma")
        artifact = self.atmosphere.model.artifact if self.atmosphere else None
        if isinstance(scaleSigma, float) and artifact is not None:
            self.artifactTerm = np.abs(
                scaleSigma * selection.fit.beta[:, None] * artifact
            )

    @property
    def _candidatesUsed(self) -> tuple[Transmission, ...]:
        return self.atmosphere.candidates if self.atmosphere is not None else ()

    def _photometryRecord(self) -> dict[str, object]:
        photometry = self.recipe.photometry
        record: dict[str, object] = {
            "enabled": bool(photometry.enabled.get()),
            "incidence": photometry.incidence.get().name,
        }
        if self.incidenceFit is not None:
            record |= {
                "coefficients": self.incidenceFit.coefficients,
                "center": list(self.incidenceFit.center),
                "scale": list(self.incidenceFit.scale),
                "rmsDeg": self.incidenceFit.rmsDeg,
            }
        return record

    def _atmosphereRecord(
        self, span: list[int], elevation: ElevationModel
    ) -> dict[str, object]:
        assert self.atmosphere is not None
        selection = self.atmosphere.selection
        chosen = selection.chosen
        shift = selection.fit.shiftNm
        return {
            "enabled": True,
            "selectionLines": span,
            "source": self.recipe.atmosphere.source.get().name,
            "candidates": [
                {
                    "key": c.key,
                    "scanId": c.scanId,
                    "sourceName": c.sourceName,
                    "sclkStart": c.sclkStart,
                    "sclkStop": c.sclkStop,
                    "extrapolated": c.extrapolated,
                    "valuesSha256": c.sha256,
                }
                for c in self.atmosphere.candidates
            ],
            "scores": selection.scores,
            "chosen": {
                "key": chosen.key,
                "scanId": chosen.scanId,
                "sourceName": chosen.sourceName,
                "extrapolated": chosen.extrapolated,
            },
            "margin": selection.margin,
            "relativeMargin": selection.relativeMargin,
            "winFraction": selection.winFraction,
            "shift": {
                "fitted": bool(self.recipe.atmosphere.fitShift.get()),
                "smoothedMinNm": float(np.min(shift)),
                "smoothedMaxNm": float(np.max(shift)),
            },
            "elevationModel": attrs.asdict(elevation)
            | {"usedAsPrior": self.atmosphere.priorUsed},
            "betaMode": self.recipe.atmosphere.betaMode.get().name,
            "artifact": self.atmosphere.artifact,
        }

    # --- pass B --------------------------------------------------------------

    def _memmap(self, name: str, bands: int) -> np.memmap:
        return np.memmap(
            self.work / name,
            dtype=np.float32,
            mode="w+",
            shape=(self.lines, self.samples, bands),
        )

    def _passB(self) -> None:
        self.cube = self._memmap("atmosphere.f32", self.bands)
        self.sigma = self._memmap("atmosphere_sigma.f32", self.bands)
        self.planes = {
            name: np.full((self.lines, self.samples), np.nan) for name in _PLANES
        }
        self.spikeCount = np.zeros((self.lines, self.samples))
        self.leaskCount = np.zeros((self.lines, self.samples))
        positions: list[np.ndarray] = []
        model = self.atmosphere.model if self.atmosphere is not None else None
        for start, stop in _chunks(self.lines, CHUNK_LINES):
            self.progress.report(
                "passB",
                start / self.lines,
                f"Pass B: photometry, despike and atmosphere, lines {start + 1}–"
                f"{stop} of {self.lines}",
            )
            cube, sigma, spikes = self._prepare(start, stop, tally=True)
            if spikes is not None:
                self.spikeCount[start:stop] = spikes.perPixel
                self.leaskCount[start:stop] = spikes.leaskCount
                positions.append(spikes.positions)
            if model is not None:
                elevation = self.ddr.band(DdrBand.ELEVATION, slice(start, stop))
                fit = self._fitLines(cube, sigma, model, elevation)
                cube, sigma = correct(cube, sigma, self.wavelengths, model, fit)
                self.planes["beta"][start:stop] = fit.beta
                self.planes["betaSigma"][start:stop] = fit.betaSigmaEmpirical
                self.planes["residualRms"][start:stop] = fit.residualRms
                self.planes["artifactAmplitude"][start:stop] = fit.artifactAmplitude
            self.cube[start:stop] = cube
            self.sigma[start:stop] = sigma
            self.progress.check()
        self.cube.flush()
        self.sigma.flush()
        found = np.vstack(positions) if positions else np.zeros((0, 3), np.int32)
        self.diagnostics["spikePositions"] = found.astype(np.int32)
        self.diagnostics["spikesPerBand"] = np.bincount(
            found[:, 2], minlength=self.bands
        )
        self.steps["mask"] = dict(self.maskCounts) | {
            "slewLines": int((~self.usable).sum()),
            "badPixelMap": self.badPixels is not None,
        }
        self.steps["despike"] = {
            "enabled": bool(self.recipe.despike.enabled.get()),
            "threshold": self.recipe.despike.threshold.get(),
            "halo": ALONG_TRACK_HALO,
            "spikes": int(found.shape[0]),
            "leask": int(self.leaskCount.sum()),
        }
        if self.atmosphere is not None:
            beta = self.planes["beta"]
            record = self.steps["atmosphere"]
            record["pixels"] = {
                "valid": int(np.isfinite(beta).sum()),
                "medianBeta": float(np.nanmedian(beta))
                if np.isfinite(beta).any()
                else None,
                "medianBetaSigmaEmpirical": float(
                    np.nanmedian(self.planes["betaSigma"])
                )
                if np.isfinite(self.planes["betaSigma"]).any()
                else None,
            }

    # --- pass C --------------------------------------------------------------

    def _columnWavelengths(self) -> np.ndarray:
        """The wavelengths each column is resampled from: the WA (moved by the
        fitted shift when asked), or with the smile step off, the bands'
        median wavelengths in every column (no smile correction)."""
        if not self.recipe.smile.enabled.get():
            return np.broadcast_to(self.bandWavelengths, self.wavelengths.shape)
        if self.recipe.smile.applyFittedShift.get() and self.atmosphere is not None:
            return self.wavelengths + self.atmosphere.selection.fit.shiftNm[:, None]
        return self.wavelengths

    def _resampleWeights(self) -> ResampleWeights:
        target = self.vector.wavelengths
        return linearWeights(
            self._columnWavelengths(),
            target,
            sourceSegments(self.wavelengths),
            segmentIds(target),
        )

    def _passC(self) -> None:
        recipe = self.recipe
        vector = self.vector
        count = len(vector.wavelengths)
        self.weights = self._resampleWeights()
        destriping = bool(recipe.destripe.enabled.get())
        escOn = bool(recipe.smile.enabled.get() and recipe.smile.esc.get())
        bland = None
        if destriping or escOn:
            self.progress.report("passC", 0.0, "Pass C: finding bland pixels")
            bland = blandMask(self.cube, self.wavelengths)
        if destriping:
            assert bland is not None
            self.progress.report("passC", 0.05, "Pass C: estimating the stripe frame")
            self.stripes = estimateStripes(
                self.cube,
                self.sigma,
                self.wavelengths,
                bland,
                splitHalf=bool(recipe.destripe.splitHalf.get()),
            )
        self.resampled = self._memmap("resampled.f32", count)
        self.resampledSigma = self._memmap("resampled_sigma.f32", count)
        for start, stop in _chunks(self.lines, CHUNK_LINES):
            self.progress.report(
                "passC",
                0.1 + 0.4 * start / self.lines,
                f"Pass C: {'destripe and ' if destriping else ''}resampling, "
                f"lines {start + 1}–{stop}",
            )
            cube = np.asarray(self.cube[start:stop], dtype=np.float64)
            sigma = np.asarray(self.sigma[start:stop], dtype=np.float64)
            if self.stripes is not None:
                cube, sigma = applyStripes(cube, sigma, self.stripes)
            cube, sigma = resample(cube, sigma, self.weights)
            self.resampled[start:stop] = cube
            self.resampledSigma[start:stop] = sigma
            self.progress.check()
        if escOn:
            assert bland is not None
            self.progress.report("passC", 0.5, "Pass C: estimating the ESC frame")
            self.esc = self._escFrame(bland)
            for start, stop in _chunks(self.lines, CHUNK_LINES):
                self.progress.report(
                    "passC",
                    0.55 + 0.25 * start / self.lines,
                    f"Pass C: ESC, lines {start + 1}–{stop}",
                )
                cube, sigma = applyEsc(
                    np.asarray(self.resampled[start:stop], dtype=np.float64),
                    np.asarray(self.resampledSigma[start:stop], dtype=np.float64),
                    self.esc,
                )
                self.resampled[start:stop] = cube
                self.resampledSigma[start:stop] = sigma
                self.progress.check()
        self.resampled.flush()
        self.resampledSigma.flush()
        self._bandList()
        self._recordPassC(destriping, escOn)
        sensor = self.recipe.output.sensorSpace.get() or not (
            self.recipe.projection.enabled.get()
        )
        if sensor and self._writing():
            self._writeSensorSpace()

    def _escFrame(self, bland: np.ndarray) -> EscFrame:
        """ESC from the bland pixels' column spectra of the resampled cube,
        a block of columns at a time (columns are independent)."""
        target = self.vector.wavelengths
        spectra = np.full((self.samples, len(target)), np.nan)
        wavelengths = np.broadcast_to(target, spectra.shape)
        for first in range(0, self.samples, _COLUMN_BLOCK):
            columns = slice(first, min(first + _COLUMN_BLOCK, self.samples))
            cube = np.array(self.resampled[:, columns], dtype=np.float64)
            sigma = np.array(self.resampledSigma[:, columns], dtype=np.float64)
            cube[~bland[:, columns]] = np.nan
            spectra[columns], _ = columnSpectra(cube, sigma, wavelengths[columns])
        offsets = escOffsets(self._alignedToVector(), target)
        binNm = _ESC_BIN_NM.get(self.trr3.id.classType, _DEFAULT_ESC_BIN_NM)
        self.escBinNm = binNm
        return estimateEsc(spectra, offsets, binNm, target)

    def _alignedToVector(self) -> np.ndarray:
        """The resampling wavelengths at the vector's detector rows ``(s, n)``."""
        index = {int(row): i for i, row in enumerate(self.trr3.detectorRows)}
        missing = [int(r) for r in self.vector.detectorRows if int(r) not in index]
        if missing:
            raise ValueError(
                f"{self.trr3.id.stem} has no detector row {missing[0]} of the "
                "MTRDR vector"
            )
        rows = [index[int(r)] for r in self.vector.detectorRows]
        return self._columnWavelengths()[:, rows]

    def _bandList(self) -> None:
        """S7: the vector's own bbl, and 0 for any band valid in fewer than
        half of the scene's pixels (``footprint``: those with any valid band)."""
        count = len(self.vector.wavelengths)
        valid = np.zeros(count)
        self.footprint = np.zeros((self.lines, self.samples), dtype=bool)
        for start, stop in _chunks(self.lines, CHUNK_LINES):
            finite = np.isfinite(self.resampled[start:stop])
            valid += finite.sum(axis=(0, 1))
            self.footprint[start:stop] = finite.any(axis=2)
        pixels = int(self.footprint.sum())
        fraction = valid / pixels if pixels else np.zeros(count)
        self.validFraction = fraction
        self.bbl = ((self.vector.bbl == 1) & (fraction >= VALID_BAND_FRACTION)).astype(
            np.uint8
        )

    def _recordPassC(self, destriping: bool, escOn: bool) -> None:
        record: dict[str, object] = {"enabled": destriping}
        if self.stripes is not None:
            frame = self.stripes
            record |= {
                "splitHalf": bool(self.recipe.destripe.splitHalf.get()),
                "columnsWithSmoothPart": int(frame.smoothAccepted.sum()),
                "elementsWithHighFrequency": int(frame.highFrequencyKept.sum()),
                "medianFactorSigma": float(np.median(frame.sigma / frame.factors)),
            }
            self.diagnostics |= {
                "stripeFactors": frame.factors,
                "stripeSigma": frame.sigma,
                "stripeHighFrequency": frame.highFrequency,
                "stripeHighFrequencyKept": frame.highFrequencyKept,
                "stripeSmooth": frame.smooth,
                "stripeSmoothAccepted": frame.smoothAccepted,
            }
        self.steps["destripe"] = record
        smile = self.recipe.smile
        self.steps["smile"] = {
            "enabled": bool(smile.enabled.get()),
            "method": smile.method.get().name,
            "target": "MTRDR IR standard vector",
            "bands": len(self.vector.wavelengths),
            "esc": escOn,
            "escBinNm": self.escBinNm,
            "appliedFittedShift": bool(
                smile.enabled.get()
                and smile.applyFittedShift.get()
                and self.atmosphere is not None
            ),
        }
        if self.esc is not None:
            self.diagnostics["escCorrection"] = self.esc.correction
        self.steps["bands"] = {
            "bbl": self.bbl,
            "validFraction": self.validFraction,
            "badBands": int((self.bbl == 0).sum()),
            "rule": "bbl = 0 above 2600 nm and where a band is valid in fewer "
            "than half of the scene's pixels",
        }
        self.diagnostics |= {
            "wavelengths": self.vector.wavelengths,
            "bbl": self.bbl,
            "validFraction": self.validFraction,
        }

    # --- writing -------------------------------------------------------------

    def _history(self, mapped: bool) -> tuple[str, list[str]]:
        """The ``varda history`` string and the steps that shaped σ."""
        recipe = self.recipe
        steps: list[str] = []
        sigmaSteps = ["NOISE"]
        if recipe.photometry.enabled.get():
            kind = (
                "areoid-poly"
                if recipe.photometry.incidence.get()
                is IncidenceSource.AREOID_POLYNOMIAL
                else "mola"
            )
            steps.append(f"PHT({kind})")
            sigmaSteps.append("PHT")
        if recipe.despike.enabled.get():
            steps.append(f"DSK({recipe.despike.threshold.get():.1f})")
            sigmaSteps.append("DSK")
        if self.atmosphere is not None:
            atm = self.atmosphere
            parts = [
                "fit",
                atm.selection.chosen.key,
                "shift" if recipe.atmosphere.fitShift.get() else "noshift",
                "elev" if atm.priorUsed else "pixel",
                "resid1" if atm.model.artifact is not None else "noresid",
            ]
            steps.append(f"ATM({';'.join(parts)})")
            sigmaSteps.append("ATM")
        if self.stripes is not None:
            steps.append("RSC(frame)")
            sigmaSteps.append("RSC")
        # resampling to the vector always shapes σ; with the smile step off it
        # is from the bands' nominal wavelengths
        if not recipe.smile.enabled.get():
            steps.append("SMI(nominal)")
        else:
            steps.append("ESC(linear)" if self.esc is not None else "SMI(linear)")
        sigmaSteps.append("SMI")
        if self.esc is not None:
            sigmaSteps.append("ESC")
        if mapped:
            projection = recipe.projection
            kind = "nn" if projection.resampling.get() is Resampling.NEAREST else "area"
            projectionName = "eqc" if self.grid.kind == "equirectangular" else "ps"
            steps.append(f"MAP({projectionName};{self.grid.pixelSize:g}m;{kind})")
            sigmaSteps.append("MAP")
        return historyString(steps), sigmaSteps

    def _description(self, what: str, history: str) -> str:
        summary = (
            f"Made with Varda {vardaVersion()}: CRISM processing ({history}) from "
            f"{self.trr3.id.stem} on {self.created}"
        )
        return f"{what}. {PROVENANCE_KEY}: {summary}"

    def _writer(
        self,
        name: str,
        lines: int,
        samples: int,
        bands: EnviBands,
        grid: MapGrid | None,
        what: str,
        withSigma: bool,
    ) -> EnviCubeWriter:
        """A staged product; ``withSigma`` names its ``_sigma`` companion."""
        history, sigmaSteps = self._history(grid is not None)
        extra = {"varda history": history}
        if withSigma:
            extra |= {
                "varda sigma file": f"{name}_sigma.hdr",
                "varda sigma kind": SIGMA_KIND,
                "varda sigma steps": ", ".join(sigmaSteps),
            }
        path = self.staging / f"{name}.img"
        self.outputs[name] = path
        return EnviCubeWriter(
            path, lines, samples, bands, grid, self._description(what, history), extra
        )

    def _spectralBands(self) -> EnviBands:
        return EnviBands(
            (),
            self.vector.wavelengths,
            self.vector.fwhm,
            self.bbl,
            _defaultBands(self.vector),
        )

    def _writeSensorSpace(self) -> None:
        name = self.names["sensor"]
        bands = self._spectralBands()
        what = "CRISM IR I/F on the MTRDR wavelength vector, sensor space"
        with (
            self._writer(
                name, self.lines, self.samples, bands, None, what, withSigma=True
            ) as data,
            self._writer(
                f"{name}_sigma",
                self.lines,
                self.samples,
                bands,
                None,
                f"1-sigma ({SIGMA_KIND}) of {name}",
                withSigma=False,
            ) as sigma,
        ):
            for start, stop in _chunks(self.lines, CHUNK_LINES):
                self.progress.report(
                    "passC",
                    0.8 + 0.2 * start / self.lines,
                    f"Pass C: writing the sensor-space product, lines {start + 1}–{stop}",
                )
                data.write(start, self.resampled[start:stop])
                sigma.write(start, self.resampledSigma[start:stop])
                self.progress.check()

    # --- pass D --------------------------------------------------------------

    def _passD(self) -> None:
        projection = self.recipe.projection
        lat = self.ddr.band(DdrBand.LATITUDE)
        lon = self.ddr.band(DdrBand.LONGITUDE)
        pixelSize = projection.pixelSize.get() or PIXEL_SIZE_M * self.trr3.binning
        self.progress.report("passD", 0.0, "Pass D: the map grid")
        self.grid = mtrdrGrid(lat, lon, pixelSize, bool(projection.snapToLattice.get()))
        grid = self.grid
        glt = nearestNeighborGlt(grid, lat, lon, projection.maxDistanceFactor.get())
        area = (
            areaWeights(grid, lat, lon)
            if projection.resampling.get() is Resampling.AREA
            else None
        )
        bands = self._spectralBands()
        name = self.names["map"]
        what = "CRISM IR I/F on the MTRDR wavelength vector, map projected"
        with (
            self._writer(
                name, grid.height, grid.width, bands, grid, what, withSigma=True
            ) as data,
            self._writer(
                f"{name}_sigma",
                grid.height,
                grid.width,
                bands,
                grid,
                f"1-sigma ({SIGMA_KIND}) of {name}",
                withSigma=False,
            ) as sigma,
        ):
            if area is None:
                for start, stop in _chunks(grid.height, _MAP_ROWS):
                    self.progress.report(
                        "passD",
                        0.1 + 0.7 * start / grid.height,
                        f"Pass D: projecting rows {start + 1}–{stop} of {grid.height}",
                    )
                    part = Glt(glt.sourceLine[start:stop], glt.sourceSample[start:stop])
                    data.write(start, applyGlt(self.resampled, part))
                    sigma.write(start, applyGlt(self.resampledSigma, part))
                    self.progress.check()
            else:
                count = len(self.vector.wavelengths)
                for first in range(0, count, _BAND_BLOCK):
                    block = slice(first, min(first + _BAND_BLOCK, count))
                    self.progress.report(
                        "passD",
                        0.1 + 0.7 * first / count,
                        f"Pass D: area weighting bands {block.start + 1}–{block.stop}",
                    )
                    mean, meanSigma = applyAreaWeights(
                        self.resampled[:, :, block],
                        self.resampledSigma[:, :, block],
                        area,
                    )
                    data.writeBands(first, mean)
                    sigma.writeBands(first, meanSigma)
                    self.progress.check()
        self.progress.report(
            "passD", 0.85, "Pass D: geometry and processing backplanes"
        )
        self._writeBackplanes(glt, area)
        self._recordProjection(glt)

    def _writeBackplanes(self, glt: Glt, area: AreaWeights | None) -> None:
        grid = self.grid
        ddr = np.stack([self.ddr.band(band) for band in DdrBand], axis=-1)
        with self._writer(
            self.names["geometry"],
            grid.height,
            grid.width,
            EnviBands(_bandNames(self.ddr), defaultBands=(1, 1, 1)),
            grid,
            "CRISM DDR backplanes, map projected (nearest neighbor)",
            withSigma=False,
        ) as writer:
            writer.write(0, applyGlt(ddr, glt))
        # the bands derived from the data are no-data where the source pixel
        # has none (Review Focus 2); its geometry and position are still known
        filled = glt.sourceLine >= 0
        hasData = np.where(self.footprint, 1.0, np.nan)
        planes = np.stack(
            [self.planes[name] for name in _PLANES]
            + [self.spikeCount * hasData, self.leaskCount * hasData],
            axis=-1,
        )
        if area is None:
            mapped = applyGlt(planes, glt)
            looks = applyGlt(hasData, glt)
        else:
            mapped, _ = applyAreaWeights(planes, np.zeros(planes.shape), area)
            covered, _ = applyAreaWeights(hasData, np.zeros(hasData.shape), area)
            looks = np.where(np.isfinite(covered), area.looks, np.nan)
        info = np.concatenate(
            [
                np.where(filled, glt.sourceSample, np.nan)[..., None],
                np.where(filled, glt.sourceLine, np.nan)[..., None],
                mapped,
                looks[..., None],
            ],
            axis=-1,
        )
        with self._writer(
            self.names["info"],
            grid.height,
            grid.width,
            EnviBands(IN_BAND_NAMES, defaultBands=(1, 1, 1)),
            grid,
            "Varda CRISM processing backplanes (source sample and line are "
            "0-based and nearest neighbor; looks is the effective number)",
            withSigma=False,
        ) as writer:
            writer.write(0, info)

    def _recordProjection(self, glt: Glt) -> None:
        grid = self.grid
        projection = self.recipe.projection
        record: dict[str, object] = {
            "enabled": True,
            "kind": grid.kind,
            "referenceLatDeg": grid.referenceLatDeg,
            "radius": grid.radius,
            "pixelSize": grid.pixelSize,
            "originX": grid.originX,
            "originY": grid.originY,
            "width": grid.width,
            "height": grid.height,
            "resampling": projection.resampling.get().name,
            "maxDistanceFactor": projection.maxDistanceFactor.get(),
            "snapToLattice": bool(projection.snapToLattice.get()),
            "filledPixels": int((glt.sourceLine >= 0).sum()),
            "crsWkt": grid.crsWkt(),
        }
        if grid.kind == "equirectangular":
            limit = math.pi * grid.radius * math.cos(math.radians(grid.referenceLatDeg))
            if (
                max(abs(grid.originX), abs(grid.originX + grid.width * grid.pixelSize))
                > limit
            ):
                record["note"] = (
                    "the scene straddles ±180°: x is unwrapped and passes ±π R cos φ_P"
                )
        self.steps["projection"] = record

    # --- sidecars ------------------------------------------------------------

    def _systematic(self) -> dict[str, object]:
        """The systematic terms per output band (relative to I/F)."""
        zeros = np.zeros((self.samples, self.bands))
        terms = {
            "scanChoice": self.scanChoice,
            "artifactAmplitude": self.artifactTerm,
            "destripeFrame": (
                self.stripes.sigma / self.stripes.factors
                if self.stripes is not None
                else zeros
            ),
        }
        resampled: dict[str, np.ndarray] = {}
        for key, term in terms.items():
            values, _ = resample(term[None], np.zeros((1, *term.shape)), self.weights)
            resampled[key] = _nanMedian(values[0], axis=0)
            self.diagnostics[f"systematic{key[0].upper()}{key[1:]}"] = resampled[key]
        return {
            "wavelengths": self.vector.wavelengths,
            **resampled,
            "note": "relative to I/F, per output band, the median over columns; "
            "not part of σ (N2). scanChoice: the chosen transmission against the "
            "runner-up on the column spectra; artifactAmplitude: σ of the scale "
            "times β times the basis; destripeFrame: the frame's σ",
        }

    def _calibrationRecord(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        seen: set[str] = set()

        def add(
            kind: str, name: str, path: Path | None, required: bool, note: str
        ) -> None:
            seen.add(name.lower())
            records.append(
                {
                    "kind": kind,
                    "name": name.lower(),
                    "path": path,
                    "sha256": sha256File(path) if path is not None else None,
                    "required": required,
                    "note": note,
                }
            )

        for entry in self.status:
            add(entry.kind, entry.name, entry.localPath, entry.required, entry.note)
        for candidate in self._candidatesUsed:
            name = candidate.sourceName.lower()
            if name in seen:
                continue
            kind = "AT" if candidate.artifacts is None else "VS"
            add(
                kind,
                name,
                self.library.localFile(kind, name) if kind == "AT" else None,
                False,
                f"used as the candidate {candidate.key}",
            )
        return records

    def _inputs(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for role, product in (("TRR3", self.trr3), ("DDR", self.ddr)):
            data = product.label.image.dataFile
            records.append(
                {
                    "role": role,
                    "productId": product.id.stem.upper(),
                    "label": product.label.path,
                    "labelSha256": sha256File(product.label.path),
                    "data": data,
                    "sha256": sha256File(data),
                }
            )
        return records

    def _finish(self) -> ProcessResult:
        self.progress.report("sidecars", 0.0, "Writing the recipe and provenance")
        name = self.names["map"]
        if self._writing() and self.recipe.output.cog.get() and name in self.outputs:
            tif = self.staging / f"{name}.tif"
            writeCog(self.outputs[name], tif)
            self.outputs[f"{name}.tif"] = tif
        mapped = name in self.outputs
        history, sigmaSteps = self._history(mapped)
        provenance = {
            "vardaVersion": vardaVersion(),
            "created": self.created,
            "observation": self.observation.name,
            "counter": self.observation.counter,
            "inputs": self._inputs(),
            "calibration": self._calibrationRecord(),
            "recipe": recipeToDict(self.recipe),
            "history": history,
            "sigma": {"kind": SIGMA_KIND, "steps": sigmaSteps},
            "steps": self.steps,
            "systematic": self._systematic(),
            "wallTimes": self.wallTimes,
            "outputs": sorted(
                [path.name for path in self.staging.iterdir() if path.is_file()]
                + [f"{name}{suffix}" for suffix in _SIDECARS]
            ),
        }
        recipePath, provenancePath, diagnosticsPath = writeSidecars(
            self.staging / name, recipeToDict(self.recipe), provenance, self.diagnostics
        )
        self.progress.report("sidecars", 1.0, "Done")

        def product(key: str) -> Path | None:
            return self.outputs.get(self.names[key])

        return ProcessResult(
            mapProjected=product("map"),
            mapSigma=self.outputs.get(f"{name}_sigma"),
            geometry=product("geometry"),
            info=product("info"),
            sensorSpace=product("sensor"),
            recipe=recipePath,
            provenance=provenancePath,
            diagnostics=diagnosticsPath,
        )


def _publish(staged: ProcessResult, staging: Path, outputFolder: Path) -> ProcessResult:
    """Move every staged file into the output folder, the sidecars last."""
    files = sorted(p for p in staging.iterdir() if p.is_file())
    for path in sorted(files, key=lambda p: p.name.endswith(_SIDECARS)):
        os.replace(path, outputFolder / path.name)

    def moved(path: Path | None) -> Path | None:
        return outputFolder / path.name if path is not None else None

    return ProcessResult(
        mapProjected=moved(staged.mapProjected),
        mapSigma=moved(staged.mapSigma),
        geometry=moved(staged.geometry),
        info=moved(staged.info),
        sensorSpace=moved(staged.sensorSpace),
        recipe=outputFolder / staged.recipe.name,
        provenance=outputFolder / staged.provenance.name,
        diagnostics=outputFolder / staged.diagnostics.name,
    )


def process(
    observation: CrismObservation,
    recipe: Recipe,
    outputFolder: Path,
    *,
    library: CalibrationLibrary,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ProcessResult:
    """Process one observation's IR cube with ``recipe`` into ``outputFolder``
    (created if needed); the module docstring describes the passes.

    ``library.ensure`` runs first, so missing calibration files raise
    ``MissingCalibrationError`` before any pass. ``progress`` gets a fraction
    from 0 to 1 that never decreases, and a message naming the pass.
    ``cancelled`` is asked between passes and chunks; when it returns True,
    ``ProcessCancelled`` is raised. On a cancel or any failure nothing is
    written: the products already in the folder stay as they were."""
    _checkRecipe(recipe)
    outputFolder = Path(outputFolder)
    outputFolder.mkdir(parents=True, exist_ok=True)
    work = outputFolder / WORK_FOLDER
    shutil.rmtree(work, ignore_errors=True)
    (work / "out").mkdir(parents=True)
    try:
        run = _Run(observation, recipe, work, library, _Progress(progress, cancelled))
        staged = run.run()
        del run  # closes the work memmaps before the folder goes
        return _publish(staged, work / "out", outputFolder)
    finally:
        shutil.rmtree(work, ignore_errors=True)
