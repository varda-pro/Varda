"""What the window shows before any workspace is open: how to begin.

A short list of ways in (import images, open or restore a session, find data)
standing on a reflectance spectrum drawn in the brand's spectral ramp, with
its dotted continuum, as in the wordmark. The whole window takes dropped
files; while files are dragged over it the spectrum's absorption bands deepen
and the headline says what a drop will do.

Ground and ink follow the brand rules (resources/brand/README.md): Ink with
Paper text when the system palette is dark, Paper with Ink text when it is
light. Poiret One is used only for the headline.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from varda.command_ids import IMPORT_IMAGE_ID, OPEN_SESSION_ID, RESTORE_LAST_SESSION_ID
from varda.common.keys import shortcutText
from varda.session_autosave import defaultAutosavePath
from varda.utilities.resources import resource_path

if TYPE_CHECKING:
    from varda.app import VardaApplication

FIND_DATA_ID = "varda.data.find_data"
NEXT_STEPS = (
    "Double-click the image in the Image List",
    "Turn on Pixel Select under the image",
    "Click a pixel to see its spectrum",
)
HEADLINE = "Begin with an image"
SPECTRAL_RAMP = (
    "#7A5CFF", "#4479FF", "#18B4E0", "#2DC285", "#F0BE2C", "#FF8838", "#EE4258", "#B4235F",
)  # fmt: skip
INK, PANEL, PAPER, TAGLINE = "#110D2B", "#1C1938", "#F6F4EF", "#D6D2EE"
COLUMN_WIDTH = (400, 560)  # px, the list's narrowest and widest
SPECTRUM_HEIGHT = (60, 170)  # px, the spectrum's shortest and tallest
SPECTRUM_GAP = 24  # px, below the spectrum and between it and the list
DRAG_DEPTH = 1.75  # how much deeper the absorption bands get under a drag
DRAG_MS = 220
# (center, width, depth) of each absorption band, as fractions of the width
BANDS = ((0.16, 0.035, 0.55), (0.43, 0.08, 0.30), (0.83, 0.045, 0.62))
_POIRET = "resources/brand/fonts/PoiretOne-Regular.ttf"
_poiretFamily: str | None = None


def headlineFamily() -> str | None:
    """Poiret One, loaded once; None if it can't be loaded."""
    global _poiretFamily
    if _poiretFamily is None:
        fontId = QFontDatabase.addApplicationFont(resource_path(_POIRET))
        families = QFontDatabase.applicationFontFamilies(fontId) if fontId >= 0 else []
        _poiretFamily = families[0] if families else ""
    return _poiretFamily or None


class Colors:
    """The panel's colors for a dark or a light system palette."""

    def __init__(self, dark: bool):
        self.dark = dark
        self.ground = QColor(INK if dark else PAPER)
        self.raised = QColor(PANEL if dark else "#ECE9F4")
        self.text = QColor(PAPER if dark else INK)
        self.muted = QColor(TAGLINE if dark else "#57527A")
        self.edge = QColor(self.muted)
        self.edge.setAlphaF(0.35)
        self.focus = QColor("#4479FF")

    @classmethod
    def forPalette(cls, palette: QPalette) -> Colors:
        return cls(palette.color(QPalette.ColorRole.Window).lightness() < 128)


def spectralRamp(x0: float, x1: float) -> QLinearGradient:
    gradient = QLinearGradient(x0, 0, x1, 0)
    for i, color in enumerate(SPECTRAL_RAMP):
        gradient.setColorAt(i / (len(SPECTRAL_RAMP) - 1), QColor(color))
    return gradient


def spectrumPath(rect: QRectF, depth: float = 1.0) -> QPainterPath:
    """A reflectance-like curve filling ``rect``: a gently rising continuum
    with Gaussian absorption bands (``BANDS``), deepened by ``depth``."""
    path = QPainterPath()
    steps = 240
    for i in range(steps + 1):
        t = i / steps
        continuum = 0.82 + 0.14 * t - 0.12 * (t - 0.5) ** 2
        value = continuum
        for center, width, bandDepth in BANDS:
            gauss = math.exp(-0.5 * ((t - center) / width) ** 2)
            value -= min(0.95, bandDepth * depth) * 0.6 * continuum * gauss
        point = QPointF(
            rect.left() + t * rect.width(), rect.bottom() - value * rect.height()
        )
        if i == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    return path


