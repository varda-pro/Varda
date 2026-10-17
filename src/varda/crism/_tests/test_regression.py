"""Phase 1's definition of done: the spec's V2 and V3 ("Validation design",
the tolerances Michael approved as D4) on the demo data; skipped without it.

- **V2:** FRT00005850, processed with the default recipe, against its
  official MTRDR (``frt00005850_07_if167j_mtr3``). HRL000040FF has no TRR3 in
  the demo data, so it is not in V2.
- **V3:** the rim FRSs (FRS00031442, FRS0002FE75, FRS00031260): their CO2
  residual, and FRS00031442's offsets from the crater-floor MTRDRs it overlaps
  (by 48,488, 22,627 and 46,057 18 m pixels with FRT00005C5E, FRT000047A3 and
  HRL000040FF, so ruling P4's fallback is not needed); plus the artifact basis
  (ledger A6): its leave-one-out check under each scan the pipeline chooses,
  and its effect on the mineral parameters.

Every test asserts an approved tolerance as written. Where one is not met,
the test is ``xfail(strict=True)`` with its measured cause, and an
``..._achieved`` test pins what is met, so a regression still fails.
``docs/superpowers/specs/crism/phase1-validation.md`` has every number, the
attribution and the decisions the failures need.

The V3 overlap metrics are the fusion prototype's
(``data-for-paper/fusion_prototype/code``, 2026-10-15), ported here:
``stage1_grid.py`` (the 36 m grid snapped to HRL000040FF, area-averaged
regridding, tiled phase correlation and the trimmed affine fit, registered in
a chain), ``fusion_core.py`` (``gain_solve``, ``block_mean``,
``project_out``, ``pair_noise``, ``three_cornered_hat``, ``smooth_noise``),
``stage2b_diag.py`` (the shape χ²) and ``stage3_figs.py`` (band-depth
repeatability: the median over pixels with three or more looks of the
standard deviation across them). On the five crater-floor MTRDRs the port
reproduces the prototype's registration residuals (0.559, 0.236, 1.070,
0.587 px), its BD2300 repeatability (0.00139 against 0.00140) and its BD1900
repeatability (0.00162 against 0.00162).

Processing the four observations (seven runs) takes about 7 minutes; each
module fixture runs once.
"""

import itertools
import json
import math
import os
import re
import warnings
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from attrs import frozen
from numpy.lib.stride_tricks import sliding_window_view
from rasterio.warp import Resampling, reproject
from scipy.ndimage import uniform_filter1d
from scipy.optimize import nnls
from skimage.registration import phase_cross_correlation

from varda.crism import validation_report as report
from varda.crism.atmosphere import (
    ARTIFACT_EPOCH_MARGIN_SCLK,
    AtmosphereModel,
    ArtifactBasis,
    artifactScale,
    bundledArtifactBasis,
    learnArtifactBasis,
    shiftedLogTransmission,
)
from varda.crism.calibration import CalibrationLibrary
from varda.crism.observation import CrismObservation
from varda.crism.pipeline import (
    ColumnResiduals,
    ProcessResult,
    columnResiduals,
    process,
)
from varda.crism.readers import DdrBand, readDdr, readTrr3
from varda.crism.recipe import Recipe
from varda.crism.validation import (
    CO2_WINDOW_NM,
    co2Residual,
    compareMaps,
    geolocationOffset,
    sceneResidualRms,
    shapeRatio,
    summaryParameter,
)
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource

REPO_ROOT = Path(__file__).resolve().parents[4]
FRT = "FRT00005850"
FRS = ("FRS00031442", "FRS0002FE75", "FRS00031260")
PARAMETERS = ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310")
CRATER_FLOOR = {  # the prototype's order: FRT00005C5E is its reference
    "FRT00005C5E": "FRT00005C5E/mtrdr/frt00005c5e_07_if166j_mtr3.img",
    "FRT000047A3": "FRT000047A3/mtrdr/frt000047a3_07_if166j_mtr3.img",
    "FRT000066A4": "FRT000066A4/mtrdr/frt000066a4_07_if166j_mtr3.img",
    "FRT0001FB74": "FRT0001FB74/mtrdr/frt0001fb74_07_if166j_mtr3.img",
    "HRL000040FF": "HRL000040FF/mtrdr/hrl000040ff_07_if183j_mtr3.img",
}
MTRDR_5850 = "FRT00005850/mtrdr/frt00005850_07_if167j_mtr3.img"
MTRDR_5850_IN = "FRT00005850/mtrdr/frt00005850_07_in167j_mtr3.img"
_MTRDR_IR_SAMPLE, _MTRDR_IR_LINE = 5, 6  # the MTRDR IN bands "IR Sample", "IR Line"

# --- the approved tolerances (spec V2, V3) ------------------------------------------

TIE_M = 0.01
GEOLOCATION_PX = 0.25
SHAPE_RANGE_NM = (1050.0, 2600.0)
SHAPE_CO2_NM = (1940.0, 2100.0)
SHAPE_TOLERANCE = 0.01
SHAPE_CO2_TOLERANCE = 0.02
PEARSON = 0.90
PROTOTYPE_REPEATABILITY = {"BD2310": 0.0014, "BD1900_2": 0.0016}  # M9
FRS_CO2_MEDIAN = 0.0083
FRS_CO2_SCENE = 0.0080
BD2310_OFFSET = 0.004
LEAVE_ONE_OUT_REDUCTION = 0.30

# The FRSs' choices with the default recipe (PDS ATs) and their bundled bases.
CHOSEN_SCANS = {
    "FRS00031442": "061C4:AT",
    "FRS0002FE75": "0A93E:AT",
    "FRS00031260": "08608:AT",
}

# --- causes of the criteria that are not met (measured; phase1-validation.md) --------

