"""Identifying endmember spectra against spectral libraries."""

import numpy as np
import pytest
import spectral.io.envi as envi

from varda.analysis.spectral_library import (
    LibrarySpectrum,
    binaryEncodingMatch,
    continuumRemoved,
    featureFit,
    rankLibrary,
    resampleTo,
)
from varda.spectral_libraries.formats import (
    loadCsvLibrary,
    loadEnviLibrary,
    loadUsgsLibrary,
)
from varda.spectral_libraries.store import (
    MICA_LIBRARY_NAME,
    USGS_LIBRARY_NAME,
    bundledLibraries,
)


def loadBundledLibraries() -> dict[str, list[LibrarySpectrum]]:
    return {library.name: library.spectra for library in bundledLibraries()}


WL = np.linspace(1000.0, 2500.0, 151)


def _gauss(centre, depth, width=25.0):
    return 1.0 - depth * np.exp(-0.5 * ((WL - centre) / width) ** 2)


def _library():
    return [
        LibrarySpectrum("Kaolinite", WL, 0.7 * _gauss(2200, 0.3) * _gauss(1400, 0.15)),
        LibrarySpectrum("Calcite", WL, 0.6 * _gauss(2340, 0.3)),
        LibrarySpectrum("Flat", WL, np.full(WL.size, 0.5)),
    ]


def test_continuum_removal_maps_the_hull_to_one_and_keeps_features():
    y = 0.4 + 0.1 * (WL - 1000) / 1500 - 0.1 * np.exp(-0.5 * ((WL - 2000) / 30) ** 2)
    cr = continuumRemoved(WL, y)
    assert cr.max() == pytest.approx(1.0)
    assert cr[0] == pytest.approx(1.0) and cr[-1] == pytest.approx(1.0)
    assert cr[np.argmin(np.abs(WL - 2000))] < 0.8


def test_feature_fit_is_perfect_for_a_scaled_feature():
    reference = _gauss(2200, 0.3)
    unknown = 0.5 * (1 - 0.5 * (1 - reference))  # half-depth, darker
    scale, rms = featureFit(WL, unknown, reference)
    assert scale == pytest.approx(0.5, rel=1e-3)
    assert rms == pytest.approx(0.0, abs=1e-6)


def test_binary_encoding_counts_matching_bands():
    a = _gauss(2200, 0.3)
    assert binaryEncodingMatch(a, 2.0 * a) == 1.0
    assert binaryEncodingMatch(a, 2.0 - a) < 0.3


def test_resampling_interpolates_and_blanks_outside_the_library_range():
    spectrum = LibrarySpectrum("x", WL, np.linspace(0, 1, WL.size))  # nm
    out = resampleTo(spectrum, np.array([900.0, 1000.0, 1750.0, 2600.0]))
    assert np.isnan(out[0]) and np.isnan(out[3])
    assert out[1] == pytest.approx(0.0) and out[2] == pytest.approx(0.5, abs=1e-3)


def test_rank_library_puts_the_right_mineral_first():
    unknown = (
        0.35 * _gauss(2205, 0.2) * _gauss(1400, 0.1)
    )  # darker, slightly shifted kaolinite
    matches = rankLibrary(WL, unknown, _library())
    assert [m.name for m in matches][0] == "Kaolinite"
    best = matches[0]
    assert 0.0 < best.score <= 1.0 and best.score > matches[1].score
    assert best.angle < matches[1].angle
    assert best.flag == "best"
    assert all(m.flag in {"best", "close", "far"} for m in matches)


def test_rank_library_can_restrict_the_wavelength_range():
    unknown = 0.6 * _gauss(2340, 0.3) * _gauss(1400, 0.3)  # calcite + a big 1.4 µm band
    full = rankLibrary(WL, unknown, _library())
    swir = rankLibrary(WL, unknown, _library(), wavelengthRange=(2250.0, 2450.0))
    assert swir[0].name == "Calcite"
    assert swir[0].score >= full[[m.name for m in full].index("Calcite")].score


def test_load_bundled_usgs_library():
    library = loadUsgsLibrary()
    assert len(library) > 10
    kaolinite = next(s for s in library if s.name.startswith("Kaolinite"))
    assert np.nanmax(kaolinite.wavelengths) > 2000.0  # nm


