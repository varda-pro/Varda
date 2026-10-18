"""The pipeline panel: one page per hourglass step, each with its settings and
a primary action. Step titles carry their status (done, needs re-running)."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QToolBox,
    QVBoxLayout,
    QWidget,
)

from superqt import QLabeledDoubleRangeSlider

from varda.common.ui import MutedLabel
from varda.analysis.eigenvalue_plot import EigenvaluePlot
from varda.analysis.unmixing import Constraint
from varda.common.parameter import IntParameter
from varda.common.parameter_widgets import connectWhileAlive
from varda.common.spectral_axis import SpectralUnit
from varda.workspaces.spectral_hourglass.hourglass_model import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_PPI_ITERATIONS,
    DEFAULT_PPI_THRESHOLD,
    DEFAULT_SAM_MAX_ANGLE,
    HourglassModel,
    MapMethod,
    Step,
    StepState,
)
from varda.workspaces.spectral_hourglass.identify_panel import IdentifyPanel
from varda.workspaces.spectral_hourglass.mtmf_review import MtmfReview
from varda.workspaces.spectral_hourglass.task_runner import TaskRunner

# Images with at least this many bands get the spike filter by default
HYPERSPECTRAL_BANDS = 100

_MARKS = {StepState.NOT_RUN: "○", StepState.DONE: "✓", StepState.STALE: "⚠"}


def _hint(text: str) -> QLabel:
    label = MutedLabel(text)
    label.setWordWrap(True)
    return label


def _primary(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setDefault(True)
    return button


class PreparePage(QWidget):
    def __init__(self, model: HourglassModel, run) -> None:
        super().__init__()
        self.model = model
        image = model.image
        bands = image.bandModel
        # The window is chosen in the image's own unit (e.g. µm for CRISM)
        numeric = None if bands.centers is None else bands.native(bands.centers)
        self._wavelengths = numeric
        self._units = bands.label
        # one slider for the band window; the full range means no limit
        self.rangeSlider = QLabeledDoubleRangeSlider(Qt.Orientation.Horizontal)
        self.rangeSlider.setToolTip(
            "Drag the handles to keep only this wavelength window"
        )
        self.bandCountLabel = QLabel()
        if numeric is not None:
            lo, hi = float(np.nanmin(numeric)), float(np.nanmax(numeric))
            # superqt's stubs inherit the int slider's signatures; the double
            # slider takes floats
            self.rangeSlider.setRange(lo, hi)  # ty: ignore[invalid-argument-type]
            self.rangeSlider.setSingleStep(max((hi - lo) / 1000.0, 1e-6))  # ty: ignore[invalid-argument-type]
            self.rangeSlider.setDecimals(
                3 if bands.unit is SpectralUnit.MICROMETRE else 0
            )
            self.rangeSlider.setValue((lo, hi))
            self.rangeSlider.valueChanged.connect(lambda _: self._updateBandCount())
        else:
            self.rangeSlider.setEnabled(False)
        self._updateBandCount()
        self.iovfBox = QCheckBox("Remove spikes with IOVF")
        self.iovfBox.setToolTip(
            "Iterative outlier voting filter. Spikes otherwise pose as pure "
            "pixels and add noise to endmember spectra. Takes about a minute "
            "for a full CRISM scene."
        )
        # On by default for hyperspectral data, where spikes are the norm
        self.iovfBox.setChecked(image.bandCount >= HYPERSPECTRAL_BANDS)
        self.runButton = _primary("Prepare")
        self.runButton.clicked.connect(lambda: run(self._settings()))
        self.summary = QLabel()
        self.summary.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(
            _hint(
                f"{image.name}: {image.width} × {image.height} pixels, "
                f"{image.bandCount} bands. Drop noisy band ranges (e.g. CRISM "
                "beyond 2.6 µm); missing bands are filled along each spectrum."
            )
        )
        layout.addWidget(QLabel("Wavelength window"))
        layout.addWidget(self.rangeSlider)
        layout.addWidget(self.bandCountLabel)
        layout.addWidget(self.iovfBox)
        layout.addWidget(self.runButton)
        layout.addWidget(self.summary)
        layout.addStretch()

    def wavelengthRange(self) -> tuple[float, float] | None:
        """The chosen window, or None when it covers every band."""
        if self._wavelengths is None:
            return None
        lo, hi = (float(v) for v in self.rangeSlider.value())
        keep = (self._wavelengths >= lo) & (self._wavelengths <= hi)
        return None if keep.all() else (lo, hi)

    def setWavelengthRange(self, lo: float, hi: float) -> None:
        self.rangeSlider.setValue((lo, hi))
        self._updateBandCount()

    def _updateBandCount(self) -> None:
        if self._wavelengths is None:
            self.bandCountLabel.setText("No numeric wavelengths: every band is used.")
            return
        lo, hi = (float(v) for v in self.rangeSlider.value())
        kept = int(((self._wavelengths >= lo) & (self._wavelengths <= hi)).sum())
        total = self._wavelengths.size
        self.bandCountLabel.setText(
            f"{lo:g}–{hi:g} {self._units}: {kept} of {total} bands".replace("  ", " ")
        )

    def _settings(self) -> None:
        self.model.settings.wavelengthRange = self.wavelengthRange()
        self.model.settings.useIovf = self.iovfBox.isChecked()

    def refresh(self) -> None:
        if self.model.prepared is not None:
            prepared = self.model.prepared
            text = (
                f"{prepared.cube.shape[2]} bands, "
                f"{int(prepared.valid.sum()):,} usable pixels."
            )
            if prepared.iovf is not None:
                iovf = prepared.iovf
                rounds = iovf["rounds"]
                text += (
                    f" IOVF replaced {iovf['replacedTotal']:,} values at "
                    f"{iovf['pixelsWithReplacements']:,} pixels in {rounds} "
                    f"round{'s' if rounds != 1 else ''} "
                    f"({', '.join(str(n) for n in iovf['replacedPerRound'])})."
                )
            self.summary.setText(text)


class ReducePage(QWidget):
    sigShowComponent = pyqtSignal()

    def __init__(self, model: HourglassModel, run) -> None:
        super().__init__()
        self.model = model
        self.components = IntParameter("Components", 1, range=(1, 1))
        self.components.sigParameterChanged.connect(
            lambda v: model.setComponents(int(v))
        )
        self.runButton = _primary("Compute MNF")
        self.runButton.clicked.connect(run)
        self.plotHolder = QVBoxLayout()
        self.plot: EigenvaluePlot | None = None
        self.countSpin = QSpinBox()
        self.countSpin.setRange(1, 1)
        self.countSpin.valueChanged.connect(self.components.set)
        self.components.sigParameterChanged.connect(lambda v: self._syncSpin(int(v)))
        viewButton = QPushButton("View Components")
        viewButton.setToolTip(
            "Overlay MNF components on the scene to see where they turn to noise"
        )
        viewButton.clicked.connect(self.sigShowComponent.emit)
        self.addButton = QPushButton("Add MNF Image to Project")

        keep = QHBoxLayout()
        keep.addWidget(QLabel("Keep"))
        keep.addWidget(self.countSpin)
        keep.addWidget(QLabel("components"))
        keep.addStretch()
        keep.addWidget(viewButton)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _hint(
                "MNF orders components by signal-to-noise. Keep those with "
                "eigenvalues clearly above 1 (noise) and spatially coherent "
                "images; drag the cutoff line or set the count."
            )
        )
        layout.addWidget(self.runButton)
        layout.addLayout(self.plotHolder)
        layout.addLayout(keep)
        layout.addWidget(self.addButton)
        layout.addStretch()

    def _syncSpin(self, value: int) -> None:
        self.countSpin.blockSignals(True)
        self.countSpin.setValue(value)
        self.countSpin.blockSignals(False)

    def refresh(self) -> None:
        result = self.model.mnf
        if result is None:
            return
        count = result.eigenvalues.size
        self.components.setRange((1, count), value=self.model.components)
        self.countSpin.setRange(1, count)
        self._syncSpin(self.model.components)
        if self.plot is None or self.plot.property("eigenvalues") is not result:
            if self.plot is not None:
                self.plotHolder.removeWidget(self.plot)
                self.plot.deleteLater()
            self.plot = EigenvaluePlot(
                result.eigenvalues, self.components, logScale=False
            )
            self.plot.setProperty("eigenvalues", result)
            self.plotHolder.addWidget(self.plot)


class PurityPage(QWidget):
    def __init__(self, model: HourglassModel, run) -> None:
        super().__init__()
        self.model = model
        self.iterations = QSpinBox()
        self.iterations.setRange(100, 1_000_000)
        self.iterations.setSingleStep(1000)
        self.iterations.setValue(DEFAULT_PPI_ITERATIONS)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.1, 50.0)
        self.threshold.setSingleStep(0.5)
        self.threshold.setValue(DEFAULT_PPI_THRESHOLD)
        self.threshold.setToolTip("In MNF units (noise sigmas); 2-3 is typical")
        self.seed = QSpinBox()
        self.seed.setRange(0, 2**31 - 1)
        self.seed.setToolTip(
            "Picks the random skewers: the same seed and total iterations give "
            "the same counts"
        )
        self.runButton = _primary("Run PPI")
        self.runButton.clicked.connect(lambda: self._run(run, False))
        self.moreButton = QPushButton("Run More")
        self.moreButton.setToolTip(
            "Add more iterations of the same seed to the current counts (only "
            "while the threshold, seed and MNF components are unchanged)"
        )
        self.moreButton.clicked.connect(lambda: self._run(run, True))
        self.threshold.valueChanged.connect(self.refresh)
        self.seed.valueChanged.connect(self.refresh)
        self.minCount = QSpinBox()
        self.minCount.setRange(1, 1_000_000)
        self.minCount.setValue(model.minCount)
        self.maxCandidates = QSpinBox()
        self.maxCandidates.setRange(10, 100_000)
        self.maxCandidates.setSingleStep(500)
        self.maxCandidates.setValue(DEFAULT_MAX_CANDIDATES)
        for spin in (self.minCount, self.maxCandidates):
            spin.valueChanged.connect(self._applyRule)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.addButton = QPushButton("Add PPI Image to Project")

        form = QFormLayout()
        form.addRow("Iterations", self.iterations)
        form.addRow("Threshold", self.threshold)
        form.addRow("Seed", self.seed)
        buttons = QHBoxLayout()
        buttons.addWidget(self.runButton)
        buttons.addWidget(self.moreButton)
        candidates = QFormLayout()
        candidates.addRow("Min. PPI count", self.minCount)
        candidates.addRow("Max. pixels in n-D view", self.maxCandidates)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _hint(
                "Random projections of the kept MNF components count how often "
                "each pixel lands at an extreme. The highest-scoring pixels are "
                "the candidate pure pixels shown in the n-D view."
            )
        )
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addLayout(candidates)
        layout.addWidget(self.summary)
        layout.addWidget(self.addButton)
        layout.addStretch()

    def _applyRule(self) -> None:
        if self.model.ppi is not None:
            self.model.setCandidateRule(
                self.minCount.value(), self.maxCandidates.value()
            )
        self.refresh()

    def _run(self, run, resume: bool) -> None:
        run(self.iterations.value(), self.threshold.value(), self.seed.value(), resume)

    def refresh(self) -> None:
        ppi = self.model.ppi
        self.moreButton.setEnabled(
            self.model.canRunMore(self.threshold.value(), self.seed.value())
        )
        if ppi is None:
            return
        candidates = self.model.candidatePixels().size
        seed = "unknown seed" if ppi.seed is None else f"seed {ppi.seed}"
        self.summary.setText(
            f"{ppi.iterations:,} iterations ({seed}, threshold {ppi.threshold:g}): "
            f"{int((ppi.counts > 0).sum()):,} pixels were ever extreme; "
            f"{candidates:,} go to the n-D view."
        )


class EndmembersPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(
            _hint(
                "In the n-D view, press Play to tour the candidate pixels "
                "through the kept MNF dimensions. Pure endmembers sit at the "
                "corners of the cloud.<br><br>"
                "• Drag a lasso around a corner to add it to the active "
                "endmember (Shift-drag removes).<br>"
                "• Suggest finds corners of the cloud (ATGP) and groups the "
                "candidates near each one, as a starting point.<br>"
                "• Collapse classes hides the corners you have, revealing the next.<br>"
                "• A real endmember stays together as the view rotates; mean "
                "spectra appear in the Spectra panel."
            )
        )
        layout.addStretch()


class MapPage(QWidget):
    def __init__(self, run) -> None:
        super().__init__()
        self.group = QButtonGroup(self)
        self.buttons: dict[MapMethod, QRadioButton] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(
            _hint(
                "Map the endmembers across the whole scene. Results join the "
                "project's images and appear in the scene overlay."
            )
        )
        for method in MapMethod:
            button = QRadioButton(method.value)
            self.group.addButton(button)
            self.buttons[method] = button
            layout.addWidget(button)
        self.buttons[MapMethod.SAM].setChecked(True)
        self.maxAngle = QDoubleSpinBox()
        self.maxAngle.setRange(0.001, 1.5)
        self.maxAngle.setDecimals(3)
        self.maxAngle.setSingleStep(0.01)
        self.maxAngle.setValue(DEFAULT_SAM_MAX_ANGLE)
        self.maxAngle.setSuffix(" rad")
        self.constraint = QComboBox()
        for constraint in Constraint:
            self.constraint.addItem(constraint.value, constraint)
        self.constraint.setCurrentIndex(
            list(Constraint).index(Constraint.FULLY_CONSTRAINED)
        )
        form = QFormLayout()
        form.addRow("SAM max. angle", self.maxAngle)
        form.addRow("Unmixing", self.constraint)
        layout.addLayout(form)
        layout.addWidget(
            _hint(
                "SAM: class + angle per endmember (smaller is closer). Unmixing: "
                "abundance per endmember + RMS error (structure in it means a "
                "missing endmember). MTMF: matched-filter score ≈ abundance, and "
                "infeasibility; trust high scores only where infeasibility is low."
            )
        )
        self.runButton = _primary("Map Endmembers")
        self.runButton.clicked.connect(lambda: run(self.method()))
        layout.addWidget(self.runButton)
        self.review = MtmfReview()
        self.review.setVisible(False)
        self.reviewHint = _hint(
            "Review: drag the lines to keep pixels with a high score and low "
            "infeasibility; they light up in the scene."
        )
        self.reviewHint.setVisible(False)
        layout.addWidget(self.reviewHint)
        layout.addWidget(self.review, stretch=1)
        layout.addStretch()

    def showReview(self, result) -> None:
        self.review.setResult(result)
        self.review.setVisible(True)
        self.reviewHint.setVisible(True)

    def method(self) -> MapMethod:
        return next(m for m, b in self.buttons.items() if b.isChecked())


class PipelinePanel(QToolBox):
    """Accordion of the six steps. The workspace supplies the actions. Click
    any step to go back to it; re-running it marks later steps as needing a
    re-run, and "Start over" clears them."""

    sigStartOver = pyqtSignal(object)  # Step

    def __init__(
        self,
        model: HourglassModel,
        runner: TaskRunner,
        *,
        prepare,
        reduce,
        purity,
        mapEndmembers,
        identifyPanel: IdentifyPanel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMinimumWidth(360)
        self.model = model
        self.preparePage = PreparePage(model, prepare)
        self.reducePage = ReducePage(model, reduce)
        self.purityPage = PurityPage(model, purity)
        self.endmembersPage = EndmembersPage()
        self.identifyPanel = identifyPanel
        self.mapPage = MapPage(mapEndmembers)
        self._pages = {
            Step.PREPARE: self.preparePage,
            Step.REDUCE: self.reducePage,
            Step.PURITY: self.purityPage,
            Step.ENDMEMBERS: self.endmembersPage,
            Step.IDENTIFY: identifyPanel,
            Step.MAP: self.mapPage,
        }
        self.startOverButtons: dict[Step, QPushButton] = {}
        for step, page in self._pages.items():
            button = QPushButton("↺ Start over from this step")
            button.setFlat(True)
            button.setToolTip(
                "Clear this step's result and every later one, then begin again "
                "here. Endmember classes are kept unless you start over from "
                "Endmembers (which can be undone)."
            )
            button.clicked.connect(lambda _=False, st=step: self.sigStartOver.emit(st))
            self.startOverButtons[step] = button
            container = QWidget()
            column = QVBoxLayout(container)
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(page, stretch=1)
            column.addWidget(button)
            self.addItem(container, "")
        self._runButtons = [
            self.preparePage.runButton,
            self.reducePage.runButton,
            self.purityPage.runButton,
            self.purityPage.moreButton,
            self.mapPage.runButton,
        ]
        # the model is plain psygnal state: drop these once the panel is deleted
        connectWhileAlive(model.sigStepChanged, self._onStepChanged, self)
        connectWhileAlive(model.sigEndmembersChanged, self.refresh, self)
        connectWhileAlive(model.sigCandidatesChanged, self.refresh, self)
        runner.sigBusyChanged.connect(self._onBusy)
        self.refresh()

    def showStep(self, step: Step) -> None:
        self.setCurrentIndex(list(self._pages).index(step))

    def _onBusy(self, busy: bool) -> None:
        for button in self._runButtons:
            button.setEnabled(not busy)
        if not busy:
            self.refresh()

    def _onStepChanged(self, _step: Step) -> None:
        self.refresh()

    def refresh(self) -> None:
        for index, step in enumerate(self._pages):
            state = self.model.state(step)
            suffix = "  — needs re-running" if state is StepState.STALE else ""
            self.setItemText(
                index, f"{_MARKS[state]}  {index + 1}. {step.value}{suffix}"
            )
        model = self.model
        self.reducePage.runButton.setEnabled(model.prepared is not None)
        self.purityPage.runButton.setEnabled(model.mnf is not None)
        self.mapPage.runButton.setEnabled(len(model.usableEndmembers()) >= 2)
        self.preparePage.refresh()
        self.reducePage.refresh()
        self.purityPage.refresh()
