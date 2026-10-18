"""What S and P save in the CRISM Ratio workspace: a class prefilled with the
reference mineral on the Ratio plot, asked in a dialog that Enter accepts."""

import json

import numpy as np
import pytest
from PyQt6.QtCore import Qt

from varda.common.layout_audit import assertNothingClipped
from varda.spectral_libraries.spectrum import LibrarySpectrum
from varda.workspaces.crism_ratio._tests.test_crism_ratio_workspace import (
    _pair,
    accept,
    build,
    key,
    raster,
)
from varda.workspaces.crism_ratio.crism_ratio_workspace import CrismRatioWorkspace
from varda.workspaces.crism_ratio.ratio_classes import (
    CLASS_COLORS,
    DENOMINATOR_CLASS_COLOR,
)
from varda.workspaces.linked_images.linking import Link
from varda.workspaces.workspace_kinds import buildWorkspace

SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def spectralPair():
    """Images with enough bands for every blandness window."""
    return (
        raster("browse", (30, 40), seed=1, bands=60),
        raster("data", (30, 40), seed=2, bands=60),
    )


def overlay(workspace, name):
    """Overlay a library spectrum named ``name`` on the Ratio plot."""
    workspace.plots.ratioPlot.addLibrarySpectrum(
        LibrarySpectrum(name, np.linspace(1000, 2500, 20), np.linspace(0.2, 0.3, 20))
    )


def workspaceWith(qtbot, spectralPair, *minerals):
    workspace = build(qtbot, spectralPair[0], spectralPair[1], Link.PIXEL)
    for mineral in minerals:
        overlay(workspace, mineral)
    _pair(workspace)
    return workspace


def test_s_prefills_the_reference_mineral_and_enter_saves_both_classes(
    qtbot, spectralPair
):
    workspace = workspaceWith(qtbot, spectralPair, "Olivine")

    key(workspace, Qt.Key.Key_S)
    dialog = workspace.saveDialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.windowTitle() == "Save ratio pair as ROIs"
    assert dialog.className() == "Olivine"
    edit = dialog.classBox.lineEdit()
    assert edit is not None and edit.selectedText() == "Olivine"
    mark = "✓" if workspace.plots.readout.lastResult.passed else "✗"
    pair = f"Ratio 1 (denominator {mark} Generic)"
    assert dialog.nameEdit.text() == pair
    summary = dialog.summaryLabel.text()
    assert "Numerator (11, 21) · denominator (31, 5)" in summary
    assert "(Generic check)" in summary
    accept(qtbot, workspace)

    collection = workspace.roiCollection
    numerator, denominator = collection.getAllROIs()
    assert (numerator.name, denominator.name) == ("Olivine", "Denominator")
    for roi, role in ((numerator, "numerator"), (denominator, "denominator")):
        assert collection.getProperty(roi.fid, "pair") == pair
        assert collection.getProperty(roi.fid, "role") == role
        assert collection.getProperty(roi.fid, "target") == "Olivine"
        assert "Generic check" in collection.getProperty(roi.fid, "description")
    assert workspace.roiManager.denominatorFid == denominator.fid
    assert workspace.lastClass == "Olivine"


def test_the_newest_overlay_is_prefilled_and_the_others_listed_first(
    qtbot, spectralPair
):
    workspace = workspaceWith(qtbot, spectralPair, "Olivine", "Kaolinite")
    key(workspace, Qt.Key.Key_S)
    accept(qtbot, workspace, "Jarosite")  # a class of one's own

    key(workspace, Qt.Key.Key_S)
    dialog = workspace.saveDialog
    assert dialog is not None
    assert dialog.className() == "Kaolinite"
    box = dialog.classBox
    # the overlays (newest first), then the classes used; never Denominator
    assert [box.itemText(i) for i in range(box.count())] == [
        "Kaolinite",
        "Olivine",
        "Jarosite",
    ]


def test_without_an_overlay_the_last_class_is_prefilled(qtbot, spectralPair):
    workspace = workspaceWith(qtbot, spectralPair)
    key(workspace, Qt.Key.Key_S)
    accept(qtbot, workspace, "Hematite")

    key(workspace, Qt.Key.Key_S)
    dialog = workspace.saveDialog
    assert dialog is not None
    assert dialog.className() == "Hematite"
    assert dialog.nameEdit.text().startswith("Ratio 2 ")
    accept(qtbot, workspace)
    names = [roi.name for roi in workspace.roiCollection.getAllROIs()]
    assert names == ["Hematite", "Denominator"] * 2
    # one class each: the second pair took the classes' colors
    rois = workspace.roiCollection.getAllROIs()
    assert rois[0].color == rois[2].color and rois[1].color == rois[3].color


def test_esc_saves_nothing(qtbot, spectralPair):
    workspace = workspaceWith(qtbot, spectralPair, "Olivine")
    for saveKey in (Qt.Key.Key_S, Qt.Key.Key_P):
        key(workspace, saveKey)
        dialog = workspace.saveDialog
        assert dialog is not None and dialog.isVisible()
        edit = dialog.classBox.lineEdit()
        assert edit is not None
        qtbot.keyClick(edit, Qt.Key.Key_Escape)
        assert not dialog.isVisible()
    assert len(workspace.roiCollection) == 0
    assert not workspace.pointCollection.getAllPoints()
    assert workspace.lastClass == ""