_PEARSON_CAUSE = (
    "DECISION PENDING, inside Phase 1: 52-91% of the difference's variance is "
    "cross-track (per detector column); ours has 2-4x the MTRDR's column-to-"
    "column scatter; the per-column spectral factor ours/MTRDR reproduces "
    "89-94% of it and is band-structured, not smooth (EGN-like smooth factors "
    "explain <= 9%); not noise (pixel-scale fluctuations are shared, r rises "
    "<= 0.03 at 8x8 block means). Toggles: destripe on +0.01..+0.04, no fitted "
    "shift +0.01..+0.08 (CO2 0.0067 -> 0.0123), ESC off -0.05..-0.14; none "
    "reaches 0.90 (destripe + no shift: 0.804/0.837/0.734/0.908)"
)
_PEARSON_CAUSES = {
    "D2300": _PEARSON_CAUSE,
    "BD2290": _PEARSON_CAUSE,
    "BD1900_2": _PEARSON_CAUSE,
    "BD2310": _PEARSON_CAUSE,
    "OLINDEX3": (
        "EGN (outside Phase 1): 92% of the difference's variance is along "
        "track and its line profile follows the phase angle (r = 0.95, phase "
        "33-66 deg along the FRT's gimbal scan); with the column and line "
        "components removed r = 0.999"
    ),
}
_BIAS_CAUSES = {
    "D2300": (
        "DECISION PENDING, inside Phase 1: the cross-track structure behind "
        "the Pearson shortfall; the per-column median difference is -0.0020, ours "
        "0.5-1% low at 2120 and 2170 nm on columns > 480"
    ),
}
_SHAPE_CAUSE = (
    "DECISION PENDING, inside Phase 1 (ledger A6/A7): a 2 um bowl, ours up to "
    "3.4% below the MTRDR at 2033-2093 nm and 1-2.4% at 1875-1940 and "
    "2100-2210 nm; ln(shape ratio) correlates -0.81/-0.87 with CAT's VS ADR "
    "artifact spectra (11D87, McGuire/Pelkey), the patch the MTRDR has and a "
    "high-passed learned basis cannot hold; not EGN (no trend with the phase "
    "angle along track at 2080 nm). Beyond 2.2 um a 1.0-1.35% offset varies "
    "with phase (r = -0.72 at 2400 nm): EGN"
)
_OFFSET_CAUSE = (
    "DECISION PENDING: FRS00031442's BD2310 is 0.0055 and 0.0051 above "
    "FRT00005C5E's and FRT000047A3's in their overlaps (0.0004 against "
    "HRL000040FF's); the same without the artifact basis (0.0057, 0.0053) and "
    "with a leave-one-out one; our pipeline's BD2310 bias against the MTRDR on "
    "FRT00005850 is -0.0002 (V2), so it is between observations, not "
    "attributed to a Phase 1 step; the MTRDR pairs themselves reach 0.0045 "
    "(FRT00005C5E-HRL000040FF), above the spec's 0.004"
)
_MINERAL_CAUSES = {
    ("FRS0002FE75", "D2300"): (
        "DECISION PENDING, inside Phase 1 (A6): the 0A93E basis moves D2300's "
        "median by -0.00177 (leave-one-out basis -0.00168), above the "
        "repeatability 0.00152"
    ),
    ("FRS00031260", "BD1900_2"): (
        "DECISION PENDING, inside Phase 1 (A6): the 08608 basis moves "
        "BD1900_2's median by +0.0035 (leave-one-out basis +0.0031), above "
        "the repeatability 0.0019; it moves the FRS toward two of the three "
        "overlapping MTRDRs and away from the third"
    ),
}


def _xfailIf(cause: str | None, values: object) -> object:
    marks = [pytest.mark.xfail(strict=True, reason=cause)] if cause else []
    return pytest.param(values, marks=marks, id=str(values))


# --- data and products --------------------------------------------------------------


def _refuse(url: str, target: Path) -> None:
    raise OSError(f"offline (test): {url}")


@pytest.fixture(scope="module")
def paper() -> Path:
    """``data-for-paper`` in the demo data (``VARDA_DEMO_DATA``); skips without it."""
    folder = Path(
        os.environ.get("VARDA_DEMO_DATA", REPO_ROOT.parent / "varda-demo-data")
    )
    paper = folder / "data-for-paper"
    needed = [paper / FRT / "trdr", paper / MTRDR_5850, *(paper / n for n in FRS)]
    missing = [str(p) for p in needed if not p.exists()]
    if missing:
        raise pytest.skip.Exception(f"demo data not found: {missing[0]}")
    return paper


def _library(paper: Path, name: str, cache: Path) -> CalibrationLibrary:
    """The observation's own folder, the local ATs and WA, and CAT's aux files
    (FRT00005850's WA is only there); nothing is downloaded."""
    return CalibrationLibrary(
        [paper / "ATO0004EC45", paper / name],
        cache,
        paper / "CAT_v7_4_aux/aux_files",
        fetch=_refuse,
    )


def _process(
    paper: Path, name: str, folder: Path, artifactModel: bool = True
) -> ProcessResult:
    recipe = Recipe()
    recipe.atmosphere.artifactModel.set(artifactModel)
    return process(
        CrismObservation.fromFolder(paper / name),
        recipe,
        folder,
        library=_library(paper, name, folder / "cache"),
    )


@pytest.fixture(scope="module")
def frt5850(paper: Path, tmp_path_factory: pytest.TempPathFactory) -> ProcessResult:
    return _process(paper, FRT, tmp_path_factory.mktemp("frt5850"))


@pytest.fixture(scope="module")
def frsProducts(
    paper: Path, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, tuple[ProcessResult, ProcessResult]]:
    """Each FRS with the default recipe and with the artifact model off."""
    return {
        name: (
            _process(paper, name, tmp_path_factory.mktemp(name.lower())),
            _process(
                paper, name, tmp_path_factory.mktemp(f"{name.lower()}_nobasis"), False
            ),
        )
        for name in FRS
    }


def _mapped(result: ProcessResult) -> Path:
    assert result.mapProjected is not None
    return result.mapProjected


# --- V2: FRT00005850 against its MTRDR ----------------------------------------------


@frozen(eq=False)
class _V2:
    """The overlap of ours and the MTRDR (validation_report's own blockwise
    comparison) and the source pixels of both."""

    comparison: report._Comparison
    oursSample: np.ndarray  # (h, w) detector column of each map pixel
    oursLine: np.ndarray
    mtrdrSample: np.ndarray
    phaseByLine: np.ndarray  # the DDR phase angle, median over each line


@pytest.fixture(scope="module")
def v2(paper: Path, frt5850: ProcessResult) -> _V2:
    ours = ENVIDataSource(str(_mapped(frt5850)))
    reference = ENVIDataSource(str(paper / MTRDR_5850))
    info = ENVIDataSource(str(frt5850.info))
    mtrdrInfo = ENVIDataSource(str(paper / MTRDR_5850_IN))
    try:
        window = report._overlap(ours, reference)
        comparison = report._compare(ours, reference, window)
        dr, dc = window.shift
        rows = slice(window.rows.start - dr, window.rows.stop - dr)
        cols = slice(window.cols.start - dc, window.cols.stop - dc)
        source = report._read(info, window.rows, window.cols, np.array([0, 1]))
        theirs = report._read(mtrdrInfo, rows, cols, np.array([_MTRDR_IR_SAMPLE]))
    finally:
        for source_ in (ours, reference, info, mtrdrInfo):
            source_.close()
    theirs[theirs >= 65534] = np.nan
    ddr = readDdr(paper / FRT / "ddr/frt00005850_07_de167l_ddr1.lbl")
    phase = ddr.band(DdrBand.PHASE)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        phaseByLine = np.nanmedian(np.where(phase > -1000, phase, np.nan), axis=1)
    return _V2(comparison, source[..., 0], source[..., 1], theirs[..., 0], phaseByLine)


def _header(path: Path) -> str:
    return path.with_suffix(".hdr").read_text()


