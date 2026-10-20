"""Image Properties: shows the metadata, turns what's changed into edits
(only what differs from the file), and can save them into an ENVI header."""

import threading

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QLabel, QPlainTextEdit, QTableWidget

from varda.all_images_view_list import image_properties_dialog
from varda.all_images_view_list.image_properties_dialog import (
    GOOD,
    NAME,
    WAVELENGTH,
    ImagePropertiesDialog,
    parseNoData,
)
from varda.analysis.class_tools import ClassAreas, classAreas, classRaster
from varda.common.entities import VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.geo._tests.fixtures import (
    CRISM_MTRDR_ENVI_WKT,
    CRISM_MTRDR_WKT,
    GDAL_WEST_PROJ,
    HIRISE_LABEL_GROUP,
    HIRISE_LABEL_TRANSFORM,
    HIRISE_POLAR_PROJ,
    WEST_LABEL_GROUP,
    makeImage,
    readWithoutCrs,
    westAxis,
    writePds3,
    writeWgs84,
)
from varda.geo.equivalence import sameCrs
from varda.geo.health import flagged
from varda.image_loading.data_sources._tests.test_fits_data_source import writeAltwg
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


# --- CRSs built from the label ---


def test_a_label_gdal_misread_is_applied_and_undone(qtbot, tmp_path):
    image = loadRaster(str(writePds3(tmp_path, WEST_LABEL_GROUP)))
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    assert any(
        t.startswith("Problem: Its label describes Mars") for t in _crsTexts(dialog)
    )
    assert dialog.undoButton is None and len(dialog.applyButtons) == 1
    dialog.applyButtons[0].click()
    assert westAxis(image.crs)  # the label's CRS
    assert image.extraMetadata["crsFix"]["source"] == "label"
    texts = _crsTexts(dialog)
    assert any(t.startswith("Fixed: Its label (PDS3 label x.lbl)") for t in texts)
    assert any(t.startswith("You applied this fix") for t in texts)
    assert dialog.undoButton is not None
    dialog.undoButton.click()
    assert sameCrs(image.crs, GDAL_WEST_PROJ) and not westAxis(image.crs)  # GDAL's
    assert image.extraMetadata["crsFix"]["undone"] is True
    assert dialog.undoButton is None and len(dialog.applyButtons) == 1


def test_undo_of_a_label_fix_removes_its_grid_too(qtbot, tmp_path, monkeypatch):
    readWithoutCrs(monkeypatch)
    image = loadRaster(str(writePds3(tmp_path, HIRISE_LABEL_GROUP)))
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    texts = _crsTexts(dialog)
    assert any(
        t.startswith("Varda applied this fix when the image opened") for t in texts
    )
    assert dialog.undoButton is not None
    dialog.undoButton.click()
    assert image.metadataOverrides == {"crs": None}
    assert not image.hasGeospatialData
    assert image.extraMetadata["crsFix"]["undone"] is True
    assert len(dialog.applyButtons) == 1
    dialog.applyButtons[0].click()  # and back, as the user's
    assert sameCrs(image.crs, HIRISE_POLAR_PROJ)
    assert tuple(image.transform)[:6] == pytest.approx(HIRISE_LABEL_TRANSFORM)
    assert image.extraMetadata["crsFix"]["automatic"] is False
    assert dialog.undoButton is not None


def test_editing_keeps_a_corrected_transform(qtbot, tmp_path, monkeypatch):
    readWithoutCrs(monkeypatch)
    image = loadRaster(str(writePds3(tmp_path, HIRISE_LABEL_GROUP)))
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    dialog.noDataEdit.setText("-1")
    dialog.accept()
    overrides = image.metadataOverrides
    assert overrides["nodata"] == -1.0
    assert sameCrs(overrides["crs"], HIRISE_POLAR_PROJ)
    assert overrides["transform"] == pytest.approx(list(HIRISE_LABEL_TRANSFORM))


def test_a_label_mismatch_is_not_cut_off_on_a_laptop_screen(qtbot, tmp_path):
    image = loadRaster(str(writePds3(tmp_path, WEST_LABEL_GROUP)))
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    dialog.resize(1280, 800)
    dialog.show()
    qtbot.waitExposed(dialog)
    assertNothingClipped(dialog)
    dialog.applyButtons[0].click()  # the Undo layout too
    qtbot.wait(10)
    assertNothingClipped(dialog)


def _cell(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    assert item is not None
    return item.text()


def _classMap(referenced: bool = True) -> VardaRaster:
    """A 3-class map on Mars's equirectangular sphere at 0°N (1 km pixels),
    or the same classes without map coordinates."""
    base = (
        makeImage(
            "IAU_2015:49910",
            centerLonLat=(10.0, 0.0),
            pixelSize=1000.0,
            width=12,
            height=10,
        )
        if referenced
        else VardaRaster(ArrayDataSource(np.zeros((10, 12, 1), np.float32)), name="b")
    )
    classes = np.full((10, 12), np.nan)
    classes[:4] = 0
    classes[4:7, :8] = 1
    classes[7:, 3:] = 2
    return classRaster(
        base,
        classes,
        ["water", "rock, bare", "dust"],
        ["#0000ff", "#888888", "#ffaa00"],
        name="classes",
        description="test",
    )


def _measured(qtbot, image: VardaRaster) -> ImagePropertiesDialog:
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.classAreas is not None, timeout=10000)
    return dialog


def test_an_image_that_is_not_a_class_map_has_no_classes_group(qtbot):
    dialog = ImagePropertiesDialog(_image())
    qtbot.addWidget(dialog)
    assert dialog.classesBox is None


