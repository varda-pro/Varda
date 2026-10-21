"""
Entrypoint for Varda.

Shows the splash before anything else: importing the application takes a few
seconds, so this module imports only what the splash needs, and ``main``
imports the rest once the splash is on screen.
"""

import ctypes
import importlib
import os
import sys
import threading

from PyQt6.QtCore import QCoreApplication, QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

import varda
from varda.branding.splash import createSplash
from varda.utilities.resources import resource_path

if sys.platform == "win32":
    # this "registers" varda as its own unique application, which lets it use its own icon for the taskbar, instead of the generic python icon.
    # Kept free of the version, so taskbar pins survive updates.
    appid = "varda"
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)

ICON_PATH = resource_path("resources/logo.svg")
SELF_TEST_FLAG = "--self-test"
# Kindle a Feature checks a plugin by running Varda in this mode (no window);
# the same as varda.kindling.checks.CHECK_FLAG
CHECK_PLUGIN_FLAG = "--check-plugin"
CRISM_FLAG = "--crism"
# Where the self-test writes its report when the app has no console (the
# windowed Windows app); the release build reads it from here
SELF_TEST_REPORT_ENV = "VARDA_SELF_TEST_REPORT"
SELF_TEST_REPORT_DEFAULT = "varda-self-test.txt"
FRAME_SECONDS = 0.016

# Libraries without Qt that most of the import time goes to. They load on a
# worker thread while this thread keeps the splash animating.
PRELOAD = (
    "numpy",
    "scipy.ndimage",
    "scipy.signal",
    "scipy.stats",
    "pandas",
    "geopandas",
    "shapely",
    "pyproj",
    "rasterio",
    "numba",
    "spectral",
    "torch",  # only when installed in Varda's own environment (the ml extra)
)


def _preload(q_app: QApplication) -> None:
    """Import ``PRELOAD`` in the background, repainting the splash meanwhile."""

    def load() -> None:
        for name in PRELOAD:
            try:
                importlib.import_module(name)
            except ImportError:
                pass  # the real import later reports it properly

    worker = threading.Thread(target=load, name="preload", daemon=True)
    worker.start()
    while worker.is_alive():
        q_app.processEvents()
        worker.join(FRAME_SECONDS)


def _consoleForSelfTest() -> None:
    """A windowed Windows app has no stdout or stderr, so the self-test's
    report would vanish; send it to a file instead."""
    if SELF_TEST_FLAG in sys.argv and sys.stdout is None:
        report = open(  # noqa: SIM115 (open for the life of the process)
            os.environ.get(SELF_TEST_REPORT_ENV, SELF_TEST_REPORT_DEFAULT),
            "w",
            encoding="utf-8",
        )
        sys.stdout = sys.stderr = report


def main():
    if CHECK_PLUGIN_FLAG in sys.argv:
        # Imported only in this mode: an ordinary start shouldn't wait for it
        from varda.kindling.check_worker import main as checkPlugin

        sys.exit(checkPlugin(sys.argv[sys.argv.index(CHECK_PLUGIN_FLAG) + 1 :]))
    if CRISM_FLAG in sys.argv:
        # Imported only in this mode, like --check-plugin: no Qt, no splash wait
        from varda.crism.cli import (
            consoleForWindowedApp,
            crismMain,
            isWindowedWindowsApp,
        )

        consoleForWindowedApp(windowed=isWindowedWindowsApp())
        sys.exit(crismMain(sys.argv[sys.argv.index(CRISM_FLAG) + 1 :]))
    _consoleForSelfTest()
    # The 3D terrain view (Qt WebEngine) shares OpenGL with the rest of Qt
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    if sys.platform.startswith("linux"):
        # Chromium's sandbox needs user namespaces, which recent Ubuntu blocks;
        # the view only ever loads Varda's own local page, never remote content
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    q_app = QApplication(sys.argv)
    q_app.setApplicationName("Varda")
    appIcon = QIcon()
    appIcon.addFile(ICON_PATH, QSize(16, 16))
    q_app.setWindowIcon(appIcon)
    splash = createSplash()
    splash.show()
    q_app.processEvents()
    _preload(q_app)

    # Imported here, not at the top, so the splash is up while they load
    from varda.startup import createVarda

    # The self-test stays offline: no update check
    app = createVarda(q_app, checkForUpdates=SELF_TEST_FLAG not in sys.argv)
    if SELF_TEST_FLAG in sys.argv:
        from varda.self_test import runSelfTest

        sys.exit(runSelfTest(app))
    assert app.maingui is not None  # createVarda builds and shows it
    splash.finishWhenDone(app.maingui)
    varda.log.info("starting the GUI event loop...")
    exitCode = q_app.exec()
    varda.log.info("Application exiting, performing cleanup...")
    sys.exit(exitCode)


if __name__ == "__main__":
    main()
