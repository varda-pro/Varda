from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSplitter

from varda.image_rendering.image_renderer import ImageRenderer, StretchSource
from varda.rois.varda_roi import VardaROIItem
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.image_rendering.raster_view.region_controller import (
    RegionController,
)


class TripleRasterView(QWidget):
    def __init__(self, imageRenderer: ImageRenderer, parent=None):
        super().__init__(parent)
        self.imageRenderer = imageRenderer
        self._initUI()
        self._initROIControllers()

    def _initUI(self):
        self.viewport1 = ImageViewport(self.imageRenderer)
        # only need to connect one first viewport, because others are linked via region controllers.
        # viewport2/3 are region-controller targets: navigating them drives the controlling
        # ROI rather than their own view, so they don't self-update or self-navigate.
        self.viewport2 = ImageViewport(self.imageRenderer)
        self.viewport2.disableSelfUpdating()
        self.viewport2.disableSelfNavigation()
        self.viewport3 = ImageViewport(self.imageRenderer)
        self.viewport3.disableSelfUpdating()
        self.viewport3.disableSelfNavigation()

        # top-level layout
        verticalSplitter = QSplitter(Qt.Orientation.Vertical)
        verticalSplitter.addWidget(self.viewport1)
        verticalSplitter.addWidget(self.viewport3)

        horizontalSplitter = QSplitter(Qt.Orientation.Horizontal)
        horizontalSplitter.addWidget(self.viewport2)
        horizontalSplitter.addWidget(verticalSplitter)

        layout = QVBoxLayout(self)
        layout.addWidget(horizontalSplitter)
        self.setLayout(layout)

    def _initROIControllers(self):
        """Initialize ROI controllers for the viewports"""
        imageShape = self.viewport1.imageEntity.shape
        height, width = imageShape[0], imageShape[1]

        # The boxes start with the image's shape and are free-form: dragging a
        # corner changes width and height independently (zooming a view
        # scales both, keeping the shape)
        startPoint = width // 3, height // 3
        size = width // 3, height // 3
        roiTempIndex = -1  # temp -- ROIs probably dont need an index field at all
        self.roi1 = VardaROIItem.rectROI(
            startPoint,
            size,
            roiTempIndex,
            QColor(255, 0, 0, 0),
        )

        startPoint = startPoint[0] // 3, startPoint[1] // 3
        size = size[0] // 3, size[1] // 3
        self.roi2 = VardaROIItem.rectROI(
            startPoint,
            size,
            roiTempIndex,  # temp -- ROIs probably dont need an index field at all
            QColor(255, 0, 0, 0),
        )

        self.mainController = RegionController(
            self.viewport1, self.viewport2, self.roi1
        )
        self.zoomController = RegionController(
            self.viewport2, self.viewport3, self.roi2, self.mainController
        )

        # The renderer learns what the main and zoom views show, for Stretch
        # From; pans and zooms settle before the stretch is computed again
        self._windowTimer = QTimer(self)
        self._windowTimer.setSingleShot(True)
        self._windowTimer.setInterval(150)
        self._windowTimer.timeout.connect(self.pushStretchWindows)
        self.mainController.sigRegionChanged.connect(self._windowTimer.start)
        self.zoomController.sigRegionChanged.connect(self._windowTimer.start)
        self.pushStretchWindows()

    def pushStretchWindows(self) -> None:
        height, width = self.viewport1.imageEntity.shape[:2]
        for source, controller in (
            (StretchSource.MAIN, self.mainController),
            (StretchSource.ZOOM, self.zoomController),
        ):
            self.imageRenderer.setStretchWindow(
                source, controller.imageWindow(height, width)
            )

    def addToolbarToViewport(self, viewport, toolbar):
        """Add a toolbar to a specific viewport."""
        if viewport is self.viewport1:
            self.viewport1.addToolBar(toolbar)
        elif viewport is self.viewport2:
            self.viewport2.addToolBar(toolbar)
        elif viewport is self.viewport3:
            self.viewport3.addToolBar(toolbar)
        else:
            raise ValueError("Invalid viewport specified.")
