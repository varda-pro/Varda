"""Drive the real Varda offscreen the way a first-time user would, and write
down what happened.

A ``QaRun`` is one persona's session: it builds the app as
``varda.startup.createVarda`` does, then acts only through what a user can
reach (menus, toolbar buttons, dialogs, clicks, keys and typing). Every step
is timed and logged; every problem becomes a ``Finding`` with a screenshot
instead of failing the test, added to ``findings.json`` as it is found.

Two things a user does outside Varda are stood in for:

- the operating system's file chooser (native on macOS and Windows, so not
  Varda's UI): ``chooseFiles`` queues the files the persona picks, and every
  file dialog Varda asks for is logged;
- the web browser: links Varda opens (Help > Documentation) are recorded,
  never opened.
"""

from __future__ import annotations

import contextlib
import json
import logging
import platform
import re
import sys
import time
import traceback
import warnings
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import TracebackType

from app_model import Application
from PyQt6.QtCore import QPoint, QStandardPaths, Qt, QTimer, QUrl
from PyQt6 import sip
from PyQt6.QtGui import QAction, QDesktopServices, QGuiApplication, QInputMethodEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QWidget,
)

from varda.app import VardaApplication
from varda.maingui import MainGUI
from varda.branding.loader import BusyDialog
from varda.startup import createVarda
from varda.version import vardaVersion

# Worst first: the order findings are ranked in
SEVERITIES = ("crash", "wrong result", "blocked", "confused", "annoyed")
SLOW_S = 2.0  # a wait longer than this needs visible feedback
POLL_S = 0.02
STEP_LIMIT_S = 240.0  # a step this long is stuck; its dialogs get closed


LEFT = Qt.MouseButton.LeftButton
NO_MODIFIER = Qt.KeyboardModifier.NoModifier


# PyQt6's stubs declare QTest's functions without @staticmethod, so type
# checkers reject every call; these wrappers are the only calls to them.
def mouseClick(
    widget: QWidget,
    modifier: Qt.KeyboardModifier = NO_MODIFIER,
    pos: QPoint | None = None,
    button: Qt.MouseButton = LEFT,
) -> None:
    """Click ``widget`` at ``pos`` (its center by default)."""
    QTest.mouseClick(widget, button, modifier, QPoint() if pos is None else pos)  # ty: ignore[no-matching-overload]


def mouseDoubleClick(widget: QWidget, pos: QPoint) -> None:
    QTest.mouseDClick(widget, LEFT, NO_MODIFIER, pos)  # ty: ignore[no-matching-overload]
    QTest.mouseRelease(widget, LEFT, NO_MODIFIER, pos)  # ty: ignore[no-matching-overload]


def keyClick(
    widget: QWidget, key: Qt.Key | str, modifier: Qt.KeyboardModifier = NO_MODIFIER
) -> None:
    QTest.keyClick(widget, key, modifier)  # ty: ignore[no-matching-overload]


# Error text from the libraries underneath, which a user can't act on
LIBRARY_JARGON = re.compile(
    r"(RasterioIOError|CPLE_|GDAL|TIFFRead|errno|Errno|Traceback|"
    r"not recognized as (?:being in )?a supported file format|NoneType|KeyError|"
    r"\bdtype\b)"
)


def osName() -> str:
    """``macOS``, ``Windows`` or ``Linux`` (what findings are filed under)."""
    return {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, "Linux")


@dataclass
class Finding:
    persona: str
    step: str
    severity: str
    message: str
    screenshot: str | None  # relative to the output folder
    os: str = field(default_factory=osName)
    reproduce: str = ""
    where: str = ""  # file:line when known


@dataclass
class MessageSeen:
    step: str
    title: str
    text: str
    icon: str
    screenshot: str | None


FINDINGS_FILE = "findings.json"
STEPS_FILE = "steps.jsonl"
CONSOLE_FILE = "console.txt"


def readFindings(outDir: Path) -> list[Finding]:
    path = outDir / FINDINGS_FILE
    if not path.exists():
        return []
    report = json.loads(path.read_text(encoding="utf-8"))
    return [Finding(**f) for f in report.get("findings", [])]


