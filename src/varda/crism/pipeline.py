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
  samples, columns without a wavelength, slew lines; not the BP-flagged
  elements, which the calibration already interpolated), σ,
  photometry, despike and the atmospheric fit and correction, into a float32
  work memmap of I/F and σ in the run's work folder.
- **C**: the destripe frame (estimated from the work memmap, off by default),
  resampling to the MTRDR vector, and the ESC frame (estimated from the
  resampled memmap's bland pixels), into a second memmap; ``bbl`` (S7); the
  sensor-space product when asked for, or when the projection is off.
- **D**: the projection (the nearest-neighbor GLT, or area weights) of the
  I/F and σ, from the usable lines' positions only (slew lines hold no I/F),
  the DDR's 13 backplanes and its spare (DE, always through the GLT, also when the
  I/F is area weighted: averaging longitudes or azimuths across their wrap is
  meaningless), and the IN backplanes.

Each run works in its own folder, ``.work-<run id>`` in the output folder,
so two runs into one folder do not touch each other's work. Products are
written into its ``out`` and moved into the output folder only when
everything succeeded, as one transaction (``_publish``), so a failure, a
cancel, or a product open in another program leaves the folder as it was, a
previous run's products included. The work folder is removed in every case;
when it cannot be, a warning names it. A work folder left by a run that was
killed is removed by a later run once it is a day old
(``STALE_WORK_SECONDS``), never sooner: it may belong to a run in progress.

``fitPixels`` is called one line at a time. It weighs a column's complete
lines by their mean σ, which would make β depend on how the lines are
chunked; per line the weights are the pixel's own and nothing depends on
``CHUNK_LINES`` (``test_chunking_invariance``).

The systematic uncertainty is not in σ (N2). The provenance reports it per
output band, relative to I/F, as the median over columns of: the scan choice
(the column spectra corrected with the chosen transmission against the
runner-up), the artifact amplitude (the scale's σ times β times the basis),
the atmosphere's β excess (|ln T′| times the empirical σ_β's excess over the
formal one, √max(σ²_emp − σ²_formal, 0)) and the destripe frame's bootstrap
σ (the frame divides σ, σ/f, and adds no variance to it). The spec's Monte
Carlo over δ, the scan and the amplitude on 1,000 pixels is not done here.
The empirical σ_β is ``PixelFit.betaSigmaEmpirical`` with the elevation
prior, and the formal σ_β times the elevation model's along-track
``sigmaRatio`` without it.

