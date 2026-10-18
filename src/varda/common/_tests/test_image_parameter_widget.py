"""The image dropdown must show the parameter's value and follow later changes."""

import pytest

from varda.common.grid import Excluded, ImageChoices
from varda.common.parameter import ImageParameter
from varda.utilities.debug import generate_random_image
from varda.common.parameter_widgets import ImageParameterWidget


@pytest.fixture
def images():
    return [generate_random_image((10, 10, 10)) for _ in range(3)]


@pytest.fixture
def param(images):
    param = ImageParameter("Image")
    param.setProvider(lambda: images)
    return param


def test_widget_shows_the_parameters_current_value(qapp, param, images):
    param.set(images[1])
    widget = ImageParameterWidget(param)
    assert widget.comboBox.currentIndex() == 1


def test_widget_follows_a_value_set_after_creation(qapp, param, images):
    widget = ImageParameterWidget(param)
    param.set(images[2])
    assert widget.comboBox.currentIndex() == 2


def test_choosing_in_the_widget_sets_the_parameter(qapp, param, images):
    widget = ImageParameterWidget(param)
    widget.comboBox.setCurrentIndex(2)
    assert param.get() is images[2]


def test_syncing_the_widget_does_not_re_emit_the_parameter(qapp, param, images):
    widget = ImageParameterWidget(param)
    changes = []
    param.sigParameterChanged.connect(lambda value: changes.append(value))
    param.set(images[1])
    assert changes == [images[1]]  # once from set(), not again from the combo
    assert widget.comboBox.currentIndex() == 1


def test_an_optional_image_starts_as_none_and_can_go_back_to_it(qapp, images):
    param = ImageParameter("Weights", optional=True, noneLabel="None (unweighted)")
    param.setProvider(lambda: images)
    assert param.get() is None  # not the first image
    widget = ImageParameterWidget(param)
    assert widget.comboBox.itemText(0) == "None (unweighted)"
    widget.comboBox.setCurrentIndex(2)
    assert param.get() is images[1]
    widget.comboBox.setCurrentIndex(0)
    assert param.get() is None
    assert param.clone().optional


def test_an_optional_image_works_with_nothing_to_choose(qapp):
    param = ImageParameter("Weights", optional=True)
    param.setProvider(lambda: [])
    widget = ImageParameterWidget(param)  # no images is fine when optional
    assert widget.comboBox.count() == 1 and param.get() is None


def test_images_left_out_are_explained_under_the_dropdown(qapp, images):
    param = ImageParameter("Mask")
    choices = ImageChoices(
        images[:1], [Excluded(images[2], "doesn't overlap this image")]
    )
    param.setChoices(lambda: choices)
    widget = ImageParameterWidget(param)
    assert widget.comboBox.count() == 1
    assert widget.notListed.text() == (
        f"Not listed: {images[2].name} (doesn't overlap this image)"
    )
    assert not widget.notListed.isHidden() and widget.notListed.wordWrap()
    assert param.clone().explain() == widget.notListed.text()


def test_nothing_left_out_shows_no_note(qapp, param):
    assert ImageParameterWidget(param).notListed.isHidden()
