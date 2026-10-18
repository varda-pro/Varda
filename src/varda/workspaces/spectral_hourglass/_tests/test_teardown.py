"""A closed hourglass workspace's widgets are never called by its model or
by a step still running in the background."""

import threading

from PyQt6 import sip

from synthetic_scene import syntheticScene
from varda.workspaces.spectral_hourglass.hourglass_model import Step, StepState
from varda.workspaces.spectral_hourglass.hourglass_workspace import (
    SpectralHourglassWorkspace,
)
from varda.workspaces.spectral_hourglass.task_runner import TaskRunner


def test_a_deleted_pipeline_panel_ignores_the_model(qtbot):
    image, _ = syntheticScene()
    workspace = SpectralHourglassWorkspace(image, [image])
    qtbot.addWidget(workspace)
    model = workspace.model
    sip.delete(workspace.pipeline)
    model._setState(Step.PREPARE, StepState.DONE)  # raised RuntimeError


def test_a_step_finishing_after_its_runner_is_deleted_is_dropped(qtbot):
    runner = TaskRunner()
    release = threading.Event()
    finished = threading.Event()
    done = []

    def task(progress):
        release.wait(5)
        progress(50, "halfway")
        finished.set()
        return 1

    with qtbot.captureExceptions() as exceptions:
        runner.run("Slow", task, done.append)
        sip.delete(runner)
        release.set()
        assert finished.wait(5)
        qtbot.wait(200)  # the result and progress reach the GUI thread
    assert exceptions == []  # was RuntimeError: the runner had been deleted
    assert done == []
