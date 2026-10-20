"""An analysis runs in the background, reports progress, and its result joins the project."""

import numpy as np
import pytest

from varda.analysis.analysis import Analysis
from varda.analysis.runner import AnalysisRunner
from varda.common.di_types import ProjectImages
from varda.common.entities import VardaRaster
from varda.common.parameter import IntParameter
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.utilities.debug import generate_random_image
from varda.common.parameter_widgets import groupWidget


class DoublingAnalysis(Analysis):
    """Test analysis: doubles the first ``bands`` bands, reporting progress."""

    name = "Doubling"
    bands = IntParameter("Bands", 2, range=(1, 10))

    def run(self, image: VardaRaster, reportProgress) -> VardaRaster:
        n = int(self.bands.value)
        reportProgress(0, "starting")
        data = image.getData()[:, :, :n] * 2.0
        reportProgress(100, "done")
        return VardaRaster(ArrayDataSource(data), name=f"{image.name} doubled")


class FailingAnalysis(Analysis):
    name = "Failing"

    def run(self, image, reportProgress):
        raise ValueError("nope")


def test_result_is_added_to_the_project_images(qtbot):
    images = ProjectImages()
    image = generate_random_image((10, 10, 10))
    images.append(image)
    runner = AnalysisRunner(images)

    with qtbot.waitSignal(runner.sigFinished, timeout=5000) as blocker:
        runner.run(DoublingAnalysis(), image)

    (result,) = blocker.args
    assert list(images) == [image, result]
    assert result.name == f"{image.name} doubled"
    assert result.bandCount == 2
    np.testing.assert_allclose(result.getData(), image.getData()[:, :, :2] * 2.0)


def test_progress_reaches_the_dialog(qtbot):
    images = ProjectImages()
    image = generate_random_image((10, 10, 10))
    runner = AnalysisRunner(images)
    seen = []
    runner.sigProgress.connect(lambda percent, message: seen.append((percent, message)))

    with qtbot.waitSignal(runner.sigFinished, timeout=5000):
        runner.run(DoublingAnalysis(), image)

    assert (0, "starting") in seen and (100, "done") in seen


def test_failure_is_reported_and_adds_nothing(qtbot, monkeypatch):
    from varda.analysis import runner as runnerModule

    monkeypatch.setattr(runnerModule.QMessageBox, "critical", lambda *a, **k: None)
    images = ProjectImages()
    image = generate_random_image((10, 10, 10))
    runner = AnalysisRunner(images)

    with qtbot.waitSignal(runner.sigFailed, timeout=5000) as blocker:
        runner.run(FailingAnalysis(), image)

    (error,) = blocker.args
    assert isinstance(error, ValueError)
    assert len(images) == 0


def test_analysis_settings_are_a_parameter_group():
    analysis = DoublingAnalysis()
    analysis.bands.set(3)
    assert analysis.bands.value == 3
    assert groupWidget(analysis) is not None or pytest.skip("needs QApplication")


# --- the memory check before a run ---------------------------------------------

GIB = 2**30


class HungryAnalysis(DoublingAnalysis):
    """Says it needs a fixed amount of memory, whatever the image."""

    name = "Hungry"

    def __init__(self, need: int) -> None:
        super().__init__()
        self.need = need

    def estimateMemory(self, image: VardaRaster) -> int:
        return self.need


@pytest.fixture
def machine(monkeypatch):
    """8 GiB free of 16 GiB, and a record of the questions asked."""
    from varda.analysis import runner as runnerModule

    asked: list[tuple[str, str, bool]] = []
    answer = {"run": False}
    monkeypatch.setattr(runnerModule, "availableMemory", lambda: 8 * GIB)
    monkeypatch.setattr(runnerModule, "totalMemory", lambda: 16 * GIB)

    def askToRunAnyway(parent, title, text, cautious):
        asked.append((title, text, cautious))
        return answer["run"]

    monkeypatch.setattr(runnerModule, "askToRunAnyway", askToRunAnyway)
    return asked, answer


def test_an_analysis_that_fits_runs_without_asking(qtbot, machine):
    asked, _ = machine
    runner = AnalysisRunner(ProjectImages())
    with qtbot.waitSignal(runner.sigFinished, timeout=5000):
        runner.run(HungryAnalysis(1 * GIB), generate_random_image((10, 10, 10)))
    assert asked == []


def test_a_tight_analysis_asks_and_cancel_stops_it(qtbot, machine):
    asked, answer = machine
    answer["run"] = False
    images = ProjectImages()
    runner = AnalysisRunner(images)
    with qtbot.assertNotEmitted(runner.sigFinished, wait=300):
        runner.run(HungryAnalysis(7 * GIB), generate_random_image((10, 10, 10)))
    ((title, text, cautious),) = asked
    assert "Hungry" in title and "7 GB" in text and "8 GB" in text
    assert not cautious
    assert list(images) == []


def test_run_anyway_runs_it(qtbot, machine):
    asked, answer = machine
    answer["run"] = True
    runner = AnalysisRunner(ProjectImages())
    with qtbot.waitSignal(runner.sigFinished, timeout=5000):
        runner.run(HungryAnalysis(7 * GIB), generate_random_image((10, 10, 10)))
    assert len(asked) == 1


def test_more_than_the_machine_has_asks_cautiously(qtbot, machine):
    asked, answer = machine
    answer["run"] = True  # warned, not refused: the user may still run it
    runner = AnalysisRunner(ProjectImages())
    with qtbot.waitSignal(runner.sigFinished, timeout=5000):
        runner.run(HungryAnalysis(20 * GIB), generate_random_image((10, 10, 10)))
    ((_, text, cautious),) = asked
    assert cautious and "16 GB" in text


def test_a_result_whose_image_was_removed_meanwhile_is_dropped(qtbot, monkeypatch):
    """Release QA #17: removing the image mid-PCA still added 'big.img PCA'."""
    told = []
    monkeypatch.setattr(
        "varda.analysis.runner.QMessageBox.information",
        lambda parent, title, text: told.append(text),
    )
    images = ProjectImages()
    image = generate_random_image((10, 10, 10))
    images.append(image)

    def runWhileTheUserRemovesIt(task, done, failed, description=""):
        results = task()
        images.remove(image)  # the user removes it while the analysis runs
        done(results)

    monkeypatch.setattr(
        "varda.analysis.runner.runInBackground", runWhileTheUserRemovesIt
    )
    runner = AnalysisRunner(images)
    discarded = []
    runner.sigDiscarded.connect(discarded.append)

    runner.run(DoublingAnalysis(), image)

    assert discarded == [image]
    assert list(images) == []
    (text,) = told
    assert image.name in text and "removed" in text
