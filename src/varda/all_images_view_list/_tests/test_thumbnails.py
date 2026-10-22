"""The image list draws each image from a small cached thumbnail."""

import numpy as np
import pytest

from varda.all_images_view_list import imageview_list
from varda.all_images_view_list.imageview_list import ImageListWidget
from varda.all_images_view_list.thumbnail import THUMBNAIL_SIZE, thumbnailRgba
from varda.common.entities import VardaRaster
from varda.common.observable_list import ObservableList
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering.image_renderer import ImageRenderer
from varda.utilities.debug import generate_random_image


@pytest.mark.parametrize("seed", range(0, 200, 25))
def test_a_small_image_looks_as_a_new_view_shows_it(seed):
    """Exactly, level for level: in float32 a value on a rounding edge landed a
    level away from the view's, in about 1 image in 400 (24% once a stretch
    returned float32). 25 seeded images per case, so it never flakes."""
    for offset in range(25):
        np.random.seed(seed + offset)
        image = generate_random_image((20, 30, 10))
        np.testing.assert_array_equal(
            thumbnailRgba(image),
            ImageRenderer(image).render(),
            err_msg=f"seed {seed + offset}",
        )


def test_a_large_image_is_read_from_a_sample_of_rows_never_whole(monkeypatch):
    image = VardaRaster(ArrayDataSource(np.random.rand(1500, 500, 3)))
    windows = []
    getData = image.getData

    def recording(bandIndices=None, window=None, masked=True):
        windows.append(window)
        return getData(bandIndices, window, masked)

    monkeypatch.setattr(image, "getData", recording)
    monkeypatch.setattr(image, "getBands", None)  # a full-band read would fail

    rgba = thumbnailRgba(image)

    assert max(rgba.shape[:2]) <= THUMBNAIL_SIZE
    assert len(windows) <= THUMBNAIL_SIZE
    assert all(window is not None and window[2] == 1 for window in windows)


def _shownList(qtbot, monkeypatch, *images):
    builds = []
    monkeypatch.setattr(
        imageview_list,
        "thumbnailRgba",
        lambda image, size: builds.append(image) or thumbnailRgba(image, size),
    )
    widget = ImageListWidget(ObservableList(images))
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    return widget, builds


def test_each_thumbnail_is_made_once_not_on_every_paint(qtbot, monkeypatch):
    first = generate_random_image((20, 20, 10))
    second = generate_random_image((20, 20, 10))
    widget, builds = _shownList(qtbot, monkeypatch, first, second)

    for _ in range(3):
        widget.viewport().grab()
    widget.item(0).setSelected(True)
    widget.viewport().grab()
    widget.imageList.append(generate_random_image((20, 20, 10)))
    widget.viewport().grab()

    assert sorted(map(id, builds[:2])) == sorted(map(id, (first, second)))
    assert len(builds) == 3


def test_an_edited_image_gets_a_new_thumbnail_and_name(qtbot, monkeypatch):
    image = generate_random_image((20, 20, 10))
    widget, builds = _shownList(qtbot, monkeypatch, image)
    before = widget.thumbnail(image).toImage()

    # a no-data value hides pixels of the band shown
    shown = int(image.defaultBands[0])
    value = float(image.getData([shown])[5, 5, 0])
    image.setMetadataOverrides({"nodata": value}, name="edited")
    widget.viewport().grab()

    assert len(builds) == 2
    assert widget.thumbnail(image).toImage() != before
    assert widget.item(0).text() == "edited"


def test_an_unreadable_image_gets_a_placeholder_made_once(qtbot, monkeypatch):
    image = generate_random_image((20, 20, 10))
    calls = []

    def failing(image, size):
        calls.append(image)
        raise OSError("unreadable")

    monkeypatch.setattr(imageview_list, "thumbnailRgba", failing)
    widget = ImageListWidget(ObservableList([image]))
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    for _ in range(3):
        widget.viewport().grab()

    assert len(calls) == 1
    assert not widget.thumbnail(image).isNull()


def test_thumbnails_sit_at_the_left_edge_and_long_names_are_in_the_tooltip(
    qtbot, monkeypatch
):
    short = generate_random_image((20, 20, 10))
    long = generate_random_image((20, 20, 10))
    long._name = "02_01_FRT000107A7_07_IF166J_MTR3_HDR_IOVF_replaced_subset_ratio.img"
    widget, _ = _shownList(qtbot, monkeypatch, short, long)

    lefts = []
    for row in range(widget.count()):
        shot = widget.viewport().grab(widget.visualItemRect(widget.item(row)))
        pixels = shot.toImage()
        background = pixels.pixelColor(0, 0)
        columns = [
            x
            for x in range(pixels.width())
            if any(pixels.pixelColor(x, y) != background for y in range(10, 40))
        ]
        lefts.append(columns[0])

    # both thumbnails start at the same place near the left edge, however
    # long the name under them
    assert lefts[0] == lefts[1] <= imageview_list.LEFT_MARGIN + 2
    assert widget.item(1).toolTip() == long.name
