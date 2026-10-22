"""A gallery of the selected products' browse images: one large picture with
its caption, arrows on its sides and a filmstrip of thumbnails beneath, which
turns like a lazy susan (the last picture is followed by the first).

Items are listed before their pictures arrive (the workspace fetches them in
the background): each shows a quiet "Loading preview…" card until
``setPicture`` or ``setUnavailable`` is called for it. The widget does no
network work itself."""

from __future__ import annotations

from attrs import frozen
from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QEnterEvent,
    QHelpEvent,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
    QResizeEvent,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from varda.common.ui import MutedLabel, mutedTextColor

MOTION_MS = 180  # the slide from one picture to the next
WHEEL_STEP = 120  # a mouse wheel's notch (angle delta) turns the gallery once
STAGE_MIN = QSize(200, 120)
GROUND_RADIUS = 10.0
PICTURE_RADIUS = 6.0
STAGE_PADDING = 14  # px between the ground's edge and the picture
ARROW_SIZE = 30
ARROW_MARGIN = 8
THUMB = QSize(64, 48)
THUMB_GAP = 8
THUMB_RADIUS = 5.0
STRIP_PADDING = 5  # room for the current thumbnail's ring
EDGE_FADE = 40  # px of fade where the filmstrip runs on past its edge
RESTING_THUMB = 0.78  # opacity of the thumbnails not current or hovered
LOADING = "Loading preview…"
EMPTY = "Select products to see their previews."


@frozen
class GalleryItem:
    """One product in the gallery: ``subtitle`` is e.g. its date and an
    instrument detail."""

    id: str
    title: str
    subtitle: str = ""


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """``a`` moved the fraction ``t`` towards ``b``."""
    return QColor.fromRgbF(
        a.redF() + t * (b.redF() - a.redF()),
        a.greenF() + t * (b.greenF() - a.greenF()),
        a.blueF() + t * (b.blueF() - a.blueF()),
    )


def _tone(palette: QPalette, amount: float) -> QColor:
    """The window color moved ``amount`` towards the text color: quiet
    surfaces that work on light and dark themes alike."""
    return _mix(
        palette.color(QPalette.ColorRole.Window),
        palette.color(QPalette.ColorRole.WindowText),
        amount,
    )


def _fitted(size: QSize, box: QRectF) -> QRectF:
    """The largest rectangle of ``size``'s shape centered in ``box``."""
    if size.isEmpty():
        return QRectF(box)
    scale = min(box.width() / size.width(), box.height() / size.height())
    width, height = size.width() * scale, size.height() * scale
    return QRectF(
        box.center().x() - width / 2, box.center().y() - height / 2, width, height
    )


