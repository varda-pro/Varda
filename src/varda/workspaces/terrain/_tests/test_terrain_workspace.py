"""The 3D Terrain workspace: choices, the surface it builds, sessions and the
web page export (rendering itself needs WebGL, which offscreen test runs may
not have, so those checks are skipped there)."""

import base64
import io

import geopandas as gpd
import numpy as np
import pytest
import shapely
from affine import Affine
from pytestqt.exceptions import TimeoutError as QtTimeoutError
from PIL import Image
from pyproj import CRS

from varda.common.di_types import ProjectImages
from PyQt6.QtCore import QThreadPool
from PyQt6.QtGui import QColor

from varda.common.entities import VardaRaster
from varda.lines.line_collection import LineCollection, shareLines, unshareLines
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.terrain.scene import FILL_METHOD
from varda.utilities.debug import generate_perlin_noise_3d
from varda.workspaces.terrain import terrain_workspace
from varda.workspaces.terrain.overlay_panel import MATCH_ITSELF
from varda.workspaces.terrain.terrain_workspace import (
    ELEVATION,
    PLAIN,
    AnimationDialog,
    PrintDialog,
    StillDialog,
    TerrainWorkspace,
)
from varda.workspaces.workspace_kinds import buildWorkspace, describeWorkspace

TRANSFORM = Affine(20, 0, 0, 0, -20, 0)
CRS_ = CRS.from_user_input("+proj=eqc +R=3389500 +units=m +no_defs")


def _image(data, name) -> VardaRaster:
    return VardaRaster(
        ArrayDataSource(np.asarray(data, np.float32), transform=TRANSFORM, crs=CRS_),
        name=name,
    )


@pytest.fixture
def images():
    rows, cols = 40, 60
    yy, xx = np.mgrid[0:rows, 0:cols]
    dem = -1500 + 200 * np.exp(-((xx - 30) ** 2 + (yy - 20) ** 2) / 100.0)
    ortho = np.random.default_rng(0).random((rows, cols, 1))
    return ProjectImages([_image(dem[..., None], "dem"), _image(ortho, "ortho")])


@pytest.fixture
def workspace(qtbot, images):
    widget = TerrainWorkspace(images)
    qtbot.addWidget(widget)
    qtbot.waitUntil(lambda: widget.terrain is not None, timeout=10000)
    return widget


def test_the_dem_and_drapes_on_its_grid_are_offered(workspace, images):
    assert workspace.dem() is images[0]
    drapes = [workspace.drapeBox.itemText(i) for i in range(workspace.drapeBox.count())]
    assert drapes == [PLAIN, ELEVATION, "ortho"]
    assert workspace.terrain.heights.shape == (40, 60)
    low, high = workspace.terrain.zRange
    assert low == pytest.approx(-1500, abs=1) and high == pytest.approx(-1300, abs=1)


def test_images_loaded_later_can_be_draped(workspace, images):
    images.append(_image(np.zeros((40, 60, 3)), "color"))
    assert workspace.drapeBox.findText("color") >= 0


def test_images_that_cant_be_placed_are_named_under_the_drapes(workspace, images):
    assert workspace.drapeNotListed.isHidden()
    elsewhere = VardaRaster(
        ArrayDataSource(
            np.zeros((20, 30, 3), np.float32),
            transform=Affine(20, 0, 90000, 0, -20, 90000),
            crs=CRS_,
        ),
        name="elsewhere",
    )
    images.append(elsewhere)
    assert workspace.drapeBox.findText("elsewhere") < 0
    assert "elsewhere (doesn't overlap" in workspace.drapeNotListed.text()
    assert not workspace.drapeNotListed.isHidden()


def _textureOf(dataUrl: str | None) -> np.ndarray:
    assert dataUrl is not None
    png = base64.b64decode(dataUrl.split(",", 1)[1])
    return np.asarray(Image.open(io.BytesIO(png)))


