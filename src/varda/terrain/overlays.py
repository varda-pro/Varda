"""Overlays on a 3D terrain: an image that isn't on the DEM's grid (a CRISM
parameter composite over a CTX DEM, say), aligned to it and draped. No Qt.

Alignment reuses the mosaic core. Mosaics need one map projection and one
band count, so the overlay's best band is first brought into the DEM's map
projection (at its own resolution) and matched, as a one-band image, against
a one-band reference on the DEM's grid (its orthoimage, or a hillshade of the
DEM). The fitted correction (a matrix, or a warp) maps the overlay's map
coordinates onto the DEM's; the overlay's rendering (RGBA) is then resampled
through it onto a texture that covers its footprint over the DEM.
"""

from __future__ import annotations

import math

import numpy as np
from affine import Affine
from attrs import define, field, frozen
from rasterio.crs import CRS as RasterioCRS
from rasterio.warp import Resampling, calculate_default_transform, reproject

from varda.common.entities import VardaRaster
from varda.geo.equivalence import sameCrs
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.mosaicking.bands import chooseBands
from varda.mosaicking.grid import TargetGrid
from varda.mosaicking.matching import MatchSettings, autoAlign, estimateSnr
from varda.mosaicking.model import MosaicInput, MosaicProject
from varda.mosaicking.render import sampleOnGrid
from varda.mosaicking.transforms import TransformModel, Warp, applyMatrix, translation

MAX_TEXTURE = 8192  # px on a side for one overlay's texture
# Map-projected products (CRISM MTRDR, say) are mostly offset; scale and
# rotation from a handful of noisy tie points do more harm than good
DEFAULT_MODEL = TransformModel.TRANSLATION


class AlignmentError(ValueError):
    """Auto Align found nothing to fit; the message is for the user."""


@define
class Alignment:
    """How an overlay's map coordinates (in the DEM's projection) move onto
    the DEM's: a fitted matrix or warp, then a manual shift (map units)."""

    model: TransformModel = DEFAULT_MODEL
    correction: np.ndarray = field(factory=lambda: np.eye(3))
    warp: Warp | None = None
    shift: tuple[float, float] = (0.0, 0.0)
    points: int = 0  # tie points the last fit used
    rms: float = float("nan")  # their residual, map units
    # What Auto Align matched: the image (by name), its band (0-based) and
    # that band's SNR, and the tie points' median phase-plane SNR (0-1)
    matchedOn: str = ""
    band: int | None = None
    bandLabel: str = ""
    bandSnr: float = float("nan")
    phaseSnr: float = float("nan")

    def matrix(self) -> np.ndarray:
        """The correction with the manual shift after it."""
        return translation(*self.shift) @ self.correction

    def correct(self, points: np.ndarray) -> np.ndarray:
        points = np.atleast_2d(np.asarray(points, float))
        if self.warp is not None:
            points = self.warp.forward(points)
        return applyMatrix(self.matrix(), points)

    def state(self) -> dict:
        return {
            "model": self.model.name,
            "correction": self.correction.tolist(),
            "warp": None if self.warp is None else self.warp.state(),
            "shift": list(self.shift),
            "points": self.points,
            "rms": _orNone(self.rms),
            "matchedOn": self.matchedOn,
            "band": self.band,
            "bandLabel": self.bandLabel,
            "bandSnr": _orNone(self.bandSnr),
            "phaseSnr": _orNone(self.phaseSnr),
        }

    @classmethod
    def fromState(cls, state: dict) -> Alignment:
        warp = state.get("warp")
        band = state.get("band")
        return cls(
            TransformModel[state.get("model", DEFAULT_MODEL.name)],
            np.asarray(state.get("correction", np.eye(3).tolist()), float),
            None if warp is None else Warp.fromState(warp),
            tuple(state.get("shift", (0.0, 0.0))),
            int(state.get("points", 0)),
            _orNan(state.get("rms")),
            str(state.get("matchedOn", "")),
            None if band is None else int(band),
            str(state.get("bandLabel", "")),
            _orNan(state.get("bandSnr")),
            _orNan(state.get("phaseSnr")),
        )


