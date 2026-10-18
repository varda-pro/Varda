"""Is a spectrum "bland" (featureless) enough to be a ratio denominator?

A check is a set of named wavelength windows. In each window the spectrum
(bands in an excluded range left out) is lightly smoothed, divided by its
upper convex hull (the continuum), and two things are measured:

- the **deepest dip**: 1 minus the smallest continuum-removed value, and the
  wavelength where it is;
- the **relative slope**: the least-squares slope of the (unsmoothed)
  spectrum over the window divided by its mean, per micrometer.

A window passes when its dip is no deeper than its depth limit and its
|slope| is within the slope limit. The depth limit is noise-aware: the larger
of an absolute floor and k times the spectrum's own per-band noise in that
window (a robust estimate from second differences between neighboring bands),
so a noisy spectrum is not failed for its noise alone. A spectrum is bland
for a check when every window passes.

Window choices follow the band centers of Viviano-Beck et al. (2014); the
excluded ranges and the defaults are explained, with the calibration that
chose the defaults, in docs/user-guide/crism-ratio.md. No Qt.
"""

from __future__ import annotations

import math
import re

import attrs
import numpy as np

from varda.analysis.continuum import continuumRemovedCube

GENERIC = "Generic"
# fewest bands a window needs: the hull, the 5-band smoothing and the noise
# estimate (second differences) all need several
MIN_WINDOW_BANDS = 5
# the dip is found on a running mean over this many neighboring bands (about
# 33 nm at CRISM's 6.55 nm spacing, well under the 50-200 nm width of the
# mineral bands looked for), so single-band noise spikes are not "dips"
SMOOTHING_BANDS = 5
# MAD to standard deviation, and the standard deviation of a second difference
# of independent noise is sqrt(6) times the per-band noise
_MAD_TO_SIGMA = 1.4826
_SECOND_DIFFERENCE_SCALE = math.sqrt(6.0)
_CHUNK_PIXELS = 1 << 15  # pixels per block when masking a cube


@attrs.frozen
class Window:
    """A named wavelength range, in nm."""

    name: str
    lowNm: float
    highNm: float


@attrs.frozen
class Check:
    name: str
    windows: tuple[Window, ...]


@attrs.frozen
class Limits:
    """The limits a window must stay within: its deepest dip may not exceed
    ``max(floor, noiseMultiple × noise)`` (fractions of the continuum), and its
    |relative slope| may not exceed ``maxSlope`` (per µm)."""

    floor: float
    noiseMultiple: float
    maxSlope: float

    def depthLimit(self, noise: float) -> float:
        return float(np.fmax(self.floor, self.noiseMultiple * noise))


# Calibrated on MTRDR FRT000107A7 (docs/user-guide/crism-ratio.md,
# "Calibration"). The readout tests the mean of a clicked box, the ratio cube
# a 9-pixel run mean in the column. They are kept apart so either can be
# tuned alone, but the scene gave no reason for different values: the noise
# term already follows each spectrum's own noise, the band-to-band noise of
# run means and box means was the same there (median 0.3 percent), and both
# passed at the same rates; a stricter floor for the ratio cube only pushed
# more columns to the whole-column fallback.
READOUT_LIMITS = Limits(floor=0.03, noiseMultiple=3.0, maxSlope=0.75)
RATIO_CUBE_LIMITS = Limits(floor=0.03, noiseMultiple=3.0, maxSlope=0.75)

LIMIT_RANGES = {  # valid ranges of the three limits
    "floor": (0.0, 1.0),
    "noiseMultiple": (0.0, 100.0),
    "maxSlope": (0.0, 10.0),
}

# Ranges no check looks at (nm). The CO2 residual is the 1.96/2.01/2.06 um
# atmospheric CO2 triplet that volcano-scan correction leaves behind (McGuire
# et al. 2009); 1000-1050 nm is the VNIR/IR detector join (the 1010-1047 nm
# gap in MTRDR band centers: the IR detector's first bands, Murchie et al.
# 2007, 2009); beyond 2650 nm lie the IR order-sorting filter boundary near
# 2.7 um (the 2655-2800 nm gap in MTRDR band centers), the 2.7 um CO2 band, the
# 3 um H2O band, and thermal emission. 4000 nm is past CRISM's last band.
CO2_RESIDUAL_NM = (1950.0, 2100.0)
DETECTOR_JOIN_NM = (1000.0, 1050.0)
BEYOND_NM = (2650.0, 4000.0)
DEFAULT_EXCLUSIONS: tuple[tuple[float, float], ...] = (
    DETECTOR_JOIN_NM,
    CO2_RESIDUAL_NM,
    BEYOND_NM,
)

