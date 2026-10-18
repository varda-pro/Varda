"""The impatient one: skims, clicks the first plausible thing, never reads
tooltips or docs, and gives up at the first error or unexplained wait.
Reports where a real user would have quit."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from qa import journeys
from qa.data import QaData
from qa.harness import QaRun

pytestmark = [pytest.mark.qa, pytest.mark.qt_no_exception_capture]

NO_MODIFIER = Qt.KeyboardModifier.NoModifier


def test_first_success_without_reading_anything(qaRun: QaRun, qaData: QaData) -> None:
    run = qaRun
    with run.step("empty window"):
        hints = [
            label.text()
            for label in run.mainWindow.findChildren(QLabel)
            if label.isVisible()
            and any(
                w in label.text().lower() for w in ("import", "drag", "drop", "open")
            )
        ]
        run.log("start hints", hints=hints)
        if not hints:
            run.finding(
                "confused",
                "The empty window doesn't say how to start (no hint to import "
                "or drop an image)",
                widget=run.mainWindow,
            )

    with run.step("open image", "File > Import Image(s) > qa_scene.tif"):
        image = journeys.openImage(run, qaData.geotiff)
    if image is None:
        run.finding(
            "blocked", "Gave up: the first image didn't open", widget=run.mainWindow
        )
        return

    with run.step("view image", "Double-click the image in the Image List"):
        workspace = journeys.viewImage(run, image)
    if workspace is None:
        run.finding("blocked", "Gave up: double-clicking the image showed nothing")
        return

    with run.step("pixel spectrum, plain click", "Pixel Select, then click the image"):
        # Doesn't read the tool tip: turns the tool on and just clicks
        if not journeys.lookAtSpectrum(run, workspace, NO_MODIFIER):
            run.finding(
                "confused",
                "With Pixel Select on, clicking the image does nothing; it needs "
                "Ctrl+Click, which only the tool tip says",
                widget=workspace,
                reproduce="Turn on Pixel Select, click the image without Ctrl",
            )
    with run.step("pixel spectrum", "Pixel Select, Ctrl+Click the image"):
        plotted = journeys.lookAtSpectrum(run, workspace)
        if not plotted:
            run.finding("blocked", "Ctrl+Click plotted no spectrum", widget=workspace)

    with run.step("run analysis", " > ".join(journeys.PCA_MENU)):
        result = journeys.runAnalysis(run, image)
    if result is None:
        run.finding("blocked", "Gave up: PCA gave no result", widget=run.mainWindow)
        return

    with run.step("look at the result", "Double-click the PCA result"):
        if journeys.viewImage(run, result) is None:
            run.finding("blocked", "The PCA result doesn't open")

    target = run.folder / "pca result.tif"
    with run.step("export", "File > Export Image… > pca result.tif"):
        if journeys.exportImage(run, result, target) is None:
            run.finding(
                "blocked", "Gave up: export wrote nothing", widget=run.mainWindow
            )

    with run.step("double-click Run", "Processing > PCA, double-click Run"):
        # Impatient: double-clicks buttons. Does one double-click run it twice?
        before = len(journeys.images(run))
        if run.useMenu(*journeys.PCA_MENU):
            dialog = run.newDialog()
            runButton = run.button(dialog, "Run") if dialog else None
            if runButton is not None:
                run.doubleClick(runButton, runButton.rect().center())
                run.waitFor(
                    lambda: len(journeys.images(run)) > before,
                    "the double-clicked PCA",
                    timeout=60,
                )
                run.settle(2.0)
                added = len(journeys.images(run)) - before
                run.log("results from a double-click", count=added)
                if added > 1:
                    run.finding(
                        "annoyed",
                        f"Double-clicking Run ran PCA {added} times",
                        widget=run.mainWindow,
                    )
