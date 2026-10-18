"""Helpers for analyses that read a cube and write a raster."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np
from affine import Affine

from varda.common.entities import VardaRaster
from varda.common.spectral_axis import BAND_METADATA_KEYS, parseEnviList
from varda.image_loading.data_sources.array_data_source import ArrayDataSource


logger = logging.getLogger(__name__)

# Pixels missing more than this share of their bands are dropped rather than filled
MIN_VALID_BAND_FRACTION = 0.5
_FILL_CHUNK = 16_384


def validPixels(cube: np.ndarray, nodata: float | None) -> np.ndarray:
    """(rows, cols) mask of pixels that are finite and not nodata in every band."""
    valid = np.all(np.isfinite(cube), axis=2)
    if nodata is not None:
        valid &= np.all(cube != nodata, axis=2)
    return valid


def readCube(image: VardaRaster, fill: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """The image as a float64 (rows, cols, bands) cube ready for analysis, and
    the (rows, cols) mask of usable pixels.

    Nodata and the image's bad bands (ENVI ``bbl``) become NaN; a pixel's
    missing bands are filled along its spectrum (see ``fillAlongSpectrum``) so
    that a bad band here and there does not knock the pixel out; pixels
    missing most of their good bands stay NaN. With ``fill=False`` nothing is
    filled: missing values stay NaN and the mask is of the pixels with a value
    in every good band.
    """
    raw = image.getData()
    if np.asarray(raw).dtype != np.float64:
        logger.debug("Reading %s as float64 (stored as %s)", image.name, raw.dtype)
    cube = np.array(raw, dtype=np.float64, copy=True)
    model = image.bandModel
    if model.noData is not None:
        cube[cube == model.noData] = np.nan
    if model.badBands.any():
        logger.info(
            "%s: ignoring %d bad band(s) marked in its header",
            image.name,
            int(model.badBands.sum()),
        )
        cube[:, :, model.badBands] = np.nan
    if not fill:
        return cube, np.all(np.isfinite(cube[:, :, goodBands(image)]), axis=2)
    return fillAlongSpectrum(cube, badBands=model.badBands)


def goodBands(image: VardaRaster) -> np.ndarray:
    """Indices of the bands not marked bad in the image's header (ENVI ``bbl``)."""
    bad = image.bandModel.badBands
    if bad.size != image.bandCount:
        return np.arange(image.bandCount)
    return np.flatnonzero(~bad)


