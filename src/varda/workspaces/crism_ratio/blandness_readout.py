"""The denominator's blandness readout: is this spectrum bland (featureless)
enough to divide by? A compact strip: the chosen check, the limits (floor, k,
slope) and the CO₂ exclusion, which the user can change, on one wrapping row;
a one-line verdict naming each failing window's dip and limit; and, behind
Details, one row per wavelength window of the check with where its deepest
dip is and how deep, the noise-aware limit, the slope and a mark."""

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
    QToolButton,
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


def failureText(w: WindowResult) -> str:
    """Why a window fails, e.g. ``1.7–2.65 µm, 3.9 % dip at 1948 nm (limit
    3.0 %)``, or ``…, slope 0.91 /µm (limit 0.75)``."""
    reasons = []
    if not w.depthPassed:
        reasons.append(
            f"{_percent(w.depth)} dip at {w.dipNm:.0f} nm "
            f"(limit {_percent(w.depthLimit)})"
        )
    if not w.slopePassed:
        reasons.append(f"slope {w.slope:.2f} /µm (limit {w.maxSlope:g})")
    return ", ".join([w.window.name, *reasons])


def verdictText(result: BlandnessResult) -> str:
    """The one-line verdict: ✓, or ✗ with each failing window's reason."""
    if not result.windows:
        return result.note
    if result.passed:
        text = "✓ Bland enough to divide by"
    else:
        failing = "; ".join(failureText(w) for w in result.windows if not w.passed)
        text = f"✗ Not bland: {failing}"
    return f"{text}. {result.note}" if result.note else text


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
        # the check, the limits and the CO₂ exclusion share one row, which
        # wraps when the panel is narrow; the row widget owns them from the start
        self.limitsRow = QWidget(self)
        limits = FlowLayout(self.limitsRow)
        limits.addWidget(self.checkCombo)
        for key, label, scale, decimals, suffix, tip in _EDITORS:
            low, high = LIMIT_RANGES[key]
            box = QDoubleSpinBox()
            box.setRange(low * scale, high * scale)
            box.setDecimals(decimals)
            box.setSingleStep(5 * 10**-decimals)
            box.setSuffix(suffix)
            box.setValue(getattr(READOUT_LIMITS, key) * scale)
            box.setToolTip(tip)
            # a bound method (not a lambda or partial), so Qt disconnects it
            # when the readout is destroyed
            box.valueChanged.connect(self._onEdited)
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

        low, high = CO2_RESIDUAL_NM
        self.co2Check = QCheckBox(f"Leave out CO₂ ({low:g}–{high:g} nm)")
        self.co2Check.setChecked(True)
        self.co2Check.setToolTip(
            f"Leave out the CO₂ residual ({low:g}–{high:g} nm): atmospheric CO₂ "
            "left by the volcano-scan correction; uncheck for data with a "
            "better atmospheric correction"
        )
        self.co2Check.toggled.connect(self._evaluate)
        # in a plain row: a flow layout gives a macOS check box 2 px less
        # than its text needs (its layout margins don't round-trip)
        co2 = QWidget()
        co2Row = QHBoxLayout(co2)
        co2Row.setContentsMargins(0, 0, 0, 0)
        co2Row.addWidget(self.co2Check)
        limits.addWidget(co2)
        limits.addWidget(self.defaultsButton)

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
        # the per-window table, folded away until wanted
        self.detailsButton = QToolButton()
        self.detailsButton.setText("Details")
        self.detailsButton.setCheckable(True)
        self.detailsButton.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.detailsButton.setAutoRaise(True)
        self.detailsButton.toggled.connect(self.setDetailsVisible)
        verdictRow = QHBoxLayout()
        verdictRow.setContentsMargins(0, 0, 0, 0)
        verdictRow.addWidget(self.verdictLabel, 1)
        verdictRow.addWidget(self.detailsButton, 0, Qt.AlignmentFlag.AlignTop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        layout.addWidget(self.limitsRow)
        layout.addLayout(verdictRow)
        layout.addWidget(self.table)
        self.setDetailsVisible(False)

    def _horizontalHeader(self) -> QHeaderView:
        header = self.table.horizontalHeader()
        assert header is not None
        return header

    def _verticalHeader(self) -> QHeaderView:
        header = self.table.verticalHeader()
        assert header is not None
        return header

    def detailsVisible(self) -> bool:
        return self.detailsButton.isChecked()

    def setDetailsVisible(self, visible: bool) -> None:
        """Show or fold the per-window table."""
        self.detailsButton.setChecked(visible)
        self.detailsButton.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )
        self.detailsButton.setToolTip(
            "Hide the per-window table"
            if visible
            else "Show each window's dip, limit, slope and mark"
        )
        self.table.setVisible(visible)

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

    def _onEdited(self, shown: float) -> None:
        sender = self.sender()
        key, scale = next(
            (k, scale)
            for k, _, scale, *_rest in _EDITORS
            if self.editors.get(k) is sender
        )
        value = shown / scale
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
        self.verdictLabel.setText(verdictText(result))
        self.verdictLabel.setToolTip(
            ""
            if result.passed or not result.windows
            else "The ratio may show the denominator's structure, inverted, "
            "at the failing windows (shaded on the Spectra plot)."
        )
        self.sigVerdictChanged.emit(result)
