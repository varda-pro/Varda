import numpy as np
import pytest

from varda.crism._tests.real_data import realChunk
from varda.crism._tests.synthetic import makeSyntheticCube
from varda.crism.noise import estimateNoise


def _nearInfrared(truth) -> np.ndarray:
    wavelengths = np.append(np.median(truth.columnWavelengths[:, :-1], axis=0), np.nan)
    return (wavelengths >= 1000.0) & (wavelengths <= 2600.0)


def _ratio(window: tuple[float, float] = (1000.0, 2600.0), **kwargs) -> float:
    """median(model σ / injected σ) in a window, both at the observed signal.

    ``truth.surface`` is before the atmosphere, and the injected σ follows the
    signal after it (about 22% lower inside the CO2 band), so the model is
    evaluated on the cube (its noise is 1% of the signal).
    """
    cube, truth = makeSyntheticCube(lines=128, spikeRate=0.0, **kwargs)
    model = estimateNoise(cube)
    wavelengths = np.append(np.median(truth.columnWavelengths[:, :-1], axis=0), np.nan)
    inWindow = (wavelengths >= window[0]) & (wavelengths <= window[1])
    ratio = model.sigma(cube) / truth.noiseSigma
    return float(np.nanmedian(ratio[:, :, inWindow]))


def test_recovers_injected_sigma():
    assert abs(_ratio() - 1.0) < 0.03


def test_stripes_do_not_inflate_sigma():
    assert abs(_ratio(stripeAmplitude=0.02) - 1.0) < 0.03


def test_shot_noise_slope():
    cube, truth = makeSyntheticCube(lines=128, spikeRate=0.0)
    model = estimateNoise(cube)
    band = _nearInfrared(truth)
    # sigma² = a + b·R: recover the injected b from the truth by regression
    # over the noise-free signal.
    r = truth.surface[:, :, band]
    var = truth.noiseSigma[:, :, band] ** 2
    rc = r - r.mean(axis=(0, 1))
    injected = (rc * (var - var.mean(axis=(0, 1)))).sum(axis=(0, 1)) / (rc**2).sum(
        axis=(0, 1)
    )
    assert abs(np.median(model.shot[band]) / np.median(injected) - 1.0) < 0.10


def test_all_nan_band_is_nan_without_warnings():
    cube, _ = makeSyntheticCube(lines=64)
    model = estimateNoise(cube)
    assert np.isnan(model.shot[-1]) and np.isnan(model.floor[:, -1]).all()


def test_sigma_unbiased_inside_co2_band():
    assert abs(_ratio((1950.0, 2070.0)) - 1.0) < 0.05


def test_edges_get_group_floor_and_short_cube_raises():
    cube, _ = makeSyntheticCube(lines=64, spikeRate=0.0)
    model = estimateNoise(cube)
    assert (
        np.isfinite(model.floor[0, :-1]).all()
        and np.isfinite(model.floor[-1, :-1]).all()
    )
    assert np.array_equal(model.floor[0], model.floor[1], equal_nan=True)
    with pytest.raises(ValueError, match="at least 3 lines"):
        estimateNoise(cube[:2])


def test_sparse_column_group_gets_a_finite_sigma():
    original, _ = makeSyntheticCube(lines=64, spikeRate=0.0)
    cube = original.copy()
    cube[:, 32:48] = np.nan  # column group 2 keeps a few pixels only
    cube[::16, 32:48:6] = original[::16, 32:48:6]
    sigma = estimateNoise(cube).sigma(cube)
    assert np.isfinite(sigma[np.isfinite(cube)]).all()
    cube[:, 32:48] = np.nan  # none at all: the nearest group's floor
    assert np.isfinite(estimateNoise(cube).floor[32:48, :-1]).all()


def test_real_chunk_sigma_is_finite_where_data_are(demoData):
    cube, _ = realChunk(demoData)
    sigma = estimateNoise(cube).sigma(cube)
    assert np.isfinite(sigma[np.isfinite(cube)]).all()


def test_texture_does_not_inflate_sigma():
    """An achromatic per-pixel factor (2% RMS) is surface texture, not noise:
    the plain along-track second difference would read it as σ (about 3x the
    injected 1% there); the brightness-normalized one does not."""
    for window in ((1000.0, 2600.0), (1950.0, 2070.0)):
        assert abs(_ratio(window, textureAmplitude=0.02) - 1.0) < 0.03


