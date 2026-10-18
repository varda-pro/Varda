import numpy as np
import pytest

from varda.analysis.blandness import (
    CHECKS,
    DEFAULT_EXCLUSIONS,
    GENERIC,
    READOUT_LIMITS,
    Check,
    Limits,
    Window,
    assess,
    blandMask,
    formatExclusions,
    parseExclusions,
    windowBandCounts,
)

# CRISM's IR band spacing over the range the checks use
WL = np.arange(1047.0, 2700.0, 6.55)
LOOSE_SLOPE = Limits(floor=0.01, noiseMultiple=3.0, maxSlope=10.0)


def _band(depth: float, center: float, width: float = 30.0) -> np.ndarray:
    return 1 - depth * np.exp(-0.5 * ((WL - center) / width) ** 2)


def _red(slope: float = 0.1) -> np.ndarray:
    """A flat spectrum with a red slope (per µm, relative)."""
    return 0.3 * (1 + slope * (WL - 1800) / 1000)


def _window(result, name):
    return next(w for w in result.windows if w.window.name == name)


def test_a_flat_spectrum_is_bland_for_every_check():
    for check in CHECKS.values():
        result = assess(np.full(WL.shape, 0.3), WL, check, READOUT_LIMITS)
        assert result.passed, check.name
        assert all(w.depth == pytest.approx(0, abs=1e-9) for w in result.windows)


def test_a_red_slope_with_a_co2_dip_passes_generic():
    """The CO2 residual at 2.0 µm is excluded by default."""
    spectrum = _red() * _band(0.08, 2010, 25)
    result = assess(spectrum, WL, CHECKS[GENERIC], READOUT_LIMITS)
    assert result.passed
    included = assess(spectrum, WL, CHECKS[GENERIC], READOUT_LIMITS, exclusions=())
    assert not included.passed
    failing = [w for w in included.windows if not w.passed]
    assert [w.window.name for w in failing] == ["1.7–2.65 µm"]
    assert failing[0].dipNm == pytest.approx(2010, abs=10)


def test_a_3_percent_band_at_2310_fails_fe_mg_and_says_where():
    result = assess(
        _red() * _band(0.03, 2310), WL, CHECKS["Fe/Mg phyllosilicates"], LOOSE_SLOPE
    )
    assert not result.passed
    window = _window(result, "2.3 µm Fe/Mg-OH")
    assert not window.depthPassed
    assert window.dipNm == pytest.approx(2310, abs=10)
    assert window.depth == pytest.approx(0.03, abs=0.006)
    assert _window(result, "1.9 µm H₂O").passed


def test_noise_alone_does_not_fail():
    rng = np.random.default_rng(0)
    passed = [
        assess(
            _red() * (1 + 0.005 * rng.normal(size=WL.size)),
            WL,
            CHECKS[GENERIC],
            Limits(floor=0.0, noiseMultiple=3.0, maxSlope=10.0),
        ).passed
        for _ in range(200)
    ]
    assert np.mean(passed) >= 0.9


def test_the_noise_estimate_follows_the_spectrums_noise():
    rng = np.random.default_rng(1)
    noisy = _red() * (1 + 0.01 * rng.normal(size=WL.size))
    for w in assess(noisy, WL, CHECKS[GENERIC], READOUT_LIMITS).windows:
        assert w.noise == pytest.approx(0.01, rel=0.3)


def test_the_depth_limit_is_the_larger_of_the_floor_and_k_noise():
    limits = Limits(floor=0.02, noiseMultiple=3.0, maxSlope=1.0)
    assert limits.depthLimit(0.001) == 0.02  # quiet: the floor decides
    assert limits.depthLimit(0.01) == pytest.approx(0.03)  # noisy: k × noise
    assert limits.depthLimit(float("nan")) == 0.02


