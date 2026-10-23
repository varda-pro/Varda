# third-party imports
import logging
import math

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QTransform
from affine import Affine

from varda.image_rendering.image_renderer import ImageRenderer
from varda.utilities.roi_utils import RegionCoordinateTransform
from varda.common.entities import VardaRaster

logger = logging.getLogger(__name__)

# A large image's view draws the part on screen plus this fraction of it on
# each side, so a small pan needs no new piece
PIECE_MARGIN = 0.5


def affine_to_qtransform(affine: Affine) -> QTransform:
    return QTransform(
        affine.a,
        affine.d,  # m11, m21
        affine.b,
        affine.e,  # m12, m22
        affine.c,
        affine.f,  # dx, dy
    )


class VardaImageItem(pg.ImageItem):
    """A modification of the pyqtgraph ImageItem. To support hyperspectral images and regional display from an ROI.

    Using Image, Stretch, and Band inputs, it handles the logic to generate an image suitable for display.
    It also can display a region of the image, and maps those local coordinates to absolute image coordinates.
    Being able to convert like this means we can draw ROIs on this item, and know where they are in the full image.
    """

    def __init__(
        self,
        imageRenderer: ImageRenderer,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._imageRenderer = imageRenderer
        # not totally sure how good this setup is --
        # the renderer is created way up at the workspace level, which is probably also where the settings panel will be displayed
        # but the signals emitted when those settings change is sent straight down here. idk feels weird but maybe it's actually genius
        self._imageEntity = self._imageRenderer.image

        # Region state
        self._backgroundImageItem = pg.ImageItem()
        self._backgroundImageItem.setZValue(-10)
        self._backgroundImageItem.setOpacity(0)

        self._roi: pg.ROI | None = None
        self._regionalData = None
        self._coordinateTransform = None
        self._isShowingRegion = False
        # The region shown, in image pixels: rows r0:r1, columns c0:c1
        self._box = (0, self._imageEntity.height, 0, self._imageEntity.width)
        # A large image draws a piece of the region (see renderPiece): where
        # it sits in local coordinates, and every how many pixels it reads
        self._pieceRect: QRectF | None = None
        self._pieceStep = 1
        # Base/world transform from image affine and the current composed QTransform
        self._baseQTransform = self._buildImageQTransformFromAffine()
        self._qtransform = QTransform(self._baseQTransform)
        # Update the display
        self.refresh()

    def setROI(self, roi: pg.ROI):
        """Set the region to display from the full image."""
        self._roi = roi
        self._isShowingRegion = True
        self.refresh()

    def clearROI(self):
        """Clear the region and show the full image."""
        self._roi = None
        self._coordinateTransform = None
        self._isShowingRegion = False
        self._qtransform = QTransform(self._baseQTransform)
        self.refresh()

    def refresh(self):
        """Refresh the image display with current settings."""
        self._updateImage()

    def localToImage(self, point):
        """Convert local coordinates to full image coordinates.
        Note that this does not protect against points outside the image bounds.
        """
        if not self._isShowingRegion:
            return point
        pointsList = []
        if isinstance(point, QPointF):
            pointsList.append((point.x(), point.y()))
        elif isinstance(point, tuple) and len(point) == 2:
            pointsList.append(point)
        elif isinstance(point, list | np.ndarray) and all(
            isinstance(p, tuple) and len(p) == 2 for p in point
        ):
            pointsList.extend(point)
        else:
            raise ValueError(
                "Invalid point format. Expected QPointF, tuple, or list of tuples."
            )
        converted = self._coordinateTransform.localToGlobal(pointsList)

        # return same type as input
        if isinstance(point, QPointF):
            return QPointF(pg.Point(converted[0]))
        return converted

    def imageToLocal(self, point):
        """Convert full image coordinates to local coordinates.
        Note that this does not protect against points outside the image bounds.
        """
        if not self._isShowingRegion:
            return point

        pointsList = []
        if isinstance(point, QPointF):
            pointsList.append((point.x(), point.y()))
        elif isinstance(point, tuple) and len(point) == 2:
            pointsList.append(point)
        elif isinstance(point, list | np.ndarray) and all(
            isinstance(p, tuple) and len(p) == 2 for p in point
        ):
            pointsList.extend(point)
        else:
            raise ValueError(
                "Invalid point format. Expected QPointF, tuple, or list of tuples."
            )

        converted = self._coordinateTransform.globalToLocal(pointsList)

        # return same type as input
        if isinstance(point, QPointF):
            return QPointF(pg.Point(converted[0]))
        return converted

    def getTransform(self):
        """Get the affine transform for the current region."""
        return affine_to_qtransform(self.imageEntity.transform)

    def _updateImage(self):
        """Update the displayed image data."""
        self._calculateRegionalData()
        if self._imageRenderer.isLarge():
            self._showPiece(force=True)
        else:
            self._pieceRect = None
            self.setImage(self._regionalData, levels=(0, 255))

    # --- a large image, a piece at a time ---

    def width(self):
        """The region's width in image pixels, whatever piece is drawn."""
        if not self._imageRenderer.isLarge():
            return super().width()
        return self._box[3] - self._box[2]

    def height(self):
        if not self._imageRenderer.isLarge():
            return super().height()
        return self._box[1] - self._box[0]

    def paint(self, p, *args):
        if self._pieceRect is None:
            super().paint(p, *args)
            return
        if self.image is None:
            return
        if self._renderRequired:
            self.render()
            if self._unrenderable:
                return
        p.drawImage(self._pieceRect, self.qimage)

    def viewRangeChanged(self):
        super().viewRangeChanged()
        if self._pieceRect is not None:
            self._showPiece()

    def viewTransformChanged(self):
        super().viewTransformChanged()
        if self._pieceRect is not None:
            self._showPiece()

    def _wantedPiece(self) -> tuple[int, int, int, int, int, QRectF]:
        """The part of the region on screen (local coordinates, clipped to
        the region) and the step that draws it at about one pixel per
        screen pixel."""
        height, width = self.height(), self.width()
        whole = QRectF(0, 0, width, height)
        viewBox = self.getViewBox()
        if viewBox is None or viewBox.width() <= 0 or viewBox.height() <= 0:
            step = max(1, math.ceil(max(width, height) / 2048))
            return 0, height, 0, width, step, whole
        shown = self.mapRectFromView(viewBox.viewRect()).intersected(whole)
        if shown.isEmpty():
            shown = whole
        views = viewBox.scene().views() if viewBox.scene() else []
        ratio = views[0].devicePixelRatioF() if views else 1.0
        perPixel = min(
            shown.width() / (viewBox.width() * ratio),
            shown.height() / (viewBox.height() * ratio),
        )
        step = max(1, int(perPixel))
        return (
            max(0, math.floor(shown.top())),
            min(height, math.ceil(shown.bottom())),
            max(0, math.floor(shown.left())),
            min(width, math.ceil(shown.right())),
            step,
            shown,
        )

    def _showPiece(self, force: bool = False) -> None:
        """Draw the part of a large image's region on screen (with a margin),
        unless the piece drawn already covers it at the right step."""
        top, bottom, left, right, step, shown = self._wantedPiece()
        if (
            not force
            and self._pieceRect is not None
            and step == self._pieceStep
            and self._pieceRect.contains(shown)
        ):
            return
        height, width = self.height(), self.width()
        marginY = int((bottom - top) * PIECE_MARGIN)
        marginX = int((right - left) * PIECE_MARGIN)
        top = max(0, top - marginY) // step * step
        left = max(0, left - marginX) // step * step
        bottom, right = min(height, bottom + marginY), min(width, right + marginX)
        r0, _r1, c0, _c1 = self._box
        piece = self._imageRenderer.renderPiece(
            slice(r0 + top, r0 + bottom, step), slice(c0 + left, c0 + right, step)
        )
        self._pieceStep = step
        self._pieceRect = QRectF(
            left, top, piece.shape[1] * step, piece.shape[0] * step
        )
        # a new piece of the same region: the overlays needn't hear of it
        wasBlocked = self.blockSignals(not force or self.signalsBlocked())
        self.setImage(piece, levels=(0, 255))
        self.blockSignals(wasBlocked)
        self.update()

    def _buildImageQTransformFromAffine(self) -> QTransform:
        """Build a QTransform from the image's affine (pixel -> world)."""
        affine = self._imageEntity.transform

        # Map: X = a*x + b*y + c; Y = d*x + e*y + f
        return QTransform(
            affine.a,
            affine.d,
            affine.b,
            affine.e,
            affine.c,
            affine.f,
        )

    def _buildRegionQTransform(self) -> QTransform:
        """
        Build a QTransform that maps local ROI coords (x=col, y=row) -> global image pixel coords.
        Requires self._coordinateTransform to be present.
        """

        # RegionCoordinateTransform stores origin (row,col) and basis vectors vx, vy in (row,col)
        origin = np.asarray(self._coordinateTransform.origin)
        vx = np.asarray(self._coordinateTransform.vx)
        vy = np.asarray(self._coordinateTransform.vy)

        # x_global = vy[1]*x_local + vx[1]*y_local + origin[1]
        # y_global = vy[0]*x_local + vx[0]*y_local + origin[0]
        return QTransform(
            vy[1], vy[0], 0.0, vx[1], vx[0], 0.0, origin[1], origin[0], 1.0
        )

    def _updateQTransform(self):
        """Update composed QTransform to account for current regional state."""
        self._baseQTransform = self._buildImageQTransformFromAffine()
        if self._isShowingRegion and self._coordinateTransform is not None:
            regionT = self._buildRegionQTransform()
            # Compose: worldT ∘ regionT
            self._qtransform = self._baseQTransform * regionT
        else:
            self._qtransform = QTransform(self._baseQTransform)
        # Apply to the item
        self.setTransform(self._qtransform)

    def _calculateRegionalData(self):
        """The region shown: the ROI's bounding box in whole image pixels
        (the image's pixels, sliced, not resampled), or the whole image. A
        large image is drawn later, a piece at a time."""
        image = self._imageEntity
        if self._isShowingRegion and self._roi is not None:
            x0, y0, x1, y1 = self._roi.getBounds()
            r0 = min(max(0, math.floor(y0)), image.height - 1)
            c0 = min(max(0, math.floor(x0)), image.width - 1)
            r1 = max(r0 + 1, min(image.height, math.ceil(y1)))
            c1 = max(c0 + 1, min(image.width, math.ceil(x1)))
            self._coordinateTransform = RegionCoordinateTransform(
                origin=(r0, c0), basisVectors=((1.0, 0.0), (0.0, 1.0))
            )
        else:
            r0, r1, c0, c1 = 0, image.height, 0, image.width
            self._coordinateTransform = None
        self._box = (r0, r1, c0, c1)
        if self._imageRenderer.isLarge():
            self._regionalData = None
        else:
            self._regionalData = self._imageRenderer.render()[r0:r1, c0:c1]

    @property
    def imageEntity(self) -> VardaRaster:
        """Get the underlying hyperspectral image entity."""
        return self._imageEntity

    @property
    def roi(self) -> pg.ROI | None:
        """Get the current region being displayed."""
        return self._roi

    @property
    def isShowingRegion(self) -> bool:
        """Check if showing a region vs full image."""
        return self._isShowingRegion

    @property
    def coordinateTransform(self) -> np.ndarray:
        """Get the coordinate mapping array (if showing region)."""
        return self._coordinateTransform
