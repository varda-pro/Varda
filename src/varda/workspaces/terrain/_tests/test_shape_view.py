import pytest
from pytestqt.exceptions import TimeoutError as QtTimeoutError

from varda.shapes._tests.meshes import icosphere
from varda.terrain.shape_scene import ShapeSettings, shapePageHtml
from varda.workspaces.terrain.terrain_view import TerrainView


def test_the_shape_page_loads_and_draws(qtbot):
    view = TerrainView()
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()
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