def writeFindings(outDir: Path, findings: list[Finding]) -> Path:
    """Write ``findings.json``: every finding, worst first, with counts per
    severity and per persona. Each persona runs in its own process and adds
    to the file, so it survives a crash."""
    findings = sorted(findings, key=lambda f: SEVERITIES.index(f.severity))
    personas = sorted({f.persona for f in findings})
    report = {
        "os": osName(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "varda": vardaVersion(),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "counts": _counts(findings),
        "byPersona": {
            p: _counts([f for f in findings if f.persona == p]) for p in personas
        },
        "findings": [asdict(f) for f in findings],
    }
    path = outDir / FINDINGS_FILE
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def addFinding(outDir: Path, found: Finding) -> None:
    writeFindings(outDir, [*readFindings(outDir), found])


def _counts(findings: list[Finding]) -> dict[str, int]:
    return {s: sum(f.severity == s for f in findings) for s in SEVERITIES}


class Network:
    """Every network connection Python makes, recorded through an audit
    hook (installed once; hooks can't be removed). ``offline`` refuses them,
    as on a plane."""

    hosts: list[str] = []
    offline = False
    _installed = False

    @classmethod
    def install(cls) -> None:
        if not cls._installed:
            sys.addaudithook(cls._hook)
            cls._installed = True

    @classmethod
    def _hook(cls, event: str, args: tuple[object, ...]) -> None:
        if event != "socket.connect":
            return
        address = args[1] if len(args) > 1 else None
        if not (isinstance(address, tuple) and address):
            return  # a local (Unix) socket
        host = str(address[0])
        if host in ("127.0.0.1", "::1", "localhost"):
            return
        cls.hosts.append(host)
        if cls.offline:
            raise OSError(f"QA: offline (refused a connection to {host})")


class _Records(logging.Handler):
    """Warnings and errors logged during a step."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.name}: {record.getMessage()}")


def plain(text: str) -> str:
    """Menu or button text as a user reads it: no mnemonic &, no ellipsis."""
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&").strip(" .…")


class QaRun:
    """One persona's session with the app."""

    def __init__(self, persona: str, outDir: Path) -> None:
        self.persona = persona
        self.outDir = outDir
        self.folder = outDir / persona
        self.folder.mkdir(parents=True, exist_ok=True)
        self.logPath = self.folder / STEPS_FILE
        self.app: VardaApplication | None = None
        self.stepName = "start"
        # numbered on from another test's screenshots of the same persona
        self._shots = len(list(self.folder.glob("*.png")))
        self.messages: list[MessageSeen] = []
        # Button text to press in the next message box (else Esc / its default)
        self.answers: list[str] = []
        self.fileAnswers: list[list[str]] = []  # one list per file dialog
        self.menuAnswers: list[str] = []  # items to pick in menus that pop up
        self.fileDialogs: list[str] = []  # what each file dialog asked for
        self.openedUrls: list[str] = []
        self.uncaught: list[str] = []
        self.statusMessages: list[str] = []  # what the status bar said
        self._records = _Records()
        self._watcher = QTimer()
        self._watcher.setInterval(50)
        self._watcher.timeout.connect(self._handleMessageBoxes)
        self._seenBoxes: set[int] = set()
        self._stepStarted = time.monotonic()
        self._restore: list[Callable[[], None]] = []

    # --- the app ----------------------------------------------------------

    def start(self) -> VardaApplication:
        """Launch Varda as ``varda`` does (minus the splash and the update
        check), with app data in Qt's test location so the user's plugins,
        libraries and sessions are neither used nor touched."""
        QStandardPaths.setTestModeEnabled(True)
        Network.install()
        self._patchOutsideWorld()
        with self.step("launch"):
            started = time.monotonic()
            qApp = QApplication.instance()
            assert isinstance(qApp, QApplication)
            self.app = createVarda(qApp, checkForUpdates=False)
            logging.getLogger().addHandler(self._records)
            statusBar = self.app.maingui.statusBar() if self.app.maingui else None
            if statusBar is not None:
                statusBar.messageChanged.connect(self._onStatusMessage)
            self._watcher.start()
            self.settle()
            seconds = time.monotonic() - started
            self.qaShot(self.mainWindow, "launch", f"started in {seconds:.1f} s")
        return self.app

    @property
    def mainWindow(self) -> MainGUI:
        assert self.app is not None and self.app.maingui is not None
        return self.app.maingui

    def close(self) -> None:
        """Quit the app and put back what ``start`` changed."""
        self._watcher.stop()
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                widget.reject()
        if self.app is not None and self.app.maingui is not None:
            self.app.maingui.close()
            self.app.maingui.deleteLater()
        self.settle()
        if self.app is not None:
            Application.destroy(self.app.name)
        logging.getLogger().removeHandler(self._records)
        for undo in reversed(self._restore):
            undo()
        QStandardPaths.setTestModeEnabled(False)
        Network.offline = False

    def _patchOutsideWorld(self) -> None:
        """Stand in for the OS file chooser and the browser, and catch
        exceptions that escape Qt's event handlers (PyQt would abort)."""
        originals = {
            name: getattr(QFileDialog, name)
            for name in (
                "getOpenFileName",
                "getOpenFileNames",
                "getSaveFileName",
                "getExistingDirectory",
            )
        }

        def chooser(name: str) -> Callable[..., object]:
            def choose(*args: object, **kwargs: object) -> object:
                caption = next((a for a in args if isinstance(a, str)), "")
                self.fileDialogs.append(f"{name}: {caption}")
                paths = self.fileAnswers.pop(0) if self.fileAnswers else []
                self.log("file dialog", dialog=name, caption=caption, chose=paths)
                first = paths[0] if paths else ""
                if name == "getExistingDirectory":
                    return first
                if name == "getOpenFileNames":
                    return (paths, "")
                return (first, "")

            return staticmethod(choose)

        for name in originals:
            setattr(QFileDialog, name, chooser(name))
        openUrl = QDesktopServices.openUrl

        def recordUrl(url: QUrl) -> bool:
            self.openedUrls.append(url.toString())
            self.log("link opened", url=url.toString())
            return True

        setattr(QDesktopServices, "openUrl", staticmethod(recordUrl))
        hook = sys.excepthook
        sys.excepthook = self._uncaught

        def undo() -> None:
            for name, original in originals.items():
                setattr(QFileDialog, name, original)
            setattr(QDesktopServices, "openUrl", openUrl)
            sys.excepthook = hook

        self._restore.append(undo)

    def _uncaught(
        self,
        kind: type[BaseException],
        error: BaseException,
        tb: TracebackType | None,
    ) -> None:
        text = "".join(traceback.format_exception(kind, error, tb))
        self.uncaught.append(text)
        where = _innermostVarda(tb)
        self.finding(
            "crash",
            f"Unhandled {kind.__name__} in an event handler: {error}",
            widget=QApplication.activeWindow() or self._mainOrNone(),
            where=where,
        )
        self.log("uncaught exception", traceback=text)

    def _onStatusMessage(self, message: str) -> None:
        if message:
            self.statusMessages.append(message)
            self.log("status bar", message=message)

    def _mainOrNone(self) -> QWidget | None:
        return self.app.maingui if self.app is not None else None

    # --- recording --------------------------------------------------------

    def log(self, event: str, **details: object) -> None:
        line: dict[str, object] = {
            "t": round(time.time(), 3),
            "step": self.stepName,
            "event": event,
        }
        line.update(details)
        with self.logPath.open("a", encoding="utf-8") as log:
            log.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")

    def qaShot(self, widget: QWidget | None, name: str, note: str = "") -> str | None:
        """Save ``widget`` as the next numbered PNG and log it; returns the
        path relative to the output folder."""
        if widget is None:
            return None
        self._shots += 1
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:60]
        path = self.folder / f"{self._shots:02}-{safe}.png"
        QApplication.processEvents()
        widget.grab().save(str(path))
        relative = path.relative_to(self.outDir).as_posix()
        self.log("screenshot", file=relative, note=note, window=widget.windowTitle())
        return relative

    def finding(
        self,
        severity: str,
        message: str,
        *,
        widget: QWidget | None = None,
        shot: str | None = None,
        reproduce: str = "",
        where: str = "",
    ) -> Finding:
        assert severity in SEVERITIES, severity
        if shot is None and widget is not None:
            shot = self.qaShot(widget, f"finding-{self.stepName}", message)
        found = Finding(
            persona=self.persona,
            step=self.stepName,
            severity=severity,
            message=message,
            screenshot=shot,
            reproduce=reproduce,
            where=where,
        )
        addFinding(self.outDir, found)
        self.log("finding", **asdict(found))
        return found

    @contextlib.contextmanager
    def step(self, name: str, reproduce: str = "") -> Iterator[None]:
        """Time a step and keep what it logged; an exception in it is a
        finding (a crash when it came from Varda), never a test failure."""
        self.stepName = name
        self._records.lines.clear()
        started = self._stepStarted = time.monotonic()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                yield
            except Exception as error:  # noqa: BLE001 (recorded, not hidden)
                tb = error.__traceback__
                where = _innermostVarda(tb)
                self.finding(
                    "crash" if where else "blocked",
                    f"{type(error).__name__}: {error}",
                    widget=QApplication.activeWindow() or self._mainOrNone(),
                    reproduce=reproduce,
                    where=where,
                )
                self.log("step error", traceback=traceback.format_exc())
        self.log(
            "step done",
            seconds=round(time.monotonic() - started, 2),
            warnings=[str(w.message) for w in caught][:20],
            logged=self._records.lines[:50],
        )

    # --- waiting ----------------------------------------------------------

    def settle(self, seconds: float = 0.2) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(POLL_S)

    def waitFor(
        self,
        condition: Callable[[], object],
        what: str,
        timeout: float = 60.0,
        severity: str | None = "blocked",
    ) -> bool:
        """Process events until ``condition()`` holds. A wait over
        ``SLOW_S`` with nothing on screen saying Varda is busy is an
        annoyance; a timeout is a finding of ``severity`` (None: just False)."""
        started = time.monotonic()
        feedback = False
        while not condition():
            QApplication.processEvents()
            feedback = feedback or _busyFeedbackShown()
            elapsed = time.monotonic() - started
            if elapsed > timeout:
                if severity:
                    self.finding(
                        severity,
                        f"Gave up after {timeout:.0f} s waiting for {what}",
                        widget=QApplication.activeWindow() or self._mainOrNone(),
                    )
                return False
            time.sleep(POLL_S)
        elapsed = time.monotonic() - started
        self.log("waited", what=what, seconds=round(elapsed, 2), feedback=feedback)
        if elapsed > SLOW_S and not feedback:
            self.finding(
                "annoyed",
                f"Waited {elapsed:.1f} s for {what} with nothing saying Varda was busy",
                widget=self._mainOrNone(),
            )
        return True

    # --- message boxes ----------------------------------------------------

    def _handleMessageBoxes(self) -> None:
        if time.monotonic() - self._stepStarted > STEP_LIMIT_S:
            self._unstick()
        popup = QApplication.activePopupWidget()
        if isinstance(popup, QMenu) and id(popup) not in self._seenBoxes:
            self._seenBoxes.add(id(popup))
            QTimer.singleShot(0, lambda menu=popup: self._choose(menu))
        for widget in QApplication.topLevelWidgets():
            if not (isinstance(widget, QMessageBox) and widget.isVisible()):
                continue
            if id(widget) in self._seenBoxes:
                continue
            self._seenBoxes.add(id(widget))
            text = "\n".join(t for t in (widget.text(), widget.informativeText()) if t)
            shot = self.qaShot(widget, f"message-{widget.windowTitle()}", text)
            seen = MessageSeen(
                step=self.stepName,
                title=widget.windowTitle(),
                text=text,
                icon=widget.icon().name,
                screenshot=shot,
            )
            self.messages.append(seen)
            self.log("message box", **asdict(seen))
            if "Traceback (most recent call last)" in text + widget.detailedText():
                self.finding(
                    "confused",
                    f"A message box shows a Python traceback: {widget.windowTitle()}",
                    shot=shot,
                )
            # Defer the answer: this may run inside the box's own exec()
            QTimer.singleShot(0, lambda box=widget: self._answer(box))

    def _unstick(self) -> None:
        """A step that runs far too long is stuck in a dialog or menu's own
        event loop: record it and close what's open, so the run goes on."""
        self._stepStarted = time.monotonic()
        stuck = QApplication.activeModalWidget() or QApplication.activePopupWidget()
        self.finding(
            "blocked",
            f"Stuck for over {STEP_LIMIT_S:.0f} s"
            + (f" in {type(stuck).__name__} {stuck.windowTitle()!r}" if stuck else ""),
            widget=stuck or self._mainOrNone(),
        )
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                widget.reject()
            elif isinstance(widget, QMenu) and widget.isVisible():
                widget.close()

    def _choose(self, menu: QMenu) -> None:
        """A menu that popped up on its own (e.g. a tool bar's » overflow):
        pick the queued item, or close it."""
        self.qaShot(menu, "popup-menu", "a menu popped up")
        wanted = self.menuAnswers.pop(0) if self.menuAnswers else None
        items = [a for a in menu.actions() if plain(a.text())]
        self.log("popup menu", items=[plain(a.text()) for a in items], chose=wanted)
        chosen = next((a for a in items if wanted and plain(a.text()) == wanted), None)
        menu.close()
        if chosen is not None:
            chosen.trigger()

    def _answer(self, box: QMessageBox) -> None:
        wanted = self.answers.pop(0) if self.answers else None
        buttons = box.buttons()
        chosen = next(
            (b for b in buttons if wanted and plain(b.text()) == plain(wanted)), None
        )
        chosen = chosen or box.escapeButton() or box.defaultButton()
        if chosen is not None:
            mouseClick(chosen)
        if box.isVisible():
            box.done(0)

    def messagesSince(self, count: int) -> list[MessageSeen]:
        return self.messages[count:]

    # --- acting -----------------------------------------------------------

    def chooseFiles(self, *paths: str | Path) -> None:
        """The files the persona will pick in the next file dialog (several
        at once where the dialog allows it; none is Cancel)."""
        self.fileAnswers.append([str(p) for p in paths])

    def menuAction(self, *path: str) -> QAction | None:
        """Find a menu item by its visible text, opening each menu on the way
        (so its items' enablement is current). ``None`` when it isn't there."""
        menuBar = self.app.maingui.menuBar() if self.app and self.app.maingui else None
        actions = menuBar.actions() if menuBar is not None else []
        found: QAction | None = None
        for depth, label in enumerate(path):
            found = next((a for a in actions if plain(a.text()) == plain(label)), None)
            if found is None:
                found = next(
                    (a for a in actions if plain(a.text()).startswith(plain(label))),
                    None,
                )
            if found is None:
                return None
            menu = found.menu()
            if depth < len(path) - 1:
                if menu is None:
                    return None
                menu.aboutToShow.emit()
                actions = menu.actions()
        return found

    def useMenu(self, *path: str, shot: bool = True) -> bool:
        """Choose ``File > Import Image(s)`` and the like. A missing or
        disabled item is a finding."""
        action = self.menuAction(*path)
        label = " > ".join(path)
        if action is None:
            self.finding("blocked", f"No menu item {label}", widget=self._mainOrNone())
            return False
        if shot:
            self._shootMenu(path)
        if not action.isEnabled():
            self.finding(
                "confused",
                f"{label} is disabled ({action.toolTip() or 'no reason given'})",
                widget=self._mainOrNone(),
            )
            return False
        self.log("menu", item=label)
        action.trigger()
        QApplication.processEvents()
        return True

    def _shootMenu(self, path: tuple[str, ...]) -> None:
        """Pop the item's menu up for a screenshot, as the user would see it."""
        parent = self.menuAction(*path[:-1]) if len(path) > 1 else None
        menu = parent.menu() if parent is not None else None
        if not isinstance(menu, QMenu):
            return
        self._seenBoxes.add(id(menu))  # not one to answer: shown for the shot
        menu.popup(self.mainWindow.mapToGlobal(QPoint(40, 30)))
        QApplication.processEvents()
        self.qaShot(menu, "menu-" + "-".join(path[-2:]), " > ".join(path))
        menu.hide()
        self._seenBoxes.discard(id(menu))

    def newDialog(
        self, kind: type[QDialog] = QDialog, timeout: float = 10.0
    ) -> QDialog | None:
        """The dialog that has just appeared (the newest visible one)."""
        found: list[QDialog] = []

        def look() -> bool:
            found[:] = [
                w
                for w in QApplication.topLevelWidgets()
                if isinstance(w, kind)
                and w.isVisible()
                and not isinstance(w, (QMessageBox, BusyDialog, QProgressDialog))
            ]
            return bool(found)

        if not self.waitFor(look, f"a {kind.__name__} to open", timeout, None):
            return None
        return found[-1]

    def click(self, widget: QWidget | None, label: str = "") -> bool:
        if widget is None or not widget.isVisible():
            self.finding("blocked", f"Can't find the {label or 'button'} to click")
            return False
        if not widget.isEnabled():
            self.finding(
                "confused",
                f"The {label or 'button'} is disabled",
                widget=widget.window(),
            )
            return False
        mouseClick(widget)
        QApplication.processEvents()
        return True

    @staticmethod
    def doubleClick(widget: QWidget, pos: QPoint) -> None:
        """Press, release, then the double-click and its release, as a mouse
        sends them (QTest.mouseDClick alone isn't a whole double-click)."""
        mouseClick(widget, pos=pos)
        if widget.isVisible():  # else the second click lands on what's behind
            mouseDoubleClick(widget, pos)
        QApplication.processEvents()

    def button(self, parent: QWidget, text: str) -> QAbstractButton | None:
        """A visible button whose text reads ``text``."""
        return next(
            (
                b
                for b in parent.findChildren(QAbstractButton)
                if b.isVisible() and plain(b.text()) == plain(text)
            ),
            None,
        )

    def typeInto(self, field: QLineEdit, text: str, replace: bool = True) -> None:
        """Click into the field, select what's there, and type."""
        mouseClick(field)
        if replace:
            field.selectAll()
            keyClick(field, Qt.Key.Key_Delete)
        typeText(field, text)
        if field.text() != text and replace:  # e.g. a validator refused some
            self.log("typing fell back to setText", wanted=text, got=field.text())
            field.setText(text)
        QApplication.processEvents()

    def chooseInCombo(self, combo: QComboBox, contains: str) -> bool:
        """Pick the first item whose text contains ``contains``, with the
        keyboard (arrow keys), as a user would."""
        target = next(
            (i for i in range(combo.count()) if contains in combo.itemText(i)), -1
        )
        if target < 0:
            return False
        combo.setFocus()
        key = Qt.Key.Key_Down if target > combo.currentIndex() else Qt.Key.Key_Up
        for _ in range(abs(target - combo.currentIndex())):
            keyClick(combo, key)
        if combo.currentIndex() != target:
            combo.setCurrentIndex(target)
        QApplication.processEvents()
        return True

    def resizeWindow(self, width: int, height: int) -> None:
        window = self.mainWindow
        window.showNormal()
        window.resize(width, height)
        self.settle()

    @staticmethod
    def clipboardText() -> str:
        clipboard = QGuiApplication.clipboard()
        return clipboard.text() if clipboard is not None else ""