def _orNone(value: float) -> float | None:
    """NaN as None, for JSON."""
    return None if math.isnan(value) else float(value)


def _orNan(value: float | None) -> float:
    return float("nan") if value is None else float(value)


@frozen
class Draped:
    """An overlay's texture and where it sits on the DEM, as fractions of the
    DEM's extent: (west, south, east, north), 0-1."""

    rgba: np.ndarray  # (rows, cols, 4) uint8, north row first
    uvBounds: tuple[float, float, float, float]


def asRaster(
    data: np.ndarray, transform: Affine, crs, name: str = "overlay"
) -> VardaRaster:
    """An array (rows, cols, bands), NaN for no data, as an in-memory raster."""
    return VardaRaster(
        ArrayDataSource(np.asarray(data, np.float32), transform=transform, crs=crs),
        name=name,
    )


def toProjection(
    data: np.ndarray, transform: Affine, sourceWkt: str, targetWkt: str
) -> tuple[np.ndarray, Affine]:
    """An array (rows, cols, bands), NaN for no data, reprojected into
    another map projection at about its own resolution (unchanged when the
    projections are the same, however they're written). Takes WKT so a
    worker thread can call it: map projection objects mustn't be shared
    between threads."""
    if sameCrs(sourceWkt, targetWkt):
        return data, transform
    source, target = RasterioCRS.from_wkt(sourceWkt), RasterioCRS.from_wkt(targetWkt)
    rows, cols = data.shape[:2]
    left, top = transform * (0, 0)
    right, bottom = transform * (cols, rows)
    destination, width, height = calculate_default_transform(
        source,
        target,
        cols,
        rows,
        left=min(left, right),
        bottom=min(top, bottom),
        right=max(left, right),
        top=max(top, bottom),
    )
    out = np.full((data.shape[2], height, width), np.nan, np.float32)
    reproject(
        source=np.moveaxis(np.asarray(data, np.float32), 2, 0),
        destination=out,
        src_transform=transform,
        src_crs=source,
        dst_transform=destination,
        dst_crs=target,
        resampling=Resampling.bilinear,
        src_nodata=np.nan,
        dst_nodata=np.nan,
    )
    return np.moveaxis(out, 0, 2), destination


def bandArray(image: VardaRaster, band: int) -> np.ndarray:
    """One band as float32 (rows, cols), NaN for no data."""
    raw = image.getBands([band])
    data = np.ma.filled(raw, np.nan) if np.ma.isMaskedArray(raw) else raw
    data = np.asarray(data, dtype=np.float32)[:, :, 0]
    nodata = image.nodata
    if nodata is not None and np.isfinite(nodata):
        data = np.where(data == nodata, np.nan, data)
    return data


def bestBand(image: VardaRaster) -> tuple[int, float]:
    """The band with the best signal-to-noise ratio, to align on, and its
    SNR (``mosaicking.bands.bandSnr``)."""
    snr = estimateSnr(MosaicInput(image, 0))
    bands = chooseBands(snr, image.bandModel.centers, 1, image.bandModel.badBands)
    band = int(bands[0]) if bands else 0
    return band, float(snr[band])


def bandLabel(image: VardaRaster, band: int) -> str:
    """A band as people name it: its number (1-based), with its wavelength
    or its name when it has one."""
    centers = image.bandModel.centers
    if centers is not None and np.isfinite(centers[band]):
        return f"band {band + 1} ({centers[band]:.6g} nm)"
    label = f"band {band + 1}"
    names = list(image.bandNames or [])
    name = str(names[band]) if band < len(names) else ""
    plain = name.lower() in ("", str(band + 1), label)  # nothing more to say
    return label if plain else f"{label} ({name})"


