"""Image Properties: shows the metadata, turns what's changed into edits
(only what differs from the file), and can save them into an ENVI header."""

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from varda.all_images_view_list.image_properties_dialog import (
    GOOD,
    NAME,
    WAVELENGTH,
    ImagePropertiesDialog,
    parseNoData,
)
from varda.common.entities import VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.geo._tests.fixtures import CRISM_MTRDR_ENVI_WKT, CRISM_MTRDR_WKT, makeImage
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.image_loading_service import openDataSource
from varda.image_loading.raster_writer import writeRaster


def _image():
    data = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
    return VardaRaster(
        ArrayDataSource(data, wavelengths=np.array([1.0, 2.0, 3.0, 4.0])), name="cube"
    )


def test_no_data_values_read_as_typed():
    assert parseNoData("") is None and parseNoData(" none ") is None
    assert parseNoData("-999") == -999.0 and np.isnan(parseNoData("nan"))
    with pytest.raises(ValueError):
        parseNoData("lots")


def test_nothing_changed_is_no_edit(qtbot):
    dialog = ImagePropertiesDialog(_image())
    qtbot.addWidget(dialog)
    assert dialog.edits() == ({}, "cube")


def test_changes_become_edits_and_apply(qtbot):
    image = _image()
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    dialog.nameEdit.setText("scene")
    dialog.noDataEdit.setText("0")
    dialog.unitsBox.setCurrentText("µm")
    dialog.table.item(2, GOOD).setCheckState(Qt.CheckState.Unchecked)
    dialog.table.item(0, NAME).setText("blue")
    dialog.fillStart.setValue(0.5)
    dialog.fillStep.setValue(0.1)
    dialog.fillWavelengths()
    overrides, name = dialog.edits()
    assert overrides["nodata"] == 0.0 and overrides["wavelengthUnits"] == "µm"
    assert overrides["bbl"] == [1, 1, 0, 1] and overrides["bandNames"][0] == "blue"
    np.testing.assert_allclose(overrides["wavelengths"], [0.5, 0.6, 0.7, 0.8])
    dialog.accept()
    assert image.name == "scene" and image.nodata == 0.0
    np.testing.assert_allclose(image.bandModel.centers, [500, 600, 700, 800])


def test_a_wavelength_that_does_not_read_is_refused(qtbot):
    dialog = ImagePropertiesDialog(_image())
    qtbot.addWidget(dialog)
    dialog.table.item(1, WAVELENGTH).setText("blue")
    with pytest.raises(ValueError, match="wavelength"):
        dialog.edits()


def test_edits_save_into_an_envi_header(qtbot, tmp_path):
    path = writeRaster(_image(), tmp_path / "cube.img")
    image = VardaRaster(openDataSource(str(path)))
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    assert dialog.saveToFile.isEnabled()
    dialog.noDataEdit.setText("-1")
    dialog.saveToFile.setChecked(True)
    dialog.accept()
    reopened = VardaRaster(openDataSource(str(path)))
    assert reopened.nodata == -1.0


def test_coordinate_system_group_describes_the_crs(qtbot):
    image = makeImage(CRISM_MTRDR_WKT, centerLonLat=(77.0, 15.0), pixelSize=18.0)
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    texts = [label.text() for label in dialog.crsBox.findChildren(QLabel)]
    assert (
        "Mars, sphere R = 3394.84 km, Equirectangular, center lat 15°, 18 m/px" in texts
    )
    assert "18 × 18 m (18 m on the ground)" in texts
    assert "PROJCRS" in dialog.wktEdit.toPlainText()
    assert not dialog.wktEdit.isVisibleTo(dialog)  # collapsed until asked for
    dialog.wktToggle.setChecked(True)
    assert dialog.wktEdit.isVisibleTo(dialog)


def test_coordinate_system_names_a_matching_authority_code(qtbot):
    image = makeImage(
        "+proj=longlat +R=3396190 +no_defs", centerLonLat=(0, 0), pixelSize=0.01
    )
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    texts = [label.text() for label in dialog.crsBox.findChildren(QLabel)]
    assert "matches IAU_2015:49900" in texts


def test_an_image_without_a_crs_says_so(qtbot):
    dialog = ImagePropertiesDialog(_image())
    qtbot.addWidget(dialog)
    texts = [label.text() for label in dialog.crsBox.findChildren(QLabel)]
    assert "No coordinate system (pixel coordinates)" in texts


@pytest.mark.parametrize("size", [(1280, 800), (640, 500)])  # laptop; short window
def test_nothing_is_cut_off_on_a_laptop_screen(qtbot, size):
    image = makeImage(CRISM_MTRDR_ENVI_WKT, centerLonLat=(77.0, 15.0), pixelSize=18.0)
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    dialog.wktToggle.setChecked(True)
    dialog.resize(*size)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert (dialog.width(), dialog.height()) == size
    assertNothingClipped(dialog)


def test_a_local_crs_has_no_conventions_row(qtbot):
    local = CRS.from_user_input(
        'LOCAL_CS["arbitrary",UNIT["metre",1],AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
    )
    image = VardaRaster(
        ArrayDataSource(
            np.zeros((4, 4, 1), np.float32),
            transform=Affine(2.0, 0, 0, 0, -2.0, 0),
            crs=local,
        ),
        name="local",
    )
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    texts = [label.text() for label in dialog.crsBox.findChildren(QLabel)]
    assert "Local coordinates (no body), arbitrary, 2 m/px" in texts
    assert "Conventions:" not in texts and "2 × 2 m (2 m on the ground)" in texts
