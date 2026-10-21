"""Go to Sensor: type the detector sample and line of the numerator (and of
the denominator) and the boxes jump there, as if clicked on the I/F view.
Non-modal, so positions can be typed one after another."""

from __future__ import annotations

import attrs
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from varda.common.ui import MutedLabel


@attrs.frozen
class SensorTargets:
    """Where to put the boxes: (sample, line) each; no denominator to leave
    it where it is."""

    numerator: tuple[int, int]
    denominator: tuple[int, int] | None


def _spinBox(maximum: int) -> QSpinBox:
    box = QSpinBox()
    box.setRange(0, max(maximum, 0))
    return box


class SensorGotoDialog(QDialog):
    sigGo = pyqtSignal(object)  # SensorTargets

    def __init__(
        self,
        maxSample: int,
        maxLine: int,
        sameColumn: bool,
        parent: QWidget | None = None,
    ) -> None:
        """``maxSample``/``maxLine``: the largest detector sample and line
        of the cube's geometry; ``sameColumn``: start with the denominator
        on the numerator's column (as the column lock places it)."""
        super().__init__(parent)
        self.setWindowTitle("Go to Sensor Position")
        self.numeratorSample, self.numeratorLine = (
            _spinBox(maxSample),
            _spinBox(maxLine),
        )
        self.denominatorSample = _spinBox(maxSample)
        self.denominatorLine = _spinBox(maxLine)
        self.withDenominator = QCheckBox("Place the denominator too")
        self.withDenominator.setChecked(True)
        self.sameColumn = QCheckBox("On the numerator's detector column")
        self.sameColumn.setChecked(sameColumn)
        self.sameColumn.setToolTip(
            "The denominator's sample is the numerator's: only its line is used "
            "(with Lock to sensor column on, the denominator always goes there)"
        )
        self.resultLabel = MutedLabel()
        self.resultLabel.setWordWrap(True)

        hint = MutedLabel(
            "Detector sample and line, 0-based, as the I/F cube's geometry gives "
            "them (an IN file's IR Sample and IR Line; an unprojected cube's "
            "columns and rows). Each box goes on the image pixel nearest that "
            "position."
        )
        hint.setWordWrap(True)
        form = QFormLayout()
        for label, sample, line in (
            ("Numerator", self.numeratorSample, self.numeratorLine),
            ("Denominator", self.denominatorSample, self.denominatorLine),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel("sample"))
            row.addWidget(sample)
            row.addWidget(QLabel("line"))
            row.addWidget(line)
            form.addRow(label, row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.goButton = buttons.addButton("Go", QDialogButtonBox.ButtonRole.ApplyRole)
        assert self.goButton is not None
        self.goButton.setDefault(True)
        self.goButton.clicked.connect(self._go)
        buttons.rejected.connect(self.close)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(self.withDenominator)
        layout.addWidget(self.sameColumn)
        layout.addWidget(self.resultLabel)
        layout.addWidget(buttons)

        self.withDenominator.toggled.connect(self._enable)
        self.sameColumn.toggled.connect(self._enable)
        self.numeratorSample.valueChanged.connect(self._followSample)
        self._enable()

    def setPositions(
        self,
        numerator: tuple[float, float] | None,
        denominator: tuple[float, float] | None,
    ) -> None:
        """Start from where the boxes are now (sensor positions, if known)."""
        if numerator is not None:
            self.numeratorSample.setValue(round(numerator[0]))
            self.numeratorLine.setValue(round(numerator[1]))
        if denominator is not None:
            self.denominatorSample.setValue(round(denominator[0]))
            self.denominatorLine.setValue(round(denominator[1]))
        self._followSample()

    def showResult(self, text: str) -> None:
        self.resultLabel.setText(text)

    def _enable(self) -> None:
        on = self.withDenominator.isChecked()
        self.sameColumn.setEnabled(on)
        self.denominatorLine.setEnabled(on)
        self.denominatorSample.setEnabled(on and not self.sameColumn.isChecked())
        self._followSample()

    def _followSample(self) -> None:
        if self.sameColumn.isChecked():
            self.denominatorSample.setValue(self.numeratorSample.value())

    def targets(self) -> SensorTargets:
        numerator = (self.numeratorSample.value(), self.numeratorLine.value())
        denominator = (
            (self.denominatorSample.value(), self.denominatorLine.value())
            if self.withDenominator.isChecked()
            else None
        )
        return SensorTargets(numerator, denominator)

    def _go(self) -> None:
        self.sigGo.emit(self.targets())
