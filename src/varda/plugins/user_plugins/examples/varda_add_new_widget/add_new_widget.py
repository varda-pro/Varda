import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QPushButton, QLabel, QVBoxLayout

import varda

logger = logging.getLogger(__name__)


@varda.plugins.hookimpl
def onLoad(app):
    """Hook called on plugin load."""
    logger.info("Plugin hook implementation called: varda_add_new_widget :O")
    app.registry.registerWidget(MyNewWidget)


class MyNewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.initUI()

    def initUI(self):
        self.setWindowTitle("My New Widget")
        self.setMinimumSize(200, 200)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.label = QLabel(self)
        self.button = QPushButton("Click Me", self)
        self.button.clicked.connect(self.onButtonClick)

        layout = QVBoxLayout()
        layout.addWidget(self.button)
        layout.addWidget(self.label)
        self.setLayout(layout)

    def onButtonClick(self):
        """Handle button click event."""
        self.label.setText("Hello World!")

    def onActivate(self):
        """Called when the widget is activated."""
        logger.info("MyNewWidget activated!")
