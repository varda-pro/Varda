"""The image list draws each image from a small cached thumbnail."""

import numpy as np

from varda.all_images_view_list import imageview_list
from varda.all_images_view_list.imageview_list import ImageListWidget
from varda.all_images_view_list.thumbnail import THUMBNAIL_SIZE, thumbnailRgba
from varda.common.entities import VardaRaster
from varda.common.observable_list import ObservableList
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering.image_renderer import ImageRenderer
from varda.utilities.debug import generate_random_image


def test_a_small_image_looks_as_a_new_view_shows_it():
    image = generate_random_image((20, 30, 10))
    np.testing.assert_array_equal(thumbnailRgba(image), ImageRenderer(image).render())


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
        lambda image: builds.append(image) or thumbnailRgba(image),
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
