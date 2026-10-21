from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage
from spectral.io import envi

from varda.crism.readers import readDetectorFrame, readTrr3
from varda.crism.validation import (
    SUMMARY_PARAMETERS,
    bd2310,
    co2Residual,
    compareMaps,
    geolocationOffset,
    sceneResidualRms,
    shapeRatio,
    summaryParameter,
)

TRR3 = "frs00031442_01_if168l_trr3.lbl"
WA = "cdr490947778566_wa0000000l_3.lbl"
AT = "cdr420862848019_at0000000l_7.lbl"
MTRDR = "data-for-paper/FRT00005850/mtrdr/frt00005850_07_if167j_mtr3"
MTRDR_TABLE = Path(__file__).parents[1] / "resources" / "mtrdr_wavelengths.csv"


def _mtrdrWavelengths() -> np.ndarray:
    return np.loadtxt(MTRDR_TABLE, delimiter=",", skiprows=2, usecols=2)


def _co2Shape(cropDir: Path) -> tuple[np.ndarray, np.ndarray]:
    """A real column's wavelengths and ln T, scaled to an RMS of 1 over
    1940–2100 nm."""
    rows = readTrr3(cropDir / TRR3).detectorRows
    wavelengths = readDetectorFrame(cropDir / WA).alignedTo(rows)[60]
    logT = np.log(readDetectorFrame(cropDir / AT).alignedTo(rows)[60])
    valid = np.isfinite(wavelengths) & np.isfinite(logT)
    wavelengths, logT = wavelengths[valid], logT[valid]
    window = (wavelengths > 1940) & (wavelengths < 2100)
    return wavelengths, logT / np.sqrt(np.mean(logT[window] ** 2))


def test_co2_metric_on_synthetic(cropDir: Path) -> None:
    wavelengths, shape = _co2Shape(cropDir)
    flat = np.full((3, wavelengths.size), 0.2)
    assert np.all(co2Residual(wavelengths, flat) < 1e-6)
    # a smooth (quadratic) continuum is not a residual either
    u = (wavelengths - 2020.0) / 100.0
    assert co2Residual(wavelengths, 0.2 + 0.01 * u - 0.005 * u**2) < 1e-6
    withCo2 = 0.2 * (1.0 + 0.01 * shape)
    assert 0.004 < co2Residual(wavelengths, withCo2) < 0.01
    # shapes: (..., b) in, (...) out; NaN only where the spectrum is invalid
    spectra = np.stack([flat[0], withCo2, np.full(wavelengths.size, np.nan)])
    scores = co2Residual(wavelengths, spectra.reshape(1, 3, -1))
    assert scores.shape == (1, 3)
    assert np.isnan(scores[0, 2]) and np.isfinite(scores[0, :2]).all()


def test_scene_residual_rms(cropDir: Path) -> None:
    wavelengths, shape = _co2Shape(cropDir)
    spectrum = 0.2 * (1.0 + 0.01 * shape)
    scale = np.linspace(0.5, 1.5, 7)[:, None]
    assert sceneResidualRms(wavelengths, scale * spectrum) == pytest.approx(
        float(co2Residual(wavelengths, spectrum))
    )


def _prototypeBd2300(wl: np.ndarray, cube: np.ndarray) -> np.ndarray:
    # Reference copy of the fusion prototype, stage2b_diag.py:55-58
    # (M. Phillips 2026), kept verbatim in behavior; the prototype calls it
    # "BD2300".
    def b_(w: float) -> np.ndarray:
        i = int(np.argmin(np.abs(wl - w)))
        return np.nanmean(cube[..., i - 2 : i + 3], axis=-1)

    t = (2310 - 2160) / (2400 - 2160)
    cont = (1 - t) * b_(2160) + t * b_(2400)
    return 1 - b_(2310) / cont


def test_bd2310_matches_prototype() -> None:
    wavelengths = _mtrdrWavelengths()
    rng = np.random.default_rng(0)
    for shape in [(5, 7), (40,), (3, 4, 2)]:
        cube = rng.uniform(0.05, 0.3, (*shape, wavelengths.size))
        cube[rng.random(cube.shape) < 0.02] = np.nan
        np.testing.assert_allclose(
            bd2310(wavelengths, cube), _prototypeBd2300(wavelengths, cube), rtol=1e-12
        )
    assert summaryParameter("BD2310", wavelengths, cube) == pytest.approx(
        bd2310(wavelengths, cube), nan_ok=True
    )


def test_co2_metric_reproduces_m7(demoData: Path) -> None:
    header = envi.read_envi_header(str(demoData / f"{MTRDR}.hdr"))
    wavelengths = np.array([float(w) for w in header["wavelength"]])
    shape = (int(header["bands"]), int(header["lines"]), int(header["samples"]))
    cube = np.memmap(demoData / f"{MTRDR}.img", dtype="<f4", mode="r", shape=shape)
    selected = np.nonzero((wavelengths > 1930) & (wavelengths < 2110))[0]
    assert 20 <= selected.size <= 30  # only these bands are read
    spectra = np.array(cube[selected][:, ::2, ::2], dtype=np.float64).transpose(1, 2, 0)
    valid = np.all((spectra != 65535) & (spectra > 0), axis=-1)
    scores = co2Residual(wavelengths[selected], spectra[valid])
    assert float(np.median(scores)) == pytest.approx(0.0082, abs=0.0003)
    # the same numbers as the M7 measurement script, to its printed precision
    assert float(np.median(scores)) == pytest.approx(0.008237, abs=1e-6)
    scene = sceneResidualRms(wavelengths[selected], spectra[valid])
    assert scene == pytest.approx(0.008033, abs=1e-6)


