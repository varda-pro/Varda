"""The spectral hourglass workspace.

Pipeline steps on the left, the scene in the middle, the n-D visualizer and
the endmember table on the right, spectra underneath. Everything is linked:
lassoing points in the n-D view, or drawing on the scene, colors those pixels
in the scene and plots the class mean spectrum; hovering a point marks its
pixel; clicking a pixel
rings its point; picking a library match overlays it on the endmember.
"""

from __future__ import annotations

from collections.abc import MutableSequence

import json
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import PyQt6Ads as ads
from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from varda.analysis.linear_algebra import unflatten
from varda.analysis.spectral_library import LibrarySpectrum, resampleTo
from varda.branding.loader import RedrawSpinner
from varda.common.entities import Color, VardaRaster
from varda.common.provenance import setProvenance
from varda.common.ui import VardaDockWidget, showsBusyCursor
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering.new_histogram_view import NewHistogramView
from varda.plotting.plot import VardaPlotWidget
from varda.plotting.spectrum_matching import matchToReference
from varda.spectral_libraries.spectrum import SpectrumOrigin
from varda.workspaces.cropping import addCrop, cropMessage
from varda.workspaces.plot_libraries import plotLibrariesState, restorePlotLibraries
from varda.workspaces.spectral_hourglass.endmember_table import EndmemberTable
from varda.workspaces.spectral_hourglass.hourglass_model import (
    HourglassModel,
    MapMethod,
    Step,
    StepState,
)
from varda.workspaces.spectral_hourglass.identify_panel import IdentifyPanel
from varda.workspaces.spectral_hourglass.nd_visualizer import NdVisualizer
from varda.workspaces.spectral_hourglass.pipeline_panel import PipelinePanel
from varda.workspaces.spectral_hourglass.scene_view import Overlay, SceneView
from varda.workspaces.spectral_hourglass.task_runner import TaskRunner

_WORKFLOW_FORMAT = "varda-hourglass"
_HOVER_COLOR = Color(1.0, 1.0, 1.0, 0.8)
_LIBRARY_COLOR = Color(1.0, 1.0, 0.0, 0.9)
_ACTIVE_WIDTH = 4.0
_OTHER_WIDTH = 1.5
_FADED_ALPHA = 0.35


