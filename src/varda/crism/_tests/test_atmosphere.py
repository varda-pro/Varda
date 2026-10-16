import math

import numpy as np
import pytest
from numpy.polynomial import legendre

from varda.crism import atmosphere
from varda.crism.atmosphere import (
    FIT_WINDOW_NM,
    HYDRATION_MASK_NM,
    columnSpectra,
    fitColumns,
    selectTransmission,
    shiftedLogTransmission,
    smoothShift,
)
from varda.crism.calibration import Transmission
from varda.crism._tests.synthetic import SyntheticTruth, makeSyntheticCube

_CLEAN = dict(artifactAmplitude=0.0, spikeRate=0.0, stripeAmplitude=0.0)


def _smoothSurface(
    cube: np.ndarray, truth: SyntheticTruth
) -> tuple[np.ndarray, np.ndarray]:
    """The cube and σ with each pixel's surface replaced, over the fit window,
    by its own cubic continuum in ln R (Legendre, as the fit's).

    The library spectra under the synthetic surface keep broad structure in
    the window that no cubic follows: the wing of the 1.9 µm water band past
    the hydration mask and a 1–2% bump at 2020–2040 nm, under the deepest
    CO2 bands. On the raw cube that leaves residuals about 10× the noise and
    biases δ by +0.16 to +0.18 nm (``test_raw_surface``); the tests that
    check the fit's own σ need a surface the model can reach. Here the
    atmosphere, the shift and the noise are the synthetic's own."""
    smooth = truth.surface.copy()
    for x, wavelengths in enumerate(truth.columnWavelengths):
        inside = (wavelengths >= FIT_WINDOW_NM[0]) & (wavelengths <= FIT_WINDOW_NM[1])
        basis = legendre.legvander(
            np.interp(wavelengths[inside], FIT_WINDOW_NM, (-1, 1)), 3
        )
        logS = np.log(truth.surface[:, x, inside]).T
        coefficients = np.linalg.lstsq(basis, logS, rcond=None)[0]
        smooth[:, x, inside] = np.exp(basis @ coefficients).T
    factor = smooth / truth.surface
    return cube * factor, truth.noiseSigma * factor


def _columnInputs(
    lines: int = 128, seed: int = 0, **options: float
) -> tuple[np.ndarray, np.ndarray, SyntheticTruth]:
    cube, truth = makeSyntheticCube(lines, seed, **(_CLEAN | options))
    spectra, sigma = columnSpectra(
        *_smoothSurface(cube, truth), truth.columnWavelengths
    )
    return spectra, sigma, truth


def _candidate(
    scanId: str, values: np.ndarray, artifacts: np.ndarray | None = None
) -> Transmission:
    return Transmission(scanId, f"synthetic {scanId}", values, scanId, artifacts)


def _decoys(wavelengths: np.ndarray, true: np.ndarray) -> list[Transmission]:
    """The true AT and copies shifted by 1.5 nm and scaled in depth.

    Depth is scaled linearly, 1 − k(1 − T) with k = 0.9 and 1.1. A power,
    T**k, would be no decoy: k·ln T is the same model with β·k, so its score
    equals the truth's exactly."""
    return [
        _candidate("shifted", _shifted(wavelengths, true, 1.5)),
        _candidate("shallow", 1 - 0.9 * (1 - true)),
        _candidate("truth", true),
        _candidate("deep", 1 - 1.1 * (1 - true)),
    ]


def _shifted(
    wavelengths: np.ndarray, transmission: np.ndarray, shiftNm: np.ndarray | float
) -> np.ndarray:
    """T_x(λ + δ_x), re-interpolated along each column's own wavelengths."""
    shift = np.broadcast_to(np.asarray(shiftNm, dtype=float), wavelengths.shape[:1])
    result = np.full(transmission.shape, np.nan)
    for x in range(wavelengths.shape[0]):
        bands = np.isfinite(wavelengths[x])
        w = wavelengths[x, bands]
        result[x, bands] = np.interp(w + shift[x], w, transmission[x, bands])
    return result


