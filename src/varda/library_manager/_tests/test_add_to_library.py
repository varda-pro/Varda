"""Add to Library from a plotted curve records where the spectrum came from."""

import numpy as np
import pytest

from varda.common.entities import VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.library_manager.add_to_library_dialog import NEW_LIBRARY, AddToLibraryDialog
from varda.plotting.pixel_spectra_plot import PixelSpectraPlotWidget
from varda.spectral_libraries.store import USGS_LIBRARY_NAME, LibraryStore
from varda.version import vardaVersion

MICROMETRES = np.linspace(1.0, 2.6, 12)


def _image() -> VardaRaster:
    data = np.random.default_rng(0).random((6, 6, 12)) + 0.1
    return VardaRaster(
        ArrayDataSource(data, wavelengths=MICROMETRES, wavelengthUnits="Micrometers"),
        name="scene",
    )


@pytest.fixture
def store(tmp_path):
    return LibraryStore(tmp_path / "libraries")


@pytest.fixture
def plot(qtbot, store):
    widget = PixelSpectraPlotWidget(store=store)
    qtbot.addWidget(widget)
    return widget


def _addToNewLibrary(dialog: AddToLibraryDialog, name: str) -> None:
    dialog.targetBox.setCurrentIndex(dialog.targetBox.findText(NEW_LIBRARY))
    dialog.newNameEdit.setText(name)
    dialog.unitsEdit.setText("reflectance")
    dialog.accept()


def test_a_pixel_spectrum_records_its_image_pixel_and_version(plot, store):
    image = _image()
    curve = plot.addPixelSpectrum(image, 2, 3)
    dialog = plot.addToLibraryDialog(curve)
    assert dialog is not None
    dialog.nameEdit.setText("bright dune")
    _addToNewLibrary(dialog, "Field site")

    library = store.library("Field site")
    assert library is not None
    (spectrum,) = library.spectra
    assert spectrum.name == "bright dune"
    assert spectrum.valueUnits == "reflectance"
    np.testing.assert_allclose(spectrum.wavelengths, MICROMETRES * 1000.0)
    np.testing.assert_allclose(spectrum.reflectance, image.getSpectrum(2, 3).values)
    assert spectrum.std is None
    assert spectrum.provenance["sourceImage"] == "scene"
    assert spectrum.provenance["region"] == "pixel (col 2, row 3)"
    assert spectrum.provenance["pixelCount"] == 1
    assert spectrum.provenance["statistic"] == "single pixel"
    assert spectrum.provenance["vardaVersion"] == vardaVersion()


def test_a_box_mean_keeps_its_spread_and_pixel_count(plot, store):
    plot.pixelConfig.windowWidth.set(3)
    plot.pixelConfig.windowHeight.set(3)
    curve = plot.addPixelSpectrum(_image(), 2, 2)
    # the scale/offset of the display is not what's saved
    curve.config.scale.set(5.0)
    dialog = plot.addToLibraryDialog(curve)
    assert dialog is not None
    _addToNewLibrary(dialog, "Boxes")
    library = store.library("Boxes")
    assert library is not None
    (spectrum,) = library.spectra
    assert spectrum.provenance["pixelCount"] == 9
    assert spectrum.provenance["statistic"] == "mean ± 1 σ"
    assert spectrum.provenance["region"] == "3×3 px box at (col 2, row 2)"
    assert spectrum.std is not None and np.all(spectrum.std > 0)
    np.testing.assert_allclose(spectrum.reflectance, curve.data()[1])


def test_a_library_curve_is_copied_with_its_own_record(plot, store):
    usgs = store.library(USGS_LIBRARY_NAME)
    assert usgs is not None
    plot.addPixelSpectrum(_image(), 1, 1)
    curve = plot.addLibrarySpectrum(usgs.spectra[0])
    dialog = plot.addToLibraryDialog(curve)
    assert dialog is not None
    _addToNewLibrary(dialog, "Copies")
    library = store.library("Copies")
    assert library is not None
    (spectrum,) = library.spectra
    # the library's values in nm, not the plot's scaled, converted view
    np.testing.assert_array_equal(spectrum.reflectance, usgs.spectra[0].reflectance)
    assert spectrum.provenance["library"] == "USGS splib07a"
    assert spectrum.provenance["copiedFrom"] == "USGS splib07a"
    assert spectrum.valueUnits == "reflectance"


def test_a_plot_of_band_numbers_cannot_add_to_a_library(plot):
    curve = plot.plot(np.arange(5.0), np.ones(5), name="bands")
    assert plot.spectralUnit is None
    assert plot.addToLibraryDialog(curve) is None


def test_spectra_go_into_an_existing_library_and_it_is_offered_next(plot, store):
    mine = store.createLibrary("Mine")
    image = _image()
    first = plot.addToLibraryDialog(plot.addPixelSpectrum(image, 1, 1))
    assert first is not None
    first.targetBox.setCurrentIndex(first.targetBox.findData("Mine"))
    first.accept()
    second = plot.addToLibraryDialog(plot.addPixelSpectrum(image, 2, 2))
    assert second is not None
    assert second.targetBox.currentData() == "Mine"  # the last one used
    second.accept()
    assert len(mine.spectra) == 2


def test_the_dialog_fits_a_laptop_screen(qtbot, plot):
    curve = plot.addPixelSpectrum(_image(), 1, 1)
    dialog = plot.addToLibraryDialog(curve)
    assert dialog is not None
    qtbot.addWidget(dialog)
    dialog.resize(dialog.minimumSizeHint())
    dialog.show()
    qtbot.waitExposed(dialog)
    assert dialog.width() <= 1280 and dialog.height() <= 800
    assertNothingClipped(dialog)
