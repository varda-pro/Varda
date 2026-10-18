"""Add Column: an empty column to type into, or one calculated from geometry,
a raster's zonal statistics, or an expression."""

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialogButtonBox
from shapely.geometry import box

from varda.common.entities import Color, ROIMode, VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.geo._tests.fixtures import LRO_WAC_PROJ, makeImage
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.rois.add_column_dialog import AddColumnDialog
from varda.rois.roi_calculations import CalculatedColumns, ZonalStatistic
from varda.rois.roi_collection import ROICollection
from varda.rois.roi_table_model import ROITableModel


def _image(name="scene", rows=20) -> VardaRaster:
    data = np.zeros((rows, 30, 2), dtype=np.float32)
    data[:, :, 0] = np.arange(30)[None, :]
    return VardaRaster(ArrayDataSource(data, bandNames=["col", "zero"]), name=name)


@pytest.fixture
def setup(qtbot):
    image = _image()
    rois = ROICollection.fromImage(image)
    fid = rois.addROI(box(2, 2, 6, 4), "r", Color(1, 0, 0, 0.5), ROIMode.RECTANGLE)
    calculated = CalculatedColumns(rois)
    other = _image("other scene", rows=5)  # not on the grid: not offered
    dialog = AddColumnDialog(calculated, [image, other])
    qtbot.addWidget(dialog)
    return dialog, rois, fid, calculated


def _ok(dialog) -> None:
    dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()


def test_an_empty_column_is_still_the_default(setup):
    dialog, rois, fid, calculated = setup
    dialog.nameEdit.setText("notes")
    _ok(dialog)
    assert "notes" in rois.userColumns and not calculated.isCalculated("notes")


def test_a_zonal_statistic_column(setup):
    dialog, rois, fid, calculated = setup
    dialog.setKind(dialog.ZONAL)
    assert dialog.imageBox.count() == 1  # only images on the ROIs' grid
    assert dialog.notListed.text() == (
        "Not listed: other scene (different pixel grid: 30 × 5 vs 30 × 20 "
        "pixels; this needs the same grid)"
    )
    dialog.statisticBox.setCurrentText("max")
    assert dialog.nameEdit.text() == "max_col"  # suggested until typed over
    assert "5" in dialog.previewLabel.text()
    _ok(dialog)
    assert rois.getProperty(fid, "max_col") == pytest.approx(5)


def test_map_units_need_a_georeferenced_image(setup):
    dialog, *_ = setup
    dialog.setKind(dialog.GEOMETRY)
    model = dialog.measureBox.model()
    enabled = {
        dialog.measureBox.itemData(i): model.item(i).isEnabled()
        for i in range(dialog.measureBox.count())
    }
    assert enabled["area_pixels"] and not enabled["area_map"]


def test_a_bad_expression_says_why_and_cannot_be_added(setup):
    dialog, rois, fid, calculated = setup
    calculated.add("mean_col", calculated_zonal(rois))
    dialog.setKind(dialog.EXPRESSION)
    dialog.expressionEdit.setText("mean_col +")
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert not ok.isEnabled() and "syntax" in dialog.previewLabel.text()
    dialog.expressionEdit.setText("mean_col * 2")
    assert ok.isEnabled() and "7" in dialog.previewLabel.text()
    assert "mean_col" in dialog.variablesLabel.text()


def calculated_zonal(rois):
    return ZonalStatistic(_image(), 0, "mean")


def test_calculated_columns_are_read_only_and_explained_in_the_table(setup, qtbot):
    dialog, rois, fid, calculated = setup
    calculated.add("mean_col", calculated_zonal(rois))
    rois.addColumn("notes")
    model = ROITableModel(rois, calculated=calculated)
    columns = [
        model.headerData(c, Qt.Orientation.Horizontal)
        for c in range(model.columnCount())
    ]
    meanColumn, notesColumn = columns.index("mean_col"), columns.index("notes")
    assert not model.flags(model.index(0, meanColumn)) & Qt.ItemFlag.ItemIsEditable
    assert model.flags(model.index(0, notesColumn)) & Qt.ItemFlag.ItemIsEditable
    tip = model.headerData(
        meanColumn, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole
    )
    assert "mean of col" in tip


def _georeferencedDialog(qtbot) -> AddColumnDialog:
    # equirectangular, standard parallel 0, at 60°N: about 100% east–west
    image = makeImage(LRO_WAC_PROJ, centerLonLat=(0.0, 60.0), pixelSize=100.0)
    rois = ROICollection.fromImage(image)
    dialog = AddColumnDialog(CalculatedColumns(rois), [image])
    qtbot.addWidget(dialog)
    dialog.setKind(dialog.GEOMETRY)
    return dialog


def _measureItems(dialog) -> dict:
    model = dialog.measureBox.model()
    return {
        dialog.measureBox.itemData(i): model.item(i)
        for i in range(dialog.measureBox.count())
    }


def test_planar_measures_say_how_distorted_the_projection_is(qtbot):
    dialog = _georeferencedDialog(qtbot)
    items = _measureItems(dialog)
    for kind in ("area_map", "perimeter_map"):
        tip = items[kind].toolTip()
        assert "scale error" in tip and "on the body" in tip
    assert items["area_body"].isEnabled() and items["area_body"].toolTip()
    assert items["area_map"].text() == "Area (metre², planar)"


def test_body_measures_suggest_km_names_and_preview(qtbot):
    dialog = _georeferencedDialog(qtbot)
    dialog.measureBox.setCurrentIndex(dialog.measureBox.findData("area_body"))
    assert dialog.nameEdit.text() == "area_km2"


def test_body_measures_need_a_georeferenced_image(setup):
    dialog, *_ = setup
    dialog.setKind(dialog.GEOMETRY)
    items = _measureItems(dialog)
    assert not items["area_body"].isEnabled()
    assert not items["perimeter_body"].isEnabled()


def test_the_geometry_page_fits_at_laptop_size(qtbot):
    dialog = _georeferencedDialog(qtbot)
    dialog.resize(dialog.minimumSizeHint())
    dialog.show()
    qtbot.waitExposed(dialog)
    assertNothingClipped(dialog)
