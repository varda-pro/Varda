"""Opening several images: they join the project in the order chosen, a busy
indicator shows at once, and failures read plainly (release QA #20, #25, #4)."""

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from varda.branding.loader import BusyDialog
from varda.image_loading import image_loading_service as service
from varda.image_loading.image_loading_service import ImageLoadingService


@pytest.fixture
def jobs(monkeypatch):
    """Background loads, held until the test finishes them in any order."""
    pending = []

    def fake(work, done, failed, description=""):
        pending.append((work, done, failed))

    monkeypatch.setattr(service, "runInBackground", fake)
    return pending


def test_images_join_in_the_order_chosen_not_the_order_they_finish(qtbot, jobs):
    added = []
    ImageLoadingService.load_images(
        file_paths=["a.img", "b.img", "c.img"], on_success_callback=added.append
    )
    assert len(jobs) == 3
    jobs[2][1]("C")
    jobs[0][1]("A")
    assert added == ["A"]  # C waits for B
    jobs[1][1]("B")
    assert added == ["A", "B", "C"]


def test_a_failure_does_not_hold_up_the_rest(qtbot, jobs):
    added, failures = [], []
    ImageLoadingService.load_images(
        file_paths=["a.img", "missing.img", "c.img"],
        on_success_callback=added.append,
        on_failure_callback=failures.append,
    )
    jobs[2][1]("C")
    jobs[1][2](FileNotFoundError("missing.img: No such file or directory"))
    jobs[0][1]("A")
    assert added == ["A", "C"]
    assert len(failures) == 1 and "doesn't exist" in failures[0]


def test_a_busy_indicator_shows_while_loading_and_closes_after(qtbot, jobs):
    ImageLoadingService.load_images(file_paths=["a.img"], on_success_callback=print)
    qtbot.waitUntil(lambda: any(w.isVisible() for w in _busyDialogs()), timeout=2000)
    jobs[0][1]("A")
    assert not any(w.isVisible() for w in _busyDialogs())


def test_a_failure_shows_a_plain_message_with_the_details(qtbot, jobs, monkeypatch):
    shown = []
    monkeypatch.setattr(
        ImageLoadingService,
        "_show_error",
        staticmethod(lambda error: shown.append(error)),
    )
    ImageLoadingService.load_images(file_paths=["/nowhere/gone.tif"])
    raw = "/nowhere/gone.tif: No such file or directory"
    jobs[0][2](Exception(raw))
    (error,) = shown
    assert "doesn't exist" in error.message and error.details == raw


def test_the_error_box_keeps_the_library_text_under_details(qtbot, monkeypatch):
    boxes = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: boxes.append(self) or 0)
    ImageLoadingService._show_error(
        service.explainOpenError("/nowhere/x.tif", Exception("raw text"))
    )
    (box,) = boxes
    assert "doesn't exist" in box.text()
    assert box.detailedText() == "raw text"


def _busyDialogs():
    return [w for w in QApplication.topLevelWidgets() if isinstance(w, BusyDialog)]