def keyNames(sequence: str) -> list[str]:
    """The keys of a shortcut as this platform shows them, one per cap:
    ["⇧", "⌘", "O"] on a Mac, ["Ctrl", "Shift", "O"] elsewhere."""
    text = shortcutText(sequence)
    if sys.platform == "darwin":
        return list(text)
    return text.split("+")


def autosaveSummary(path: Path, now: datetime | None = None) -> str | None:
    """ "4 images, saved yesterday at 4:12 PM" for the autosaved session, or
    None when there is none to restore."""
    if not path.exists():
        return None
    try:
        count = len(json.loads(path.read_text()).get("images", []))
    except (OSError, ValueError, AttributeError):
        count = None
    saved = datetime.fromtimestamp(path.stat().st_mtime)
    now = now or datetime.now()
    clock = saved.strftime("%I:%M %p").lstrip("0")
    days = (now.date() - saved.date()).days
    if days == 0:
        when = f"today at {clock}"
    elif days == 1:
        when = f"yesterday at {clock}"
    else:
        when = f"on {saved.strftime('%b')} {saved.day}"
    if count is None:
        return f"Your last work, saved {when}."
    images = "1 image" if count == 1 else f"{count} images"
    return f"{images}, saved {when}."


class BalancedLabel(QLabel):
    """Wrapped like a QLabel, but at the narrowest width that keeps the same
    number of lines (CSS's text-wrap: balance), so no line ends with a word
    stranded on it."""

    _WRAP = (
        Qt.AlignmentFlag.AlignLeft
        | Qt.AlignmentFlag.AlignTop
        | Qt.TextFlag.TextWordWrap
    )

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setWordWrap(True)

    def _height(self, width: int) -> int:
        metrics = self.fontMetrics()
        return metrics.boundingRect(
            QRect(0, 0, max(1, width), 100000), self._WRAP, self.text()
        ).height()

    def balancedWidth(self, width: int) -> int:
        lineHeight = self.fontMetrics().lineSpacing()
        lines = max(1, round(self._height(width) / lineHeight))
        if lines == 1:
            return width
        low, high = width // lines, width
        while low < high:
            middle = (low + high) // 2
            if round(self._height(middle) / lineHeight) <= lines:
                high = middle
            else:
                low = middle + 1
        return high

    def heightForWidth(self, a0: int) -> int:
        margins = self.contentsMargins()
        inner = a0 - margins.left() - margins.right()
        return (
            self._height(self.balancedWidth(inner)) + margins.top() + margins.bottom()
        )

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        painter.setFont(self.font())
        bounds = self.contentsRect()
        bounds.setWidth(self.balancedWidth(bounds.width()))
        painter.drawText(bounds, self._WRAP, self.text())


