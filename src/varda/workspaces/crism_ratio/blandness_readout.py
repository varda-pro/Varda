"""The blandness readout under the Denominator plot: is this spectrum bland
(featureless) enough to divide by? One row per wavelength window of the chosen
check, with where its deepest dip is and how deep, the noise-aware limit, the
slope and a mark; an overall verdict; and the limits (floor, k, slope) and
the CO₂ exclusion, which the user can change."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from varda.analysis.blandness import (
    CHECKS,
    CO2_RESIDUAL_NM,
    DEFAULT_EXCLUSIONS,
    GENERIC,
    LIMIT_RANGES,
    READOUT_LIMITS,
    BlandnessResult,
    Limits,
    WindowResult,
    assess,
    formatExclusions,
)
from varda.common.ui import FlowLayout

_MARKS = {True: "✓", False: "✗"}
_HEADERS = ["Window", "Range (nm)", "Deepest dip", "Limit", "Slope /µm", ""]
_DIP, _LIMIT, _SLOPE, _MARK = 2, 3, 4, 5
# the editable limits: (key, label, scale shown, decimals, suffix, tooltip)
_EDITORS = (
    (
        "floor",
        "Floor",
        100.0,
        1,
        " %",
        "Absolute floor: a dip shallower than this never fails",
    ),
    (
        "noiseMultiple",
        "k",
        1.0,
        1,
        "",
        "A dip within k × the spectrum's own per-band noise never fails",
    ),
    (
        "maxSlope",
        "Slope ≤",
        1.0,
        2,
        " /µm",
        "Largest |relative slope| allowed in a window",
    ),
)
_EDITED_NAMES = {"floor": "floor", "noiseMultiple": "k", "maxSlope": "slope"}


def _percent(value: float) -> str:
    return "—" if not np.isfinite(value) else f"{100 * value:.1f} %"


def dipText(w: WindowResult) -> str:
    """e.g. ``2.7 % at 1920 nm`` (— when the window was not measured)."""
    if not np.isfinite(w.depth):
        return "—"
    return f"{_percent(w.depth)} at {w.dipNm:.0f} nm"


class BlandnessReadout(QWidget):
    sigVerdictChanged = pyqtSignal(object)  # BlandnessResult

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wavelengths: np.ndarray | None = None
        self._spectrum: np.ndarray | None = None
        self._overrides: dict[str, float] = {}  # edited limits by key
        self.lastResult = BlandnessResult(GENERIC)

        self.checkCombo = QComboBox()
        self.checkCombo.addItems(list(CHECKS))
        self.checkCombo.setToolTip(
            "Which wavelength windows must be free of absorptions: broad "
            "Generic windows, or the windows of a target mineral group's bands"
        )
        self.checkCombo.currentIndexChanged.connect(self._evaluate)

        self.editors: dict[str, QDoubleSpinBox] = {}
        limits = FlowLayout()
        for key, label, scale, decimals, suffix, tip in _EDITORS:
            low, high = LIMIT_RANGES[key]
            box = QDoubleSpinBox()
            box.setRange(low * scale, high * scale)
            box.setDecimals(decimals)
            box.setSingleStep(5 * 10**-decimals)
            box.setSuffix(suffix)
            box.setValue(getattr(READOUT_LIMITS, key) * scale)
            box.setToolTip(tip)
            box.valueChanged.connect(lambda v, k=key, s=scale: self._onEdited(k, v / s))
            self.editors[key] = box
            pair = QWidget()
            row = QHBoxLayout(pair)
            row.setContentsMargins(0, 0, 0, 0)
            caption = QLabel(label)
            caption.setToolTip(tip)
            row.addWidget(caption)
            row.addWidget(box)
            limits.addWidget(pair)
        self.defaultsButton = QPushButton("Defaults")
        self.defaultsButton.setToolTip("Put the default limits back")
        self.defaultsButton.clicked.connect(self.resetLimits)
        limits.addWidget(self.defaultsButton)

        low, high = CO2_RESIDUAL_NM
        self.co2Check = QCheckBox(f"Leave out the CO₂ residual ({low:g}–{high:g} nm)")
        self.co2Check.setChecked(True)
        self.co2Check.setToolTip(
            "Atmospheric CO₂ left by the volcano-scan correction; uncheck for "
            "data with a better atmospheric correction"
        )
        self.co2Check.toggled.connect(self._evaluate)

        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self._verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(_HEADERS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        self.verdictLabel = QLabel()
        self.verdictLabel.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.checkCombo)
        layout.addLayout(limits)
        layout.addWidget(self.co2Check)
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

    def limits(self) -> Limits:
        return Limits(
            self._overrides.get("floor", READOUT_LIMITS.floor),
            self._overrides.get("noiseMultiple", READOUT_LIMITS.noiseMultiple),
            self._overrides.get("maxSlope", READOUT_LIMITS.maxSlope),
        )

    def exclusions(self) -> tuple[tuple[float, float], ...]:
        if self.co2Check.isChecked():
            return DEFAULT_EXCLUSIONS
        return tuple(r for r in DEFAULT_EXCLUSIONS if r != CO2_RESIDUAL_NM)

    def limitOverrides(self) -> dict[str, float]:
        """The limits the user changed from the defaults, by key (``floor``,
        ``noiseMultiple``, ``maxSlope``)."""
        return dict(self._overrides)

    def setLimitOverrides(self, overrides: dict[str, float]) -> None:
        """Changed limits (e.g. from a session); unknown keys and values out
        of range are ignored."""
        self._overrides = {
            key: float(value)
            for key, value in overrides.items()
            if key in LIMIT_RANGES
            and isinstance(value, (int, float))
            and LIMIT_RANGES[key][0] <= value <= LIMIT_RANGES[key][1]
        }
        self._syncEditors()
        self._evaluate()

    def resetLimits(self) -> None:
        self.setLimitOverrides({})

    def _syncEditors(self) -> None:
        limits = self.limits()
        for key, _, scale, *_rest in _EDITORS:
            box = self.editors[key]
            box.blockSignals(True)
            box.setValue(getattr(limits, key) * scale)
            box.blockSignals(False)

    def _onEdited(self, key: str, value: float) -> None:
        if np.isclose(value, getattr(READOUT_LIMITS, key)):
            self._overrides.pop(key, None)
        else:
            self._overrides[key] = value
        self._evaluate()

    def describe(self) -> str:
        """The verdict with every window's dip, limit and slope and the limits
        used, for the description of what is saved ("" with no verdict)."""
        result = self.lastResult
        if not result.windows or result.limits is None:
            return ""
        limits = result.limits
        edited = [_EDITED_NAMES[k] for k in _EDITED_NAMES if k in self._overrides]
        rows = "; ".join(
            f"{w.window.name} {w.window.lowNm:g}–{w.window.highNm:g} nm: dip "
            f"{dipText(w)} (limit {_percent(w.depthLimit)}, noise "
            f"{_percent(w.noise)}), slope {w.slope:.3g} /µm {_MARKS[w.passed]}"
            for w in result.windows
        )
        verdict = "bland" if result.passed else "not bland"
        return (
            f"Denominator blandness, {result.check} check: "
            f"{_MARKS[result.passed]} {verdict}. Limits: dip ≤ max("
            f"{_percent(limits.floor)}, {limits.noiseMultiple:g} × noise), "
            f"|slope| ≤ {limits.maxSlope:g} /µm"
            f"{' (edited: ' + ', '.join(edited) + ')' if edited else ''}; excluded "
            f"{formatExclusions(result.exclusions) or 'none'} nm. {rows}."
        )

    def setSpectrum(
        self, wavelengthsNm: np.ndarray, spectrum: np.ndarray
    ) -> BlandnessResult:
        self._wavelengths, self._spectrum = wavelengthsNm, spectrum
        return self._evaluate()

    def setUnavailable(self, note: str) -> BlandnessResult:
        """No verdict, and why (e.g. the image has no wavelengths)."""
        self._wavelengths = self._spectrum = None
        self._show(BlandnessResult(self.currentCheckName(), note=note))
        return self.lastResult

    def _evaluate(self) -> BlandnessResult:
        if self._wavelengths is None or self._spectrum is None:
            return self.lastResult
        result = assess(
            self._spectrum,
            self._wavelengths,
            CHECKS[self.currentCheckName()],
            self.limits(),
            self.exclusions(),
        )
        self._show(result)
        return result

    def _show(self, result: BlandnessResult) -> None:
        self.lastResult = result
        self.table.setRowCount(len(result.windows))
        for row, w in enumerate(result.windows):
            limits = result.limits
            assert limits is not None
            cells = (
                (w.window.name, ""),
                (f"{w.window.lowNm:g}–{w.window.highNm:g}", ""),
                (dipText(w), f"{w.bands} bands measured"),
                (
                    _percent(w.depthLimit),
                    f"max(floor {_percent(limits.floor)}, {limits.noiseMultiple:g}"
                    f" × noise {_percent(w.noise)})",
                ),
                (
                    "—" if not np.isfinite(w.slope) else f"{w.slope:.2f}",
                    f"|slope| ≤ {limits.maxSlope:g} /µm",
                ),
                (_MARKS[w.passed], ""),
            )
            for column, (text, tip) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip(tip)
                self.table.setItem(row, column, item)
        self.table.setFixedHeight(
            self._horizontalHeader().height()
            + sum(self.table.rowHeight(r) for r in range(self.table.rowCount()))
            + 2 * self.table.frameWidth()
            + 4
        )
        failing = [w.window.name for w in result.windows if not w.passed]
        if result.note and not result.windows:
            self.verdictLabel.setText(result.note)
        elif result.passed:
            self.verdictLabel.setText("✓ Bland enough to divide by")
        else:
            self.verdictLabel.setText(
                f"✗ Not bland ({', '.join(failing)}): the ratio may show its "
                f"structure, inverted. {result.note}".strip()
            )
        self.sigVerdictChanged.emit(result)
