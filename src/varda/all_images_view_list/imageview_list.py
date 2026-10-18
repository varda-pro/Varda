# standard library
import logging
import weakref

# third party imports
import numpy as np
from psygnal import Signal
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtGui import QImage, QPixmap, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QListWidget,
    QListWidgetItem,
)
from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal

# local imports
from varda.all_images_view_list.thumbnail import THUMBNAIL_SIZE, thumbnailRgba
from varda.common import ObservableList
from varda.common.entities import VardaRaster
from varda.common.parameter_widgets import connectWhileAlive

ICON_SIZE = THUMBNAIL_SIZE

logger = logging.getLogger(__name__)


def _pixmap(rgba: np.ndarray) -> QPixmap:
    height, width = rgba.shape[:2]
    data = np.ascontiguousarray(rgba).tobytes()
    image = QImage(data, width, height, 4 * width, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(image.copy())  # copy: QImage doesn't own ``data``


class ImageListWidget(QListWidget):
    """Widget for displaying all the images of a project.

    This class gives users a way to see previews of all the images in the project.
    Interactions are surfaced as signals carrying the affected images, so this
    widget stays independent of what "opening" an image means:

    - ``sigImagesActivated(list[VardaRaster])`` on double-click.
    - ``sigContextMenuRequested(list[VardaRaster], QPoint)`` on right-click over an
      item; the clicked image comes first, followed by any other selected images.
    - ``sigRemoveRequested(list[VardaRaster])`` on Delete or Backspace, with the
      selected images.
    """

    sigImagesActivated = pyqtSignal(list)
    sigContextMenuRequested = pyqtSignal(list, QPoint)
    sigRemoveRequested = pyqtSignal(list)

    def __init__(self, imageList: ObservableList, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setIconSize(QtCore.QSize(64, 64))  # Set icon size
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setMouseTracking(True)  # hover state for the item delegate
        self.imageList = imageList
        # One small thumbnail per image, made on its first paint and again
        # after the image's metadata (e.g. its no-data value) changes
        self._thumbnails: weakref.WeakKeyDictionary[VardaRaster, QPixmap] = (
            weakref.WeakKeyDictionary()
        )
        self._watched: weakref.WeakSet[VardaRaster] = weakref.WeakSet()
        placeholder = QPixmap(ICON_SIZE, ICON_SIZE)
        placeholder.fill(QtGui.QColor("pink"))
        self._placeholderIcon = QIcon(placeholder)  # sizes the items

        self.setItemDelegate(ImageItemDelegate(self.thumbnail, self))

        self._updateItems()
        self.imageList.sigDataChanged.connect(self._updateItems)
        self.itemDoubleClicked.connect(self._onItemDoubleClicked)
        self.customContextMenuRequested.connect(self._onContextMenuRequested)

    def keyPressEvent(self, e) -> None:
        if e is not None and e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            selected = [
                item.data(Qt.ItemDataRole.UserRole) for item in self.selectedItems()
            ]
            if selected:
                self.sigRemoveRequested.emit(selected)
                return
        super().keyPressEvent(e)

    def _onItemDoubleClicked(self, item: QListWidgetItem) -> None:
        self.sigImagesActivated.emit([item.data(Qt.ItemDataRole.UserRole)])

    def _onContextMenuRequested(self, pos: QPoint) -> None:
        clicked = self.itemAt(pos)
        if clicked is None:
            return
        others = [
            item for item in self.selectedItems() if self.row(item) != self.row(clicked)
        ]
        images = [item.data(Qt.ItemDataRole.UserRole) for item in [clicked, *others]]
        self.sigContextMenuRequested.emit(images, self.viewport().mapToGlobal(pos))

    def thumbnail(self, image: VardaRaster) -> QPixmap:
        """The image's thumbnail, made once and kept until the image changes."""
        pixmap = self._thumbnails.get(image)
        if pixmap is None:
            ratio = self.devicePixelRatioF()  # sharp on high-DPI screens
            try:
                pixmap = _pixmap(thumbnailRgba(image, round(ICON_SIZE * ratio)))
            except Exception:
                logger.exception("Couldn't make a thumbnail of %s", image.name)
                pixmap = self._placeholderIcon.pixmap(ICON_SIZE, ICON_SIZE)
            pixmap.setDevicePixelRatio(ratio)
            self._thumbnails[image] = pixmap
        return pixmap

    def _onImageEdited(self) -> None:
        image = Signal.sender()
        self._thumbnails.pop(image, None)
        for row in range(self.count()):
            item = self.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) is image:
                item.setText(image.name)
        viewport = self.viewport()
        if viewport is not None:
            viewport.update()

    def _updateItems(self):
        self.clear()
        for image in self.imageList:
            item = QListWidgetItem()
            item.setText(image.name)
            item.setData(Qt.ItemDataRole.UserRole, image)
            item.setIcon(self._placeholderIcon)
            self.addItem(item)
            if image not in self._watched:
                self._watched.add(image)
                connectWhileAlive(image.sigMetadataChanged, self._onImageEdited, self)
        self.update()


class ImageItemDelegate(QtWidgets.QStyledItemDelegate):
    """Draws each image's cached thumbnail with its name below."""

    def __init__(self, thumbnail, parent=None):
        super().__init__(parent)
        self._thumbnail = thumbnail  # VardaRaster -> QPixmap

    def paint(self, painter, option, index):
        image = index.data(QtCore.Qt.ItemDataRole.UserRole)
        label = index.data(QtCore.Qt.ItemDataRole.DisplayRole) or ""
        rect = option.rect
        iconRect = QRect(
            rect.x() + (rect.width() - ICON_SIZE) // 2, rect.y(), ICON_SIZE, ICON_SIZE
        )
        if image is not None:
            pixmap = self._thumbnail(image)
            size = pixmap.size().scaled(
                iconRect.size(), Qt.AspectRatioMode.KeepAspectRatio
            )
            target = QRect(QPoint(0, 0), size)
            target.moveCenter(iconRect.center())
            painter.drawPixmap(target, pixmap)

        textRect = QRect(rect.x(), iconRect.bottom() + 1, rect.width(), 0)
        textRect.setBottom(rect.bottom())
        text = option.fontMetrics.elidedText(
            label, Qt.TextElideMode.ElideRight, textRect.width()
        )
        painter.save()
        painter.setPen(option.palette.text().color())
        painter.drawText(
            textRect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text
        )

        # Selection: a translucent tint plus a solid frame in the palette's
        # highlight color over the whole item (thumbnail and label), so it
        # reads at a glance. Hover: a thin frame as a clickability cue.
        highlight = option.palette.highlight().color()
        frame = rect.adjusted(1, 1, -2, -2)
        if option.state & QtWidgets.QStyle.StateFlag.State_Selected:
            tint = QtGui.QColor(highlight)
            tint.setAlpha(60)
            painter.setBrush(tint)
            painter.setPen(QtGui.QPen(highlight, 3))
            painter.drawRoundedRect(frame, 3, 3)
        elif option.state & QtWidgets.QStyle.StateFlag.State_MouseOver:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QtGui.QPen(highlight, 1))
            painter.drawRoundedRect(frame, 3, 3)
        painter.restore()
