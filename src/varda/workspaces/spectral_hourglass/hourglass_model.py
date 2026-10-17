"""State of a spectral hourglass run: prepare → MNF → PPI → endmembers →
identify → map.

Qt-free. Long computations are split in two so signals never fire from a
worker thread: ``xxxTask(...)`` returns a callable that computes from a
snapshot of the current state and returns a result object (run it in the
background), and ``applyXxx(result)`` stores it (call it on the GUI thread).

Every step's result is kept; re-running a step marks the steps that depend
on it STALE rather than discarding them, so the user can go back, change one
choice and see what else needs redoing. Endmember pixels are flat image
indices (row * width + col), so classes survive re-runs of earlier steps.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable, Sequence
from enum import Enum

import attrs
import numpy as np
from psygnal import Signal

from varda.analysis.endmembers import pixelPurityIndex, suggestGroups
from varda.analysis.iovf import DEFAULT_STOP_FRACTION, DEFAULT_WINDOW, iovfFilter
from varda.analysis.linear_algebra import (
    NoiseEstimate,
    TransformResult,
    flattenValid,
    mnf,
    noiseCovariance,
    suggestedComponents,
)
from varda.analysis.rasters import readCube
from varda.analysis.mapping import (
    DEFAULT_SAM_MAX_ANGLE,
    UNMIXING_METADATA_KEY,
    MapMethod,
    mapBands,
    mapRaster,
    unmixingDetail,
    unmixingMetadata,
)
from varda.analysis.unmixing import Constraint, UnmixingReport
from varda.analysis.transforms import TRANSFORM_METADATA_KEY, transformMetadata
from varda.common.entities import VardaRaster
from varda.common.provenance import makeProvenance, setProvenance
from varda.spectral_libraries.spectrum import (
    LibrarySpectrum,
    SpectrumOrigin,
    spectrumFromImage,
)

ProgressCallback = Callable[[int, str], None]

# Distinct, color-blind-friendly-ish class colors (Kelly / Tol mixes)
PALETTE = [
    "#e6194b",
    "#3cb44b",
    "#4363d8",
    "#f58231",
    "#911eb4",
    "#42d4f4",
    "#f032e6",
    "#bfef45",
    "#fabed4",
    "#469990",
    "#dcbeff",
    "#9a6324",
    "#fffac8",
    "#800000",
    "#aaffc3",
    "#808000",
    "#ffd8b1",
    "#000075",
]

DEFAULT_PPI_ITERATIONS = 10_000
DEFAULT_PPI_THRESHOLD = 2.5  # MNF units = noise sigmas
DEFAULT_MAX_CANDIDATES = 2_000
MNF_NOISE_ESTIMATE = NoiseEstimate.HORIZONTAL
# Suggested endmembers smaller than this are left out (single outliers)
MIN_SUGGESTED_PIXELS = 3
__all__ = ["DEFAULT_SAM_MAX_ANGLE", "HourglassModel", "MapMethod", "Step", "StepState"]


class Step(Enum):
    PREPARE = "Prepare"
    REDUCE = "Reduce (MNF)"
    PURITY = "Pixel Purity"
    ENDMEMBERS = "Endmembers"
    IDENTIFY = "Identify"
    MAP = "Map"


class StepState(Enum):
    NOT_RUN = "not run"
    DONE = "done"
    STALE = "needs re-running"


# Endmember edits remembered for undo
UNDO_LIMIT = 100


def _undoable(method):
    """Remember the endmembers before a top-level edit so ``undo`` can restore
    them (edits made inside another edit are part of it)."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if self._editDepth == 0:
            self._pushUndo()
        self._editDepth += 1
        try:
            return method(self, *args, **kwargs)
        finally:
            self._editDepth -= 1

    return wrapper


# Which computed steps each step's result depends on
_DEPENDS_ON: dict[Step, tuple[Step, ...]] = {
    Step.REDUCE: (Step.PREPARE,),
    Step.PURITY: (Step.REDUCE,),
}