# Windows, with the Viviano-Beck et al. (2014) parameters whose band centers
# and shoulders they follow. Every window is measured outside the excluded
# ranges, so a window that crosses one (the 1.9 um window crosses the CO2
# residual) is fitted on both sides of the gap, as BD1900r2 is. Windows reach
# past each band's shoulders on both sides: the hull is anchored at a window's
# ends, so a window that stopped inside a band's wing would see only part of
# its depth. Windows may overlap.
_MAFIC_1UM = Window("1 µm mafic", 1050, 1700)  # olivine 1.05-1.7 um composite
# band and the long-wavelength wing of the pyroxene 1 um band; the IR detector
# begins at 1.05 um (OLINDEX3 bands 1.08-1.47 um, LCPINDEX2/HCPINDEX2 shoulder
# at 1.56-1.69 um)
_OLIVINE_1UM = Window("1 µm olivine", 1050, 1800)  # OLINDEX3, out to its
# 1.75-1.86 um shoulder, so the broad olivine band is seen whole
_PYROXENE_2UM = Window("2 µm pyroxene", 1700, 2550)  # LCP 1.81-1.87 um
# (LCPINDEX2) and HCP 2.12-2.46 um (HCPINDEX2) bands
_H2O_1_4 = Window("1.4 µm OH/H₂O", 1300, 1560)  # BD1400: the 1.4 um OH/H2O overtone
_H2O_1_9 = Window("1.9 µm H₂O", 1840, 2140)  # BD1900r2: band 1908-1941 nm,
# shoulders 1862-1875 and 2112-2126 nm (the CO2 range in between is excluded)
_ALOH_2_2 = Window("2.2 µm Al-OH", 2120, 2320)  # BD2210_2, BD2190, MIN2250:
# Al-OH and Si-OH 2.16-2.26 um, shoulders 2.12-2.29 um
_FEMG_2_3 = Window("2.3 µm Fe/Mg-OH", 2120, 2400)  # D2300, BD2290: Fe/Mg-OH
# 2.28-2.33 um, shoulders 2.12-2.17 and ~2.40 um
_FEMG_2_4 = Window("2.4 µm Fe/Mg-OH", 2340, 2520)  # the 2.39-2.41 um Fe/Mg-OH
# combination band and MIN2295_2480's 2.48 um minimum
_METALOH = Window("2.2–2.3 µm metal-OH", 2120, 2400)  # D2300, BD2210_2, MIN2250
_CARB_2_3 = Window("2.3 µm carbonate", 2200, 2420)  # BDCARB / MIN2345_2537:
# Mg and Ca carbonate 2.30-2.34 um
_CARB_2_5 = Window("2.5 µm carbonate", 2390, 2620)  # BDCARB: 2.50-2.54 um,
# shoulder 2.60 um
_SULF_2_1 = Window("2.1 µm sulfate", 2100, 2260)  # BD2100_2: monohydrated
# sulfate 2.10-2.13 um; its short-wavelength wing is in the CO2 range
_SULF_2_4 = Window("2.4 µm sulfate", 2330, 2600)  # SINDEX2: the polyhydrated
# sulfate 2.4 um drop

CHECKS: dict[str, Check] = {
    check.name: check
    for check in (
        # broad windows over the IR range the presets use, outside the
        # exclusions: anything a mineral check would see shows here too
        Check(
            GENERIC,
            (Window("1.05–1.7 µm", 1050, 1700), Window("1.7–2.65 µm", 1700, 2650)),
        ),
        # a plagioclase ratio is spoiled by mafic bands in the denominator
        Check("Plagioclase", (_MAFIC_1UM, _PYROXENE_2UM)),
        # olivine's own band, and pyroxene, whose bands overlap it
        Check("Olivine", (_OLIVINE_1UM, _PYROXENE_2UM)),
        Check("Fe/Mg phyllosilicates", (_H2O_1_9, _FEMG_2_3, _FEMG_2_4)),
        Check("Al phyllosilicates", (_H2O_1_9, _ALOH_2_2)),
        Check("Carbonates", (_CARB_2_3, _CARB_2_5)),
        Check("Sulfates", (_H2O_1_9, _SULF_2_1, _SULF_2_4)),
        Check("Hydrated (any)", (_H2O_1_4, _H2O_1_9, _METALOH)),
    )
}


@attrs.frozen
class WindowResult:
    window: Window
    bands: int  # bands measured (in the window, not excluded, finite)
    depth: float  # deepest continuum-removed dip, fraction (NaN: not measured)
    dipNm: float  # where it is
    noise: float  # per-band noise, fraction of the spectrum's mean
    depthLimit: float
    slope: float  # relative slope per µm, signed
    maxSlope: float

    @property
    def depthPassed(self) -> bool:
        return bool(self.depth <= self.depthLimit)

    @property
    def slopePassed(self) -> bool:
        return bool(abs(self.slope) <= self.maxSlope)

    @property
    def passed(self) -> bool:
        return self.depthPassed and self.slopePassed


