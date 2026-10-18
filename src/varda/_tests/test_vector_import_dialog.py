"""The Import… flow of the ROI, Points and Lines managers: the file dialog
starts in the remembered folder, the file is read in the background, the
import dialog fits a laptop screen, and importing adds to the collection."""

import geopandas as gpd
import numpy as np
import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QMessageBox
from shapely.geometry import LineString, Point, box

from varda import vector_import_dialog
from varda.common.entities import VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.lines.line_collection import LineCollection
from varda.lines.line_manager_widget import LineManagerWidget
from varda.plotting.plot import VardaPlotWidget
from varda.points.point_collection import PointCollection
from varda.points.point_manager_widget import PointManagerWidget
from varda.rois.roi_collection import ROICollection
from varda.rois.roi_manager_widget import ROIManagerWidget
from varda.vector_import import Coordinates, Target, readVectorFile
from varda.vector_import_dialog import FOLDER_KEY, VectorImportDialog


def _image() -> VardaRaster:
    return VardaRaster(ArrayDataSource(np.zeros((30, 40, 2))), name="scene")


@pytest.fixture
def mixedFile(tmp_path):
    """A GeoPackage in pixel coordinates with two layers: polygons, points
    and a line in one, a polygon in the other."""
    path = tmp_path / "features.gpkg"
    gpd.GeoDataFrame(
        {
            "name": ["field", "rock", "trail"],
            "geologic_unit_description_long": ["Basalt", "Dust", "Path"],
        },
        geometry=[box(1, 1, 6, 6), Point(3, 3), LineString([(0, 0), (20, 10)])],
    ).to_file(path, layer="mapped features with a long layer name")
    gpd.GeoDataFrame({"name": ["other"]}, geometry=[box(10, 10, 12, 12)]).to_file(
        path, layer="second"
    )
    return path


@pytest.mark.parametrize("target", list(Target))
def test_the_import_dialog_fits_a_laptop_screen(qtbot, mixedFile, target):
    dialog = VectorImportDialog(readVectorFile(mixedFile), _image(), target)
    qtbot.addWidget(dialog)
    minimum = dialog.minimumSizeHint()
    assert minimum.width() <= 800 and minimum.height() <= 600
    dialog.resize(1280, 800)
    dialog.show()
    qtbot.waitExposed(dialog)
    assertNothingClipped(dialog)
    dialog.resize(minimum.width(), 560)
    assertNothingClipped(dialog)


def test_the_csv_dialog_fits_a_laptop_screen(qtbot, tmp_path):
    path = tmp_path / "sites.csv"
    path.write_text("site,x,y\nA,3,4\n")
    dialog = VectorImportDialog(readVectorFile(path), _image(), Target.POINTS)
    qtbot.addWidget(dialog)
    dialog.resize(1280, 800)
    dialog.show()
    qtbot.waitExposed(dialog)
    assertNothingClipped(dialog)
    assert dialog.coordinatesCombo.currentData() is Coordinates.PIXELS


def test_the_preview_counts_and_names(qtbot, mixedFile):
    dialog = VectorImportDialog(readVectorFile(mixedFile), _image(), Target.ROIS)
    qtbot.addWidget(dialog)
    text = dialog.preview.text()
    assert "1 polygons, 1 points, 1 lines" in text
    assert "1 polygons become ROIs" in text and "field" in text
    assert dialog.importButton.isEnabled()


def test_choosing_another_layer_reads_it_in_the_background(qtbot, mixedFile):
    dialog = VectorImportDialog(readVectorFile(mixedFile), _image(), Target.ROIS)
    qtbot.addWidget(dialog)
    dialog.layerCombo.setCurrentText("second")
    qtbot.waitUntil(lambda: dialog.source.layer == "second")
    assert dialog.plan is not None
    assert [f.name for f in dialog.plan.features] == ["other"]


def test_map_coordinates_on_an_image_without_a_crs_are_refused(qtbot, mixedFile):
    dialog = VectorImportDialog(readVectorFile(mixedFile), _image(), Target.ROIS)
    qtbot.addWidget(dialog)
    dialog.coordinatesCombo.setCurrentIndex(
        dialog.coordinatesCombo.findData(Coordinates.IMAGE)
    )
    assert "no map coordinates" in dialog.preview.text()
    assert not dialog.importButton.isEnabled()


def _importThroughTheButton(qtbot, manager, path, tmp_path, monkeypatch):
    """Click Import…, choose ``path``, accept the dialog; returns the folder
    the file dialog started in."""
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue(FOLDER_KEY, str(tmp_path / "earlier"))
    manager.importer.settings = settings
    started = []

    def choose(parent, caption, folder, filters):
        started.append(folder)
        return str(path), ""

    monkeypatch.setattr(vector_import_dialog.QFileDialog, "getOpenFileName", choose)
    manager.importButton.click()
    qtbot.waitUntil(lambda: manager.importer.dialog is not None)
    manager.importer.dialog.accept()
    message = manager.importer.message
    assert isinstance(message, QMessageBox) and "Imported 1" in message.text()
    message.close()
    assert settings.value(FOLDER_KEY) == str(path.parent)
    return started[0]


def test_the_roi_managers_import_button_adds_rois(
    qtbot, mixedFile, tmp_path, monkeypatch
):
    rois = ROICollection()
    manager = ROIManagerWidget(rois, _image(), VardaPlotWidget())
    qtbot.addWidget(manager)
    folder = _importThroughTheButton(qtbot, manager, mixedFile, tmp_path, monkeypatch)
    assert folder == str(tmp_path / "earlier")
    assert [roi.name for roi in rois.getAllROIs()] == ["field"]


def test_the_points_managers_import_button_adds_points(
    qtbot, mixedFile, tmp_path, monkeypatch
):
    points = PointCollection()
    manager = PointManagerWidget(points, image=_image())
    qtbot.addWidget(manager)
    _importThroughTheButton(qtbot, manager, mixedFile, tmp_path, monkeypatch)
    point = points.getAllPoints()[0]
    assert (point.name, point.x, point.y, point.image) == ("rock", 3, 3, "scene")


def test_the_lines_managers_import_button_adds_lines(
    qtbot, mixedFile, tmp_path, monkeypatch
):
    lines = LineCollection()
    manager = LineManagerWidget(lines, _image())
    qtbot.addWidget(manager)
    _importThroughTheButton(qtbot, manager, mixedFile, tmp_path, monkeypatch)
    assert [line.name for line in lines.lines()] == ["trail"]


def test_import_is_off_without_an_image(qtbot):
    points = PointManagerWidget(PointCollection())
    lines = LineManagerWidget(LineCollection())
    qtbot.addWidget(points)
    qtbot.addWidget(lines)
    assert not points.importButton.isEnabled()
    assert not lines.importButton.isEnabled()


def test_an_unreadable_file_is_reported(qtbot, tmp_path):
    points = PointCollection()
    manager = PointManagerWidget(points, image=_image())
    qtbot.addWidget(manager)
    path = tmp_path / "broken.geojson"
    path.write_text("not json")
    manager.importer.openFile(path)
    qtbot.waitUntil(lambda: manager.importer.message is not None)
    message = manager.importer.message
    assert message is not None and "couldn't read broken.geojson" in message.text()
    message.close()