def _roundedPath(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


class PreviewGallery(QWidget):
    """The selected products' previews: a large picture, a caption ("2 of 7")
    and a filmstrip; arrows, the Left/Right/Home/End keys, the mouse wheel
    and a trackpad's swipe turn it. With one item only the picture and its
    caption show; with none, a short note."""

    sigCurrentChanged = pyqtSignal(str)
    sigActivated = pyqtSignal(str)  # the large picture was double-clicked

    animated = True  # slide between pictures (only ever while shown)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[GalleryItem] = []
        self._current = -1
        self._pictures: dict[str, QPixmap] = {}
        self._unavailable: dict[str, str] = {}
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # one motion: the pictures slide and the filmstrip follows
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(MOTION_MS)
        self._motion.setStartValue(0.0)
        self._motion.setEndValue(1.0)
        self._motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._motion.valueChanged.connect(self._onMotion)
        self._motion.finished.connect(self._onMotionFinished)

        self.stage = _Stage(self)
        self.title = _ElidedLabel()
        font = self.title.font()
        font.setWeight(font.Weight.DemiBold)
        self.title.setFont(font)
        self.position = MutedLabel()
        self.position.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.subtitle = _ElidedLabel(muted=True)
        self.strip = _Filmstrip(self)
        self.emptyNote = MutedLabel(EMPTY)
        self.emptyNote.setWordWrap(True)
        self.emptyNote.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.emptyNote.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )

        titleRow = QHBoxLayout()
        titleRow.setSpacing(8)
        titleRow.addWidget(self.title, 1)
        titleRow.addWidget(self.position)
        caption = QVBoxLayout()
        caption.setContentsMargins(4, 0, 4, 0)
        caption.setSpacing(1)
        caption.addLayout(titleRow)
        caption.addWidget(self.subtitle)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.stage, 1)
        layout.addLayout(caption)
        layout.addWidget(self.strip)
        layout.addWidget(self.emptyNote, 1)
        self._refresh()

    # --- the items ------------------------------------------------------------------

    def setItems(self, items: list[GalleryItem], current: str | None = None) -> None:
        """Show ``items``: on ``current`` if given, else on the item shown
        now if it's still among them, else on the first. Pictures and
        messages already given for items still present are kept."""
        before = self.currentId()
        self._motion.stop()
        self._items = list(items)
        ids = [item.id for item in self._items]
        self._pictures = {i: p for i, p in self._pictures.items() if i in ids}
        self._unavailable = {i: m for i, m in self._unavailable.items() if i in ids}
        self.stage.forget(keep=set(ids))
        self.strip.forget(keep=set(ids))
        focus = current if current in ids else before if before in ids else None
        self._current = ids.index(focus) if focus is not None else (0 if ids else -1)
        self.stage.previous = None
        self.strip.centerOn(self._current, animate=False)
        self._refresh()
        now = self.currentId()
        if now is not None and now != before:
            self.sigCurrentChanged.emit(now)

    def items(self) -> list[GalleryItem]:
        return list(self._items)

    def setPicture(self, itemId: str, pixmap: QPixmap) -> None:
        """The picture of ``itemId`` has arrived (ignored if it's no longer
        in the gallery)."""
        if itemId not in self._ids():
            return
        self._pictures[itemId] = pixmap
        self._unavailable.pop(itemId, None)
        self.stage.forget(drop={itemId})
        self.strip.forget(drop={itemId})
        self.stage.update()
        self.strip.update()

    def setUnavailable(self, itemId: str, message: str) -> None:
        """``itemId`` has no picture: show ``message`` (e.g. "No preview.")
        in its place."""
        if itemId not in self._ids():
            return
        self._unavailable[itemId] = message
        self._pictures.pop(itemId, None)
        self.stage.forget(drop={itemId})
        self.strip.forget(drop={itemId})
        self.stage.update()
        self.strip.update()

    def picture(self, itemId: str) -> QPixmap | None:
        return self._pictures.get(itemId)

    def isUnavailable(self, itemId: str) -> bool:
        return itemId in self._unavailable

    def message(self, itemId: str) -> str:
        """What shows in place of ``itemId``'s picture ("" once it has one)."""
        if itemId in self._pictures:
            return ""
        return self._unavailable.get(itemId, LOADING)

    # --- turning --------------------------------------------------------------------

    def currentIndex(self) -> int:
        """The current item's place in ``items()`` (-1 with none)."""
        return self._current

    def currentId(self) -> str | None:
        return self._items[self._current].id if self._items else None

    def setCurrent(self, itemId: str) -> None:
        ids = self._ids()
        if itemId in ids:
            index = ids.index(itemId)
            self._turnTo(index, 1 if index >= self._current else -1)

    def next(self) -> None:
        if len(self._items) > 1:
            self._turnTo((self._current + 1) % len(self._items), 1)

    def previous(self) -> None:
        if len(self._items) > 1:
            self._turnTo((self._current - 1) % len(self._items), -1)

    def first(self) -> None:
        if self._items:
            self._turnTo(0, -1)

    def last(self) -> None:
        if self._items:
            self._turnTo(len(self._items) - 1, 1)

    def activate(self) -> None:
        now = self.currentId()
        if now is not None:
            self.sigActivated.emit(now)

    def _turnTo(self, index: int, direction: int) -> None:
        if index == self._current:
            return
        animate = self.animated and self.isVisible()
        # the strip slides on from where it is, even mid-slide
        self.strip.centerOn(index, animate=animate)
        self._motion.stop()
        self.stage.previous = self._items[self._current].id
        self.stage.direction = direction
        self._current = index
        if animate:
            self._motion.start()
        else:
            self._onMotionFinished()
        self._refresh()
        self.sigCurrentChanged.emit(self._items[index].id)

    def motionProgress(self) -> float:
        """How far the slide to the current picture has come (1: at rest)."""
        if self._motion.state() == QVariantAnimation.State.Stopped:
            return 1.0
        value = self._motion.currentValue()
        return float(value) if isinstance(value, float) else 1.0

    def _onMotion(self, _value: object) -> None:
        self.stage.update()
        self.strip.update()

    def _onMotionFinished(self) -> None:
        self.stage.previous = None
        self.strip.settle()
        self.stage.update()
        self.strip.update()

    # --- showing --------------------------------------------------------------------

    def _ids(self) -> list[str]:
        return [item.id for item in self._items]

    def _refresh(self) -> None:
        count = len(self._items)
        many = count > 1
        self.emptyNote.setVisible(count == 0)
        for widget in (self.stage, self.title):
            widget.setVisible(count > 0)
        self.strip.setVisible(many)
        self.position.setVisible(many)
        self.stage.setArrowsVisible(many)
        item = self._items[self._current] if count else None
        self.title.setFullText(item.title if item else "")
        self.subtitle.setFullText(item.subtitle if item else "")
        self.subtitle.setVisible(bool(item and item.subtitle))
        self.position.setText(f"{self._current + 1} of {count}" if many else "")
        self.stage.setAccessibleName(item.title if item else EMPTY)
        self.stage.update()
        self.strip.update()

    def keyPressEvent(self, a0: QKeyEvent | None) -> None:
        if a0 is None:
            return
        key = a0.key()
        actions = {
            Qt.Key.Key_Right: self.next,
            Qt.Key.Key_Down: self.next,
            Qt.Key.Key_Left: self.previous,
            Qt.Key.Key_Up: self.previous,
            Qt.Key.Key_Home: self.first,
            Qt.Key.Key_End: self.last,
            Qt.Key.Key_Return: self.activate,
            Qt.Key.Key_Enter: self.activate,
        }
        action = actions.get(Qt.Key(key))
        if action is None or not self._items:
            super().keyPressEvent(a0)
            return
        action()
        a0.accept()

    def minimumSizeHint(self) -> QSize:
        return QSize(STAGE_MIN.width(), STAGE_MIN.height() + 40)

    def sizeHint(self) -> QSize:
        return QSize(360, 420)


