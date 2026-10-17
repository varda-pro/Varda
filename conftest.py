"""Repository-wide pytest fixtures.

The per-package ``_tests/conftest.py`` files put Qt in offscreen mode; this one
keeps the GUI tests from segfaulting and away from the real keychain.
"""

import gc

# Before any test module imports rasterio or pyproj: varda drops a foreign
# PROJ_DATA/GDAL_DATA (e.g. from an active conda base), as the app does at launch.
import varda  # noqa: F401
import keyring
import keyring.errors
import pytest

# Before pytest-qt makes the QApplication: the 3D view (Qt WebEngine) needs
# to be imported first (and on Linux, without Chromium's sandbox, as the app)
import os
import sys

if sys.platform.startswith("linux"):
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
import PyQt6.QtWebEngineWidgets  # noqa: E402, F401

from varda.workspaces.terrain.terrain_view import TerrainView  # noqa: E402
from varda.spectral_libraries.store import setDefaultStore  # noqa: E402
from keyring.backend import KeyringBackend
from PyQt6.QtWidgets import QApplication


class MemoryKeyring(KeyringBackend):
    """Keeps passwords in a dict, so tests never touch the real keychain."""

    priority = 1  # type: ignore[assignment]

    def __init__(self):
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.passwords.get((service, username))

    def set_password(self, service, username, password):
        self.passwords[(service, username)] = password

    def delete_password(self, service, username):
        if self.passwords.pop((service, username), None) is None:
            raise keyring.errors.PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def memoryKeyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def _freshLibraryStore():
    """Each test starts with only the bundled spectral libraries, in memory:
    nothing a test creates reaches the real app-data folder or the next test."""
    setDefaultStore(None)
    yield
    setDefaultStore(None)


@pytest.fixture(autouse=True, scope="session")
def _releaseWebViews():
    """Delete the 3D views before Python exits: WebEngine crashes at exit if
    a page outlives its profile."""
    yield
    TerrainView.releaseAll()


@pytest.fixture(autouse=True, scope="session")
def _freezeImportTimeHeap():
    """Park everything allocated before the first test (imported modules, Qt,
    numpy, torch) in the permanent generation so the per-test collections
    below only walk what the tests themselves create."""
    gc.collect()
    gc.freeze()
    yield
    gc.unfreeze()


@pytest.fixture(autouse=True)
def _collectQtGarbageAtASafePoint():
    """Free leaked Qt objects after each test, not in the middle of event
    dispatch.

    Widgets that a test leaves behind in reference cycles are only freed by the
    cyclic garbage collector, which otherwise runs whenever allocation counts
    happen to cross a threshold — including while Qt is delivering posted
    events to those very objects (a use-after-free segfault whose timing
    shifts with every unrelated change in what gets imported). Collecting here,
    then draining the event queue, makes each test's leftovers die at a known
    safe point.
    """
    yield
    gc.collect()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
        app.processEvents()
