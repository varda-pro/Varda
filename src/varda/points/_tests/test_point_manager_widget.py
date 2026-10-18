"""The Points Manager lists saved points and can delete them."""

import numpy as np
from PyQt6.QtCore import Qt

from varda.common.entities import Color, VardaRaster
from varda.geo._tests.fixtures import CRISM_MTRDR_WKT, WGS84, makeImage
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.points.point_collection import PointCollection
from varda.points.point_manager_widget import PointManagerWidget

RED = Color(1.0, 0.0, 0.0, 1.0)


def _image() -> VardaRaster:
    return VardaRaster(ArrayDataSource(np.zeros((10, 10, 3))), name="scene")


def _cell(widget, row, column) -> str:
    model = widget.model
    return model.data(model.index(row, column), Qt.ItemDataRole.DisplayRole)


def test_table_lists_the_points_and_follows_the_collection(qtbot):
    points = PointCollection()
    widget = PointManagerWidget(points)
    qtbot.addWidget(widget)
    assert widget.model.rowCount() == 0

    points.addPixel(_image(), 3, 4, name="rock", color=RED)

    assert widget.model.rowCount() == 1
    assert _cell(widget, 0, 0) == "rock"
    assert _cell(widget, 0, 1) == "scene"
    assert (_cell(widget, 0, 2), _cell(widget, 0, 3)) == ("3", "4")


def test_delete_selected_removes_the_point(qtbot):
    points = PointCollection()
    first = points.addPixel(_image(), 1, 1, color=RED)
    second = points.addPixel(_image(), 2, 2, color=RED)
    widget = PointManagerWidget(points)
    qtbot.addWidget(widget)

    widget.table.selectRow(0)
    widget.deleteButton.click()

    assert points.fids == [second]
    assert first not in points.fids


def test_selecting_a_row_reports_its_fid(qtbot):
    points = PointCollection()
    points.addPixel(_image(), 1, 1, color=RED)
    fid = points.addPixel(_image(), 2, 2, color=RED)
    widget = PointManagerWidget(points)
    qtbot.addWidget(widget)
    seen = []
    widget.sigSelectionChanged.connect(seen.append)

    widget.table.selectRow(1)

    assert seen[-1] == fid


def test_a_point_from_another_body_is_refused_in_the_status_line(qtbot):
    points = PointCollection()
    widget = PointManagerWidget(points)
    qtbot.addWidget(widget)
    mars = makeImage(CRISM_MTRDR_WKT, centerLonLat=(10.0, 15.0), pixelSize=18.0)
    earth = makeImage(WGS84, centerLonLat=(10.0, 15.0), pixelSize=0.01)

    assert widget.addPixel(mars, 3, 4, name="rock") is not None
    assert widget.status.isHidden()
    assert widget.addPixel(earth, 3, 4) is None

    assert len(points) == 1
    assert "Earth and Mars are different bodies" in widget.status.text()
    assert not widget.status.isHidden()
