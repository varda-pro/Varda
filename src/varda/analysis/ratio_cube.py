"""Ratio cubes: divide a cube by a denominator spectrum per detector column.

CRISM's artifacts (striping, column-dependent gain) are fixed per sensor
column, so each pixel is divided by a spectrum built only from pixels of its own
column. The column of a pixel comes from the backplane
(``ColumnGeometry.ir_sample``), which also holds for map-projected cubes, or
from an unprojected cube's own image columns. A key
is (strip, column); spectra are never averaged across columns of different
keys except by the explicit, recorded neighbor fallback of
:func:`columnDenominators`.
"""

from __future__ import annotations

import numpy as np
from attrs import frozen

from varda.image_loading.crism_geometry import ColumnGeometry

ColumnKey = tuple[int, int]  # (strip id, detector column)

NEIGHBORS = "neighbors"
WHOLE_COLUMN = "whole column"


def columnKeys(geometry: ColumnGeometry) -> tuple[np.ndarray, np.ndarray]:
    """(strip, column) int64 arrays, shape (rows, cols); -1 where there is no backplane.

    The strip is a dense id over (target_id, segment_id) pairs (0 when both
    are absent); the column is ``rint(ir_sample)``.
    """
    sample = np.asarray(geometry.ir_sample, dtype=np.float64)
    valid = np.isfinite(sample)
    column = np.where(valid, np.rint(np.where(valid, sample, 0.0)), -1).astype(np.int64)

    ids = [a for a in (geometry.target_id, geometry.segment_id) if a is not None]
    strip = np.zeros(sample.shape, dtype=np.int64)
    if ids:
        parts = [np.asarray(a, dtype=np.float64) for a in ids]
        for a in parts:
            valid &= np.isfinite(a)
        stacked = np.stack([np.where(valid, a, 0.0) for a in parts], axis=-1)
        _, inverse = np.unique(stacked[valid], axis=0, return_inverse=True)
        strip[valid] = inverse.reshape(-1)
    strip[~valid] = -1
    column[~valid] = -1
    return strip, column


