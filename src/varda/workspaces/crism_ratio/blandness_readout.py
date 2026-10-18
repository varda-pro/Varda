"""The blandness readout under the Denominator plot: is this spectrum bland
(featureless) enough to divide by? One row per check with its value, limit and
a mark, an overall verdict, and limits the user can edit."""

from __future__ import annotations

import attrs
import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from varda.analysis.blandness import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_SLOPE,
    PRESETS,
    BlandnessResult,
    ParameterFunction,
    genericBlandness,
    hypyrameterValues,
    presetBlandness,
)

GENERIC = "Generic"
_VALUE, _LIMIT, _MARK = 1, 2, 3
_MARKS = {True: "✓", False: "✗"}


class BlandnessReadout(QWidget):
    sigVerdictChanged = pyqtSignal(object)  # BlandnessResult

    def __init__(
        self,
        parameters: ParameterFunction = hypyrameterValues,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._parameters = parameters
        self._wavelengths: np.ndarray | None = None
        self._spectrum: np.ndarray | None = None
        self._overrides: dict[str, float] = {}  # edited limits by check name
        self.lastResult = BlandnessResult(())

        self.checkCombo = QComboBox()
        self.checkCombo.addItems([GENERIC, *PRESETS])
        self.checkCombo.setToolTip(
            "Which check decides whether the denominator is bland enough: the "
            "generic depth and slope, or the parameters of a target mineral"
        )
        self.checkCombo.currentIndexChanged.connect(self._onCheckChanged)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Check", "Value", "Limit (≤)", ""])
        self._verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setToolTip("Double-click a limit to edit it")
        header = self._horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (_VALUE, _LIMIT, _MARK):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._onItemChanged)

        self.verdictLabel = QLabel()
        self.verdictLabel.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.checkCombo)
        layout.addWidget(self.table)
        layout.addWidget(self.verdictLabel)

    def _horizontalHeader(self) -> QHeaderView:
        header = self.table.horizontalHeader()
        assert header is not None
        return header

    def _verticalHeader(self) -> QHeaderView:
        header = self.table.verticalHeader()
        assert header is not None
        return header

    def currentCheckName(self) -> str:
        return self.checkCombo.currentText()

    def setSpectrum(
        self, wavelengthsNm: np.ndarray, spectrum: np.ndarray
    ) -> BlandnessResult:
        self._wavelengths, self._spectrum = wavelengthsNm, spectrum
        return self._evaluate()

    def setUnavailable(self, note: str) -> BlandnessResult:
        """No verdict, and why (e.g. the image has no wavelengths)."""
        self._wavelengths = self._spectrum = None
        self._show(BlandnessResult((), note=note))
        return self.lastResult

    def _onCheckChanged(self) -> None:
        self._overrides.clear()
        self._evaluate()

    def _evaluate(self) -> BlandnessResult:
        if self._wavelengths is None or self._spectrum is None:
            return self.lastResult
        name = self.currentCheckName()
        if name == GENERIC:
            result = genericBlandness(
                self._spectrum,
                self._wavelengths,
                self._overrides.get("Absorption depth", DEFAULT_MAX_DEPTH),
                self._overrides.get("Slope", DEFAULT_MAX_SLOPE),
            )
        else:
            preset = PRESETS[name]
            preset = attrs.evolve(
                preset,
                limits=tuple(
                    attrs.evolve(p, maximum=self._overrides.get(p.name, p.maximum))
                    for p in preset.limits
                ),
                maxSlope=self._overrides.get("Slope", preset.maxSlope),
            )
            result = presetBlandness(
                self._spectrum, self._wavelengths, preset, self._parameters
            )
        self._show(result)
        return result

    def _show(self, result: BlandnessResult) -> None:
        self.lastResult = result
        self.table.blockSignals(True)
        self.table.setRowCount(len(result.checks))
        for row, check in enumerate(result.checks):
            cells = (
                (check.name, False),
                (f"{check.value:.4g}", False),
                (f"{check.limit:.4g}", True),
                (_MARKS[check.passed], False),
            )
            for column, (text, editable) in enumerate(cells):
                item = QTableWidgetItem(text)
                flags = Qt.ItemFlag.ItemIsEnabled
                if editable:
                    flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                self.table.setItem(row, column, item)
        self.table.blockSignals(False)
        self.table.setFixedHeight(
            self._horizontalHeader().height()
            + sum(self.table.rowHeight(r) for r in range(self.table.rowCount()))
            + 2 * self.table.frameWidth()
            + 4
        )
        if result.note:
            self.verdictLabel.setText(result.note)
        elif result.passed:
            self.verdictLabel.setText("✓ Bland enough to divide by")
        else:
            self.verdictLabel.setText("✗ Not bland: the ratio may show its structure")
        self.sigVerdictChanged.emit(result)

    def _onItemChanged(self, item: QTableWidgetItem) -> None:
        if item.column() != _LIMIT:
            return
        check = self.lastResult.checks[item.row()]
        try:
            limit = float(item.text())
            if not np.isfinite(limit) or limit < 0:
                raise ValueError(item.text())
        except ValueError:
            self.table.blockSignals(True)
            item.setText(f"{check.limit:.4g}")
            self.table.blockSignals(False)
            return
        self._overrides[check.name] = limit
        self._evaluate()