def test_co2_metric_independent_of_band_grid() -> None:
    # Native bands, no interpolation: white noise scores the same on the
    # MTRDR grid and on that grid shifted by half a band
    grid = _mtrdrWavelengths()
    rng = np.random.default_rng(4)
    noise = 0.2 + rng.normal(0.0, 0.002, (4000, grid.size))
    onGrid = np.median(co2Residual(grid, noise))
    shifted = np.median(co2Residual(grid + 3.3, noise))
    assert shifted == pytest.approx(onGrid, rel=0.03)


def test_co2_metric_per_spectrum_wavelengths(cropDir: Path) -> None:
    wavelengths, shape = _co2Shape(cropDir)
    rng = np.random.default_rng(5)
    offsets = rng.uniform(-1.0, 1.0, (6, 1))
    perSpectrum = wavelengths + offsets
    spectra = 0.2 * (1.0 + 0.01 * shape) + rng.normal(0, 0.001, perSpectrum.shape)
    expected = [co2Residual(w, y) for w, y in zip(perSpectrum, spectra)]
    np.testing.assert_allclose(co2Residual(perSpectrum, spectra), expected)
    assert np.isfinite(sceneResidualRms(perSpectrum, spectra))
    with pytest.raises(ValueError, match="wavelengths"):
        co2Residual(perSpectrum[:3], spectra)


def test_geolocation_offset() -> None:
    rng = np.random.default_rng(1)
    texture = ndimage.gaussian_filter(rng.standard_normal((160, 160)), 2.0)
    bands = np.stack([0.1 + 0.02 * texture, 0.12 + 0.03 * texture], axis=-1)
    shifted = ndimage.shift(bands, (-0.5, 1.5, 0.0), order=3, mode="nearest")
    inner = (slice(16, -16), slice(16, -16))
    reference, ours = bands[inner], shifted[inner]
    ours[3, 5] = np.nan  # invalid pixels don't stop it
    dx, dy = geolocationOffset(ours, reference)
    assert dx == pytest.approx(1.5, abs=0.05)
    assert dy == pytest.approx(-0.5, abs=0.05)
    # an albedo image works as well as a cube
    dx, dy = geolocationOffset(ours[..., 0], reference[..., 0])
    assert (dx, dy) == pytest.approx((1.5, -0.5), abs=0.05)


def test_compare_maps() -> None:
    rng = np.random.default_rng(2)
    reference = rng.uniform(0.0, 0.05, (20, 30))
    ours = reference + 0.003
    ours[0, :5] = np.nan
    reference[1, :3] = np.inf
    comparison = compareMaps(ours, reference)
    assert comparison.n == 600 - 8
    assert comparison.pearson == pytest.approx(1.0)
    assert comparison.medianBias == pytest.approx(0.003)
    assert comparison.rmsDifference == pytest.approx(0.003)


@pytest.mark.filterwarnings("error")
def test_compare_maps_constant_and_infinite() -> None:
    constant = compareMaps(np.full((4, 4), 0.01), np.linspace(0, 1, 16).reshape(4, 4))
    assert np.isnan(constant.pearson) and constant.n == 16
    # ±inf counts as missing in the medians as well
    wavelengths = _mtrdrWavelengths()
    reference = np.full((3, wavelengths.size), 0.2)
    ours = reference.copy()
    ours[0, 400] = np.inf
    ours[1, 400] = -np.inf
    assert shapeRatio(ours, reference, wavelengths)[400] == pytest.approx(1.0)


def test_shape_ratio() -> None:
    wavelengths = _mtrdrWavelengths()
    rng = np.random.default_rng(3)
    reference = rng.uniform(0.1, 0.3, (10, 4, wavelengths.size))
    ours = 1.7 * reference  # brightness does not change the shape
    bump = int(np.argmin(np.abs(wavelengths - 2000.0)))
    ours[..., bump] *= 1.01
    ratio = shapeRatio(ours, reference, wavelengths)
    assert ratio.shape == wavelengths.shape
    expected = np.ones(wavelengths.size)
    expected[bump] = 1.01
    np.testing.assert_allclose(ratio, expected, rtol=1e-12)


# A flat-bottomed dip of known depth on a sloped continuum, covering each
# parameter's numerator bands (and their kernels) and none of its anchors.
DIPS = {
    "BD2290": (2270.0, 2310.0),
    "BD1900_2": (1880.0, 2030.0),
    "BD2310": (2280.0, 2340.0),
    "D2300": (2270.0, 2345.0),
    "OLINDEX3": (1040.0, 1520.0),
}


@pytest.mark.parametrize("name", sorted(DIPS))
def test_summary_parameter_depth(name: str) -> None:
    wavelengths = _mtrdrWavelengths()
    continuum = 0.15 + 2e-5 * (wavelengths - 1000.0)
    low, high = DIPS[name]
    depths = np.array([0.0, 0.02, 0.1])
    inside = (wavelengths >= low) & (wavelengths <= high)
    cube = continuum * (1.0 - depths[:, None] * inside)
    np.testing.assert_allclose(
        summaryParameter(name, wavelengths, cube), depths, atol=1e-3
    )


def test_summary_parameter_names() -> None:
    assert set(SUMMARY_PARAMETERS) == set(DIPS)
    with pytest.raises(ValueError, match="BD9999"):
        summaryParameter("BD9999", _mtrdrWavelengths(), np.ones((2, 489)))
