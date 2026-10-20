"""Key names for UI text, as the platform shows them.

Qt's Control modifier is the ⌘ key on a Mac (the physical Control key there
is Qt's Meta, and Control-click is a right-click), so a hint that says "Ctrl"
sends a Mac user to the wrong key. Build hint text from these instead of
writing "Ctrl" or "Ctrl/Cmd".
"""

import sys

from PyQt6.QtCore import QObject, QTranslator
from PyQt6.QtGui import QKeySequence

_NATIVE = QKeySequence.SequenceFormat.NativeText


def shortcutText(sequence: str) -> str:
    """A shortcut written the portable way ("Ctrl+Z") as this platform shows
    it: "⌘Z" on a Mac, "Ctrl+Z" elsewhere."""
    return QKeySequence(sequence).toString(_NATIVE)


def controlKey() -> str:
    """The key Qt calls Control: "⌘" on a Mac, "Ctrl" elsewhere."""
    return shortcutText("Ctrl+A").removesuffix("A").rstrip("+")


def controlClick(action: str = "click") -> str:
    """A click (or "drag", "right-click"…) with that key held: "⌘-click" on a
    Mac, as Apple writes it, "Ctrl+click" elsewhere."""
    joiner = "-" if sys.platform == "darwin" else "+"
    return f"{controlKey()}{joiner}{action}"


class NativeKeyTranslator(QTranslator):
    """Shows this platform's key name in libraries' UI text, which says
    "Ctrl" everywhere (e.g. the docking library's "Pin Active Tab (Press Ctrl
    to Pin Group)", where the key is ⌘ on a Mac). Installed on the
    application; Qt's own key names (context QShortcut), text without "Ctrl",
    and every text off a Mac are left to other translators."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = controlKey()

    # PyQt's stub says str, but only None tells Qt "no translation here"
    def translate(  # ty: ignore[invalid-method-override]
        self,
        context: str | None,
        sourceText: str | None,
        disambiguation: str | None = None,
        n: int = -1,
    ) -> str | None:
        # Only sentences: Qt names the keys themselves through this system
        # too (context QShortcut), and a shortcut must keep parsing
        if (
            self._key == "Ctrl"
            or context == "QShortcut"
            or not sourceText
            or "Ctrl" not in sourceText
            or " " not in sourceText
        ):
            return None  # not ours ("" would be a translation: empty text)
        return sourceText.replace("Ctrl", self._key)

    def isEmpty(self) -> bool:
        return False
