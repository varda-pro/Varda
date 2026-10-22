"""The continuum of one spectrum: its upper convex hull, the straight-line
segments that touch the spectrum from above. Dividing by it leaves 1 where
the spectrum is on the hull and dips below 1 in absorptions (Clark &
Roush 1984). Pure numpy."""

from __future__ import annotations

import numpy as np


def upperHull(x: np.ndarray, y: np.ndarray) -> list[int]:
    """Indices of the points on the upper convex hull of (x, y), x ascending
    (Andrew's monotone chain)."""
    hull: list[int] = []
    for i in range(len(x)):
        while len(hull) >= 2:
            a, b = hull[-2], hull[-1]
            cross = (x[b] - x[a]) * (y[i] - y[a]) - (y[b] - y[a]) * (x[i] - x[a])
            if cross >= 0:
                hull.pop()
            else:
                break
        hull.append(i)
    return hull


def spectrumContinuum(
    x: np.ndarray,
    y: np.ndarray,
    window: tuple[float, float] | None = None,
    hullX: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """The indices of the samples within ``window`` (in ``x``'s units; all
    samples for None) and the continuum at each: the upper convex hull of
    the finite samples there, linear between its points. The hull is drawn
    against ``hullX`` (e.g. ``x`` in nm) when given. The continuum is NaN
    throughout when fewer than two samples are finite."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    along = x if hullX is None else np.asarray(hullX, dtype=np.float64)
    keep = np.isfinite(x) & np.isfinite(along)
    if window is not None:
        low, high = sorted(window)
        keep &= (x >= low) & (x <= high)
    indices = np.flatnonzero(keep)
    continuum = np.full(indices.size, np.nan)
    good = indices[np.isfinite(y[indices])]
    if good.size < 2:
        return indices, continuum
    order = good[np.argsort(along[good], kind="stable")]
    hull = order[upperHull(along[order], y[order])]
    continuum[:] = np.interp(along[indices], along[hull], y[hull])
    return indices, continuum