def _mapInfo(path: Path) -> list[str]:
    match = re.search(r"map info = \{([^}]*)\}", _header(path))
    assert match is not None, path
    return [value.strip() for value in match.group(1).split(",")]


def test_frt5850_grid_identical(paper: Path, frt5850: ProcessResult) -> None:
    """V2: the same φ_P, R and pixel size as the MTRDR; the tie point within
    0.01 m (measured: identical, 4417887.6, 1091944.9)."""
    ours, theirs = _mapped(frt5850), paper / MTRDR_5850
    with rasterio.open(ours) as a, rasterio.open(theirs) as b:
        projectionA, projectionB = a.crs.to_dict(), b.crs.to_dict()
        assert projectionA["proj"] == projectionB["proj"] == "eqc"
        assert projectionA["lat_ts"] == projectionB["lat_ts"] == 15
        assert projectionA["R"] == pytest.approx(projectionB["R"], abs=1e-6)
        assert a.res == b.res == (18.0, 18.0)
    infoA, infoB = _mapInfo(ours), _mapInfo(theirs)
    assert infoA[0] == infoB[0]  # the projection's name, φ_P in it
    assert abs(float(infoA[3]) - float(infoB[3])) <= TIE_M
    assert abs(float(infoA[4]) - float(infoB[4])) <= TIE_M


def _tiles(image: np.ndarray, size: int = 128) -> Iterator[tuple[slice, slice]]:
    for r in range(0, image.shape[0] - size + 1, size):
        for c in range(0, image.shape[1] - size + 1, size):
            yield slice(r, r + size), slice(c, c + size)


def test_frt5850_geolocation(v2: _V2) -> None:
    """V2: phase correlation of the 1.3–1.7 µm albedo images, the median over
    128 px tiles at most 0.25 px in each direction (measured over the whole
    overlap: −0.14, −0.08 px)."""
    ours, theirs = v2.comparison.albedo
    shifts = [
        geolocationOffset(ours[tile], theirs[tile])
        for tile in _tiles(ours)
        if np.isfinite(ours[tile]).all() and np.isfinite(theirs[tile]).all()
    ]
    assert len(shifts) >= 10
    dx, dy = np.median(np.array(shifts), axis=0)
    assert abs(dx) <= GEOLOCATION_PX and abs(dy) <= GEOLOCATION_PX
    whole = geolocationOffset(ours, theirs)
    assert abs(whole[0]) <= GEOLOCATION_PX and abs(whole[1]) <= GEOLOCATION_PX


def _shapeRatio(v2: _V2) -> tuple[np.ndarray, np.ndarray]:
    c = v2.comparison
    good = c.good
    ratio = shapeRatio(
        c.samples[0][:, good], c.samples[1][:, good], c.wavelengths[good]
    )
    return c.wavelengths[good], ratio


def _shapeTolerance(wavelengths: np.ndarray) -> np.ndarray:
    inside = (wavelengths >= SHAPE_CO2_NM[0]) & (wavelengths <= SHAPE_CO2_NM[1])
    return np.where(inside, SHAPE_CO2_TOLERANCE, SHAPE_TOLERANCE)


@pytest.mark.xfail(strict=True, reason=_SHAPE_CAUSE)
def test_frt5850_shape_ratio(v2: _V2) -> None:
    """V2: per band, the median of (ours ÷ its 1.3–1.7 µm mean) ÷ (MTRDR ÷ its
    1.3–1.7 µm mean) within ±1% over 1.05–2.6 µm, and ±2% inside
    1.94–2.10 µm."""
    wavelengths, ratio = _shapeRatio(v2)
    inRange = (wavelengths >= SHAPE_RANGE_NM[0]) & (wavelengths <= SHAPE_RANGE_NM[1])
    off = np.abs(ratio - 1) > _shapeTolerance(wavelengths)
    assert not (off & inRange).any(), wavelengths[off & inRange]


def test_frt5850_shape_ratio_achieved(v2: _V2) -> None:
    """What is met (measured): ±1% over 1.05–1.85 µm (max 0.76%); the bowl's
    regions at most 2.0% (1.85–1.94 µm), 3.4% (1.94–2.10 µm), 2.4%
    (2.10–2.20 µm) and 1.35% (2.2–2.6 µm). Pinned at the measured maxima
    plus 0.2%: a regression bound, not a tolerance."""
    wavelengths, ratio = _shapeRatio(v2)
    for (low, high), bound in (
        ((1050.0, 1850.0), SHAPE_TOLERANCE),
        ((1850.0, 1940.0), 0.022),
        ((1940.0, 2100.0), 0.036),
        ((2100.0, 2200.0), 0.026),
        ((2200.0, 2600.0), 0.0155),
    ):
        region = (wavelengths >= low) & (wavelengths <= high)
        assert np.abs(ratio[region] - 1).max() <= bound, (low, high)


def test_frt5850_co2_not_worse(v2: _V2) -> None:
    """V2: our CO2 residual (the M7 metric, the same code) at most the
    MTRDR's on the same pixels: per-pixel median (measured 0.00665 against
    0.00824) and the scene-median residual (0.0061 against 0.0080)."""
    c = v2.comparison
    ours, theirs = (float(np.nanmedian(m)) for m in c.co2)
    assert theirs == pytest.approx(0.0082, abs=0.0001)  # M7's MTRDR value
    assert ours <= theirs
    inside = (c.wavelengths > CO2_WINDOW_NM[0]) & (c.wavelengths < CO2_WINDOW_NM[1])
    inside &= c.good
    sceneOurs, sceneTheirs = (
        sceneResidualRms(c.wavelengths[inside], s[:, inside]) for s in c.samples
    )
    assert sceneOurs <= sceneTheirs


@pytest.mark.parametrize(
    "name", [_xfailIf(_PEARSON_CAUSES.get(n), n) for n in PARAMETERS]
)
def test_frt5850_parameter_pearson(v2: _V2, name: str) -> None:
    """V2: Pearson r of each parameter map with the MTRDR's at least 0.90
    (measured: D2300 0.785, BD2290 0.774, BD1900_2 0.627, OLINDEX3 0.895,
    BD2310 0.883)."""
    ours, theirs = v2.comparison.parameters[name]
    assert compareMaps(ours, theirs).pearson >= PEARSON


@pytest.fixture(scope="module")
def repeatability(craterFloor: "_Stack") -> dict[str, float]:
    """The crater-floor MTRDRs' between-observation repeatability of each
    parameter (``stage3_figs.py``, the five MTRDRs only, their own gains).
    Measured: BD2310 0.00139, D2300 0.00152, BD2290 0.00103, BD1900_2 0.00189,
    OLINDEX3 0.00595."""
    members = list(range(len(CRATER_FLOOR)))
    gains = _subsetGains(craterFloor, members)
    return {
        name: _repeatability(_parameterStack(craterFloor, members, gains, name))
        for name in PARAMETERS
    }


