"""Helpers for locating bundled resource files.

In development, resources are read relative to the project root. When the app is
frozen by PyInstaller, bundled data is extracted to ``sys._MEIPASS`` (onefile) or
placed next to the executable (onedir); either way ``sys._MEIPASS`` points at it.
"""

import sys
from pathlib import Path


def resource_path(relative: str) -> str:
    """Return an absolute path to a bundled resource.

    ``relative`` is given relative to the project root (e.g. ``"resources/logo.svg"``).
    """
    base = getattr(sys, "_MEIPASS", None)
    if base is not None:
        return str(Path(base) / relative)
    # Project root is three parents up from this file: utilities -> varda -> src -> root.
    return str(Path(__file__).resolve().parents[3] / relative)


def bundledIcon(name: str) -> str:
    """An app_model icon for ``resources/icons/<name>.svg``, as a file URL:
    icons are bundled, never fetched (see resources/icons/README.md)."""
    return Path(resource_path(f"resources/icons/{name}.svg")).as_uri()
