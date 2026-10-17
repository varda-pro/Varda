"""Library files: reading the formats Varda accepts, and writing ENVI and CSV
libraries that read back whole (wavelengths, values, provenance, σ)."""

import json

import numpy as np
import pytest
import spectral.io.envi as envi

from varda.spectral_libraries.formats import (
    loadCsvLibrary,
    loadEnviLibrary,
    loadLibrary,
    loadUsgsLibrary,
    readLibraryInfo,
    sidecarPath,
    writeCsvLibrary,
    writeEnviLibrary,
)
from varda.spectral_libraries.spectrum import (
    LibrarySpectrum,
    SpectrumOrigin,
    copiedSpectrum,
    spectrumFromImage,
)
from varda.version import vardaVersion

WL = np.linspace(1000.0, 2500.0, 151)


def _gauss(center, depth, width=25.0, wavelengths=WL):
    return 1.0 - depth * np.exp(-0.5 * ((wavelengths - center) / width) ** 2)


def _regionMean():
    """A box mean from an image on another sensor's (coarser) axis."""
    wavelengths = np.linspace(1020.0, 2600.0, 80)
    return spectrumFromImage(
        "Dunes, east",
        wavelengths,
        0.3 * _gauss(2300, 0.1, wavelengths=wavelengths),
        SpectrumOrigin(
            "frt0000a425_07_if165j_mtr3",
            "/data/frt0000a425.img",
            "5×5 px box centered on (12, 40)",
            pixelCount=25,
        ),
        std=np.full(80, 0.01),
        valueUnits="I/F",
    )


def _library():
    return [
        LibrarySpectrum(
            "Kaolinite {KGa-1}",
            WL,
            0.7 * _gauss(2200, 0.3),
            "USGS splib07a",
            valueUnits="reflectance",
            provenance={"library": "USGS minerals", "file": "/x/kaol"},
        ),
        _regionMean(),
    ]


def _assertSame(a: LibrarySpectrum, b: LibrarySpectrum) -> None:
    assert a.name == b.name and a.source == b.source
    assert a.valueUnits == b.valueUnits
    assert dict(a.provenance) == dict(b.provenance)
    np.testing.assert_array_equal(a.wavelengths, b.wavelengths)
    np.testing.assert_array_equal(a.reflectance, b.reflectance)
    if a.std is None:
        assert b.std is None
    else:
        assert b.std is not None
        np.testing.assert_array_equal(a.std, b.std)


def test_a_spectrum_from_an_image_records_where_it_came_from():
    spectrum = _regionMean()
    assert spectrum.provenance["sourceImage"] == "frt0000a425_07_if165j_mtr3"
    assert spectrum.provenance["sourcePath"] == "/data/frt0000a425.img"
    assert spectrum.provenance["region"] == "5×5 px box centered on (12, 40)"
    assert spectrum.provenance["pixelCount"] == 25
    assert spectrum.provenance["statistic"] == "mean ± 1 σ"
    assert spectrum.provenance["vardaVersion"] == vardaVersion()
    assert str(spectrum.provenance["added"])[:2] == "20"


def test_envi_write_then_read_keeps_everything(tmp_path):
    spectra = _library()
    header = writeEnviLibrary(
        tmp_path / "mine.sli", spectra, name="My minerals", citation="Me (2026)"
    )
    assert header == tmp_path / "mine.hdr" and (tmp_path / "mine.sli").is_file()
    loaded = loadEnviLibrary(header)
    for original, back in zip(spectra, loaded, strict=True):
        _assertSame(original, back)
    assert readLibraryInfo(header)["name"] == "My minerals"
    assert readLibraryInfo(header)["citation"] == "Me (2026)"


def test_an_envi_library_varda_wrote_is_standard_without_its_sidecar(tmp_path):
    spectra = _library()
    header = writeEnviLibrary(tmp_path / "mine.hdr", spectra)
    sidecarPath(header).unlink()
    plain = envi.open(str(header))
    assert plain.metadata["wavelength units"] == "Nanometers"
    assert len(plain.names) == 2 and "{" not in plain.names[0]
    # one shared axis: the union of both spectra's wavelengths
    loaded = loadEnviLibrary(header)
    assert (
        loaded[0].wavelengths.size
        == np.unique(np.concatenate([s.wavelengths for s in spectra])).size
    )
    assert str(loaded[0].provenance["file"]).endswith("mine.sli")


def test_a_sidecar_for_a_different_number_of_spectra_is_ignored(tmp_path):
    header = writeEnviLibrary(tmp_path / "mine.hdr", _library())
    data = json.loads(sidecarPath(header).read_text())
    data["spectra"] = data["spectra"][:1]
    sidecarPath(header).write_text(json.dumps(data))
    assert [s.name for s in loadEnviLibrary(header)] == [
        "Kaolinite KGa-1",
        "Dunes east",
    ]


def test_csv_write_then_read_keeps_everything(tmp_path):
    spectra = _library()
    path = tmp_path / "mine.csv"
    writeCsvLibrary(path, spectra, name="My minerals", citation="Me (2026)")
    text = path.read_text()
    assert "# Cite: Me (2026)" in text
    assert "Dunes, east std" in text  # σ is exported beside the mean
    for original, back in zip(spectra, loadCsvLibrary(path), strict=True):
        _assertSame(original, back)


def test_a_plain_csv_records_its_file_and_skips_pixel_count_columns(tmp_path):
    path = tmp_path / "plot.csv"
    rows = ["wavelength_nm,A,A std,A n"] + [
        f"{w},{v},0.01,9" for w, v in zip(WL, _gauss(2200, 0.3))
    ]
    path.write_text("\n".join(rows))
    (spectrum,) = loadCsvLibrary(path)
    assert spectrum.name == "A"
    assert spectrum.std is not None and spectrum.std[0] == pytest.approx(0.01)
    assert spectrum.provenance == {"library": "plot", "file": str(path)}


def test_copying_a_spectrum_keeps_its_record_and_says_where_from():
    original = _library()[0]
    copy = copiedSpectrum(original, "USGS minerals", name="Kaolinite")
    assert copy.name == "Kaolinite"
    assert copy.provenance["file"] == "/x/kaol"
    assert copy.provenance["copiedFrom"] == "USGS minerals"


def test_usgs_spectra_carry_their_library_and_folder():
    spectra = loadUsgsLibrary()
    assert spectra and spectra[0].provenance["library"] == "USGS splib07a"
    assert spectra[0].valueUnits == "reflectance"


def test_load_library_reports_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        loadLibrary(tmp_path / "gone.sli")