def test_load_envi_spectral_library(tmp_path):
    spectra = np.stack([_gauss(2200, 0.3), _gauss(2340, 0.2)]).astype(np.float32)
    header = tmp_path / "lib.hdr"
    library = envi.SpectralLibrary(
        spectra,
        {
            "spectra names": ["kaol", "calc"],
            "wavelength": list(WL / 1000.0),
            "wavelength units": "Micrometers",
        },
        {},
    )
    library.save(str(tmp_path / "lib"))
    loaded = loadEnviLibrary(header)
    assert [s.name for s in loaded] == ["kaol", "calc"]
    np.testing.assert_allclose(loaded[0].wavelengths, WL, rtol=1e-6)


def test_load_csv_library(tmp_path):
    path = tmp_path / "lib.csv"
    rows = ["wavelength,A,B"] + [
        f"{w},{a},{b}" for w, a, b in zip(WL, _gauss(2200, 0.3), _gauss(2340, 0.2))
    ]
    path.write_text("\n".join(rows))
    loaded = loadCsvLibrary(path)
    assert [s.name for s in loaded] == ["A", "B"]
    assert loaded[1].reflectance.shape == WL.shape


def test_featureless_spectra_match_each_other_not_featured_ones():
    flat = np.full(WL.size, 0.4) + 0.001 * np.sin(WL)
    library = [
        LibrarySpectrum("Kaolinite", WL, 0.7 * _gauss(2200, 0.3)),
        LibrarySpectrum("Flat", WL, np.full(WL.size, 0.5)),
    ]
    assert rankLibrary(WL, flat, library)[0].name == "Flat"


def test_csv_libraries_may_start_with_comment_lines(tmp_path):
    path = tmp_path / "lib.csv"
    rows = ["# source: somewhere", "# units: um", "wavelength,A"] + [
        f"{w / 1000},{v}" for w, v in zip(WL, _gauss(2200, 0.3))
    ]
    path.write_text("\n".join(rows))
    (spectrum,) = loadCsvLibrary(path)
    assert spectrum.name == "A"
    np.testing.assert_allclose(spectrum.wavelengths, WL)


def test_bundled_libraries_include_crism_mica_numerators():
    libraries = loadBundledLibraries()
    assert list(libraries) == [MICA_LIBRARY_NAME, USGS_LIBRARY_NAME]
    mica = libraries[MICA_LIBRARY_NAME]
    assert len(mica) == 31
    kaolinite = next(s for s in mica if s.name == "Kaolinite")
    assert kaolinite.wavelengths.min() == pytest.approx(436.13)
    assert kaolinite.wavelengths.max() == pytest.approx(3897.0, abs=1.0)
    assert np.nanmax(kaolinite.reflectance) < 1.0


def test_mica_kaolinite_identifies_itself_from_a_noisy_crism_spectrum():
    mica = loadBundledLibraries()[MICA_LIBRARY_NAME]
    kaolinite = next(s for s in mica if s.name == "Kaolinite")
    wl = np.arange(1000.0, 2600.0, 6.55)
    observed = resampleTo(kaolinite, wl) * 0.9 + 0.002 * np.random.default_rng(
        0
    ).normal(size=wl.size)
    assert rankLibrary(wl, observed, mica)[0].name == "Kaolinite"


def _csv(path, header, wavelengths, comments=()):
    rows = [*comments, header] + [
        f"{w},{v}" for w, v in zip(wavelengths, _gauss(2200, 0.3))
    ]
    path.write_text("\n".join(rows))
    (spectrum,) = loadCsvLibrary(path)
    return spectrum


@pytest.mark.parametrize(
    "header, comments, scale",
    [
        ("wavelength_um,A", (), 1000.0),
        ("Wavelength (nm),A", (), 1.0),
        ("wavelength,A", ("# units: micrometers",), 1000.0),
    ],
)
def test_csv_libraries_declare_their_units_in_the_header_or_a_comment(
    tmp_path, header, comments, scale
):
    spectrum = _csv(tmp_path / "lib.csv", header, WL / scale, comments)
    np.testing.assert_allclose(spectrum.wavelengths, WL)


def test_a_thermal_library_in_micrometres_is_not_read_as_nanometres(tmp_path):
    """The old "below 10 means µm" rule read 8-14 µm as 8-14 nm."""
    thermal = np.linspace(8.0, 14.0, 151)
    spectrum = _csv(tmp_path / "tir.csv", "wavelength_um,A", thermal)
    np.testing.assert_allclose(spectrum.wavelengths, thermal * 1000)


def test_matching_takes_wavelengths_in_nanometres_only():
    library = _library()
    unknown = library[0].reflectance
    assert rankLibrary(WL, unknown, library)[0].name == library[0].name
    assert rankLibrary(WL / 1000.0, unknown, library) == []  # µm: no overlap
