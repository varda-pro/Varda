from functools import cache
from pathlib import Path

import numpy as np

from varda.crism import destripe
from varda.crism.brightness import segmentIds
from scipy import ndimage

from varda.crism import validation
from varda.crism._tests.synthetic import SyntheticTruth, makeSyntheticCube
from varda.crism.atmosphere import shiftedLogTransmission
from varda.crism.destripe import (
    StripeFrame,
    applyStripes,
    blandMask,
    estimateStripes,
)
from varda.crism.noise import estimateNoise

_STRIPES = 0.005  # the synthetic default stripeAmplitude
_METRIC_NM = (1000.0, 2600.0)


def _atmosphere(truth: SyntheticTruth) -> np.ndarray:
    """The truth atmosphere and artifact factor ``(l, s, b)``."""
    logT = shiftedLogTransmission(
        truth.columnWavelengths, truth.transmission, truth.shiftNm
    )
    return np.exp(truth.beta[..., None] * (logT + truth.artifact))


def _afterAtmosphere(
    lines: int, snr: float = 1000.0
) -> tuple[np.ndarray, np.ndarray, SyntheticTruth]:
    """A synthetic cube after a truth-based atmosphere removal (surface ×
    stripes + noise) and its truth σ."""
    cube, truth = makeSyntheticCube(lines=lines, snr=snr)
    atmosphere = _atmosphere(truth)
    return cube / atmosphere, truth.noiseSigma / atmosphere, truth


@cache
def _scene() -> tuple[np.ndarray, np.ndarray, SyntheticTruth, np.ndarray, StripeFrame]:
    """192 lines at SNR 1000, so the column medians' own noise (about
    1.25 σ/√l) stays well under the 0.05% bar; σ from the noise model."""
    cube, _, truth = _afterAtmosphere(192)
    sigma = estimateNoise(
        cube, wavelengths=np.median(truth.columnWavelengths, axis=0)
    ).sigma(cube)
    bland = blandMask(cube, truth.columnWavelengths)
    frame = estimateStripes(cube, sigma, truth.columnWavelengths, bland)
    return cube, sigma, truth, bland, frame


def _stripeRms(cube: np.ndarray, truth: SyntheticTruth, columns: np.ndarray) -> float:
    """The M11 stripe metric against the truth: over ``columns`` and the
    1.0–2.6 µm bands, the RMS of ln(column median of cube ÷ truth surface)
    less its 15-column running median per band. Only column-scale stripe is
    measured: structure wider than that is scene to the frame (its column
    scale step), and the share of the stripes there is neither removed nor
    counted."""
    ratio = np.log(np.median(cube / truth.surface, axis=0))  # (s, b)
    wavelengths = np.median(truth.columnWavelengths, axis=0)
    bands = (wavelengths >= _METRIC_NM[0]) & (wavelengths <= _METRIC_NM[1])
    values = ratio[columns][:, bands]
    running = ndimage.median_filter(values, size=(15, 1), mode="nearest")
    return float(np.sqrt(np.mean((values - running) ** 2)))


def _unitColumns(truth: SyntheticTruth) -> np.ndarray:
    return np.all(truth.columnUnitMask, axis=0)


def test_bland_mask_excludes_absorbers() -> None:
    _, _, truth, bland, _ = _scene()
    assert bland.shape == truth.units.shape
    for unit in (1, 3):  # the 1.9 µm hydrated phase and the carbonate
        assert not bland[truth.units == unit].any()
    assert bland[truth.units == 0].mean() > 0.4


def test_removes_injected_stripes() -> None:
    # S1: after a truth-based atmosphere removal, the column stripes drop to
    # at most a tenth of the injected 0.5% (columns that have bland pixels;
    # the column-aligned unit has none and is left alone, S2)
    cube, sigma, truth, _, frame = _scene()
    columns = ~_unitColumns(truth)
    before = _stripeRms(cube, truth, columns)
    corrected, _ = applyStripes(cube, sigma, frame)
    after = _stripeRms(corrected, truth, columns)
    assert before > 0.8 * _STRIPES
    assert after <= 0.1 * _STRIPES
    assert frame.smoothAccepted[columns].mean() > 0.9


def test_preserves_column_aligned_unit() -> None:
    # S2: the carbonate unit spans every line; its 2.3 µm band depth keeps at
    # least 95% of the truth after the correction
    cube, sigma, truth, _, frame = _scene()
    corrected, _ = applyStripes(cube, sigma, frame)
    unit = truth.columnUnitMask
    wavelengths = np.median(truth.columnWavelengths, axis=0)
    depth = np.median(validation.bd2310(wavelengths, corrected[unit]))
    truthDepth = np.median(validation.bd2310(wavelengths, truth.surface[unit]))
    assert truthDepth > 0.03
    assert depth >= 0.95 * truthDepth
    # its columns carry no factor: nothing about them came from geology
    assert np.all(frame.factors[_unitColumns(truth)] == 1.0)