def test_repeatability_reproduces_the_prototype(
    repeatability: dict[str, float],
) -> None:
    """The port gives the prototype's BD2300 (our BD2310) repeatability, M9."""
    assert repeatability["BD2310"] == pytest.approx(
        PROTOTYPE_REPEATABILITY["BD2310"], abs=0.0001
    )


def _biasBound(name: str, repeatability: dict[str, float]) -> float:
    return PROTOTYPE_REPEATABILITY.get(name, repeatability[name])


@pytest.mark.parametrize("name", [_xfailIf(_BIAS_CAUSES.get(n), n) for n in PARAMETERS])
def test_frt5850_parameter_bias(
    v2: _V2, repeatability: dict[str, float], name: str
) -> None:
    """V2: the median of ours − MTRDR at most 0.0014 (BD2310) and 0.0016
    (BD1900_2), and for the others at most the crater-floor repeatability
    measured here with the same code (measured bias: D2300 −0.0019 against
    0.00152, BD2290 +0.0002 against 0.00103, BD1900_2 −0.0004, OLINDEX3
    −0.0038 against 0.00595, BD2310 −0.0002)."""
    ours, theirs = v2.comparison.parameters[name]
    assert abs(compareMaps(ours, theirs).medianBias) <= _biasBound(name, repeatability)