Memory: anonymous memory does not grow with the scene's length (Pass A reads
``SELECTION_LINES``, the other passes a chunk, a block of output rows or a
block of 16 columns at a time). The work memmaps are flushed after every
chunk, so the OS may reclaim their pages, and the products are written with
plain file I/O (``EnviCubeWriter``), the I/F and its σ one after the other.
"""

import contextlib
import gc
import logging
import math
import os
import platform
import shutil
import subprocess
import tempfile
import time
import tomllib
import traceback
import warnings
from collections.abc import Callable, Generator, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path

import attrs
import numpy as np
import rasterio
import scipy
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
WORK_PREFIX = ".work-"
STALE_WORK_SECONDS = 86400.0
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
_COLUMN_BLOCK = 16  # columns per ESC column-spectrum block: (lines, 16, 411)
_MAP_ROWS = 64
_BAND_BLOCK = 32
_PLANES = ("beta", "betaSigma", "residualRms", "artifactAmplitude")
_SIDECARS = (".recipe.json", ".provenance.json", ".diagnostics.npz")
_AREA_IN_NAMES = {"Spike count": "Mean spike count", "Leask count": "Mean Leask count"}
_GIT_TIMEOUT_S = 5

_log = logging.getLogger(__name__)
# (first line or band, block, by lines?) pieces of a product
type _Pieces = Generator[tuple[int, np.ndarray, bool]]
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


class ProductInUseError(OSError):
    """A product of an earlier run could not be replaced (on Windows: it is
    open in another program); the earlier products were restored."""


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


def _git(*args: str) -> str | None:
    """A git command's output in this package's folder, or None (no git, not
    a checkout, a frozen bundle). No console window opens on Windows."""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip()


def _isVardaCheckout(top: str) -> bool:
    """Whether the repository at ``top`` is Varda's own (its pyproject.toml
    names the project ``varda``), not one Varda happens to sit in."""
    try:
        with (Path(top) / "pyproject.toml").open("rb") as file:
            project = tomllib.load(file).get("project", {})
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return project.get("name") == "varda"


def _software() -> dict[str, object]:
    """Versions, and the git commit (with a dirty flag) when Varda runs from
    a checkout; best effort."""
    top = _git("rev-parse", "--show-toplevel")
    commit = _git("rev-parse", "HEAD") if top and _isVardaCheckout(top) else None
    status = _git("status", "--porcelain", "--untracked-files=no") if commit else None
    return {
        "varda": vardaVersion(),
        "gitCommit": commit,
        "gitDirty": bool(status) if status is not None else None,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "rasterio": rasterio.__version__,
    }


@frozen(eq=False)
class _Atmosphere:
    """What Pass A decided for the atmospheric correction."""

    selection: SelectionResult
    candidates: tuple[Transmission, ...]
    model: AtmosphereModel
    artifact: dict[str, object]
    priorUsed: bool
    # the elevation model's empirical over formal σ_β (1 when undefined); the
    # empirical σ_β of a fit without the prior is the formal one times it
    sigmaRatio: float


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
        ddrShape = (self.ddr.label.image.lines, self.ddr.label.image.samples)
        if ddrShape != (self.lines, self.samples):
            raise ValueError(
                f"{self.trr3.id.stem} is {self.lines} × {self.samples} (lines × "
                f"samples) but its DDR {self.ddr.id.stem} is {ddrShape[0]} × "
                f"{ddrShape[1]}: they must come from the same observation and crop"
            )
        self.vector = standardVector()
        self.created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.wallTimes: dict[str, float] = {}
        self.steps: dict[str, dict[str, object]] = {}
        self.diagnostics: dict[str, np.ndarray] = {}
        self.maskCounts = {
            "invalidSamples": 0,
            "noWavelength": 0,
            "slewLine": 0,
            # BP-flagged samples the calibration already replaced: counted, kept
            "badPixelInterpolated": 0,
        }
        # set by the passes, in order
        self.status: list[CalibrationFileStatus] = []
        self.wavelengths = np.empty((0, 0))  # (s, b) the WA
        self.bandWavelengths = np.empty(0)  # (b,) its median over columns
        self.badPixels: np.ndarray | None = None
        self.usable = np.empty(0, dtype=bool)
        self.incidenceFit: IncidenceFit | None = None
        self.noise: NoiseModel | None = None
        self.scanChoice = np.empty((0, 0))
        self.artifactTerm = np.empty((0, 0))
        self.planes: dict[str, np.ndarray] = {}
        self.betaSigmaFormal = np.empty((0, 0))
        self.spikeCount = np.empty((0, 0))
        self.leaskCount = np.empty((0, 0))
        self.memmaps: dict[str, np.memmap] = {}  # the work files, by name
        self.weights: ResampleWeights | None = None
        self.footprint = np.empty((0, 0), dtype=bool)
        self.validFraction = np.empty(0)
        self.bbl = np.empty(0, dtype=np.uint8)
        self.grid: MapGrid | None = None
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
        projected = self.recipe.projection.enabled.get()
        if projected:
            self._timed("passD", self._passD)
        else:
            self.steps["projection"] = {"enabled": False}
        return self._timed("sidecars", self._finish)

    def close(self) -> None:
        """Drop every work memmap, so the work folder can be removed (Windows
        cannot delete a mapped file)."""
        self.memmaps.clear()

    def _timed[T](self, stage: str, step: Callable[[], T]) -> T:
        start = time.perf_counter()
        result = step()
        self.wallTimes[stage] = time.perf_counter() - start
        return result

    # --- calibration and masks -----------------------------------------------

    def _calibration(self) -> None:
        self.progress.report("calibration", 0.0, "Checking the calibration files")
        self.status = self.library.ensure(self.trr3)
        self.wavelengths = self.library.wavelengths(self.trr3)  # (s, b)
        self.bandWavelengths = _nanMedian(self.wavelengths, axis=0)
        self.badPixels = self.library.badPixels(self.trr3)
        self.usable = usableLines(self.ddr)
        if self.recipe.photometry.incidence.get() is IncidenceSource.AREOID_POLYNOMIAL:
            incidence = self.ddr.band(DdrBand.INCIDENCE_AREOID)
            incidence[~self.usable] = np.nan
            try:
                self.incidenceFit = fitIncidence(incidence)
            except ValueError as error:
                raise ValueError(
                    f"{self.trr3.id.stem}: no valid incidence in the DDR "
                    f"{self.ddr.id.stem} ({error}); photometry needs it"
                ) from error
        self.progress.report("calibration", 1.0, "Calibration files found")

    def _read(self, lo: int, hi: int, tally: tuple[int, int] | None) -> np.ndarray:
        """TRR3 lines [lo, hi) with every masked sample NaN; ``tally`` (start,
        stop) counts each mask reason over those lines.

        Elements flagged in the BP CDRs are not masked: they were interpolated
        during calibration, in DN space, from the neighboring columns of the
        same detector row (DPSIS §2.6, the a priori mask, and §2.15), so in the
        TRR3 they are finite, non-outlier values, which the MTRDRs use. Masking
        them dropped 17% of FRS00031442's samples (the 2014 BPs flag 14–24% of
        the elements) and left 98% of its pixels with a gap in the CO2 window.
        They are counted (``badPixelInterpolated``); σ treats them as
        independent samples, though each is a mean of two neighbors."""
        cube = self.trr3.lines(slice(lo, hi))
        invalid = np.isnan(cube)
        inner = slice(tally[0] - lo, tally[1] - lo) if tally is not None else None
        if inner is not None:
            self.maskCounts["invalidSamples"] += int(invalid[inner].sum())
        shape = cube.shape
        masks = [
            ("noWavelength", np.broadcast_to(~np.isfinite(self.wavelengths), shape))
        ]
        slew = ~self.usable[lo:hi]
        masks.append(("slewLine", np.broadcast_to(slew[:, None, None], shape)))
        for reason, mask in masks:
            if inner is not None:
                self.maskCounts[reason] += int((mask & ~invalid)[inner].sum())
            invalid |= mask
        if inner is not None and self.badPixels is not None:
            interpolated = np.broadcast_to(self.badPixels, shape) & ~invalid
            self.maskCounts["badPixelInterpolated"] += int(interpolated[inner].sum())
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
        assert self.noise is not None  # Pass A
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
        noise = self.noise = estimateNoise(raw, wavelengths=self.bandWavelengths)
        noiseSpectrum = _nanMedian(noise.sigma(raw).reshape(-1, self.bands), 0)
        self.diagnostics |= {
            "noiseFloor": noise.floor,
            "noiseShot": noise.shot,
            "noiseAchromaticFraction": noise.achromaticFraction,
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
            "achromaticFraction": noise.achromaticFraction,
        }
        self.steps["photometry"] = self._photometryRecord()
        if not self.recipe.atmosphere.enabled.get():
            self.steps["atmosphere"] = {"enabled": False}
            self._systematicNative(None, None, None)
            return
        self.progress.report("passA", 0.3, "Pass A: choosing the transmission")
        cube, sigma, _ = self._prepare(central.start, central.stop, tally=False)
        spectra, spread, candidates, selection = self._chooseTransmission(cube, sigma)
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
            elevation.sigmaRatio if math.isfinite(elevation.sigmaRatio) else 1.0,
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

    def _chooseTransmission(
        self, cube: np.ndarray, sigma: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, list[Transmission], SelectionResult]:
        """The column spectra of ``cube`` and the transmission they choose
        (S4.A, without the artifact basis)."""
        spectra, spread = columnSpectra(cube, sigma, self.wavelengths)
        candidates = self._candidates()
        fitShift = bool(self.recipe.atmosphere.fitShift.get())
        selection = selectTransmission(
            spectra, spread, self.wavelengths, candidates, fitShift=fitShift
        )
        return spectra, spread, candidates, selection

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
            "continuumDegree": CONTINUUM_DEGREE,
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
        """A float32 ``(l, s, bands)`` work file, kept in ``memmaps``."""
        memmap = np.memmap(
            self.work / f"{name}.f32",
            dtype=np.float32,
            mode="w+",
            shape=(self.lines, self.samples, bands),
        )
        self.memmaps[name] = memmap
        return memmap

    def _passB(self) -> None:
        work = self._memmap("atmosphere", self.bands)
        workSigma = self._memmap("atmosphereSigma", self.bands)
        self.planes = {
            name: np.full((self.lines, self.samples), np.nan) for name in _PLANES
        }
        self.betaSigmaFormal = np.full((self.lines, self.samples), np.nan)
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
                assert self.atmosphere is not None
                atmosphere = self.atmosphere
                self.planes["betaSigma"][start:stop] = (
                    fit.betaSigmaEmpirical
                    if atmosphere.priorUsed
                    else fit.betaSigma * atmosphere.sigmaRatio
                )
                self.betaSigmaFormal[start:stop] = fit.betaSigma
                self.planes["residualRms"][start:stop] = fit.residualRms
                self.planes["artifactAmplitude"][start:stop] = fit.artifactAmplitude
            work[start:stop] = cube
            workSigma[start:stop] = sigma
            work.flush()  # the OS may reclaim the written pages
            workSigma.flush()
            self.progress.check()
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
        weights = self.weights = self._resampleWeights()
        work, workSigma = self.memmaps["atmosphere"], self.memmaps["atmosphereSigma"]
        destriping = bool(recipe.destripe.enabled.get())
        escOn = bool(recipe.smile.enabled.get() and recipe.smile.esc.get())
        bland = None
        if destriping or escOn:
            self.progress.report("passC", 0.0, "Pass C: finding bland pixels")
            bland = blandMask(work, self.wavelengths)
        if destriping:
            assert bland is not None
            self.progress.report("passC", 0.05, "Pass C: estimating the stripe frame")
            self.stripes = estimateStripes(
                work,
                workSigma,
                self.wavelengths,
                bland,
                splitHalf=bool(recipe.destripe.splitHalf.get()),
            )
        resampled = self._memmap("resampled", count)
        resampledSigma = self._memmap("resampledSigma", count)
        for start, stop in _chunks(self.lines, CHUNK_LINES):
            self.progress.report(
                "passC",
                0.1 + 0.4 * start / self.lines,
                f"Pass C: {'destripe and ' if destriping else ''}resampling, "
                f"lines {start + 1}–{stop}",
            )
            cube = np.asarray(work[start:stop], dtype=np.float64)
            sigma = np.asarray(workSigma[start:stop], dtype=np.float64)
            if self.stripes is not None:
                cube, sigma = applyStripes(cube, sigma, self.stripes)
            cube, sigma = resample(cube, sigma, weights)
            resampled[start:stop] = cube
            resampledSigma[start:stop] = sigma
            resampled.flush()
            resampledSigma.flush()
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
                    np.asarray(resampled[start:stop], dtype=np.float64),
                    np.asarray(resampledSigma[start:stop], dtype=np.float64),
                    self.esc,
                )
                resampled[start:stop] = cube
                resampledSigma[start:stop] = sigma
                resampled.flush()
                resampledSigma.flush()
                self.progress.check()
        self._bandList()
        self._recordPassC(destriping, escOn)
        sensor = self.recipe.output.sensorSpace.get() or not (
            self.recipe.projection.enabled.get()
        )
        if sensor:
            self._writeSensorSpace()

    def _escFrame(self, bland: np.ndarray) -> EscFrame:
        """ESC from the bland pixels' column spectra of the resampled cube,
        a block of columns at a time (columns are independent)."""
        target = self.vector.wavelengths
        spectra = np.full((self.samples, len(target)), np.nan)
        wavelengths = np.broadcast_to(target, spectra.shape)
        resampled = self.memmaps["resampled"]
        resampledSigma = self.memmaps["resampledSigma"]
        for first in range(0, self.samples, _COLUMN_BLOCK):
            columns = slice(first, min(first + _COLUMN_BLOCK, self.samples))
            cube = np.array(resampled[:, columns], dtype=np.float64)
            sigma = np.array(resampledSigma[:, columns], dtype=np.float64)
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
            finite = np.isfinite(self.memmaps["resampled"][start:stop])
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
        if self.stripes is not None:  # σ/f only: the frame's σ is systematic
            steps.append("RSC(frame)")
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
            assert self.grid is not None
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

    def _writeSpectral(
        self,
        name: str,
        grid: MapGrid | None,
        what: str,
        blocks: Callable[[np.memmap, float, float], _Pieces],
        span: tuple[float, float],
    ) -> None:
        """An I/F product, then its σ, one after the other: ``blocks`` turns a
        resampled work memmap into (first, block, whole lines?) pieces, written
        with ``write`` (lines) or ``writeBands`` (bands), reporting progress
        over its share of the stage's ``span`` (the first half for the I/F,
        the second for σ)."""
        low, high = span
        middle = 0.5 * (low + high)
        halves = [(low, middle), (middle, high)]
        lines = grid.height if grid is not None else self.lines
        samples = grid.width if grid is not None else self.samples
        bands = self._spectralBands()
        for key, product, description, withSigma in (
            ("resampled", name, what, True),
            (
                "resampledSigma",
                f"{name}_sigma",
                f"1-sigma ({SIGMA_KIND}) of {name}",
                False,
            ),
        ):
            with self._writer(
                product, lines, samples, bands, grid, description, withSigma
            ) as writer:
                # closing: a cancel or error ends the generator, and with it
                # its reference to the work memmap
                with contextlib.closing(
                    blocks(self.memmaps[key], *halves.pop(0))
                ) as pieces:
                    for first, block, byLines in pieces:
                        if byLines:
                            writer.write(first, block)
                        else:
                            writer.writeBands(first, block)
                        self.progress.check()

    def _writeSensorSpace(self) -> None:
        def blocks(source: np.memmap, low: float, high: float) -> _Pieces:
            for start, stop in _chunks(self.lines, CHUNK_LINES):
                self.progress.report(
                    "passC",
                    low + (high - low) * start / self.lines,
                    f"Pass C: writing the sensor-space product, lines {start + 1}–{stop}",
                )
                yield start, np.asarray(source[start:stop]), True

        self._writeSpectral(
            self.names["sensor"],
            None,
            "CRISM IR I/F on the MTRDR wavelength vector, sensor space",
            blocks,
            (0.8, 1.0),
        )

    # --- pass D --------------------------------------------------------------

    def _position(self, band: DdrBand) -> np.ndarray:
        """A DDR latitude or longitude, NaN on the slew lines: they hold no
        I/F, so they must not widen the grid or reach the map through the
        GLT (FRS00031442's 13 made its grid 756 × 903 for 702 × 295)."""
        values = self.ddr.band(band)
        values[~self.usable] = np.nan
        return values

    def _passD(self) -> None:
        projection = self.recipe.projection
        lat = self._position(DdrBand.LATITUDE)
        lon = self._position(DdrBand.LONGITUDE)
        pixelSize = projection.pixelSize.get() or PIXEL_SIZE_M * self.trr3.binning
        self.progress.report("passD", 0.0, "Pass D: the map grid")
        grid = self.grid = mtrdrGrid(
            lat, lon, pixelSize, bool(projection.snapToLattice.get())
        )
        glt = nearestNeighborGlt(grid, lat, lon, projection.maxDistanceFactor.get())
        del lat, lon
        area = (
            areaWeights(
                grid,
                self._position(DdrBand.LATITUDE),
                self._position(DdrBand.LONGITUDE),
            )
            if projection.resampling.get() is Resampling.AREA
            else None
        )
        count = len(self.vector.wavelengths)

        def blocks(source: np.memmap, low: float, high: float) -> _Pieces:
            if area is None:
                for start, stop in _chunks(grid.height, _MAP_ROWS):
                    self.progress.report(
                        "passD",
                        low + (high - low) * start / grid.height,
                        f"Pass D: projecting rows {start + 1}–{stop} of {grid.height}",
                    )
                    yield start, applyGlt(source, _rows(glt, start, stop)), True
                return
            sigma = self.memmaps["resampledSigma"]
            for first in range(0, count, _BAND_BLOCK):
                block = slice(first, min(first + _BAND_BLOCK, count))
                self.progress.report(
                    "passD",
                    low + (high - low) * first / count,
                    f"Pass D: area weighting bands {block.start + 1}–{block.stop}",
                )
                mean, meanSigma = applyAreaWeights(
                    source[:, :, block], sigma[:, :, block], area
                )
                yield first, (meanSigma if source is sigma else mean), False

        self._writeSpectral(
            self.names["map"],
            grid,
            "CRISM IR I/F on the MTRDR wavelength vector, map projected",
            blocks,
            (0.05, 0.8),
        )
        self.progress.report(
            "passD", 0.85, "Pass D: geometry and processing backplanes"
        )
        self._writeBackplanes(glt, area)
        self._recordProjection(grid, glt)

    def _writeBackplanes(self, glt: Glt, area: AreaWeights | None) -> None:
        """DE through the GLT and IN, in blocks of output rows."""
        assert self.grid is not None
        grid = self.grid
        ddr = np.stack([self.ddr.band(band) for band in DdrBand], axis=-1).astype(
            np.float32
        )
        with self._writer(
            self.names["geometry"],
            grid.height,
            grid.width,
            EnviBands(_bandNames(self.ddr), defaultBands=(1, 1, 1)),
            grid,
            "CRISM DDR backplanes, map projected (nearest neighbor)",
            withSigma=False,
        ) as writer:
            for start, stop in _chunks(grid.height, _MAP_ROWS):
                writer.write(start, applyGlt(ddr, _rows(glt, start, stop)))
                self.progress.check()
        del ddr
        # the bands derived from the data are no-data where the source pixel
        # has none (Review Focus 2); its geometry and position are still known
        hasData = np.where(self.footprint, 1.0, np.nan)
        planes = np.stack(
            [self.planes[name] for name in _PLANES]
            + [self.spikeCount * hasData, self.leaskCount * hasData, hasData],
            axis=-1,
        )
        names = IN_BAND_NAMES
        if area is not None:  # the counts are area-weighted means there
            names = tuple(_AREA_IN_NAMES.get(n, n) for n in IN_BAND_NAMES)
        with self._writer(
            self.names["info"],
            grid.height,
            grid.width,
            EnviBands(names, defaultBands=(1, 1, 1)),
            grid,
            "Varda CRISM processing backplanes (source sample and line are "
            "0-based and nearest neighbor; looks is the effective number)",
            withSigma=False,
        ) as writer:
            for start, stop in _chunks(grid.height, _MAP_ROWS):
                part = _rows(glt, start, stop)
                filled = part.sourceLine >= 0
                if area is None:
                    mapped = applyGlt(planes, part)
                    looks = mapped[..., -1]
                else:
                    rows = slice(start * grid.width, stop * grid.width)
                    weights = AreaWeights(area.matrix[rows], area.looks[start:stop])
                    mapped, _ = applyAreaWeights(
                        planes, np.zeros(planes.shape), weights
                    )
                    looks = np.where(
                        np.isfinite(mapped[..., -1]), area.looks[start:stop], np.nan
                    )
                info = np.concatenate(
                    [
                        np.where(filled, part.sourceSample, np.nan)[..., None],
                        np.where(filled, part.sourceLine, np.nan)[..., None],
                        mapped[..., :-1],
                        looks[..., None],
                    ],
                    axis=-1,
                )
                writer.write(start, info)
                self.progress.check()

    def _recordProjection(self, grid: MapGrid, glt: Glt) -> None:
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

    def _betaExcess(self) -> np.ndarray:
        """``(s, b)``: |ln T′| times each column's median excess of the
        empirical σ_β over the formal one, √max(σ²_emp − σ²_formal, 0): the
        β uncertainty the per-band σ does not carry (ln T′ includes a
        path-scaled artifact)."""
        if self.atmosphere is None:
            return np.zeros((self.samples, self.bands))
        model = self.atmosphere.model
        logT = shiftedLogTransmission(
            self.wavelengths, model.transmission.values, model.shiftNm
        )
        if model.artifact is not None and model.artifactScale is not None:
            logT = logT + model.artifactScale * model.artifact
        with np.errstate(invalid="ignore"):
            excess = np.sqrt(
                np.fmax(self.planes["betaSigma"] ** 2 - self.betaSigmaFormal**2, 0.0)
            )
        excess[~np.isfinite(self.planes["betaSigma"])] = np.nan
        return np.abs(logT) * _nanMedian(excess, axis=0)[:, None]

    def _systematic(self) -> dict[str, object]:
        """The systematic terms per output band (relative to I/F)."""
        assert self.weights is not None  # Pass C
        zeros = np.zeros((self.samples, self.bands))
        terms = {
            "scanChoice": self.scanChoice,
            "artifactAmplitude": self.artifactTerm,
            "atmosphereBetaExcess": self._betaExcess(),
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
            "times β times the basis; atmosphereBetaExcess: |ln T′| times the "
            "empirical σ_β's excess over the formal one; destripeFrame: the "
            "frame's bootstrap σ",
        }

    def _calibrationRecord(self) -> list[dict[str, object]]:
        """Every calibration file: ``required`` means needed for this
        processing (the WA, and the AT the atmosphere used), ``used`` that its
        data went in (also the bad-pixel maps found)."""
        chosen = (
            self.atmosphere.selection.chosen.sourceName.lower()
            if self.atmosphere is not None
            else None
        )
        records: list[dict[str, object]] = []
        seen: set[str] = set()

        def add(kind: str, name: str, path: Path | None, note: str) -> None:
            name = name.lower()
            seen.add(name)
            used = path is not None and (
                kind == "WA"
                or name == chosen
                or (kind == "BP" and self.badPixels is not None)
            )
            records.append(
                {
                    "kind": kind,
                    "name": name,
                    "path": path,
                    "sha256": sha256File(path) if path is not None else None,
                    "required": kind == "WA" or name == chosen,
                    "used": used,
                    "note": note,
                }
            )

        for entry in self.status:
            add(entry.kind, entry.name, entry.localPath, entry.note)
        for candidate in self._candidatesUsed:
            name = candidate.sourceName.lower()
            if name in seen:
                continue
            kind = "AT" if candidate.artifacts is None else "VS"
            add(
                kind,
                name,
                self.library.localFile(kind, name) if kind == "AT" else None,
                f"a candidate, {candidate.key}",
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
        if self.recipe.output.cog.get() and name in self.outputs:
            tif = self.staging / f"{name}.tif"
            writeCog(self.outputs[name], tif)
            self.outputs[f"{name}.tif"] = tif
        mapped = name in self.outputs
        history, sigmaSteps = self._history(mapped)
        provenance = {
            "vardaVersion": vardaVersion(),
            "software": _software(),
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


def _rows(glt: Glt, start: int, stop: int) -> Glt:
    """Output rows [start, stop) of a GLT."""
    return Glt(glt.sourceLine[start:stop], glt.sourceSample[start:stop])


def _productFiles(names: Mapping[str, str]) -> set[str]:
    """Every file name a run can write for these product names."""
    stems = [names[key] for key in ("map", "geometry", "info", "sensor")]
    stems += [f"{names['map']}_sigma", f"{names['sensor']}_sigma"]
    files = {f"{stem}{suffix}" for stem in stems for suffix in (".img", ".hdr")}
    return files | {f"{names['map']}.tif"} | {names["map"] + s for s in _SIDECARS}


def _publish(
    staged: ProcessResult,
    staging: Path,
    outputFolder: Path,
    names: Mapping[str, str],
    runId: str,
) -> ProcessResult:
    """Move every staged file into the output folder as one transaction.
    First every file an earlier run of this observation left (Varda's own
    names only: those this run writes, and those it no longer does) is
    renamed to ``<name>.old-<runId>``; then the staged files are moved in,
    images before their headers and the sidecars last. When any step fails
    (on Windows a product open in another program cannot be renamed), every
    step is undone, the earlier products are back under their names, and
    ``ProductInUseError`` names the file. On success the ``.old`` files are
    deleted."""

    def order(path: Path) -> tuple[bool, bool, str]:
        return (path.name.endswith(_SIDECARS), path.suffix == ".hdr", path.name)

    files = sorted((p for p in staging.iterdir() if p.is_file()), key=order)
    earlier = sorted(
        name
        for name in _productFiles(names) | {path.name for path in files}
        if (outputFolder / name).is_file()
    )
    backups: list[tuple[Path, Path]] = []  # (product, its .old name)
    placed: list[Path] = []
    current = outputFolder
    try:
        for name in earlier:
            current = outputFolder / name
            backup = outputFolder / f"{name}.old-{runId}"
            os.replace(current, backup)
            backups.append((current, backup))
        for path in files:
            current = outputFolder / path.name
            os.replace(path, current)
            placed.append(current)
    except OSError as error:
        _undoPublish(placed, backups)
        raise ProductInUseError(
            f"{current.name} in {outputFolder} is in use; close it and rerun ({error})"
        ) from error
    for _, backup in backups:
        try:
            backup.unlink()
        except OSError as error:
            _log.warning("Could not remove the earlier product %s: %s", backup, error)

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


def _undoPublish(placed: list[Path], backups: list[tuple[Path, Path]]) -> None:
    """Remove the files a failed publish moved in and put the earlier
    products back; what cannot be undone is logged (each step is tried)."""
    for path in placed:
        try:
            path.unlink()
        except OSError as error:
            _log.error("Could not remove %s after a failed publish: %s", path, error)
    for product, backup in reversed(backups):
        try:
            os.replace(backup, product)
        except OSError as error:
            _log.error("Could not restore %s from %s: %s", product, backup, error)


def _releaseFrames(error: BaseException) -> None:
    """Clear the locals of the frames ``error`` (and its causes) passed
    through, then collect: they hold work memmaps and views of them, and
    Windows cannot delete a mapped file. The frame still running is kept."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)
        pending += [
            e for e in (current.__cause__, current.__context__) if e is not None
        ]
    gc.collect()


def _removeWork(work: Path, succeeded: bool) -> None:
    """Remove the work folder; a failure is a warning, never the run's
    failure (``succeeded`` says how the run ended, for the message)."""
    try:
        shutil.rmtree(work)
    except FileNotFoundError:
        pass
    except OSError as error:
        ended = "succeeded" if succeeded else "failed"
        _log.warning(
            "The CRISM run %s, but its work folder %s could not be removed: %s",
            ended,
            work,
            error,
        )


def _removeStaleWork(outputFolder: Path) -> None:
    """Remove the work folders of runs that were killed, once they are
    ``STALE_WORK_SECONDS`` old (a newer one may belong to a run in
    progress); best effort."""
    now = time.time()
    for folder in outputFolder.glob(WORK_PREFIX + "*"):
        try:
            if folder.is_dir() and now - folder.stat().st_mtime > STALE_WORK_SECONDS:
                shutil.rmtree(folder)
        except OSError as error:
            _log.warning("Could not remove the old work folder %s: %s", folder, error)


@frozen(eq=False)
class ColumnResiduals:
    """What Pass A's transmission choice leaves in one observation's column
    spectra: the input of ``learnArtifactBasis`` (``fit.residuals``) and of
    its leave-one-out check. ``wavelengths``, ``spectra`` and ``spread`` are
    ``(s, b)``."""

    name: str
    sclk: float
    wavelengths: np.ndarray
    spectra: np.ndarray
    spread: np.ndarray
    selection: SelectionResult

    @property
    def transmissionKey(self) -> str:
        return self.selection.chosen.key

    @property
    def residuals(self) -> np.ndarray:
        return self.selection.fit.residuals


def columnResiduals(
    observation: CrismObservation, recipe: Recipe, library: CalibrationLibrary
) -> ColumnResiduals:
    """Pass A's pieces up to the transmission choice, nothing written: the
    calibration check, the noise model, the central lines (masked,
    photometric, despiked) and their column spectra fitted with the
    candidates (``recipe.atmosphere.scan`` forces one). Used to learn an
    artifact basis (``learnArtifactBasis``)."""
    with tempfile.TemporaryDirectory(prefix="varda-crism-") as folder:
        run = _Run(observation, recipe, Path(folder), library, _Progress(None, None))
        try:
            run._calibration()
            central = run._selectionLines()
            run.noise = estimateNoise(
                run._read(central.start, central.stop, None),
                wavelengths=run.bandWavelengths,
            )
            cube, sigma, _ = run._prepare(central.start, central.stop, tally=False)
            spectra, spread, _, selection = run._chooseTransmission(cube, sigma)
            return ColumnResiduals(
                observation.name,
                run.trr3.sclk,
                run.wavelengths,
                spectra,
                spread,
                selection,
            )
        finally:
            run.close()


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
    written: the products already in the folder stay as they were (also
    when one is open in another program: ``ProductInUseError``)."""
    outputFolder = Path(outputFolder)
    outputFolder.mkdir(parents=True, exist_ok=True)
    _removeStaleWork(outputFolder)
    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=outputFolder))
    (work / "out").mkdir()
    runId = work.name.removeprefix(WORK_PREFIX)
    run: _Run | None = None
    succeeded = False
    try:
        run = _Run(observation, recipe, work, library, _Progress(progress, cancelled))
        result = _publish(run.run(), work / "out", outputFolder, run.names, runId)
        succeeded = True
        return result
    except BaseException as error:
        _releaseFrames(error)
        raise
    finally:
        if run is not None:
            run.close()
        _removeWork(work, succeeded)
