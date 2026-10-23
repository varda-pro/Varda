"""The Varda busy indicator ("redraw"): the V's spectrum draws on, then
undraws from its start, forever. Painted with QPainter from
resources/brand/loader/loader-motion.json.

``RedrawSpinner`` is the widget. ``BusyDialog`` puts it beside a message,
with a percentage bar when the work reports progress, and replaces
QProgressDialog across the app. ``BusyOverlay`` lays it over one view or
panel while work for it runs in the background, leaving the rest of the
window usable.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from PyQt6.QtCore import QElapsedTimer, QEvent, QObject, QPointF, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QHideEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
    QShowEvent,
)
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from varda.branding.splash import BRAND_DIR, cubicBezierEase, partialPolylines

LOADER_SPEC_PATH = BRAND_DIR / "loader" / "loader-motion.json"
SMALL_SIZE = 24  # px; at or below this, a thicker stroke and no track
_EASE = cubicBezierEase(0.65, 0.0, 0.35, 1.0)
_FRAME_MS = 16


@cache
def loadLoaderSpec(path: Path = LOADER_SPEC_PATH) -> dict:
    return json.loads(Path(path).read_text())


def visibleInterval(t: float, period: float) -> tuple[float, float]:
    """The visible arc-length interval of the V at time ``t``: it draws on over
    the first half of the period and undraws from its start over the second."""
    phase = (t % period) / period
    if phase < 0.5:
        return 0.0, _EASE(2 * phase)
    return _EASE(2 * phase - 1), 1.0


def isDark(palette: QPalette) -> bool:
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def _path(points: list[tuple[float, float]], scale: float) -> QPainterPath:
    path = QPainterPath(QPointF(points[0][0] * scale, points[0][1] * scale))
    for x, y in points[1:]:
        path.lineTo(x * scale, y * scale)
    return path


def paintRedraw(
    painter: QPainter, spec: dict, size: int, t: float, *, dark: bool
) -> None:
    """Paint one frame of the indicator into a ``size`` square at the origin."""
    scale = size / spec["viewBox"][2]
    small = size <= SMALL_SIZE
    width = (spec["strokeWidthAtOrBelow24px"] if small else spec["strokeWidth"]) * scale
    polyline = [tuple(p) for p in spec["polyline"]]
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    def pen(brush: QColor | QLinearGradient) -> QPen:
        result = QPen(brush, width)
        result.setCapStyle(Qt.PenCapStyle.RoundCap)
        result.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return result

    if not small:
        track = spec["track"]["onDark" if dark else "onLight"]
        color = QColor(track["color"])
        color.setAlphaF(track["opacity"])
        painter.strokePath(_path(polyline, scale), pen(color))

    f0, f1 = visibleInterval(t, spec["period"])
    gradient = QLinearGradient(
        spec["gradient"]["x1"] * scale, 0, spec["gradient"]["x2"] * scale, 0
    )
    for stop, color in spec["gradient"]["stops"]:
        gradient.setColorAt(stop, QColor(color))
    for piece in partialPolylines([polyline], f0, f1):
        painter.strokePath(_path(piece, scale), pen(gradient))
    painter.restore()


class RedrawSpinner(QWidget):
    """The animated V at a fixed square size; it runs only while visible."""

    def __init__(self, size: int = 48, parent: QWidget | None = None):
        super().__init__(parent)
        self._size = size
        self._spec = loadLoaderSpec()
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self.update)
        self.setFixedSize(size, size)
        self.setAccessibleName("Working")

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    def isAnimating(self) -> bool:
        return self._timer.isActive()

    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        self._clock.start()
        self._timer.start()

    def hideEvent(self, a0: QHideEvent | None) -> None:
        super().hideEvent(a0)
        self._timer.stop()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        painter = QPainter(self)
        t = self._clock.elapsed() / 1000 if self._clock.isValid() else 0.0
        paintRedraw(painter, self._spec, self._size, t, dark=isDark(self.palette()))
        painter.end()


class BusyDialog(QDialog):
    """A message beside the redraw indicator. ``setValue`` adds a percentage
    bar for work that reports progress; without it the dialog just shows that
    something is happening."""

    def __init__(
        self,
        message: str,
        *,
        title: str = "Working",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.label = QLabel(message)
        self.label.setWordWrap(True)
        self.label.setMinimumWidth(280)
        self.progressBar = QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressBar.setVisible(False)
        text = QVBoxLayout()
        text.addStretch()
        text.addWidget(self.label)
        text.addWidget(self.progressBar)
        text.addStretch()
        layout = QHBoxLayout(self)
        layout.setSpacing(16)
        layout.addWidget(RedrawSpinner(48, self))
        layout.addLayout(text)
        self._closed = False

    def setLabelText(self, text: str) -> None:
        self.label.setText(text)

    def setValue(self, percent: int) -> None:
        self.progressBar.setVisible(True)
        self.progressBar.setValue(percent)

    def showAfter(self, delayMs: int) -> None:
        """Show the dialog only if the work is still going after ``delayMs``."""
        if delayMs <= 0:
            self.show()
        else:
            QTimer.singleShot(delayMs, self._showIfOpen)

    def _showIfOpen(self) -> None:
        if not self._closed:
            self.show()

    def done(self, a0: int) -> None:
        self._closed = True
        super().done(a0)

    def close(self) -> bool:
        self._closed = True
        return super().close()


class BusyOverlay(QWidget):
    """The indicator and a message over ``target`` (a view or panel) while
    work for it runs in the background. ``start`` shows it after
    ``delayMs`` (quick work shows nothing) and can change the message as the
    work moves on; ``stop`` hides it."""

    DELAY_MS = 300
    BACKDROP_ALPHA = 200  # of 255: the view stays faintly visible under it

    def __init__(self, target: QWidget, delayMs: int = DELAY_MS):
        super().__init__(target)
        self._target = target
        self.spinner = RedrawSpinner(48, self)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.label)
        layout.addStretch()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delayMs)
        self._timer.timeout.connect(self._appear)
        self._busy = False
        target.installEventFilter(self)
        self.hide()

    def isBusy(self) -> bool:
        return self._busy

    def message(self) -> str:
        return self.label.text()

    def start(self, message: str) -> None:
        self.label.setText(message)
        if not self._busy:
            self._busy = True
            self._timer.start()

    def stop(self) -> None:
        self._busy = False
        self._timer.stop()
        self.hide()

    def _appear(self) -> None:
        if self._busy:
            self.setGeometry(self._target.rect())
            self.show()
            self.raise_()

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a0 is self._target and a1 is not None:
            if a1.type() == QEvent.Type.Resize:
                self.setGeometry(self._target.rect())
            elif a1.type() == QEvent.Type.ChildAdded and self.isVisible():
                QTimer.singleShot(0, self.raise_)  # stay above the view's own
        return super().eventFilter(a0, a1)

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        backdrop = QColor(self.palette().color(QPalette.ColorRole.Window))
        backdrop.setAlpha(self.BACKDROP_ALPHA)
        painter = QPainter(self)
        painter.fillRect(self.rect(), backdrop)
        painter.end()