def test_a_band_hidden_in_noise_passes_but_fails_a_quiet_spectrum():
    rng = np.random.default_rng(2)
    band = _band(0.025, 2310)
    noise = 1 + 0.012 * rng.normal(size=WL.size)
    check = CHECKS["Fe/Mg phyllosilicates"]
    limits = Limits(floor=0.01, noiseMultiple=3.0, maxSlope=10.0)
    assert not assess(_red() * band, WL, check, limits).passed
    noisy = assess(_red() * band * noise, WL, check, limits)
    window = _window(noisy, "2.3 µm Fe/Mg-OH")
    assert window.depthLimit == pytest.approx(3 * window.noise)
    assert window.depthLimit > 0.025
    assert window.depthPassed


def test_a_steep_slope_fails():
    limits = Limits(floor=0.03, noiseMultiple=3.0, maxSlope=0.3)
    result = assess(_red(0.5), WL, CHECKS[GENERIC], limits)
    assert not result.passed
    assert all(not w.slopePassed for w in result.windows)
    # relative slope: 0.5 per µm over the window's mean level
    expected = 0.5 / (1 + 0.5 * (1375 - 1800) / 1000)
    assert _window(result, "1.05–1.7 µm").slope == pytest.approx(expected, rel=0.05)


def test_micrometers_converted_to_nanometers_give_the_same_result():
    spectrum = _red() * _band(0.04, 2310)
    um = WL / 1000
    a = assess(spectrum, WL, CHECKS["Hydrated (any)"], READOUT_LIMITS)
    b = assess(spectrum, um * 1000, CHECKS["Hydrated (any)"], READOUT_LIMITS)
    assert a == b


def test_nan_bands_are_left_out():
    spectrum = _red()
    spectrum[::17] = np.nan
    assert assess(spectrum, WL, CHECKS[GENERIC], READOUT_LIMITS).passed


def test_a_window_with_too_few_bands_fails_and_says_so():
    check = Check("T", (Window("narrow", 1500, 1520),))
    result = assess(_red(), WL, check, READOUT_LIMITS)
    assert not result.passed
    assert "narrow" in result.note
    assert windowBandCounts(WL, check) == {"narrow": 3}


def test_the_mask_matches_the_per_spectrum_verdicts():
    rng = np.random.default_rng(4)
    spectra = [
        _red(rng.uniform(0, 1))
        * _band(rng.uniform(0, 0.06), rng.uniform(1100, 2600))
        * (1 + 0.003 * rng.normal(size=WL.size))
        for _ in range(40)
    ]
    cube = np.array(spectra).reshape(5, 8, WL.size)
    cube[4, 7, 3] = np.nan
    for check in CHECKS.values():
        mask = blandMask(cube, WL, check, READOUT_LIMITS)
        expected = [assess(s, WL, check, READOUT_LIMITS).passed for s in spectra[:-1]]
        assert mask.ravel()[:-1].tolist() == expected, check.name
    # a NaN band inside a window (1067 nm) makes the pixel not bland
    assert not blandMask(cube, WL, CHECKS[GENERIC], READOUT_LIMITS)[4, 7]


def test_the_checks_are_the_spec_targets():
    assert list(CHECKS) == [
        "Generic",
        "Plagioclase",
        "Olivine",
        "Fe/Mg phyllosilicates",
        "Al phyllosilicates",
        "Carbonates",
        "Sulfates",
        "Hydrated (any)",
    ]
    for check in CHECKS.values():
        for w in check.windows:
            assert 1050 <= w.lowNm < w.highNm <= 2650, w


def test_exclusions_parse_and_format():
    text = formatExclusions(DEFAULT_EXCLUSIONS)
    assert text == "1000–1050, 1950–2100, 2650–4000"
    assert parseExclusions(text) == DEFAULT_EXCLUSIONS
    assert parseExclusions("1950-2100 nm; 2650 to 4000") == ((1950, 2100), (2650, 4000))
    assert parseExclusions("  ") == ()
    with pytest.raises(ValueError, match="not like"):
        parseExclusions("CO2")
    with pytest.raises(ValueError, match="start < end"):
        parseExclusions("2100-1950")
