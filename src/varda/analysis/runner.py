"""Run an analysis in the background with a progress dialog; results join the project."""

from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QWidget

from varda.analysis.analysis import Analysis
from varda.analysis.provenance import recordProvenance
from varda.branding.loader import BusyDialog
from varda.common.di_types import ProjectImages
from varda.common.entities import VardaRaster
from varda.common.memory import (
    MemoryFit,
    assessMemory,
    availableMemory,
    memoryWarning,
    totalMemory,
)
from varda.image_loading.raster_writer import writeRaster
from varda.utilities.background import onMainThread, runInBackground


def companionPath(outputPath: str, main: VardaRaster, companion: VardaRaster) -> Path:
    """Where a companion result is saved: beside the main one, the words of
    its name that differ from the main one's added to the file name ("scene
    IOVF replaced" for "scene IOVF" in "out.img" is "out_replaced.img")."""
    path = Path(outputPath)
    words, mainWords = companion.name.split(), main.name.split()
    shared = 0
    while shared < min(len(words), len(mainWords)) and (
        words[shared] == mainWords[shared]
    ):
        shared += 1
    extra = " ".join(words[shared:]) or companion.name
    slug = re.sub(r"[^\w-]+", "_", extra).strip("_")
    return path.with_name(f"{path.stem}_{slug}{path.suffix}")


def askToRunAnyway(
    parent: QWidget | None, title: str, text: str, cautious: bool
) -> bool:
    """Warn about memory and let the user decide. ``cautious``: the job needs
    more than the machine has, so Cancel is the default button."""
    box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=parent)
    runAnyway = box.addButton("Run Anyway", QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(cancel if cautious else runAnyway)
    box.exec()
    return box.clickedButton() is runAnyway


class AnalysisRunner(QObject):
    """Runs analyses in the background; a successful result is appended to the
    project's images."""

    sigProgress = pyqtSignal(int, str)
    sigFinished = pyqtSignal(object)  # the resulting VardaRaster (the first)
    sigFailed = pyqtSignal(object)  # the exception

    def __init__(self, images: ProjectImages, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._images = images
        self._parentWidget = parent

    def run(
        self, analysis: Analysis, image: VardaRaster, outputPath: str | None = None
    ) -> None:
        """``outputPath``: also write the result there (ENVI or GeoTIFF). An
        analysis that may not fit in the free memory asks first."""
        if not self._memoryAllows(analysis, image):
            return
        # No cancel button: analyses run to completion
        dialog = BusyDialog(
            f"Running {analysis.name} on {image.name}…",
            title="Analysis",
            parent=self._parentWidget,
        )
        dialog.setValue(0)
        dialog.show()

        def onProgress(percent: int, message: str) -> None:
            dialog.setLabelText(f"{analysis.name}: {message}")
            dialog.setValue(percent)
            self.sigProgress.emit(percent, message)

        report = onMainThread(onProgress)

        def task() -> list[VardaRaster]:
            results = analysis.runAll(image, report)
            for result in results:
                recordProvenance(result, analysis, image)
            if outputPath is not None:
                report(100, f"saving to {outputPath}")
                writeRaster(results[0], outputPath)
                for companion in results[1:]:
                    writeRaster(
                        companion, companionPath(outputPath, results[0], companion)
                    )
            return results

        def onFinished(results: list[VardaRaster]) -> None:
            dialog.close()
            for result in results:
                self._images.append(result)
            self.sigFinished.emit(results[0])

        def onFailed(error: Exception) -> None:
            dialog.close()
            QMessageBox.critical(
                self._parentWidget,
                "Analysis failed",
                f"{analysis.name} failed:\n{error}",
            )
            self.sigFailed.emit(error)

        runInBackground(
            task, onFinished, onFailed, description=f"Analysis {analysis.name}"
        )

    def _memoryAllows(self, analysis: Analysis, image: VardaRaster) -> bool:
        estimate = analysis.estimateMemory(image)
        available, total = availableMemory(), totalMemory()
        fit = assessMemory(estimate, available, total)
        if fit is MemoryFit.FITS:
            return True
        title, text = memoryWarning(analysis.name, estimate, available, total)
        return askToRunAnyway(
            self._parentWidget, title, text, fit is MemoryFit.EXCEEDS_TOTAL
        )
