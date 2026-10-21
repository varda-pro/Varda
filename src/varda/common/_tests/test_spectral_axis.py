"""The spectral axis: units parsed once, wavelengths in nm for every calculation,
FWHM, bad bands, scale factor and no-data carried alongside."""

import numpy as np
import pytest

from varda.common.entities import VardaRaster

from varda.common.spectral_axis import (
    bandWavelengthLabels,
    formatWavelength,
    wavelengthDecimals,
    BandModel,
    SpectralUnit,
    bandModelFor,
    displayBands,
    fromNanometres,
    parseEnviList,
    parseUnit,
    toNanometres,
    unitLabel,
)
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.raster_writer import writeRaster


@pytest.mark.parametrize(
    "text, unit",
    [
        ("Nanometers", SpectralUnit.NANOMETRE),
        ("nm", SpectralUnit.NANOMETRE),
        ("nanometres", SpectralUnit.NANOMETRE),
        ("Micrometers", SpectralUnit.MICROMETRE),
        ("um", SpectralUnit.MICROMETRE),
        ("µm", SpectralUnit.MICROMETRE),
        ("microns", SpectralUnit.MICROMETRE),
        ("Wavenumber", SpectralUnit.WAVENUMBER),
        ("cm-1", SpectralUnit.WAVENUMBER),
        ("Unknown", None),
        ("parameters", None),
        ("", None),
        (None, None),
    ],
)
def test_unit_strings_are_parsed(text, unit):
    assert parseUnit(text) is unit


def test_conversions_to_and_from_nanometres():
    np.testing.assert_allclose(
        toNanometres([1.0, 2.5], SpectralUnit.MICROMETRE), [1000, 2500]
    )
    np.testing.assert_allclose(toNanometres([10000.0], SpectralUnit.WAVENUMBER), [1000])
    np.testing.assert_allclose(toNanometres([700.0], SpectralUnit.NANOMETRE), [700])
    np.testing.assert_allclose(fromNanometres([2500.0], SpectralUnit.MICROMETRE), [2.5])
    np.testing.assert_allclose(
        fromNanometres([1000.0], SpectralUnit.WAVENUMBER), [10000]
    )


def test_envi_lists_are_parsed():
    assert parseEnviList("{ 1 , 0 , 1 }") == [1.0, 0.0, 1.0]
    assert parseEnviList([0.01, 0.02]) == [0.01, 0.02]
    assert parseEnviList(None) is None


def test_a_declared_unit_wins_over_the_size_of_the_numbers():
    """Thermal IR in µm (above 10) was read as nm by the old guess."""
    model = bandModelFor(np.array([8.0, 10.5, 12.0]), "Micrometers", None, {}, 3)
    np.testing.assert_allclose(model.centers, [8000, 10500, 12000])
    assert model.unit is SpectralUnit.MICROMETRE and not model.unitWasInferred


def test_the_envi_extras_are_read_and_converted():
    extra = {
        "fwhm": "{ 0.01 , 0.01 , 0.02 }",
        "bbl": "{ 1 , 0 , 1 }",
        "reflectance_scale_factor": "10000",
    }
    model = bandModelFor(np.array([1.0, 1.5, 2.0]), "Micrometers", -9999.0, extra, 3)
    np.testing.assert_allclose(model.fwhm, [10, 10, 20])
    assert model.badBands.tolist() == [False, True, False]
    assert model.scaleFactor == 10000
    assert model.noData == -9999


def test_band_indices_and_labels_are_not_a_spectral_axis():
    indices = bandModelFor(np.arange(5), "Unknown", None, {}, 5)
    labels = bandModelFor(np.array(["a", "b"]), "Unknown", None, {}, 2)
    parameters = bandModelFor(np.array([1.0, 2.0]), "parameters", None, {}, 2)
    for model in (indices, labels, parameters):
        assert not model.isSpectral and model.centers is None
    assert indices.badBands.tolist() == [False] * 5


@pytest.mark.parametrize(
    "values, unit",
    [
        (np.linspace(400, 2500, 10), SpectralUnit.NANOMETRE),
        (np.linspace(0.4, 2.5, 10), SpectralUnit.MICROMETRE),
        (np.linspace(7.5, 13.5, 10), SpectralUnit.MICROMETRE),  # thermal IR, undeclared
    ],
)
def test_undeclared_units_are_inferred_and_flagged(values, unit):
    model = bandModelFor(values, "Unknown", None, {}, len(values))
    assert model.unit is unit and model.unitWasInferred
    assert model.unitNote.startswith(
        f"Wavelength units not declared; assuming {unit.value} from the values ("
    )
    assert bandModelFor(values, unit.value, None, {}, len(values)).unitNote == ""


