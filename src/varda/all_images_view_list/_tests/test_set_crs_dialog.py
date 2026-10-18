"""Set Coordinate System: any CRS PROJ reads, or the same as another open
image, described as it is typed, with a warning (never a refusal) for a CRS
on another body."""

import numpy as np
import pytest
from PyQt6.QtWidgets import QDialogButtonBox

from varda.all_images_view_list.set_crs_dialog import SetCrsDialog
from varda.common.entities import VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.geo._tests.fixtures import CRISM_MTRDR_WKT, makeImage, writeWgs84
from varda.geo.equivalence import sameCrs
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.image_loading_service import loadRaster


@pytest.fixture
def mars(tmp_path) -> VardaRaster:
    return loadRaster(str(writeWgs84(tmp_path)))


def _ok(dialog: SetCrsDialog):
    return dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)


def test_a_crs_is_described_as_it_is_typed(qtbot, mars):
    dialog = SetCrsDialog(mars)
    qtbot.addWidget(dialog)
    dialog.crsEdit.setText("IAU_2015:49910")
    assert dialog.descriptionLabel.text().startswith(
        "Mars, sphere R = 3396.19 km, Equirectangular"
    )
    assert dialog.warningLabel.text() == ""
    assert _ok(dialog).isEnabled()
    chosen = dialog.chosenWkt()
    assert chosen is not None and sameCrs(chosen, "IAU_2015:49910")


def test_a_crs_on_another_body_is_warned_about_but_allowed(qtbot, mars):
    dialog = SetCrsDialog(mars)
    qtbot.addWidget(dialog)
    dialog.crsEdit.setText("EPSG:4326")
    assert "Mars and Earth are different bodies" in dialog.warningLabel.text()
    assert _ok(dialog).isEnabled()  # the user may know better
    assert dialog.chosenWkt() is not None


@pytest.mark.parametrize(
    "text", ["+proj=longlat +R=3396190 +no_defs", CRISM_MTRDR_WKT, "ESRI:104905"]
)
def test_proj_strings_and_wkt_are_read(qtbot, mars, text):
    dialog = SetCrsDialog(mars)
    qtbot.addWidget(dialog)
    dialog.crsEdit.setText(text)
    assert dialog.chosenWkt() is not None and _ok(dialog).isEnabled()


@pytest.mark.parametrize("text", ["", "not a crs"])
def test_nothing_readable_is_nothing_to_choose(qtbot, mars, text):
    dialog = SetCrsDialog(mars)
    qtbot.addWidget(dialog)
    dialog.crsEdit.setText(text)
    assert dialog.chosenWkt() is None and not _ok(dialog).isEnabled()


def test_same_as_another_open_image(qtbot, mars):
    other = makeImage(
        CRISM_MTRDR_WKT, centerLonLat=(77.0, 15.0), pixelSize=18.0, name="mtrdr"
    )
    bare = VardaRaster(ArrayDataSource(np.zeros((4, 4, 1), np.float32)), name="bare")
    dialog = SetCrsDialog(mars, [mars, other, bare])
    qtbot.addWidget(dialog)
    names = [dialog.sameAsBox.itemText(i) for i in range(dialog.sameAsBox.count())]
    assert names == ["—", "mtrdr"]  # neither itself nor one without a CRS
    dialog.sameAsBox.setCurrentIndex(1)
    chosen = dialog.chosenWkt()
    assert chosen is not None and sameCrs(chosen, CRISM_MTRDR_WKT)
    assert dialog.reason() == "The same as mtrdr (Set coordinate system…)"


def test_nothing_is_cut_off_on_a_laptop_screen(qtbot, mars):
    dialog = SetCrsDialog(mars, [mars])
    qtbot.addWidget(dialog)
    dialog.crsEdit.setText("EPSG:4326")  # the warning shows too
    dialog.show()
    qtbot.waitExposed(dialog)
    assertNothingClipped(dialog)
    dialog.resize(1280, 800)
    qtbot.wait(10)
    assertNothingClipped(dialog)
