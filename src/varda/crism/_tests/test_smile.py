import re
from pathlib import Path

import numpy as np

from varda.crism._tests.synthetic import makeSyntheticCube
from varda.crism.smile import (
    EscFrame,
    applyEsc,
    estimateEsc,
    linearWeights,
    resample,
    standardVector,
)

_MTR3_HDR = Path(__file__).parent / "data" / "frt00005850_07_if167j_mtr3.hdr"
SIGMA = 0.1 / 100  # the SNR-100 σ at I/F 0.1


def _headerWavelengths() -> np.ndarray:
    text = _MTR3_HDR.read_text()
    match = re.search(r"wavelength = \{([^}]*)\}", text)
    assert match
    return np.array([float(v) for v in match.group(1).replace("\n", " ").split(",")])


def _columnWavelengths() -> np.ndarray:
    return makeSyntheticCube(8)[1].columnWavelengths


def _smooth(wavelengths: np.ndarray) -> np.ndarray:
    return (
        0.1
        + 0.05 * np.exp(-(((wavelengths - 2300.0) / 60.0) ** 2))
        + 0.02 * np.sin(wavelengths / 150.0)
    )


def test_target_vector_matches_mtrdr():
    vector = standardVector()
    assert len(vector.wavelengths) == 411
    assert vector.wavelengths[0] == 1047.20
    assert vector.wavelengths[-1] == 3896.76
    assert np.all(np.diff(vector.wavelengths) > 0)
    assert not set(range(173, 194)) & set(vector.detectorRows.tolist())
    assert vector.detectorRows.min() == 7 and vector.detectorRows.max() == 438
    assert np.all(vector.bbl[vector.wavelengths > 2600] == 0)
    assert np.all(vector.bbl[vector.wavelengths <= 2600] == 1)
    header = _headerWavelengths()
    assert len(header) == 489
    np.testing.assert_allclose(vector.wavelengths, header[-411:], atol=0.01)


def test_resampling_error_and_sigma():
    wavelengths = _columnWavelengths()
    target = standardVector().wavelengths
    weights = linearWeights(wavelengths, target)
    lines = 300
    truth = np.broadcast_to(_smooth(wavelengths), (lines,) + wavelengths.shape)
    sigma = np.full(truth.shape, SIGMA)

    clean, _ = resample(truth, sigma, weights)
    expected = _smooth(target)[None, None, :]
    error = (clean - expected)[np.isfinite(clean)]
    assert error.size > 0.8 * clean.size
    assert np.sqrt(np.mean(error**2)) <= 0.2 * SIGMA

    noisy = truth + np.random.default_rng(1).normal(0, SIGMA, truth.shape)
    out, outSigma = resample(noisy, sigma, weights)
    finite = np.isfinite(out[0])
    empirical = np.sqrt(np.mean(np.var(out, axis=0)[finite]))
    propagated = np.sqrt(np.mean(outSigma[0][finite] ** 2))
    assert abs(empirical / propagated - 1) < 0.03


def test_outside_range_is_nan():
    wavelengths = _columnWavelengths().copy()
    wavelengths[5, wavelengths[5] > 2600] = np.nan
    target = standardVector().wavelengths
    weights = linearWeights(wavelengths, target)
    cube = np.ones((2,) + wavelengths.shape)
    out, sigma = resample(cube, 0.01 * cube, weights)
    above = target > 2600
    assert np.all(np.isnan(out[:, 5, above])) and np.all(np.isnan(sigma[:, 5, above]))
    assert np.all(weights.weights[5, above] == 0)
    assert np.all(np.isfinite(out[:, 5, target < 2590]))
    assert np.all(np.isfinite(out[:, 4, target < 3800]))


def test_nan_source_tap_gives_nan():
    wavelengths = np.tile(np.arange(10.0, 20.0), (2, 1))
    weights = linearWeights(wavelengths, np.array([12.5, 14.0]))
    cube = np.ones((1, 2, 10))
    cube[0, 0, 3] = np.nan
    cube[0, 1, 5] = np.nan
    out, _ = resample(cube, cube, weights)
    assert np.isnan(out[0, 0, 0]) and np.isfinite(out[0, 1, 0])
    assert out[0, 1, 1] == 1.0  # an exact hit: the NaN neighbor has weight 0


def test_filter_join_is_not_bridged():
    wavelengths = np.array([[1640.0, 1650.0, 1660.0, 1670.0]])
    values = np.array([[[1.0, 1.0, 5.0, 5.0]]])
    weights = linearWeights(wavelengths, np.array([1652.0, 1655.0, 1658.0, 1665.0]))
    out, _ = resample(values, values, weights)
    np.testing.assert_allclose(out[0, 0], [1.0, 1.0, 5.0, 5.0])


def test_esc_removes_column_slope():
    """ESC works on bland pixels: here the median spectrum of the scene (the
    same in every column) with 0.1% column noise, times an error linear in the
    column's wavelength offset, 0.5% per nm, its sign alternating in pairs of
    bands like a spectral derivative (a smooth error would be removed with the
    continuum)."""
    cube, truth = makeSyntheticCube(8)
    wavelengths = truth.columnWavelengths
    target = standardVector().wavelengths
    weights = linearWeights(wavelengths, target)
    reference, _ = resample(cube, np.ones_like(cube), weights)
    scene = np.nanmedian(np.nanmedian(reference, axis=0), axis=0)
    noise = np.random.default_rng(2).normal(
        0, 0.001, (wavelengths.shape[0], len(scene))
    )
    spectra = scene[None, :] * (1 + noise)
    offsets = _nativeOffsets(wavelengths, target, weights)
    assert np.nanmax(np.abs(offsets)) > 1.0
    pairs = np.where(np.arange(len(target)) // 2 % 2 == 0, 1.0, -1.0)
    observed = spectra * (1.0 + 0.005 * offsets * pairs[None, :])

    frame = estimateEsc(observed, offsets, 0.125, target)
    fixed, _ = applyEsc(observed[None], np.ones((1,) + spectra.shape), frame)
    before = np.nanstd(observed / spectra - 1)
    after = np.nanstd(fixed[0] / spectra - 1)
    assert before > 0.002
    assert after < before / 10
    assert isinstance(frame, EscFrame)


def _nativeOffsets(wavelengths, target, weights):
    """Each column's nearest source wavelength minus the target, per target band."""
    nearest = np.where(
        weights.weights[..., 1] > 0.5, weights.indices[..., 1], weights.indices[..., 0]
    )
    native = np.take_along_axis(wavelengths, nearest, axis=1)
    return native - target[None, :]
