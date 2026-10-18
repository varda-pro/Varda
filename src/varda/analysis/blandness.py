"""Is a spectrum "bland" (featureless) enough to be a ratio denominator?

Generic check: the deepest continuum-removed absorption and the relative
slope must both be small. Target check: the spectral parameters that detect a
mineral (Viviano-Beck et al. 2014, computed by HyPyRameter) must stay below a
maximum, and the slope over the target's wavelength window must be small.
The maxima are conservative starting points, editable by the user.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import attrs
import numpy as np

from varda.analysis.continuum import continuumRemovedCube
from varda.analysis.hypyrameter_adapter import computeParameterCube

DEFAULT_MAX_DEPTH = 0.02  # deepest absorption: 1 - continuum-removed value
DEFAULT_MAX_SLOPE = 0.10  # |relative slope| per micrometer
_NEEDS_HYPYRAMETER = "needs HyPyRameter"

ParameterFunction = Callable[[np.ndarray, np.ndarray, Sequence[str]], np.ndarray]
ParameterCubeFunction = Callable[[np.ndarray, np.ndarray, Sequence[str]], np.ndarray]


@attrs.frozen
class CheckResult:
    name: str
    value: float
    limit: float
    passed: bool


@attrs.frozen
class BlandnessResult:
    checks: tuple[CheckResult, ...]
    note: str = ""

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)


@attrs.frozen
class ParameterLimit:
    name: str
    maximum: float


@attrs.frozen
class Preset:
    name: str
    limits: tuple[ParameterLimit, ...]
    slopeWindowNm: tuple[float, float]
    maxSlope: float = DEFAULT_MAX_SLOPE


def _limits(maximum: float, *names: str) -> tuple[ParameterLimit, ...]:
    return tuple(ParameterLimit(name, maximum) for name in names)


# Parameter definitions are from Viviano-Beck et al. 2014 (JGR Planets 119).
# Each maximum is a conservative starting point: the parameters are
# band-depth-like indices that are ~0 for a featureless spectrum, so 0.01 to
# 0.02 (1 to 2 percent) is below what typically marks a detection, and a
# denominator under it adds little false structure to a ratio.
# Mafic targets use a 1000-1700 nm slope window (the 1 um region, before
# hydration features); hydrated targets use 1000-2600 nm, the range the
# 1.9, 2.3 and 2.5 um parameters span.
PRESETS: dict[str, Preset] = {
    preset.name: preset
    for preset in (
        Preset(  # low- and high-Ca pyroxene (LCP, HCP) and olivine bands near 1 um
            "Plagioclase",
            _limits(0.02, "LCPINDEX2", "HCPINDEX2", "OLINDEX3"),
            (1000, 1700),
        ),
        Preset(  # olivine 1 um band; pyroxene indices exclude mafic confusion
            "Olivine",
            _limits(0.02, "OLINDEX3", "LCPINDEX2", "HCPINDEX2"),
            (1000, 1700),
        ),
        Preset(  # 1.9 um H2O, 2.3 um Fe/Mg-OH drop, 2.29-2.48 um metal-OH
            "Fe/Mg phyllosilicates",
            _limits(0.01, "BD1900r2", "D2300", "MIN2295_2480"),
            (1000, 2600),
        ),
        Preset(  # 1.9 um H2O, 2.2-2.25 um Al-OH, 2.3 um drop
            "Al phyllosilicates",
            _limits(0.01, "BD1900r2", "MIN2250", "D2300"),
            (1000, 2600),
        ),
        Preset(  # carbonate overtone and metal-OH/carbonate minima near 2.3-2.5 um
            "Carbonates",
            _limits(0.01, "BDCARB", "MIN2295_2480", "MIN2345_2537"),
            (1000, 2600),
        ),
        Preset(  # sulfate 2.4 um drop (SINDEX2) with the 1.9 um hydration band
            "Sulfates",
            _limits(0.02, "SINDEX2", "BD1900r2"),
            (1000, 2600),
        ),
        Preset(  # any hydration: 1.9 um H2O, 2.3 um drop, 2.25 um Al-OH
            "Hydrated (any)",
            _limits(0.01, "BD1900r2", "D2300", "MIN2250"),
            (1000, 2600),
        ),
    )
}


def _bandSelection(
    wavelengthsNm: np.ndarray, window: tuple[float, float] | None
) -> np.ndarray:
    wavelengths = np.asarray(wavelengthsNm, dtype=np.float64)
    if window is None:
        return np.ones(wavelengths.shape, dtype=bool)
    low, high = min(window), max(window)
    return (wavelengths >= low) & (wavelengths <= high)


def maxAbsorptionDepth(
    spectrum: np.ndarray,
    wavelengthsNm: np.ndarray,
    window: tuple[float, float] | None = None,
) -> float:
    """Deepest dip of the continuum-removed spectrum below 1 (NaN bands and
    bands outside the window are ignored); NaN if fewer than 3 bands remain."""
    y = np.asarray(spectrum, dtype=np.float64)
    keep = _bandSelection(wavelengthsNm, window) & np.isfinite(y)
    if keep.sum() < 3:
        return float("nan")
    removed = continuumRemovedCube(
        y[keep].reshape(1, 1, -1), np.asarray(wavelengthsNm, dtype=np.float64)[keep]
    )
    return float(1 - np.nanmin(removed))


def relativeSlope(
    spectrum: np.ndarray,
    wavelengthsNm: np.ndarray,
    window: tuple[float, float] | None = None,
) -> float:
    """Least-squares slope of spectrum / its mean, per micrometer."""
    y = np.asarray(spectrum, dtype=np.float64)
    x = np.asarray(wavelengthsNm, dtype=np.float64) / 1000
    keep = _bandSelection(wavelengthsNm, window) & np.isfinite(y)
    if keep.sum() < 2:
        return float("nan")
    slope = np.polyfit(x[keep], y[keep], 1)[0]
    mean = y[keep].mean()
    return float(slope / mean) if mean != 0 else float("nan")


def genericBlandness(
    spectrum: np.ndarray,
    wavelengthsNm: np.ndarray,
    maxDepth: float = DEFAULT_MAX_DEPTH,
    maxSlope: float = DEFAULT_MAX_SLOPE,
    window: tuple[float, float] | None = None,
) -> BlandnessResult:
    depth = maxAbsorptionDepth(spectrum, wavelengthsNm, window)
    slope = abs(relativeSlope(spectrum, wavelengthsNm, window))
    return BlandnessResult(
        (
            CheckResult("Absorption depth", depth, maxDepth, bool(depth <= maxDepth)),
            CheckResult("Slope", slope, maxSlope, bool(slope <= maxSlope)),
        )
    )


def hypyrameterValues(
    spectrum: np.ndarray, wavelengthsNm: np.ndarray, names: Sequence[str]
) -> np.ndarray:
    """The named HyPyRameter parameters of one spectrum (raises ImportError
    when HyPyRameter is not installed)."""
    cube = np.asarray(spectrum, dtype=np.float64).reshape(1, 1, -1)
    return computeParameterCube(cube, wavelengthsNm, names).reshape(-1)


def presetBlandness(
    spectrum: np.ndarray,
    wavelengthsNm: np.ndarray,
    preset: Preset,
    parameters: ParameterFunction = hypyrameterValues,
) -> BlandnessResult:
    names = [limit.name for limit in preset.limits]
    try:
        values = parameters(spectrum, wavelengthsNm, names)
    except ImportError:
        return BlandnessResult((), note=_NEEDS_HYPYRAMETER)
    checks = tuple(
        CheckResult(
            limit.name, float(value), limit.maximum, bool(value <= limit.maximum)
        )
        for limit, value in zip(preset.limits, values)
    )
    slope = abs(relativeSlope(spectrum, wavelengthsNm, preset.slopeWindowNm))
    slopeCheck = CheckResult(
        "Slope", slope, preset.maxSlope, bool(slope <= preset.maxSlope)
    )
    return BlandnessResult((*checks, slopeCheck))


def _slopeMap(
    cube: np.ndarray, wavelengthsNm: np.ndarray, window: tuple[float, float] | None
) -> np.ndarray:
    """|relative slope| per micrometer of every pixel (rows, cols); NaN pixels
    are NaN."""
    keep = _bandSelection(wavelengthsNm, window)
    x = np.asarray(wavelengthsNm, dtype=np.float64)[keep] / 1000
    y = np.asarray(cube, dtype=np.float64)[:, :, keep]
    centered = x - x.mean()
    mean = y.mean(axis=2)
    with np.errstate(invalid="ignore", divide="ignore"):
        slope = (y * centered).sum(axis=2) / (centered**2).sum()
        return np.abs(slope / mean)


def genericBlandMask(
    cube: np.ndarray,
    wavelengthsNm: np.ndarray,
    maxDepth: float = DEFAULT_MAX_DEPTH,
    maxSlope: float = DEFAULT_MAX_SLOPE,
    window: tuple[float, float] | None = None,
) -> np.ndarray:
    """(rows, cols) bool: pixels whose spectrum passes both generic checks.
    Pixels with a NaN band are False."""
    keep = _bandSelection(wavelengthsNm, window)
    wavelengths = np.asarray(wavelengthsNm, dtype=np.float64)[keep]
    windowed = np.asarray(cube, dtype=np.float64)[:, :, keep]
    with np.errstate(invalid="ignore"):
        depth = 1 - continuumRemovedCube(windowed, wavelengths).min(axis=2)
        return (depth <= maxDepth) & (
            _slopeMap(cube, wavelengthsNm, window) <= maxSlope
        )


def presetBlandMask(
    cube: np.ndarray,
    wavelengthsNm: np.ndarray,
    preset: Preset,
    parameterCube: ParameterCubeFunction = computeParameterCube,
) -> np.ndarray:
    """(rows, cols) bool: pixels under every parameter maximum and the slope
    limit. NaN pixels are False."""
    names = [limit.name for limit in preset.limits]
    values = parameterCube(cube, wavelengthsNm, names)
    maxima = np.array([limit.maximum for limit in preset.limits])
    with np.errstate(invalid="ignore"):
        slope = _slopeMap(cube, wavelengthsNm, preset.slopeWindowNm)
        return (values <= maxima).all(axis=2) & (slope <= preset.maxSlope)
