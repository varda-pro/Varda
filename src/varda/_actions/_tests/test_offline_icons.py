"""Menu icons are bundled files, never fetched (release QA #9: every launch
called api.iconify.design, a Cloudflare address, even offline)."""

from pathlib import Path

from PyQt6.QtCore import QUrl

from varda._actions import ALL_ACTIONS


def _iconNames(action) -> list[str]:
    icon = action.icon
    if icon is None:
        return []
    return [name for name in (icon.dark, icon.light) if name]


def test_every_action_icon_is_a_bundled_file():
    names = [name for action in ALL_ACTIONS for name in _iconNames(action)]
    assert names, "no icons found: the test looks in the wrong place"
    for name in names:
        assert name.startswith("file://"), name
        path = Path(QUrl(name).toLocalFile())
        assert path.is_file(), path
        assert "resources/icons" in path.as_posix()
