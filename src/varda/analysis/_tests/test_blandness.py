import numpy as np
import pytest

from varda.analysis.blandness import (
    PRESETS,
    ParameterLimit,
    Preset,
    genericBlandMask,
    genericBlandness,
    presetBlandness,
)

WL = np.linspace(1000, 2600, 200)


def _dip(depth: float, center: float = 2300, width: float = 40) -> np.ndarray:
    return 0.3 * (1 - depth * np.exp(-0.5 * ((WL - center) / width) ** 2))


def _check(result, name):
    return next(c for c in result.checks if c.name == name)


def test_a_flat_spectrum_is_bland():
    result = genericBlandness(np.full(200, 0.3), WL)
    assert result.passed is True
    assert _check(result, "Absorption depth").value == pytest.approx(0, abs=1e-9)
    assert _check(result, "Slope").value == pytest.approx(0, abs=1e-9)


def test_an_absorption_fails_the_depth_check():
    check = _check(genericBlandness(_dip(0.05), WL), "Absorption depth")
    assert check.value == pytest.approx(0.05, abs=0.005)
    assert check.passed is False


def test_a_slope_fails_the_slope_check():
    ramp = np.linspace(0.30, 0.39, 200)
    check = _check(genericBlandness(ramp, WL, maxSlope=0.1), "Slope")
    assert check.value == pytest.approx((0.09 / 1.6) / 0.345, abs=0.002)
    assert check.passed is False
    assert genericBlandness(ramp, WL, maxSlope=0.2).passed is True


def test_nan_bands_are_ignored():
    spectrum = np.full(200, 0.3)
    spectrum[::20] = np.nan
    assert genericBlandness(spectrum, WL).passed is True


def test_preset_uses_the_named_parameters():
    preset = Preset("T", (ParameterLimit("LCPINDEX2", 0.02),), (1000, 1700))
    seen = []

    def fake(spectrum, wavelengths, names):
        seen.append(list(names))
        return np.array([0.05])

    result = presetBlandness(np.full(200, 0.3), WL, preset, parameters=fake)
    check = _check(result, "LCPINDEX2")
    assert (check.value, check.limit, check.passed) == (
        pytest.approx(0.05),
        0.02,
        False,
    )
    assert seen == [["LCPINDEX2"]]
    assert _check(result, "Slope").passed is True


def test_preset_without_hypyrameter_says_so():
    def missing(spectrum, wavelengths, names):
        raise ImportError("no hypyrameter")

    preset = Preset("T", (ParameterLimit("LCPINDEX2", 0.02),), (1000, 1700))
    result = presetBlandness(np.full(200, 0.3), WL, preset, parameters=missing)
    assert result.checks == ()
    assert result.note == "needs HyPyRameter"
    assert result.passed is False


def test_masks_match_the_per_spectrum_checks():
    spectra = [np.full(200, 0.3) if i % 2 else _dip(0.05 + 0.01 * i) for i in range(20)]
    cube = np.array(spectra).reshape(4, 5, 200)
    cube[3, 4] = np.nan
    mask = genericBlandMask(cube, WL)
    expected = [genericBlandness(s, WL).passed for s in spectra[:-1]]
    assert mask.shape == (4, 5)
    assert mask.ravel()[:-1].tolist() == expected
    assert not mask[3, 4]


def test_presets_cover_the_spec_targets():
    assert set(PRESETS) == {
        "Plagioclase",
        "Olivine",
        "Fe/Mg phyllosilicates",
        "Al phyllosilicates",
        "Carbonates",
        "Sulfates",
        "Hydrated (any)",
    }
    names = {limit.name for limit in PRESETS["Plagioclase"].limits}
    assert {"LCPINDEX2", "HCPINDEX2"} <= names


def test_preset_mask_applies_each_maximum_and_the_slope():
    from varda.analysis.blandness import presetBlandMask

    cube = np.full((2, 2, 200), 0.3)
    cube[1, 1] = np.nan
    cube[0, 1] = np.linspace(0.3, 0.6, 200)  # too steep

    def fake(cube, wavelengths, names):
        values = np.zeros((2, 2, 1))
        values[1, 0, 0] = 0.05  # over the maximum
        return values

    preset = Preset("T", (ParameterLimit("LCPINDEX2", 0.02),), (1000, 1700))
    mask = presetBlandMask(cube, WL, preset, parameterCube=fake)
    assert mask.tolist() == [[True, False], [False, False]]
