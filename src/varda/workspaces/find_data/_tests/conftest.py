import os

import pytest
from PyQt6.QtCore import QSettings

from varda.catalogs import basemaps
from varda.catalogs.catalog import CatalogError
from varda.workspaces.find_data import imagery

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolatedSettings(tmp_path):
    """QSettings() writes to a throwaway file, never the user's settings."""
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(tmp_path / "settings"),
    )
    yield
    QSettings.setDefaultFormat(QSettings.Format.NativeFormat)


@pytest.fixture(autouse=True)
def offlineTiles(tmp_path, monkeypatch):
    """No test reaches Trek: tile fetches fail as if offline (a test may
    patch ``basemaps.fetchBytes`` itself; this works because
    ``fetchTileBytes`` looks it up when called), and tiles are cached in a
    throwaway folder."""

    def offline(url: str, *args, **kwargs) -> bytes:
        raise CatalogError("trek.nasa.gov couldn't be reached.")

    monkeypatch.setattr(basemaps, "fetchBytes", offline)
    monkeypatch.setattr(imagery, "cacheFolder", lambda: tmp_path / "tiles")