def typeText(widget: QWidget, text: str) -> None:
    """Type ``text`` as a user would: keys for ASCII, and what an input
    method commits for everything else (QTest's key clicks abort on
    characters outside Latin-1)."""
    for character in text:
        if character.isascii():
            keyClick(widget, character)
        else:
            event = QInputMethodEvent()
            event.setCommitString(character)
            QApplication.sendEvent(widget, event)


def alive(widget: QWidget | None) -> bool:
    """False once Qt has deleted the widget (e.g. a dialog closed with
    WA_DeleteOnClose)."""
    return widget is not None and not sip.isdeleted(widget)


def _busyFeedbackShown() -> bool:
    if QApplication.overrideCursor() is not None:
        return True
    return any(
        isinstance(w, (BusyDialog, QProgressDialog)) and w.isVisible()
        for w in QApplication.topLevelWidgets()
    )


def _innermostVarda(tb: TracebackType | None) -> str:
    """``file:line`` of the innermost frame in Varda's own code, if any."""
    where = ""
    for frame, line in traceback.walk_tb(tb):
        name = frame.f_code.co_filename.replace("\\", "/")
        if "/varda/" in name and "/qa/" not in name:
            where = f"{name.rpartition('/varda/')[2]}:{line}"
    return f"src/varda/{where}" if where else ""