@attrs.frozen
class BlandnessResult:
    check: str
    windows: tuple[WindowResult, ...] = ()
    limits: Limits | None = None
    exclusions: tuple[tuple[float, float], ...] = ()
    note: str = ""

    @property
    def passed(self) -> bool:
        return bool(self.windows) and all(w.passed for w in self.windows)


def _selection(
    wavelengthsNm: np.ndarray,
    window: Window,
    exclusions: tuple[tuple[float, float], ...],
) -> np.ndarray:
    x = np.asarray(wavelengthsNm, dtype=np.float64)
    keep = (x >= window.lowNm) & (x <= window.highNm)
    for low, high in exclusions:
        keep &= ~((x >= min(low, high)) & (x <= max(low, high)))
    return keep


def windowBandCounts(
    wavelengthsNm: np.ndarray,
    check: Check,
    exclusions: tuple[tuple[float, float], ...] = DEFAULT_EXCLUSIONS,
) -> dict[str, int]:
    """How many bands each window of ``check`` would measure."""
    return {
        w.name: int(_selection(wavelengthsNm, w, exclusions).sum())
        for w in check.windows
    }


def _segments(x: np.ndarray) -> np.ndarray:
    """A segment id per band: a new segment starts after a gap of more than
    1.5 band spacings (an excluded range or a sensor gap)."""
    if x.size < 2:
        return np.zeros(x.size, dtype=np.int64)
    step = np.diff(x)
    return np.concatenate([[0], np.cumsum(step > 1.5 * np.median(step))])


def _smooth(values: np.ndarray, segments: np.ndarray) -> np.ndarray:
    """Running mean over SMOOTHING_BANDS bands within each segment (fewer at
    a segment's ends). values: (pixels, bands)."""
    out = np.empty_like(values)
    half = SMOOTHING_BANDS // 2
    for s in np.unique(segments):
        idx = np.flatnonzero(segments == s)
        part = values[:, idx]
        n = idx.size
        sums = np.concatenate(
            [np.zeros((part.shape[0], 1)), np.cumsum(part, axis=1)], axis=1
        )
        start = np.clip(np.arange(n) - half, 0, n)
        stop = np.clip(np.arange(n) + half + 1, 0, n)
        out[:, idx] = (sums[:, stop] - sums[:, start]) / (stop - start)
    return out


def _noise(relative: np.ndarray, segments: np.ndarray) -> np.ndarray:
    """Per-band noise of each row: the MAD of the second differences between
    neighboring bands (inside a segment), as a standard deviation."""
    same = (segments[:-2] == segments[1:-1]) & (segments[1:-1] == segments[2:])
    if same.sum() < 3:
        return np.full(relative.shape[0], np.nan)
    second = (relative[:, :-2] - 2 * relative[:, 1:-1] + relative[:, 2:])[:, same]
    center = np.median(second, axis=1, keepdims=True)
    mad = np.median(np.abs(second - center), axis=1)
    return _MAD_TO_SIGMA * mad / _SECOND_DIFFERENCE_SCALE