def twoWavelengthBeta(
    wavelengths: np.ndarray, spectra: np.ndarray, transmission: np.ndarray
) -> np.ndarray:
    """McGuire 2009's classic β (Eq. 4): ln(R2007 / R1980) / ln(T2007 / T1980),
    on each column's bands nearest 1980 and 2007 nm. ``spectra`` is
    ``(..., s, b)``; ``wavelengths`` and ``transmission`` are ``(s, b)``."""
    columns = np.arange(wavelengths.shape[0])
    filled = np.where(np.isfinite(wavelengths), wavelengths, np.inf)
    low = np.argmin(np.abs(filled - 1980.0), axis=1)
    high = np.argmin(np.abs(filled - 2007.0), axis=1)
    ratio = np.log(spectra[..., columns, high] / spectra[..., columns, low])
    return ratio / np.log(transmission[columns, high] / transmission[columns, low])


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_recovers_beta_and_shift(seed: int) -> None:
    """A1, A4: δ_x (a smooth curve within ±0.8 nm) and the column β come
    back, unbiased and with the σ the fit predicts."""
    spectra, sigma, truth = _columnInputs(seed=seed)
    fit = fitColumns(spectra, sigma, truth.columnWavelengths, truth.transmission)
    assert fit.valid.all()
    smooth = smoothShift(fit.shiftNm, fit.valid)
    assert np.mean(np.abs(smooth - truth.shiftNm) <= 3 * fit.shiftSigma) >= 0.99
    # the column spectrum is a mean of ln R, so its β is the column's mean β
    for z in (
        (fit.beta - np.mean(truth.beta, axis=0)) / fit.betaSigma,
        (fit.shiftNm - truth.shiftNm) / fit.shiftSigma,
    ):
        assert abs(np.mean(z)) < 0.3 and 0.8 < np.std(z) < 1.25
        assert np.mean(np.abs(z) <= 3) >= 0.98
    # the fit leaves noise only: residuals over the score window are near σ
    assert np.median(fit.residualRms) < 1.2 * np.nanmedian(sigma / spectra)


def test_selects_injected_transmission() -> None:
    """A3: the true AT beats copies of it shifted by 1.5 nm and scaled in
    depth (``_decoys``), and the result says by how much."""
    spectra, sigma, truth = _columnInputs()
    wavelengths = truth.columnWavelengths
    candidates = _decoys(wavelengths, truth.transmission)
    result = selectTransmission(spectra, sigma, wavelengths, candidates)
    assert result.chosen.scanId == "truth"
    assert set(result.scores) == {"shifted:AT", "shallow:AT", "truth:AT", "deep:AT"}
    best = result.scores["truth:AT"]
    assert result.margin > 0
    assert result.margin == pytest.approx(sorted(result.scores.values())[1] - best)
    assert result.relativeMargin == pytest.approx(result.margin / best)
    assert result.winFraction > 0.9  # a clear win in nearly every column
    assert result.fit.valid.all()


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_raw_surface(seed: int) -> None:
    """A1, A3, A5: on the synthetic's own library surface (a 1.9 µm water
    band, broad structure under the CO2 bands), the hydration mask keeps the
    column β within 2% and the true AT still wins. δ is biased by the
    surface (about +0.17 nm), beyond its formal σ but within 3σ once σ is
    scaled by √χ²_ν, which says the model does not reach the noise."""
    cube, truth = makeSyntheticCube(128, seed, **_CLEAN)
    wavelengths = truth.columnWavelengths
    spectra, sigma = columnSpectra(cube, truth.noiseSigma, wavelengths)
    fit = fitColumns(spectra, sigma, wavelengths, truth.transmission)
    assert fit.valid.all()
    assert abs(np.median(fit.beta / np.mean(truth.beta, axis=0) - 1)) <= 0.02
    smooth = smoothShift(fit.shiftNm, fit.valid)
    scaled = fit.shiftSigma * np.sqrt(np.maximum(1.0, fit.reducedChiSquare))
    assert np.mean(np.abs(smooth - truth.shiftNm) <= 3 * scaled) >= 0.99
    candidates = _decoys(wavelengths, truth.transmission)
    result = selectTransmission(spectra, sigma, wavelengths, candidates)
    assert result.chosen.scanId == "truth"


def test_sources_sharing_a_scan_id() -> None:
    """A PDS AT and a VS ADR of the same scan are two candidates."""
    spectra, sigma, truth = _columnInputs(lines=16)
    wavelengths, true = truth.columnWavelengths, truth.transmission
    artifacts = np.zeros((2, *true.shape))
    candidates = [
        _candidate("061C4", true),
        _candidate("061C4", 1 - 1.1 * (1 - true), artifacts),
    ]
    result = selectTransmission(spectra, sigma, wavelengths, candidates)
    assert set(result.scores) == {"061C4:AT", "061C4:VS"}
    assert result.chosen.key == "061C4:AT"
    with pytest.raises(ValueError, match="share a key"):
        selectTransmission(spectra, sigma, wavelengths, candidates + candidates[:1])
    single = selectTransmission(spectra, sigma, wavelengths, candidates[:1])
    assert single.margin == math.inf and single.relativeMargin == math.inf
    assert single.winFraction == 1.0


