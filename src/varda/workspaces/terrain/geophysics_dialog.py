"""The settings of Shape Geophysics: density ± σ, rotation period, the
reference potential, the tilt radius and the exact-evaluation limit, filled
from the body's cited values where Varda has them."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from varda.common.ui import MutedLabel
from varda.geo.bodies import Body
from varda.shapes.geophysics import GeophysicsSettings
from varda.shapes.gravity import G

U_REFS = {
    "Area-weighted mean (as ALTWG)": "area-weighted mean",
    "Minimum": "minimum",
}
SECONDS_PER_PLATE_PAIR = 5e-9  # measured on 14 cores; laptops take 2–4 times longer


class GeophysicsDialog(QDialog):
    """``settings()`` is None while a value is invalid (and Run is off)."""

    def __init__(
        self, body: Body | None, plateCount: int, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.setWindowTitle("Shape Geophysics")
        self.plateCount = plateCount
        self.density = QDoubleSpinBox()
        self.density.setRange(0.0, 20000.0)
        self.density.setDecimals(1)
        self.density.setSuffix(" kg/m³")
        self.sigma = QDoubleSpinBox()
        self.sigma.setRange(0.0, 20000.0)
        self.sigma.setDecimals(1)
        self.sigma.setSuffix(" kg/m³")
        self.sigma.setToolTip(
            "1σ of the bulk density: slope and elevation are also given at "
            "ρ − σ and ρ + σ"
        )
        self.period = QDoubleSpinBox()
        self.period.setRange(0.0, 10000.0)
        self.period.setDecimals(6)
        self.period.setSuffix(" h")
        self.period.setToolTip("Sidereal rotation period, about the body's +Z axis")
        self.uRef = QComboBox()
        self.uRef.addItems(list(U_REFS))
        self.uRef.setToolTip(
            "The reference potential geopotential elevations are measured from"
        )
        self.tiltRadius = QDoubleSpinBox()
        self.tiltRadius.setRange(0.0, 1e6)
        self.tiltRadius.setDecimals(1)
        self.tiltRadius.setSuffix(" m")
        self.tiltRadius.setToolTip(
            "Tilt uses the mean normal of the plates within this distance; 0 uses "
            "each plate's own normal"
        )
        self.maxExact = QSpinBox()
        self.maxExact.setRange(1000, 5_000_000)
        self.maxExact.setSingleStep(10_000)
        self.maxExact.setValue(200_000)
        self.maxExact.setSuffix(" plates")
        self.maxExact.setToolTip(
            "Larger models are evaluated on a decimated model of at most this "
            "many plates and carried to every plate"
        )
        self.sources = MutedLabel()
        self.sources.setWordWrap(True)
        self.estimate = MutedLabel()
        self.estimate.setWordWrap(True)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.runButton = QPushButton("Run")
        buttons.addButton(self.runButton, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout(self)
        form.addRow("Density:", self.density)
        form.addRow("Density σ:", self.sigma)
        form.addRow("Rotation period:", self.period)
        form.addRow("Reference potential:", self.uRef)
        form.addRow("Tilt radius:", self.tiltRadius)
        form.addRow("Evaluate exactly up to:", self.maxExact)
        form.addRow(self.sources)
        form.addRow(self.estimate)
        form.addRow(self.problem)
        form.addRow(buttons)
        self._fill(body)
        for box in (self.density, self.sigma, self.period, self.tiltRadius):
            box.valueChanged.connect(lambda _: self._validate())
        self.maxExact.valueChanged.connect(lambda _: self._validate())
        self._validate()

    def _fill(self, body: Body | None) -> None:
        notes = []
        if body is not None and body.density is not None:
            self.density.setValue(body.density[0])
            self.sigma.setValue(body.density[1])
            notes.append(f"Density: {body.sources.get('density', '')}")
        elif body is not None:
            notes.append(
                f"Varda has no density for {body.name}"
                + (f" ({body.sources['density']})" if "density" in body.sources else "")
                + ": enter one with its source."
            )
        if body is not None and body.rotationPeriodH is not None:
            self.period.setValue(body.rotationPeriodH)
            notes.append(f"Rotation period: {body.sources.get('rotationPeriodH', '')}")
        elif body is not None:
            notes.append(f"Varda has no rotation period for {body.name}: enter one.")
        if body is None:
            notes.append("No body is set: enter the density and the rotation period.")
        notes.append(f"G = {G:.5e} m³ kg⁻¹ s⁻² (CODATA 2018).")
        self.sources.setText("\n".join(notes))

    def _validate(self) -> None:
        problems = []
        if self.density.value() <= 0:
            problems.append("The density must be positive.")
        elif not self.sigma.value() < self.density.value():
            problems.append("The density's σ must be less than the density.")
        if self.period.value() <= 0:
            problems.append("The rotation period must be positive.")
        self.problem.setText(" ".join(problems))
        self.runButton.setEnabled(not problems)
        evaluated = min(self.plateCount, self.maxExact.value())
        seconds = 2.5 * evaluated**2 * SECONDS_PER_PLATE_PAIR
        self.estimate.setText(
            f"Gravity at {evaluated:,} of {self.plateCount:,} plates: about "
            f"{_duration(seconds)} on a fast desktop, longer on a laptop."
        )

    def settings(self) -> GeophysicsSettings | None:
        if not self.runButton.isEnabled():
            return None
        return GeophysicsSettings(
            density=self.density.value(),
            densitySigma=self.sigma.value(),
            rotationPeriodH=self.period.value(),
            uRef=U_REFS[self.uRef.currentText()],  # ty: ignore[invalid-argument-type]
            tiltRadiusM=self.tiltRadius.value(),
            maxExactPlates=self.maxExact.value(),
        )


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{max(seconds, 1):.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"