def _blockMeans(a: np.ndarray, f: int) -> np.ndarray:
    rows, cols = a.shape[0] // f * f, a.shape[1] // f * f
    return a[:rows, :cols].reshape(rows // f, f, cols // f, f).mean(axis=(1, 3))


def _groupMedian(keys: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(keys, kind="stable")
    unique, first = np.unique(keys[order], return_index=True)
    groups = np.split(values[order], first[1:])
    return unique, np.array([np.median(g) for g in groups])


@frozen
class _Decomposition:
    columnFraction: float  # of var(ours − MTRDR): the per-detector-column medians
    lineFraction: float  # after the column part: the per-line medians
    pearsonColumnRemoved: float
    pearsonBothRemoved: float
    linePhaseCorrelation: float


def _decompose(v2: _V2, name: str) -> _Decomposition:
    ours, theirs = v2.comparison.parameters[name]
    ok = np.isfinite(ours) & np.isfinite(theirs) & np.isfinite(v2.oursSample)
    a, b = ours[ok], theirs[ok]
    columns = np.round(v2.oursSample[ok]).astype(int)
    lines = np.round(v2.oursLine[ok]).astype(int)
    d = a - b
    unique, byColumn = _groupMedian(columns, d)
    column = byColumn[np.searchsorted(unique, columns)]
    rest = d - column
    uniqueLines, byLine = _groupMedian(lines, rest)
    line = byLine[np.searchsorted(uniqueLines, lines)]
    total = float(np.var(d))
    return _Decomposition(
        1 - float(np.var(rest)) / total,
        (float(np.var(rest)) - float(np.var(rest - line))) / total,
        compareMaps(a - column, b).pearson,
        compareMaps(a - column - line, b).pearson,
        float(np.corrcoef(byLine, v2.phaseByLine[uniqueLines])[0, 1]),
    )


def test_frt5850_parameter_maps_achieved(v2: _V2) -> None:
    """What is met, and the measured causes of the Pearson shortfall, pinned
    (regression bounds, not tolerances; phase1-validation.md):

    - r as measured less 0.02;
    - not noise-limited: 4×4 block means raise r by at most 0.03;
    - D2300, BD2290, BD1900_2, BD2310: most of ours − MTRDR is a per-column
      (cross-track) component; removing it gives r ≥ 0.85;
    - OLINDEX3: after the column part, a per-line component that follows the
      phase angle (|r| ≥ 0.9); removing both gives r ≥ 0.99."""
    floors = {
        "D2300": 0.765,
        "BD2290": 0.754,
        "BD1900_2": 0.607,
        "OLINDEX3": 0.875,
        "BD2310": 0.863,
    }
    for name in PARAMETERS:
        ours, theirs = v2.comparison.parameters[name]
        pearson = compareMaps(ours, theirs).pearson
        assert pearson >= floors[name], name
        blocks = compareMaps(_blockMeans(ours, 4), _blockMeans(theirs, 4)).pearson
        assert blocks - pearson <= 0.03, name
        parts = _decompose(v2, name)
        if name == "OLINDEX3":
            assert parts.lineFraction >= 0.8
            assert abs(parts.linePhaseCorrelation) >= 0.9
            assert parts.pearsonBothRemoved >= 0.99
        else:
            assert parts.columnFraction >= 0.5, name
            assert parts.pearsonColumnRemoved >= 0.85, name


# --- V3: the rim FRSs ---------------------------------------------------------------


def _co2(path: Path) -> tuple[float, float]:
    """The M7 metric on a map-projected product: the per-pixel median and the
    scene-median residual's RMS, over every pixel complete in the window."""
    source = ENVIDataSource(str(path))
    try:
        wavelengths = report._nanometers(source)
        good = report._goodBands(path, source.bandCount)
        inside = np.flatnonzero(
            (wavelengths > CO2_WINDOW_NM[0]) & (wavelengths < CO2_WINDOW_NM[1]) & good
        )
        cube = report._read(
            source, slice(0, source.height), slice(0, source.width), inside
        )
    finally:
        source.close()
    residual = co2Residual(wavelengths[inside], cube)
    valid = np.isfinite(residual)
    footprint = np.isfinite(cube).any(axis=-1)
    assert valid.sum() >= 0.99 * footprint.sum()  # (BP elements are kept)
    return (
        float(np.median(residual[valid])),
        sceneResidualRms(wavelengths[inside], cube[valid]),
    )


@pytest.mark.parametrize("name", FRS)
def test_frs_co2_residual(
    frsProducts: dict[str, tuple[ProcessResult, ProcessResult]], name: str
) -> None:
    """V3: the per-pixel median CO2 residual at most 0.0083 and the scene
    residual RMS at most 0.0080 (the medians over the six local MTRDRs, M7).
    The bundled basis was learned from these three FRSs, so the product
    without it must pass too. Measured (with / without the basis; a
    leave-one-out basis in between): FRS00031442 0.00397 / 0.00770 (0.00439),
    scene 0.00193 / 0.00659; FRS0002FE75 0.00517 / 0.00741 (0.00537), scene
    0.00362 / 0.00619; FRS00031260 0.00420 / 0.00731 (0.00457), scene 0.00203
    / 0.00597."""
    for result in frsProducts[name]:
        median, scene = _co2(_mapped(result))
        assert median <= FRS_CO2_MEDIAN
        assert scene <= FRS_CO2_SCENE


# --- the artifact basis (A6) --------------------------------------------------------


def _columnCo2(column: ColumnResiduals, basis: ArtifactBasis | None) -> float:
    """The CLI's leave-one-out metric: the median over columns of the M7 CO2
    residual of the column spectra corrected for the transmission, and for
    ``basis`` when given (c from ``artifactScale``)."""
    fit = column.selection.fit
    path = shiftedLogTransmission(
        column.wavelengths, column.selection.chosen.values, fit.shiftNm
    )
    if basis is not None:
        probe = AtmosphereModel(column.selection.chosen, fit.shiftNm, basis.spectra)
        scale, _ = artifactScale(
            column.spectra, column.spread, column.wavelengths, probe
        )
        assert math.isfinite(scale)
        path = path + scale * basis.spectra
    with np.errstate(invalid="ignore", over="ignore"):
        corrected = column.spectra * np.exp(-fit.beta[:, None] * path)
        window = (column.wavelengths > CO2_WINDOW_NM[0]) & (
            column.wavelengths < CO2_WINDOW_NM[1]
        )
    covered = window.sum(axis=1) >= 4
    residual = co2Residual(column.wavelengths[covered], corrected[covered])
    return float(np.median(residual[np.isfinite(residual)]))


@pytest.mark.parametrize("key", sorted(set(CHOSEN_SCANS.values())))
def test_artifact_basis_leave_one_out(
    paper: Path, tmp_path_factory: pytest.TempPathFactory, key: str
) -> None:
    """A6, under each scan the pipeline chooses for an FRS: a basis learned
    from the other two FRSs (``learn-artifacts``' pipeline path,
    ``columnResiduals``) reduces the held-out FRS's column CO2 residual by at
    least 30%. Measured (none → leave-one-out): 061C4 0.00630 → 0.00341,
    0.00846 → 0.00501, 0.00634 → 0.00306; 0A93E 0.01022 → 0.00369, 0.00665 →
    0.00405, 0.00933 → 0.00357; 08608 0.00613 → 0.00360, 0.00752 → 0.00507,
    0.00588 → 0.00329 (FRS00031442, FRS0002FE75, FRS00031260; the smallest
    reduction 33%)."""
    cache = tmp_path_factory.mktemp("loo")
    recipe = Recipe()
    recipe.atmosphere.scan.set(key)
    columns = {
        name: columnResiduals(
            CrismObservation.fromFolder(paper / name),
            recipe,
            _library(paper, name, cache),
        )
        for name in FRS
    }
    for held, column in columns.items():
        others = [n for n in FRS if n != held]
        basis = learnArtifactBasis(
            [columns[n].residuals for n in others],
            key,
            others,
            wavelengths=columns[others[0]].wavelengths,
            sclks=[columns[n].sclk for n in others],
        )
        without = _columnCo2(column, None)
        assert _columnCo2(column, basis) <= (1 - LEAVE_ONE_OUT_REDUCTION) * without


def test_bundled_bases_serve_the_frss_only(
    paper: Path, frsProducts: dict[str, tuple[ProcessResult, ProcessResult]]
) -> None:
    """Each FRS's choice has a bundled basis learned from the three FRSs; the
    epoch gate keeps it from FRT00005850 (2007)."""
    sclk = readTrr3(paper / FRT / "trdr/frt00005850_07_if167l_trr3.lbl").sclk
    for name, (result, _) in frsProducts.items():
        record = json.loads(result.provenance.read_text())["steps"]["atmosphere"]
        key = record["chosen"]["key"]
        assert key == CHOSEN_SCANS[name]
        assert record["artifact"]["used"] is True
        assert set(record["artifact"]["sourceObservations"]) == set(FRS)
        lookup = bundledArtifactBasis(key, 640, 438, sclk)
        assert lookup.basis is None and "180 days" in lookup.reason
        first, last = record["artifact"]["sclkSpan"]
        assert sclk < first - ARTIFACT_EPOCH_MARGIN_SCLK


def _parameterMedians(path: Path, other: Path) -> dict[str, tuple[float, float]]:
    """Each parameter's median in two products of one observation, over the
    pixels valid in both."""
    names = ("D2300", "BD2290", "BD1900_2")
    taken: dict[str, tuple[list[np.ndarray], list[np.ndarray]]] = {
        name: ([], []) for name in names
    }
    a, b = ENVIDataSource(str(path)), ENVIDataSource(str(other))
    try:
        wavelengths = report._nanometers(a)
        for rows in report._blocks(slice(0, a.height)):
            cubes = [report._read(s, rows, slice(0, s.width)) for s in (a, b)]
            for name in names:
                pa, pb = (summaryParameter(name, wavelengths, c) for c in cubes)
                both = np.isfinite(pa) & np.isfinite(pb)
                taken[name][0].append(pa[both])
                taken[name][1].append(pb[both])
    finally:
        a.close()
        b.close()
    return {
        name: (float(np.median(np.concatenate(x))), float(np.median(np.concatenate(y))))
        for name, (x, y) in taken.items()
    }


@pytest.mark.parametrize(
    ("name", "parameter"),
    [
        pytest.param(
            name,
            parameter,
            marks=[pytest.mark.xfail(strict=True, reason=cause)]
            if (cause := _MINERAL_CAUSES.get((name, parameter)))
            else [],
            id=f"{name}-{parameter}",
        )
        for name in FRS
        for parameter in ("D2300", "BD2290", "BD1900_2")
    ],
)
def test_artifact_basis_preserves_mineral_medians(
    frsProducts: dict[str, tuple[ProcessResult, ProcessResult]],
    repeatability: dict[str, float],
    name: str,
    parameter: str,
) -> None:
    """Task 14's review: the basis must not move the D2300, BD2290 and
    BD1900_2 medians by more than the between-observation repeatability
    (measured here on the crater floor). Measured shifts: FRS00031442 −0.00071,
    −0.00001, +0.00122; FRS0002FE75 −0.00177, −0.00007, +0.00176;
    FRS00031260 −0.00094, −0.00000, +0.00351."""
    with_, without = frsProducts[name]
    a, b = _parameterMedians(_mapped(with_), _mapped(without))[parameter]
    assert abs(a - b) <= repeatability[parameter]


def test_artifact_basis_mineral_shift_achieved(
    frsProducts: dict[str, tuple[ProcessResult, ProcessResult]],
) -> None:
    """What is met: BD2290 unmoved (≤ 0.0001), D2300 ≤ 0.0019 and BD1900_2 ≤
    0.0037 (the measured maxima plus 0.0001 and 0.0002: regression bounds)."""
    bounds = {"D2300": 0.0019, "BD2290": 0.0001, "BD1900_2": 0.0037}
    for with_, without in frsProducts.values():
        medians = _parameterMedians(_mapped(with_), _mapped(without))
        for parameter, (a, b) in medians.items():
            assert abs(a - b) <= bounds[parameter], parameter


# --- V3: FRS00031442 against the crater-floor MTRDRs (the prototype's metrics) -------

GRID_M = 36.0
_RANGE_NM = (1020.0, 2600.0)
_ALBEDO_NM = (1300.0, 1700.0)
_MIN_OVERLAP = 3000  # 36 m pixels, for a pair's gains
_GAIN_BLOCK = 4
_NOISE_COMPONENTS = 3
_PAIR_POINTS = 60000
_TILE, _TILE_STEP = 48, 24
_CHUNK = 24
_POINTS = 80000
_P4_MIN_OVERLAP_18M = 1000


@frozen
class _Grid:
    transform: Affine
    width: int
    height: int


@frozen(eq=False)
class _Stack:
    """Co-registered spectra on the 36 m grid: ``values`` (n, b, points) at
    ``points`` (pixels with two or more looks, sampled), NaN where invalid;
    ``valid`` (n, H, W); the per-band medians of each pair's 4×4 block means
    over its overlap (the gains' input)."""

    names: tuple[str, ...]
    wavelengths: np.ndarray
    valid: np.ndarray
    values: np.ndarray
    blockMedians: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]]
    registrationPx: dict[str, float]