def test_smooth_part_needs_split_half() -> None:
    # A 3% offset in one column over the first half of the lines only: real
    # geology, not a detector element, so the smooth part stays
    cube, sigma, truth = _afterAtmosphere(128)
    # the bland pixels of the unchanged scene: an offset in one band would
    # also raise those pixels' variance and take them out of the mask, and
    # this test is about the split-half test, not the mask
    bland = blandMask(cube, truth.columnWavelengths)
    clean = np.nonzero((truth.units[:64] == 0).all(axis=0))[0]
    column = int(clean[np.argmin(np.abs(clean - 60))])  # away from the edges
    cube[:64, column] *= 1.03
    sigma[:64, column] *= 1.03
    # and one element (a column at one band) 2% off in the first half only:
    # not persistent, so its h stays too
    element = int(clean[np.argmin(np.abs(clean - 90))])
    centers = np.median(truth.columnWavelengths, axis=0)  # NaN: the placeholder
    band = int(np.nanargmin(np.abs(centers - 1500.0)))
    cube[:64, element, band] *= 1.02
    sigma[:64, element, band] *= 1.02
    frame = estimateStripes(cube, sigma, truth.columnWavelengths, bland)
    assert not frame.smoothAccepted[column]
    assert not frame.highFrequencyKept[element, band]
    assert frame.highFrequencyKept[element].mean() > 0.8  # the rest of it goes
    others = ~_unitColumns(truth)
    others[[column, element]] = False
    assert frame.smoothAccepted[others].mean() > 0.9
    corrected, _ = applyStripes(cube, sigma, frame)
    kept = np.median((corrected / truth.surface)[:64, column, :-1])
    assert kept > 1.02
    # without the split-half test, the offset counts as a detector's
    loose = estimateStripes(
        cube, sigma, truth.columnWavelengths, bland, splitHalf=False
    )
    assert loose.smoothAccepted[column]
    assert loose.highFrequencyKept[element, band]


def test_no_harm_at_default_snr() -> None:
    # At the synthetic default SNR 100 the frame is noisier, but it must not
    # add stripes, nor touch the column-aligned unit
    cube, _, truth = _afterAtmosphere(128, snr=100.0)
    sigma = estimateNoise(
        cube, wavelengths=np.median(truth.columnWavelengths, axis=0)
    ).sigma(cube)
    bland = blandMask(cube, truth.columnWavelengths)
    frame = estimateStripes(cube, sigma, truth.columnWavelengths, bland)
    corrected, _ = applyStripes(cube, sigma, frame)
    unitColumns = _unitColumns(truth)
    columns = ~unitColumns
    before = _stripeRms(cube, truth, columns)
    assert _stripeRms(corrected, truth, columns) <= 1.05 * before
    unit = truth.columnUnitMask
    wavelengths = np.median(truth.columnWavelengths, axis=0)
    depth = np.median(validation.bd2310(wavelengths, corrected[unit]))
    truthDepth = np.median(validation.bd2310(wavelengths, truth.surface[unit]))
    assert depth >= 0.95 * truthDepth
    np.testing.assert_array_equal(
        corrected[:, unitColumns], cube[:, unitColumns].astype(np.float64)
    )


def test_sigma_is_scaled_frame_uncertainty_is_systematic() -> None:
    # σ is random only (N2): the frame divides it, σ′ = σ/f, and the frame's
    # own bootstrap σ is a systematic term reported apart (the provenance)
    cube, sigma, _, _, frame = _scene()
    corrected, sigmaOut = applyStripes(cube, sigma, frame)
    valid = np.isfinite(cube) & np.isfinite(sigma)
    assert np.isnan(corrected[~np.isfinite(cube)]).all()
    np.testing.assert_allclose(sigmaOut[valid], (sigma / frame.factors)[valid])
    estimated = frame.sigma > 0
    assert estimated.mean() > 0.8
    assert np.all(frame.sigma >= 0) and np.isfinite(frame.sigma).all()


def test_memmap_input_matches(tmp_path: Path) -> None:
    cube, sigma, truth = _afterAtmosphere(32)
    bland = blandMask(cube, truth.columnWavelengths)
    stored = np.memmap(
        tmp_path / "cube.f32", dtype=np.float32, mode="w+", shape=cube.shape
    )
    stored[:] = cube
    storedSigma = np.memmap(
        tmp_path / "sigma.f32", dtype=np.float32, mode="w+", shape=cube.shape
    )
    storedSigma[:] = sigma
    fromMemory = estimateStripes(
        cube.astype(np.float32),
        sigma.astype(np.float32),
        truth.columnWavelengths,
        bland,
    )
    fromDisk = estimateStripes(stored, storedSigma, truth.columnWavelengths, bland)
    np.testing.assert_array_equal(fromMemory.factors, fromDisk.factors)

    np.testing.assert_array_equal(
        blandMask(stored, truth.columnWavelengths),
        blandMask(cube.astype(np.float32), truth.columnWavelengths),
    )


def test_smooth_parts_use_each_bands_segment() -> None:
    """Final review: a band's filter segment is fixed (the join is a detector
    row), so a column whose own wavelength for the last band before the join
    is past 1656 nm still fits that band with its segment."""
    wavelengths = np.linspace(1500.0, 1800.0, 40)
    segment = segmentIds(wavelengths)
    join = int(np.argmax(segment == 1))
    column = wavelengths.copy()
    column[join - 1] = 1657.0  # this column's smile moves it across 1656 nm
    targets = (column - 1650.0)[None] / 100.0
    targets[:, join:] += 1.0  # the step at the join
    parts = destripe._smoothParts(targets, column, np.ones(40), segment)
    np.testing.assert_allclose(parts, targets, atol=1e-9)
