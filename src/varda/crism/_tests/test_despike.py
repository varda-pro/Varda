import time

import numpy as np
import pytest

from varda.crism.despike import (
    ALONG_TRACK_HALO,
    SpikeReport,
    _nanMedian,
    _segments,
    _spectralMedian,
    despike,
)
from varda.crism._tests.real_data import realChunk
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


@pytest.mark.parametrize("profile", [(1, 1, 1), (0.5, 1, 0.5)], ids=["flat", "V"])
def test_preserves_narrow_real_feature(profile):
    cube, truth = _quietCube()
    rows = slice(30, 35)
    columns = np.arange(50, 55)
    center = _bandAt(truth.columnWavelengths, 2300.0)[columns]
    for column, band in zip(columns, center):
        for offset, depth in zip((-1, 0, 1), profile):
            cube[rows, column, band + offset] *= 1 - 0.1 * depth
    _, _, report = _whole(cube, truth.noiseSigma, truth.columnWavelengths)
    assert report.perPixel[rows, 50:55].sum() == 0


def test_step_at_a_filter_boundary_is_not_a_spike():
    """A per-column step between two filter segments (spec: the 1.65 µm join)."""
    cube, truth = _quietCube()
    join = int(
        np.argmax(_segments(truth.columnWavelengths) == 1)
    )  # first band above 1656 nm
    step = np.random.default_rng(3).normal(0.0, 0.04, cube.shape[1])
    cube[:, :, join:] *= 1 + step[None, :, None]
    _, _, report = _whole(cube, truth.noiseSigma, truth.columnWavelengths)
    assert np.isin(report.positions[:, 2], [join - 1, join]).sum() <= 2


def test_spectral_median_stops_at_a_filter_boundary():
    wavelengths = np.tile(1620.0 + 6.55 * np.arange(12), (3, 1))  # 1656 nm after band 5
    segment = _segments(wavelengths)
    assert segment.tolist() == [0] * 6 + [1] * 6
    middle, _ = _spectralMedian(np.tile(np.arange(12.0), (2, 3, 1)), segment)
    finite = np.isfinite(middle[0, 0])
    assert np.nonzero(finite)[0].tolist() == [2, 3, 8, 9]


def test_chunk_halo_equivalence(synthetic):
    cube, truth = synthetic
    sigma, wavelengths = truth.noiseSigma, truth.columnWavelengths
    whole = _whole(cube, sigma, wavelengths)
    n = ALONG_TRACK_HALO
    first = despike(cube[: 32 + n], sigma[: 32 + n], wavelengths, halo=0, haloBelow=n)
    second = despike(
        cube[32 - n :], sigma[32 - n :], wavelengths, firstLine=32, halo=n, haloBelow=0
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
    # one spike in sixty sits on a steep slope with a matching line neighbor
    assert (report.leaskCount[inWindow] == 1).mean() >= 0.95
    assert (report.perPixel[inWindow] == 1).mean() >= 0.95
    # the other flags are the rate of false ones; leaskCount covers the window only
    assert abs(int(report.perPixel.sum()) - len(spots)) <= 20
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


def _train(cube, truth, profile, seed, low=12.0, count=30):
    """Spike trains on separate detector elements: ``profile`` (a tuple of
    multipliers) times 12-20 sigma down the lines, at one band each."""
    rng = np.random.default_rng(seed)
    cube = cube.copy()
    hits = []
    for k in range(count):
        line = 6 + (k % 6) * 9
        column = 8 + (k // 6) * 22 + int(rng.integers(0, 10))
        band = int(rng.integers(40, 120))
        amplitude = rng.uniform(low, 20) * rng.choice((-1.0, 1.0))
        for i, share in enumerate(profile):
            cube[line + i, column, band] += (
                amplitude * share * truth.noiseSigma[line + i, column, band]
            )
            hits.append((line + i, column, band))
    return cube, np.array(hits)


@pytest.mark.parametrize(
    "profile",
    [(1.0, 1.0), (1.0, 1.0, 1.0), (0.55, 1.0, 0.7), (1.0, 0.6)],
    ids=["flat2", "flat3", "rise-decay", "decay"],
)
def test_short_trains_are_spikes(profile):
    cube, truth = _quietCube()
    # every sample of every train is 10-20 sigma
    spiked, hits = _train(cube, truth, profile, seed=5, low=18.0)
    _, _, report = _whole(spiked, truth.noiseSigma, truth.columnWavelengths)
    found = np.zeros(cube.shape, dtype=bool)
    found[tuple(report.positions.T)] = True
    assert found[tuple(hits.T)].mean() >= 0.9


def test_a_long_element_offset_is_not_a_spike():
    cube, truth = _quietCube()
    cube = cube.copy()
    columns = np.arange(10, 110, 5)
    for column in columns:
        band = 40 + column % 30
        cube[20:32, column, band] += 8 * truth.noiseSigma[20:32, column, band]
    _, _, report = _whole(cube, truth.noiseSigma, truth.columnWavelengths)
    interior = report.perPixel[23:29][:, columns]  # 3+ lines inside the offset
    # onsets and ends of a step look like a train; the scene's own gradient
    # along the track leaves a stray flag now and then
    assert interior.sum() <= 0.05 * interior.size


def test_halo_arguments_are_validated(synthetic):
    cube, truth = synthetic
    args = (cube[:4], truth.noiseSigma[:4], truth.columnWavelengths)
    with pytest.raises(ValueError, match="negative"):
        despike(*args, halo=-1)
    with pytest.raises(ValueError, match="negative"):
        despike(*args, halo=0, haloBelow=-1)
    with pytest.raises(ValueError, match="none between"):
        despike(*args, halo=2, haloBelow=2)
    with pytest.raises(ValueError, match="sigma"):
        despike(cube[:4], truth.noiseSigma[:3], truth.columnWavelengths, halo=1)


def test_real_chunk_has_no_hot_bands(demoData):
    """FRS00031442 lines 0-63: spikes are rare and no filter join piles up."""
    cube, wavelengths = realChunk(demoData)
    sigma = estimateNoise(cube).sigma(cube)
    _, _, report = _whole(cube, sigma, wavelengths)
    valid = np.isfinite(cube)
    flagged = np.zeros(cube.shape, dtype=bool)
    flagged[tuple(report.positions.T)] = True
    overall = flagged.sum() / valid.sum()
    assert overall <= 3e-4
    perBand = flagged.sum(axis=(0, 1))
    samples = valid.sum(axis=(0, 1))
    rate = perBand / np.maximum(samples, 1)
    median = np.median(rate[samples > 0])
    # a band holding a handful of flags is Poisson noise, not a pile-up
    crowded = perBand >= 30
    assert (rate[crowded] <= 10 * max(median, overall)).all()
    for low, high in [(1640.0, 1660.0), (2690.0, 2800.0)]:
        near = (_nanMedian(wavelengths) >= low) & (_nanMedian(wavelengths) < high)
        assert flagged[:, :, near].sum() / valid[:, :, near].sum() <= 10 * overall
