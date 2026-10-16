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

# The M7 metric's grid: the 25 MTRDR band centers between 1940 and 2100 nm
# (the MTRDR IR standard vector), so an MTRDR scores exactly as the M7
# measurement did and a per-column TRR3 is interpolated onto the same points.
CO2_GRID_NM = np.array(
    [
        1941.25, 1947.85, 1954.44, 1961.04, 1967.64, 1974.24, 1980.84, 1987.43,
        1994.03, 2000.63, 2007.23, 2013.83, 2020.43, 2027.03, 2033.63, 2040.24,
        2046.84, 2053.44, 2060.04, 2066.64, 2073.25, 2079.85, 2086.45, 2093.06,
        2099.66,
    ]
)  # fmt: skip
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
    """np.nanmedian, but NaN (not a warning) where every value is NaN."""
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


def _co2Residuals(wavelengths: np.ndarray, spectra: np.ndarray) -> np.ndarray:
    """``(..., 25)``: each spectrum, linearly interpolated onto ``CO2_GRID_NM``,
    divided by its least-squares quadratic in (λ − 2020 nm) / 100 nm, minus 1."""
    wavelengths = np.asarray(wavelengths, dtype=np.float64)
    spectra = np.asarray(spectra, dtype=np.float64)
    finite = np.nonzero(np.isfinite(wavelengths))[0]
    order = finite[np.argsort(wavelengths[finite])]
    x = wavelengths[order]
    if x.size < 2 or x[0] > CO2_GRID_NM[0] or x[-1] < CO2_GRID_NM[-1]:
        raise ValueError(
            f"the CO2 residual needs bands from {CO2_GRID_NM[0]} to {CO2_GRID_NM[-1]} nm"
        )
    upper = np.clip(np.searchsorted(x, CO2_GRID_NM), 1, x.size - 1)
    lower = upper - 1
    weight = (CO2_GRID_NM - x[lower]) / (x[upper] - x[lower])
    resampled = (
        spectra[..., order[lower]] * (1.0 - weight)
        + spectra[..., order[upper]] * weight
    )
    u = (CO2_GRID_NM - _CO2_CENTER_NM) / _CO2_HALF_WIDTH_NM
    basis = np.vander(u, 3, increasing=True)  # (25, 3)
    fit = resampled @ (basis @ np.linalg.pinv(basis)).T
    with np.errstate(invalid="ignore", divide="ignore"):
        return resampled / fit - 1.0


def co2Residual(wavelengths: np.ndarray, spectra: np.ndarray) -> np.ndarray:
    """The M7 CO2 residual of each spectrum, ``(...)`` for ``(..., b)``: the
    RMS of the spectrum ÷ its quadratic fit over 1940–2100 nm, on the 25 MTRDR
    band centers there (``CO2_GRID_NM``; other band sets are interpolated)."""
    residuals = _co2Residuals(wavelengths, spectra)
    return np.sqrt(np.mean(residuals**2, axis=-1))


def sceneResidualRms(wavelengths: np.ndarray, spectra: np.ndarray) -> float:
    """The RMS of the scene's median residual spectrum: the part of the CO2
    residual every pixel shares (M7's "scene-median residual")."""
    residuals = _co2Residuals(wavelengths, spectra).reshape(-1, CO2_GRID_NM.size)
    return float(np.sqrt(np.mean(_nanMedian(residuals, axis=0) ** 2)))


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
    if a.size < 2:
        return MapComparison(np.nan, np.nan, int(a.size))
    return MapComparison(
        float(np.corrcoef(a, b)[0, 1]), float(np.median(a - b)), int(a.size)
    )


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
    """Median-kernel band values of one cube (Viviano-Beck 2014 §2)."""

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
        total = total + weight * (continuum - s.R(target, 7)) / np.abs(continuum)
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
    - ``BD1900_2`` = ½(1 − R1930/RC1930) + ½(1 − R1985/RC1985), RC from R1850
      (5) to R2067 (5); R1930, R1985 (5). The long anchor is R2067, as in the
      table's kernel list and CAT; the R2046 in the table's formula is a typo.
    - ``OLINDEX3`` = Σ w_λ (RC_λ − R_λ) / |RC_λ| over the 11 terms 1080, 1152,
      1210, 1250 (0.03 each), 1263, 1276 (0.07), 1330, 1368 (0.12), 1395
      (0.14), 1427, 1470 (0.18); RC from R1750 (7) to R2400 (7); all R (7).
      This is the 11-term form CAT computes, not the 5-term 1210–1330 one.
    - ``BD2310``: the prototype's definition (``bd2310``; 5-band means, nominal
      continuum weight).

    The same code scores ours and the MTRDR's I/F, so these variant choices
    cannot bias a comparison; absolute values may differ slightly from CAT's
    SU products.
    """
    if name not in SUMMARY_PARAMETERS:
        raise ValueError(
            f"unknown summary parameter {name!r}; known: {', '.join(SUMMARY_PARAMETERS)}"
        )
    with np.errstate(invalid="ignore", divide="ignore"):
        return SUMMARY_PARAMETERS[name](wavelengths, cube)