def _groups(strip: np.ndarray, column: np.ndarray) -> dict[ColumnKey, np.ndarray]:
    """Flat pixel indices of each key, in row-then-column order."""
    s, c = strip.reshape(-1), column.reshape(-1)
    valid = np.flatnonzero((s >= 0) & (c >= 0))
    if valid.size == 0:
        return {}
    width = int(c[valid].max()) + 1
    combined = s[valid] * width + c[valid]
    order = np.argsort(combined, kind="stable")  # stable: keeps row-major order
    sortedIdx, sortedKeys = valid[order], combined[order]
    cuts = np.flatnonzero(np.diff(sortedKeys)) + 1
    starts = np.concatenate([[0], cuts])
    return {
        (int(sortedKeys[a] // width), int(sortedKeys[a] % width)): sortedIdx[a:b]
        for a, b in zip(starts, np.concatenate([cuts, [sortedIdx.size]]))
    }


def inColumnRunMeans(
    cube: np.ndarray, strip: np.ndarray, column: np.ndarray, run: int
) -> np.ndarray:
    """Each pixel's NaN-aware mean over the ``run`` pixels of its key nearest in row order.

    The window is centered on the pixel (shifted inward at the ends so it stays
    ``run`` long when the key has that many pixels) and never crosses keys.
    Pixels with no key are NaN. Returns float32, same shape as ``cube``.
    """
    bands = cube.shape[-1]
    flat = cube.reshape(-1, bands)
    out = np.full(flat.shape, np.nan, dtype=np.float32)
    for idx in _groups(strip, column).values():
        values = flat[idx].astype(np.float64)
        good = np.isfinite(values)
        n = idx.size
        sums = np.vstack([np.zeros(bands), np.cumsum(np.where(good, values, 0.0), 0)])
        counts = np.vstack([np.zeros(bands), np.cumsum(good, 0)])
        start = np.clip(np.arange(n) - run // 2, 0, max(n - run, 0))
        stop = np.minimum(start + run, n)
        total = sums[stop] - sums[start]
        count = counts[stop] - counts[start]
        with np.errstate(invalid="ignore", divide="ignore"):
            out[idx] = np.where(count > 0, total / count, np.nan)
    return out.reshape(cube.shape)


@frozen
class ColumnDenominators:
    """Denominator spectra per key, with their spread, support and fallbacks.

    ``fallback[key]`` is ``"neighbors"`` or ``"whole column"``; absent means the
    key had enough used pixels of its own. ``pooled`` is the (rows, cols) mask
    of pixels that went into at least one key's denominator: a key's own used
    pixels, the used pixels it borrowed from neighbors, or every pixel of the
    column in the whole-column fallback.
    """

    spectra: dict[ColumnKey, np.ndarray]
    std: dict[ColumnKey, np.ndarray]
    counts: dict[ColumnKey, int]
    fallback: dict[ColumnKey, str]
    pooled: np.ndarray


def columnDenominators(
    cube: np.ndarray,
    strip: np.ndarray,
    column: np.ndarray,
    use: np.ndarray | None,
    minCount: int,
    radius: int,
) -> ColumnDenominators:
    """Mean (and std) over each key's used pixels.

    ``use`` is a boolean (rows, cols) mask of pixels allowed in the denominator
    (all pixels with a key when None). A key with fewer than ``minCount`` used
    pixels borrows used pixels from columns ±1..±``radius`` of the same strip,
    nearest first, until it has ``minCount`` (recorded as ``"neighbors"``); if
    still short it uses all pixels of its own column (``"whole column"``).
    """
    flat = cube.reshape(-1, cube.shape[-1])
    groups = _groups(strip, column)
    usable = np.ones(flat.shape[0], dtype=bool) if use is None else use.reshape(-1)
    used = {key: idx[usable[idx]] for key, idx in groups.items()}

    spectra: dict[ColumnKey, np.ndarray] = {}
    std: dict[ColumnKey, np.ndarray] = {}
    counts: dict[ColumnKey, int] = {}
    fallback: dict[ColumnKey, str] = {}
    pooled = np.zeros(flat.shape[0], dtype=bool)
    for key, own in used.items():
        picked, how = own, None
        if own.size < minCount:
            parts = [own]
            total = own.size
            for d in range(1, radius + 1):
                for neighbor in ((key[0], key[1] - d), (key[0], key[1] + d)):
                    if total >= minCount:
                        break
                    if neighbor in used:
                        parts.append(used[neighbor])
                        total += used[neighbor].size
            if total >= minCount:
                picked, how = np.concatenate(parts), NEIGHBORS
            else:
                picked, how = groups[key], WHOLE_COLUMN
        pooled[picked] = True
        values = flat[picked].astype(np.float64)
        with np.errstate(all="ignore"):
            good = np.isfinite(values)
            n = good.sum(axis=0)
            mean = np.where(n > 0, np.where(good, values, 0.0).sum(axis=0) / n, np.nan)
            var = np.where(
                n > 0, (np.where(good, values - mean, 0.0) ** 2).sum(axis=0) / n, np.nan
            )
        spectra[key], std[key], counts[key] = mean, np.sqrt(var), int(picked.size)
        if how:
            fallback[key] = how
    return ColumnDenominators(
        spectra, std, counts, fallback, pooled.reshape(cube.shape[:2])
    )


def applyRatio(
    cube: np.ndarray,
    strip: np.ndarray,
    column: np.ndarray,
    denominators: ColumnDenominators,
) -> np.ndarray:
    """``cube`` divided by each pixel's column denominator, as float32.

    NaN where a pixel has no key, no denominator, or a denominator band that is
    0 or NaN.
    """
    flat = cube.reshape(-1, cube.shape[-1])
    out = np.full(flat.shape, np.nan, dtype=np.float32)
    for key, idx in _groups(strip, column).items():
        den = denominators.spectra.get(key)
        if den is None:
            continue
        safe = np.where((den == 0) | ~np.isfinite(den), np.nan, den)
        out[idx] = flat[idx] / safe
    return out.reshape(cube.shape)
