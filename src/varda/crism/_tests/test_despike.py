import time

import numpy as np
import pytest

from varda.crism.despike import SpikeReport, despike
from varda.crism.noise import estimateNoise
from varda.crism._tests.synthetic import makeSyntheticCube

RECALL_SIGMAS = 8.0


def _whole(cube, sigma, wavelengths):
    """A whole scene has no halo lines."""
    return despike(cube, sigma, wavelengths, halo=0)


@pytest.fixture(scope="module")
def synthetic():
    return makeSyntheticCube(lines=64, seed=0)


def _rates(cube, truth, sigma):
    _, _, report = _whole(cube, sigma, truth.columnWavelengths)
    found = np.zeros(cube.shape, dtype=bool)
    found[tuple(report.positions.T)] = True
    isSpike = np.zeros(cube.shape, dtype=bool)
    isSpike[tuple(truth.spikes.T)] = True
    big = np.abs(truth.spikeSigmas) >= RECALL_SIGMAS
    recall = found[tuple(truth.spikes[big].T)].mean()
    valid = np.isfinite(cube) & ~isSpike
    return recall, (found & valid).sum() / valid.sum(), int(big.sum())


def test_recall_and_false_rate(synthetic):
    cube, truth = synthetic
    recall, falseRate, count = _rates(cube, truth, truth.noiseSigma)
    assert count > 100
    assert recall >= 0.95
    assert falseRate <= 1e-5


def test_recall_and_false_rate_with_estimated_sigma(synthetic):
    cube, truth = synthetic
    sigma = estimateNoise(cube).sigma(cube)
    recall, falseRate, _ = _rates(cube, truth, sigma)
    assert recall >= 0.95
    assert falseRate <= 1e-5


def _bandAt(wavelengths: np.ndarray, nm: float) -> np.ndarray:
    return np.nanargmin(np.abs(wavelengths - nm), axis=1)  # per column


def _quietCube():
    return makeSyntheticCube(lines=64, seed=1, spikeRate=0.0)


@pytest.mark.parametrize("flatBottom", [True, False])
def test_preserves_narrow_real_feature(flatBottom):
    cube, truth = _quietCube()
    rows = slice(30, 35)
    columns = np.arange(50, 55)
    center = _bandAt(truth.columnWavelengths, 2300.0)[columns]
    profile = (1, 1, 1) if flatBottom else (0.5, 1, 0.5)
    for column, band in zip(columns, center):
        for offset, depth in zip((-1, 0, 1), profile):
            cube[rows, column, band + offset] *= 1 - 0.1 * depth
    _, _, report = _whole(cube, truth.noiseSigma, truth.columnWavelengths)
    # a V-shaped feature is only safe where every neighbor shares it
    checked = (
        report.perPixel[31:34, 51:54]
        if not flatBottom
        else report.perPixel[rows, 50:55]
    )
    assert checked.sum() == 0


def test_chunk_halo_equivalence(synthetic):
    cube, truth = synthetic
    sigma, wavelengths = truth.noiseSigma, truth.columnWavelengths
    whole = _whole(cube, sigma, wavelengths)
    first = despike(cube[:33], sigma[:33], wavelengths, halo=0, haloBelow=1)
    second = despike(
        cube[31:], sigma[31:], wavelengths, firstLine=32, halo=1, haloBelow=0
    )
    np.testing.assert_array_equal(whole[0], np.concatenate([first[0], second[0]]))
    np.testing.assert_array_equal(whole[1], np.concatenate([first[1], second[1]]))
    merged = first[2].merged(second[2])
    np.testing.assert_array_equal(whole[2].perPixel, merged.perPixel)
    np.testing.assert_array_equal(whole[2].leaskCount, merged.leaskCount)
    np.testing.assert_array_equal(whole[2].positions, merged.positions)
    assert isinstance(merged, SpikeReport)
    assert whole[2].positions.shape[0] > 0


def test_leask_count():
    cube, truth = _quietCube()
    band = _bandAt(truth.columnWavelengths, 2000.0)
    spots = [(10 + 8 * i, 10 + 9 * j) for i in range(6) for j in range(10)]
    for line, column in spots:
        cube[line, column, band[column]] += (
            15 * truth.noiseSigma[line, column, band[column]]
        )
    _, _, report = _whole(cube, truth.noiseSigma, truth.columnWavelengths)
    inWindow = np.zeros(report.perPixel.shape, dtype=bool)
    for line, column in spots:
        inWindow[line, column] = True
    assert (report.leaskCount[inWindow] == 1).all()
    assert (report.perPixel[inWindow] == 1).all()
    # the other flags are the rate of false ones; leaskCount covers the window only
    assert report.perPixel.sum() - len(spots) <= 20
    inside = [
        1850.0 <= truth.columnWavelengths[x, b] <= 2150.0
        for _, x, b in report.positions
    ]
    assert report.leaskCount.sum() == sum(inside)


def test_nan_is_never_flagged_or_evidence(synthetic):
    cube, truth = synthetic
    cube = cube.copy()
    cube[10:12, :, 100] = np.nan
    cube[40, 60, :] = np.nan
    out, outSigma, report = _whole(cube, truth.noiseSigma, truth.columnWavelengths)
    assert np.array_equal(np.isnan(out), np.isnan(cube))
    assert not np.isnan(cube[tuple(report.positions.T)]).any()
    assert report.perPixel[40, 60] == 0
    # the NaN lines do not turn the band's other samples into spikes
    assert report.positions.shape[0] < 400


def test_replaced_samples_get_prediction_and_sigma_p(synthetic):
    cube, truth = synthetic
    out, outSigma, report = _whole(cube, truth.noiseSigma, truth.columnWavelengths)
    index = tuple(report.positions.T)
    changed = ~np.isclose(out, cube, rtol=0, atol=0, equal_nan=True)
    assert changed.sum() == len(report.positions)
    assert changed[index].all()
    assert (outSigma[index] > 0).all()
    unchanged = np.isclose(outSigma, truth.noiseSigma, rtol=0, atol=0, equal_nan=True)
    assert unchanged[~changed].all()
    # the replacement is near the unspiked truth, within a few noise sigmas
    spikes = tuple(truth.spikes.T)
    caught = changed[spikes]
    error = np.abs(
        out[spikes] - cube[spikes] + truth.spikeSigmas * truth.noiseSigma[spikes]
    )
    assert np.median(error[caught] / truth.noiseSigma[spikes][caught]) < 3


def test_speed(synthetic):
    cube, truth = synthetic
    start = time.perf_counter()
    _whole(cube, truth.noiseSigma, truth.columnWavelengths)
    assert time.perf_counter() - start < 10