def _wavelengths(path: Path) -> np.ndarray:
    match = re.search(r"wavelength = \{([^}]*)\}", _header(path))
    assert match is not None, path
    values = np.array([float(x) for x in match.group(1).split(",")])
    return values * 1000 if values.max() < 20 else values


def _commonGrid(paths: list[Path], snapTo: Path) -> _Grid:
    """``stage1_grid.grid``: the union of the MTRDRs at 36 m, on HRL's lattice."""
    bounds = []
    for path in paths:
        with rasterio.open(path) as d:
            bounds.append(d.bounds)
    with rasterio.open(snapTo) as d:
        hx, hy = d.bounds.left, d.bounds.top
    x0, y0 = min(b.left for b in bounds), min(b.bottom for b in bounds)
    x1, y1 = max(b.right for b in bounds), max(b.top for b in bounds)
    gx0 = hx - math.ceil((hx - x0) / GRID_M) * GRID_M
    gy1 = hy + math.ceil((y1 - hy) / GRID_M) * GRID_M
    return _Grid(
        Affine(GRID_M, 0, gx0, 0, -GRID_M, gy1),
        int(math.ceil((x1 - gx0) / GRID_M)),
        int(math.ceil((gy1 - y0) / GRID_M)),
    )


def _regrid(
    path: Path, grid: _Grid, bands: np.ndarray, correction: Affine | None
) -> np.ndarray:
    """``stage1_grid.regrid_bands``: 0-based ``bands`` onto the grid, averaged
    from finer pixels, bilinear from equal ones; ``correction`` moves the
    image onto the reference in map coordinates."""
    out = np.full((len(bands), grid.height, grid.width), np.nan, np.float32)
    with rasterio.open(path) as d:
        transform = (correction or Affine.identity()) * d.transform
        finer = abs(d.transform.a) < GRID_M - 1e-6
        method = Resampling.average if finer else Resampling.bilinear
        for n, band in enumerate(bands):
            values = d.read(int(band) + 1).astype(np.float32)
            values[(values >= 65534) | (values <= -1)] = np.nan
            reproject(
                values,
                out[n],
                src_transform=transform,
                src_crs=d.crs,
                dst_transform=grid.transform,
                dst_crs=d.crs,
                resampling=method,
                src_nodata=np.nan,
                dst_nodata=np.nan,
            )
    return out


def _tileShifts(reference: np.ndarray, moving: np.ndarray) -> np.ndarray:
    """``stage1_grid.tile_shifts``: (row, col, dr, dc) of 48 px tiles every 24 px."""
    found = []
    for r in range(0, reference.shape[0] - _TILE, _TILE_STEP):
        for c in range(0, reference.shape[1] - _TILE, _TILE_STEP):
            a = reference[r : r + _TILE, c : c + _TILE]
            b = moving[r : r + _TILE, c : c + _TILE]
            if not (np.isfinite(a).all() and np.isfinite(b).all()):
                continue
            if a.std() < 1e-4 or b.std() < 1e-4:
                continue
            shift, _, _ = phase_cross_correlation(
                a - a.mean(), b - b.mean(), upsample_factor=20, normalization=None
            )
            if np.abs(shift).max() < 4:
                found.append([r + _TILE / 2, c + _TILE / 2, shift[0], shift[1]])
    return np.array(found)


def _fitAffine(
    shifts: np.ndarray, grid: _Grid, trim: float = 2.5
) -> tuple[Affine, float]:
    """``stage1_grid.fit_affine``: the map affine moving the image by the
    trimmed linear fit of the tile shifts, and the median residual (px)."""
    r, c, dr, dc = shifts.T
    keep = np.ones(len(r), bool)
    residual = np.zeros(len(r))
    pr = pc = np.zeros(3)
    for _ in range(3):
        design = np.c_[np.ones(keep.sum()), r[keep], c[keep]]
        pr = np.linalg.lstsq(design, dr[keep], rcond=None)[0]
        pc = np.linalg.lstsq(design, dc[keep], rcond=None)[0]
        everywhere = np.c_[np.ones(len(r)), r, c]
        residual = np.hypot(dr - everywhere @ pr, dc - everywhere @ pc)
        keep = residual < trim * (1.4826 * np.median(residual[keep]) + 1e-6)
    size, x0, y1 = GRID_M, grid.transform.c, grid.transform.f

    def linear(p: np.ndarray) -> tuple[float, float, float]:
        return p[0] + p[1] * y1 / size - p[2] * x0 / size, p[2] / size, -p[1] / size

    cc, cx, cy = linear(pc)
    rc, rx, ry = linear(pr)
    affine = Affine(
        1 + size * cx, size * cy, size * cc, -size * rx, 1 - size * ry, -size * rc
    )
    return affine, float(np.median(residual[keep]))


