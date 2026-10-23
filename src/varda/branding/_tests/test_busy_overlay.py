"""The busy overlay: the indicator and a message over one view while work
for it runs in the background; quick work shows nothing."""

from PyQt6.QtWidgets import QWidget

from varda.branding.loader import BusyOverlay


def _target(qtbot) -> QWidget:
    target = QWidget()
    qtbot.addWidget(target)
    target.resize(400, 300)
    target.show()
    qtbot.waitExposed(target)
    return target


def test_it_appears_after_its_delay_over_the_whole_view(qtbot):
    target = _target(qtbot)
    overlay = BusyOverlay(target, delayMs=50)
    overlay.start("Reading bennu.obj…")
    assert overlay.isBusy() and not overlay.isVisible()  # not yet
    qtbot.waitUntil(overlay.isVisible, timeout=1000)
    assert overlay.geometry() == target.rect()
    assert overlay.message() == "Reading bennu.obj…"
    assert overlay.spinner.isAnimating()
    overlay.start("Drawing…")  # the work moves on
    assert overlay.message() == "Drawing…"
    target.resize(500, 350)
    qtbot.waitUntil(lambda: overlay.geometry() == target.rect(), timeout=1000)
    overlay.stop()
    assert not overlay.isVisible() and not overlay.isBusy()


def test_quick_work_shows_nothing(qtbot):
    target = _target(qtbot)
    overlay = BusyOverlay(target, delayMs=200)
    overlay.start("Reading…")
    overlay.stop()
    qtbot.wait(300)
    assert not overlay.isVisible()
