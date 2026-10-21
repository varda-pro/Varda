"""The laptop user: works at 1280×800 and 1024×700, offline, with a dozen
images open and one large cube. Flags anything clipped, needing sideways
scrolling, or frozen without progress. On Windows also at 150 % display
scaling; on Linux, with whatever fonts the system has."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtGui import QFontDatabase, QFontInfo, QFontMetrics
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from qa import journeys
from qa.data import QaData, manySmall
from qa.harness import Network, QaRun, keyClick
from varda.analysis.dialog import RunAnalysisDialog
from varda.common.entities import VardaRaster
from varda.common.layout_audit import clippedWidgets
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.export_image_dialog import ExportImageDialog
from varda.image_loading.raster_writer import writeRaster

pytestmark = [pytest.mark.qa, pytest.mark.qt_no_exception_capture]

SIZES = ((1280, 800), (1024, 700))
LARGE_SHAPE = (700, 640, 120)  # rows, columns, bands: ~215 MB of float32
# Characters Varda's own text uses that a fallback font must draw
UI_CHARACTERS = "…→×µ²–°"


def audit(run: QaRun, widget: QWidget, where: str, limit: int = 8) -> None:
    """Every widget whose text is cut off is a finding (the first few, with
    one screenshot; all of them in the log)."""
    clipped = clippedWidgets(widget)
    run.log("layout audit", where=where, clipped=clipped)
    if not clipped:
        return
    shot = run.qaShot(widget, f"clipped-{where}", f"{len(clipped)} clipped")
    for problem in clipped[:limit]:
        run.finding("confused", f"Cut off in {where}: {problem}", shot=shot)
    if len(clipped) > limit:
        run.finding(
            "confused",
            f"…and {len(clipped) - limit} more cut-off widgets in {where} "
            "(see steps.jsonl)",
            shot=shot,
        )


def test_small_screen_offline(qaRun: QaRun, qaData: QaData) -> None:
    run = qaRun
    Network.offline = True  # on a plane
    width, height = SIZES[0]
    with run.step(f"window {width}×{height}"):
        run.resizeWindow(width, height)
        run.qaShot(run.mainWindow, f"empty-{width}x{height}")
        audit(run, run.mainWindow, f"the empty window at {width}×{height}")

    outcome = journeys.firstSuccess(run, qaData.cube, run.folder / "pca.img")

    if outcome.viewed is not None and outcome.spectrum:
        with run.step("the same pixel again", "Click the same pixel a second time"):
            added = journeys.clickSamePixelAgain(run, outcome.viewed)
            run.log("curves added by a repeat click", count=added)
            if added:
                run.finding(
                    "annoyed",
                    "Selecting the same pixel again adds a second, identical curve "
                    "and legend entry",
                    widget=outcome.viewed,
                    reproduce="Pixel Select, click one pixel twice",
                )

    if outcome.viewed is not None:
        for width, height in SIZES:
            with run.step(f"workspace at {width}×{height}"):
                run.resizeWindow(width, height)
                run.qaShot(run.mainWindow, f"workspace-{width}x{height}")
                audit(run, run.mainWindow, f"the workspace at {width}×{height}")
        run.resizeWindow(*SIZES[0])

    if outcome.opened is not None:
        with run.step(
            "dialogs at laptop size",
            "Processing > PCA; File > Export Image…; Processing > CRISM",
        ):
            for menu, kind in (
                (journeys.PCA_MENU, RunAnalysisDialog),
                (("File", "Export Image"), ExportImageDialog),
                (("Help", "Cite Varda"), QWidget),
                *((menu, QWidget) for menu in journeys.CRISM_DIALOG_MENUS),
            ):
                if not run.useMenu(*menu, shot=False):
                    continue
                dialog = run.newDialog() if kind is QWidget else run.newDialog(kind)
                if dialog is None:
                    continue
                screen = QApplication.primaryScreen()
                available = screen.availableGeometry() if screen else None
                run.log("dialog size", dialog=dialog.windowTitle(), size=dialog.size())
                if dialog.height() > SIZES[0][1] or dialog.width() > SIZES[0][0]:
                    run.finding(
                        "confused",
                        f"{dialog.windowTitle()} opens {dialog.width()}×"
                        f"{dialog.height()}, larger than a {SIZES[0][0]}×"
                        f"{SIZES[0][1]} screen (available: {available})",
                        widget=dialog,
                    )
                audit(run, dialog, f"the {dialog.windowTitle()} dialog")
                keyClick(dialog, Qt.Key.Key_Escape)
                run.settle(0.2)

    with run.step("a dozen images", "File > Import Image(s), pick 12 files"):
        tiles = manySmall(run.folder / "tiles", 12)
        before = len(journeys.images(run))
        started = time.monotonic()
        run.chooseFiles(*tiles)
        if run.useMenu("File", "Import Image(s)"):
            run.waitFor(
                lambda: len(journeys.images(run)) >= before + len(tiles),
                "12 images to open",
                timeout=120,
            )
        run.log("dozen opened", seconds=round(time.monotonic() - started, 2))
        run.settle(0.5)
        listed = [Path(i.name).stem for i in journeys.images(run)[before:]]
        chosen = [tile.stem for tile in tiles]
        run.log("dozen order", listed=listed, chosen=chosen)
        if listed != chosen[: len(listed)]:
            run.finding(
                "annoyed",
                "Images opened together are listed in the order they finished "
                f"loading, not the order chosen ({', '.join(listed[:4])}…)",
                widget=run.mainWindow,
                reproduce="File > Import Image(s), pick tile_00 … tile_11",
            )
        run.qaShot(run.mainWindow, "a-dozen-images")
        audit(run, run.mainWindow, "the window with 14 images open")

    with run.step("large cube", "File > Import Image(s) > a ~215 MB cube"):
        large = writeRaster(_largeCube(), run.folder / "large cube.img")
        started = time.monotonic()
        image = journeys.openImage(run, large, timeout=300)
        seconds = time.monotonic() - started
        run.log("large cube opened", seconds=round(seconds, 2), ok=image is not None)
        if image is not None:
            started = time.monotonic()
            workspace = journeys.viewImage(run, image)
            run.log("large cube shown", seconds=round(time.monotonic() - started, 2))
            if workspace is not None:
                journeys.lookAtSpectrum(run, workspace)
                run.mainWindow.closeWorkspace(workspace)
            # Windows locks an open file: remove it from the project first
            journeys.removeImage(run, image)
        for part in (large, large.with_suffix(".hdr"), Path(f"{large}.aux.xml")):
            try:
                part.unlink(missing_ok=True)
            except PermissionError as error:
                run.log("large cube still in use", file=str(part), error=str(error))

    with run.step("offline"):
        hosts = sorted(set(Network.hosts))
        run.log(
            "connections refused",
            hosts=hosts,
            callers={h: Network.callers.get(h, []) for h in hosts},
        )
        for host in hosts:
            caller = " ← ".join(reversed(Network.callers.get(host, [])[-4:]))
            run.finding(
                "confused",
                f"Offline, Varda still tried a connection to {host} that no step "
                f"explains (from {caller or 'unknown'})",
            )

    if sys.platform.startswith("linux"):
        with run.step("fonts (Linux)", "Look at the window's text"):
            _checkFonts(run)


def _largeCube() -> VardaRaster:
    rows, columns, bands = LARGE_SHAPE
    rng = np.random.default_rng(3)
    data = rng.uniform(0.1, 0.4, LARGE_SHAPE).astype(np.float32)
    source = ArrayDataSource(
        data,
        wavelengths=np.linspace(400.0, 2500.0, bands),
        wavelengthUnits="Nanometers",
    )
    return VardaRaster(dataSource=source, name="large cube")


def _checkFonts(run: QaRun) -> None:
    """Linux machines often lack the fonts Qt asks for: the text must still
    show, through a fallback, with every character Varda uses."""
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    font = app.font()
    resolved = QFontInfo(font).family()
    families = QFontDatabase.families()
    run.log(
        "fonts", requested=font.family(), resolved=resolved, installed=len(families)
    )
    if not families:
        run.finding("blocked", "No fonts are installed: text can't be drawn")
        return
    metrics = QFontMetrics(font)
    missing = [c for c in UI_CHARACTERS if not metrics.inFontUcs4(ord(c))]
    unreachable = [
        c
        for c in missing
        if not any(
            QFontMetrics(QFontDatabase.font(f, "", 10)).inFontUcs4(ord(c))
            for f in families
        )
    ]
    run.log("glyphs", missingInDefault=missing, missingEverywhere=unreachable)
    if unreachable:
        run.finding(
            "confused",
            f"No installed font has {''.join(unreachable)!r}, which Varda's "
            f"text uses (default font {resolved!r}): they show as boxes",
            widget=run.mainWindow,
        )


@pytest.mark.qa_env(QT_SCALE_FACTOR="1.5")
@pytest.mark.skipif(sys.platform != "win32", reason="150 % scaling: a Windows check")
def test_windows_150_percent_scaling(qaRun: QaRun, qaData: QaData) -> None:
    """A 1920×1080 laptop at Windows' recommended 150 %: 1280×720 to lay out in."""
    run = qaRun
    with run.step("150 % scaling", "Display settings: Scale 150 %; open Varda"):
        run.resizeWindow(1280, 720)
        run.qaShot(run.mainWindow, "scaled-150-empty")
        audit(run, run.mainWindow, "the window at 150 % scaling")
    with run.step("150 % scaling, workspace"):
        image = journeys.openImage(run, qaData.cube)
        workspace = journeys.viewImage(run, image) if image is not None else None
        if workspace is not None:
            run.qaShot(run.mainWindow, "scaled-150-workspace")
            audit(run, run.mainWindow, "the workspace at 150 % scaling")
    with run.step("150 % scaling, dialog", " > ".join(journeys.PCA_MENU)):
        if image is not None and run.useMenu(*journeys.PCA_MENU, shot=False):
            dialog = run.newDialog(RunAnalysisDialog)
            if dialog is not None:
                run.qaShot(dialog, "scaled-150-pca-dialog")
                audit(run, dialog, "the PCA dialog at 150 % scaling")
                journeys.checkAnalysisDialog(run, dialog)
                dialog.reject()