class _ElidedLabel(QLabel):
    """One line of text, elided in the middle when it doesn't fit (product
    names differ at their ends), the full text then in the tooltip."""

    def __init__(self, muted: bool = False) -> None:
        super().__init__()
        self._full = ""
        self._muted = muted
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._applyColor()

    def setFullText(self, text: str) -> None:
        self._full = text
        self._elide()

    def fullText(self) -> str:
        return self._full

    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        super().resizeEvent(a0)
        self._elide()

    def changeEvent(self, a0: QEvent | None) -> None:
        super().changeEvent(a0)
        if a0 is not None and a0.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
        ):
            self._applyColor()
            self._elide()

    def _applyColor(self) -> None:
        if not self._muted:
            return
        color = mutedTextColor(QApplication.palette())
        palette = self.palette()
        if palette.color(QPalette.ColorRole.WindowText) != color:  # no loop
            palette.setColor(QPalette.ColorRole.WindowText, color)
            self.setPalette(palette)

    def _elide(self) -> None:
        # 2 px spare: the label's own size hint must fit its width
        shown = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideMiddle, max(self.width() - 2, 0)
        )
        if shown != self.text():
            super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class _ArrowButton(QAbstractButton):
    """A round previous/next button laid over the picture's side."""

    def __init__(self, direction: int, parent: QWidget) -> None:
        super().__init__(parent)
        self.direction = direction
        self.setFixedSize(ARROW_SIZE, ARROW_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        name = "Next" if direction > 0 else "Previous"
        self.setAccessibleName(name)
        self.setToolTip(f"{name} ({'→' if direction > 0 else '←'})")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setAutoRepeat(True)
        self.setAutoRepeatDelay(400)
        self.setAutoRepeatInterval(260)
        self.raised = False  # the mouse is over the picture

    def sizeHint(self) -> QSize:
        return QSize(ARROW_SIZE, ARROW_SIZE)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, e: QPaintEvent | None) -> None:
        palette = self.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse()
        painter.setOpacity(1.0 if hovered or self.raised else 0.72)
        disc = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        fill = palette.color(QPalette.ColorRole.Window)
        if self.isDown():
            fill = _tone(palette, 0.12)
        fill.setAlphaF(0.92)
        ring = (
            palette.color(QPalette.ColorRole.Highlight)
            if hovered
            else _tone(palette, 0.22)
        )
        painter.setPen(QPen(ring, 1.0))
        painter.setBrush(fill)
        painter.drawEllipse(disc)
        pen = QPen(palette.color(QPalette.ColorRole.WindowText), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        center = disc.center()
        x, y = 3.0 * self.direction, 6.0
        painter.drawPolyline(
            QPolygonF(
                [
                    QPointF(center.x() - x, center.y() - y),
                    QPointF(center.x() + x, center.y()),
                    QPointF(center.x() - x, center.y() + y),
                ]
            )
        )


class _Stage(QWidget):
    """The large picture on a quiet ground, sliding to the next one."""

    def __init__(self, gallery: PreviewGallery) -> None:
        super().__init__(gallery)
        self.gallery = gallery
        self.previous: str | None = None  # the picture sliding away
        self.direction = 1
        self._scaled: dict[str, QPixmap] = {}  # fitted to the stage, by id
        self._wheel = 0
        self._swiped = False  # this trackpad gesture has turned already
        self.setMinimumSize(STAGE_MIN)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setToolTip("Double-click to zoom the map to it")
        self.setMouseTracking(True)
        self.previousButton = _ArrowButton(-1, self)
        self.nextButton = _ArrowButton(1, self)
        self.previousButton.clicked.connect(gallery.previous)
        self.nextButton.clicked.connect(gallery.next)

    def sizeHint(self) -> QSize:
        return QSize(360, 260)

    def setArrowsVisible(self, visible: bool) -> None:
        self.previousButton.setVisible(visible)
        self.nextButton.setVisible(visible)

    def forget(
        self, keep: set[str] | None = None, drop: set[str] | None = None
    ) -> None:
        """Drop scaled pictures: those not in ``keep``, or those in ``drop``."""
        for itemId in list(self._scaled):
            if (keep is not None and itemId not in keep) or (drop and itemId in drop):
                del self._scaled[itemId]

    def _ground(self) -> QRectF:
        return QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

    def _box(self) -> QRectF:
        return self._ground().adjusted(
            STAGE_PADDING, STAGE_PADDING, -STAGE_PADDING, -STAGE_PADDING
        )

    def _scaledPicture(self, itemId: str, pixmap: QPixmap) -> QPixmap:
        """The picture scaled once to the size it's shown at, in device
        pixels (sharp, and cheap to draw while sliding)."""
        ratio = self.devicePixelRatioF()
        target = _fitted(pixmap.size(), self._box()).size().toSize() * ratio
        scaled = self._scaled.get(itemId)
        if scaled is None or scaled.size() != target:
            scaled = pixmap.scaled(
                target,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(ratio)
            self._scaled[itemId] = scaled
        return scaled

    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        super().resizeEvent(a0)
        y = (self.height() - ARROW_SIZE) // 2
        self.previousButton.move(ARROW_MARGIN, y)
        self.nextButton.move(self.width() - ARROW_SIZE - ARROW_MARGIN, y)

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        palette = self.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        ground = self._ground()
        painter.setPen(QPen(_tone(palette, 0.1), 1.0))
        painter.setBrush(_tone(palette, 0.055))
        painter.drawRoundedRect(ground, GROUND_RADIUS, GROUND_RADIUS)
        current = self.gallery.currentId()
        if current is None:
            return
        painter.setClipPath(_roundedPath(ground, GROUND_RADIUS))
        progress = self.gallery.motionProgress()
        if self.previous is None or progress >= 1.0:
            self._paintPage(painter, current, 0.0)
            return
        travel = self.width() * self.direction
        self._paintPage(painter, self.previous, -travel * progress)
        self._paintPage(painter, current, travel * (1.0 - progress))

    def _paintPage(self, painter: QPainter, itemId: str, dx: float) -> None:
        painter.save()
        painter.translate(dx, 0)
        pixmap = self.gallery.picture(itemId)
        if pixmap is not None and not pixmap.isNull():
            self._paintPicture(painter, itemId, pixmap)
        else:
            self._paintCard(painter, self.gallery.message(itemId), itemId)
        painter.restore()

    def _paintPicture(self, painter: QPainter, itemId: str, pixmap: QPixmap) -> None:
        rect = _fitted(pixmap.size(), self._box())
        # a soft shadow lifts the picture off the ground
        painter.setPen(Qt.PenStyle.NoPen)
        for spread, alpha in ((6, 0.03), (4, 0.05), (2, 0.08)):
            painter.setBrush(QColor.fromRgbF(0, 0, 0, alpha))
            painter.drawRoundedRect(
                rect.adjusted(-spread, -spread + 2, spread, spread + 2),
                PICTURE_RADIUS + spread,
                PICTURE_RADIUS + spread,
            )
        painter.save()
        painter.setClipPath(
            _roundedPath(rect, PICTURE_RADIUS), Qt.ClipOperation.IntersectClip
        )
        painter.drawPixmap(rect.topLeft(), self._scaledPicture(itemId, pixmap))
        painter.restore()

    def _paintCard(self, painter: QPainter, message: str, itemId: str) -> None:
        palette = self.palette()
        box = self._box().adjusted(ARROW_SIZE, 0, -ARROW_SIZE, 0)
        if box.width() < 80:
            box = self._box()
        card = _fitted(QSize(4, 3), box)
        painter.setPen(QPen(_tone(palette, 0.12), 1.0))
        painter.setBrush(_tone(palette, 0.09))
        painter.drawRoundedRect(card, PICTURE_RADIUS * 1.5, PICTURE_RADIUS * 1.5)
        muted = _tone(palette, 0.65)
        loading = not self.gallery.isUnavailable(itemId)
        glyph = QRectF(0, 0, 22, 22)
        glyph.moveCenter(QPointF(card.center().x(), card.center().y() - 16))
        if card.height() > 90:
            _paintGlyph(painter, glyph, muted, loading)
            text = card.adjusted(10, glyph.bottom() - card.top() + 8, -10, -6)
        else:
            text = card.adjusted(10, 6, -10, -6)
        painter.setPen(muted)
        flags = (
            Qt.AlignmentFlag.AlignHCenter
            | Qt.AlignmentFlag.AlignTop
            | Qt.TextFlag.TextWordWrap
        )
        if text.height() < painter.fontMetrics().height() * 2:
            flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
            text = card.adjusted(10, 4, -10, -4)
        painter.drawText(text, int(flags), message)

    def mouseDoubleClickEvent(self, a0: QMouseEvent | None) -> None:
        if a0 is not None and a0.button() == Qt.MouseButton.LeftButton:
            self.gallery.activate()

    def mousePressEvent(self, a0: QMouseEvent | None) -> None:
        self.gallery.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(a0)

    def enterEvent(self, event: QEnterEvent | None) -> None:
        self._raiseArrows(True)
        super().enterEvent(event)

    def leaveEvent(self, a0: QEvent | None) -> None:
        self._raiseArrows(False)
        super().leaveEvent(a0)

    def _raiseArrows(self, raised: bool) -> None:
        for button in (self.previousButton, self.nextButton):
            button.raised = raised
            button.update()

    def wheelEvent(self, a0: QWheelEvent | None) -> None:
        """A mouse wheel turns once a notch; a trackpad swipe turns once a
        gesture (its momentum is ignored, or a flick would spin it)."""
        if a0 is None:
            return
        a0.accept()
        phase = a0.phase()
        if phase in (Qt.ScrollPhase.ScrollBegin, Qt.ScrollPhase.ScrollEnd):
            self._wheel, self._swiped = 0, False
        if phase == Qt.ScrollPhase.ScrollMomentum or self._swiped:
            return
        delta = a0.angleDelta()
        step = delta.x() if abs(delta.x()) > abs(delta.y()) else delta.y()
        self._wheel += step
        if abs(self._wheel) < WHEEL_STEP:
            return
        forward = self._wheel < 0
        self._wheel = 0
        self._swiped = phase != Qt.ScrollPhase.NoScrollPhase
        (self.gallery.next if forward else self.gallery.previous)()


def _paintGlyph(painter: QPainter, rect: QRectF, color: QColor, loading: bool) -> None:
    """A small picture-frame glyph for "loading", a crossed circle for "no
    picture"."""
    pen = QPen(color, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if not loading:
        painter.drawEllipse(rect.adjusted(2, 2, -2, -2))
        painter.drawLine(
            QPointF(rect.left() + 5.5, rect.bottom() - 5.5),
            QPointF(rect.right() - 5.5, rect.top() + 5.5),
        )
        return
    frame = rect.adjusted(1, 3, -1, -3)
    painter.drawRoundedRect(frame, 3, 3)
    painter.drawPolyline(
        QPolygonF(
            [
                QPointF(frame.left() + 3, frame.bottom() - 3),
                QPointF(frame.left() + 8, frame.top() + 7),
                QPointF(frame.left() + 12, frame.bottom() - 6),
                QPointF(frame.left() + 15, frame.top() + 10),
                QPointF(frame.right() - 3, frame.bottom() - 3),
            ]
        )
    )
    painter.drawEllipse(QPointF(frame.right() - 5, frame.top() + 4.5), 1.5, 1.5)


class _Filmstrip(QWidget):
    """Thumbnails in a row, drawn rather than made of widgets so it scrolls
    smoothly and its edges fade. The current one is ringed and kept in the
    middle (as far as the row allows); the wheel scrolls the row, a click
    turns the gallery to a thumbnail."""

    def __init__(self, gallery: PreviewGallery) -> None:
        super().__init__(gallery)
        self.gallery = gallery
        self._offset = 0.0  # px of the row scrolled out on the left, at rest
        self._from = 0.0  # where the slide to ``_offset`` started
        self._hovered = -1
        self._thumbs: dict[str, QPixmap] = {}
        self.setMouseTracking(True)
        self.setFixedHeight(THUMB.height() + 2 * STRIP_PADDING)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Previews")

    def sizeHint(self) -> QSize:
        return QSize(320, self.height())

    def forget(
        self, keep: set[str] | None = None, drop: set[str] | None = None
    ) -> None:
        for itemId in list(self._thumbs):
            if (keep is not None and itemId not in keep) or (drop and itemId in drop):
                del self._thumbs[itemId]

    # --- geometry --------------------------------------------------------------

    def _rowWidth(self) -> float:
        count = len(self.gallery.items())
        return 2 * STRIP_PADDING + count * THUMB.width() + (count - 1) * THUMB_GAP

    def _maxOffset(self) -> float:
        return max(self._rowWidth() - self.width(), 0.0)

    def _start(self) -> float:
        """The row's left end in the strip (centered when it all fits)."""
        if self._rowWidth() <= self.width():
            return (self.width() - self._rowWidth()) / 2
        return -self.offset()

    def offset(self) -> float:
        progress = self.gallery.motionProgress()
        return self._from + (self._offset - self._from) * progress

    def thumbRect(self, index: int) -> QRectF:
        x = self._start() + STRIP_PADDING + index * (THUMB.width() + THUMB_GAP)
        return QRectF(x, STRIP_PADDING, THUMB.width(), THUMB.height())

    def indexAt(self, x: float, y: float) -> int:
        for index in range(len(self.gallery.items())):
            if self.thumbRect(index).adjusted(-2, -2, 2, 2).contains(QPointF(x, y)):
                return index
        return -1

    def centerOn(self, index: int, animate: bool) -> None:
        """Scroll so thumbnail ``index`` sits in the middle (as far as the
        row allows), sliding with the gallery's motion when ``animate``."""
        self._from = self.offset() if animate else 0.0
        middle = STRIP_PADDING + index * (THUMB.width() + THUMB_GAP) + THUMB.width() / 2
        self._offset = min(max(middle - self.width() / 2, 0.0), self._maxOffset())
        if not animate:
            self._from = self._offset
        self.update()

    def settle(self) -> None:
        self._from = self._offset

    def resizeEvent(self, a0: QResizeEvent | None) -> None:
        super().resizeEvent(a0)
        self.centerOn(max(self.gallery.currentIndex(), 0), animate=False)

    # --- drawing ---------------------------------------------------------------

    def _thumbnail(self, itemId: str, pixmap: QPixmap) -> QPixmap:
        """The picture filling a thumbnail (cropped to its shape)."""
        thumb = self._thumbs.get(itemId)
        if thumb is None:
            ratio = self.devicePixelRatioF()
            size = THUMB * ratio
            scaled = pixmap.scaled(
                size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            thumb = scaled.copy(
                QRect(
                    (scaled.width() - size.width()) // 2,
                    (scaled.height() - size.height()) // 2,
                    size.width(),
                    size.height(),
                )
            )
            thumb.setDevicePixelRatio(ratio)
            self._thumbs[itemId] = thumb
        return thumb

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        palette = self.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        current = self.gallery.currentIndex()
        highlight = palette.color(QPalette.ColorRole.Highlight)
        for index, item in enumerate(self.gallery.items()):
            rect = self.thumbRect(index)
            if rect.right() < -THUMB_GAP or rect.left() > self.width() + THUMB_GAP:
                continue
            chosen = index == current
            painter.setOpacity(
                1.0 if chosen or index == self._hovered else RESTING_THUMB
            )
            pixmap = self.gallery.picture(item.id)
            if pixmap is not None and not pixmap.isNull():
                painter.save()
                painter.setClipPath(_roundedPath(rect, THUMB_RADIUS))
                painter.drawPixmap(rect.topLeft(), self._thumbnail(item.id, pixmap))
                painter.restore()
            else:
                painter.setPen(QPen(_tone(palette, 0.14), 1.0))
                painter.setBrush(_tone(palette, 0.09))
                painter.drawRoundedRect(rect, THUMB_RADIUS, THUMB_RADIUS)
                muted = _tone(palette, 0.55)
                if self.gallery.isUnavailable(item.id):
                    glyph = QRectF(0, 0, 16, 16)
                    glyph.moveCenter(rect.center())
                    _paintGlyph(painter, glyph, muted, loading=False)
                else:  # a quiet ellipsis: on its way
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(muted)
                    for dot in (-6, 0, 6):
                        center = rect.center() + QPointF(dot, 0)
                        painter.drawEllipse(center, 1.6, 1.6)
            painter.setOpacity(1.0)
            if chosen:
                painter.setPen(QPen(highlight, 2.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                ring = rect.adjusted(-3, -3, 3, 3)
                painter.drawRoundedRect(ring, THUMB_RADIUS + 3, THUMB_RADIUS + 3)
        self._paintEdgeFades(painter, palette)

    def _paintEdgeFades(self, painter: QPainter, palette: QPalette) -> None:
        """Fade the row into the panel where it runs on past an edge."""
        offset = self.offset()
        window = palette.color(QPalette.ColorRole.Window)
        clear = QColor(window)
        clear.setAlpha(0)
        painter.setPen(Qt.PenStyle.NoPen)
        if offset > 0.5:
            self._paintFade(painter, 0.0, EDGE_FADE, window, clear)
        if offset < self._maxOffset() - 0.5:
            right = float(self.width())
            self._paintFade(painter, right - EDGE_FADE, right, clear, window)

    def _paintFade(
        self, painter: QPainter, left: float, right: float, start: QColor, end: QColor
    ) -> None:
        gradient = QLinearGradient(left, 0, right, 0)
        gradient.setColorAt(0, start)
        gradient.setColorAt(1, end)
        painter.setBrush(gradient)
        painter.drawRect(QRectF(left, 0, right - left, self.height()))

    # --- input -----------------------------------------------------------------

    def mousePressEvent(self, a0: QMouseEvent | None) -> None:
        if a0 is None or a0.button() != Qt.MouseButton.LeftButton:
            return
        index = self.indexAt(a0.position().x(), a0.position().y())
        self.gallery.setFocus(Qt.FocusReason.MouseFocusReason)
        if index >= 0:
            self.gallery.setCurrent(self.gallery.items()[index].id)

    def mouseMoveEvent(self, a0: QMouseEvent | None) -> None:
        if a0 is None:
            return
        index = self.indexAt(a0.position().x(), a0.position().y())
        if index != self._hovered:
            self._hovered = index
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if index >= 0
                else Qt.CursorShape.ArrowCursor
            )
            self.update()

    def leaveEvent(self, a0: QEvent | None) -> None:
        self._hovered = -1
        self.update()
        super().leaveEvent(a0)

    def wheelEvent(self, a0: QWheelEvent | None) -> None:
        if a0 is None:
            return
        pixels = a0.pixelDelta()
        if not pixels.isNull():
            step = pixels.x() if abs(pixels.x()) > abs(pixels.y()) else pixels.y()
        else:
            angle = a0.angleDelta()
            step = (angle.x() if abs(angle.x()) > abs(angle.y()) else angle.y()) / 2
        if self._maxOffset() <= 0:
            a0.ignore()
            return
        a0.accept()
        self._offset = min(max(self.offset() - step, 0.0), self._maxOffset())
        self._from = self._offset
        self.update()

    def event(self, a0: QEvent | None) -> bool:
        if isinstance(a0, QHelpEvent) and a0.type() == QEvent.Type.ToolTip:
            index = self.indexAt(a0.pos().x(), a0.pos().y())
            if index >= 0:
                item = self.gallery.items()[index]
                tip = f"{item.title}\n{item.subtitle}" if item.subtitle else item.title
                QToolTip.showText(a0.globalPos(), tip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(a0)