class KeyCaps(QWidget):
    """A shortcut drawn as small key caps."""

    def __init__(self, keys: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.keys = keys
        self.capFont = QFont(self.font())
        self.capFont.setPixelSize(12)
        metrics = QFontMetrics(self.capFont)
        self.widths = [max(22, metrics.horizontalAdvance(k) + 12) for k in keys]
        self.setFixedSize(sum(self.widths) + 4 * (len(keys) - 1) + 2, 24)
        self.setAccessibleName("+".join(keys))

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        colors = Colors.forPalette(self.palette())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self.capFont)
        x = 1.0
        for key, width in zip(self.keys, self.widths):
            cap = QRectF(x, 1.5, width, 21)
            painter.setPen(QPen(colors.edge, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(cap, 5, 5)
            painter.setPen(colors.muted)
            painter.drawText(cap, Qt.AlignmentFlag.AlignCenter, key)
            x += width + 4


def _drawGlyph(painter: QPainter, kind: str, pen: QPen, ground: QColor) -> None:
    """A 28 px line glyph: a stack of image bands, a folder, a clock turning
    back, a globe."""
    painter.setPen(pen)
    if kind == "import":
        for offset in (6.0, 3.0, 0.0):  # back to front, each hiding the one behind
            painter.setBrush(ground)
            painter.drawRoundedRect(QRectF(5 + offset, 11 - offset, 13, 13), 2.5, 2.5)
    elif kind == "session":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        folder = QPainterPath(QPointF(4, 9))
        for x, y in (
            (4, 22),
            (24, 22),
            (24, 11.5),
            (13.5, 11.5),
            (11.5, 8.5),
            (4, 8.5),
        ):
            folder.lineTo(x, y)
        painter.drawPath(folder)
    elif kind == "restore":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(5, 5.5, 18, 18), 100 * 16, 290 * 16)
        painter.drawPolyline([QPointF(14, 9.5), QPointF(14, 14.5), QPointF(17.5, 16.5)])
        painter.drawPolyline([QPointF(8.5, 3.5), QPointF(11.2, 6.6), QPointF(7.5, 8.2)])
    elif kind == "find":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(5, 5, 18, 18))
        painter.drawEllipse(QRectF(10, 5, 8, 18))
        painter.drawLine(QPointF(5, 14), QPointF(23, 14))