def _blockMean(a: np.ndarray, f: int) -> np.ndarray:
    """``fusion_core.block_mean``: a block with any NaN is NaN."""
    *lead, rows, cols = a.shape
    r2, c2 = rows // f * f, cols // f * f
    return a[..., :r2, :c2].reshape(*lead, r2 // f, f, c2 // f, f).mean(axis=(-1, -3))


def _buildStack(paths: dict[str, Path], grid: _Grid, always: str | None) -> _Stack:
    """``stage1_grid.py``'s chain registration and ``stage2_fuse.py``'s
    validity (finite in more than 97% of every 8th band) and overlap medians;
    every pixel where ``always`` overlaps another observation is kept."""
    names = tuple(paths)
    wavelengths = _wavelengths(paths[names[0]])
    wavelengths = wavelengths[
        (wavelengths >= _RANGE_NM[0]) & (wavelengths <= _RANGE_NM[1])
    ]
    bands = {}
    for name, path in paths.items():
        own = _wavelengths(path)
        nearest = np.abs(own[None, :] - wavelengths[:, None]).argmin(axis=1)
        assert np.all(np.abs(own[nearest] - wavelengths) < 1.0), name
        bands[name] = nearest
    albedo = (wavelengths >= _ALBEDO_NM[0]) & (wavelengths <= _ALBEDO_NM[1])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        raw = {
            n: np.nanmean(_regrid(p, grid, bands[n][albedo], None), 0)
            for n, p in paths.items()
        }
        corrections: dict[str, Affine | None] = {names[0]: None}
        corrected = {names[0]: raw[names[0]]}
        residuals = {names[0]: 0.0}
        for name in names[1:]:
            overlap = {
                k: int((np.isfinite(v) & np.isfinite(raw[name])).sum())
                for k, v in corrected.items()
            }
            target = max(overlap, key=lambda k: overlap[k])
            shifts = _tileShifts(corrected[target], raw[name])
            assert len(shifts) >= 8, f"{name}: {len(shifts)} tiles against {target}"
            corrections[name], residuals[name] = _fitAffine(shifts, grid)
            corrected[name] = np.nanmean(
                _regrid(paths[name], grid, bands[name][albedo], corrections[name]), 0
            )
    valid = np.stack(
        [
            np.isfinite(_regrid(p, grid, bands[n][::8], corrections[n])).mean(0) > 0.97
            for n, p in paths.items()
        ]
    )
    looks = valid.sum(0)
    rng = np.random.default_rng(3)
    candidates = np.flatnonzero((looks >= 2).ravel())
    chosen = rng.choice(candidates, min(_POINTS, candidates.size), replace=False)
    if always is not None:
        k = names.index(always)
        kept = np.flatnonzero((valid[k] & (looks >= 2)).ravel())
        chosen = np.union1d(chosen, kept)
    rows, cols = np.unravel_index(np.sort(chosen), looks.shape)
    pairs = [
        (i, j)
        for i, j in itertools.combinations(range(len(names)), 2)
        if (valid[i] & valid[j]).sum() >= _MIN_OVERLAP
    ]
    medians = {
        p: (np.zeros(wavelengths.size), np.zeros(wavelengths.size)) for p in pairs
    }
    values = np.full((len(names), wavelengths.size, rows.size), np.nan, np.float32)
    for start in range(0, wavelengths.size, _CHUNK):
        chunk = slice(start, min(start + _CHUNK, wavelengths.size))
        cubes = [
            np.where(
                valid[k][None],
                _regrid(paths[n], grid, bands[n][chunk], corrections[n]),
                np.nan,
            )
            for k, n in enumerate(names)
        ]
        for k, cube in enumerate(cubes):
            values[k, chunk] = cube[:, rows, cols]
        for i, j in pairs:
            both = valid[i] & valid[j]
            bi = _blockMean(np.where(both, cubes[i], np.nan), _GAIN_BLOCK)
            bj = _blockMean(np.where(both, cubes[j], np.nan), _GAIN_BLOCK)
            for n, band in enumerate(range(chunk.start, chunk.stop)):
                ok = np.isfinite(bi[n]) & np.isfinite(bj[n])
                medians[(i, j)][0][band] = np.median(bi[n][ok])
                medians[(i, j)][1][band] = np.median(bj[n][ok])
    return _Stack(names, wavelengths, valid, values, medians, residuals)


def _gainSolve(
    medians: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]], count: int
) -> np.ndarray:
    """``fusion_core.gain_solve`` with observation 0 held at gain 1."""
    keys = list(medians)
    bands = len(next(iter(medians.values()))[0])
    design = np.zeros((len(keys), count - 1))
    for row, (i, j) in enumerate(keys):
        if i:
            design[row, i - 1] = 1.0
        if j:
            design[row, j - 1] = -1.0
    gains = np.ones((count, bands))
    for b in range(bands):
        y = np.array(
            [np.log(medians[p][1][b]) - np.log(medians[p][0][b]) for p in keys]
        )
        gains[1:, b] = np.exp(np.linalg.lstsq(design, y, rcond=None)[0])
    return gains


def _subsetGains(stack: _Stack, members: list[int]) -> np.ndarray:
    index = {k: n for n, k in enumerate(members)}
    medians = {
        (index[i], index[j]): m
        for (i, j), m in stack.blockMedians.items()
        if i in index and j in index
    }
    return _gainSolve(medians, len(members))


def _robustVariance(x: np.ndarray) -> np.ndarray:
    median = np.median(x, axis=0, keepdims=True)
    return (1.4826 * np.median(np.abs(x - median), axis=0)) ** 2


def _pairNoise(yi: np.ndarray, yj: np.ndarray) -> np.ndarray:
    """``fusion_core.pair_noise`` (the robust variance, 3 smooth components out)."""
    d = yi - yj
    d = d[np.all(np.isfinite(d), axis=1)]
    centered = d - d.mean(0)
    smooth = uniform_filter1d(centered, size=9, axis=1, mode="nearest")
    w, v = np.linalg.eigh(smooth.T @ smooth / max(smooth.shape[0] - 1, 1))
    top = v[:, np.argsort(w)[::-1][:_NOISE_COMPONENTS]]
    rest = centered - (centered @ top) @ top.T
    return _robustVariance(rest) / np.maximum(1.0 - (top**2).sum(1), 0.5)


def _noiseVariance(stack: _Stack, gains: np.ndarray) -> np.ndarray:
    """``stage2_fuse.py``: the three-cornered hat on the gain-aligned pair
    differences, smoothed (``smooth_noise``), (n, b)."""
    rng = np.random.default_rng(7)
    count = len(stack.names)
    pairs = {}
    for i, j in itertools.combinations(range(count), 2):
        both = np.isfinite(stack.values[i]).all(0) & np.isfinite(stack.values[j]).all(0)
        index = np.flatnonzero(both)
        if index.size < _MIN_OVERLAP // 2:
            continue
        if index.size > _PAIR_POINTS:
            index = np.sort(rng.choice(index, _PAIR_POINTS, replace=False))
        pairs[(i, j)] = _pairNoise(
            (gains[i][:, None] * stack.values[i][:, index]).T,
            (gains[j][:, None] * stack.values[j][:, index]).T,
        )
    design = np.zeros((len(pairs), count))
    for row, (i, j) in enumerate(pairs):
        design[row, i] = design[row, j] = 1.0
    hat = np.stack(
        [
            nnls(design, np.array([pairs[p][b] for p in pairs]))[0]
            for b in range(stack.wavelengths.size)
        ],
        axis=1,
    )
    positive = np.where(hat > 0, hat, np.nan)
    padded = np.pad(positive, ((0, 0), (3, 3)), mode="edge")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        smoothed = np.nanmedian(sliding_window_view(padded, 7, axis=1), axis=-1)
        fill = np.nanmedian(positive, axis=1, keepdims=True)
    return np.where(np.isfinite(smoothed), smoothed, fill)


