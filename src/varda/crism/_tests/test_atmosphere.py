import numpy as np
import pytest
from numpy.polynomial import legendre

from varda.crism.atmosphere import (
    FIT_WINDOW_NM,
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

    The library spectra under the synthetic surface carry a 1.9 µm water band
    and about 3% band-to-band structure at 2.1–2.2 µm that no continuum
    follows. On the raw cube that structure biases the column β by about 5%
    and δ by 0.12–0.15 nm (and picks the deep decoy below); that is the
    mineral-band problem of A5 and A6, not the fit's. Here the atmosphere,
    the shift and the noise are the synthetic's own."""
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
    spectra, sigma = columnSpectra(*_smoothSurface(cube, truth))
    return spectra, sigma, truth


def _candidate(scanId: str, values: np.ndarray) -> Transmission:
    return Transmission(scanId, f"synthetic {scanId}", values, scanId)


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
    """A3: the true AT beats copies of it shifted by 1.5 nm and scaled in depth.

    Depth is scaled linearly, 1 − k(1 − T) with k = 0.9 and 1.1. A power,
    T**k, would be no decoy: k·ln T is the same model with β·k, so its score
    equals the truth's exactly."""
    spectra, sigma, truth = _columnInputs()
    wavelengths, true = truth.columnWavelengths, truth.transmission
    candidates = [
        _candidate("shifted", _shifted(wavelengths, true, 1.5)),
        _candidate("shallow", 1 - 0.9 * (1 - true)),
        _candidate("truth", true),
        _candidate("deep", 1 - 1.1 * (1 - true)),
    ]
    result = selectTransmission(spectra, sigma, wavelengths, candidates)
    assert result.chosen.scanId == "truth"
    assert set(result.scores) == {"shifted", "shallow", "truth", "deep"}
    assert result.margin > 0
    assert result.margin == pytest.approx(
        sorted(result.scores.values())[1] - result.scores["truth"]
    )
    assert result.fit.valid.all()


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
    assert joint.scores["truth"] < without.scores["truth"]


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
    # residuals are NaN outside the fit window
    assert np.isnan(fit.residuals[0, ~inside[0]]).all()
    assert np.isfinite(fit.residuals[0, inside[0]]).all()


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
    spectra, spread = columnSpectra(cube, sigma)
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
