import logging

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QWidget, QPushButton, QVBoxLayout

import varda
from . import vectroscopy_lite

logger = logging.getLogger(__name__)


@varda.plugins.hookimpl
def onLoad(app):
    """Hook called on plugin load."""
    logger.debug("hook called onLoad")
    app.registry.registerWidget(VectroscopyWidget)


class VectroscopyWidget(QWidget):
    """A simple widget for demonstrating a custom user plugin in Varda."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vectroscopy Widget")
        self.setMinimumSize(300, 200)

        layout = QVBoxLayout()
        self.proj = app.proj

        # Add image selection combobox at the top
        if self.proj:
            # Create a label for the image selection
            imageLabel = QtWidgets.QLabel("Select Image:")
            imageLabel.setToolTip("Select the image")

            # Create the combobox
            self.image_combobox = QtWidgets.QComboBox()

            # Get all images from the project
            all_images = self.proj.getAllImages()

            # Populate the combobox with image names
            for i, img in enumerate(all_images):
                name = img.metadata.name or f"Image {i}"
                self.image_combobox.addItem(
                    name, i
                )  # Store the image index as user data

            # Set the current image as the selected item if it exists
            self.image_combobox.setCurrentIndex(0)

            # Connect the combobox signal to update the selected image
            self.image_combobox.currentIndexChanged.connect(self.updateSelectedImage)

            # Add the combobox to the layout
            layout.addWidget(imageLabel, self.image_combobox)

        self.button = QPushButton("start")
        self.button.clicked.connect(self.startVectroscopy)
        self.setLayout(layout)

    def startVectroscopy(self):
        array = self.image.raster[:, :, 0]
        crs = self.image.metadata.crs
        transform = self.image.metadata.transform
        name = self.image.metadata.name

        result = vectroscopy_lite.Vectroscopy.from_array(array, crs, transform, name)

        print(result)

    def updateSelectedImage(self):
        self.image = self.proj.getImage(self.image_combobox.currentIndex())
