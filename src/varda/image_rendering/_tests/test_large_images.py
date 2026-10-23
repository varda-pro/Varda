"""Large images are drawn a piece at a time: each view renders the part on
screen at about screen resolution, stretched with statistics from a regular
subsample, so opening a HiRISE-sized image takes seconds and memory near
its own size. Smaller images are drawn as before."""

import numpy as np
import pyqtgraph as pg
import pytest
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtWidgets import QApplication

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering import image_renderer
from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.raster_view.image_region_item import VardaImageItem
from varda.workspaces.general_image_analysis import general_image_analysis as gia


@pytest.fixture
def large(monkeypatch):
    """Images over 40,000 pixels count as large; statistics read ~2,500."""
    monkeypatch.setattr(image_renderer, "LARGE_IMAGE_PIXELS", 40_000)
    monkeypatch.setattr(image_renderer, "SAMPLE_PIXELS", 2_500)


def _image(rows=400, cols=300, bands=3) -> VardaRaster:
    rng = np.random.default_rng(7)
    cube = rng.integers(0, 255, (rows, cols, bands)).astype(np.uint8)
    return VardaRaster(ArrayDataSource(cube), name="big")


def test_statistics_come_from_a_regular_subsample(large):
    renderer = ImageRenderer(_image())
    assert renderer.isLarge()
    sample = renderer.getSampleData()
    step = ImageRenderer.sampleStep(renderer.getRawBandData())
    assert step == 7  # ceil(sqrt(120,000 / 2,500))
    np.testing.assert_array_equal(sample, renderer.getRawBandData()[::7, ::7])


def test_small_images_keep_every_pixel_for_statistics():
    renderer = ImageRenderer(_image(100, 100))
    assert not renderer.isLarge()
    assert renderer.getSampleData() is renderer.getRawBandData()


def test_a_piece_is_the_whole_render_cut_down(large):
    renderer = ImageRenderer(_image())
    whole = renderer.render()
    piece = renderer.renderPiece(slice(50, 250, 3), slice(20, 200, 3))
    np.testing.assert_array_equal(piece, whole[50:250:3, 20:200:3])


def test_the_stretch_limits_dont_draw_the_whole_image(large):
    renderer = ImageRenderer(_image())
    assert renderer.getMinMaxValues() is not None
    assert renderer.cachedRender is None


def _shown(qtbot, image, viewSize=(200, 150)):
    widget = pg.GraphicsLayoutWidget()
    qtbot.addWidget(widget)
    viewBox = widget.addViewBox(lockAspect=True)
    item = VardaImageItem(ImageRenderer(image))
    viewBox.addItem(item)
    widget.resize(*viewSize)
    widget.show()
    qtbot.waitExposed(widget)
    viewBox.autoRange(padding=0)
    qtbot.wait(20)
    return widget, viewBox, item


def test_a_view_draws_about_its_own_resolution_and_keeps_coordinates(qtbot, large):
    image = _image(2000, 1500)
    _widget, viewBox, item = _shown(qtbot, image)
    # the item spans the whole image in its coordinates...
    assert item.boundingRect() == QRectF(0, 0, 1500, 2000)
    # ...but draws a piece about the view's size (with its margin)
    assert item.image.shape[0] * item.image.shape[1] < 0.1 * 2000 * 1500
    np.testing.assert_allclose(item.localToImage((750.0, 1000.0)), (750.0, 1000.0))

    # zoomed in: every pixel of what's on screen
    viewBox.setRange(QRectF(700, 900, 40, 30), padding=0)
    qtbot.wait(20)
    assert item._pieceStep == 1
    assert item._pieceRect is not None and item._pieceRect.contains(
        QRectF(700, 900, 40, 30)
    )


def test_a_small_image_is_drawn_whole(qtbot):
    _widget, _, item = _shown(qtbot, _image(300, 200))
    assert item._pieceRect is None
    assert item.image.shape[:2] == (300, 200)


class _Box:
    """An ROI's bounds, as the region controllers give them."""

    def __init__(self, x0, y0, x1, y1):
        self.bounds = (x0, y0, x1, y1)

    def getBounds(self):
        return self.bounds


def test_a_region_is_the_image_s_own_pixels(qtbot):
    image = _image(300, 200)
    renderer = ImageRenderer(image)
    item = VardaImageItem(renderer)
    item.setROI(_Box(20.4, 30.6, 80.2, 90.9))
    np.testing.assert_array_equal(item.image, renderer.render()[30:91, 20:81])
    np.testing.assert_allclose(item.localToImage((0.0, 0.0)), [(20.0, 30.0)])
    np.testing.assert_allclose(item.imageToLocal((25.0, 35.0)), [(5.0, 5.0)])


def test_resizing_the_color_bar_doesnt_redraw_the_image(qtbot):
    renderer = ImageRenderer(_image(100, 100, 1))
    panel = renderer.getSettingsPanel()
    qtbot.addWidget(panel)
    panel.show()
    redraws = []
    renderer.sigShouldRefresh.connect(lambda: redraws.append(1))
    for width in (250, 400, 320):
        panel.resize(width, 400)
        qtbot.wait(10)
    assert redraws == []
    gradient = next(
        w for w in panel.findChildren(pg.GradientWidget)
    )  # a real edit still does
    gradient.loadPreset("viridis")
    assert redraws


def test_a_workspace_is_built_under_the_wait_cursor(qtbot, monkeypatch):
    seen = []
    real = gia.GeneralImageAnalysisWorkflow._initComponents

    def recording(self):
        cursor = QApplication.overrideCursor()
        seen.append(None if cursor is None else cursor.shape())
        real(self)

    monkeypatch.setattr(gia.GeneralImageAnalysisWorkflow, "_initComponents", recording)
    image = _image(60, 50)
    config = gia.GeneralImageAnalysisConfig([image])
    config.image.set(image)
    workspace = gia.GeneralImageAnalysisWorkflow(config)
    qtbot.addWidget(workspace)
    assert seen == [Qt.CursorShape.WaitCursor]
    assert QApplication.overrideCursor() is None  # put back
