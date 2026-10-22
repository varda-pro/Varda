import json
import sys
from datetime import datetime, timedelta

import pytest
from app_model import Application
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtTest import QTest

from varda import start_panel
from varda.common.di_types import ProjectImages
from varda.common.keys import shortcutText
from varda.common.layout_audit import assertNothingClipped
from varda.maingui import MainGUI
from varda.start_panel import (
    DRAG_DEPTH,
    HEADLINE,
    INK,
    PAPER,
    BalancedLabel,
    Colors,
    autosaveSummary,
    keyNames,
)

APP_NAME = "test_start_panel"


@pytest.fixture
def noAutosave(tmp_path, monkeypatch):
    monkeypatch.setattr(
        start_panel, "defaultAutosavePath", lambda: tmp_path / "none.varda"
    )


@pytest.fixture
def gui(qtbot, noAutosave):
    app = Application(APP_NAME)
    app.images = ProjectImages()
    gui = MainGUI(app)
    qtbot.addWidget(gui)
    yield gui
    Application.destroy(APP_NAME)


def _session(path, images):
    path.write_text(json.dumps({"format": "varda-session", "images": images}))
    return path


def test_the_autosave_says_what_it_holds_and_when(tmp_path):
    path = _session(tmp_path / "autosave.varda", ["a.img", "b.img", "c.img", "d.img"])
    saved = datetime.fromtimestamp(path.stat().st_mtime)
    clock = saved.strftime("%I:%M %p").lstrip("0")
    assert autosaveSummary(path, saved) == f"4 images, saved today at {clock}."
    assert autosaveSummary(path, saved + timedelta(days=1)) == (
        f"4 images, saved yesterday at {clock}."
    )
    later = autosaveSummary(path, saved + timedelta(days=9))
    assert later == f"4 images, saved on {saved.strftime('%b')} {saved.day}."
    one = _session(tmp_path / "one.varda", ["a.img"])
    assert autosaveSummary(one, saved).startswith("1 image, ")


def test_no_autosave_means_nothing_to_restore(tmp_path):
    assert autosaveSummary(tmp_path / "missing.varda") is None
    broken = tmp_path / "broken.varda"
    broken.write_text("{ not json")
    assert autosaveSummary(broken).startswith("Your last work, saved ")


def test_restore_shows_only_when_there_is_a_session(gui, tmp_path, monkeypatch):
    panel = gui.startPanel
    gui.show()
    assert not panel.restoreRow.isVisible()
    path = _session(tmp_path / "autosave.varda", ["a.img", "b.img"])
    monkeypatch.setattr(start_panel, "defaultAutosavePath", lambda: path)
    panel.refresh()
    assert panel.restoreRow.isVisible()
    assert panel.restoreRow.accessibleDescription().startswith("2 images, saved today")


@pytest.mark.parametrize(
    ("name", "command"),
    [
        ("importButton", "varda.file.import_image"),
        ("sessionButton", "varda.file.open_session"),
        ("restoreButton", "varda.file.restore_last_session"),
        ("findDataButton", "varda.data.find_data"),
    ],
)
def test_each_row_runs_its_command(gui, monkeypatch, name, command):
    ran = []
    monkeypatch.setattr(gui.app.commands, "execute_command", ran.append)
    row = getattr(
        gui.startPanel,
        {
            "importButton": "importRow",
            "sessionButton": "sessionRow",
            "restoreButton": "restoreRow",
            "findDataButton": "findRow",
        }[name],
    )
    assert row.objectName() == name
    row.click()
    assert ran == [command]


def test_return_on_a_focused_row_runs_it(gui, qtbot, monkeypatch):
    ran = []
    monkeypatch.setattr(gui.app.commands, "execute_command", ran.append)
    gui.show()
    qtbot.waitExposed(gui)
    row = gui.startPanel.sessionRow
    row.setFocus()
    QTest.keyClick(row, Qt.Key.Key_Return)
    assert ran == ["varda.file.open_session"]


def test_shortcuts_are_shown_as_this_platform_writes_them():
    for sequence in ("Ctrl+O", "Ctrl+Shift+O"):
        joiner = "" if sys.platform == "darwin" else "+"
        assert joiner.join(keyNames(sequence)) == shortcutText(sequence)
        assert len(keyNames(sequence)) == sequence.count("+") + 1


def test_a_drag_says_what_a_drop_will_do(gui, qtbot):
    panel = gui.startPanel
    panel.setDropCount(3)
    assert panel.headline.text() == "Drop to open 3 images"
    qtbot.waitUntil(lambda: abs(panel._depth - DRAG_DEPTH) < 1e-6, timeout=2000)
    panel.setDropCount(1)
    assert panel.headline.text() == "Drop to open the image"
    panel.setDropCount(0)
    assert panel.headline.text() == HEADLINE
    qtbot.waitUntil(lambda: abs(panel._depth - 1.0) < 1e-6, timeout=2000)


def test_the_ground_follows_the_system_palette(qtbot, noAutosave):
    for windowColor, ground in (("#2b2b2b", INK), ("#ececec", PAPER)):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(windowColor))
        assert Colors.forPalette(palette).ground == QColor(ground)


@pytest.mark.parametrize("size", [(1280, 800), (1024, 700), "smallest"])
def test_nothing_is_cut_off_at_laptop_sizes(gui, qtbot, tmp_path, monkeypatch, size):
    """Release check: at the window's smallest width (about 800 px on a Mac)
    the import row's detail lost its last line ("HDF5 and more.")."""
    path = _session(tmp_path / "autosave.varda", ["a.img"])
    monkeypatch.setattr(start_panel, "defaultAutosavePath", lambda: path)
    if size == "smallest":
        size = (gui.minimumSizeHint().width(), 800)
    gui.resize(*size)
    gui.show()
    qtbot.waitExposed(gui)
    gui.startPanel.refresh()
    qtbot.wait(50)
    assertNothingClipped(gui.startPanel)
    band = gui.startPanel.spectrumRect()
    if not band.isEmpty():  # the list stays clear of the spectrum
        column = gui.startPanel.column.geometry()
        assert column.bottom() < band.top()


def test_a_short_window_drops_the_spectrum_rather_than_crowd_the_list(gui, qtbot):
    gui.show()
    qtbot.waitExposed(gui)
    panel = gui.startPanel
    panel.resize(700, panel.column.sizeHint().height() + 60)
    assert panel.spectrumRect().isEmpty()


def test_balanced_wrapping_strands_no_word(qtbot):
    label = BalancedLabel(
        "Then double-click the image in the Image List, turn on Pixel Select, and see"
    )
    qtbot.addWidget(label)
    width = 420
    balanced = label.balancedWidth(width)
    assert balanced <= width
    assert label._height(balanced) == label._height(width)  # as many lines
    assert label.balancedWidth(2000) == 2000  # one line: left alone
