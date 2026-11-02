# standard library
import logging

# third party imports
from PyQt6.QtWidgets import QMenu

logger = logging.getLogger(__name__)


class ProcessingMenu(QMenu):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setTitle("Image Processing")
        self.refreshProcessingMenu()

    def refreshProcessingMenu(self):
        self.clear()
        for name, process in self.app.registry.imageProcesses:
            logger.debug("process being added to menu: %s", name)
            # Create action with the class name as text
            action = self.addAction(name)
            # Store the process class in the action data
            action.setData(process)
