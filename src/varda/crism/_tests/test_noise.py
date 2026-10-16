import numpy as np

from varda.crism._tests.synthetic import makeSyntheticCube
from varda.crism.noise import estimateNoise


def _nearInfrared(truth) -> np.ndarray:
    wavelengths = np.append(np.median(truth.columnWavelengths[:, :-1], axis=0), np.nan)
    return (wavelengths >= 1000.0) & (wavelengths <= 2600.0)


def _ratio(**kwargs) -> float:
    cube, truth = makeSyntheticCube(lines=128, spikeRate=0.0, **kwargs)
    model = estimateNoise(cube)
    ratio = model.sigma(truth.surface) / truth.noiseSigma
    return float(np.nanmedian(ratio[:, :, _nearInfrared(truth)]))


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
