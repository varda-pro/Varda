"""Products' browse images for the find-data workspace: fetched on a worker
thread, decoded and shrunk to at most ``PREVIEW_PIXELS``, and kept (the most
recent ``CACHE_BYTES`` of them) so that selecting a product again, or
drawing the map in another projection, downloads nothing."""

from __future__ import annotations

from collections import OrderedDict

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap

from varda.catalogs.catalog import CatalogError
from varda.catalogs.http import fetchBytes
from varda.workspaces.find_data.previews import PREVIEW_PIXELS

# the largest browse file fetched (ODE's large browse images of HiRISE reach 8 MB)
PREVIEW_BYTES = 25_000_000
CACHE_BYTES = 256 * 2**20


def loadPicture(url: str) -> np.ndarray:
    """The PNG, JPEG or GIF at ``url`` as RGBA (rows north to south), its
    longer side at most ``PREVIEW_PIXELS``. Runs on a worker thread (a
    ``QImage``, not a ``QPixmap``)."""
    data = fetchBytes(url, limit=PREVIEW_BYTES)
    if len(data) >= PREVIEW_BYTES:
        raise CatalogError("The browse image is too large to preview.")
    image = QImage.fromData(data)
    if image.isNull():  # ODE answers a product without one in XML
        raise CatalogError("The catalog has no browse image of it.")
    if max(image.width(), image.height()) > PREVIEW_PIXELS:
        image = image.scaled(
            PREVIEW_PIXELS,
            PREVIEW_PIXELS,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    bits = image.constBits()
    assert bits is not None
    bits.setsize(image.sizeInBytes())
    rows = np.frombuffer(bits, np.uint8).reshape(height, image.bytesPerLine())
    return rows[:, : 4 * width].reshape(height, width, 4).copy()


def toPixmap(rgba: np.ndarray) -> QPixmap:
    """An RGBA picture as a pixmap (on the GUI thread)."""
    height, width = rgba.shape[:2]
    data = np.ascontiguousarray(rgba).tobytes()
    image = QImage(data, width, height, 4 * width, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(image.copy())  # copy: QImage doesn't own ``data``


class PictureCache:
    """Decoded browse images by URL, the most recently used kept up to
    ``limit`` bytes."""

    def __init__(self, limit: int = CACHE_BYTES):
        self.limit = limit
        self._pictures: OrderedDict[str, np.ndarray] = OrderedDict()

    def get(self, url: str) -> np.ndarray | None:
        picture = self._pictures.get(url)
        if picture is not None:
            self._pictures.move_to_end(url)
        return picture

    def put(self, url: str, picture: np.ndarray) -> None:
        self._pictures[url] = picture
        self._pictures.move_to_end(url)
        while (
            len(self._pictures) > 1
            and sum(p.nbytes for p in self._pictures.values()) > self.limit
        ):
            self._pictures.popitem(last=False)
