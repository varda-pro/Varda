"""A mosaic's recipe: its inputs in priority order (the first is on top),
each with a correction (a transform of its map coordinates), and the tie
points the corrections are fitted to. Nothing is resampled until rendering,
so every choice stays editable."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from attrs import define, field, frozen

from varda.common.entities import VardaRaster
from varda.geo.describe import describeCrs
from varda.geo.equivalence import sameCrs
from varda.mosaicking.recipe import (
    Blend,
    BlendMethod,
    Correction,
    LinearMode,
    RadiometryMethod,
    RadiometryQuality,
    Seam,
    SeamMethod,
)
from varda.mosaicking.transforms import (
    WARPS,
    TransformModel,
    Warp,
    applyMatrix,
    fitTransform,
    fitWarp,
    ransacFit,
    robustWarpFit,
    translation,
)

# A pixel with data in fewer of its good bands than this is empty (as analyses
# treat it: analysis.rasters.MIN_VALID_BAND_FRACTION)
MIN_VALID_BAND_FRACTION = 0.5
VALID_CHUNK = 32  # bands read at a time when finding the pixels with data
DEFAULT_INLIER_PX = 1.0  # RANSAC's inlier threshold, in the moving image's pixels


@define(eq=False)
class MosaicInput:
    image: VardaRaster
    key: int  # stable across reordering; tie points refer to it
    # The correction, map → corrected map: the warp (if any), then the matrix
    correction: np.ndarray = field(factory=lambda: np.eye(3))
    model: TransformModel = TransformModel.TRANSLATION
    visible: bool = True
    warp: Warp | None = None  # a polynomial or thin-plate spline
    radiometry: Correction | None = None  # applied to its values when rendering
    _valid: np.ndarray | None = field(default=None, init=False, repr=False)

    def validMask(self) -> np.ndarray:
        """(rows, cols): the pixels with data. As for analyses, a pixel counts
        when at least half its good bands have data (one missing band, from
        a bad detector column say, doesn't empty it). Read once, a few bands
        at a time, and from every band, so a preview of a few bands agrees
        with the full export."""
        if self._valid is None:
            image = self.image
            bad = image.bandModel.badBands
            good = (
                np.flatnonzero(~bad)
                if bad.size and not bad.all()
                else np.arange(image.bandCount)
            )
            count = np.zeros((image.height, image.width), np.int32)
            for start in range(0, len(good), VALID_CHUNK):
                bands = [int(b) for b in good[start : start + VALID_CHUNK]]
                data = np.ma.filled(
                    np.ma.asarray(image.getData(bandIndices=bands), dtype=np.float32),
                    np.nan,
                )
                finite = np.isfinite(data)
                if image.nodata is not None:
                    finite &= data != image.nodata
                count += finite.sum(axis=2)
            self._valid = count >= max(
                1, math.ceil(MIN_VALID_BAND_FRACTION * len(good))
            )
        return self._valid

    def correct(self, points: np.ndarray) -> np.ndarray:
        """Raw map coordinates to corrected ones."""
        points = np.atleast_2d(np.asarray(points, float))
        if self.warp is not None:
            points = self.warp.forward(points)
        return applyMatrix(self.correction, points)

    def uncorrect(self, points: np.ndarray) -> np.ndarray:
        """Corrected map coordinates back to raw ones."""
        points = applyMatrix(np.linalg.inv(self.correction), np.atleast_2d(points))
        return self.warp.inverse(points) if self.warp is not None else points

    def toMap(self, pixels: np.ndarray, corrected: bool = True) -> np.ndarray:
        """Pixel (col, row) coordinates to map coordinates, corrected or not."""
        pixels = np.atleast_2d(np.asarray(pixels, float))
        x, y = self.image.transform * (pixels[:, 0], pixels[:, 1])
        mapped = np.column_stack([x, y])
        return self.correct(mapped) if corrected else mapped

    def fromMap(self, points: np.ndarray, corrected: bool = True) -> np.ndarray:
        """Map coordinates (corrected or not) back to pixel (col, row)."""
        points = np.atleast_2d(np.asarray(points, float))
        if corrected:
            points = self.uncorrect(points)
        cols, rows = ~self.image.transform * (points[:, 0], points[:, 1])
        return np.column_stack([cols, rows])

    @property
    def pixelSize(self) -> float:
        transform = self.image.transform
        return float(np.hypot(transform.a, transform.d))

    def footprint(self) -> np.ndarray:
        """The corrected outline of the image, starting at its top-left corner:
        its four corners, or points along the edges when a warp bends them."""
        w, h = self.image.width, self.image.height
        if self.warp is None:
            return self.toMap(np.array([[0, 0], [w, 0], [w, h], [0, h]]))
        t = np.linspace(0, 1, 16, endpoint=False)
        edges = np.concatenate(
            [
                np.column_stack([t * w, np.zeros_like(t)]),
                np.column_stack([np.full_like(t, w), t * h]),
                np.column_stack([(1 - t) * w, np.full_like(t, h)]),
                np.column_stack([np.zeros_like(t), (1 - t) * h]),
            ]
        )
        return self.toMap(edges)


@define(eq=False)
class TiePoint:
    """A point on the moving image and the same feature on the reference."""

    movingKey: int
    movingPixel: tuple[float, float]
    referenceKey: int
    referencePixel: tuple[float, float]
    enabled: bool = True
    residual: float = float("nan")  # map units, after the last fit
    auto: bool = False  # found by matching rather than placed by hand
    quality: float = float("nan")  # the match's score (0-1), for automatic points
    band: int | None = None  # the band it was matched on
    inlier: bool = True  # whether the last (robust) fit kept it
    # The phase-plane fit's SNR (0-1, Leprince et al. 2007): NaN unless the
    # point was refined by it
    phaseSnr: float = float("nan")


@frozen
class BandFit:
    """How one band's automatic tie points agreed with the last fit."""

    count: int
    inliers: int
    rms: float  # the inliers' residual, in the moving image's pixels
    phaseSnr: float = float("nan")  # the points' median phase-plane SNR (0-1)


class MosaicError(ValueError):
    """The inputs can't be mosaicked as they are; the message is for the user."""


class MosaicProject:
    def __init__(self, images: Sequence[VardaRaster]):
        if len(images) < 2:
            raise MosaicError("A mosaic needs at least two images.")
        first = images[0].crs
        firstWkt = None if first is None else first.to_wkt()
        for image in images[1:]:
            wkt = None if image.crs is None else image.crs.to_wkt()
            if not sameCrs(firstWkt, wkt):
                raise MosaicError(
                    "The images are in different map projections ("
                    f"{describeCrs(firstWkt).text}; {describeCrs(wkt).text}); "
                    "reproject them to one first (Varda doesn't yet reproject "
                    "while mosaicking)."
                )
        bands = {image.bandCount for image in images}
        if len(bands) > 1:
            raise MosaicError(
                "The images have different numbers of bands ("
                + ", ".join(str(b) for b in sorted(bands))
                + "); a mosaic needs the same bands in every image."
            )
        self.inputs = [MosaicInput(image, key) for key, image in enumerate(images)]
        self.tiePoints: list[TiePoint] = []
        # How the radiometric corrections were made (for labels and provenance)
        self.radiometryMethod = RadiometryMethod.NONE
        self.radiometryMode = LinearMode.BOTH
        self.heldKeys: frozenset[int] = frozenset()
        # How well the corrections fit (None: none, or not assessed)
        self.radiometryQuality: RadiometryQuality | None = None
        self.seamMethod = SeamMethod.PRIORITY
        self.seams: list[Seam] = []
        self.blend = Blend()

    # --- inputs ------------------------------------------------------------

    def input(self, key: int) -> MosaicInput:
        return next(i for i in self.inputs if i.key == key)

    def move(self, key: int, steps: int) -> None:
        """Move an input up (negative) or down the priority order."""
        item = self.input(key)
        index = self.inputs.index(item)
        target = int(np.clip(index + steps, 0, len(self.inputs) - 1))
        self.inputs.insert(target, self.inputs.pop(index))

    def nudge(self, key: int, dx: float, dy: float) -> None:
        """Shift an input by (dx, dy) map units."""
        item = self.input(key)
        item.correction = translation(dx, dy) @ item.correction

    def resetAlignment(self, key: int) -> None:
        item = self.input(key)
        item.correction = np.eye(3)
        item.warp = None

    # --- radiometry ------------------------------------------------------------

    def applyRadiometry(self, result) -> None:
        """Use a ``radiometry.balance`` result: its corrections (inputs it
        left out get none) and how they were made."""
        for item in self.inputs:
            item.radiometry = result.corrections.get(item.key)
        self.radiometryMethod = result.method
        self.radiometryMode = result.mode
        self.heldKeys = frozenset(result.held)
        self.radiometryQuality = result.quality

    def clearRadiometry(self) -> None:
        for item in self.inputs:
            item.radiometry = None
        self.radiometryMethod = RadiometryMethod.NONE
        self.heldKeys = frozenset()
        self.radiometryQuality = None

    @property
    def isVisual(self) -> bool:
        """Whether the recipe changes spectral shape (a visual product)."""
        return (
            self.radiometryMethod is RadiometryMethod.HISTOGRAM
            or self.blend.method is not BlendMethod.NONE
        )

    # --- seams -------------------------------------------------------------------

    def setSeams(self, method: SeamMethod, seams: Sequence[Seam]) -> None:
        self.seamMethod = method
        self.seams = list(seams)

    def clearSeams(self) -> None:
        self.setSeams(SeamMethod.PRIORITY, [])

    def setBlend(
        self, method: BlendMethod, width: int | None = None, levels: int | None = None
    ) -> None:
        self.blend = Blend(
            method,
            self.blend.width if width is None else int(width),
            self.blend.levels if levels is None else int(levels),
        )

    # --- tie points ----------------------------------------------------------

    def addTiePoint(
        self,
        movingKey: int,
        movingPixel,
        referenceKey: int,
        referencePixel,
        *,
        auto: bool = False,
        quality: float = float("nan"),
        band: int | None = None,
        phaseSnr: float = float("nan"),
    ) -> TiePoint:
        if movingKey == referenceKey:
            raise MosaicError("A tie point joins two different images.")
        point = TiePoint(
            movingKey,
            (float(movingPixel[0]), float(movingPixel[1])),
            referenceKey,
            (float(referencePixel[0]), float(referencePixel[1])),
            auto=auto,
            quality=float(quality),
            band=band,
            phaseSnr=float(phaseSnr),
        )
        self.tiePoints.append(point)
        return point

    def removeAutoTiePoints(self, movingKey: int) -> None:
        """Drop the automatic tie points of an image (before matching again)."""
        self.tiePoints = [
            p for p in self.tiePoints if not (p.auto and p.movingKey == movingKey)
        ]

    def tiePointsOf(self, key: int) -> list[TiePoint]:
        return [p for p in self.tiePoints if p.movingKey == key]

    def fit(self, key: int, threshold: float = DEFAULT_INLIER_PX) -> np.ndarray:
        """Fit the input's correction to its enabled tie points (its pixels
        onto the references' current positions); returns the residuals (map
        units). With automatic points, the fit is robust: points off by more
        than ``threshold`` pixels are left out (manual points never are)."""
        moving = self.input(key)
        points = [p for p in self.tiePointsOf(key) if p.enabled]
        src = moving.toMap(
            np.array([p.movingPixel for p in points]).reshape(-1, 2), corrected=False
        )
        dst = np.array(
            [
                self.input(p.referenceKey).toMap(np.array([p.referencePixel]))[0]
                for p in points
            ]
        ).reshape(-1, 2)
        manual = np.array([not p.auto for p in points], bool)
        limit = threshold * moving.pixelSize
        if moving.model in WARPS:
            if manual.all():
                warp, residuals = fitWarp(moving.model, src, dst)
                inliers = np.ones(len(points), bool)
            else:
                warp, residuals, inliers = robustWarpFit(
                    moving.model, src, dst, limit, forced=manual
                )
            moving.warp, moving.correction = warp, np.eye(3)
        else:
            if manual.all():
                matrix, residuals = fitTransform(moving.model, src, dst)
                inliers = np.ones(len(points), bool)
            else:
                matrix, residuals, inliers = ransacFit(
                    moving.model, src, dst, limit, forced=manual
                )
            moving.warp, moving.correction = None, matrix
        for point, residual, inlier in zip(points, residuals, inliers, strict=True):
            point.residual = float(residual)
            point.inlier = bool(inlier)
        return residuals

    def bandReport(self, key: int) -> dict[int, BandFit]:
        """For each band an image's automatic tie points came from: how many
        there are, how many the last fit kept, their RMS residual, and their
        median phase-plane SNR (NaN when none was refined)."""
        size = self.input(key).pixelSize
        report = {}
        auto = [p for p in self.tiePointsOf(key) if p.auto and p.enabled]
        for band in sorted({p.band for p in auto if p.band is not None}):
            points = [p for p in auto if p.band == band]
            kept = np.array([p.residual for p in points if p.inlier], float)
            rms = float(np.sqrt(np.mean(kept**2))) / size if kept.size else float("nan")
            snr = np.array([p.phaseSnr for p in points], float)
            snr = snr[np.isfinite(snr)]
            median = float(np.median(snr)) if snr.size else float("nan")
            report[band] = BandFit(len(points), int(kept.size), rms, median)
        return report

    def alignmentSummary(self) -> list[dict]:
        """Each input's alignment quality, in priority order (an input's
        position is its class in the source map): its transform, enabled
        tie points, how many the last fit kept, their RMS residual (moving
        image's pixels) and median phase-plane SNR (None when there's none)."""
        summary = []
        for position, item in enumerate(self.inputs):
            points = [p for p in self.tiePointsOf(item.key) if p.enabled]
            kept = np.array(
                [p.residual for p in points if p.inlier and np.isfinite(p.residual)]
            )
            snr = np.array([p.phaseSnr for p in points], float)
            snr = snr[np.isfinite(snr)]
            summary.append(
                {
                    "class": position,
                    "image": item.image.name,
                    "model": item.model.name,
                    "tiePoints": len(points),
                    "kept": int(kept.size),
                    "rmsPixels": float(np.sqrt(np.mean(kept**2))) / item.pixelSize
                    if kept.size
                    else None,
                    "medianPhaseSnr": float(np.median(snr)) if snr.size else None,
                }
            )
        return summary

    # --- saving ----------------------------------------------------------------

    def state(self) -> dict:
        """The recipe as plain data; images are referred to by position in the
        list the project was made from."""
        order = sorted(self.inputs, key=lambda i: i.key)
        return {
            "order": [i.key for i in self.inputs],
            "blend": self.blend.state(),
            "seams": {
                "method": self.seamMethod.name,
                "seams": [seam.state() for seam in self.seams],
            },
            "radiometry": {
                "method": self.radiometryMethod.name,
                "mode": self.radiometryMode.name,
                "held": sorted(self.heldKeys),
                "quality": None
                if self.radiometryQuality is None
                else self.radiometryQuality.state(),
            },
            "inputs": [
                {
                    "correction": i.correction.tolist(),
                    "model": i.model.name,
                    "visible": i.visible,
                    "warp": None if i.warp is None else i.warp.state(),
                    "radiometry": None
                    if i.radiometry is None
                    else i.radiometry.state(),
                }
                for i in order
            ],
            "tiePoints": [
                {
                    "moving": p.movingKey,
                    "movingPixel": list(p.movingPixel),
                    "reference": p.referenceKey,
                    "referencePixel": list(p.referencePixel),
                    "enabled": p.enabled,
                    "auto": p.auto,
                    "quality": None if np.isnan(p.quality) else p.quality,
                    "band": p.band,
                    "phaseSnr": None if np.isnan(p.phaseSnr) else p.phaseSnr,
                }
                for p in self.tiePoints
            ],
        }

    def restore(self, state: dict) -> None:
        for item, saved in zip(
            sorted(self.inputs, key=lambda i: i.key), state.get("inputs", [])
        ):
            item.correction = np.asarray(saved["correction"], float)
            item.model = TransformModel[saved.get("model", "TRANSLATION")]
            item.visible = bool(saved.get("visible", True))
            warp = saved.get("warp")
            item.warp = None if warp is None else Warp.fromState(warp)
            radiometry = saved.get("radiometry")
            item.radiometry = (
                None if radiometry is None else Correction.fromState(radiometry)
            )
        self.blend = Blend.fromState(state.get("blend", {}))
        seams = state.get("seams", {})
        self.setSeams(
            SeamMethod[seams.get("method", "PRIORITY")],
            [Seam.fromState(seam) for seam in seams.get("seams", [])],
        )
        radiometry = state.get("radiometry", {})
        self.radiometryMethod = RadiometryMethod[radiometry.get("method", "NONE")]
        self.radiometryMode = LinearMode[radiometry.get("mode", "BOTH")]
        self.heldKeys = frozenset(radiometry.get("held", []))
        quality = radiometry.get("quality")
        self.radiometryQuality = (
            None if quality is None else RadiometryQuality.fromState(quality)
        )
        order = state.get("order")
        if order and sorted(order) == sorted(i.key for i in self.inputs):
            self.inputs.sort(key=lambda i: order.index(i.key))
        self.tiePoints = [
            TiePoint(
                p["moving"],
                tuple(p["movingPixel"]),
                p["reference"],
                tuple(p["referencePixel"]),
                p.get("enabled", True),
                auto=p.get("auto", False),
                quality=float("nan") if p.get("quality") is None else p["quality"],
                band=p.get("band"),
                phaseSnr=float("nan")
                if p.get("phaseSnr") is None
                else float(p["phaseSnr"]),
            )
            for p in state.get("tiePoints", [])
        ]
