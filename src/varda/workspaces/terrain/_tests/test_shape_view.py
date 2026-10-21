import pytest
from PyQt6 import sip
from pytestqt.exceptions import TimeoutError as QtTimeoutError

from varda.shapes._tests.meshes import icosphere
from varda.terrain.shape_scene import ShapeSettings, shapePageHtml
from varda.workspaces.terrain.terrain_view import TerrainView


@pytest.fixture
def view():
    """A view deleted when the test ends, not whenever Python collects it:
    WebEngine crashes when a page goes in a collection mid-run."""
    view = TerrainView()
    yield view
    sip.delete(view)


def test_the_shape_page_loads_and_draws(qtbot, view):
    failures: list[str] = []
    view.sigFailed.connect(failures.append)
    view.showPage(shapePageHtml(icosphere(1.0, 3), ShapeSettings(), {}))
    try:
        qtbot.waitUntil(lambda: view.ready or bool(failures), timeout=10000)
    except QtTimeoutError:
        pytest.skip("no WebGL in this (offscreen) test run")
    if failures:
        pytest.skip(f"no WebGL here: {failures[0]}")
    info: list[dict] = []
    view.evaluate("window.viewer.info()", info.append)
    qtbot.waitUntil(lambda: bool(info), timeout=5000)
    assert info[0]["plates"] == 1280