def readGoodBands(
    image: VardaRaster, fill: bool = True
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``readCube`` without the bad bands, and the indices of the bands kept.

    For analyses that compute statistics or fits across bands: a bad band
    filled from its neighbors is a linear combination of them, which makes a
    covariance (nearly) singular and counts its neighbors twice."""
    cube, valid = readCube(image, fill)
    bands = goodBands(image)
    if bands.size < image.bandCount:
        cube = np.ascontiguousarray(cube[:, :, bands])
    return cube, valid, bands


def fillAlongSpectrum(
    cube: np.ndarray,
    minValidFraction: float = MIN_VALID_BAND_FRACTION,
    badBands: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Fill NaN bands of each pixel by linear interpolation along its spectrum
    (the nearest value beyond the first/last finite band). Pixels with fewer
    than ``minValidFraction`` of their good bands finite (``badBands`` don't
    count) are set entirely to NaN and reported False in the returned
    (rows, cols) mask.

    A float64 input is modified in place (a cube can be several GB)."""
    data = np.asarray(cube, dtype=np.float64)
    rows, cols, bands = data.shape
    flat = data.reshape(-1, bands)
    finite = np.isfinite(flat)
    good = np.ones(bands, bool) if badBands is None else ~np.asarray(badBands, bool)
    goodCount = max(int(good.sum()), 1)
    needed = max(1 if goodCount == 1 else 2, int(np.ceil(minValidFraction * goodCount)))
    valid = finite[:, good].sum(axis=1) >= needed
    flat[~valid] = np.nan

    toFill = np.flatnonzero(valid & ~finite.all(axis=1))
    idx = np.arange(bands)
    for start in range(0, toFill.size, _FILL_CHUNK):
        rowsToFill = toFill[start : start + _FILL_CHUNK]
        block, ok = flat[rowsToFill], finite[rowsToFill]
        # index of the previous / next finite band for every band position
        prevIdx = np.maximum.accumulate(np.where(ok, idx, -1), axis=1)
        nextRev = np.maximum.accumulate(np.where(ok[:, ::-1], idx, -1), axis=1)
        nextIdx = np.where(nextRev >= 0, bands - 1 - nextRev, -1)[:, ::-1]
        hasPrev, hasNext = prevIdx >= 0, nextIdx >= 0
        prevVal = np.take_along_axis(block, np.clip(prevIdx, 0, bands - 1), axis=1)
        nextVal = np.take_along_axis(block, np.clip(nextIdx, 0, bands - 1), axis=1)
        both = hasPrev & hasNext
        # (at finite positions prev == next: no gap to interpolate, avoid 0/0)
        span = np.where(both & (nextIdx > prevIdx), nextIdx - prevIdx, 1)
        weight = np.where(both, (idx - prevIdx) / span, 0.0)
        interpolated = np.where(
            both,
            prevVal + weight * (nextVal - prevVal),
            np.where(hasPrev, prevVal, nextVal),
        )
        flat[rowsToFill] = np.where(ok, block, interpolated)
    if toFill.size:
        logger.info(
            "Filled missing bands along the spectrum for %d of %d pixels",
            toFill.size,
            flat.shape[0],
        )
    return data, valid.reshape(rows, cols)


def bandIndex(image: VardaRaster, oneBased: int) -> int:
    """Clamp a user-facing 1-based band number to a valid 0-based index."""
    return int(np.clip(oneBased - 1, 0, image.bandCount - 1))


def singleBandRaster(
    image: VardaRaster,
    values: np.ndarray,
    *,
    name: str,
    bandName: str,
    units: str = "",
    description: str = "",
) -> VardaRaster:
    """A one-band raster over ``image``'s footprint and georeferencing."""
    source = ArrayDataSource(
        np.asarray(values, dtype=np.float32)[:, :, np.newaxis],
        wavelengths=np.array([bandName]),
        wavelengthUnits=units,
        bandNames=[bandName],
        transform=image.transform,
        crs=image.crs,
        description=description or f"{bandName} of {image.name}",
    )
    return VardaRaster(source, name=name)


def namedBandsRaster(
    image: VardaRaster,
    values: np.ndarray,
    bandNames: list[str],
    *,
    name: str,
    description: str,
    units: str = "",
) -> VardaRaster:
    """A raster over ``image``'s footprint whose bands are named, not
    wavelengths (hue, saturation, value...)."""
    source = ArrayDataSource(
        np.asarray(values, dtype=np.float32),
        wavelengths=np.array(bandNames),
        wavelengthUnits=units,
        bandNames=list(bandNames),
        transform=image.transform,
        crs=image.crs,
        description=description,
    )
    return VardaRaster(source, name=name)


def bandMetadata(image: VardaRaster) -> dict:
    """The image's per-band metadata (FWHM, bad bands, scale factor) for a
    derived raster with the same bands, so its band model carries over."""
    return {
        k: image.extraMetadata[k]
        for k in BAND_METADATA_KEYS
        if k in image.extraMetadata
    }


def bandMetadataSubset(image: VardaRaster, bands: Sequence[int] | np.ndarray) -> dict:
    """The per-band metadata (FWHM, bad bands) for just ``bands``; values for
    every band (the scale factor) carry over as they are."""
    metadata = {}
    for key in BAND_METADATA_KEYS:
        value = image.extraMetadata.get(key)
        if value is None:
            continue
        try:
            values = parseEnviList(value)
        except (TypeError, ValueError):
            values = None
        if values is not None and len(values) == image.bandCount:
            metadata[key] = [values[b] for b in bands]
        else:
            metadata[key] = value
    return metadata


def sameBandsRaster(
    image: VardaRaster,
    values: np.ndarray,
    *,
    name: str,
    description: str,
    transform: Affine | None = None,
    extraMetadata: dict | None = None,
) -> VardaRaster:
    """A cube with ``image``'s bands, wavelengths (and their FWHM and bad
    bands), display bands and footprint, or another grid (``transform``)
    when it was resampled, rotated or cropped; plus ``extraMetadata``."""
    source = ArrayDataSource(
        np.asarray(values, dtype=np.float32),
        wavelengths=np.asarray(image.wavelengths),
        wavelengthUnits=image.wavelengthUnits,
        bandNames=list(image.bandNames),
        transform=image.transform if transform is None else transform,
        crs=image.crs,
        defaultBands=np.asarray(image.defaultBands),
        extraMetadata={**bandMetadata(image), **(extraMetadata or {})},
        description=description,
    )
    return VardaRaster(source, name=name)
