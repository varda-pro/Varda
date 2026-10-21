"""The image list can be closed and brought back (View > Image List), and the
menu item's check mark follows it."""

import pytest
from app_model import Application

from varda.common.di_types import ProjectImages
from varda.context_keys import IMAGE_LIST_VISIBLE
from varda.geo._tests.fixtures import writeWgs84
from varda.image_loading.image_loading_service import loadRaster
from varda.maingui import MainGUI
from varda.utilities.debug import generate_random_image

APP_NAME = "varda-test-image-list"


@pytest.fixture
def gui(qtbot):
    app = Application(APP_NAME)
    app.images = ProjectImages()  # what VardaApplication provides
    gui = MainGUI(app)
    qtbot.addWidget(gui)
    gui.show()
    yield gui
    Application.destroy(APP_NAME)


def test_the_image_list_starts_open(gui):
    assert gui.imageListDock.isVisible()
    assert gui.app.context[IMAGE_LIST_VISIBLE] is True


def test_closing_the_image_list_unchecks_the_menu_item(gui):
    gui.imageListDock.close()  # its title bar's close button
    assert gui.app.context[IMAGE_LIST_VISIBLE] is False


def test_view_image_list_brings_it_back(gui):
    gui.imageListDock.close()
    gui.toggleImageList()
    assert gui.imageListDock.isVisible()
    assert gui.app.context[IMAGE_LIST_VISIBLE] is True
    gui.toggleImageList()
    assert not gui.imageListDock.isVisible()


def test_a_crs_problem_is_announced_once_per_image(gui, tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    messages = []
    gui.statusBar().messageChanged.connect(messages.append)
    gui.app.images.append(image)
    gui.app.images.refresh()  # a second change: not announced again
    shown = [m for m in messages if m]
    assert shown == [
        "scene.tif — Its CRS says Earth; read as Mars (from its label). "
        "Details in Image Properties."
    ]


def test_an_image_without_problems_is_not_announced(gui):
    messages = []
    gui.statusBar().messageChanged.connect(messages.append)
    gui.app.images.append(generate_random_image((20, 20, 10)))
    assert [m for m in messages if m] == []


def test_several_problems_at_once_are_one_message(gui, tmp_path):
    first = loadRaster(str(writeWgs84(tmp_path)))
    (tmp_path / "b").mkdir()
    second = loadRaster(str(writeWgs84(tmp_path / "b")))
    messages = []
    gui.statusBar().messageChanged.connect(messages.append)
    gui.app.images.extend([first, second])
    assert [m for m in messages if m] == [
        "2 images have coordinate-system problems (a dot on their thumbnails). "
        "Details in Image Properties."
    ]


def test_the_image_list_shows_its_title_and_names_in_the_smallest_window(gui, qtbot):
    """Release QA 0.12.0: at the default window size the dock was ~90 px
    wide, titled "Ima…", its names cut to "qa_cube.im…"."""
    gui.resize(0, 0)  # clamped to the window's minimum
    qtbot.wait(50)
    metrics = gui.imageListDock.fontMetrics()
    width = gui.imageListDock.width()
    assert width >= metrics.horizontalAdvance("Image List") + 50  # + its buttons
    assert width >= 16 * metrics.averageCharWidth()