def _shapeChiSquare(
    y: np.ndarray, variance: np.ndarray, wavelengths: np.ndarray
) -> float:
    """``stage2b_diag.chi2_stats`` on spectra each divided by its 1.3–1.7 µm
    mean: ``y`` (n, b, points), ``variance`` (n, b); the bands outside
    1950–2080 nm and below 2560 nm."""
    good = ~(((wavelengths > 1950) & (wavelengths < 2080)) | (wavelengths > 2560))
    norm = (wavelengths >= _ALBEDO_NM[0]) & (wavelengths <= _ALBEDO_NM[1])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        m = np.nanmean(y[:, norm, :], 1, keepdims=True)
        shaped = y / m
        shapedVariance = variance / np.nanmedian(m, axis=2) ** 2
        finite = np.isfinite(shaped)
        w = np.where(finite, 1 / shapedVariance[:, :, None], 0)
        fused = np.nansum(np.where(finite, shaped, 0) * w, 0) / w.sum(0)
        looks = finite[:, 0, :].sum(0)
        r2 = np.nansum(np.where(finite, (shaped - fused) ** 2 * w, 0), 0)
        return float(np.nanmedian(r2[good].mean(0) / np.maximum(looks - 1, 1)))


def _parameterStack(
    stack: _Stack, members: list[int], gains: np.ndarray, name: str
) -> np.ndarray:
    return np.stack(
        [
            summaryParameter(
                name, stack.wavelengths, (gains[n][:, None] * stack.values[k]).T
            )
            for n, k in enumerate(members)
        ]
    )


def _repeatability(per: np.ndarray) -> float:
    """``stage3_figs.py``: the median, over points with three or more looks,
    of the standard deviation across the looks."""
    three = np.isfinite(per).sum(0) >= 3
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return float(np.nanmedian(np.nanstd(per[:, three], 0, ddof=1)))


@pytest.fixture(scope="module")
def craterFloor(
    paper: Path, frsProducts: dict[str, tuple[ProcessResult, ProcessResult]]
) -> _Stack:
    """The five crater-floor MTRDRs and our FRS00031442 (default recipe) on
    the prototype's 36 m grid; the FRS is registered last, so the MTRDRs'
    registration is the prototype's."""
    paths = {name: paper / path for name, path in CRATER_FLOOR.items()}
    grid = _commonGrid(list(paths.values()), paths["HRL000040FF"])
    paths["FRS00031442"] = _mapped(frsProducts["FRS00031442"][0])
    return _buildStack(paths, grid, "FRS00031442")


@frozen
class _Pair:
    chiSquare: float  # shape χ², as delivered
    chiSquareGained: float  # after the joint gains
    bd2310Offset: float  # median over the overlap of BD2310_i − BD2310_j


def _pairs(stack: _Stack) -> dict[tuple[str, str], _Pair]:
    count = len(stack.names)
    gains = _subsetGains(stack, list(range(count)))
    variance = _noiseVariance(stack, gains)
    wavelengths = stack.wavelengths
    found = {}
    for i, j in itertools.combinations(range(count), 2):
        both = np.isfinite(stack.values[i, 0]) & np.isfinite(stack.values[j, 0])
        if both.sum() < 500:
            continue
        yi, yj = stack.values[i][:, both], stack.values[j][:, both]
        bi = summaryParameter("BD2310", wavelengths, yi.T)
        bj = summaryParameter("BD2310", wavelengths, yj.T)
        found[(stack.names[i], stack.names[j])] = _Pair(
            _shapeChiSquare(np.stack([yi, yj]), variance[[i, j]], wavelengths),
            _shapeChiSquare(
                np.stack([gains[i][:, None] * yi, gains[j][:, None] * yj]),
                variance[[i, j]],
                wavelengths,
            ),
            float(np.nanmedian(bi - bj)),
        )
    return found


@pytest.fixture(scope="module")
def overlapPairs(craterFloor: _Stack) -> dict[tuple[str, str], _Pair]:
    return _pairs(craterFloor)


def _split(
    pairs: dict[tuple[str, str], _Pair],
) -> tuple[list[_Pair], list[_Pair]]:
    ours = [p for names, p in pairs.items() if "FRS00031442" in names]
    theirs = [p for names, p in pairs.items() if "FRS00031442" not in names]
    return ours, theirs


def test_frs31442_overlaps_the_crater_floor(craterFloor: _Stack) -> None:
    """Ruling P4: FRS00031442 overlaps crater-floor MTRDRs by at least 1000
    pixels at 18 m (250 at 36 m), so V3 uses it (measured at 18 m: 48,488,
    22,627 and 46,057 pixels with FRT00005C5E, FRT000047A3, HRL000040FF)."""
    k = craterFloor.names.index("FRS00031442")
    overlaps = [
        int((craterFloor.valid[k] & craterFloor.valid[i]).sum())
        for i in range(len(CRATER_FLOOR))
    ]
    assert max(overlaps) * 4 >= _P4_MIN_OVERLAP_18M
    assert sum(o * 4 >= _P4_MIN_OVERLAP_18M for o in overlaps) >= 3


def test_frs31442_overlap_shape_chi_square(
    overlapPairs: dict[tuple[str, str], _Pair],
) -> None:
    """V3: the shape χ² of FRS00031442 against each overlapping MTRDR at most
    the largest MTRDR–MTRDR value, as delivered and after the gains (measured:
    64.1, 5.6, 12.1 against 171.3; 5.7, 3.6, 5.3 against 9.5)."""
    ours, theirs = _split(overlapPairs)
    assert len(ours) >= 3 and len(theirs) >= 6
    assert max(p.chiSquare for p in ours) <= max(p.chiSquare for p in theirs)
    assert max(p.chiSquareGained for p in ours) <= max(
        p.chiSquareGained for p in theirs
    )


@pytest.mark.xfail(strict=True, reason=_OFFSET_CAUSE)
def test_frs31442_overlap_bd2310_offset(
    overlapPairs: dict[tuple[str, str], _Pair],
) -> None:
    """V3: FRS00031442's BD2310 median offset from each overlapping MTRDR at
    most the largest MTRDR–MTRDR offset and at most 0.004 (measured: 0.0055,
    0.0051, 0.0004 against 0.0045)."""
    ours, theirs = _split(overlapPairs)
    bound = min(max(abs(p.bd2310Offset) for p in theirs), BD2310_OFFSET)
    assert max(abs(p.bd2310Offset) for p in ours) <= bound


def test_frs31442_overlap_bd2310_offset_achieved(
    overlapPairs: dict[tuple[str, str], _Pair],
) -> None:
    """What is met: against HRL000040FF within 0.004 (0.0004); the largest
    0.0057 or less (the measured 0.0055 plus 0.0002: a regression bound)."""
    offsets = {names: p.bd2310Offset for names, p in overlapPairs.items()}
    assert abs(offsets[("HRL000040FF", "FRS00031442")]) <= BD2310_OFFSET
    ours, _ = _split(overlapPairs)
    assert max(abs(p.bd2310Offset) for p in ours) <= 0.0057