def test_selection_requires_shift_fit() -> None:
    """A3, the M10 regression: scored without the shift term, a decoy that
    happens to line up with the scene's wavelengths beats the right scan.

    The truth is the AT at the column's own wavelengths, while the scene's
    CO2 bands sit δ_x away (±0.8 nm). The decoy is the AT pre-shifted by
    0.9 δ_x, so it nearly lines up, times a different scan's band-to-band
    pattern (0.4% Gaussian, fixed seed). Without a shift term the truth pays
    for the whole misalignment and the decoy only for its small pattern; with
    the shift fitted the truth leaves noise only and the decoy keeps its
    pattern."""
    spectra, sigma, truth = _columnInputs(shiftAmplitudeNm=0.8)
    wavelengths, true = truth.columnWavelengths, truth.transmission
    pattern = np.exp(0.004 * np.random.default_rng(7).standard_normal(true.shape))
    decoy = _shifted(wavelengths, true, 0.9 * truth.shiftNm) * pattern
    candidates = [_candidate("truth", true), _candidate("aligned", decoy)]
    without = selectTransmission(
        spectra, sigma, wavelengths, candidates, fitShift=False
    )
    assert without.chosen.scanId == "aligned"
    joint = selectTransmission(spectra, sigma, wavelengths, candidates)
    assert joint.chosen.scanId == "truth"
    assert joint.scores["truth:AT"] < without.scores["truth:AT"]


def test_beta_sigma_below_two_wavelength() -> None:
    """A2: a least-squares β over the window scatters less, pixel by pixel,
    than McGuire's two-wavelength β (1980/2007 nm) on the same pixels."""
    raw, truth = makeSyntheticCube(64, 3, shiftAmplitudeNm=0.0, **_CLEAN)
    cube, sigma = _smoothSurface(raw, truth)
    lines, samples, bands = cube.shape
    wavelengths = np.broadcast_to(truth.columnWavelengths, cube.shape)
    fit = fitColumns(
        cube.reshape(-1, bands),
        sigma.reshape(-1, bands),
        wavelengths.reshape(-1, bands),
        np.broadcast_to(truth.transmission, cube.shape).reshape(-1, bands),
        fitShift=False,
    )
    window = fit.beta.reshape(lines, samples) - truth.beta
    classic = twoWavelengthBeta(truth.columnWavelengths, cube, truth.transmission)
    classic = classic - truth.beta
    assert np.std(window) < 0.5 * np.std(classic)


def test_too_few_bands_is_invalid() -> None:
    spectra, sigma, truth = _columnInputs(lines=16)
    inside = (truth.columnWavelengths > 1850) & (truth.columnWavelengths < 2200)
    spectra[3] = np.nan
    spectra[5, inside[5]] = np.nan
    spectra[5, np.nonzero(inside[5])[0][::10]] = 0.1
    fit = fitColumns(spectra, sigma, truth.columnWavelengths, truth.transmission)
    assert not fit.valid[3] and not fit.valid[5]
    assert fit.valid[[0, 4, 6]].all()
    assert np.isnan(fit.beta[3]) and np.isnan(fit.residuals[3]).all()
    # residuals are NaN outside the fit window and in the hydration mask
    wavelengths = truth.columnWavelengths[0]
    masked = (wavelengths >= HYDRATION_MASK_NM[0]) & (
        wavelengths <= HYDRATION_MASK_NM[1]
    )
    assert masked.sum() >= 9
    assert np.isnan(fit.residuals[0, ~inside[0] | masked]).all()
    assert np.isfinite(fit.residuals[0, inside[0] & ~masked]).all()


def test_smooth_shift_resists_outliers() -> None:
    x = np.linspace(-1, 1, 120)
    truth = 0.3 + 0.4 * x - 0.2 * x**3
    shift = truth + np.random.default_rng(1).normal(0, 0.01, x.size)
    shift[::9] += 2.0  # 14 wild columns
    valid = np.ones(x.size, dtype=bool)
    valid[50:60] = False
    shift[50:60] = np.nan
    smooth = smoothShift(shift, valid)
    assert np.abs(smooth - truth).max() < 0.01


