"""Key names in UI text follow the platform: ⌘ on a Mac, Ctrl elsewhere."""

import sys

import pytest

from varda.common.keys import controlClick, controlKey, shortcutText

MAC = sys.platform == "darwin"


@pytest.mark.skipif(not MAC, reason="macOS key names")
def test_a_mac_names_the_command_key():
    assert controlKey() == "⌘"
    assert controlClick() == "⌘-click"
    assert controlClick("drag") == "⌘-drag"
    assert shortcutText("Ctrl+Z") == "⌘Z"


@pytest.mark.skipif(MAC, reason="Windows and Linux key names")
def test_elsewhere_the_control_key_is_ctrl():
    assert controlKey() == "Ctrl"
    assert controlClick() == "Ctrl+click"
    assert controlClick("right-click") == "Ctrl+right-click"
    assert shortcutText("Ctrl+Z") == "Ctrl+Z"