def test_dont_ask_saves_at_once_and_shift_s_still_asks(qtbot, spectralPair):
    workspace = workspaceWith(qtbot, spectralPair, "Olivine")
    key(workspace, Qt.Key.Key_S)
    dialog = workspace.saveDialog
    assert dialog is not None
    dialog.dontAskCheck.setChecked(True)
    accept(qtbot, workspace)
    assert not workspace.askClass

    key(workspace, Qt.Key.Key_S)  # no dialog: saved with the reference mineral
    assert workspace.saveDialog is dialog and not dialog.isVisible()
    names = [roi.name for roi in workspace.roiCollection.getAllROIs()]
    assert names[2:] == ["Olivine", "Denominator"]
    key(workspace, Qt.Key.Key_P)
    (point,) = workspace.pointCollection.getAllPoints()
    assert point.description.startswith("Class: Olivine. Denominator blandness")

    key(workspace, Qt.Key.Key_S, modifiers=SHIFT)
    asked = workspace.saveDialog
    assert asked is not None and asked is not dialog and asked.isVisible()
    assert asked.dontAskCheck.isChecked()
    asked.dontAskCheck.setChecked(False)  # ask again from now on
    accept(qtbot, workspace)
    assert workspace.askClass
    assert len(workspace.roiCollection) == 6


def test_p_asks_for_a_class_and_puts_it_in_the_description(qtbot, spectralPair):
    workspace = workspaceWith(qtbot, spectralPair, "Olivine")
    key(workspace, Qt.Key.Key_P)
    dialog = workspace.saveDialog
    assert dialog is not None
    assert dialog.windowTitle() == "Save numerator pixel as a point"
    assert dialog.className() == "Olivine"
    dialog.nameEdit.setText("Olivine spot")
    accept(qtbot, workspace)
    (point,) = workspace.pointCollection.getAllPoints()
    assert point.name == "Olivine spot"
    readout = workspace.plots.readout.describe()
    assert point.description == f"Class: Olivine. {readout}"


def test_the_session_keeps_the_last_class_and_dont_ask(qtbot, spectralPair):
    browse, data = spectralPair
    workspace = workspaceWith(qtbot, spectralPair)
    key(workspace, Qt.Key.Key_S)
    dialog = workspace.saveDialog
    assert dialog is not None
    dialog.dontAskCheck.setChecked(True)
    accept(qtbot, workspace, "Serpentine")
    state = json.loads(json.dumps(workspace.sessionState()))
    assert state["lastClass"] == "Serpentine" and state["askClass"] is False

    copy = buildWorkspace("crism_ratio", [browse, data], [browse, data], state)
    assert isinstance(copy, CrismRatioWorkspace)
    qtbot.addWidget(copy)
    assert copy.lastClass == "Serpentine" and not copy.askClass
    _pair(copy)
    key(copy, Qt.Key.Key_S)
    assert copy.saveDialog is None
    names = [roi.name for roi in copy.roiCollection.getAllROIs()]
    assert names == ["Serpentine", "Denominator"]


def test_a_long_library_name_prefills_its_short_form_and_lists_the_full_name(
    qtbot, spectralPair
):
    full = "Olivine Fo91 GDS70.d 165-250 µm USGS splib07a AREF"
    workspace = workspaceWith(qtbot, spectralPair, full)
    key(workspace, Qt.Key.Key_S)
    dialog = workspace.saveDialog
    assert dialog is not None
    assert dialog.className() == "Olivine Fo91"
    box = dialog.classBox
    assert [box.itemText(i) for i in range(box.count())] == ["Olivine Fo91", full]
    accept(qtbot, workspace)
    assert workspace.roiCollection.getAllROIs()[0].name == "Olivine Fo91"


def test_the_mineral_in_the_library_metadata_is_prefilled(qtbot, spectralPair):
    workspace = workspaceWith(qtbot, spectralPair)
    workspace.plots.ratioPlot.addLibrarySpectrum(
        LibrarySpectrum(
            "Ol GDS70.d",
            np.linspace(1000, 2500, 20),
            np.linspace(0.2, 0.3, 20),
            provenance={"mineral": "Olivine"},
        )
    )
    key(workspace, Qt.Key.Key_S)
    dialog = workspace.saveDialog
    assert dialog is not None and dialog.className() == "Olivine"


def test_new_classes_get_distinct_colors_and_denominators_one_gray(qtbot, spectralPair):
    workspace = workspaceWith(qtbot, spectralPair)
    for className in ("Olivine", "Kaolinite", "Olivine"):
        key(workspace, Qt.Key.Key_S)
        accept(qtbot, workspace, className)
    rois = workspace.roiCollection.getAllROIs()
    olivine, denominator, kaolinite, _, olivineAgain, _ = rois
    hexes = [r.color.toHexString()[:7] for r in rois]
    assert hexes[0] == CLASS_COLORS[0] and hexes[2] == CLASS_COLORS[1]
    assert olivineAgain.color == olivine.color  # an existing class keeps its color
    assert {hexes[1], hexes[3], hexes[5]} == {DENOMINATOR_CLASS_COLOR}
    assert kaolinite.color != olivine.color != denominator.color


def test_the_dialog_fits_a_laptop_with_a_long_mineral_name(qtbot, spectralPair):
    workspace = workspaceWith(
        qtbot,
        spectralPair,
        "Olivine Fo91 GDS70.d 165-250 µm USGS splib07a ASD+NIC4 "
        "(San Carlos, Arizona) AREF reflectance",
    )
    workspace.resize(1280, 800)
    workspace.show()
    key(workspace, Qt.Key.Key_S)
    dialog = workspace.saveDialog
    assert dialog is not None
    qtbot.waitExposed(dialog)
    assert dialog.width() <= 640
    assertNothingClipped(dialog)
    dialog.resize(dialog.minimumSizeHint())
    assertNothingClipped(dialog)