def test_a_class_map_shows_computing_then_each_class_area(qtbot):
    image = _classMap()
    dialog = ImagePropertiesDialog(image)
    qtbot.addWidget(dialog)
    assert _cell(dialog.classTable, 0, 0) == "Computing…"
    assert not dialog.copyCsvButton.isEnabled()
    qtbot.waitUntil(lambda: dialog.classAreas is not None, timeout=10000)
    expected = classAreas(image)
    table = dialog.classTable
    assert table.rowCount() == 3
    for row, area in enumerate(expected.rows):
        assert _cell(table, row, 1) == area.name
        assert _cell(table, row, 2) == f"{area.pixels:,}"
        assert float(_cell(table, row, 3).replace(",", "")) == pytest.approx(
            area.areaKm2, rel=1e-5
        )
    assert dialog.classAreas == expected
    assert dialog.classMethod.text().startswith("exact per row")
    assert "99 classified pixels" in dialog.classTotals.text()
    assert "21 unclassified" in dialog.classTotals.text()
    assert dialog.copyCsvButton.isEnabled()


def test_copy_as_csv_puts_the_table_on_the_clipboard(qtbot):
    dialog = _measured(qtbot, _classMap())
    dialog.copyCsvButton.click()
    clipboard = QGuiApplication.clipboard()
    assert clipboard is not None
    text = clipboard.text()
    assert "class,name,pixels,area_km2,fraction" in text.splitlines()
    assert '1,"rock, bare",24,' in text


def test_export_csv_writes_the_file(qtbot, tmp_path):
    dialog = _measured(qtbot, _classMap())
    path = tmp_path / "areas.csv"
    dialog.writeCsv(str(path))
    assert "# image: classes" in path.read_text(encoding="utf-8").splitlines()


def test_an_unreferenced_class_map_shows_pixel_counts(qtbot):
    dialog = _measured(qtbot, _classMap(referenced=False))
    table = dialog.classTable
    assert [_cell(table, r, 2) for r in range(3)] == ["48", "24", "27"]
    assert [_cell(table, r, 3) for r in range(3)] == ["–"] * 3
    assert "no map coordinates" in dialog.classMethod.text()


def test_a_new_crs_measures_the_classes_again(qtbot):
    image = _classMap(referenced=False)
    dialog = _measured(qtbot, image)
    dialog.classAreas = None
    image.setMetadataOverrides(
        {"crs": CRS("IAU_2015:49910").to_wkt(), "transform": [1000, 0, 0, 0, -1000, 0]}
    )
    dialog.refreshCoordinateSystem()
    qtbot.waitUntil(lambda: dialog.classAreas is not None, timeout=10000)
    assert dialog.classAreas is not None
    assert dialog.classAreas.totalKm2 == pytest.approx(99.0, rel=1e-3)


@pytest.mark.parametrize("size", [(1280, 800), (640, 500)])
def test_the_classes_group_is_not_cut_off_on_a_laptop_screen(qtbot, size):
    dialog = _measured(qtbot, _classMap())
    dialog.resize(*size)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert (dialog.width(), dialog.height()) == size
    assertNothingClipped(dialog)
    table = dialog.classTable
    viewport = table.viewport()
    assert viewport is not None
    assert viewport.height() >= 3 * table.rowHeight(0)  # every class row shows


@pytest.mark.parametrize("size", [(1280, 800), (640, 500), None])  # None: minimum
def test_a_fits_image_shows_its_header(qtbot, tmp_path, size):
    path = writeAltwg(tmp_path / "dtm.fits")
    dialog = ImagePropertiesDialog(loadRaster(str(path)))
    qtbot.addWidget(dialog)
    shown = [box.toPlainText() for box in dialog.findChildren(QPlainTextEdit)]
    assert any("PLANE9  = 'Elevation'" in text for text in shown)
    if size is None:
        hint = dialog.minimumSizeHint()
        size = (hint.width(), hint.height())
    dialog.resize(*size)
    dialog.show()
    qtbot.waitExposed(dialog)
    assertNothingClipped(dialog)


def test_a_stale_failure_leaves_the_newer_measurement(qtbot, monkeypatch):
    """A measurement that fails after a newer one finished (the CRS changed
    meanwhile) doesn't replace the newer table."""
    release, failed, calls = threading.Event(), threading.Event(), []

    def firstFailsLate(image: VardaRaster) -> ClassAreas:
        calls.append(image)
        if len(calls) == 1:
            release.wait(10)
            failed.set()
            raise OSError("stale read")
        return classAreas(image)

    monkeypatch.setattr(image_properties_dialog, "classAreas", firstFailsLate)
    dialog = ImagePropertiesDialog(_classMap())
    qtbot.addWidget(dialog)
    dialog.measureClasses()  # a newer run
    qtbot.waitUntil(lambda: dialog.classAreas is not None, timeout=10000)
    release.set()
    qtbot.waitUntil(failed.is_set, timeout=10000)
    qtbot.wait(300)  # the stale failure's callback arrives
    assert dialog.classTable.rowCount() == 3
    assert "stale read" not in dialog.classMethod.text()


def test_a_failed_read_is_shown(qtbot, monkeypatch):
    def broken(image: VardaRaster) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(image_properties_dialog, "classAreas", broken)
    dialog = ImagePropertiesDialog(_classMap())
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: "disk gone" in dialog.classMethod.text(), timeout=10000)
    assert dialog.classTable.rowCount() == 0
