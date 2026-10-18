"""Parameter widgets come from a registry and stop listening once deleted."""

import gc

from PyQt6 import sip
from PyQt6.QtWidgets import QWidget

from varda.common.parameter import FloatParameter, IntParameter, ParameterGroup
from varda.common.parameter_widgets import (
    FloatParameterWidget,
    connectWhileAlive,
    groupWidget,
    parameterWidget,
)


class _Settings(ParameterGroup):
    count = IntParameter("Count", 3, range=(0, 10))
    scale = FloatParameter("Scale", 1.0)


def test_the_registry_picks_the_widget_for_the_parameter_type(qapp):
    assert isinstance(
        parameterWidget(FloatParameter("Scale", 1.0)), FloatParameterWidget
    )


def test_a_group_widget_edits_the_groups_parameters(qapp):
    settings = _Settings()
    widget = groupWidget(settings)
    widget.findChild(FloatParameterWidget).spinBox.setValue(2.5)
    assert settings.scale.get() == 2.5


def test_a_widget_follows_its_parameter(qapp):
    param = IntParameter("Count", 3, range=(0, 10))
    widget = parameterWidget(param)
    param.set(7)
    assert widget.spinBox.value() == 7


def test_a_deleted_widget_is_never_called(qapp):
    param = IntParameter("Count", 3)
    calls = []

    class Receiver(QWidget):
        def onChanged(self, value):
            calls.append(value)

    receiver = Receiver()
    connectWhileAlive(param.sigParameterChanged, receiver.onChanged, receiver)
    param.set(4)
    sip.delete(receiver)  # the C++ widget is gone; the Python wrapper is not
    param.set(5)
    assert calls == [4]
    assert len(param.sigParameterChanged) == 0  # the connection removed itself


def test_the_connection_does_not_keep_its_widget_alive(qapp):
    param = IntParameter("Count", 3)
    widget = parameterWidget(param)
    del widget
    gc.collect()
    param.set(5)  # nothing left to call, and no error
    assert len(param.sigParameterChanged) == 0


def test_a_deleted_widgets_connection_goes_without_waiting_for_the_collector(qapp):
    """The connection must not be a reference cycle (a slot holding the signal
    that holds it): that left garbage only the cyclic collector frees, and a
    collection during Qt's event dispatch segfaulted the suite."""
    param = IntParameter("Count", 3)

    class Receiver(QWidget):
        def onChanged(self, value):
            pass

    receiver = Receiver()
    connectWhileAlive(param.sigParameterChanged, receiver.onChanged, receiver)
    sip.delete(receiver)
    del receiver
    assert len(param.sigParameterChanged) == 0  # no emit or gc.collect() needed
