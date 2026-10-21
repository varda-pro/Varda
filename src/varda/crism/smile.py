"""Spectral smile: resampling every column to the MTRDR IR wavelength vector
(spec S6).

CRISM's IR detector has smile: a detector row's wavelength changes across the
track by a little over a band (M4). ``standardVector`` is the 411-band MTRDR
IR vector, 0.2–0.3 nm above each band's minimum WA wavelength (M2), so
resampling to it makes the output index-compatible with MTRDR IR bands.

**Resampling** is linear interpolation per column, from the column's own WA
wavelengths to the vector. It has exact two-tap σ propagation, σ² = Σ w²σ²,
and no ringing. The returned σ is the per-band standard deviation only: the
band-to-band correlation interpolation introduces (adjacent targets share a
source band; on the real WA the adjacent-band ρ has median 0.18, 95th
percentile 0.50, maximum 0.93) is not in it. A target outside a column's
valid WA range, or whose tap is NaN, is NaN (never extrapolated).

**Filter joins** (``FILTER_BOUNDARIES_NM``) are detector-row boundaries, the
same in every column, so a band's segment is fixed (``segmentIds`` of the
median wavelength per band) and a target never takes taps from another
segment: it blends two taps of its own segment, or takes the single nearest
one when only one lies in it, or is NaN.

**ESC residual** (TER-style): the continuum-normalized spectrum of each column
still carries a small multiplicative error that is linear in the column's
wavelength offset (a sensitivity the interpolation cannot remove). Per band,
the normalized spectra are binned by offset, a line is fitted to the bin
means (weighted by bin counts), and the correction is model(offset) / model(0).
"""

import warnings
from importlib import resources

import numpy as np
from attrs import frozen

from varda.crism.brightness import bandWindows, segmentIds, smoothBands

_VECTOR_RESOURCE = "mtrdr_wavelengths.csv"
_IR_SPECT_ID = 0
BAD_BAND_ABOVE_NM = 2600.0  # bbl = 0 above this
_KEY_STRIDE = 1e7  # separates columns in the batched search; far above any wavelength
ESC_PASSES = 3
_NO_WAVELENGTH = 1e6  # sorts NaN wavelengths after every valid one


@frozen
class StandardVector:
    wavelengths: np.ndarray  # (411,) nm, ascending
    fwhm: np.ndarray  # (411,) nm
    detectorRows: np.ndarray  # (411,) detector row of each band (descending)
    bbl: np.ndarray  # (411,) uint8, 0 above 2600 nm


def standardVector() -> StandardVector:
    """The IR rows of ``resources/mtrdr_wavelengths.csv``, ascending."""
    text = (resources.files("varda.crism") / "resources" / _VECTOR_RESOURCE).read_text()
    table = np.loadtxt(text.splitlines(), delimiter=",", skiprows=2)
    ir = table[table[:, 0] == _IR_SPECT_ID]
    ir = ir[np.argsort(ir[:, 2])]
    wavelengths = ir[:, 2]
    return StandardVector(
        wavelengths=wavelengths,
        fwhm=ir[:, 3],
        detectorRows=ir[:, 1].astype(int),
        bbl=((ir[:, 4] > 0) & (wavelengths <= BAD_BAND_ABOVE_NM)).astype(np.uint8),
    )


@frozen
class ResampleWeights:
    indices: np.ndarray  # (s, n, 2) int: source band of each tap
    weights: np.ndarray  # (s, n, 2) float64; both 0 where the output is NaN