def test_texture_inflates_the_plain_second_difference():
    cube, truth = makeSyntheticCube(lines=128, spikeRate=0.0, textureAmplitude=0.02)
    band = int(
        np.argmin(np.abs(np.median(truth.columnWavelengths[:, :-1], axis=0) - 1500.0))
    )
    plain = cube[1:-1, :, band] - 0.5 * (cube[:-2, :, band] + cube[2:, :, band])
    injected = np.median(truth.noiseSigma[:, :, band])
    assert 1.4826 * np.median(np.abs(plain)) / np.sqrt(1.5) > 2 * injected


def test_achromatic_fraction_flags_texture():
    cube, truth = makeSyntheticCube(lines=128, spikeRate=0.0, textureAmplitude=0.05)
    model = estimateNoise(cube)
    window = _nearInfrared(truth)
    assert np.nanmedian(model.achromaticFraction[window]) > 0.8
    clean = estimateNoise(makeSyntheticCube(lines=128, spikeRate=0.0)[0])
    assert np.nanmedian(clean.achromaticFraction[window]) < 0.3


def test_white_noise_calibration():
    """On pure white noise over a flat spectrum σ comes back to within 3%."""
    rng = np.random.default_rng(1)
    cube = 0.1 + 0.001 * rng.standard_normal((128, 64, 100))
    model = estimateNoise(cube)
    assert abs(np.median(model.sigma(cube)) / 0.001 - 1.0) < 0.03


def _window(wavelengths: np.ndarray, low: float, high: float) -> np.ndarray:
    middle = np.nanmedian(wavelengths[:, :-1], axis=0)
    middle = np.append(middle, np.nan)
    return (middle >= low) & (middle <= high)


def realNoiseNumbers(demoData):
    """(new σ median, chromatic residual σ of the plain differences, achromatic
    fraction median) over 1850–2200 nm of FRS00031442 lines 0–63, in the pixels
    whose spectra are valid in all those bands."""
    cube, wavelengths = realChunk(demoData)
    model = estimateNoise(
        cube, wavelengths=np.append(np.nanmedian(wavelengths[:, :-1], axis=0), np.nan)
    )
    bands = _window(wavelengths, 1850.0, 2200.0)
    window = cube[:, :, bands]
    d0 = window[1:-1] - 0.5 * (window[:-2] + window[2:])
    spectrum = window[1:-1]
    ok = np.isfinite(d0).all(axis=2) & np.isfinite(spectrum).all(axis=2)
    brightness = np.where(ok, spectrum.mean(axis=2), np.nan)
    gradient = np.hypot(*np.gradient(brightness))
    ok &= gradient <= np.nanmedian(gradient)  # the quiet half
    d0, spectrum = d0[ok], spectrum[ok]
    # the achromatic part of the differences is their first principal component
    _, _, components = np.linalg.svd(d0 - d0.mean(axis=0), full_matrices=False)
    residual = d0 - (d0 @ components[0])[:, None] * components[0]
    chromatic = 1.4826 * np.median(np.abs(residual)) / np.sqrt(1.5)
    sigma = model.sigma(cube)[:, :, bands][1:-1][ok]
    return (
        float(np.median(sigma)),
        float(chromatic),
        float(np.nanmedian(model.achromaticFraction[bands])),
    )


# The reviewer's measurement of the chromatic residual of the along-track
# differences on these lines (quiet pixels, 1850-2200 nm): 0.175 of the plain
# estimator's σ of 5.57e-4 I/F.
REVIEWED_CHROMATIC_SIGMA = 0.175 * 5.57e-4


def test_real_sigma_is_the_chromatic_noise(demoData):
    """On FRS00031442 lines 0-63 the plain second differences over 1850-2200 nm
    are mostly one achromatic component (texture); σ is what is left once it is
    removed. Measured: σ 1.08e-4 (0.20 of the plain estimator's), the PCA
    residual 1.57e-4 (it also holds chromatic texture, so it bounds σ above),
    achromatic fraction 0.92."""
    sigma, chromatic, fraction = realNoiseNumbers(demoData)
    assert abs(sigma / REVIEWED_CHROMATIC_SIGMA - 1.0) < 0.3
    assert sigma < chromatic
    assert fraction > 0.8