@attrs.define
class HourglassSettings:
    wavelengthRange: tuple[float, float] | None = None  # keep bands inside (inclusive)
    useIovf: bool = False


@attrs.define(eq=False)
class Endmember:
    id: int
    name: str
    color: str  # "#rrggbb"
    pixels: np.ndarray = attrs.field(factory=lambda: np.empty(0, dtype=np.int64))
    identification: str = ""  # best library match, e.g. "Kaolinite (0.91)"


@attrs.frozen
class PreparedData:
    cube: np.ndarray  # float64 (rows, cols, bands), unusable pixels NaN
    valid: np.ndarray  # bool (rows, cols)
    wavelengths: np.ndarray | None  # band centers in the image's own unit, or None
    bandIndices: np.ndarray  # indices of the kept bands in the source image
    wavelengthsNm: np.ndarray | None = None  # the same centers in nm (for libraries)
    fwhmNm: np.ndarray | None = None  # their FWHM in nm, when the header gives it
    settings: HourglassSettings | None = None  # what Prepare ran with
    # what IOVF replaced (IovfResult.record()), or None when it didn't run
    iovf: dict | None = None


@attrs.frozen
class PpiResult:
    counts: np.ndarray  # int64, one per valid pixel (flattenValid order)
    iterations: int  # total skewers behind the counts, over every Run More
    threshold: float
    seed: int | None  # None: loaded from a workflow saved before seeds were kept
    components: int  # MNF components projected


