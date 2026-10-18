"""Runs one hourglass step at a time in the background, reporting progress."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6 import sip
from PyQt6.QtCore import QObject, pyqtSignal

from varda.utilities.background import onMainThread, runInBackground

ProgressCallback = Callable[[int, str], None]
Task = Callable[[ProgressCallback], object]


class TaskRunner(QObject):
    """One task at a time. ``synchronous`` runs tasks inline (for tests)."""

    sigBusyChanged = pyqtSignal(bool)
    sigProgress = pyqtSignal(int, str)  # percent, "<label>: <message>"
    sigFailed = pyqtSignal(str, object)  # label, exception

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.synchronous = False
        self.busy = False

    def run(self, label: str, task: Task, onDone: Callable[[object], None]) -> None:
        if self.busy:
            return
        self.busy = True
        self.sigBusyChanged.emit(True)

        def progress(percent: int, message: str) -> None:
            if not sip.isdeleted(self):  # the workspace may have been closed
                self.sigProgress.emit(percent, f"{label}: {message}")

        def finished(result: object) -> None:
            self.busy = False
            self.sigBusyChanged.emit(False)
            onDone(result)

        def failed(error: Exception) -> None:
            self.busy = False
            self.sigBusyChanged.emit(False)
            self.sigFailed.emit(label, error)

        if self.synchronous:
            try:
                result = task(progress)
            except Exception as error:
                failed(error)
                return
            finished(result)
            return
        runInBackground(
            lambda: task(onMainThread(progress)),
            finished,
            failed,
            description=f"Spectral hourglass step {label}",
            owner=self,  # a closed workspace's step is dropped when it ends
        )
