import re
from pathlib import Path

import numpy as np

from varda.crism._tests.synthetic import makeSyntheticCube
from varda.crism.brightness import segmentIds
from varda.crism.readers import readDetectorFrame, readTrr3
from varda.crism.smile import (
    EscFrame,
    escOffsets,
    applyEsc,
    estimateEsc,
    linearWeights,
    resample,
    sourceSegments,
    standardVector,
)

_MTR3_HDR = Path(__file__).parent / "data" / "frt00005850_07_if167j_mtr3.hdr"
_DATA = Path(__file__).parent / "data"
_TRR3 = _DATA / "frs00031442_01_if168l_trr3.lbl"
_WA = _DATA / "cdr490947778566_wa0000000l_3.lbl"
SIGMA = 0.1 / 100  # the SNR-100 σ at I/F 0.1


def _headerWavelengths() -> np.ndarray:
    text = _MTR3_HDR.read_text()
    match = re.search(r"wavelength = \{([^}]*)\}", text)
    assert match
    return np.array([float(v) for v in match.group(1).replace("\n", " ").split(",")])


def _columnWavelengths() -> np.ndarray:
    return makeSyntheticCube(8)[1].columnWavelengths


def _weights(wavelengths: np.ndarray, target: np.ndarray):
    return linearWeights(
        wavelengths,
        target,
        sourceSegments(wavelengths),
        segmentIds(target),
    )


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
    weights = _weights(wavelengths, target)
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
    empirical = np.sqrt(np.var(out, axis=0))
    perBand = [
        np.sqrt(np.mean(empirical[finite[:, b], b] ** 2))
        / np.sqrt(np.mean(outSigma[0][finite[:, b], b] ** 2))
        - 1
        for b in range(finite.shape[1])
        if finite[:, b].sum() > 20
    ]
    assert np.median(np.abs(perBand)) < 0.01
    assert np.max(np.abs(perBand)) < 0.05


def test_outside_range_is_nan():
    wavelengths = _columnWavelengths().copy()
    wavelengths[5, wavelengths[5] > 2600] = np.nan
    target = standardVector().wavelengths
    weights = _weights(wavelengths, target)
    cube = np.ones((2,) + wavelengths.shape)
    out, sigma = resample(cube, 0.01 * cube, weights)
    above = target > 2600
    assert np.all(np.isnan(out[:, 5, above])) and np.all(np.isnan(sigma[:, 5, above]))
    assert np.all(weights.weights[5, above] == 0)
    assert np.all(np.isfinite(out[:, 5, target < 2590]))
    assert np.all(np.isfinite(out[:, 4, target < 3800]))


def test_nan_source_tap_gives_nan():
    wavelengths = np.tile(np.arange(10.0, 20.0), (2, 1))
    weights = linearWeights(
        wavelengths, np.array([12.5, 14.0]), np.zeros(10, int), np.zeros(2, int)
    )
    cube = np.ones((1, 2, 10))
    cube[0, 0, 3] = np.nan
    cube[0, 1, 5] = np.nan
    out, _ = resample(cube, cube, weights)
    assert np.isnan(out[0, 0, 0]) and np.isfinite(out[0, 1, 0])
    assert out[0, 1, 1] == 1.0  # an exact hit: the NaN neighbor has weight 0


def test_filter_join_is_not_bridged():
    """On the real crop WA, plain and with an added parabolic smile that
    moves the columns' joining rows across 1656 nm, no target takes a tap from
    another filter segment, and none blends the rows either side of the
    1.65 um join (detector rows 346 and 345)."""
    rows = readTrr3(_TRR3).detectorRows
    wa = readDetectorFrame(_WA).alignedTo(rows)
    target = standardVector().wavelengths
    row346, row345 = (int(np.flatnonzero(rows == r)[0]) for r in (346, 345))
    across = np.linspace(-0.2, 0.8, wa.shape[0])[:, None]
    segments = sourceSegments(wa)
    crossed = False
    for wavelengths in (wa, wa + 10.0 * across**2 / 0.64):
        crossed |= bool(
            (wavelengths[:, row345] < 1656).any()
            or (wavelengths[:, row346] > 1656).any()
        )
        weights = _weights(wavelengths, target)
        used = weights.weights > 0
        assert np.all(
            (segments[weights.indices] == segmentIds(target)[None, :, None]) | ~used
        )
        taps = np.where(used, weights.indices, -1)
        assert not ((taps == row346).any(axis=2) & (taps == row345).any(axis=2)).any()
    assert crossed  # the shifted smile does cross the join in nm


def test_esc_removes_column_slope():
    """Offsets of the real kind (the crop WA at the target rows minus the
    target: about 0 to 10 nm, parabolic across the columns) and a spectral
    sensitivity: the observed spectrum is the scene sampled o nm off, an error
    linear in the offset (0.5% per nm of the band's log slope scale)."""
    vector = standardVector()
    wa = readDetectorFrame(_WA).alignedTo(vector.detectorRows)
    target = vector.wavelengths
    # the crop holds only central columns: add the parabola of the full frame
    across = np.linspace(-0.2, 0.8, wa.shape[0])[:, None]
    offsets = escOffsets(wa + 10.0 * across**2 / 0.64, target)
    assert np.nanmax(offsets) > 5.0
    scene = _smooth(target)[None, :] + 0.03 * np.sin(target / 20.0)[None, :]
    slope = np.gradient(np.log(scene[0]), target)[None, :]  # d ln S / d lambda
    noise = np.random.default_rng(2).normal(0, 0.0005, offsets.shape)
    error = 1.0 + 0.5 * slope * offsets  # = S(lambda + o / 2) / S(lambda)
    spectra = scene * (1 + noise)
    observed = spectra * error

    frame = estimateEsc(observed, offsets, 0.125, target)
    fixed, _ = applyEsc(observed[None], np.ones((1,) + spectra.shape), frame)
    ok = np.isfinite(offsets)
    before = np.std((observed / spectra - 1)[ok])
    after = np.std((fixed[0] / spectra - 1)[ok])
    assert before > 0.002
    assert after < before / 10
    assert isinstance(frame, EscFrame)
