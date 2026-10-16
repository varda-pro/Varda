"""Metrics for comparing CRISM products: ours, the official MTRDRs and the
synthetic truth. The same code scores every product, so a metric's own
conventions cannot bias a comparison.

Spectra are ``(..., band)`` with wavelengths in nm; NaN marks invalid
samples and gives NaN results where it matters, never a warning.
"""

from collections.abc import Callable

import numpy as np
from attrs import frozen

from varda.mosaicking.matching import refineShift, tileShift

CO2_WINDOW_NM = (1940.0, 2100.0)  # exclusive, as in M7
_CO2_CENTER_NM = 2020.0
_CO2_HALF_WIDTH_NM = 100.0
SHAPE_WINDOW_NM = (1300.0, 1700.0)


# --- NaN-aware reductions without warnings -----------------------------------------


def _nanMean(values: np.ndarray, axis: int = -1) -> np.ndarray:
    """np.nanmean, but NaN (not a warning) where every value is NaN."""
    finite = np.isfinite(values)
    total = np.where(finite, values, 0.0).sum(axis=axis)
    count = finite.sum(axis=axis)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / count, np.nan)


def _nanMedian(values: np.ndarray, axis: int = -1) -> np.ndarray:
    """np.nanmedian with ±inf also missing, and NaN (not a warning) where
    every value is missing."""
    values = np.where(np.isfinite(values), values, np.nan)
    ordered = np.sort(np.moveaxis(values, axis, -1), axis=-1)  # NaN sorts last
    count = np.isfinite(ordered).sum(axis=-1)
    lower = np.maximum((count - 1) // 2, 0)[..., None]
    upper = (count // 2)[..., None]
    middle = (
        0.5
        * (
            np.take_along_axis(ordered, lower, -1)
            + np.take_along_axis(ordered, upper, -1)
        )[..., 0]
    )
    return np.where(count > 0, middle, np.nan)


# --- the CO2 residual (V1, measurement M7) -----------------------------------------


def _co2Residuals(
    wavelengths: np.ndarray, spectra: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Each spectrum ÷ its least-squares quadratic in (λ − 2020 nm) / 100 nm,
    minus 1, on its own bands inside 1940–2100 nm: ``(..., b)`` residuals (0
    outside the window, NaN where a band inside it is invalid) and the
    window mask, ``(b,)`` or ``(..., b)`` like ``wavelengths``."""
    wavelengths = np.asarray(wavelengths, dtype=np.float64)
    spectra = np.asarray(spectra, dtype=np.float64)
    if wavelengths.shape not in (spectra.shape[-1:], spectra.shape):
        raise ValueError(
            f"wavelengths {wavelengths.shape} fit neither one spectrum nor all "
            f"of {spectra.shape}"
        )
    with np.errstate(invalid="ignore"):  # NaN wavelengths are outside
        inside = (wavelengths > CO2_WINDOW_NM[0]) & (wavelengths < CO2_WINDOW_NM[1])
    if (inside.sum(axis=-1) < 4).any():
        raise ValueError(
            f"the CO2 residual needs bands inside {CO2_WINDOW_NM[0]}–"
            f"{CO2_WINDOW_NM[1]} nm"
        )
    u = np.where(inside, (wavelengths - _CO2_CENTER_NM) / _CO2_HALF_WIDTH_NM, 0.0)
    basis = np.stack([np.ones_like(u), u, u**2], axis=-1)  # (..., b, 3)
    masked = basis * inside[..., None]
    gram = np.einsum("...bj,...bk->...jk", masked, masked)
    moments = np.einsum("...bj,...b->...j", masked, np.where(inside, spectra, 0.0))
    coefficients = np.einsum("...jk,...k->...j", np.linalg.inv(gram), moments)
    fit = np.einsum("...bj,...j->...b", basis, coefficients)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(inside, spectra / fit - 1.0, 0.0), inside


def co2Residual(wavelengths: np.ndarray, spectra: np.ndarray) -> np.ndarray:
    """The M7 CO2 residual of each spectrum, ``(...)`` for ``(..., b)``: the
    RMS of the spectrum ÷ its quadratic fit, over its own bands inside
    1940–2100 nm (no interpolation, so white noise scores the same on any
    band grid). ``wavelengths`` is one ``(b,)`` vector shared by every
    spectrum, or ``(..., b)`` like ``spectra`` (e.g. sensor-space columns,
    each with its own wavelengths). NaN where a band in the window is invalid."""
    residuals, inside = _co2Residuals(wavelengths, spectra)
    return np.sqrt(np.sum(residuals**2, axis=-1) / np.sum(inside, axis=-1))


def sceneResidualRms(wavelengths: np.ndarray, spectra: np.ndarray) -> float:
    """The RMS of the scene's median residual spectrum: the part of the CO2
    residual every pixel shares (M7's "scene-median residual"). The median is
    taken band by band over the valid spectra (those with a finite
    ``co2Residual``). With one shared wavelength vector that is the median
    residual on those bands. With per-spectrum wavelengths it is still taken
    per band index (the same detector row for sensor-space columns, whose
    wavelengths differ by about 1 nm), each spectrum counting only where that
    band is inside its window."""
    residuals, inside = _co2Residuals(wavelengths, spectra)
    bands = residuals.shape[-1]
    residuals = residuals.reshape(-1, bands)
    inside = np.broadcast_to(inside, (*np.shape(spectra)[:-1], bands)).reshape(
        -1, bands
    )
    valid = np.isfinite(residuals).all(axis=-1)
    if not valid.any():
        return float("nan")
    median = _nanMedian(np.where(inside, residuals, np.nan)[valid], axis=0)
    return float(np.sqrt(_nanMean(median**2)))


# --- spectral shape and maps -------------------------------------------------------


def shapeRatio(
    ours: np.ndarray, reference: np.ndarray, wavelengths: np.ndarray
) -> np.ndarray:
    """Per band, the median over spectra of (ours ÷ its 1300–1700 nm mean) ÷
    (reference ÷ its 1300–1700 nm mean): 1 where the shapes agree, whatever
    the brightness. ``ours`` and ``reference`` are matching ``(..., b)``."""
    wavelengths = np.asarray(wavelengths, dtype=np.float64)
    window = (wavelengths >= SHAPE_WINDOW_NM[0]) & (wavelengths <= SHAPE_WINDOW_NM[1])
    bands = wavelengths.size
    a = np.asarray(ours, dtype=np.float64).reshape(-1, bands)
    b = np.asarray(reference, dtype=np.float64).reshape(-1, bands)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = (a / _nanMean(a[:, window])[:, None]) / (
            b / _nanMean(b[:, window])[:, None]
        )
    return _nanMedian(ratio, axis=0)


@frozen
class MapComparison:
    pearson: float
    medianBias: float  # median of ours − reference
    n: int  # pixels where both are finite


def compareMaps(ours: np.ndarray, reference: np.ndarray) -> MapComparison:
    """Pearson r and median bias of two co-registered parameter maps, over the
    pixels where both are finite."""
    a, b = np.asarray(ours, np.float64), np.asarray(reference, np.float64)
    both = np.isfinite(a) & np.isfinite(b)
    a, b = a[both], b[both]
    if a.size == 0:
        return MapComparison(np.nan, np.nan, 0)
    da, db = a - a.mean(), b - b.mean()
    scale = np.sqrt((da @ da) * (db @ db))
    pearson = float(da @ db / scale) if scale > 0 else np.nan  # NaN if constant
    return MapComparison(pearson, float(np.median(a - b)), int(a.size))


def _albedo(image: np.ndarray) -> np.ndarray:
    """A finite 2-D image: a cube's band mean, NaN filled with the image mean."""
    values = np.asarray(image, dtype=np.float64)
    if values.ndim == 3:
        values = _nanMean(values, axis=-1)
    if values.ndim != 2:
        raise ValueError(f"an image or cube is needed, not shape {values.shape}")
    finite = np.isfinite(values)
    if not finite.any():
        raise ValueError("the image has no valid pixels")
    return np.where(finite, values, values[finite].mean())


def geolocationOffset(ours: np.ndarray, reference: np.ndarray) -> tuple[float, float]:
    """(dx, dy) in pixels: a feature at p in ``reference`` is at p + (dx, dy)
    in ``ours``. Both are same-shaped albedo images ``(l, s)`` (e.g. a 1.3–1.7
    µm mean) or cubes ``(l, s, b)`` (averaged over bands), on the same grid.
    Phase correlation (``varda.mosaicking.matching.tileShift``) refined by the
    Leprince phase-plane fit (``refineShift``)."""
    a, b = _albedo(reference), _albedo(ours)
    if a.shape != b.shape:
        raise ValueError(f"shapes differ: {b.shape} and {a.shape}")
    coarse, _ = tileShift(a, b)
    shift, _ = refineShift(a, b, (float(coarse[0]), float(coarse[1])))
    return float(shift[0]), float(shift[1])


# --- summary parameters ------------------------------------------------------------


def bd2310(wavelengths: np.ndarray, cube: np.ndarray) -> np.ndarray:
    """The fusion prototype's "BD2300" (``stage2b_diag.py:55-58``), named BD2310
    here (D7): 1 − R2310 / ((1 − t)·R2160 + t·R2400) with t = (2310 − 2160) /
    (2400 − 2160) from the nominal wavelengths, each R the NaN-ignoring mean of
    the 5 bands centered on the band nearest it. Not a Viviano-Beck parameter."""
    wavelengths = np.asarray(wavelengths, dtype=np.float64)
    cube = np.asarray(cube, dtype=np.float64)

    def mean5(target: float) -> np.ndarray:
        i = int(np.nanargmin(np.abs(wavelengths - target)))
        return _nanMean(cube[..., i - 2 : i + 3])

    t = (2310 - 2160) / (2400 - 2160)
    with np.errstate(invalid="ignore", divide="ignore"):
        return 1 - mean5(2310) / ((1 - t) * mean5(2160) + t * mean5(2400))


class _Bands:
    """Median-kernel band values of one cube (Viviano-Beck 2014 §4.5)."""

    def __init__(self, wavelengths: np.ndarray, cube: np.ndarray) -> None:
        self.wavelengths = np.asarray(wavelengths, dtype=np.float64)
        self.cube = np.asarray(cube, dtype=np.float64)

    def index(self, target: float) -> int:
        return int(np.nanargmin(np.abs(self.wavelengths - target)))

    def W(self, target: float) -> float:
        """The center of the band nearest ``target``."""
        return float(self.wavelengths[self.index(target)])

    def R(self, target: float, kernel: int) -> np.ndarray:
        """The median of the ``kernel`` bands centered on the band nearest
        ``target``."""
        i, half = self.index(target), kernel // 2
        return _nanMedian(self.cube[..., max(i - half, 0) : i + half + 1])

    def continuum(
        self, low: float, high: float, at: float, lowKernel: int, highKernel: int
    ) -> np.ndarray:
        """The straight line from R(low) to R(high), at the band nearest ``at``."""
        weight = (self.W(at) - self.W(low)) / (self.W(high) - self.W(low))
        return (1 - weight) * self.R(low, lowKernel) + weight * self.R(high, highKernel)


def _bd2290(s: _Bands) -> np.ndarray:
    return 1 - s.R(2290, 5) / s.continuum(2250, 2350, 2290, 5, 5)


def _bd1900_2(s: _Bands) -> np.ndarray:
    depth1930 = 1 - s.R(1930, 5) / s.continuum(1850, 2067, 1930, 5, 5)
    depth1985 = 1 - s.R(1985, 5) / s.continuum(1850, 2067, 1985, 5, 5)
    return 0.5 * depth1930 + 0.5 * depth1985


def _d2300(s: _Bands) -> np.ndarray:
    def relative(target: float, kernel: int) -> np.ndarray:
        return s.R(target, kernel) / s.continuum(1815, 2530, target, 5, 5)

    numerator = relative(2290, 3) + relative(2320, 3) + relative(2330, 3)
    denominator = relative(2120, 5) + relative(2170, 5) + relative(2210, 5)
    return 1 - numerator / denominator


_OLINDEX3_WEIGHTS = {
    1080: 0.03, 1152: 0.03, 1210: 0.03, 1250: 0.03, 1263: 0.07, 1276: 0.07,
    1330: 0.12, 1368: 0.12, 1395: 0.14, 1427: 0.18, 1470: 0.18,
}  # fmt: skip


def _olindex3(s: _Bands) -> np.ndarray:
    total = np.zeros(s.cube.shape[:-1])
    for target, weight in _OLINDEX3_WEIGHTS.items():
        continuum = s.continuum(1750, 2400, target, 7, 7)
        total = total + weight * (1 - s.R(target, 7) / continuum)
    return total


SUMMARY_PARAMETERS: dict[str, Callable[[np.ndarray, np.ndarray], np.ndarray]] = {
    "BD2310": bd2310,
    "D2300": lambda w, c: _d2300(_Bands(w, c)),
    "BD2290": lambda w, c: _bd2290(_Bands(w, c)),
    "BD1900_2": lambda w, c: _bd1900_2(_Bands(w, c)),
    "OLINDEX3": lambda w, c: _olindex3(_Bands(w, c)),
}


def summaryParameter(
    name: str, wavelengths: np.ndarray, cube: np.ndarray
) -> np.ndarray:
    """A summary parameter map ``(...)`` of a cube ``(..., b)``.

    Viviano-Beck et al. (2014, JGR Planets 119, Table 2) formulas, with each
    R_λ the median of a kernel of bands centered on the band nearest λ
    (kernel widths in parentheses) and each continuum a straight line between
    two such values, weighted by the actual band centers:

    - ``D2300`` = 1 − (R2290/RC2290 + R2320/RC2320 + R2330/RC2330) /
      (R2120/RC2120 + R2170/RC2170 + R2210/RC2210), RC the line from R1815 (5)
      to R2530 (5); R2290, R2320, R2330 (3); R2120, R2170, R2210 (5).
    - ``BD2290`` = 1 − R2290 / RC2290, RC from R2250 (5) to R2350 (5); R2290 (5).
    - ``BD1900_2`` = ½·RB1930 + ½·RB1985 (RB_λ = 1 − R_λ/RC_λ), RC from R1850
      (5) to R2067 (5); R1930, R1985 (5). This is VB2014's form, with the R2067
      anchor (ruling P2). CAT's hyperspectral BD1900_2 uses RB1930 alone.
    - ``OLINDEX3`` = Σ w_λ·RB_λ (RB_λ = 1 − R_λ/RC_λ) over the 11 terms 1080,
      1152, 1210, 1250 (0.03 each), 1263, 1276 (0.07), 1330, 1368 (0.12), 1395
      (0.14), 1427, 1470 (0.18); RC from R1750 (7) to R2400 (7); all R (7).
      This is the VB2014 / DPSIS form (ruling P2). CAT and the delivered
      TER/MTRDR SU products use the 5-term form instead: RB1210·0.1 +
      RB1250·0.1 + RB1263·0.2 + RB1276·0.2 + RB1330·0.4, its continuum
      extrapolated from R1750 and R1862.
    - ``BD2310``: the prototype's definition (``bd2310``; 5-band means, nominal
      continuum weight).

    The same code scores ours and the MTRDR's I/F, so these variant choices
    cannot bias a comparison; absolute values differ from CAT's SU products
    where the variants differ (BD1900_2, OLINDEX3).
    """
    if name not in SUMMARY_PARAMETERS:
        raise ValueError(
            f"unknown summary parameter {name!r}; known: {', '.join(SUMMARY_PARAMETERS)}"
        )
    with np.errstate(invalid="ignore", divide="ignore"):
        return SUMMARY_PARAMETERS[name](wavelengths, cube)
