import math
import time
from functools import cache
from pathlib import Path

import attrs
import numpy as np
import pytest
from numpy.polynomial import legendre
from scipy import ndimage

from varda.crism import atmosphere, validation
from varda.crism.atmosphere import (
    FIT_WINDOW_NM,
    HYDRATION_MASK_NM,
    ArtifactBasis,
    AtmosphereModel,
    ElevationModel,
    PixelFit,
    artifactScale,
    bundledArtifactBasis,
    columnSpectra,
    correct,
    fitColumns,
    fitElevationModel,
    fitPixels,
    highPass,
    learnArtifactBasis,
    loadArtifactBasis,
    saveArtifactBasis,
    selectTransmission,
    shiftedLogTransmission,
    smoothShift,
)
from varda.crism.calibration import (
    CalibrationLibrary,
    Transmission,
    TransmissionSource,
)
from varda.crism.noise import estimateNoise
from varda.crism.readers import DdrBand, readDdr, readDetectorFrame, readTrr3
from varda.crism._tests.synthetic import (
    HYDRATED_UNIT,
    SyntheticTruth,
    makeSyntheticCube,
)

_CLEAN = dict(artifactAmplitude=0.0, spikeRate=0.0, stripeAmplitude=0.0)


def _smoothFactor(truth: SyntheticTruth) -> np.ndarray:
    """``(l, s, b)``: each pixel's own cubic continuum in ln R over the fit
    window (Legendre, as the fit's) divided by its surface; 1 elsewhere."""
    smooth = truth.surface.copy()
    for x, wavelengths in enumerate(truth.columnWavelengths):
        inside = (wavelengths >= FIT_WINDOW_NM[0]) & (wavelengths <= FIT_WINDOW_NM[1])
        basis = legendre.legvander(
            np.interp(wavelengths[inside], FIT_WINDOW_NM, (-1, 1)), 3
        )
        logS = np.log(truth.surface[:, x, inside]).T
        coefficients = np.linalg.lstsq(basis, logS, rcond=None)[0]
        smooth[:, x, inside] = np.exp(basis @ coefficients).T
    return smooth / truth.surface


def _smoothSurface(
    cube: np.ndarray, truth: SyntheticTruth
) -> tuple[np.ndarray, np.ndarray]:
    """The cube and σ with each pixel's surface replaced, over the fit window,
    by its own cubic continuum in ln R (``_smoothFactor``).

    The library spectra under the synthetic surface keep broad structure in
    the window that no cubic follows: the wing of the 1.9 µm water band past
    the hydration mask and a 1–2% bump at 2020–2040 nm, under the deepest
    CO2 bands. On the raw cube that leaves residuals about 10× the noise and
    biases δ by +0.16 to +0.18 nm (``test_raw_surface``); the tests that
    check the fit's own σ need a surface the model can reach. Here the
    atmosphere, the shift and the noise are the synthetic's own."""
    factor = _smoothFactor(truth)
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
    assert abs(np.median(smooth - truth.shiftNm)) <= 0.25  # the bias itself, nm
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


def test_column_spectra_fill_sparse_columns() -> None:
    """With fewer than 8 complete lines (10% of samples missing at random),
    the missing fit-window samples are filled from a rank-2 model of the
    column: on lines that differ by an offset and a multiple of one band
    shape, the filled mean is the mean of the complete data, where a plain
    per-band mean of the finite samples is not."""
    rng = np.random.default_rng(0)
    lines, bands = 40, 20
    wavelengths = np.append(np.linspace(1900.0, 2150.0, bands), 2500.0)[None]
    shape = np.append(-0.4 * np.sin(np.linspace(0, np.pi, bands)) ** 2, 0.0)
    logR = (
        np.log(np.linspace(0.2, 0.15, bands + 1))
        + rng.normal(0, 0.1, (lines, 1))
        + (1 + rng.normal(0, 0.15, (lines, 1))) * shape
    )
    cube = np.exp(logR)[:, None, :]
    missing = rng.random(cube.shape) < 0.1
    missing[..., -1] = False
    cube[missing] = np.nan
    assert np.isfinite(cube).all(axis=2).sum() < 8
    spectra, _ = columnSpectra(cube, np.full(cube.shape, 0.01), wavelengths)
    truth = logR.mean(axis=0)[:-1]
    plain = np.nanmean(np.log(cube[:, 0, :-1]), axis=0)
    assert np.abs(plain - truth).max() > 5e-3  # the fill has work to do
    np.testing.assert_allclose(np.log(spectra[0, :-1]), truth, atol=1e-4)


def test_unconverged_shift_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    spectra, sigma, truth = _columnInputs(lines=16)
    monkeypatch.setattr(atmosphere, "_MAX_SHIFT_STEPS", 1)
    fit = fitColumns(spectra, sigma, truth.columnWavelengths, truth.transmission)
    moved = np.abs(truth.shiftNm) > 0.2  # one step leaves these unconverged
    assert moved.any() and not fit.valid[moved].any()


# --- part B: per-pixel scale, elevation prior, correction --------------------------


def _model(
    truth: SyntheticTruth,
    shiftNm: np.ndarray | None = None,
    elevation: ElevationModel | None = None,
) -> AtmosphereModel:
    return AtmosphereModel(
        transmission=_candidate("truth", truth.transmission),
        shiftNm=truth.shiftNm if shiftNm is None else shiftNm,
        artifact=None,
        elevation=elevation,
    )


def _withPrior(
    cube: np.ndarray, sigma: np.ndarray, truth: SyntheticTruth, model: AtmosphereModel
) -> tuple[PixelFit, ElevationModel, PixelFit]:
    """The free per-pixel fit, the elevation model fitted to it and the fit
    with that model as the prior."""
    wavelengths = truth.columnWavelengths
    free = fitPixels(cube, sigma, wavelengths, model)
    elevation = fitElevationModel(free.beta, free.betaSigma, truth.elevationM)
    prior = fitPixels(
        cube,
        sigma,
        wavelengths,
        attrs.evolve(model, elevation=elevation),
        truth.elevationM,
    )
    return free, elevation, prior


def _atmosphere(truth: SyntheticTruth) -> np.ndarray:
    """``(l, s, b)``: the synthetic's own β · ln T_x(λ + δ_x)."""
    logT = shiftedLogTransmission(
        truth.columnWavelengths, truth.transmission, truth.shiftNm
    )
    return truth.beta[..., None] * logT