def align(
    reference: VardaRaster,
    moving: VardaRaster,
    model: TransformModel = DEFAULT_MODEL,
    settings: MatchSettings | None = None,
) -> Alignment:
    """Match a one-band ``moving`` image against a one-band ``reference`` in
    the same map projection and fit ``model``: the moving image's map
    coordinates onto the reference's."""
    project = MosaicProject([reference, moving])
    project.input(1).model = model
    result = autoAlign(project, 1, [0], settings or MatchSettings(bands=(0,)))
    if not result.points:
        raise AlignmentError(
            result.message
            or "No matching features were found between the overlay and the "
            "reference; try another reference, or align by hand."
        )
    project.fit(1)
    fitted = project.input(1)
    inliers = [p.residual for p in project.tiePointsOf(1) if p.inlier]
    rms = float(np.sqrt(np.mean(np.square(inliers)))) if inliers else math.nan
    snr = np.array([p.phaseSnr for p in project.tiePointsOf(1)], float)
    snr = snr[np.isfinite(snr)]
    return Alignment(
        model,
        fitted.correction.copy(),
        fitted.warp,
        (0.0, 0.0),
        len(inliers),
        rms,
        phaseSnr=float(np.median(snr)) if snr.size else math.nan,
    )


def hillshade(
    dem: np.ndarray, pixelSize: float, azimuth: float = 315.0, altitude: float = 45.0
) -> np.ndarray:
    """A hillshade (0-1) of a DEM array, NaN kept: a reference for aligning
    when the DEM has no orthoimage."""
    dy, dx = np.gradient(np.asarray(dem, float), pixelSize)
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    az, alt = np.radians(azimuth), np.radians(altitude)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(
        az - aspect
    )
    return np.clip(shade, 0, 1)


def drape(
    rgba: np.ndarray,
    transform: Affine,
    crs,
    alignment: Alignment,
    demBounds: tuple[float, float, float, float],
    maxSide: int = MAX_TEXTURE,
) -> Draped | None:
    """An overlay's rendering (``rgba``, uint8, alpha 0 for no data) in the
    DEM's map projection, moved by ``alignment`` and resampled onto a north-up
    texture over its footprint within the DEM; None if it misses the DEM."""
    cube = np.asarray(rgba, np.float32)
    cube[cube[:, :, 3] == 0] = np.nan
    image = asRaster(cube, transform, crs)
    item = MosaicInput(
        image, 0, alignment.matrix(), alignment.model, warp=alignment.warp
    )
    footprint = item.footprint()
    west, south, east, north = demBounds
    left, right = max(footprint[:, 0].min(), west), min(footprint[:, 0].max(), east)
    bottom, top = max(footprint[:, 1].min(), south), min(footprint[:, 1].max(), north)
    if right <= left or top <= bottom:
        return None
    size = max(item.pixelSize, max(right - left, top - bottom) / (maxSide - 2))
    width = max(1, math.ceil((right - left) / size))
    height = max(1, math.ceil((top - bottom) / size))
    grid = TargetGrid(Affine(size, 0, left, 0, -size, top), width, height, image.crs)
    sampled = sampleOnGrid(item, cube, grid)
    out = np.zeros((height + 2, width + 2, 4), np.uint8)  # a clear border: the
    # texture clamps to its edge pixels outside its bounds
    finite = np.isfinite(sampled).all(axis=2)
    values = np.clip(np.nan_to_num(sampled), 0, 255).astype(np.uint8)
    out[1:-1, 1:-1][finite] = values[finite]
    # the texture's bounds include the border
    left, top = left - size, top + size
    right, bottom = left + (width + 2) * size, top - (height + 2) * size
    spanX, spanY = east - west, north - south
    return Draped(
        out,
        (
            (left - west) / spanX,
            (bottom - south) / spanY,
            (right - west) / spanX,
            (top - south) / spanY,
        ),
    )
