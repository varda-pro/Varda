"""Reduced renders: an image (or a window of it) drawn at most so many pixels
on a side, as a 3D texture is. The whole image is never read for it; once
the image's bands are read (it is shown in a view), its picture is a slice of
them, stretched as the view is."""

import numpy as np

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering.image_renderer import ImageRenderer


class _CountingSource(ArrayDataSource):
    def __init__(self, data):
        super().__init__(data)
        self.fullReads = 0

    def getData(self, bandIndices=None, window=None, masked=True):
        if window is None:
            self.fullReads += 1
        return super().getData(bandIndices, window, masked)

    def getBands(self, bandIndices):
        self.fullReads += 1
        return super().getBands(bandIndices)


def _ramp(rows=300, cols=200):
    """Values rise from west to east: 0 at the first column, 1 at the last."""
    ramp = np.tile(np.linspace(0, 1, cols, dtype=np.float32), (rows, 1))
    return ramp[..., None]


def test_a_reduced_render_fits_and_never_reads_the_whole_image():
    source = _CountingSource(_ramp())
    renderer = ImageRenderer(VardaRaster(source, name="ramp"))
    rgba = renderer.renderReduced(64)
    assert rgba.shape == (60, 40, 4)  # every 5th pixel of 300 × 200
    assert source.fullReads == 0
    gray = rgba[0, :, 0].astype(int)
    assert gray[0] < 10 and gray[-1] > 245 and (np.diff(gray) >= 0).all()


def test_a_window_is_drawn_at_its_own_reduced_size():
    renderer = ImageRenderer(VardaRaster(_CountingSource(_ramp()), name="ramp"))
    rgba = renderer.renderReduced(50, (slice(100, 200), slice(150, 200)))
    assert rgba.shape == (50, 25, 4)  # 100 × 50, every 2nd pixel


def test_an_image_already_read_is_sliced_and_stretched_as_shown():
    source = _CountingSource(_ramp())
    renderer = ImageRenderer(VardaRaster(source, name="ramp"))
    full = renderer.render()
    reads = source.fullReads
    rgba = renderer.renderReduced(64, (slice(0, 300), slice(100, 200)))
    assert source.fullReads == reads
    np.testing.assert_array_equal(rgba, full[0:300:5, 100:200:5])


def test_small_images_are_rendered_whole():
    renderer = ImageRenderer(VardaRaster(_CountingSource(_ramp(30, 20)), name="r"))
    np.testing.assert_array_equal(renderer.renderReduced(64), renderer.render())