class ActionRow(QAbstractButton):
    """One way in: a glyph, a title, what it does, and its shortcut. The
    primary row sits on a raised ground and its glyph takes the spectral
    ramp. Clickable, focusable, and pressed with Return or Space."""

    def __init__(
        self,
        glyph: str,
        title: str,
        detail: str,
        keys: str = "",
        primary: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.glyph, self.primary = glyph, primary
        self.setText(title)
        self.setAccessibleName(title)
        self.setAccessibleDescription(detail)
        self.setToolTip(detail)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # as tall as its wrapped text needs at the width it gets
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.titleLabel = QLabel(title)
        titleFont = QFont(self.font())
        titleFont.setPixelSize(15)
        titleFont.setWeight(QFont.Weight.DemiBold)
        self.titleLabel.setFont(titleFont)
        self.detailLabel = BalancedLabel(detail)
        self.detailLabel.setObjectName("detail")
        detailFont = QFont(self.font())
        detailFont.setPixelSize(12)
        self.detailLabel.setFont(detailFont)
        for label in (self.titleLabel, self.detailLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(self.titleLabel)
        text.addWidget(self.detailLabel)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(56, 10, 16, 10)
        layout.setSpacing(14)
        layout.addLayout(text, 1)
        if keys:
            caps = KeyCaps(keyNames(keys))
            caps.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(caps, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._applyColors()

    def setDetail(self, detail: str) -> None:
        self.detailLabel.setText(detail)
        self.setAccessibleDescription(detail)
        self.setToolTip(detail)

    def sizeHint(self) -> QSize:
        layout = self.layout()
        return layout.sizeHint() if layout is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, a0: int) -> int:
        layout = self.layout()
        return layout.heightForWidth(a0) if layout is not None else -1

    def changeEvent(self, e: QEvent | None) -> None:
        super().changeEvent(e)
        if e is not None and e.type() == QEvent.Type.PaletteChange:
            self._applyColors()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, a0: QEvent | None) -> None:
        super().leaveEvent(a0)
        self.update()

    def keyPressEvent(self, e: QKeyEvent | None) -> None:
        if e is not None and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(e)

    def _applyColors(self) -> None:
        colors = Colors.forPalette(self.palette())
        self.titleLabel.setStyleSheet(f"color: {colors.text.name()};")
        self.detailLabel.setStyleSheet(f"color: {colors.muted.name()};")

    def paintEvent(self, e: QPaintEvent | None) -> None:
        colors = Colors.forPalette(self.palette())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.primary or self.underMouse() or self.isDown():
            fill = QColor(colors.raised)
            if not self.primary:
                fill.setAlphaF(0.6)
            if self.isDown():
                fill = fill.darker(108) if colors.dark else fill.darker(104)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(bounds, 12, 12)
        if self.hasFocus():
            painter.setPen(QPen(colors.focus, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(bounds.adjusted(1, 1, -1, -1), 11, 11)
        top = (self.height() - 28) / 2
        painter.translate(14, top)
        pen = QPen(QBrush(spectralRamp(4, 24)) if self.primary else colors.text, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        ground = colors.raised if (self.primary or self.underMouse()) else colors.ground
        _drawGlyph(painter, self.glyph, pen, ground)


class StartPanel(QWidget):
    """The window's first screen. ``dropCount`` > 0 while files are dragged
    over the window (``setDropCount``)."""

    def __init__(self, app: VardaApplication, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.dropCount = 0
        self._depth = 1.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(DRAG_MS)
        self._animation.valueChanged.connect(self._setDepth)
        self.setAutoFillBackground(True)

        self.headline = QLabel(HEADLINE)
        self.headline.setObjectName("startHeadline")
        family = headlineFamily()
        headlineFont = QFont(family) if family else QFont(self.font())
        headlineFont.setPixelSize(40 if family else 30)
        if not family:
            headlineFont.setWeight(QFont.Weight.Light)
        self.headline.setFont(headlineFont)
        self.headline.setAccessibleName(HEADLINE)
        self.summary = BalancedLabel(
            "Spectra, band math, classification and maps for hyperspectral "
            "and multispectral images."
        )
        self.importRow = ActionRow(
            "import",
            "Import Image(s)…",
            "Or drop them on this window.\nENVI, GeoTIFF, PDS, FITS, HDF5 and more.",
            "Ctrl+O",
            primary=True,
        )
        self.importRow.setObjectName("importButton")
        self.sessionRow = ActionRow(
            "session",
            "Open Session…",
            "Images, workspaces and ROIs you saved.",
            "Ctrl+Shift+O",
        )
        self.sessionRow.setObjectName("sessionButton")
        self.restoreRow = ActionRow(
            "restore", "Restore Last Session", "Your last work, saved automatically."
        )
        self.restoreRow.setObjectName("restoreButton")
        self.findRow = ActionRow(
            "find",
            "Find Data…",
            "No images yet? Search PDS and STAC catalogs by place.",
        )
        self.findRow.setObjectName("findDataButton")
        for row, command in (
            (self.importRow, IMPORT_IMAGE_ID),
            (self.sessionRow, OPEN_SESSION_ID),
            (self.restoreRow, RESTORE_LAST_SESSION_ID),
            (self.findRow, FIND_DATA_ID),
        ):
            row.clicked.connect(self._runner(command))
        # what comes after opening, in order
        self.hint = QWidget()
        self.hint.setObjectName("startHint")
        self.hint.setAccessibleName("Then: " + "; ".join(NEXT_STEPS))
        steps = QGridLayout(self.hint)
        steps.setContentsMargins(16, 0, 0, 0)
        steps.setHorizontalSpacing(12)
        steps.setVerticalSpacing(4)
        smallFont = QFont(self.font())
        smallFont.setPixelSize(12)
        self._stepLabels: list[QLabel] = []
        for row, text in enumerate(NEXT_STEPS):
            number, step = QLabel(str(row + 1)), QLabel(text)
            for label in (number, step):
                label.setFont(smallFont)
                self._stepLabels.append(label)
            number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            steps.addWidget(number, row, 0)
            steps.addWidget(step, row, 1)
        steps.setColumnStretch(1, 1)
        summaryFont = QFont(self.font())
        summaryFont.setPixelSize(13)
        self.summary.setFont(summaryFont)

        column = QWidget()
        column.setMinimumWidth(COLUMN_WIDTH[0])
        column.setMaximumWidth(COLUMN_WIDTH[1])
        stack = QVBoxLayout(column)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(4)
        stack.addWidget(self.headline)
        stack.addWidget(self.summary)
        stack.addSpacing(18)
        for row in (self.importRow, self.sessionRow, self.restoreRow, self.findRow):
            stack.addWidget(row)
        stack.addSpacing(14)
        stack.addWidget(self.hint)
        self.column = column

        centered = QHBoxLayout()
        centered.addStretch(1)
        centered.addWidget(column, 4)
        centered.addStretch(1)
        self._layout = QVBoxLayout(self)
        self._layout.addStretch(4)
        self._layout.addLayout(centered)
        self._layout.addStretch(5)
        self._applyColors()
        self.refresh()

    def _runner(self, command: str) -> Callable[[], None]:
        return lambda: self.app.commands.execute_command(command)

    # --- state ---------------------------------------------------------------------

    def refresh(self) -> None:
        """Show Restore Last Session only when there is a session to restore,
        with what it holds and when it was saved."""
        summary = autosaveSummary(defaultAutosavePath())
        self.restoreRow.setVisible(summary is not None)
        if summary is not None:
            self.restoreRow.setDetail(summary)

    def setDropCount(self, count: int) -> None:
        """Files are being dragged over the window (``count``), or no longer
        (0): deepen the spectrum's bands and say what a drop will do."""
        if count == self.dropCount:
            return
        self.dropCount = count
        if count:
            what = "the image" if count == 1 else f"{count} images"
            self.headline.setText(f"Drop to open {what}")
        else:
            self.headline.setText(HEADLINE)
        self._animation.stop()
        self._animation.setStartValue(self._depth)
        self._animation.setEndValue(DRAG_DEPTH if count else 1.0)
        self._animation.start()

    def _setDepth(self, value: object) -> None:
        if isinstance(value, float):
            self._depth = value
            self.update()

    def spectrumRect(self) -> QRectF:
        """Where the spectrum is drawn: along the bottom, in the room the list
        leaves (a quarter of the panel at most); empty when there's too little,
        so the list is never crowded or the window made taller for it."""
        room = self.height() - self.column.sizeHint().height() - 3 * SPECTRUM_GAP
        height = min(SPECTRUM_HEIGHT[1], self.height() * 0.24, room)
        if height < SPECTRUM_HEIGHT[0]:
            return QRectF()
        top = self.height() - height - SPECTRUM_GAP
        return QRectF(-8, top, self.width() + 16, height)

    # --- events --------------------------------------------------------------------

    def showEvent(self, a0) -> None:
        super().showEvent(a0)
        self.refresh()

    def resizeEvent(self, a0) -> None:
        super().resizeEvent(a0)
        # keep the list clear of the spectrum
        band = self.spectrumRect()
        bottom = int(band.height()) + 2 * SPECTRUM_GAP if not band.isEmpty() else 24
        self._layout.setContentsMargins(24, 24, 24, bottom)

    def changeEvent(self, a0: QEvent | None) -> None:
        super().changeEvent(a0)
        if a0 is not None and a0.type() == QEvent.Type.PaletteChange:
            self._applyColors()

    def _applyColors(self) -> None:
        colors = Colors.forPalette(QApplication.palette(self))
        palette = self.palette()
        if palette.color(QPalette.ColorRole.Window) != colors.ground:
            # (setting it sends a PaletteChange, which lands back here)
            palette.setColor(QPalette.ColorRole.Window, colors.ground)
            self.setPalette(palette)
        self.headline.setStyleSheet(f"color: {colors.text.name()};")
        for label in (self.summary, *self._stepLabels):
            label.setStyleSheet(f"color: {colors.muted.name()};")

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        colors = Colors.forPalette(self.palette())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        band = self.spectrumRect()
        if band.isEmpty():
            return
        continuum = QColor(colors.muted)
        continuum.setAlphaF(0.40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(continuum)
        y = band.top() + band.height() * 0.08
        x = 4.0
        while x < self.width():
            painter.drawEllipse(QPointF(x, y), 1.4, 1.4)
            x += 9.0
        pen = QPen(QBrush(spectralRamp(0, self.width())), 2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(spectrumPath(band, self._depth))
