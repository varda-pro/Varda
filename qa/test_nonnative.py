"""The non-native English speaker: follows only what the UI literally says,
with no outside knowledge of jargon. Flags unexplained abbreviations, idioms,
messages that don't say what to do next, and one thing called by several
names."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import pytest
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QAbstractButton, QLabel, QMenu, QWidget

from qa import journeys
from qa.data import QaData
from qa.harness import LIBRARY_JARGON, QaRun, plain
from varda.analysis.dialog import RunAnalysisDialog

pytestmark = [pytest.mark.qa, pytest.mark.qt_no_exception_capture]

ABBREVIATION = re.compile(r"\b[A-Z][A-Z0-9/]*[A-Z0-9](?:s)?\b")
# Known to nearly everyone, or file formats named as such
KNOWN = {
    "OK", "CSV", "PDF", "PNG", "JPEG", "JPG", "URL", "AI", "ID", "3D", "2D",
    "RGB", "HTML", "JSON", "TIFF", "USB", "GB", "MB", "PC", "API",
}  # fmt: skip
IDIOMS = (
    "nudge", "blandness", "tweak", "on the fly", "out of the box",
    "under the hood", "kindle", "ballpark", "rule of thumb", "sanity check",
    "a breeze", "hop ", "grab",
)  # fmt: skip
SYNONYMS = (("image", "raster", "cube", "scene"), ("band", "channel", "layer"))
NEXT_STEP = re.compile(
    r"\b(try|check|make sure|choose|select|convert|install|see|enter|"
    r"download|instead)\b",
    re.I,
)


def menuTexts(run: QaRun) -> list[tuple[str, str]]:
    """(where, text) for every menu item's title and tool tip."""
    menuBar = run.app.maingui.menuBar() if run.app and run.app.maingui else None
    texts: list[tuple[str, str]] = []

    def walk(actions: list[QAction], path: str) -> None:
        for action in actions:
            title = plain(action.text())
            if not title:
                continue
            where = f"{path} > {title}" if path else title
            texts.append((where, title))
            if action.toolTip() and plain(action.toolTip()) != title:
                texts.append((f"{where} (tool tip)", action.toolTip()))
            menu = action.menu()
            if isinstance(menu, QMenu):
                menu.aboutToShow.emit()
                walk(menu.actions(), where)

    walk(menuBar.actions() if menuBar else [], "")
    return texts


def widgetTexts(widget: QWidget, where: str) -> list[tuple[str, str]]:
    texts = []
    for child in widget.findChildren(QWidget):
        if not child.isVisible():
            continue
        if isinstance(child, (QLabel, QAbstractButton)) and child.text():
            texts.append((where, plain(child.text())))
        if child.toolTip():
            texts.append((f"{where} (tool tip)", child.toolTip()))
    return texts


def test_only_what_the_ui_says(qaRun: QaRun, qaData: QaData) -> None:
    run = qaRun
    corpus: list[tuple[str, str]] = []
    with run.step("read every menu"):
        corpus += menuTexts(run)
        run.log("menu texts", count=len(corpus))

    outcome = journeys.firstSuccess(run, qaData.cube, run.folder / "result.tif")
    if outcome.viewed is not None:
        corpus += widgetTexts(outcome.viewed, "the image workspace")
    with run.step("read the PCA dialog", " > ".join(journeys.PCA_MENU)):
        if outcome.opened is not None and run.useMenu(*journeys.PCA_MENU, shot=False):
            dialog = run.newDialog(RunAnalysisDialog)
            if dialog is not None:
                corpus += widgetTexts(dialog, "the PCA dialog")
                dialog.reject()

    with run.step("abbreviations"):
        _abbreviations(run, corpus)
    with run.step("idioms"):
        for idiom in IDIOMS:
            places = [w for w, t in corpus if re.search(rf"\b{idiom}", t, re.I)]
            if places:
                run.finding(
                    "confused",
                    f"Idiom or figurative word {idiom.strip()!r} in {places[0]}"
                    + (f" (and {len(places) - 1} more)" if len(places) > 1 else ""),
                )
    with run.step("one thing, several names"):
        for group in SYNONYMS:
            used = {
                word: sum(bool(re.search(rf"\b{word}s?\b", t, re.I)) for _, t in corpus)
                for word in group
            }
            used = {w: n for w, n in used.items() if n}
            run.log("synonyms", group=group, used=used)
            if len(used) > 2:
                run.finding(
                    "annoyed",
                    "The same kind of thing is called "
                    + ", ".join(f"{w!r} ({n}×)" for w, n in used.items())
                    + "; is it one thing or several?",
                )

    with run.step("an error message", "File > Import Image(s) > corrupt.tif"):
        before = len(run.messages)
        journeys.openImage(run, qaData.corrupt)
        messages = run.messagesSince(before)
        if not messages:
            run.finding(
                "confused",
                "Opening a broken file showed no message at all",
                widget=run.mainWindow,
            )
        for message in messages:
            _judgeMessage(run, message.text, message.screenshot)


def _abbreviations(run: QaRun, corpus: list[tuple[str, str]]) -> None:
    allText = "\n".join(t for _, t in corpus)
    places: dict[str, list[str]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for where, text in corpus:
        for token in ABBREVIATION.findall(text):
            word = (
                token.rstrip("s") if token.endswith("s") and len(token) > 2 else token
            )
            if word in KNOWN or len(word) < 2:
                continue
            counts[word] += 1
            places[word].append(where)
    unexplained = [
        word
        for word in counts
        if not re.search(rf"\(\s*{re.escape(word)}s?\s*\)", allText)  # "Long Form (LF)"
        and not re.search(
            rf"{re.escape(word)}\s*\((?![A-Z0-9/]+\))", allText
        )  # "LF (long form)"
    ]
    run.log("abbreviations", unexplained={w: places[w][:5] for w in unexplained})
    for word in sorted(unexplained, key=lambda w: -counts[w])[:15]:
        run.finding(
            "confused",
            f"{word!r} is never spelled out (used {counts[word]}×, e.g. in "
            f"{places[word][0]})",
        )


def _judgeMessage(run: QaRun, text: str, shot: str | None) -> None:
    run.log("message read", text=text)
    jargon = LIBRARY_JARGON.search(text)
    if jargon is not None:
        run.finding(
            "confused",
            "An error message speaks the library's language: "
            f"{jargon.group(0)!r} in {text[:160]!r}",
            shot=shot,
        )
    if not NEXT_STEP.search(text):
        run.finding(
            "confused",
            f"An error message doesn't say what to do next: {text[:160]!r}",
            shot=shot,
        )