def sourceSegments(columnWavelengths: np.ndarray) -> np.ndarray:
    """The filter segment of each source band ``(b,)``, from its median
    wavelength over the columns (the all-NaN placeholder band goes last)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return segmentIds(np.nanmedian(columnWavelengths, axis=0))


def linearWeights(
    columnWavelengths: np.ndarray,
    target: np.ndarray,
    sourceSegments: np.ndarray,
    targetSegments: np.ndarray,
) -> ResampleWeights:
    """Two-tap linear interpolation weights from each column's wavelengths
    ``(s, b)`` (ascending where finite; NaN entries are skipped) to ``target``
    ``(n,)``. ``sourceSegments`` ``(b,)`` and ``targetSegments`` ``(n,)`` are
    the filter segment of each band (``brightness.segmentIds``); a target takes
    taps only from its own segment. Targets outside a column's valid range, or
    with no tap in their segment, get zero weights."""
    samples, bands = columnWavelengths.shape
    n = len(target)
    finite = np.isfinite(columnWavelengths)
    order = np.argsort(np.where(finite, columnWavelengths, _NO_WAVELENGTH), axis=1)
    sortedWl = np.take_along_axis(columnWavelengths, order, axis=1)
    valid = finite.sum(axis=1)  # NaN sorted last: the valid ones are [:valid]

    column = np.arange(samples)[:, None]
    flat = (np.nan_to_num(sortedWl, nan=_NO_WAVELENGTH) + column * _KEY_STRIDE).ravel()
    keys = (target[None, :] + column * _KEY_STRIDE).ravel()
    position = np.searchsorted(flat, keys, side="right").reshape(samples, n)
    position -= column * bands

    last = np.maximum(valid - 1, 1)[:, None]
    hi = np.clip(position, 1, last)
    lo = hi - 1
    wLo = np.take_along_axis(sortedWl, lo, axis=1)
    wHi = np.take_along_axis(sortedWl, hi, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        t = (target[None, :] - wLo) / (wHi - wLo)
    t = np.nan_to_num(t, nan=0.0)

    # Taps from another filter segment are not used: one same-segment tap
    # takes the whole weight, none makes the output NaN.
    loOrder = np.take_along_axis(order, lo, axis=1)
    hiOrder = np.take_along_axis(order, hi, axis=1)
    loSame = sourceSegments[loOrder] == targetSegments[None, :]
    hiSame = sourceSegments[hiOrder] == targetSegments[None, :]
    t = np.where(loSame & hiSame, t, np.where(hiSame, 1.0, 0.0))

    firstWl = sortedWl[:, :1]
    lastWl = np.take_along_axis(sortedWl, last, axis=1)
    inRange = (
        (valid[:, None] >= 2)
        & (target[None, :] >= firstWl)
        & (target[None, :] <= lastWl)
        & (loSame | hiSame)
    )
    weights = np.stack([1.0 - t, t], axis=-1) * inRange[..., None]
    indices = np.stack([loOrder, hiOrder], axis=-1)
    return ResampleWeights(indices=indices, weights=weights)


def resample(
    cube: np.ndarray, sigma: np.ndarray, weights: ResampleWeights
) -> tuple[np.ndarray, np.ndarray]:
    """The cube ``(l, s, b)`` and its σ resampled to ``(l, s, n)``, σ² = Σ w²σ².
    NaN where the weights are all zero or a tap with weight > 0 is NaN."""
    column = np.arange(cube.shape[1])[:, None]
    values = np.zeros(cube.shape[:2] + (weights.weights.shape[1],))
    variance = np.zeros_like(values)
    for k in range(weights.weights.shape[2]):
        w = weights.weights[:, :, k]
        taps = (column, weights.indices[:, :, k])
        used = w > 0
        values += np.where(used, w * cube[:, taps[0], taps[1]], 0.0)
        variance += np.where(used, (w * sigma[:, taps[0], taps[1]]) ** 2, 0.0)
    empty = ~(weights.weights.sum(axis=2) > 0)
    values[:, empty] = np.nan
    variance[:, empty] = np.nan
    return values, np.sqrt(variance)


@frozen
class EscFrame:
    correction: np.ndarray  # (s, n): multiplicative error of each column and band


def escOffsets(waAligned: np.ndarray, target: np.ndarray) -> np.ndarray:
    """The ``columnOffsetNm`` of ``estimateEsc``: each column's actual WA
    wavelength at the target band's detector row, minus the target wavelength.
    ``waAligned`` ``(s, n)`` is the WA aligned to the vector's detector rows
    (``readDetectorFrame(wa).alignedTo(vector.detectorRows)``)."""
    return waAligned - target[None, :]


def _escPass(
    columnSpectra: np.ndarray,
    columnOffsetNm: np.ndarray,
    binNm: float,
    wavelengths: np.ndarray,
) -> np.ndarray:
    """One fit of the correction ``(s, n)``; see ``estimateEsc``."""
    samples, n = columnSpectra.shape
    first, last = bandWindows(wavelengths, n)
    level = smoothBands(columnSpectra[None], first, last, columnSpectra[None])[0]
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = columnSpectra / level

    usable = np.isfinite(ratio) & np.isfinite(columnOffsetNm)
    rawBins = np.rint(np.where(usable, columnOffsetNm, 0.0) / binNm).astype(int)
    firstBin = int(rawBins[usable].min()) if usable.any() else 0
    bins = rawBins - firstBin
    k = int(bins.max()) + 1
    flat = (np.arange(n)[None, :] * k + bins)[usable]
    count = np.bincount(flat, minlength=n * k).reshape(n, k).astype(float)
    total = np.bincount(flat, weights=ratio[usable], minlength=n * k).reshape(n, k)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = total / count
    center = (np.arange(k) + firstBin) * binNm

    sw = count.sum(axis=1)
    swx = count @ center
    swy = np.nansum(count * mean, axis=1)
    swxx = count @ center**2
    swxy = np.nansum(count * mean * center[None, :], axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        slope = (sw * swxy - swx * swy) / (sw * swxx - swx**2)
        intercept = (swy - slope * swx) / sw
        correction = (intercept[None, :] + slope[None, :] * columnOffsetNm) / intercept[
            None, :
        ]
    fitted = ((count > 0).sum(axis=1) >= 2) & (intercept > 0)
    correction = np.where(fitted[None, :] & np.isfinite(correction), correction, 1.0)
    return correction


def estimateEsc(
    columnSpectra: np.ndarray,
    columnOffsetNm: np.ndarray,
    binNm: float,
    wavelengths: np.ndarray,
) -> EscFrame:
    """The residual smile sensitivity from per-column spectra ``(s, n)``
    (typically medians over the bland lines) and each column's wavelength
    offset at each band ``(s, n)``, in nm, from the reference wavelength.

    Per band, the continuum-normalized spectra (divided by the mean of the
    band's neighbors inside its filter segment, never across a join;
    ``wavelengths`` ``(n,)`` locates the joins) are binned by offset (bins of
    ``binNm``: 0.125 for FRT/FRS, 0.25 for HRL/HRS), and a line fitted to the
    bin means, weighted by their counts, gives the model. The correction is
    model(offset) / model(0), 1 where fewer than two bins or a nonpositive
    model(0) leave the fit undefined.

    The continuum of a column contains a little of the error itself (the
    neighbors share it), so the fit is repeated ``ESC_PASSES`` times on the
    corrected spectra; the leak shrinks about tenfold per pass."""
    correction = np.ones_like(columnSpectra)
    for _ in range(ESC_PASSES):
        correction *= _escPass(
            columnSpectra / correction, columnOffsetNm, binNm, wavelengths
        )
    return EscFrame(correction=correction)


def applyEsc(
    cube: np.ndarray, sigma: np.ndarray, frame: EscFrame
) -> tuple[np.ndarray, np.ndarray]:
    """Divide the cube ``(l, s, n)`` and σ by the frame's correction."""
    return cube / frame.correction[None], sigma / frame.correction[None]
