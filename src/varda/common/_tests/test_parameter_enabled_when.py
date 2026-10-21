"""A parameter that only matters once another one is set is shown disabled
until then (release QA 0.12.0 #10: "Weight Image Holds" stayed editable
with no weight image)."""

from PyQt6.QtWidgets import QLabel

from varda.common.parameter import (
    BoolParameter,
    FloatParameter,
    ParameterGroup,
    StringParameter,
)
from varda.common.parameter_widgets import (
    FloatParameterWidget,
    StringParameterWidget,
    groupWidget,
)


class _Settings(ParameterGroup):
    smooth = BoolParameter("Smooth", False)
    width = FloatParameter("Width", 1.0).enabledWhen("smooth")


class _Nested(ParameterGroup):
    inner = _Settings()
    note = StringParameter("Note", "")


def _label(form, text: str) -> QLabel:
    return next(label for label in form.findChildren(QLabel) if label.text() == text)


def test_a_dependent_parameter_is_disabled_until_its_controller_is_set(qapp):
    settings = _Settings()
    form = groupWidget(settings)
    widget = form.findChild(FloatParameterWidget)
    assert not widget.isEnabled() and not _label(form, "Width").isEnabled()
    settings.smooth.set(True)
    assert widget.isEnabled() and _label(form, "Width").isEnabled()
    settings.smooth.set(False)
    assert not widget.isEnabled()


def test_every_instance_keeps_the_rule(qapp):
    first, second = _Settings(), _Settings()
    assert second.width.enabledBy == "smooth"
    second.smooth.set(True)
    assert not groupWidget(first).findChild(FloatParameterWidget).isEnabled()
    assert groupWidget(second).findChild(FloatParameterWidget).isEnabled()


def test_a_nested_group_keeps_its_rule_and_others_stay_enabled(qapp):
    settings = _Nested()
    form = groupWidget(settings)
    assert not form.findChild(FloatParameterWidget).isEnabled()
    assert form.findChild(StringParameterWidget).isEnabled()
    settings.inner.smooth.set(True)
    assert form.findChild(FloatParameterWidget).isEnabled()