def test_column_spectra() -> None:
    cube = np.full((4, 2, 3), 0.2)
    cube[:, 0, 0] = [0.1, 0.2, 0.4, 0.8]
    cube[1:, 1, 1] = np.nan
    cube[:, 1, 2] = np.nan
    sigma = np.full(cube.shape, 0.01)
    spectra, spread = columnSpectra(cube, sigma, np.full((2, 3), 2000.0))
    assert spectra[0, 0] == pytest.approx(np.sqrt(0.08))  # the geometric mean
    relative = np.sqrt(np.sum((0.01 / cube[:, 0, 0]) ** 2)) / 4
    assert spread[0, 0] == pytest.approx(np.sqrt(0.08) * relative)
    assert spectra[1, 1] == pytest.approx(0.2) and spread[1, 1] == pytest.approx(0.01)
    assert np.isnan(spectra[1, 2]) and np.isnan(spread[1, 2])


def test_shifted_log_transmission_matches_interp() -> None:
    rng = np.random.default_rng(3)
    wavelengths = np.sort(rng.uniform(1000, 2600, (6, 40)), axis=1)[:, ::-1].copy()
    transmission = rng.uniform(0.3, 1.0, wavelengths.shape)
    transmission[1, [0, 7, 8, 30]] = np.nan  # gaps in T
    wavelengths[2, 5] = np.nan
    transmission[3, :39] = np.nan  # a single sample: nothing to interpolate
    shift = np.array([0.8, -1.3, 25.0, 0.0, -2000.0, 0.0])  # beyond both ends too
    result = shiftedLogTransmission(wavelengths, transmission, shift)
    for x in range(wavelengths.shape[0]):
        bands = np.isfinite(wavelengths[x]) & np.isfinite(transmission[x])
        if bands.sum() < 2:
            assert np.isnan(result[x]).all()
            continue
        w, logT = wavelengths[x, bands], np.log(transmission[x, bands])
        order = np.argsort(w)
        expected = np.interp(w + shift[x], w[order], logT[order])
        np.testing.assert_allclose(result[x, bands], expected, rtol=0, atol=1e-12)
        assert np.isnan(result[x, ~bands]).all()


def test_scattered_invalid_samples() -> None:
    """10% of samples invalid at random: no line is complete, so each band
    averages its own finite lines, and β keeps its predicted σ."""
    cube, truth = makeSyntheticCube(128, 0, **_CLEAN)
    cube, sigma = _smoothSurface(cube, truth)
    cube[np.random.default_rng(5).random(cube.shape) < 0.1] = np.nan
    wavelengths = truth.columnWavelengths
    spectra, spread = columnSpectra(cube, sigma, wavelengths)
    fit = fitColumns(spectra, spread, wavelengths, truth.transmission)
    assert fit.valid.all()
    z = (fit.beta - np.mean(truth.beta, axis=0)) / fit.betaSigma
    assert abs(np.mean(z)) < 0.3 and np.std(z) <= 1.15


def test_column_spectra_use_complete_lines() -> None:
    wavelengths = np.array([[1900.0, 2000.0, 2500.0]])
    cube = np.full((10, 1, 3), 0.2)
    cube[:, 0, 1] = 0.1
    cube[0, 0, :] = 0.8  # a complete line: counted everywhere
    cube[1, 0, 1] = np.nan  # incomplete in the fit window: dropped
    cube[1, 0, 0] = 0.9
    cube[2, 0, 2] = np.nan  # outside the window: the line still counts
    spectra, _ = columnSpectra(cube, np.full(cube.shape, 0.01), wavelengths)
    assert spectra[0, 0] == pytest.approx(np.exp((np.log(0.8) + 8 * np.log(0.2)) / 9))
    assert spectra[0, 1] == pytest.approx(np.exp((np.log(0.8) + 8 * np.log(0.1)) / 9))
    assert spectra[0, 2] == pytest.approx(np.exp((np.log(0.8) + 7 * np.log(0.2)) / 8))
    # with fewer than 8 complete lines every finite sample counts
    cube[2:5, 0, 0] = np.nan
    spectra, _ = columnSpectra(cube, np.full(cube.shape, 0.01), wavelengths)
    assert spectra[0, 0] == pytest.approx(
        np.exp((np.log(0.8) + np.log(0.9) + 5 * np.log(0.2)) / 7)
    )


def test_unconverged_shift_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    spectra, sigma, truth = _columnInputs(lines=16)
    monkeypatch.setattr(atmosphere, "_MAX_SHIFT_STEPS", 1)
    fit = fitColumns(spectra, sigma, truth.columnWavelengths, truth.transmission)
    moved = np.abs(truth.shiftNm) > 0.2  # one step leaves these unconverged
    assert moved.any() and not fit.valid[moved].any()
