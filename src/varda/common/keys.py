"""Key names for UI text, as the platform shows them.

Qt's Control modifier is the ⌘ key on a Mac (the physical Control key there
is Qt's Meta, and Control-click is a right-click), so a hint that says "Ctrl"
sends a Mac user to the wrong key. Build hint text from these instead of
writing "Ctrl" or "Ctrl/Cmd".
"""

import sys

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
