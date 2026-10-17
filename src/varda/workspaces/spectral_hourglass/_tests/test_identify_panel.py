"""The identify panel ranks library spectra for the active endmember."""

import pytest

from synthetic_scene import WAVELENGTHS, endmemberSpectra, modelWithEndmembers
from varda.analysis.spectral_library import LibrarySpectrum
from varda.spectral_libraries.formats import LIBRARY_FORMAT_HELP
from varda.spectral_libraries.store import MICA_LIBRARY_NAME, USGS_LIBRARY_NAME
from varda.workspaces.spectral_hourglass.identify_panel import IdentifyPanel


def _library():
    E = endmemberSpectra()
    return [
        LibrarySpectrum("Flat Reference", WAVELENGTHS, E[0] * 1.1),
        LibrarySpectrum("Kaolinite KGa-1", WAVELENGTHS, E[1] * 0.9),
        LibrarySpectrum("Pyroxene", WAVELENGTHS, E[2] * 1.2),
    ]


@pytest.fixture
def panel(qtbot):
    model = modelWithEndmembers()
    widget = IdentifyPanel(model, library=_library())
    qtbot.addWidget(widget)
    return widget


def test_matches_for_the_active_endmember_are_ranked(panel):
    clay = panel.model.endmembers[1]
    panel.setEndmember(clay.id)
    assert panel.table.rowCount() == 3
    assert panel.table.item(0, 1).text() == "Kaolinite KGa-1"


def test_use_as_name_renames_the_endmember(panel):
    clay = panel.model.endmembers[1]
    panel.setEndmember(clay.id)
    panel.table.selectRow(0)
    panel.useNameButton.click()
    assert clay.name == "Kaolinite KGa-1"
    assert clay.identification.startswith("Kaolinite KGa-1")


def test_identify_all_records_each_best_match(panel):
    panel.identifyAllButton.click()
    identified = [e.identification.split(" (")[0] for e in panel.model.endmembers]
    assert identified == ["Flat Reference", "Kaolinite KGa-1", "Pyroxene"]


def test_double_clicking_a_match_previews_it(panel, qtbot):
    panel.setEndmember(panel.model.endmembers[2].id)
    with qtbot.waitSignal(panel.sigPreviewMatch) as blocker:
        panel.table.cellDoubleClicked.emit(0, 1)
    assert blocker.args[0].name == "Pyroxene"


def test_no_endmember_clears_the_table(panel):
    panel.setEndmember(None)
    assert panel.table.rowCount() == 0


def test_bundled_libraries_are_offered_and_crism_images_start_with_mica(qtbot):
    model = modelWithEndmembers()
    model.image._name = "frt00011a2e_07_if164j_trr3"
    widget = IdentifyPanel(model)
    qtbot.addWidget(widget)
    names = [widget.picker.box.itemData(i) for i in range(widget.picker.box.count())]
    assert names == [MICA_LIBRARY_NAME, USGS_LIBRARY_NAME]
    assert widget.picker.box.currentData() == MICA_LIBRARY_NAME


def test_other_images_start_with_the_usgs_library(qtbot):
    widget = IdentifyPanel(modelWithEndmembers())
    qtbot.addWidget(widget)
    assert widget.picker.box.currentData() == USGS_LIBRARY_NAME


def test_library_help_explains_the_formats(panel):
    for word in ("CSV", "ENVI", "USGS", "wavelength", "spectra names"):
        assert word in LIBRARY_FORMAT_HELP
    assert panel.picker.helpButton.toolTip()


def test_excluded_minerals_drop_out_of_matching(panel):
    clay = panel.model.endmembers[1]
    panel.setEndmember(clay.id)
    assert panel.table.item(0, 1).text() == "Kaolinite KGa-1"

    panel.excludeMatch("Kaolinite KGa-1")

    names = [panel.table.item(r, 1).text() for r in range(panel.table.rowCount())]
    assert "Kaolinite KGa-1" not in names and len(names) == 2
    assert panel.excludedButton.text() == "Excluded (1)"
    panel.identifyAll()
    assert not clay.identification.startswith("Kaolinite")


def test_excluded_minerals_can_be_restored(panel):
    panel.setEndmember(panel.model.endmembers[1].id)
    panel.excludeMatch("Kaolinite KGa-1")
    panel.excludeMatch("Pyroxene")
    panel.restoreMatch("Pyroxene")
    assert panel.excludedNames() == ["Kaolinite KGa-1"]
    panel.restoreAll()
    assert panel.excludedNames() == [] and panel.excludedButton.text() == "Excluded (0)"
    assert panel.table.item(0, 1).text() == "Kaolinite KGa-1"


def test_a_micrometre_scene_matches_the_library_like_its_nanometre_twin(qtbot):
    """The library is in nm; the scene's own unit must not matter."""
    names = {}
    for units in ("nm", "µm"):
        model = modelWithEndmembers(units)
        widget = IdentifyPanel(model, library=_library())
        qtbot.addWidget(widget)
        clay = model.endmembers[1]
        widget.setEndmember(clay.id)
        names[units] = widget.table.item(0, 0).text()
    assert names["µm"] == names["nm"] != ""