class HourglassModel:
    sigStepChanged = Signal(object)  # Step
    sigEndmembersChanged = Signal()
    sigCandidatesChanged = Signal()

    def __init__(self, image: VardaRaster) -> None:
        self.image = image
        self.settings = HourglassSettings()
        self._states = {step: StepState.NOT_RUN for step in Step}
        self.prepared: PreparedData | None = None
        self.mnf: TransformResult | None = None
        self.components = 0
        self.ppi: PpiResult | None = None
        self.minCount = 1
        self.maxCandidates = DEFAULT_MAX_CANDIDATES
        self.endmembers: list[Endmember] = []
        self._nextId = 1
        self._validFlat: np.ndarray | None = None  # flat indices of valid pixels
        self._undo: list[tuple[int, list[Endmember]]] = []
        self._editDepth = 0

    # --- state ---------------------------------------------------------------

    def state(self, step: Step) -> StepState:
        return self._states[step]

    def _setState(self, step: Step, state: StepState) -> None:
        if self._states[step] is not state:
            self._states[step] = state
            self.sigStepChanged.emit(step)

    def _markDone(self, step: Step) -> None:
        self._setState(step, StepState.DONE)
        for downstream, upstream in _DEPENDS_ON.items():
            if step in upstream and self._states[downstream] is StepState.DONE:
                self._markStale(downstream)

    def _markStale(self, step: Step) -> None:
        self._setState(step, StepState.STALE)
        for downstream, upstream in _DEPENDS_ON.items():
            if step in upstream and self._states[downstream] is StepState.DONE:
                self._markStale(downstream)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.image.height, self.image.width)

    # --- 1. prepare ------------------------------------------------------------

    @property
    def cube(self) -> np.ndarray:
        return self._require(self.prepared, Step.PREPARE).cube

    @property
    def valid(self) -> np.ndarray:
        return self._require(self.prepared, Step.PREPARE).valid

    @property
    def wavelengths(self) -> np.ndarray | None:
        """Kept band centers in the image's own unit (labels, the window)."""
        return self._require(self.prepared, Step.PREPARE).wavelengths

    @property
    def fwhmNm(self) -> np.ndarray | None:
        """Kept bands' FWHM in nm, or None."""
        return self._require(self.prepared, Step.PREPARE).fwhmNm

    @property
    def wavelengthsNm(self) -> np.ndarray | None:
        """Kept band centers in nm, for comparing with spectral libraries."""
        return self._require(self.prepared, Step.PREPARE).wavelengthsNm

    def prepareTask(self) -> Callable[[ProgressCallback], PreparedData]:
        image = self.image
        settings = attrs.evolve(self.settings)

        def task(progress: ProgressCallback) -> PreparedData:
            progress(0, "reading image")
            bands = image.bandModel
            nm = bands.centers
            numeric = None if nm is None else bands.native(nm)  # the image's unit
            keep = ~bands.badBands  # bands marked bad in the header are left out
            if settings.wavelengthRange is not None and numeric is not None:
                lo, hi = settings.wavelengthRange
                keep &= (numeric >= lo) & (numeric <= hi)
            indices = np.flatnonzero(keep)
            if indices.size < 3:
                raise ValueError(
                    "Fewer than three usable bands fall inside the wavelength range."
                )
            cube, valid = readCube(image)
            cube = np.ascontiguousarray(cube[:, :, indices])
            iovf = None
            if settings.useIovf:
                result = iovfFilter(
                    cube,
                    progress=lambda p, m: progress(10 + int(0.85 * p), f"IOVF {m}"),
                )
                cube = result.filtered
                cube[~valid] = np.nan
                iovf = result.record()
            progress(100, "done")
            return PreparedData(
                cube=cube,
                valid=valid,
                wavelengths=None if numeric is None else numeric[indices],
                bandIndices=indices,
                wavelengthsNm=None if nm is None else nm[indices],
                fwhmNm=None if bands.fwhm is None else bands.fwhm[indices],
                settings=settings,
                iovf=iovf,
            )

        return task

    def applyPrepared(self, prepared: PreparedData) -> None:
        self.prepared = prepared
        self._validFlat = np.flatnonzero(prepared.valid.ravel())
        self._markDone(Step.PREPARE)

    # --- 2. reduce (MNF) ---------------------------------------------------------

    def mnfTask(self) -> Callable[[ProgressCallback], TransformResult]:
        prepared = self._require(self.prepared, Step.PREPARE)

        def task(progress: ProgressCallback) -> TransformResult:
            progress(0, "estimating noise")
            X, valid = flattenValid(prepared.cube, None)
            noise = noiseCovariance(prepared.cube, valid, MNF_NOISE_ESTIMATE)
            progress(30, "computing components")
            result = mnf(X, noise, X.shape[1])
            progress(100, "done")
            return result

        return task

    def applyMnf(self, result: TransformResult) -> None:
        self.mnf = result
        self.components = suggestedComponents(result.eigenvalues, "MNF")
        self._markDone(Step.REDUCE)

    def setComponents(self, count: int) -> None:
        result = self._require(self.mnf, Step.REDUCE)
        count = int(np.clip(count, 1, result.scores.shape[1]))
        if count != self.components:
            self.components = count
            if self._states[Step.PURITY] is StepState.DONE:
                self._markStale(Step.PURITY)
            self.sigStepChanged.emit(Step.REDUCE)

    def componentImage(self, index: int) -> np.ndarray:
        """(rows, cols) MNF component ``index`` (0-based), NaN where invalid."""
        result = self._require(self.mnf, Step.REDUCE)
        return self._toImage(result.scores[:, index])

    def _toImage(self, values: np.ndarray) -> np.ndarray:
        image = np.full(self.shape[0] * self.shape[1], np.nan)
        image[self._requireValidFlat()] = values
        return image.reshape(self.shape)

    def _requireValidFlat(self) -> np.ndarray:
        if self._validFlat is None:
            raise ValueError("Run Prepare first.")
        return self._validFlat

    # --- 3. pixel purity -----------------------------------------------------------

    @property
    def ppiIterations(self) -> int:
        return 0 if self.ppi is None else self.ppi.iterations

    def canRunMore(self, threshold: float, seed: int) -> bool:
        """Whether Run More can add skewers to the current counts: only while
        they are up to date and were made with this threshold and seed, so the
        seed and the total number of skewers still determine them."""
        ppi = self.ppi
        return (
            ppi is not None
            and self._states[Step.PURITY] is StepState.DONE
            and ppi.seed == seed
            and ppi.threshold == threshold
            and ppi.components == self.components
        )

    def ppiTask(
        self,
        iterations: int = DEFAULT_PPI_ITERATIONS,
        threshold: float = DEFAULT_PPI_THRESHOLD,
        *,
        seed: int = 0,
        resume: bool = False,
    ) -> Callable[[ProgressCallback], PpiResult]:
        """Count ``iterations`` skewers of ``seed``'s sequence; with ``resume``
        (Run More), the next ``iterations`` of that sequence onto the current
        counts."""
        result = self._require(self.mnf, Step.REDUCE)
        components = self.components
        X = result.scores[:, :components]
        if resume and not self.canRunMore(threshold, seed):
            raise ValueError(
                "The pixel purity settings changed since the last run: run PPI again."
            )
        previous = self.ppi if resume else None
        before = 0 if previous is None else previous.iterations

        def task(progress: ProgressCallback) -> PpiResult:
            counts = pixelPurityIndex(
                X,
                iterations,
                threshold,
                seed=seed,
                firstSkewer=before,
                start=None if previous is None else previous.counts,
                progress=progress,
            )
            return PpiResult(
                counts=counts,
                iterations=before + iterations,
                threshold=threshold,
                seed=seed,
                components=components,
            )

        return task

    def applyPpi(self, result: PpiResult) -> None:
        self.ppi = result
        self._markDone(Step.PURITY)
        self.sigCandidatesChanged.emit()

    def ppiImage(self) -> np.ndarray:
        ppi = self._require(self.ppi, Step.PURITY)
        return self._toImage(ppi.counts.astype(np.float64))

    def setCandidateRule(self, minCount: int, maxCandidates: int) -> None:
        self.minCount = max(1, int(minCount))
        self.maxCandidates = max(1, int(maxCandidates))
        self.sigCandidatesChanged.emit()

    def _candidateRows(self) -> np.ndarray:
        """Rows of the valid-pixel matrix that are candidates, highest PPI first."""
        ppi = self._require(self.ppi, Step.PURITY)
        rows = np.flatnonzero(ppi.counts >= self.minCount)
        order = np.argsort(-ppi.counts[rows], kind="stable")
        return rows[order][: self.maxCandidates]

    def candidatePixels(self) -> np.ndarray:
        """Flat image indices of the candidate pure pixels, highest PPI first."""
        return self._requireValidFlat()[self._candidateRows()]

    def ndPoints(self) -> np.ndarray:
        """MNF coordinates (candidates, components) of the candidate pixels."""
        result = self._require(self.mnf, Step.REDUCE)
        return result.scores[self._candidateRows(), : self.components]

    def mnfCoordinates(self, pixels: Sequence[int] | np.ndarray) -> np.ndarray:
        """MNF coordinates (pixels, components) of flat image indices (NaN for
        invalid pixels)."""
        result = self._require(self.mnf, Step.REDUCE)
        rows = self._validRows(np.asarray(pixels, dtype=np.int64))
        out = np.full((rows.size, self.components), np.nan)
        ok = rows >= 0
        out[ok] = result.scores[rows[ok], : self.components]
        return out

    def _validRows(self, pixels: np.ndarray) -> np.ndarray:
        """Row in the valid-pixel matrix of each flat index (-1 if invalid)."""
        validFlat = self._requireValidFlat()
        pos = np.searchsorted(validFlat, pixels)
        pos = np.clip(pos, 0, max(validFlat.size - 1, 0))
        found = validFlat.size > 0
        return np.where(found & (validFlat[pos] == pixels), pos, -1)

    def backgroundSample(self, count: int = 5_000, seed: int = 0) -> np.ndarray:
        """MNF coordinates of a random sample of all valid pixels, for context
        behind the candidates in the n-D view."""
        result = self._require(self.mnf, Step.REDUCE)
        n = result.scores.shape[0]
        rows = np.random.default_rng(seed).choice(n, size=min(count, n), replace=False)
        return result.scores[np.sort(rows), : self.components]

    # --- 4. endmembers ---------------------------------------------------------------

    def endmember(self, endmemberId: int) -> Endmember:
        for endmember in self.endmembers:
            if endmember.id == endmemberId:
                return endmember
        raise KeyError(endmemberId)

    @_undoable
    def addEndmember(
        self, name: str | None = None, color: str | None = None
    ) -> Endmember:
        used = {e.color for e in self.endmembers}
        if color is None:
            color = next(
                (c for c in PALETTE if c not in used), PALETTE[len(used) % len(PALETTE)]
            )
        endmember = Endmember(
            id=self._nextId, name=name or f"Endmember {self._nextId}", color=color
        )
        self._nextId += 1
        self.endmembers.append(endmember)
        self._endmembersChanged()
        return endmember

    @_undoable
    def removeEndmember(self, endmemberId: int) -> None:
        self.endmembers = [e for e in self.endmembers if e.id != endmemberId]
        self._endmembersChanged()

    @_undoable
    def renameEndmember(self, endmemberId: int, name: str) -> None:
        self.endmember(endmemberId).name = (
            name.strip() or self.endmember(endmemberId).name
        )
        self._endmembersChanged()

    @_undoable
    def setIdentification(self, endmemberId: int, text: str) -> None:
        self.endmember(endmemberId).identification = text
        self._endmembersChanged()

    @_undoable
    def recolorEndmember(self, endmemberId: int, color: str) -> None:
        self.endmember(endmemberId).color = color
        self._endmembersChanged()

    @_undoable
    def assignPixels(
        self, endmemberId: int, pixels: Iterable[int], *, add: bool = True
    ) -> None:
        """Add ``pixels`` (flat indices) to an endmember, taking them from any
        other; or with ``add=False`` remove them from it."""
        chosen = np.unique(np.asarray(list(pixels), dtype=np.int64))
        target = self.endmember(endmemberId)
        if add:
            for other in self.endmembers:
                if other is not target:
                    other.pixels = np.setdiff1d(other.pixels, chosen)
            target.pixels = np.union1d(target.pixels, chosen)
        else:
            target.pixels = np.setdiff1d(target.pixels, chosen)
        self._endmembersChanged()

    @_undoable
    def mergeEndmembers(self, endmemberIds: Sequence[int]) -> Endmember:
        """Merge into the first of ``endmemberIds`` (keeping its name and color)."""
        keep = self.endmember(endmemberIds[0])
        for other in [self.endmember(i) for i in endmemberIds[1:]]:
            keep.pixels = np.union1d(keep.pixels, other.pixels)
            self.endmembers.remove(other)
        self._endmembersChanged()
        return keep

    @_undoable
    def suggestEndmembers(self, count: int) -> list[Endmember]:
        """Up to ``count`` new endmembers, one per corner of the candidate cloud
        in MNF space, each holding the candidates close to its corner (mixtures
        stay unassigned; corners with fewer than three pixels, usually lone
        outliers, are skipped). A starting point to refine in the n-D view."""
        pixels = self.candidatePixels()
        labels = suggestGroups(self.ndPoints(), count)
        created = []
        with self.sigEndmembersChanged.blocked():
            for label in range(count):
                members = pixels[labels == label]
                if members.size >= MIN_SUGGESTED_PIXELS:
                    endmember = self.addEndmember()
                    self.assignPixels(endmember.id, members)
                    created.append(endmember)
        self._endmembersChanged()
        return created

    def endmemberSpectrum(self, endmemberId: int) -> np.ndarray:
        """Mean prepared spectrum of an endmember's valid pixels (NaN if none)."""
        cube = self.cube
        pixels = self.endmember(endmemberId).pixels
        flat = cube.reshape(-1, cube.shape[2])[pixels]
        flat = flat[np.all(np.isfinite(flat), axis=1)]
        if flat.size == 0:
            return np.full(cube.shape[2], np.nan)
        return flat.mean(axis=0)

    def endmemberStatistics(
        self, endmemberId: int
    ) -> tuple[np.ndarray, np.ndarray, int]:
        """Mean and standard deviation (population, ddof 0) of an endmember's
        valid prepared spectra, and how many pixels are behind them."""
        cube = self.cube
        pixels = self.endmember(endmemberId).pixels
        flat = cube.reshape(-1, cube.shape[2])[pixels]
        flat = flat[np.all(np.isfinite(flat), axis=1)]
        if flat.size == 0:
            missing = np.full(cube.shape[2], np.nan)
            return missing, missing.copy(), 0
        return flat.mean(axis=0), flat.std(axis=0), int(flat.shape[0])

    def endmemberLibrarySpectra(
        self, endmemberIds: Sequence[int]
    ) -> list[LibrarySpectrum]:
        """The endmembers' mean spectra (± 1 σ) as library spectra, recording
        the image, the pixels behind each and how the spectra were prepared.
        Empty when the bands have no wavelengths."""
        prepared = self._require(self.prepared, Step.PREPARE)
        if prepared.wavelengthsNm is None:
            return []
        note = "Spectral Hourglass endmember: mean of its pixels' prepared spectra"
        settings = prepared.settings
        if settings is not None and settings.wavelengthRange is not None:
            lo, hi = settings.wavelengthRange
            note += f"; bands kept {lo:g}–{hi:g} (image units)"
        if settings is not None and settings.useIovf:
            note += "; IOVF-filtered"
        image = self.image
        spectra = []
        for endmemberId in endmemberIds:
            endmember = self.endmember(endmemberId)
            mean, std, count = self.endmemberStatistics(endmemberId)
            if count == 0:
                continue
            origin = SpectrumOrigin(
                image.name,
                image.filePath or "",
                f"endmember “{endmember.name}” (Spectral Hourglass)",
                count,
                note,
            )
            spectra.append(
                spectrumFromImage(
                    endmember.name, prepared.wavelengthsNm, mean, origin, std=std
                )
            )
        return spectra

    def _pushUndo(self) -> None:
        snapshot = [attrs.evolve(e, pixels=e.pixels.copy()) for e in self.endmembers]
        self._undo.append((self._nextId, snapshot))
        del self._undo[:-UNDO_LIMIT]

    def canUndo(self) -> bool:
        return bool(self._undo)

    def undo(self) -> bool:
        """Restore the endmembers as they were before the last edit."""
        if not self._undo:
            return False
        self._nextId, self.endmembers = self._undo.pop()
        self._endmembersChanged()
        return True

    def usableEndmembers(self) -> list[Endmember]:
        return [e for e in self.endmembers if e.pixels.size]

    def _endmembersChanged(self) -> None:
        state = StepState.DONE if self.usableEndmembers() else StepState.NOT_RUN
        self._setState(Step.ENDMEMBERS, state)
        self.sigEndmembersChanged.emit()

    def endmembersToJson(self) -> dict:
        return {
            "image": self.image.name,
            "shape": list(self.shape),
            "endmembers": [
                {
                    "name": e.name,
                    "color": e.color,
                    "identification": e.identification,
                    "pixels": e.pixels.tolist(),
                }
                for e in self.endmembers
            ],
        }

    @_undoable
    def endmembersFromJson(self, data: dict) -> None:
        """Replace the endmembers with those saved by ``endmembersToJson``."""
        if list(data.get("shape", self.shape)) != list(self.shape):
            raise ValueError(
                "Those endmembers were picked on an image of a different size."
            )
        self.endmembers = []
        with self.sigEndmembersChanged.blocked():
            for entry in data["endmembers"]:
                endmember = self.addEndmember(entry["name"], entry["color"])
                endmember.pixels = np.asarray(entry["pixels"], dtype=np.int64)
                endmember.identification = entry.get("identification", "")
        self._endmembersChanged()

    # --- starting over -----------------------------------------------------------------

    def resetFrom(self, step: Step) -> None:
        """Clear ``step``'s result and every later one, as if starting the run
        again from there. Endmember classes (pixel sets) survive a restart of
        an earlier step; starting over from Endmembers clears them (undoably),
        and from Identify clears only the identifications."""
        order = list(Step)
        later = order[order.index(step) :]
        if Step.PREPARE in later:
            self.prepared = None
            self._validFlat = None
        if Step.REDUCE in later:
            self.mnf = None
            self.components = 0
        if Step.PURITY in later:
            self.ppi = None
            self.sigCandidatesChanged.emit()
        if step is Step.ENDMEMBERS:
            self._clearEndmembers()
        elif step is Step.IDENTIFY:
            self._clearIdentifications()
        for s in later:
            if s is not Step.ENDMEMBERS or step is Step.ENDMEMBERS:
                self._setState(s, StepState.NOT_RUN)
        if self.usableEndmembers():
            self._setState(Step.ENDMEMBERS, StepState.DONE)

    @_undoable
    def _clearEndmembers(self) -> None:
        self.endmembers = []
        self._endmembersChanged()

    @_undoable
    def _clearIdentifications(self) -> None:
        for endmember in self.endmembers:
            endmember.identification = ""
        self._endmembersChanged()

    def ppiToJson(self) -> dict | None:
        """The purity counts, compactly (non-zero rows only), for saving."""
        if self.ppi is None:
            return None
        rows = np.flatnonzero(self.ppi.counts)
        return {
            "validPixels": int(self.ppi.counts.size),
            "rows": rows.tolist(),
            "counts": self.ppi.counts[rows].tolist(),
            "totalIterations": self.ppi.iterations,
            "threshold": self.ppi.threshold,
            "seed": self.ppi.seed,
            "components": self.ppi.components,
        }

    def ppiFromJson(self, data: dict) -> None:
        """Restore saved purity counts (needs Prepare and MNF run on the same
        image and settings, so the valid pixels line up)."""
        n = int(data["validPixels"])
        if self._validFlat is None or self._validFlat.size != n:
            raise ValueError(
                "The saved pixel purity was computed on different valid pixels."
            )
        counts = np.zeros(n, dtype=np.int64)
        counts[np.asarray(data["rows"], dtype=np.int64)] = np.asarray(
            data["counts"], dtype=np.int64
        )
        self.applyPpi(
            PpiResult(
                counts=counts,
                # older workflows: "iterations", with no seed (it was random)
                iterations=int(data.get("totalIterations", data.get("iterations", 0))),
                threshold=float(data["threshold"]),
                seed=None if data.get("seed") is None else int(data["seed"]),
                components=int(data.get("components", self.components)),
            )
        )

    # --- 5. identify -------------------------------------------------------------------

    def markIdentified(self) -> None:
        self._setState(Step.IDENTIFY, StepState.DONE)

    # --- 6. map ------------------------------------------------------------------------

    def mapTask(
        self,
        method: MapMethod,
        *,
        maxAngle: float = DEFAULT_SAM_MAX_ANGLE,
        constraint: Constraint = Constraint.FULLY_CONSTRAINED,
    ) -> Callable[[ProgressCallback], VardaRaster]:
        endmembers = self.usableEndmembers()
        if len(endmembers) < 2:
            raise ValueError("Mapping needs at least two endmembers with pixels.")
        prepared = self._require(self.prepared, Step.PREPARE)
        names = [e.name for e in endmembers]
        colors = [e.color for e in endmembers]
        spectra = np.stack([self.endmemberSpectrum(e.id) for e in endmembers])
        validFlat = self._requireValidFlat()
        if method is MapMethod.MTMF:
            mnfResult = self._require(self.mnf, Step.REDUCE)
            k = self.components
            mnfData = mnfResult.scores[:, :k]
            targets = np.stack(
                [np.nanmean(self.mnfCoordinates(e.pixels), axis=0) for e in endmembers]
            )
        image = self.image
        settings: dict[str, object] = {"method": method.name}
        if method is MapMethod.SAM:
            settings["maxAngle"] = maxAngle  # radians
        elif method is MapMethod.MTMF:
            settings["components"] = self.components
        else:
            settings["constraint"] = constraint.name
        record = self.provenance("Map", {"map": settings})

        def task(progress: ProgressCallback) -> VardaRaster:
            progress(0, "mapping")
            metadata: dict = {}
            if method is MapMethod.MTMF:
                bands, bandNames = mapBands(
                    method, names, mnfData=mnfData, targets=targets
                )
                detail = f"{k} MNF components"
            else:
                X = prepared.cube.reshape(-1, prepared.cube.shape[2])[validFlat]
                report = UnmixingReport()
                bands, bandNames = mapBands(
                    method,
                    names,
                    X,
                    spectra,
                    maxAngle=maxAngle,
                    constraint=constraint,
                    progress=lambda p, m: progress(int(0.9 * p), m),
                    report=report,
                )
                if method is MapMethod.SAM:
                    detail = f"max angle {maxAngle:g} rad"
                else:
                    detail = unmixingDetail(constraint, report)
                    metadata[UNMIXING_METADATA_KEY] = unmixingMetadata(
                        constraint, report
                    )
            progress(90, "building image")
            result = mapRaster(
                image,
                validFlat,
                bands,
                bandNames,
                method=method,
                names=names,
                colors=colors,
                detail=detail,
                metadata=metadata,
            )
            setProvenance(result, record)
            progress(100, "done")
            return result

        return task

    def applyMap(self, result: VardaRaster) -> VardaRaster:
        self._setState(Step.MAP, StepState.DONE)
        return result

    # --- provenance ----------------------------------------------------------------------

    def runSettings(self) -> dict:
        """The settings of every step run so far, and the endmembers, as plain
        data for a result's provenance."""
        found: dict = {}
        if self.prepared is not None:
            settings = self.prepared.settings or self.settings
            found["prepare"] = {
                "wavelengthRange": settings.wavelengthRange,
                "wavelengthUnits": self.image.wavelengthUnits,
                "bandsKept": self.prepared.bandIndices,
                "useIovf": settings.useIovf,
                "iovf": {
                    "window": DEFAULT_WINDOW,
                    "stopFraction": DEFAULT_STOP_FRACTION,
                    **(self.prepared.iovf or {}),
                }
                if settings.useIovf
                else None,
            }
        if self.mnf is not None:
            found["mnf"] = {
                "noiseEstimate": MNF_NOISE_ESTIMATE.name,
                "componentsComputed": self.mnf.scores.shape[1],
                "componentsKept": self.components,
            }
        if self.ppi is not None:
            found["ppi"] = {
                "iterations": self.ppi.iterations,
                "threshold": self.ppi.threshold,
                "seed": getattr(self.ppi, "seed", None),  # None: not recorded
                "minCount": self.minCount,
                "maxCandidates": self.maxCandidates,
            }
        found["endmembers"] = [
            {
                "name": e.name,
                "identification": e.identification,
                "pixels": e.pixels,  # flat indices: row * width + column
            }
            for e in self.usableEndmembers()
        ]
        return found

    def provenance(self, output: str, extra: dict | None = None) -> dict:
        """A provenance record for one of the run's outputs (MNF, PPI, Map)."""
        return makeProvenance(
            f"Spectral Hourglass {output}",
            {**self.runSettings(), **(extra or {})},
            [self.image],
            processId=f"spectral_hourglass_{output.lower()}",
        )

    def mnfMetadata(self) -> dict:
        """What Eigenvalue Plot and Inverse Transform need for the kept MNF
        components, as the Processing menu's MNF gives them."""
        result = self._require(self.mnf, Step.REDUCE)
        prepared = self._require(self.prepared, Step.PREPARE)
        info = transformMetadata(result, self.image, "MNF", prepared.bandIndices)
        k = self.components
        for key in ("inverse", "eigenvalues", "explainedVariance"):
            info[key] = info[key][:k]
        info["noiseEstimate"] = MNF_NOISE_ESTIMATE.name
        info["noiseRoiClasses"] = []
        return {
            "explainedVariance": info["explainedVariance"],
            TRANSFORM_METADATA_KEY: info,
        }

    # --- helpers -------------------------------------------------------------------------

    @staticmethod
    def _require(value, step: Step):
        if value is None:
            raise ValueError(f"Run the {step.value} step first.")
        return value
