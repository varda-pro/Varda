"""A pixel's brightness level and the detector's filter segments.

Surface texture and per-element multiplicative patterns scale all or one band
of a spectrum by a factor, so noise estimation (``noise.py``) and despiking
(``despike.py``) both work on spectra divided by the pixel's level L: the mean
of its other bands within ``SMOOTH_HALF_WIDTH`` bands, never across a filter
boundary.
"""

import numpy as np

# Where the IR detector's order-sorting filters join: spectra step there, per
# column, between the bands either side. 1656 nm falls between 1653 and 1660 nm
# (the 1.65 µm join); 2730 nm is mid-gap of the ≈2700 nm cliff, where the
# detector rows 173–193 are missing (2701 → 2761 nm).
FILTER_BOUNDARIES_NM = (1656.0, 2730.0)
SMOOTH_HALF_WIDTH = 10  # bands each side in the brightness level L


def bandWindows(
    wavelengths: np.ndarray | None, bands: int
) -> tuple[np.ndarray, np.ndarray]:
    """First and last band (inclusive) of each band's smoothing window, kept
    inside its filter segment."""
    if wavelengths is None:
        segment = np.zeros(bands, dtype=int)
    else:
        segment = np.searchsorted(
            FILTER_BOUNDARIES_NM, np.nan_to_num(wavelengths, nan=np.inf)
        )
    index = np.arange(bands)
    first = np.empty(bands, dtype=int)
    last = np.empty(bands, dtype=int)
    for s in np.unique(segment):
        members = index[segment == s]
        first[members] = np.maximum(members - SMOOTH_HALF_WIDTH, members[0])
        last[members] = np.minimum(members + SMOOTH_HALF_WIDTH, members[-1])
    return first, last


def smoothBands(
    block: np.ndarray, first: np.ndarray, last: np.ndarray, own: np.ndarray
) -> np.ndarray:
    """Mean of the finite values over each band's window ``[first, last]``
    (indices into ``block``'s band axis) without the band itself, ``own`` being
    that band's values ``(l, s, b)``; NaN where a window has none."""
    finite = np.isfinite(block)
    padding = [(0, 0), (0, 0), (1, 0)]
    total = np.pad(np.cumsum(np.where(finite, block, 0.0), axis=2), padding)
    number = np.pad(np.cumsum(finite, axis=2), padding)
    ownFinite = np.isfinite(own)
    n = number[:, :, last + 1] - number[:, :, first] - ownFinite
    summed = total[:, :, last + 1] - total[:, :, first] - np.where(ownFinite, own, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return summed / np.where(n > 0, n, np.nan)