def test_an_undeclared_unit_is_logged_once_per_image(caplog, tmp_path):
    """Release QA 0.12.0 #13: one image logged the assumption three times."""
    data = np.random.default_rng(0).random((6, 7, 5)).astype(np.float32)
    image = VardaRaster(
        ArrayDataSource(data, wavelengths=np.linspace(400, 2500, 5)), name="big"
    )
    assert image.bandModel.unitWasInferred and image.bandModel.isSpectral
    writeRaster(image, tmp_path / "big.img")  # reads the band model again
    logged = [r.getMessage() for r in caplog.records if "not declared" in r.message]
    assert logged == [
        "big: Wavelength units not declared; assuming nm from the values (400–2500)"
    ]


def test_native_values_round_trip():
    model = bandModelFor(np.array([1.0, 2.0]), "um", None, {}, 2)
    np.testing.assert_allclose(model.native(model.centers), [1.0, 2.0])


def test_unit_labels():
    assert unitLabel(SpectralUnit.NANOMETRE) == "nm"
    assert unitLabel(SpectralUnit.MICROMETRE) == "µm"
    assert unitLabel(SpectralUnit.WAVENUMBER) == "cm⁻¹"
    assert unitLabel(None, "Unknown") == "Unknown"


def test_a_bad_band_list_of_the_wrong_length_is_ignored(caplog):
    model = bandModelFor(np.array([1.0, 2.0]), "um", None, {"bbl": "{1, 0, 1}"}, 2)
    assert model.badBands.tolist() == [False, False]
    assert "bbl" in caplog.text


def test_band_model_is_frozen():
    model = BandModel.empty(3)
    with pytest.raises(AttributeError):
        model.scaleFactor = 2.0  # ty: ignore[invalid-assignment]


def test_visible_coverage_gives_true_colour_bands():
    centres = np.arange(400.0, 2501.0, 10.0)
    model = bandModelFor(centres, "nm", None, {}, centres.size)
    red, green, blue = displayBands(model)
    assert centres[[red, green, blue]].tolist() == [640.0, 550.0, 470.0]


def test_infrared_only_gives_spread_false_colour_long_wavelengths_red():
    centres = np.linspace(1000.0, 2600.0, 81)
    model = bandModelFor(centres, "nm", None, {}, centres.size)
    red, green, blue = displayBands(model)
    assert centres[red] > centres[green] > centres[blue]
    assert centres[red] - centres[blue] > 800


def test_bad_bands_are_never_chosen_for_display():
    centres = np.arange(400.0, 2501.0, 10.0)
    bbl = np.ones(centres.size, int)
    bbl[np.flatnonzero(centres == 640.0)] = 0
    model = bandModelFor(centres, "nm", None, {"bbl": list(bbl)}, centres.size)
    red, _, _ = displayBands(model)
    assert centres[red] != 640.0 and abs(centres[red] - 640.0) <= 10


def test_no_display_choice_without_a_spectral_axis():
    assert displayBands(bandModelFor(np.arange(5), "Unknown", None, {}, 5)) is None
    assert (
        displayBands(bandModelFor(np.array([500.0, 600.0]), "nm", None, {}, 2)) is None
    )


def test_coarse_visible_sampling_falls_back_to_distinct_false_colour():
    model = bandModelFor(
        np.array([400.0, 500.0, 600.0, 700.0, 800.0]), "nm", None, {}, 5
    )
    bands = displayBands(model)
    assert bands is not None and len(set(bands.tolist())) == 3


# --- Showing wavelengths (release QA #7) -------------------------------------


def test_decimals_tell_neighboring_bands_apart():
    assert wavelengthDecimals(np.linspace(1000, 2600, 40)) == 0  # 41 nm apart
    assert wavelengthDecimals(np.linspace(1000, 2600, 400)) == 1  # 4 nm
    assert wavelengthDecimals([2.3538, 2.3604, 2.3669]) == 4  # µm, 6.5 nm
    assert wavelengthDecimals([500.0]) == 1  # one band: a sensible default
    assert wavelengthDecimals([1e-9, 2e-9, 3e-9]) == 4  # never more than 4


def test_a_wavelength_reads_with_its_unit():
    assert formatWavelength(2353.8461538461543, "nm", 1) == "2353.8 nm"
    assert formatWavelength(2.35384615, "µm", 3) == "2.354 µm"
    assert formatWavelength(12.0, "", 0) == "12"


def test_band_labels_use_the_images_own_unit():
    model = bandModelFor(
        [2.3538461, 2.3603, 2.3669], "micrometers", None, {}, bandCount=3
    )
    assert bandWavelengthLabels(model) == ["2.3538 µm", "2.3603 µm", "2.3669 µm"]


def test_no_spectral_axis_has_no_wavelength_labels():
    assert bandWavelengthLabels(BandModel.empty(3)) is None