def test_an_image_on_another_grid_is_draped_by_map_location(workspace, images, qtbot):
    """An orthoimage at 10 m over a 20 m DEM (named differently, as HiRISE
    DTMs and orthoimages are): offered, and placed by its georeferencing."""
    ortho = np.zeros((80, 120, 1), np.float32)
    ortho[:, 60:] = 1  # bright east half
    finer = VardaRaster(
        ArrayDataSource(ortho, transform=Affine(10, 0, 0, 0, -10, 0), crs=CRS_),
        name="PSP_ortho",
    )
    images.append(finer)
    index = workspace.drapeBox.findText("PSP_ortho")
    assert index >= 0
    before = workspace._texture  # the elevation colors'
    workspace.drapeBox.setCurrentIndex(index)
    qtbot.waitUntil(lambda: workspace._texture != before, timeout=10000)
    texture = _textureOf(workspace._texture)
    rows, cols = texture.shape[:2]
    assert cols / rows == pytest.approx(60 / 40, rel=0.05)  # the DEM's extent
    assert texture[:, : cols // 2 - 2, 0].max() < 30
    assert texture[:, cols // 2 + 2 :, 0].min() > 225
    assert (texture[..., 3] == 255).all()


def test_drapes_and_overlays_are_drawn_at_texture_size(qtbot, monkeypatch):
    """A drape or overlay larger than a texture is read reduced, never whole."""
    monkeypatch.setattr(terrain_workspace, "MAX_TEXTURE", 32)
    dem = np.random.default_rng(1).random((40, 60, 1)) * 100
    images = ProjectImages([_image(dem, "dem"), _image(np.ones((40, 60, 3)), "color")])
    workspace = TerrainWorkspace(images, images[0])
    qtbot.addWidget(workspace)
    qtbot.waitUntil(lambda: workspace._texture is not None, timeout=10000)
    assert _textureOf(workspace._texture).shape[:2] == (20, 30)  # elevation colors
    before = workspace._texture
    workspace.drapeBox.setCurrentIndex(workspace.drapeBox.findText("color"))
    qtbot.waitUntil(lambda: workspace._texture != before, timeout=10000)
    assert _textureOf(workspace._texture).shape[:2] == (20, 30)


def test_the_web_page_export_has_controls_for_readers(workspace, tmp_path):
    workspace.drapeBox.setCurrentText("ortho")
    workspace.saveWebPage(str(tmp_path / "view.html"), None)
    html = (tmp_path / "view.html").read_text()
    assert 'id="panel"' in html and "data:image/png;base64," in html


def test_the_workspace_comes_back_from_a_session(workspace, images, qtbot):
    workspace.exaggeration.setValue(4)
    workspace.drapeBox.setCurrentText("ortho")
    workspace._keepView(
        "Crater", {"position": [0, -1, 1], "target": [0, 0, 0], "fov": 35}
    )
    described = describeWorkspace(workspace)
    assert described is not None
    kind, shown = described
    assert kind == "terrain" and shown == [images[0], images[1]]
    restored = buildWorkspace(kind, shown, images, workspace.sessionState())
    assert isinstance(restored, TerrainWorkspace)
    qtbot.addWidget(restored)
    assert restored.settings.exaggeration == 4
    assert restored.drapeImage() is images[1]
    assert list(restored.views) == ["Crater"]


def test_still_sizes(qtbot):
    dialog = StillDialog()
    qtbot.addWidget(dialog)
    assert dialog.stillSize() == (3840, 2160)
    dialog.preset.setCurrentText("Custom")
    dialog.widthBox.setValue(5000)
    assert dialog.stillSize() == (5000, 2160)


def test_a_still_renders_where_webgl_is_available(workspace, qtbot, tmp_path):
    try:
        qtbot.waitUntil(lambda: workspace.view.ready, timeout=8000)
    except QtTimeoutError:
        pytest.skip("no WebGL in this (offscreen) test run")
    workspace.saveStill(str(tmp_path / "still.png"), 800, 500, transparent=True)
    qtbot.waitUntil(lambda: (tmp_path / "still.png").exists(), timeout=10000)
    assert (tmp_path / "still.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def _offsetOverlay(images, size=(40, 60)):
    """An overlay of the DEM's own texture whose georeferencing is 60 m east
    and 40 m south of the truth (an orthoimage on the DEM's grid holds the
    texture)."""
    rows, cols = size
    texture = generate_perlin_noise_3d((rows, cols, 2), (rows // 10, cols // 10, 2))
    texture = texture[:, :, :1].astype(np.float32)
    images.append(_image(texture, "ortho"))
    top, left = rows // 8, cols // 8
    overlay = VardaRaster(
        ArrayDataSource(
            np.repeat(texture[top:-top, left:-left], 3, axis=2),
            transform=Affine(20, 0, left * 20 + 60, 0, -20, -top * 20 - 40),
            crs=CRS_,
        ),
        name="crism",
    )
    images.append(overlay)
    return overlay


def _finishBackgroundWork(qtbot):
    """Let builds still on their way finish before the workspaces go (a
    worker started within an event loop reaches the pool a tick later)."""
    qtbot.wait(20)
    QThreadPool.globalInstance().waitForDone(30000)


def test_an_overlay_is_draped_and_auto_aligned(qtbot):
    rows, cols = 240, 320
    yy, xx = np.mgrid[0:rows, 0:cols]
    dem = -1500 + 100 * np.sin(xx / 30.0) * np.cos(yy / 25.0)
    images = ProjectImages([_image(dem[..., None], "dem")])
    overlay = _offsetOverlay(images, (rows, cols))
    workspace = TerrainWorkspace(images, images[0])
    qtbot.addWidget(workspace)
    assert overlay in workspace._overlayCandidates()
    added = workspace.overlayPanel.add(overlay)
    assert added.reference == "ortho"  # the first one-band image on the DEM's grid
    qtbot.waitUntil(lambda: len(workspace._overlays) == 1, timeout=10000)
    west, south, east, north = workspace._overlays[0].uvBounds
    assert 0 <= west < east <= 1 and 0 <= south < north <= 1
    workspace.autoAlign(0)
    qtbot.waitUntil(lambda: added.alignment.points > 0, timeout=30000)
    moved = added.alignment.correct(np.array([[3000.0, -2400.0]]))[0]
    assert moved == pytest.approx([3000 - 60, -2400 + 40], abs=4)
    # which band was matched is shown and recorded
    assert added.alignment.matchedOn == "crism" and added.alignment.band is not None
    assert added.alignment.bandLabel in workspace.overlayPanel.alignment.text()
    saved = workspace.sessionState()["overlays"][0]["alignment"]
    assert saved["band"] == added.alignment.band and saved["matchedOn"] == "crism"


def test_a_finer_overlay_is_matched_at_the_references_pixel_size(qtbot, monkeypatch):
    """An overlay 4 × finer than the DEM (an orthoimage at 5 m over a 20 m
    DEM) is read every 4th pixel, only over the DEM, for Auto Align, and
    still recovers its georeferencing error."""
    rows, cols = 240, 320
    yy, xx = np.mgrid[0:rows, 0:cols]
    dem = -1500 + 100 * np.sin(xx / 30.0) * np.cos(yy / 25.0)
    images = ProjectImages([_image(dem[..., None], "dem")])
    texture = generate_perlin_noise_3d((rows, cols, 2), (rows // 10, cols // 10, 2))
    texture = texture[:, :, :1].astype(np.float32)
    images.append(_image(texture, "ortho"))
    top, left = rows // 8, cols // 8
    fine = np.kron(texture[top:-top, left:-left], np.ones((4, 4, 1), np.float32))
    overlay = VardaRaster(
        ArrayDataSource(
            fine,
            transform=Affine(5, 0, left * 20 + 60, 0, -5, -top * 20 - 40),
            crs=CRS_,
        ),
        name="hirise",
    )
    images.append(overlay)
    matched = []
    align = terrain_workspace.align

    def recording(reference, moving, *args):
        matched.append((moving.width, moving.height))
        return align(reference, moving, *args)

    monkeypatch.setattr(terrain_workspace, "align", recording)
    workspace = TerrainWorkspace(images, images[0])
    qtbot.addWidget(workspace)
    added = workspace.overlayPanel.add(overlay)
    qtbot.waitUntil(lambda: len(workspace._overlays) == 1, timeout=10000)
    workspace.autoAlign(0)
    qtbot.waitUntil(lambda: added.alignment.points > 0, timeout=30000)
    assert matched == [(fine.shape[1] // 4, fine.shape[0] // 4)]
    moved = added.alignment.correct(np.array([[3000.0, -2400.0]]))[0]
    assert moved == pytest.approx([3000 - 60, -2400 + 40], abs=4)
    _finishBackgroundWork(qtbot)


def test_overlays_come_back_from_a_session(workspace, images, qtbot):
    overlay = _offsetOverlay(images)
    added = workspace.overlayPanel.add(overlay)
    added.opacity = 0.4
    added.alignment.shift = (12.0, -3.0)
    described = describeWorkspace(workspace)
    assert described is not None
    kind, shown = described
    assert shown[-1] is overlay
    restored = buildWorkspace(kind, shown, images, workspace.sessionState())
    assert isinstance(restored, TerrainWorkspace)
    qtbot.addWidget(restored)
    (back,) = restored.overlayPanel.overlays
    assert back.image is overlay and back.opacity == 0.4
    assert back.alignment.shift == (12.0, -3.0)


def test_sides_are_a_setting_the_view_gets(workspace):
    workspace.sides.setChecked(True)
    workspace.baseDepth.setValue(150)
    assert workspace.settings.sides and workspace.settings.baseDepth == 150
    assert workspace.form.isRowVisible(workspace.baseDepth)
    assert workspace.sessionState()["settings"]["sides"] is True


def test_side_colors_and_edges_are_settings(workspace, qtbot, monkeypatch):
    workspace.sides.setChecked(True)
    assert workspace.form.isRowVisible(workspace.sideColor)
    workspace.sideColor.setCurrentText("White")
    workspace.sideColor.activated.emit(workspace.sideColor.currentIndex())
    assert workspace.settings.sideColor == "#f0f0f0"
    # a custom color, from the color dialog
    monkeypatch.setattr(
        "varda.workspaces.terrain.terrain_workspace.QColorDialog.getColor",
        lambda *_: QColor("#123456"),
    )
    workspace.sideColor.activated.emit(workspace.sideColor.count() - 1)
    assert workspace.settings.sideColor == "#123456"
    assert workspace.sideColor.currentText() == "#123456"

    assert workspace.settings.edges == "smooth"
    workspace.edges.setCurrentText("Stepped")
    assert workspace.settings.edges == "stepped"
    # trimming builds the surface again, a rectangle
    workspace.terrain = None
    workspace.edges.setCurrentText("Trimmed to a rectangle")
    qtbot.waitUntil(lambda: workspace.terrain is not None, timeout=10000)
    assert workspace.terrain is not None
    assert np.isfinite(workspace.terrain.heights).all()  # the test DEM is full

    described = describeWorkspace(workspace)
    assert described is not None
    restored = buildWorkspace(
        *described, workspace.projectImages, workspace.sessionState()
    )
    assert isinstance(restored, TerrainWorkspace)
    qtbot.addWidget(restored)
    assert restored.edges.currentText() == "Trimmed to a rectangle"
    assert restored.sideColor.currentText() == "#123456"


def test_the_print_dialog_shows_the_printed_size(workspace, qtbot):
    dialog = PrintDialog(workspace.terrain, 2.0)
    qtbot.addWidget(dialog)
    dialog.widthBox.setValue(120)
    width, depth, height = dialog.printedSize()
    # a 1200 × 800 m DEM of 20 m pixels: its points, at the pixels' centers,
    # span 1180 × 780 m
    mmPerM = 120 / 1180
    assert (width, depth) == pytest.approx((120, 780 * mmPerM))
    # 200 m of relief, doubled, on a 3 mm base
    assert height == pytest.approx(200 * mmPerM * 2 + 3, abs=0.5)
    assert "120 × 79" in dialog.sizeLabel.text()
    # heights in kilometers print a thousand times taller per unit
    inKilometers = PrintDialog(workspace.terrain, 2.0, metersPerHeightUnit=1000.0)
    qtbot.addWidget(inKilometers)
    inKilometers.widthBox.setValue(120)
    assert inKilometers.printedSize()[2] == pytest.approx(
        200_000 * mmPerM * 2 + 3, abs=500
    )


def test_the_height_unit_is_a_setting_kept_with_the_session(workspace, qtbot):
    assert workspace.settings.heightUnit == "m"  # no vertical CRS: meters
    workspace.heightUnit.setCurrentText("Kilometers")
    assert workspace.settings.heightUnit == "km"
    assert workspace.baseDepth.suffix() == " km"
    assert workspace.sessionState()["settings"]["heightUnit"] == "km"
    described = describeWorkspace(workspace)
    assert described is not None
    restored = buildWorkspace(
        *described, workspace.projectImages, workspace.sessionState()
    )
    assert isinstance(restored, TerrainWorkspace)
    qtbot.addWidget(restored)
    assert restored.settings.heightUnit == "km"
    assert restored.heightUnit.currentText() == "Kilometers"
    _finishBackgroundWork(qtbot)


def test_a_dem_whose_crs_declares_feet_is_in_feet(qtbot):
    crs = CRS.from_user_input("EPSG:2229+6360")  # US feet, also vertically
    dem = VardaRaster(
        ArrayDataSource(
            np.random.default_rng(0).random((20, 30, 1)).astype(np.float32),
            transform=TRANSFORM,
            crs=crs,
        ),
        name="dem",
    )
    workspace = TerrainWorkspace(ProjectImages([dem]), dem)
    qtbot.addWidget(workspace)
    assert workspace.settings.heightUnit == "ft"
    assert workspace.overlayPanel.mapUnit == "US ft"
    assert workspace.overlayPanel.shiftEast.suffix() == " US ft"
    _finishBackgroundWork(qtbot)


def test_overlay_units_follow_the_dem(workspace):
    panel = workspace.overlayPanel
    assert panel.mapUnit == "m" and panel.shiftEast.suffix() == " m"
    panel.setMapUnit("°")
    assert panel.shiftEast.suffix() == "°" and panel.shiftEast.decimals() == 6


def test_auto_align_shades_the_band_shown(qtbot, monkeypatch):
    rows, cols = 40, 60
    first = np.zeros((rows, cols))
    second = np.full((rows, cols), 7.0)
    dem = _image(np.stack([first, second], axis=2), "dem")
    images = ProjectImages([dem])
    overlay = _offsetOverlay(images)
    workspace = TerrainWorkspace(images, dem)
    qtbot.addWidget(workspace)
    workspace.bandBox.setCurrentIndex(1)
    shaded = []

    def hillshade(heights, pixelSize, *sun):
        shaded.append(np.asarray(heights))
        raise RuntimeError("enough")

    monkeypatch.setattr(
        "varda.workspaces.terrain.terrain_workspace.hillshade", hillshade
    )
    added = workspace.overlayPanel.add(overlay)
    added.reference = "DEM hillshade"
    workspace.autoAlign(0)
    qtbot.waitUntil(lambda: bool(shaded), timeout=10000)
    assert np.all(shaded[0] == 7.0)
    _finishBackgroundWork(qtbot)


def test_the_stl_export_writes_a_closed_solid(workspace, qtbot, tmp_path):
    path = tmp_path / "dem.stl"
    workspace.savePrint(str(path), 120.0, 2.0, 3.0, 256)
    qtbot.waitUntil(lambda: "Saved a solid" in workspace.status.text(), timeout=10000)
    data = path.read_bytes()
    count = int.from_bytes(data[80:84], "little")
    assert count > 1000 and len(data) == 84 + 50 * count


def test_elevation_colors_come_with_a_color_bar(workspace, qtbot):
    workspace.drapeBox.setCurrentText(ELEVATION)
    qtbot.waitUntil(lambda: workspace._legend is not None, timeout=10000)
    legend = workspace._legend
    assert len(legend.colors) == 16 and legend.low < legend.high
    workspace.drapeBox.setCurrentText(PLAIN)
    qtbot.waitUntil(lambda: workspace._legend is None, timeout=10000)


def test_lines_from_a_file_are_draped_and_come_back_with_the_session(
    workspace, images, qtbot, tmp_path
):
    traverse = gpd.GeoDataFrame(
        geometry=[shapely.LineString([(100, -100), (1100, -700)])], crs=CRS_
    )
    traverse.to_file(tmp_path / "traverse.gpkg")
    assert workspace.addLines(str(tmp_path / "traverse.gpkg"))
    ((path, line),) = workspace.lineLayers
    assert line.name == "traverse" and workspace.lineList.count() == 1
    assert not workspace.addLines(str(tmp_path / "missing.gpkg"))  # a message, no crash
    described = describeWorkspace(workspace)
    assert described is not None
    restored = buildWorkspace(*described, images, workspace.sessionState())
    assert isinstance(restored, TerrainWorkspace)
    qtbot.addWidget(restored)
    assert [line.name for _, line in restored.lineLayers] == ["traverse"]


def test_the_animation_dialog_says_how_long_it_runs(qtbot):
    dialog = AnimationDialog()
    qtbot.addWidget(dialog)
    dialog.framesBox.setValue(90)
    dialog.fpsBox.setCurrentText("30")
    assert dialog.length.text() == "3.0 seconds long."
    frames, degrees, fps, size, suffix = dialog.choices()
    assert (frames, degrees, fps, size, suffix) == (
        90,
        360.0,
        30.0,
        (1280, 720),
        ".webp",
    )


def test_a_flyover_renders_where_webgl_is_available(workspace, qtbot, tmp_path):
    try:
        qtbot.waitUntil(lambda: workspace.view.ready, timeout=8000)
    except QtTimeoutError:
        pytest.skip("no WebGL in this (offscreen) test run")
    workspace.saveAnimation(str(tmp_path / "orbit.gif"), 4, 90, 10, 160, 100)
    qtbot.waitUntil(lambda: "Saved a 4-frame" in workspace.status.text(), timeout=30000)
    assert (tmp_path / "orbit.gif").read_bytes()[:3] == b"GIF"


def test_the_overlay_display_dock_follows_the_selection_and_redraws(
    workspace, images, qtbot
):
    cube = VardaRaster(
        ArrayDataSource(
            np.random.default_rng(1).random((30, 40, 6)).astype(np.float32),
            transform=Affine(20, 0, 200, 0, -20, -100),
            crs=CRS_,
        ),
        name="parameters",
    )
    images.append(cube)
    overlay = workspace.overlayPanel.add(cube)
    assert workspace.displayDock.isVisibleTo(workspace)
    assert workspace.overlayDisplay.overlay is overlay
    assert workspace.overlayDisplay.splitter.count() == 2  # settings, histograms
    with qtbot.waitSignal(workspace.overlayDisplay.sigChanged, timeout=5000):
        overlay.renderer.settings.rgb.red.set(4)  # a custom composite
    workspace.overlayPanel.removeSelected()
    assert workspace.overlayDisplay.overlay is None


def test_an_overlays_display_comes_back_with_the_session(workspace, images, qtbot):
    cube = VardaRaster(
        ArrayDataSource(
            np.random.default_rng(1).random((30, 40, 6)).astype(np.float32),
            transform=Affine(20, 0, 200, 0, -20, -100),
            crs=CRS_,
        ),
        name="parameters",
    )
    images.append(cube)
    overlay = workspace.overlayPanel.add(cube)
    rgb = overlay.renderer.settings.rgb
    rgb.red.set(5)
    rgb.green.set(2)
    rgb.blue.set(0)
    described = describeWorkspace(workspace)
    assert described is not None
    restored = buildWorkspace(*described, images, workspace.sessionState())
    assert isinstance(restored, TerrainWorkspace)
    qtbot.addWidget(restored)
    back = restored.overlayPanel.overlays[0].renderer.settings.rgb
    assert (back.red.get(), back.green.get(), back.blue.get()) == (5, 2, 0)


def test_lines_drawn_in_another_workspace_are_draped_with_profiles(
    workspace, images, qtbot
):
    # lines drawn on the orthoimage (its pixels), as an image workspace has them
    lines = LineCollection()
    lines.addLine([[5, 5], [55, 35]], name="Across", color="#ff0000")
    lines.addLine([[30, 2], [30, 38]], name="Down", color="#00ff00")
    workspace.addWorkspaceLines(images[1], lines)
    assert [line.name for _, line in workspace.lineLayers] == ["Across", "Down"]
    assert [line.color for _, line in workspace.lineLayers] == ["#ff0000", "#00ff00"]
    across = workspace.lineLayers[0][1]
    np.testing.assert_allclose(across.paths[0], [(5 / 60, 35 / 40), (55 / 60, 5 / 40)])

    # their DEM profiles: the peak (-1300) is crossed by "Down" at x = 30
    profiles = workspace.profilePlot.profiles
    assert len(profiles) == 2
    assert max(np.nanmax(p.values) for p in profiles.values()) == pytest.approx(
        -1300, abs=5
    )

    # one line on its own, and the session keeps them without that workspace
    workspace.addWorkspaceLines(images[1], lines, [lines.lines()[1].fid])
    assert len(workspace.lineLayers) == 3
    described = describeWorkspace(workspace)
    assert described is not None
    restored = buildWorkspace(*described, images, workspace.sessionState())
    assert isinstance(restored, TerrainWorkspace)
    qtbot.addWidget(restored)
    assert [line.name for _, line in restored.lineLayers] == ["Across", "Down", "Down"]
    np.testing.assert_allclose(restored.lineLayers[0][1].paths[0], across.paths[0])


def test_the_add_lines_menu_offers_lines_open_workspaces_share(workspace, images):
    lines = LineCollection()
    lines.addLine([[5, 5], [55, 35]], name="Across")
    shareLines(lines, images[1])
    try:
        workspace._fillLinesMenu()
        menus = {a.text(): a.menu() for a in workspace.addLinesMenu.actions()}
        assert "From a File…" in menus
        sub = menus["ortho"]
        assert sub is not None
        assert [a.text() for a in sub.actions() if a.text()] == [
            "All Lines (1)",
            "Across",
        ]
    finally:
        unshareLines(lines)


def test_an_alignment_matched_on_another_image_moves_the_overlay(qtbot):
    # a parameter map with nothing to match on, from the same observation
    # (same georeferencing) as a simulated scene that matches the ortho
    rows, cols = 240, 320
    yy, xx = np.mgrid[0:rows, 0:cols]
    dem = -1500 + 100 * np.sin(xx / 30.0) * np.cos(yy / 25.0)
    images = ProjectImages([_image(dem[..., None], "dem")])
    scene = _offsetOverlay(images, (rows, cols))
    noise = np.random.default_rng(1).random((*scene.getBands([0]).shape[:2], 1))
    parameter = VardaRaster(
        ArrayDataSource(noise.astype(np.float32), transform=scene.transform, crs=CRS_),
        name="parameter",
    )
    images.append(parameter)
    workspace = TerrainWorkspace(images, images[0])
    qtbot.addWidget(workspace)
    panel = workspace.overlayPanel
    added = panel.add(parameter)
    assert panel.matchBox.itemText(0) == MATCH_ITSELF
    assert panel.matchBox.findText("crism") >= 0
    panel.matchBox.setCurrentText("crism")
    assert added.matchWith == "crism"
    workspace.autoAlign(0)
    qtbot.waitUntil(lambda: added.alignment.points > 0, timeout=30000)
    moved = added.alignment.correct(np.array([[3000.0, -2400.0]]))[0]
    assert moved == pytest.approx([3000 - 60, -2400 + 40], abs=4)

    # copy it to another overlay, and keep the choice in the session
    other = panel.add(scene)
    assert other.alignment.points == 0
    panel.list.setCurrentRow(0)
    panel.copyAlignment()
    panel.list.setCurrentRow(1)
    panel.pasteAlignment()
    assert other.alignment.state() == added.alignment.state()
    assert other.alignment is not added.alignment
    described = describeWorkspace(workspace)
    assert described is not None
    restored = buildWorkspace(*described, images, workspace.sessionState())
    assert isinstance(restored, TerrainWorkspace)
    qtbot.addWidget(restored)
    assert restored.overlayPanel.overlays[0].matchWith == "crism"
    _finishBackgroundWork(qtbot)


@pytest.fixture
def gappy(qtbot):
    """A workspace on a DEM with a void inside it."""
    rows, cols = 40, 60
    yy, xx = np.mgrid[0:rows, 0:cols]
    dem = -1500 + 200 * np.exp(-((xx - 30) ** 2 + (yy - 20) ** 2) / 100.0)
    dem[18:22, 28:33] = np.nan
    images = ProjectImages([_image(dem[..., None], "dem")])
    widget = TerrainWorkspace(images)
    qtbot.addWidget(widget)
    qtbot.waitUntil(lambda: widget.terrain is not None, timeout=10000)
    return widget


def test_filled_gaps_can_be_shown_and_are_recorded(gappy, qtbot):
    assert gappy.terrain.filled == 20
    assert not gappy.settings.showFilled
    gappy.showFilled.setChecked(True)
    assert gappy.settings.showFilled
    state = gappy.sessionState()
    assert state["settings"]["showFilled"] is True
    assert state["filled"] == {"count": 20, "method": FILL_METHOD}
    gappy.fillGaps.setChecked(False)
    qtbot.waitUntil(lambda: gappy.terrain.filled == 0, timeout=10000)
    assert gappy.sessionState()["filled"]["count"] == 0
    assert not gappy.showFilled.isEnabled()


def test_the_print_says_how_many_points_were_filled(gappy, qtbot, tmp_path):
    dialog = PrintDialog(gappy.terrain, 1.0)
    qtbot.addWidget(dialog)
    assert "20 points" in dialog.filledNote.text()
    assert dialog.filledNote.isVisibleTo(dialog)
    path = tmp_path / "dem.stl"
    gappy.savePrint(str(path), 120.0, 1.0, 3.0, 256)
    qtbot.waitUntil(lambda: "Saved a solid" in gappy.status.text(), timeout=10000)
    assert "20 points of gaps filled" in gappy.status.text()
    assert b"20 gap points filled" in path.read_bytes()[:80]
