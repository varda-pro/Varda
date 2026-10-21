import time

import attrs
import numpy as np
import pytest
from numpy.polynomial import legendre

from varda.crism._tests.synthetic import SyntheticTruth, makeSyntheticCube

FIT_WINDOW = (1850.0, 2200.0)
SCORE_WINDOW = (1947.0, 2066.0)


def _shiftedLogTransmission(truth: SyntheticTruth) -> np.ndarray:
    """ln T_x(λ + δ_x), interpolated along each column's own wavelengths."""
    result = np.full(truth.transmission.shape, np.nan)
    for x, (wavelengths, transmission) in enumerate(
        zip(truth.columnWavelengths, truth.transmission)
    ):
        valid = np.isfinite(wavelengths)
        result[x, valid] = np.interp(
            wavelengths[valid] + truth.shiftNm[x],
            wavelengths[valid],
            np.log(transmission[valid]),
        )
    return result


def test_deterministic() -> None:
    cube, truth = makeSyntheticCube(lines=16, seed=3)
    again, truthAgain = makeSyntheticCube(lines=16, seed=3)
    np.testing.assert_array_equal(cube, again)
    for field in attrs.fields(SyntheticTruth):
        np.testing.assert_array_equal(
            getattr(truth, field.name), getattr(truthAgain, field.name)
        )
    other, _ = makeSyntheticCube(lines=16, seed=4)
    assert not np.array_equal(cube, other, equal_nan=True)


def test_truth_consistency() -> None:
    # The observed cube is photometrically corrected I/F: no cos i factor
    cube, truth = makeSyntheticCube()
    assert cube.shape == (64, 120, 438)
    atmosphere = np.exp(truth.beta[..., None] * _shiftedLogTransmission(truth))
    artifact = np.exp(truth.beta[..., None] * truth.artifact)  # α = β
    factor = atmosphere * truth.stripes * artifact
    residual = cube / factor - truth.surface
    z = residual * factor / truth.noiseSigma
    spike = np.zeros(cube.shape, dtype=bool)
    spike[tuple(truth.spikes.T)] = True
    assert np.isfinite(cube[..., :-1]).all()
    valid = np.isfinite(z) & ~spike
    assert abs(np.std(z[valid]) - 1.0) < 0.05
    assert abs(np.mean(z[valid])) < 0.01
    # the placeholder band past the last wavelength stays invalid
    assert np.isnan(cube[..., -1]).all()
    assert np.isnan(truth.columnWavelengths[:, -1]).all()


def test_column_unit_spans_all_lines() -> None:
    _, truth = makeSyntheticCube()
    assert truth.columnUnitMask.all(axis=0).any()
    assert not truth.columnUnitMask.all()


def test_physics_contract() -> None:
    cube, truth = makeSyntheticCube(seed=1)
    # β follows the elevation exactly: ln β = a − z / H, H = 11 km
    slope = np.polyfit(truth.elevationM.ravel(), np.log(truth.beta).ravel(), 1)[0]
    assert -1.0 / slope / 1000.0 == pytest.approx(11.0, rel=1e-9)
    assert 2000.0 < np.ptp(truth.elevationM) < 3500.0
    # smooth δ_x of ±0.8 nm; incidence 50–55°
    assert 0.6 < np.abs(truth.shiftNm).max() <= 0.8
    assert np.abs(np.diff(truth.shiftNm)).max() < 0.05
    assert 50.0 <= truth.incidenceDeg.min() and truth.incidenceDeg.max() <= 55.0
    # the artifact: only inside the fit window, high-pass there, RMS 0.006 in
    # the score window
    for wavelengths, artifact in zip(truth.columnWavelengths, truth.artifact):
        inside = (wavelengths >= FIT_WINDOW[0]) & (wavelengths <= FIT_WINDOW[1])
        assert np.all(artifact[~inside] == 0.0)
        u = np.interp(wavelengths[inside], FIT_WINDOW, (-1.0, 1.0))
        coefficients = legendre.legfit(u, artifact[inside], 3)
        assert np.abs(coefficients).max() < 1e-12
        score = (wavelengths >= SCORE_WINDOW[0]) & (wavelengths <= SCORE_WINDOW[1])
        assert np.sqrt(np.mean(artifact[score] ** 2)) == pytest.approx(0.006)
    # noise: SNR 100 at I/F 0.1; spikes are 5–20 σ, both signs
    bright = np.abs(cube - 0.1) < 0.005
    snr = 0.1 / np.median(truth.noiseSigma[bright])
    assert 95.0 < snr < 105.0
    assert 0.5 * 1e-4 < len(truth.spikes) / cube[..., :-1].size < 1.5 * 1e-4
    assert np.all((np.abs(truth.spikeSigmas) >= 5) & (np.abs(truth.spikeSigmas) <= 20))
    assert (truth.spikeSigmas > 0).any() and (truth.spikeSigmas < 0).any()
    # the surface is Mars-like I/F over 1.0–2.6 µm
    sweep = (truth.columnWavelengths >= 1000) & (truth.columnWavelengths <= 2600)
    surface = truth.surface[:, sweep]
    assert 0.05 < surface.min() and surface.max() < 0.35


def test_fast() -> None:
    makeSyntheticCube()  # warm the library cache
    start = time.perf_counter()
    makeSyntheticCube(seed=5)
    assert time.perf_counter() - start < 5.0  # about 0.04 s; catches gross slowdowns
