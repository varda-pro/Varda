"""The endmember table: add, rename, recolour, merge, delete, active row."""

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHeaderView

from synthetic_scene import WAVELENGTHS, modelThroughPpi, modelWithEndmembers
from varda.spectral_libraries.formats import loadCsvLibrary
from varda.workspaces.spectral_hourglass import endmember_table
from varda.workspaces.spectral_hourglass.endmember_table import EndmemberTable


@pytest.fixture
def table(qtbot):
    widget = EndmemberTable(modelWithEndmembers())
    qtbot.addWidget(widget)
    return widget


def test_rows_follow_the_model(table):
    assert table.table.rowCount() == 3
    assert [table.table.item(r, 1).text() for r in range(3)] == [
        "flat",
        "clay",
        "mafic",
    ]
    assert table.table.item(0, 2).text() == "16"


def test_adding_makes_the_new_endmember_active(table, qtbot):
    with qtbot.waitSignal(table.sigActiveChanged) as blocker:
        table.addButton.click()
    assert table.table.rowCount() == 4
    assert blocker.args[0] == table.model.endmembers[-1].id
    assert table.activeEndmemberId() == table.model.endmembers[-1].id


def test_editing_a_name_renames_the_endmember(table):
    table.table.item(1, 1).setText("Kaolinite")
    assert table.model.endmembers[1].name == "Kaolinite"


def test_colour_cell_shows_the_endmember_colour(table):
    table.model.recolorEndmember(table.model.endmembers[0].id, "#123456")
    assert table.table.item(0, 0).background().color() == QColor("#123456")


def test_merge_and_delete_selected_rows(table):
    table.selectRows([0, 2])
    table.mergeButton.click()
    assert [e.name for e in table.model.endmembers] == ["flat", "clay"]
    assert table.model.endmembers[0].pixels.size == 32
    table.selectRows([1])
    table.deleteButton.click()
    assert [e.name for e in table.model.endmembers] == ["flat"]


def test_suggest_creates_groups_from_the_candidates(qtbot):
    widget = EndmemberTable(modelThroughPpi())
    qtbot.addWidget(widget)
    widget.suggestCount.setValue(3)
    widget.suggestButton.click()
    assert widget.table.rowCount() == 3


def test_identification_column(table):
    table.model.setIdentification(table.model.endmembers[1].id, "Kaolinite (0.91)")
    assert table.table.item(1, 3).text() == "Kaolinite (0.91)"


def test_undo_button_reverts_the_last_edit(table):
    assert table.undoButton.isEnabled() == table.model.canUndo()
    table.addButton.click()
    assert table.table.rowCount() == 4 and table.undoButton.isEnabled()
    table.undoButton.click()
    assert table.table.rowCount() == 3


def test_columns_sort_and_keep_rows_tied_to_their_endmembers(table):
    table.table.sortItems(2, Qt.SortOrder.DescendingOrder)  # by pixel count
    table.model.assignPixels(table.model.endmembers[2].id, range(500, 540))  # mafic: 56
    names = [table.table.item(r, 1).text() for r in range(3)]
    assert names[0] == "mafic"
    table.table.selectRow(0)
    table.table.setCurrentCell(0, 1)
    assert table.activeEndmemberId() == table.model.endmembers[2].id
    table.table.item(0, 1).setText("Pyroxene")
    assert table.model.endmembers[2].name == "Pyroxene"


def test_columns_can_be_resized(table):
    header = table.table.horizontalHeader()
    for column in (1, 2, 3):
        assert header.sectionResizeMode(column) == QHeaderView.ResizeMode.Interactive


def test_exported_spectra_load_back_as_a_library_in_nanometres(
    qtbot, tmp_path, monkeypatch
):
    model = modelWithEndmembers("µm")
    widget = EndmemberTable(model)
    qtbot.addWidget(widget)
    target = tmp_path / "endmembers.csv"
    monkeypatch.setattr(
        endmember_table.QFileDialog,
        "getSaveFileName",
        lambda *a, **k: (str(target), ""),
    )
    widget._exportSpectra()
    library = loadCsvLibrary(target)
    assert [s.name for s in library] == [e.name for e in model.endmembers]
    np.testing.assert_allclose(library[0].wavelengths, WAVELENGTHS)


def test_add_to_library_records_each_endmembers_mean_spread_and_pixels(table):
    table.selectRows([1])  # clay
    dialog = table.addToLibraryDialog()
    assert dialog is not None
    table.model.renameEndmember(table.model.endmembers[1].id, "ignored")  # snapshot
    (spectrum,) = dialog.spectra
    assert spectrum.name == "clay"
    assert spectrum.provenance["pixelCount"] == 16
    assert spectrum.provenance["statistic"] == "mean ± 1 σ"
    assert "Spectral Hourglass" in str(spectrum.provenance["region"])
    assert spectrum.std is not None and spectrum.std.shape == spectrum.reflectance.shape
    np.testing.assert_allclose(spectrum.wavelengths, table.model.wavelengthsNm)
    dialog.newNameEdit.setText("Endmembers")
    dialog.targetBox.setCurrentIndex(dialog.targetBox.count() - 1)
    dialog.accept()
    library = dialog.store.library("Endmembers")
    assert library is not None and [s.name for s in library.spectra] == ["clay"]


def test_add_to_library_takes_every_endmember_when_none_is_selected(table):
    table.table.clearSelection()
    dialog = table.addToLibraryDialog()
    assert dialog is not None
    assert [s.name for s in dialog.spectra] == ["flat", "clay", "mafic"]
