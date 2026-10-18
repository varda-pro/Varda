"""The Lines manager's table: lengths on the body, and how they were measured."""

import numpy as np
from affine import Affine
from pyproj import CRS
from PyQt6.QtCore import Qt

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.lines.line_collection import LineCollection
from varda.lines.line_manager_widget import LineTableModel, formatLength

_EQC = "+proj=eqc +lat_ts=0 +lon_0=0 +R=3396190 +units=m +no_defs"


def _image() -> VardaRaster:
    # 0°-centered equirectangular at 60°N: 2x east-west scale error
    return VardaRaster(
        ArrayDataSource(
            np.zeros((10, 300, 1), np.float32),
            transform=Affine(100.0, 0, 0.0, 0, -100.0, 3396190 * np.pi / 3),
            crs=CRS.from_user_input(_EQC),
        )
    )


def test_the_length_tooltip_says_how_it_was_measured(qtbot):
    lines = LineCollection()
    lines.addLine([[0.0, 0.0], [200.0, 0.0]])
    model = LineTableModel(lines, _image())
    index = model.index(0, 2)
    assert model.data(index) == "10 km"
    tip = model.data(index, Qt.ItemDataRole.ToolTipRole)
    assert "geodesic (Karney 2013) on sphere R = 3396.19 km" in tip
    assert "IAU 2015 reference ellipsoid" in tip  # differs by > 0.1% at 60°N
    assert "scale error" in tip  # the projection's distortion along it


def test_unmeasurable_lengths_show_a_dash():
    assert formatLength(float("nan"), "m") == "—"