def _measure(
    values: np.ndarray, x: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(depth, dipNm, noise, slope) of each row of ``values`` (pixels, bands),
    bands at ``x`` nm ascending. Rows with a NaN band give NaN."""
    segments = _segments(x)
    smoothed = _smooth(values, segments)
    removed = continuumRemovedCube(smoothed[:, None, :], x)[:, 0, :]
    with np.errstate(invalid="ignore", divide="ignore"):
        depth = 1 - removed.min(axis=1)
        dip = x[np.argmin(np.where(np.isnan(removed), np.inf, removed), axis=1)]
        dip = np.where(np.isfinite(depth), dip, np.nan)
        mean = values.mean(axis=1)
        noise = _noise(values / mean[:, None], segments)
        micrometers = x / 1000
        centered = micrometers - micrometers.mean()
        slope = (values * centered).sum(axis=1) / (centered**2).sum() / mean
    return depth, dip, noise, slope


def assess(
    spectrum: np.ndarray,
    wavelengthsNm: np.ndarray,
    check: Check,
    limits: Limits,
    exclusions: tuple[tuple[float, float], ...] = DEFAULT_EXCLUSIONS,
) -> BlandnessResult:
    """Every window of ``check`` measured on one spectrum (NaN bands are left
    out). A window with fewer than MIN_WINDOW_BANDS bands is not measured
    (NaN) and fails, and the note says so."""
    y = np.asarray(spectrum, dtype=np.float64)
    x = np.asarray(wavelengthsNm, dtype=np.float64)
    finite = np.isfinite(y) & np.isfinite(x)
    results = []
    short = []
    for window in check.windows:
        keep = _selection(x, window, exclusions) & finite
        order = np.argsort(x[keep])
        xs, ys = x[keep][order], y[keep][order]
        if xs.size < MIN_WINDOW_BANDS:
            short.append(window.name)
            depth = dip = noise = slope = float("nan")
        else:
            measured = _measure(ys[None, :], xs)
            depth, dip, noise, slope = (float(a[0]) for a in measured)
        results.append(
            WindowResult(
                window,
                int(xs.size),
                depth,
                dip,
                noise,
                limits.depthLimit(noise),
                slope,
                limits.maxSlope,
            )
        )
    note = (
        f"Too few bands to measure {', '.join(short)} (at least "
        f"{MIN_WINDOW_BANDS} needed)."
        if short
        else ""
    )
    return BlandnessResult(check.name, tuple(results), limits, exclusions, note)


def blandMask(
    cube: np.ndarray,
    wavelengthsNm: np.ndarray,
    check: Check,
    limits: Limits,
    exclusions: tuple[tuple[float, float], ...] = DEFAULT_EXCLUSIONS,
) -> np.ndarray:
    """(rows, cols) bool: pixels whose spectrum passes every window of
    ``check``, as :func:`assess` would judge them. A pixel with a NaN band in
    a window, or a window with too few bands, is False."""
    rows, cols, bands = cube.shape
    flat = cube.reshape(-1, bands)
    x = np.asarray(wavelengthsNm, dtype=np.float64)
    passed = np.ones(rows * cols, dtype=bool)
    for window in check.windows:
        keep = np.flatnonzero(_selection(x, window, exclusions))
        if keep.size < MIN_WINDOW_BANDS:
            return np.zeros((rows, cols), dtype=bool)
        keep = keep[np.argsort(x[keep])]
        for start in range(0, rows * cols, _CHUNK_PIXELS):
            stop = min(start + _CHUNK_PIXELS, rows * cols)
            todo = np.flatnonzero(passed[start:stop]) + start
            if todo.size == 0:
                continue
            depth, _, noise, slope = _measure(
                flat[np.ix_(todo, keep)].astype(np.float64), x[keep]
            )
            with np.errstate(invalid="ignore"):
                limit = np.fmax(limits.floor, limits.noiseMultiple * noise)
                passed[todo] = (depth <= limit) & (np.abs(slope) <= limits.maxSlope)
    return passed.reshape(rows, cols)


# --- the excluded ranges as text, for a text field --------------------------------

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_RANGE = re.compile(rf"^\s*({_NUMBER})\s*(?:–|-|to)\s*({_NUMBER})\s*$")


def formatExclusions(exclusions: tuple[tuple[float, float], ...]) -> str:
    """e.g. ``1000–1050, 1950–2100, 2650–4000`` (nm)."""
    return ", ".join(f"{low:g}–{high:g}" for low, high in exclusions)


def parseExclusions(text: str) -> tuple[tuple[float, float], ...]:
    """Ranges in nm written like ``1950–2100, 2650-4000`` (empty: none);
    ValueError says what is wrong."""
    ranges = []
    for part in filter(str.strip, text.replace(";", ",").split(",")):
        match = _RANGE.match(part.replace("nm", ""))
        if match is None:
            raise ValueError(
                f"Excluded ranges: '{part.strip()}' is not like '1950–2100' (nm)."
            )
        low, high = (float(g) for g in match.groups())
        if not (math.isfinite(low) and math.isfinite(high) and 0 <= low < high):
            raise ValueError(
                f"Excluded ranges: '{part.strip()}' needs 0 ≤ start < end."
            )
        ranges.append((low, high))
    return tuple(ranges)


def describeSettings(
    check: Check, limits: Limits, exclusions: tuple[tuple[float, float], ...]
) -> str:
    """The check's windows, limits and exclusions as one line, for records."""
    windows = ", ".join(f"{w.name} {w.lowNm:g}–{w.highNm:g} nm" for w in check.windows)
    excluded = formatExclusions(exclusions) or "none"
    return (
        f"{check.name} check (windows {windows}; excluded {excluded} nm; a "
        f"window's deepest continuum-removed dip ≤ max({limits.floor:g}, "
        f"{limits.noiseMultiple:g} × its per-band noise) and |relative slope| ≤ "
        f"{limits.maxSlope:g} per µm)"
    )
