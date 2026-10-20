"""Key names in UI text follow the platform: ⌘ on a Mac, Ctrl elsewhere."""

import sys

import pytest

from varda.common.keys import (
    NativeKeyTranslator,
    controlClick,
    controlKey,
    shortcutText,
)

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


ADS_PIN = "Pin Active Tab (Press Ctrl to Pin Group)"


@pytest.mark.skipif(not MAC, reason="macOS key names")
def test_library_text_names_the_command_key_on_a_mac(qapp):
    """Release QA #5: the docking library's pin button said Ctrl on a Mac."""
    translator = NativeKeyTranslator(qapp)
    assert translator.translate("ads::CDockAreaTitleBar", ADS_PIN) == (
        "Pin Active Tab (Press ⌘ to Pin Group)"
    )
    assert translator.translate("ads::CDockAreaTitleBar", "Close Tab") is None


@pytest.mark.skipif(MAC, reason="Windows and Linux key names")
def test_library_text_is_left_alone_elsewhere(qapp):
    assert (
        NativeKeyTranslator(qapp).translate("ads::CDockAreaTitleBar", ADS_PIN) is None
    )


def test_shortcut_names_survive_the_translator(qapp):
    """Qt names keys through the same translation system (context QShortcut):
    translating "Ctrl" there emptied every shortcut's text."""
    translator = NativeKeyTranslator(qapp)
    qapp.installTranslator(translator)
    try:
        assert shortcutText("Ctrl+O") == ("⌘O" if MAC else "Ctrl+O")
        assert shortcutText("Ctrl+Shift+O") == ("⇧⌘O" if MAC else "Ctrl+Shift+O")
    finally:
        qapp.removeTranslator(translator)
