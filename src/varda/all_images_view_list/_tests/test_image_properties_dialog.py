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
from varda.geo._tests.fixtures import (
    CRISM_MTRDR_ENVI_WKT,
    CRISM_MTRDR_WKT,
    makeImage,
    writeWgs84,
)
from varda.geo.equivalence import sameCrs
from varda.geo.health import flagged
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.image_loading_service import loadRaster, openDataSource
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


def test_editing_keeps_a_corrected_crs(qtbot, tmp_path):
    path = writeRaster(_image(), tmp_path / "cube.img")
    image = VardaRaster(openDataSource(str(path)))
    image.setMetadataOverrides({"crs": "IAU_2015:49900"})
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    dialog.noDataEdit.setText("-1")
    dialog.saveToFile.setChecked(True)  # the header takes only what it can hold
    dialog.accept()
    assert image.metadataOverrides == {"nodata": -1.0, "crs": "IAU_2015:49900"}
    assert VardaRaster(openDataSource(str(path))).nodata == -1.0


def test_reset_to_file_is_the_image_as_it_opened(qtbot, tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    image.setMetadataOverrides({"crs": None, "nodata": -1.0})
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    dialog.resetToFile()
    assert sameCrs(image.metadataOverrides["crs"], "IAU_2015:49900")
    assert "nodata" not in image.metadataOverrides
    assert image.extraMetadata["crsFix"]["automatic"] is True


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


def _crsTexts(dialog: ImagePropertiesDialog) -> list[str]:
    return [label.text() for label in dialog.crsBox.findChildren(QLabel)]


def test_the_issues_are_listed_with_the_automatic_fix_and_undo(qtbot, tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    texts = _crsTexts(dialog)
    assert "Fixed: Its CRS says Earth, but its label says Mars." in texts
    assert any(t.startswith("Varda reads it as degrees") for t in texts)
    assert any(
        t.startswith("Varda applied this fix when the image opened") for t in texts
    )
    assert dialog.undoButton is not None and dialog.applyButtons == []


def test_undo_keeps_the_files_crs_and_the_issue(qtbot, tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    assert dialog.undoButton is not None
    dialog.undoButton.click()
    assert sameCrs(image.crs, "EPSG:4326")
    assert image.extraMetadata["crsFix"]["undone"] is True
    assert flagged(image.crsHealth)  # still a problem: the badge stays
    texts = _crsTexts(dialog)
    assert any("you chose to keep the file's CRS" in t for t in texts)
    assert any(t.startswith("Earth, ellipsoid a = 6378.14") for t in texts)
    assert any(t.startswith("Problem: Its CRS says Earth") for t in texts)
    assert dialog.undoButton is None and len(dialog.applyButtons) == 1
    dialog.applyButtons[0].click()  # and back
    assert sameCrs(image.crs, "IAU_2015:49900")
    assert image.extraMetadata["crsFix"]["automatic"] is False
    assert any(t.startswith("You applied this fix") for t in _crsTexts(dialog))


def test_set_coordinate_system_uses_the_chosen_crs(qtbot, tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    dialog.setCrsButton.click()
    chooser = dialog.setCrsDialog
    assert chooser is not None and chooser.isVisible()
    chooser.crsEdit.setText("IAU_2015:49910")
    chooser.accept()
    assert sameCrs(image.crs, "IAU_2015:49910")
    assert image.extraMetadata["crsFix"]["automatic"] is False
    assert image.extraMetadata["crsFix"]["to"] == "IAU_2015:49910"
    assert any(
        t.startswith("Mars, sphere R = 3396.19 km, Equirectangular")
        for t in _crsTexts(dialog)
    )


@pytest.mark.parametrize("size", [(1280, 800), (640, 500)])
def test_the_issues_are_not_cut_off_on_a_laptop_screen(qtbot, tmp_path, size):
    image = loadRaster(str(writeWgs84(tmp_path)))
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    dialog.resize(*size)
    dialog.show()
    qtbot.waitExposed(dialog)
    assertNothingClipped(dialog)
    assert dialog.undoButton is not None
    dialog.undoButton.click()  # the Apply layout too
    qtbot.wait(10)
    assertNothingClipped(dialog)


BROKEN_WKT = 'PROJCS["Mars eqc",GEOGCS["Mars",DATUM["D_Mars",SPHEROID["Mars",3396190'


class _UnreadableCrsSource(ArrayDataSource):
    """A file whose CRS text PROJ couldn't read."""

    @property
    def rawCrsText(self) -> str | None:
        return BROKEN_WKT

    @property
    def crsError(self) -> str | None:
        return "PROJ: unexpected end of WKT"


def test_an_unreadable_crs_shows_its_text_and_the_error(qtbot):
    data = np.zeros((4, 4, 1), dtype=np.float32)
    image = VardaRaster(
        _UnreadableCrsSource(data, transform=Affine(10, 0, 0, 0, -10, 0)),
        name="broken",
    )
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    texts = [label.text() for label in dialog.crsBox.findChildren(QLabel)]
    assert "Unreadable coordinate system (PROJ: unexpected end of WKT)" in texts
    assert "No coordinate system (pixel coordinates)" not in texts
    assert dialog.wktEdit.toPlainText() == BROKEN_WKT
    dialog.wktToggle.setChecked(True)
    assert dialog.wktEdit.isVisibleTo(dialog)
    dialog.setCrsButton.click()
    chooser = dialog.setCrsDialog
    assert chooser is not None
    assert chooser.crsEdit.text() == BROKEN_WKT  # to correct by hand