class SpectralHourglassWorkspace(QMainWindow):
    @showsBusyCursor
    def __init__(
        self,
        image: VardaRaster,
        projectImages: MutableSequence[VardaRaster] | None = None,
        *,
        library: list[LibrarySpectrum] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{image.name} — Spectral Hourglass")
        self.model = HourglassModel(image)
        self.projectImages = projectImages if projectImages is not None else []
        self.runner = TaskRunner(self)
        self._hoveredPixel: int | None = None
        self._libraryPreview: LibrarySpectrum | None = None
        self._pendingComponents: int | None = None  # restored from a session
        self._pendingPpi: dict | None = None  # saved purity counts to restore
        self._resuming = False

        self.scene = SceneView(image, self)
        self.nd = NdVisualizer(self)
        self.endmemberTable = EndmemberTable(self.model, self)
        self.identifyPanel = IdentifyPanel(self.model, library=library)
        # The scene image's own display controls, as in the other workspaces
        self.displayPanel = self.scene.renderer.getSettingsPanel()
        self.histogram = NewHistogramView(self.scene.renderer, self)
        self.spectraPlot = VardaPlotWidget(parent=self)
        self.spectraPlot.setSidebarVisible(False)  # the arrow beside the plot shows it
        self.pipeline = PipelinePanel(
            self.model,
            self.runner,
            prepare=self._runPrepare,
            reduce=self._runMnf,
            purity=self._runPpi,
            mapEndmembers=self._runMap,
            identifyPanel=self.identifyPanel,
        )
        self._buildDocks()
        self._buildStatusBar()
        self.undoShortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undoShortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.undoShortcut.activated.connect(self.model.undo)
        self._connect()
        self._updateHint()

    # --- layout --------------------------------------------------------------------

    def _buildDocks(self) -> None:
        self.dockManager = ads.CDockManager(self)
        self.sceneDock = VardaDockWidget("Scene").withWidget(self.scene)
        self.pipelineDock = VardaDockWidget("Pipeline").withWidget(
            self._pipelineContainer()
        )
        self.ndDock = VardaDockWidget("n-D Visualizer").withWidget(self.nd)
        self.endmemberDock = VardaDockWidget("Endmembers").withWidget(
            self.endmemberTable
        )
        self.spectraDock = VardaDockWidget("Spectra").withWidget(self.spectraPlot)
        self.displayDock = VardaDockWidget("Display").withWidget(self.displayPanel)
        self.histogramDock = VardaDockWidget("Histogram").withWidget(self.histogram)
        manager = self.dockManager
        manager.addDockWidget(ads.DockWidgetArea.CenterDockWidgetArea, self.sceneDock)
        manager.addDockWidget(
            ads.DockWidgetArea.LeftDockWidgetArea,
            self.pipelineDock,
            self.sceneDock.dockAreaWidget(),
        )
        manager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea,
            self.ndDock,
            self.sceneDock.dockAreaWidget(),
        )
        manager.addDockWidget(
            ads.DockWidgetArea.BottomDockWidgetArea,
            self.endmemberDock,
            self.ndDock.dockAreaWidget(),
        )
        manager.addDockWidget(
            ads.DockWidgetArea.BottomDockWidgetArea,
            self.spectraDock,
            self.sceneDock.dockAreaWidget(),
        )
        # Display and histogram tabbed beside the pipeline (the pipeline in front)
        for dock in (self.displayDock, self.histogramDock):
            manager.addDockWidget(
                ads.DockWidgetArea.CenterDockWidgetArea,
                dock,
                self.pipelineDock.dockAreaWidget(),
            )
        self.pipelineDock.setAsCurrentTab()

    def _pipelineContainer(self) -> QWidget:
        saveButton = QPushButton("Save Workflow…")
        saveButton.setToolTip(
            "Save settings, endmembers, library exclusions and pixel purity, "
            "to pick up later with Open Workflow"
        )
        saveButton.clicked.connect(lambda: self._guarded(self.saveWorkflow))
        openButton = QPushButton("Open Workflow…")
        openButton.clicked.connect(lambda: self._guarded(self.openWorkflow))
        self.resumeButton = QPushButton("▶ Resume: re-run Prepare and MNF")
        self.resumeButton.setToolTip(
            "Recompute the prepared cube and MNF, then restore the saved pixel purity"
        )
        self.resumeButton.setVisible(False)
        self.resumeButton.clicked.connect(self.resume)
        row = QHBoxLayout()
        row.addWidget(saveButton)
        row.addWidget(openButton)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 0)
        layout.addLayout(row)
        layout.addWidget(self.resumeButton)
        layout.addWidget(self.pipeline, stretch=1)
        return container

    def _guarded(self, action) -> None:
        try:
            action()
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(self, "Spectral Hourglass", str(error))

    def _buildStatusBar(self) -> None:
        status = QStatusBar(self)
        self.status = status
        self.progressBar = QProgressBar()
        self.progressBar.setMaximumWidth(220)
        self.progressBar.setVisible(False)
        self.spinner = RedrawSpinner(20)
        self.spinner.setVisible(False)
        status.addPermanentWidget(self.spinner)
        status.addPermanentWidget(self.progressBar)
        self.setStatusBar(status)

    def _connect(self) -> None:
        model = self.model
        model.sigStepChanged.connect(self._onStepChanged)
        model.sigCandidatesChanged.connect(self._refreshNdData)
        model.sigEndmembersChanged.connect(self._onEndmembersChanged)
        self.runner.sigBusyChanged.connect(self._onBusy)
        self.runner.sigProgress.connect(self._onProgress)
        self.runner.sigFailed.connect(self._onFailed)
        self.nd.sigLassoSelected.connect(self._assignPixels)
        self.scene.sigPixelsDrawn.connect(self._onPixelsDrawn)
        self.scene.sigCropRequested.connect(self._onCropRequested)
        self.nd.sigPointHovered.connect(self._onPixelHovered)
        self.nd.collapseBox.toggled.connect(lambda _: self._applyCollapse())
        self.scene.sigPixelClicked.connect(self._onPixelClicked)
        self.endmemberTable.sigActiveChanged.connect(self._onActiveEndmember)
        self.identifyPanel.sigPreviewMatch.connect(self._previewLibrary)
        self.pipeline.reducePage.sigShowComponent.connect(
            lambda: self.scene.showOverlay(Overlay.MNF)
        )
        self.pipeline.reducePage.addButton.clicked.connect(self._addMnfToProject)
        self.pipeline.sigStartOver.connect(self._startOver)
        self.pipeline.purityPage.addButton.clicked.connect(self._addPpiToProject)
        review = self.pipeline.mapPage.review
        review.sigDetectionsChanged.connect(self.scene.setDetections)
        review.sigAddToProject.connect(self.projectImages.append)

    # --- running steps -------------------------------------------------------------------

    def _runPrepare(self, _settings=None) -> None:
        self.runner.run("Prepare", self.model.prepareTask(), self._onPrepared)

    def _onPrepared(self, result) -> None:
        self.model.applyPrepared(result)
        self.pipeline.showStep(Step.REDUCE)
        if self._resuming:
            self._runMnf()

    def _runMnf(self) -> None:
        self.runner.run("MNF", self.model.mnfTask(), self._onMnf)

    def _onMnf(self, result) -> None:
        self.model.applyMnf(result)
        if self._pendingComponents is not None:
            self.model.setComponents(self._pendingComponents)
            self._pendingComponents = None
        self.scene.enableComponents(result.scores.shape[1], self.model.componentImage)
        if self._resuming:
            self._finishResume()

    def _runPpi(
        self, iterations: int, threshold: float, seed: int, resume: bool
    ) -> None:
        task = self.model.ppiTask(iterations, threshold, seed=seed, resume=resume)
        self.runner.run("Pixel purity", task, self._onPpi)

    def _onPpi(self, result) -> None:
        page = self.pipeline.purityPage
        self.model.minCount = page.minCount.value()
        self.model.maxCandidates = page.maxCandidates.value()
        self.model.applyPpi(result)
        self.scene.setPpi(self.model.ppiImage())
        self.scene.showOverlay(Overlay.PPI)
        self.pipeline.showStep(Step.ENDMEMBERS)

    def _runMap(self, method: MapMethod) -> None:
        page = self.pipeline.mapPage
        try:
            task = self.model.mapTask(
                method,
                maxAngle=page.maxAngle.value(),
                constraint=page.constraint.currentData(),
            )
        except ValueError as error:
            QMessageBox.information(self, "Map Endmembers", str(error))
            return
        self.runner.run("Map", task, self._onMapped)

    def _onMapped(self, result) -> None:
        raster = self.model.applyMap(result)
        self.projectImages.append(raster)
        self.scene.setMapResult(raster)
        if raster.bandNames[0].startswith("MF("):
            self.pipeline.mapPage.showReview(raster)

    def _onBusy(self, busy: bool) -> None:
        self.progressBar.setVisible(busy)
        self.spinner.setVisible(busy)
        if not busy:
            self.status.clearMessage()

    def _onProgress(self, percent: int, message: str) -> None:
        self.progressBar.setValue(percent)
        self.status.showMessage(message)

    def _onFailed(self, label: str, error: object) -> None:
        QMessageBox.critical(self, f"{label} failed", f"{label} failed:\n{error}")

    # --- results to the project -------------------------------------------------------------

    def _footprintRaster(
        self,
        values: np.ndarray,
        bandNames: list[str],
        name: str,
        units: str,
        output: str,
        extraMetadata: dict | None = None,
        detail: str = "",
    ) -> VardaRaster:
        """A result over the image's footprint, with the run's provenance
        (``output``: "MNF" or "PPI")."""
        image = self.model.image
        source = ArrayDataSource(
            values.astype(np.float32),
            wavelengths=np.array(bandNames),
            wavelengthUnits=units,
            bandNames=bandNames,
            transform=image.transform,
            crs=image.crs,
            description=f"{name} (spectral hourglass{detail})",
            extraMetadata=dict(extraMetadata or {}),
        )
        raster = VardaRaster(source, name=name)
        setProvenance(raster, self.model.provenance(output))
        return raster

    def _addMnfToProject(self) -> None:
        result = self.model.mnf
        if result is None:
            return
        k = self.model.components
        values = unflatten(result.scores[:, :k], self.model.valid)
        names = [f"MNF {i + 1}" for i in range(k)]
        self.projectImages.append(
            self._footprintRaster(
                values,
                names,
                f"{self.model.image.name} MNF",
                "components",
                "MNF",
                self.model.mnfMetadata(),
            )
        )

    def _addPpiToProject(self) -> None:
        ppi = self.model.ppi
        if ppi is None:
            return
        values = self.model.ppiImage()[:, :, np.newaxis]
        seed = "unknown seed" if ppi.seed is None else f"seed {ppi.seed}"
        detail = (
            f": {ppi.iterations:,} iterations, {seed}, threshold {ppi.threshold:g}, "
            f"{ppi.components} MNF components"
        )
        self.projectImages.append(
            self._footprintRaster(
                values,
                ["PPI"],
                f"{self.model.image.name} PPI",
                "count",
                "PPI",
                detail=detail,
            )
        )

    # --- linking --------------------------------------------------------------------------

    def _onStepChanged(self, step: Step) -> None:
        if (
            step in (Step.REDUCE, Step.PURITY)
            and self.model.state(Step.PURITY) is StepState.DONE
        ):
            self._refreshNdData()

    def _refreshNdData(self) -> None:
        if (
            self.model.ppi is None
            or self.model.state(Step.PURITY) is not StepState.DONE
        ):
            return
        pixels = self.model.candidatePixels()
        self.nd.setData(
            self.model.ndPoints(), pixels, background=self.model.backgroundSample()
        )
        self.scene.setCandidatePixels(pixels)
        self._onEndmembersChanged()

    def _onEndmembersChanged(self) -> None:
        colors = {}
        groups = []
        for endmember in self.model.endmembers:
            groups.append((endmember.pixels, endmember.color))
            for pixel in endmember.pixels:
                colors[int(pixel)] = endmember.color
        self.nd.setPointColors(colors)
        self.scene.setEndmemberPixels(groups)
        self._applyCollapse()
        self._refreshSpectra()
        self._updateHint()

    def _applyCollapse(self) -> None:
        if not self.nd.collapseBox.isChecked() or self.model.mnf is None:
            self.nd.setCollapseMeans(None)
            return
        candidates = self.model.ndPoints()
        centre = candidates.mean(axis=0) if candidates.size else 0.0
        means = [
            np.nanmean(self.model.mnfCoordinates(e.pixels), axis=0) - centre
            for e in self.model.usableEndmembers()
        ]
        self.nd.setCollapseMeans(means if len(means) >= 2 else None)

    def _onActiveEndmember(self, endmemberId) -> None:
        self.identifyPanel.setEndmember(endmemberId)
        self._libraryPreview = None
        self._refreshSpectra()
        self._updateHint()

    def _activeOrNewEndmember(self) -> int:
        active = self.endmemberTable.activeEndmemberId()
        if active is None:
            endmember = self.model.addEndmember()
            self.endmemberTable.setActiveEndmember(endmember.id)
            return endmember.id
        return active

    def _onCropRequested(self, box: QRect) -> None:
        cropped = addCrop(self.model.image, box, self.projectImages)
        self.status.showMessage(cropMessage(cropped))

    def _onPixelsDrawn(self, pixels: np.ndarray, add: bool) -> None:
        """Pixels inside a shape drawn on the scene: the valid ones join (or
        leave) the active endmember, as a lasso in the n-D view does."""
        if self.model.prepared is None:
            self.status.showMessage(
                "Run Prepare first: endmember pixels come from the prepared image."
            )
            return
        valid = self.model.valid.ravel()
        self._assignPixels(pixels[valid[pixels]], add)

    def _assignPixels(self, pixels, add: bool) -> None:
        if not len(pixels):
            return
        if add:
            self.model.assignPixels(self._activeOrNewEndmember(), pixels)
        elif (active := self.endmemberTable.activeEndmemberId()) is not None:
            self.model.assignPixels(active, pixels, add=False)

    def _onPixelHovered(self, pixel) -> None:
        self._hoveredPixel = pixel
        self.scene.highlightPixel(pixel)
        self._refreshSpectra()

    def _onPixelClicked(self, pixel: int) -> None:
        self._hoveredPixel = pixel
        self.scene.highlightPixel(pixel)
        self.nd.highlightPixel(pixel)
        self._refreshSpectra()

    def _previewLibrary(self, spectrum: LibrarySpectrum) -> None:
        self._libraryPreview = spectrum
        self._refreshSpectra()

    def _updateHint(self) -> None:
        active = self.endmemberTable.activeEndmemberId()
        target = (
            self.model.endmember(active).name
            if active is not None
            else "a new endmember"
        )
        count = len(self.model.candidatePixels()) if self.model.ppi is not None else 0
        if not count:
            self.nd.setHint(
                "Run Pixel Purity to fill this view with candidate pure pixels."
            )
            return
        self.nd.setHint(
            f"{count:,} candidate pixels. <b>Rotate</b> (R): drag to turn the cloud. "
            f"<b>Lasso</b> (L): drag around points to add them to <b>{target}</b>, "
            "Shift-drag removes (or use Draw on Image on the scene). Right-drag "
            "pans, the wheel zooms."
        )

    # --- spectra ------------------------------------------------------------------------

    def _x(self) -> np.ndarray:
        wavelengths = self.model.wavelengths
        return (
            wavelengths
            if wavelengths is not None
            else np.arange(self.model.cube.shape[2]) + 1.0
        )

    def _refreshSpectra(self) -> None:
        plot = self.spectraPlot
        plot.clearPlots()
        if self.model.prepared is None:
            return
        x = self._x()
        model = self.model.image.bandModel
        hasWavelengths = self.model.wavelengths is not None and model.isSpectral
        plot.setSpectralUnit(model.unit if hasWavelengths else None)
        active = self.endmemberTable.activeEndmemberId()
        activeSpectrum = None
        # the active endmember last (on top), thick with a halo; the rest faded
        ordered = sorted(self.model.usableEndmembers(), key=lambda e: e.id == active)
        for endmember in ordered:
            spectrum = self.model.endmemberSpectrum(endmember.id)
            isActive = endmember.id == active
            color = QColor(endmember.color)
            if not isActive and active is not None:
                color.setAlphaF(_FADED_ALPHA)
            curve = plot.plot(
                x, spectrum, color=Color.fromQColor(color), name=endmember.name
            )
            curve.origin = SpectrumOrigin(
                self.model.image.name,
                self.model.image.filePath or "",
                f"endmember “{endmember.name}” (Spectral Hourglass)",
                int(endmember.pixels.size),
                "mean of its pixels' prepared spectra",
            )
            curve.config.width.set(_ACTIVE_WIDTH if isActive else _OTHER_WIDTH)
            if isActive:
                curve.plotDataItem.setShadowPen(
                    pg.mkPen(255, 255, 255, 90, width=_ACTIVE_WIDTH + 6)
                )
                activeSpectrum = spectrum
        if self._hoveredPixel is not None:
            flat = self.model.cube.reshape(-1, self.model.cube.shape[2])
            if 0 <= self._hoveredPixel < flat.shape[0]:
                plot.plot(x, flat[self._hoveredPixel], color=_HOVER_COLOR, name="Pixel")
        if self._libraryPreview is not None and self.model.wavelengthsNm is not None:
            y = resampleTo(
                self._libraryPreview, self.model.wavelengthsNm, self.model.fwhmNm
            )
            if activeSpectrum is not None:
                scale, offset = matchToReference(
                    x, y, x, activeSpectrum, (float(x.min()), float(x.max()))
                )
                y = y * scale + offset
            preview = plot.plot(
                x, y, color=_LIBRARY_COLOR, name=f"{self._libraryPreview.name} (scaled)"
            )
            # Add to Library copies the library spectrum, not this resampled view
            preview.librarySpectrum = self._libraryPreview

    # --- starting over, saving and resuming --------------------------------------------------

    def _startOver(self, step: Step) -> None:
        order = list(Step)
        later = order[order.index(step) :]
        self.model.resetFrom(step)
        if Step.REDUCE in later:
            self.scene.removeOverlay(Overlay.MNF)
        if Step.PURITY in later:
            self.scene.removeOverlay(Overlay.PPI)
            self.scene.setCandidatePixels(np.zeros(0, dtype=np.int64))
            self.nd.clear()
            self._updateHint()
        if Step.MAP in later:
            self.scene.clearMaps()
        self._refreshSpectra()
        self.pipeline.showStep(step)
        self.status.showMessage(f"Starting over from {step.value}.")

    def saveWorkflow(self, path=None) -> Path:
        """Write the workflow (settings, endmembers, exclusions and pixel
        purity) to a JSON file; returns its path."""
        if path is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                "Save Workflow",
                f"{self.model.image.name}.hourglass.json",
                "Workflow (*.json)",
            )
            if not chosen:
                raise OSError("Save canceled.")
            path = chosen
        image = self.model.image
        document = {
            "format": _WORKFLOW_FORMAT,
            "version": 1,
            "image": {
                "name": image.name,
                "path": image.filePath,
                "shape": [image.height, image.width],
            },
            "state": self.sessionState(),
        }
        target = Path(path)
        target.write_text(json.dumps(document))
        self.status.showMessage(f"Workflow saved to {target}")
        return target

    def openWorkflow(self, path=None) -> None:
        """Load a saved workflow for this image and resume it."""
        if path is None:
            chosen, _ = QFileDialog.getOpenFileName(
                self, "Open Workflow", "", "Workflow (*.json)"
            )
            if not chosen:
                return
            path = chosen
        document = json.loads(Path(path).read_text())
        if document.get("format") != _WORKFLOW_FORMAT:
            raise ValueError(f"{path} is not a Spectral Hourglass workflow.")
        image = self.model.image
        if list(document["image"]["shape"]) != [image.height, image.width]:
            raise ValueError(
                f"That workflow was saved for a different image ({document['image']['name']})."
            )
        self.restoreSessionState(document["state"])
        self.resume()

    def resume(self) -> None:
        """Re-run Prepare and MNF, then restore the saved component count and
        pixel purity."""
        self.resumeButton.setVisible(False)
        self._resuming = True
        self._runPrepare()

    def _finishResume(self) -> None:
        self._resuming = False
        purity = self.pipeline.purityPage
        self.model.minCount = purity.minCount.value()
        self.model.maxCandidates = purity.maxCandidates.value()
        if self._pendingPpi is not None:
            try:
                self.model.ppiFromJson(self._pendingPpi)
            except ValueError as error:
                self.status.showMessage(f"{error} Run Pixel Purity again.")
            else:
                self.scene.setPpi(self.model.ppiImage())
                self.pipeline.showStep(Step.ENDMEMBERS)
            self._pendingPpi = None

    # --- sessions --------------------------------------------------------------------------

    def sessionState(self) -> dict:
        """Settings and endmember classes (results are recomputed on restore)."""
        settings = self.model.settings
        purity = self.pipeline.purityPage
        return {
            "settings": {
                "wavelengthRange": None
                if settings.wavelengthRange is None
                else list(settings.wavelengthRange),
                "useIovf": settings.useIovf,
            },
            "components": self.model.components or None,
            "ppi": {  # the page's settings; the counts' own are in ppiCounts
                "iterationsPerRun": purity.iterations.value(),
                "threshold": purity.threshold.value(),
                "seed": purity.seed.value(),
                "minCount": purity.minCount.value(),
                "maxCandidates": purity.maxCandidates.value(),
            },
            "endmembers": self.model.endmembersToJson(),
            "excludedLibrarySpectra": self.identifyPanel.excludedNames(),
            "ppiCounts": self.model.ppiToJson(),
            **plotLibrariesState(self.spectraPlot),
        }

    def restoreSessionState(self, state: dict) -> None:
        settings = state.get("settings", {})
        prepare = self.pipeline.preparePage
        window = settings.get("wavelengthRange")
        if window is not None:
            prepare.setWavelengthRange(float(window[0]), float(window[1]))
        prepare.iovfBox.setChecked(
            bool(settings.get("useIovf", prepare.iovfBox.isChecked()))
        )
        self._pendingComponents = state.get("components")
        purity = self.pipeline.purityPage
        ppi = state.get("ppi", {})
        purity.iterations.setValue(
            int(
                ppi.get("iterationsPerRun", ppi.get("iterations"))
                or purity.iterations.value()
            )
        )
        purity.threshold.setValue(float(ppi.get("threshold", purity.threshold.value())))
        purity.seed.setValue(int(ppi.get("seed", purity.seed.value())))
        purity.minCount.setValue(int(ppi.get("minCount", purity.minCount.value())))
        purity.maxCandidates.setValue(
            int(ppi.get("maxCandidates", purity.maxCandidates.value()))
        )
        if state.get("endmembers"):
            self.model.endmembersFromJson(state["endmembers"])
        self.identifyPanel.setExcluded(state.get("excludedLibrarySpectra", []))
        restorePlotLibraries(state, self.spectraPlot)
        self._pendingPpi = state.get("ppiCounts")
        self.resumeButton.setVisible(self.model.prepared is None)
        self.status.showMessage(
            "Restored: press Resume to recompute Prepare and MNF and continue."
        )

    # --- lifecycle -------------------------------------------------------------------------

    def closeEvent(self, a0) -> None:
        self.nd.playButton.setChecked(False)
        super().closeEvent(a0)
