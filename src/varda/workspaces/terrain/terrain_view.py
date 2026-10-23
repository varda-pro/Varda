"""The 3D view: Varda's three.js terrain page in a Qt WebEngine view.

The page is self-contained (``terrain.scene.pageHtml``): it is written to a
temporary file and loaded, and the same file is what Export Web Page saves.
Settings, the camera and stills go through small JavaScript calls; the page
reports that it is ready (or that WebGL is missing) on its console."""

from __future__ import annotations

import base64
import json
import logging
import tempfile
import weakref
from collections.abc import Callable
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QWidget

from varda.branding.loader import BusyOverlay

logger = logging.getLogger(__name__)

READY, ERROR, PICK = "varda:ready", "varda:error ", "varda:pick "


class _Page(QWebEnginePage):
    sigConsole = pyqtSignal(str)

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):  # noqa: N802 (Qt's name)
        if message.startswith("varda:"):
            self.sigConsole.emit(message)
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            logger.warning("3D view: %s (line %s)", message, lineNumber)


class TerrainView(QWebEngineView):
    """Every view must be deleted before the application ends: WebEngine
    (on Linux at least) crashes at exit if a page outlives its profile, so
    ``releaseAll`` runs at quit."""

    sigReady = pyqtSignal()
    sigFailed = pyqtSignal(str)
    sigPick = pyqtSignal(dict)  # a shape page's clicked plate and view ray
    _live: weakref.WeakSet[TerrainView] = weakref.WeakSet()

    @classmethod
    def releaseAll(cls) -> None:
        """Delete every view still alive (and its page, a child of it)."""
        for view in list(cls._live):
            if not sip.isdeleted(view):
                sip.delete(view)
        cls._live.clear()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        TerrainView._live.add(self)
        self._page = _Page(self)
        self._page.sigConsole.connect(self._onConsole)
        self.setPage(self._page)
        # removed with the view (TemporaryDirectory cleans up when collected)
        self._folder = tempfile.TemporaryDirectory(prefix="varda-3d-")
        self.pagePath = Path(self._folder.name) / "terrain.html"
        self.ready = False
        # Says what's being read, built or drawn until the page is ready
        self.busy = BusyOverlay(self)
        self.loadFinished.connect(self._onLoadFinished)

    def working(self, message: str) -> None:
        """Show that work for the view is under way (reading a model,
        building a surface); it ends when a page is ready or fails, or with
        ``idle``."""
        self.busy.start(message)

    def idle(self) -> None:
        self.busy.stop()

    def showPage(self, html: str) -> None:
        """Load a viewer page (``pageHtml``); sigReady once it has drawn."""
        self.ready = False
        self.busy.start("Drawing…")
        self.pagePath.write_text(html, encoding="utf-8")
        self.load(QUrl.fromLocalFile(str(self.pagePath)))

    def _onLoadFinished(self, ok: bool) -> None:
        if not ok:  # the page didn't load, so it will never say it's ready
            self.busy.stop()

    def _onConsole(self, message: str) -> None:
        if message == READY:
            self.ready = True
            self.busy.stop()
            self.sigReady.emit()
        elif message.startswith(ERROR):
            self.busy.stop()
            self.sigFailed.emit(message.removeprefix(ERROR))
        elif message.startswith(PICK):
            self.sigPick.emit(json.loads(message.removeprefix(PICK)))

    def _call(self, script: str, callback: Callable | None = None) -> None:
        if callback is None:
            self._page.runJavaScript(script)
        else:
            self._page.runJavaScript(script, callback)

    def setSettings(self, settings: dict) -> None:
        if self.ready:
            self._call(f"window.viewer.set({json.dumps(settings)})")

    def camera(self, callback: Callable[[dict | None], None]) -> None:
        """The camera (position, target, fov), or None before the page is ready."""
        if not self.ready:
            callback(None)
            return
        self._call("window.viewer.getCamera()", callback)

    def setCamera(self, view: dict) -> None:
        if self.ready:
            self._call(f"window.viewer.setCamera({json.dumps(view)})")

    def home(self) -> None:
        if self.ready:
            self._call("window.viewer.home()")

    def still(
        self,
        width: int,
        height: int,
        transparent: bool,
        callback: Callable[[bytes | None], None],
    ) -> None:
        """A PNG of the view at ``width`` × ``height`` (None if it failed)."""

        def done(url) -> None:
            if not isinstance(url, str) or "," not in url:
                callback(None)
                return
            callback(base64.b64decode(url.split(",", 1)[1]))

        if not self.ready:
            callback(None)
            return
        self._call(
            f"window.viewer.still({int(width)}, {int(height)}, {json.dumps(transparent)})",
            done,
        )

    def evaluate(self, script: str, callback: Callable) -> None:
        """Run JavaScript in the page; ``callback`` gets its result."""
        self._call(script, callback)
