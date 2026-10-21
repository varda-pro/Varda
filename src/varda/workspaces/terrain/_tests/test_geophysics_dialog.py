"""Shape Geophysics: its settings dialog, running it from the shape panel,
and its place in the Processing menu."""

import pytest

from varda._actions import ALL_ACTIONS, PROCESSING_SUBMENUS
from varda.geo.bodies import BODIES
from varda.shapes._tests.meshes import icosphere
from varda.shapes.formats import writeObj
from varda.shapes.geophysics import GeophysicsSettings
from varda.workspaces.terrain.geophysics_dialog import GeophysicsDialog
from varda.workspaces.terrain.shape_panel import ShapePanel


@pytest.fixture
def dialog(qtbot):
    widget = GeophysicsDialog(BODIES["Bennu"], plateCount=1000)
    qtbot.addWidget(widget)
    return widget


def test_bennus_values_fill_the_dialog(dialog):
    settings = dialog.settings()
    assert settings is not None
    assert settings.density == 1190.0 and settings.densitySigma == 13.0
    assert settings.rotationPeriodH == pytest.approx(4.296061)
    assert (
        "Barnouin" in dialog.sources.text() and "Hergenrother" in dialog.sources.text()
    )


def test_a_body_without_a_density_must_be_given_one(qtbot):
    widget = GeophysicsDialog(BODIES["Arrokoth"], plateCount=1000)
    qtbot.addWidget(widget)
    assert widget.settings() is None
    assert not widget.runButton.isEnabled()
    widget.density.setValue(500.0)
    assert widget.settings() is not None and widget.runButton.isEnabled()


@pytest.mark.parametrize(
    "field, value",
    [("density", 0.0), ("period", 0.0), ("sigma", 2000.0)],
)
def test_invalid_values_are_refused(dialog, field, value):
    getattr(dialog, field).setValue(value)
    assert dialog.settings() is None
    assert not dialog.runButton.isEnabled()
    assert dialog.problem.text()


def test_running_adds_the_twelve_datasets_in_the_background(qtbot, tmp_path):
    panel = ShapePanel()
    qtbot.addWidget(panel)
    writeObj(icosphere(0.25, 2), tmp_path / "ball.obj")
    panel.openShape(tmp_path / "ball.obj")
    qtbot.waitUntil(lambda: panel.model is not None, timeout=10000)
    assert panel.geophysicsButton.isEnabled()
    panel.runGeophysics(
        GeophysicsSettings(density=1190.0, densitySigma=13.0, rotationPeriodH=4.296)
    )
    qtbot.waitUntil(lambda: len(panel.plateData) == 12, timeout=20000)
    assert "slope" in panel.plateData and "slope (ρ+σ)" in panel.plateData
    assert panel.colorBox.currentText() == "slope"


def test_the_processing_menu_offers_shape_geophysics():
    (action,) = [a for a in ALL_ACTIONS if a.id == "varda.processing.shape_geophysics"]
    assert action.title == "Shape Geophysics…"
    assert action.menus[0].id == "varda/processing/shape_models"
    assert ("varda/processing", "Shape Models") in [
        (parent, item.title) for parent, item in PROCESSING_SUBMENUS
    ]