def _correlatedNoiseCube(
    truth: SyntheticTruth, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    """The smooth-surface cube (no stripes, spikes or artifact) with its noise
    replaced by noise of the same σ per band but correlated across bands
    (white noise smoothed by a Gaussian of 1.5 bands), and that σ: the
    noise model is right band by band, but the fit's band-independent σ_β
    is not."""
    factor = _smoothFactor(truth)
    logT = shiftedLogTransmission(
        truth.columnWavelengths, truth.transmission, truth.shiftNm
    )
    signal = truth.surface * factor * np.exp(truth.beta[..., None] * logT)
    white = np.random.default_rng(seed).standard_normal(signal.shape)
    correlated = ndimage.gaussian_filter1d(white, 1.5, axis=-1)
    correlated /= correlated[..., :-1].std()
    sigma = truth.noiseSigma * factor
    return signal + correlated * sigma, sigma


def _zScores(fit: PixelFit, truth: SyntheticTruth) -> np.ndarray:
    """β's z-scores against its honest σ (the empirical one with a prior)."""
    return ((fit.beta - truth.beta) / fit.betaSigmaEmpirical)[fit.valid]


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_pixel_beta_recovery(seed: int) -> None:
    """A5, S4.B–C: per pixel, β and its σ are honest with the elevation prior
    on and off.

    The synthetic's β follows z exactly unless given its own departure, and
    then the prior's σ would have nothing to be tested against; here ln β
    departs from the elevation law by a coherent field of std 0.01
    (``betaScatter``), about half a pixel's own σ_β/β (≈ 0.018), so the prior
    moves each β about 80% of the way to the law and σ_β drops to
    τ / √(τ² + σ²) ≈ 0.45 of the free fit's. The field has few independent
    patches, so the cube has 128 lines: τ then comes back to 4% (0.0096–
    0.0097) and the prior's z-scores scatter by 1.02–1.03 (at 64 lines τ is
    off by up to 13% and the scatter reaches 1.12). The surface is the
    smooth one (``_smoothSurface``), which the model reaches."""
    raw, truth = makeSyntheticCube(128, seed, betaScatter=0.01, **_CLEAN)
    cube, sigma = _smoothSurface(raw, truth)
    free, elevation, prior = _withPrior(cube, sigma, truth, _model(truth))
    assert elevation.accepted
    assert elevation.intrinsicScatter == pytest.approx(0.01, rel=0.2)
    assert elevation.sigmaRatio == pytest.approx(1.0, abs=0.1)
    for fit in (free, prior):
        assert fit.valid.mean() >= 0.99
        z = _zScores(fit, truth)
        assert abs(np.mean(z)) < 0.1
        assert 0.9 <= np.std(z) <= 1.1
    # the prior adds information: σ_β shrinks as the prior's τ predicts
    relative = elevation.sigmaRatio * free.betaSigma / free.beta
    tau = elevation.intrinsicScatter
    predicted = np.median(tau / np.sqrt(tau**2 + relative**2))
    ratio = np.median(
        prior.betaSigmaEmpirical / (elevation.sigmaRatio * free.betaSigma)
    )
    assert 0.35 < ratio < 0.6 and ratio == pytest.approx(predicted, rel=0.05)


def test_elevation_prior_uses_empirical_sigma() -> None:
    """A5: τ and the prior's weight come from the empirical σ_β (along-track
    second differences of β), not the formal one. With σ overstated 2× the
    formal σ_β is twice too large (z std 0.5); the ratio comes back as 0.5,
    τ as the true 0.01, and the fit with the prior is the same as with the
    right σ, to rounding. On FRS00031442 the ratio is 0.17 with σ from plain
    along-track differences (96% of them common to the fit-window bands:
    achromatic texture, which the continuum absorbs) and 1.84 with the
    instrument-noise σ."""
    raw, truth = makeSyntheticCube(64, 0, betaScatter=0.01, **_CLEAN)
    cube, sigma = _smoothSurface(raw, truth)
    _, right, prior = _withPrior(cube, sigma, truth, _model(truth))
    free, overstated, priorOverstated = _withPrior(
        cube, 2 * sigma, truth, _model(truth)
    )
    assert np.std(_zScores(free, truth)) == pytest.approx(0.5, abs=0.05)
    assert overstated.sigmaRatio == pytest.approx(0.5, rel=0.1)
    assert overstated.sigmaRatio == pytest.approx(right.sigmaRatio / 2, rel=1e-9)
    assert overstated.intrinsicScatter == pytest.approx(0.01, rel=0.3)
    np.testing.assert_allclose(priorOverstated.beta, prior.beta, rtol=1e-9)
    assert 0.85 <= np.std(_zScores(priorOverstated, truth)) <= 1.2


def test_prior_with_spectrally_correlated_noise() -> None:
    """A5: with noise correlated across bands (``_correlatedNoiseCube``) the
    formal σ_β is wrong (here 1.4× too small); the empirical σ_β finds it,
    and the prior is not over-confident."""
    raw, truth = makeSyntheticCube(64, 0, betaScatter=0.01, **_CLEAN)
    cube, sigma = _correlatedNoiseCube(truth, 10)
    free, elevation, prior = _withPrior(cube, sigma, truth, _model(truth))
    assert np.std(_zScores(free, truth)) > 1.25
    assert elevation.sigmaRatio > 1.25
    assert np.std(_zScores(free, truth) / elevation.sigmaRatio) == pytest.approx(
        1.0, abs=0.1
    )
    z = _zScores(prior, truth)
    assert abs(np.mean(z)) < 0.15 and 0.8 <= np.std(z) <= 1.2


@pytest.mark.parametrize("scaleHeightKm", [11.0, 30.0])
def test_elevation_model_recovers_H(scaleHeightKm: float) -> None:
    """A5: the robust line of ln β against z gives H within 3σ, accepted only
    inside 5–20 km. With β independent of z (the elevations shuffled) H is
    undetermined and the model is rejected."""
    raw, truth = makeSyntheticCube(64, 0, scaleHeightKm=scaleHeightKm, **_CLEAN)
    cube, sigma = _smoothSurface(raw, truth)
    free = fitPixels(cube, sigma, truth.columnWavelengths, _model(truth))
    elevation = fitElevationModel(free.beta, free.betaSigma, truth.elevationM)
    assert abs(elevation.scaleHeightKm - scaleHeightKm) <= (
        3 * elevation.scaleHeightSigmaKm
    )
    assert elevation.accepted == (scaleHeightKm == 11.0)
    # ln β at the reference elevation, and the scatter of the pixels' own σ
    expected = -(elevation.referenceElevationM + 2500.0) / (scaleHeightKm * 1000.0)
    assert abs(elevation.lnBeta0 - expected) <= 3 * elevation.lnBeta0Sigma + 1e-4
    relative = np.median((free.betaSigma / free.beta)[free.valid])
    assert elevation.scatter == pytest.approx(relative, rel=0.15)
    assert elevation.intrinsicScatter < 0.3 * relative
    shuffled = np.random.default_rng(2).permutation(truth.elevationM.ravel())
    unrelated = fitElevationModel(
        free.beta, free.betaSigma, shuffled.reshape(truth.elevationM.shape)
    )
    assert not unrelated.accepted


def test_beta_regularization_resists_h2o_band() -> None:
    """A5: the elevation prior protects β in the hydrated unit (the opal
    mixture's 1.9 µm band), but on the library surface only partly.

    On the library surface (structure in the window in every unit) the
    hydrated unit's free β is biased by −3.6…−3.8% (2.0–2.2σ_β, seeds 0–3)
    and the prior leaves −1.9…−2.3%, still 2.0–2.1 times the prior fit's own
    σ_β: the elevation law inherits the scene-wide surface bias (−1% on the
    basalt) and τ (0.010–0.015, the units' coherent departures) keeps the
    prior weak. So the target of the brief, a bias under the prior's σ_β,
    is not met there (a known limitation, ledger A5); the test asserts that
    the prior at least cuts the bias to 0.6 of the free one (0.49 on seed 1).

    On the scene A5 is about, where every other pixel follows the model (its
    surface replaced by its own continuum, ``_smoothFactor``) and the
    hydrated unit keeps its library spectrum, the prior takes the bias to
    −0.5…−0.8%: under the free fit's σ_β, and 0.9–1.1 times its own."""
    raw, truth = makeSyntheticCube(64, 1, **_CLEAN)
    hydrated = truth.units == HYDRATED_UNIT
    assert hydrated.sum() > 200
    factor = _smoothFactor(truth)
    factor[hydrated] = 1.0
    model = _model(truth)

    def bias(fit: PixelFit) -> float:
        keep = hydrated & fit.valid
        return float(np.mean(fit.beta[keep] - truth.beta[keep]))

    def sigmaOf(fit: PixelFit) -> float:
        return float(np.mean(fit.betaSigmaEmpirical[hydrated & fit.valid]))

    free, elevation, prior = _withPrior(
        raw * factor, truth.noiseSigma * factor, truth, model
    )
    assert elevation.accepted
    assert bias(free) < -2 * sigmaOf(free)
    assert abs(bias(prior)) < sigmaOf(free)
    assert abs(bias(prior)) < 1.5 * sigmaOf(prior)
    free, _, prior = _withPrior(raw, truth.noiseSigma, truth, model)
    assert bias(free) < -2 * sigmaOf(free)
    assert abs(bias(prior)) < 0.6 * abs(bias(free))


def test_correct_sigma_chi2() -> None:
    """S4.D: after the correction, (R′ − surface · stripes) / σ′ has a reduced
    χ² of 1.00 ± 0.05 in every band group over 1.0–2.6 µm.

    The fitted β is correlated with the data it corrects: inside the fit
    window σ′ includes that covariance (ln R′ = (I − G) ln R for the fit's
    gain G). Neglecting it (σ′² = σ² + (ln T′ σ_β)² R′²) gives 0.97–0.98 over
    the fitted bands and 0.95–0.96 over the CO2 score window for the free
    fit; with it, 1.01 and 1.03 (the stripes, which the model does not
    contain, bias each column's β a little). Both fits, free and with the
    prior, are checked. The surface is the smooth one, so the model reaches
    the noise; spikes and the artifact are off, stripes on."""
    raw, truth = makeSyntheticCube(64, 2, spikeRate=0.0, artifactAmplitude=0.0)
    factor = _smoothFactor(truth)
    cube, sigma = raw * factor, truth.noiseSigma * factor
    expected = truth.surface * factor * truth.stripes
    wavelengths = truth.columnWavelengths
    free, _, prior = _withPrior(cube, sigma, truth, _model(truth))
    for fit in (free, prior):
        corrected, correctedSigma = correct(
            cube, sigma, wavelengths, _model(truth), fit
        )
        assert np.isfinite(correctedSigma[np.isfinite(corrected)]).all()
        chi = (corrected - expected) / correctedSigma
        groups = {
            "below the window": (1000.0, 1850.0),
            "hydration mask": HYDRATION_MASK_NM,
            "fitted": FIT_WINDOW_NM,
            "CO2 score window": atmosphere.SCORE_WINDOW_NM,
            "above the window": (2200.0, 2600.0),
        }
        for name, (low, high) in groups.items():
            band = (wavelengths >= low) & (wavelengths <= high)
            if name == "fitted":
                band &= ~(
                    (wavelengths >= HYDRATION_MASK_NM[0])
                    & (wavelengths <= HYDRATION_MASK_NM[1])
                )
            values = chi[np.broadcast_to(band, chi.shape) & fit.valid[..., None]]
            assert abs(np.mean(values**2) - 1.0) <= 0.05, name


def test_correct_sigma_is_consistent_with_the_prior() -> None:
    """S4.D: σ′ is propagated from the data's σ through the fit's linear
    operator, with or without the prior; the empirical σ ratio only sets how
    far the prior moves β. With σ overstated 2× (ratio 0.49) and a weak
    prior (τ = 0.05 ≫ σ_β), β moves by under 1% and σ′ / σ at the deepest
    CO2 band stays within 3% of the free fit's (1.85 against 1.82). Scaling
    the propagated covariance by the ratio², as round 1 did, gave 1.37."""
    raw, truth = makeSyntheticCube(64, 0, betaScatter=0.05, **_CLEAN)
    cube, sigma = _smoothSurface(raw, truth)
    sigma = 2 * sigma
    free, elevation, prior = _withPrior(cube, sigma, truth, _model(truth))
    assert elevation.sigmaRatio == pytest.approx(0.5, rel=0.1)
    assert np.nanmedian(np.abs(prior.beta / free.beta - 1)) < 0.01
    wavelengths = truth.columnWavelengths
    logT = shiftedLogTransmission(wavelengths, truth.transmission, truth.shiftNm)
    inside = (wavelengths > FIT_WINDOW_NM[0]) & (wavelengths < FIT_WINDOW_NM[1])
    deepest = np.argmin(np.where(inside, logT, np.inf), axis=1)
    columns = np.arange(wavelengths.shape[0])

    def relative(fit: PixelFit) -> float:
        _, correctedSigma = correct(cube, sigma, wavelengths, _model(truth), fit)
        ratio = correctedSigma[:, columns, deepest] / sigma[:, columns, deepest]
        return float(np.median(ratio[fit.valid]))

    assert relative(prior) == pytest.approx(relative(free), rel=0.03)


def test_co2_window_flat_after_correction() -> None:
    """V1 on the full chain (selection among the decoys, the smoothed shift,
    the free fit, the elevation model and the prior) on the library surface:
    the CO2 residual of the corrected column medians is at most 1.5 times
    that of the cube with its own atmosphere removed exactly (the noise-only
    truth, which keeps the surface and the stripes)."""
    cube, truth = makeSyntheticCube(64, 0, artifactAmplitude=0.0, spikeRate=0.0)
    wavelengths = truth.columnWavelengths
    spectra, spread = columnSpectra(cube, truth.noiseSigma, wavelengths)
    candidates = _decoys(wavelengths, truth.transmission)
    selection = selectTransmission(spectra, spread, wavelengths, candidates)
    model = AtmosphereModel(
        transmission=selection.chosen,
        shiftNm=selection.fit.shiftNm,
        artifact=None,
        elevation=None,
    )
    _, _, fit = _withPrior(cube, truth.noiseSigma, truth, model)
    corrected, _ = correct(cube, truth.noiseSigma, wavelengths, model, fit)
    exact = cube * np.exp(-_atmosphere(truth))

    def residual(values: np.ndarray) -> float:
        medians = np.full(values.shape[1:], np.nan)
        medians[:, :-1] = np.nanmedian(values[..., :-1], axis=0)  # not the placeholder
        return float(np.median(validation.co2Residual(wavelengths, medians)))

    assert residual(corrected) <= 1.5 * residual(exact)


def test_pixels_with_missing_bands() -> None:
    """Pixels missing fit-window samples are solved on their own bands (10%
    invalid at random, so nearly every pixel); too few bands make a pixel
    invalid, NaN in the corrected cube."""
    raw, truth = makeSyntheticCube(32, 0, **_CLEAN)
    cube, sigma = _smoothSurface(raw, truth)
    cube[np.random.default_rng(5).random(cube.shape) < 0.1] = np.nan
    inside = (truth.columnWavelengths >= 1850) & (truth.columnWavelengths <= 2200)
    cube[3, 7, inside[7]] = np.nan
    wavelengths = truth.columnWavelengths
    fit = fitPixels(cube, sigma, wavelengths, _model(truth))
    assert not fit.valid[3, 7] and np.isnan(fit.beta[3, 7])
    assert fit.valid.mean() > 0.99
    z = ((fit.beta - truth.beta) / fit.betaSigma)[fit.valid]
    assert abs(np.mean(z)) < 0.1 and 0.9 <= np.std(z) <= 1.1
    corrected, correctedSigma = correct(cube, sigma, wavelengths, _model(truth), fit)
    assert np.isnan(corrected[3, 7]).all() and np.isnan(correctedSigma[3, 7]).all()
    assert np.isfinite(correctedSigma[np.isfinite(corrected)]).all()
    assert np.isfinite(corrected[fit.valid & np.isfinite(cube[..., 100])][:, 100]).all()


def test_rejected_elevation_model_is_not_a_prior() -> None:
    """An elevation model outside 5–20 km leaves the per-pixel fit free; an
    accepted one needs the elevations."""
    raw, truth = makeSyntheticCube(16, 0, **_CLEAN)
    cube, sigma = _smoothSurface(raw, truth)
    wavelengths = truth.columnWavelengths
    free = fitPixels(cube, sigma, wavelengths, _model(truth))
    rejected = attrs.evolve(
        fitElevationModel(free.beta, free.betaSigma, truth.elevationM),
        scaleHeightKm=40.0,
        accepted=False,
    )
    again = fitPixels(
        cube, sigma, wavelengths, _model(truth, elevation=rejected), truth.elevationM
    )
    np.testing.assert_array_equal(again.beta, free.beta)
    accepted = attrs.evolve(rejected, scaleHeightKm=11.0, accepted=True)
    with pytest.raises(ValueError, match="elevation"):
        fitPixels(cube, sigma, wavelengths, _model(truth, elevation=accepted))


def test_fit_pixels_is_fast() -> None:
    """One precomputed solve per column serves every complete line: a
    64 × 120 × 438 chunk fits and corrects in well under a few seconds."""
    raw, truth = makeSyntheticCube(64, 0, **_CLEAN)
    cube, sigma = _smoothSurface(raw, truth)
    model = _model(truth)
    start = time.perf_counter()
    fit = fitPixels(cube, sigma, truth.columnWavelengths, model)
    correct(cube, sigma, truth.columnWavelengths, model, fit)
    assert time.perf_counter() - start < 2.0


def _noDownload(url: str, target: Path) -> None:
    raise AssertionError(f"no download expected: {url}")


def test_real_prior_spares_deviant_pixels(demoData: Path) -> None:
    """A5 on FRS00031442 (data-gated): the pixels whose free β departs most
    from the elevation law (the top 5% of |ln β − law|, about 2.9% and more)
    are real coherent departures, not noise, so the prior must not drag them
    to the law. Their median per-pixel CO2 residual with the prior is within
    3% of the free fit's (measured 0.00691 against 0.00694; with the formal
    σ_β, τ was 0 and it got 18% worse). The scan chosen is 061C4:AT, and
    the empirical σ_β is about 1.84 times the formal one."""
    folder = demoData / "data-for-paper"
    trr3 = readTrr3(folder / "FRS00031442/trdr/frs00031442_01_if168l_trr3.lbl")
    ddr = readDdr(folder / "FRS00031442/ddr/frs00031442_01_de168l_ddr1.lbl")
    wavelengths = readDetectorFrame(
        folder / "ATO0004EC45/CRISM_CDR/wa/cdr490947778566_wa0000000l_3.lbl"
    ).alignedTo(trr3.detectorRows)
    incidence = np.radians(ddr.band(DdrBand.INCIDENCE_AREOID))
    cube = trr3.lines(slice(0, trr3.shape[0])) / np.cos(incidence)[..., None]
    elevationM = ddr.band(DdrBand.ELEVATION)
    sigma = estimateNoise(cube).sigma(cube)
    library = CalibrationLibrary(
        [folder / "ATO0004EC45"], folder.parent / "missing-cache", fetch=_noDownload
    )
    candidates = library.transmissions(trr3, TransmissionSource.PDS_AT)
    spectra, spread = columnSpectra(cube, sigma, wavelengths)
    selection = selectTransmission(spectra, spread, wavelengths, candidates)
    assert selection.chosen.key == "061C4:AT"
    model = AtmosphereModel(selection.chosen, selection.fit.shiftNm)
    free = fitPixels(cube, sigma, wavelengths, model)
    elevation = fitElevationModel(free.beta, free.betaSigma, elevationM)
    assert elevation.accepted
    # With σ the instrument noise (Task 9b) the ratio is 1.84, not the 0.17 it
    # had with a σ inflated by achromatic texture: β varies along track more
    # than the formal noise propagation says (real coherent structure).
    assert elevation.sigmaRatio == pytest.approx(1.84, abs=0.2)
    withPrior = attrs.evolve(model, elevation=elevation)
    prior = fitPixels(cube, sigma, wavelengths, withPrior, elevationM)
    law, _ = elevation.prior(elevationM)
    both = free.valid & prior.valid & np.isfinite(elevationM)
    with np.errstate(invalid="ignore", divide="ignore"):
        departure = np.abs(np.log(free.beta / law))
    deviant = both & (departure > np.percentile(departure[both], 95))
    perPixel = np.broadcast_to(wavelengths, cube.shape)[deviant]

    def residual(fit: PixelFit) -> float:
        corrected, _ = correct(cube, sigma, wavelengths, model, fit)
        return float(np.nanmedian(validation.co2Residual(perPixel, corrected[deviant])))

    assert residual(prior) <= 1.03 * residual(free)


# --- part C: the residual (artifact) model -----------------------------------------

_TRAINING_SEEDS = (10, 11, 12, 13)
# The artifact's RMS for part C. The synthetic's default (0.006) is mostly the
# shape of ln T itself (``_identifiable``): what a fit leaves of it is 0.0016,
# below the stripes and the noise of column spectra. At 0.02 that part is about
# 0.0055, what M7 found the FRS fits leave in common (0.006–0.008 down to 0.003).
_ARTIFACT_RMS = 0.02


def _residualOf(
    cube: np.ndarray, sigma: np.ndarray, truth: SyntheticTruth
) -> np.ndarray:
    """The high-passed part-A residual of the column spectra, ``(s, b)``, with
    the true AT as the only candidate (its shift fitted and smoothed)."""
    wavelengths = truth.columnWavelengths
    spectra, spread = columnSpectra(cube, sigma, wavelengths)
    candidate = _candidate("truth", truth.transmission)
    selection = selectTransmission(spectra, spread, wavelengths, [candidate])
    return highPass(selection.fit.residuals, wavelengths)


@cache
def _syntheticBasis() -> ArtifactBasis:
    """A basis learned from four synthetic observations (seeds 10–13: their
    own units, albedo, stripes and noise; the same artifact) on the smooth
    surface, which the model reaches, so the residual is the artifact (and
    each one's stripes and noise). Spikes are off: despiking comes first."""
    residuals, truth = [], None
    for seed in _TRAINING_SEEDS:
        raw, truth = makeSyntheticCube(
            64, seed, spikeRate=0.0, artifactAmplitude=_ARTIFACT_RMS
        )
        residuals.append(_residualOf(*_smoothSurface(raw, truth), truth))
    assert truth is not None
    return learnArtifactBasis(
        residuals,
        "truth:AT",
        [f"synthetic {seed}" for seed in _TRAINING_SEEDS],
        wavelengths=truth.columnWavelengths,
        sclks=[1.09e9 + seed for seed in _TRAINING_SEEDS],
    )


def _identifiable(truth: SyntheticTruth, weight: np.ndarray) -> np.ndarray:
    """``(s, b)``: the injected artifact less its least-squares projection on
    the part-A model, the cubic continuum, ln T_x(λ + δ_x) and its slope in
    λ (the shift's term), weighted as the fit (``weight`` = 1/σ²_lnR of the
    column spectra), over the fitted bands, then high-passed (0 elsewhere).
    The rest, about 93% of it (its correlation with the high-passed ln T is
    −0.93), has the atmosphere's own shape: the fit reads it as β (−10% at
    an RMS of 0.02, −3% at the default 0.006) and δ, and no residual model
    can tell it apart."""
    wavelengths = truth.columnWavelengths
    logT = shiftedLogTransmission(wavelengths, truth.transmission, truth.shiftNm)
    slope = shiftedLogTransmission(
        wavelengths, truth.transmission, truth.shiftNm + 0.05
    ) - shiftedLogTransmission(wavelengths, truth.transmission, truth.shiftNm - 0.05)
    fitted = (
        (wavelengths >= FIT_WINDOW_NM[0])
        & (wavelengths <= FIT_WINDOW_NM[1])
        & ~(
            (wavelengths >= HYDRATION_MASK_NM[0])
            & (wavelengths <= HYDRATION_MASK_NM[1])
        )
    )
    result = np.full(wavelengths.shape, np.nan)
    for x in range(wavelengths.shape[0]):
        k = fitted[x]
        u = np.interp(wavelengths[x, k], FIT_WINDOW_NM, (-1, 1))
        design = np.column_stack([legendre.legvander(u, 3), logT[x, k], slope[x, k]])
        root = np.sqrt(weight[x, k])
        a = truth.artifact[x, k]
        fit = np.linalg.lstsq(design * root[:, None], a * root, rcond=None)[0]
        result[x, k] = a - design @ fit
    return np.nan_to_num(highPass(result, wavelengths))


def _rowCorrelation(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pearson r of each row of ``a`` and ``b`` over the bands where both
    are nonzero."""
    keep = (a != 0) & (b != 0)
    count = keep.sum(1, keepdims=True)
    da = np.where(keep, a - (a * keep).sum(1, keepdims=True) / count, 0.0)
    db = np.where(keep, b - (b * keep).sum(1, keepdims=True) / count, 0.0)
    return (da * db).sum(1) / np.sqrt((da**2).sum(1) * (db**2).sum(1))


def test_high_pass_removes_continuum() -> None:
    """A6: the high-pass removes any cubic over the fit window exactly (a
    smooth mineral shape is never part of the basis) and leaves NaN outside
    the window and where the input is NaN."""
    _, truth = makeSyntheticCube(4, 0, **_CLEAN)
    wavelengths = truth.columnWavelengths
    u = (wavelengths - 2025.0) / 175.0
    smooth = 0.02 - 0.03 * u + 0.05 * u**2 - 0.01 * u**3
    wiggle = 0.001 * np.sin(wavelengths / 3.0)
    values = smooth + wiggle
    values[:, 100] = np.nan
    result = highPass(values, wavelengths)
    inside = (wavelengths >= FIT_WINDOW_NM[0]) & (wavelengths <= FIT_WINDOW_NM[1])
    assert np.isnan(result[~inside]).all() and np.isnan(result[:, 100]).all()
    np.testing.assert_allclose(highPass(smooth, wavelengths)[inside], 0, atol=1e-12)
    keep = inside & np.isfinite(result)
    assert np.std(result[keep]) == pytest.approx(np.std(wiggle[keep]), rel=0.1)


def test_artifact_basis_recovered() -> None:
    """A6: the basis learned across four synthetic observations (different
    seeds and surfaces, one artifact) correlates at least 0.95 with the
    injected artifact per column (median; measured 0.987, the worst column
    0.975), against its identifiable part (``_identifiable``): the part of
    a(x, λ) that is not the shape of ln T_x and its shift. Against the
    whole artifact the correlation is 0.50: the fit absorbs the rest into β
    and δ, where no residual model can separate it. At the synthetic's
    default RMS (0.006) the median is 0.905: the identifiable part, 0.0016,
    is then below each scene's stripes."""
    basis = _syntheticBasis()
    raw, truth = makeSyntheticCube(
        64, 0, spikeRate=0.0, artifactAmplitude=_ARTIFACT_RMS
    )
    spectra, spread = columnSpectra(
        *_smoothSurface(raw, truth), truth.columnWavelengths
    )
    assert basis.spectra.shape == truth.artifact.shape
    assert basis.sourceObservations == tuple(f"synthetic {s}" for s in _TRAINING_SEEDS)
    score = (truth.columnWavelengths >= atmosphere.SCORE_WINDOW_NM[0]) & (
        truth.columnWavelengths <= atmosphere.SCORE_WINDOW_NM[1]
    )
    assert np.sqrt(np.mean(basis.spectra[score] ** 2)) == pytest.approx(1.0)
    assert np.isfinite(basis.spectra).all()
    correlation = _rowCorrelation(
        basis.spectra, _identifiable(truth, (spectra / spread) ** 2)
    )
    assert np.median(correlation) >= 0.95


def _corrected(
    cube: np.ndarray,
    truth: SyntheticTruth,
    basis: ArtifactBasis | None,
    significant: bool = True,
) -> tuple[np.ndarray, AtmosphereModel]:
    """The part-A selection (true AT), the scene's artifact scale with
    ``basis`` (``significant``: at least 5σ from 0), the free per-pixel fit
    and the correction."""
    wavelengths = truth.columnWavelengths
    sigma = truth.noiseSigma
    spectra, spread = columnSpectra(cube, sigma, wavelengths)
    candidate = _candidate("truth", truth.transmission)
    selection = selectTransmission(spectra, spread, wavelengths, [candidate])
    model = AtmosphereModel(selection.chosen, selection.fit.shiftNm)
    if basis is not None:
        free = attrs.evolve(model, artifact=basis.spectra)
        scale, scaleSigma = artifactScale(spectra, spread, wavelengths, free)
        assert scale > 5 * scaleSigma or not significant
        model = attrs.evolve(free, artifactScale=scale)
    fit = fitPixels(cube, sigma, wavelengths, model)
    if basis is not None:
        np.testing.assert_allclose(
            fit.artifactAmplitude[fit.valid], model.artifactScale * fit.beta[fit.valid]
        )
    corrected, _ = correct(cube, sigma, wavelengths, model, fit)
    return corrected, model


def _exactlyCorrected(cube: np.ndarray, truth: SyntheticTruth) -> np.ndarray:
    """The cube with its own atmosphere, β (ln T_x(λ + δ_x) + a_x), removed."""
    logT = shiftedLogTransmission(
        truth.columnWavelengths, truth.transmission, truth.shiftNm
    )
    return cube * np.exp(-truth.beta[..., None] * (logT + truth.artifact))


def _columnMetric(truth: SyntheticTruth, values: np.ndarray) -> float:
    """M7 on the column medians: the median over columns of their CO2 residual."""
    medians = np.full(values.shape[1:], np.nan)
    medians[:, :-1] = np.nanmedian(values[..., :-1], axis=0)  # not the placeholder
    return float(np.median(validation.co2Residual(truth.columnWavelengths, medians)))


def test_artifact_reduces_co2_residual() -> None:
    """A6, V1: with the artifact injected (seed 0, not a training seed; the
    smooth surface), the M7 column-median metric of the atmosphere the
    correction leaves (the corrected cube over the exactly corrected one)
    is more than twice as large without the residual model as with it
    (measured 0.0049 against 0.0011).

    On the corrected cube itself the metric also counts what is not
    atmosphere, the same with or without the model: the surface's own
    curvature, the stripes and the noise of column medians, 0.0059 here
    (the exactly corrected cube). That M7 metric goes from 0.0071 to 0.0060,
    within 2% of the exact correction's."""
    raw, truth = makeSyntheticCube(
        64, 0, spikeRate=0.0, artifactAmplitude=_ARTIFACT_RMS
    )
    cube, _ = _smoothSurface(raw, truth)
    exact = _exactlyCorrected(cube, truth)
    without, _ = _corrected(cube, truth, None)
    with_, _ = _corrected(cube, truth, _syntheticBasis())
    left = _columnMetric(truth, without / exact)
    assert left > 2 * _columnMetric(truth, with_ / exact)
    assert _columnMetric(truth, with_) < _columnMetric(truth, without)
    assert _columnMetric(truth, with_) <= 1.02 * _columnMetric(truth, exact)


def test_artifact_model_preserves_mineral_band() -> None:
    """A6: the residual model does not touch mineral bands. On the library
    surface (seed 1), BD2290 and BD1900_2 of the hydrated and smectite units
    are compared, in units of σ, the parameter's scatter within the unit
    (the units are uniform but for albedo, so that scatter is the noise):

    - with the artifact injected, the model moves them by at most 0.5σ
      (measured 0.00σ for BD2290, 0.29–0.40σ for BD1900_2, seeds 1–3);
    - without the artifact, the model with its own scene scale (c about
      −0.001, the basalt's structure) moves them by under 0.25σ (≤ 0.15σ);
    - BD2290 (outside the fit window) of the artifact cube corrected with
      the model is within 1σ of the artifact-free truth run (0.04σ).

    BD1900_2 of the artifact cube is not within 1σ of the artifact-free run,
    with the model or without it (1.1–1.4σ with, 0.7–1.0σ without): about
    93% of the synthetic artifact is the shape of ln T, which the fit reads
    as β (−10%, −11% here with the basalt's own bias), and correcting with that β leaves a smooth 2 µm bowl
    of about −3% (at 1985 nm, R1985 is 2–2.5% low). The bowl is smooth, so
    the high-passed basis cannot hold it by design (S4.E.1), and BD1900_2's
    R1985 sits on it. Removing the hash on top of the bowl is the 0.3–0.4σ
    the model adds (ledger A6)."""
    withArtifact, truth = makeSyntheticCube(
        64, 1, spikeRate=0.0, artifactAmplitude=_ARTIFACT_RMS
    )
    clean, _ = makeSyntheticCube(64, 1, spikeRate=0.0, artifactAmplitude=0.0)
    basis = _syntheticBasis()
    corrected, model = _corrected(withArtifact, truth, basis)
    assert model.artifactScale is not None
    unmodeled, _ = _corrected(withArtifact, truth, None)
    truthRun, _ = _corrected(clean, truth, None)
    cleanModeled, cleanModel = _corrected(clean, truth, basis, significant=False)
    assert cleanModel.artifactScale is not None
    assert abs(cleanModel.artifactScale) < 0.5 * model.artifactScale
    wavelengths = truth.columnWavelengths[60]
    for unit in (HYDRATED_UNIT, 2):
        pixels = truth.units == unit
        assert pixels.sum() > 100
        for name in ("BD2290", "BD1900_2"):

            def parameter(cube: np.ndarray, name: str = name) -> np.ndarray:
                return validation.summaryParameter(name, wavelengths, cube[pixels])

            sigma = np.nanstd(parameter(truthRun))
            ours = parameter(corrected)
            ownEffect = np.nanmean(ours - parameter(unmodeled))
            assert abs(ownEffect) <= 0.5 * sigma, (unit, name)
            cleanShift = parameter(cleanModeled) - parameter(truthRun)
            assert abs(np.nanmean(cleanShift)) < 0.25 * sigma, (unit, name)
            if name == "BD2290":
                assert abs(np.nanmean(ours - parameter(truthRun))) < sigma, unit


_SCLK_2014 = 1.09e9  # FRS00031442 is at 1090233681


def _basis(
    key: str = "061C4:AT",
    shape: tuple[int, int] = (640, 438),
    span: tuple[float, float] = (_SCLK_2014, _SCLK_2014 + 5e6),
) -> ArtifactBasis:
    return ArtifactBasis(
        transmissionKey=key,
        spectra=np.random.default_rng(0).normal(size=shape),
        sourceObservations=("FRS00031442", "FRS0002FE75"),
        sclkSpan=span,
        createdWith="0.0.test",
    )


def test_round_trip_npz(tmp_path: Path) -> None:
    """A6: a basis saved to .npz loads with equal fields; files are named by
    the transmission key."""
    basis = _basis(shape=(12, 438))
    path = tmp_path / "061C4_AT.npz"
    saveArtifactBasis(basis, path)
    loaded = loadArtifactBasis(path)
    assert loaded.transmissionKey == basis.transmissionKey
    assert loaded.sourceObservations == basis.sourceObservations
    assert loaded.sclkSpan == basis.sclkSpan
    assert loaded.createdWith == basis.createdWith
    np.testing.assert_array_equal(loaded.spectra, basis.spectra)
    assert atmosphere.artifactFileName("061C4:AT") == "061C4_AT.npz"


def test_bundled_basis_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A6: the bundled basis serves a cube only with the same transmission
    key, columns and bands, and an SCLK within its training span ± 180 days;
    otherwise None, with the reason for the provenance."""
    monkeypatch.setattr(atmosphere, "ARTIFACT_FOLDER", tmp_path)
    basis = _basis()
    saveArtifactBasis(basis, tmp_path / atmosphere.artifactFileName("061C4:AT"))
    # a file under one key holding another key's basis
    saveArtifactBasis(
        _basis(key="08608:AT"), tmp_path / atmosphere.artifactFileName("0A93E:AT")
    )
    first, last = basis.sclkSpan
    margin = atmosphere.ARTIFACT_EPOCH_MARGIN_SCLK
    assert margin == pytest.approx(15.55e6, rel=1e-3)
    for sclk in (first, last, first - 0.99 * margin, last + 0.99 * margin):
        found = bundledArtifactBasis("061C4:AT", 640, 438, sclk)
        assert found.basis is not None, found.reason
        assert found.basis.sourceObservations == basis.sourceObservations
    cases = {
        "180 days": ("061C4:AT", 640, 438, last + 1.01 * margin),
        "training span": ("061C4:AT", 640, 438, first - 1.01 * margin),
        "320 × 438": ("061C4:AT", 320, 438, first),
        "640 × 437": ("061C4:AT", 640, 437, first),
        "08608:AT, not 0A93E:AT": ("0A93E:AT", 640, 438, first),
        "no artifact basis": ("094B5:AT", 640, 438, first),
    }
    for reason, arguments in cases.items():
        lookup = bundledArtifactBasis(*arguments)
        assert lookup.basis is None and reason in lookup.reason, lookup.reason


def test_learn_rejects_mismatched_inputs() -> None:
    """One residual and one SCLK per named observation, all of the scan's
    shape, and a basis that is not all zero."""
    _, truth = makeSyntheticCube(4, 0, **_CLEAN)
    wavelengths = truth.columnWavelengths
    zeros = np.zeros(wavelengths.shape)
    with pytest.raises(ValueError, match="observation"):
        learnArtifactBasis(
            [zeros], "x:AT", ["a", "b"], wavelengths=wavelengths, sclks=[1.0, 2.0]
        )
    with pytest.raises(ValueError, match="SCLK"):
        learnArtifactBasis(
            [zeros, zeros], "x:AT", ["a", "b"], wavelengths=wavelengths, sclks=[1.0]
        )
    with pytest.raises(ValueError, match="shape"):
        learnArtifactBasis(
            [zeros, np.zeros((3, 438))],
            "x:AT",
            ["a", "b"],
            wavelengths=wavelengths,
            sclks=[1.0, 2.0],
        )
    with pytest.raises(ValueError, match="no artifact"):
        learnArtifactBasis(
            [zeros, zeros],
            "x:AT",
            ["a", "b"],
            wavelengths=wavelengths,
            sclks=[1.0, 2.0],
        )


def test_artifact_scale_must_be_finite() -> None:
    """A NaN scale (``artifactScale`` with too few columns) is rejected: the
    caller corrects without the basis instead."""
    _, truth = makeSyntheticCube(4, 0, **_CLEAN)
    model = _model(truth)
    with pytest.raises(ValueError, match="without the basis"):
        attrs.evolve(model, artifact=truth.artifact, artifactScale=math.nan)
    assert (
        attrs.evolve(model, artifact=truth.artifact, artifactScale=0.0).artifactScale
        == 0.0
    )


_REAL_ARTIFACT_SCAN = "061C4:AT"
_REAL_ARTIFACT_OBSERVATIONS = {
    "FRS00031442": "FRS00031442/trdr/frs00031442_01_if168l_trr3.lbl",
    "FRS0002FE75": "FRS0002FE75/trdr/frs0002fe75_01_if168l_trr3.lbl",
    "FRS00031260": "FRS00031260/trdr/frs00031260_01_if168l_trr3.lbl",
}


@attrs.frozen(eq=False)
class _RealColumns:
    spectra: np.ndarray
    spread: np.ndarray
    wavelengths: np.ndarray
    selection: atmosphere.SelectionResult
    sclk: float

    @property
    def residual(self) -> np.ndarray:
        return highPass(self.selection.fit.residuals, self.wavelengths)

    def co2Residual(self, basis: ArtifactBasis | None) -> float:
        """M7 on the corrected column spectra: the median over columns."""
        model = AtmosphereModel(self.selection.chosen, self.selection.fit.shiftNm)
        if basis is not None:
            free = attrs.evolve(model, artifact=basis.spectra)
            scale, _ = artifactScale(self.spectra, self.spread, self.wavelengths, free)
            model = attrs.evolve(free, artifactScale=scale)
        spectra, spread = self.spectra[None], self.spread[None]
        fit = fitPixels(spectra, spread, self.wavelengths, model)
        corrected, _ = correct(spectra, spread, self.wavelengths, model, fit)
        window = (self.wavelengths > 1940) & (self.wavelengths < 2100)
        columns = window.sum(axis=1) >= 4  # not the empty edge columns
        return float(
            np.nanmedian(
                validation.co2Residual(self.wavelengths[columns], corrected[0, columns])
            )
        )


def _realColumns(folder: Path, trr3Path: str) -> _RealColumns:
    """An observation's column spectra (TRR3 ÷ cos i, the instrument-noise σ)
    and its part-A fit with the artifact scan."""
    trr3 = readTrr3(folder / trr3Path)
    ddrPath = trr3Path.replace("trdr", "ddr").replace("_if1", "_de1")
    ddr = readDdr(folder / ddrPath.replace("_trr3", "_ddr1"))
    library = CalibrationLibrary(
        [folder / "ATO0004EC45"], folder.parent / "missing-cache", fetch=_noDownload
    )
    wavelengths = library.wavelengths(trr3)
    incidence = np.radians(ddr.band(DdrBand.INCIDENCE_AREOID))
    cube = trr3.lines(slice(0, trr3.shape[0])) / np.cos(incidence)[..., None]
    sigma = estimateNoise(cube).sigma(cube)
    candidates = [
        c
        for c in library.transmissions(trr3, TransmissionSource.PDS_AT)
        if c.key == _REAL_ARTIFACT_SCAN
    ]
    spectra, spread = columnSpectra(cube, sigma, wavelengths)
    selection = selectTransmission(spectra, spread, wavelengths, candidates)
    return _RealColumns(spectra, spread, wavelengths, selection, trr3.sclk)


def test_artifact_basis_leave_one_out(demoData: Path) -> None:
    """A6 (data-gated): a basis learned without observation k reduces k's
    CO2 residual (M7 on the corrected column spectra) by at least 30%
    against no basis.

    The three 2014 FRSs choose different scans on their own (061C4, 0A93E
    and 08608; FRT00005850 chooses 06822), and a basis belongs to one
    transmission, so they are pooled under one fixed scan, 061C4:AT
    (FRS00031442's choice, FRS00031260's second). Measured, none → leave-one-out (and
    in-sample with all three): FRS00031442 0.00611 → 0.00311 (0.00243),
    FRS0002FE75 0.00834 → 0.00484 (0.00420), FRS00031260 0.00622 → 0.00267
    (0.00224). FRT00005850 (2007, another WA) is not in the set: the FRSs'
    basis makes its residual 7% worse (0.00793 → 0.00848); Task 23 decides
    what a basis spans."""
    folder = demoData / "data-for-paper"
    present = {
        name: path
        for name, path in _REAL_ARTIFACT_OBSERVATIONS.items()
        if (folder / path).is_file()
    }
    if len(present) < 3:
        raise pytest.skip.Exception(
            f"needs 3 TRR3s fitted with {_REAL_ARTIFACT_SCAN}; found {sorted(present)}"
        )
    observations = {name: _realColumns(folder, path) for name, path in present.items()}
    for name, held in observations.items():
        others = [n for n in observations if n != name]
        basis = learnArtifactBasis(
            [observations[n].residual for n in others],
            _REAL_ARTIFACT_SCAN,
            others,
            wavelengths=observations[others[0]].wavelengths,
            sclks=[observations[n].sclk for n in others],
        )
        without = held.co2Residual(None)
        assert held.co2Residual(basis) <= 0.7 * without, name
