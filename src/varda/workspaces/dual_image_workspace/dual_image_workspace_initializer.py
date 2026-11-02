from PyQt6.QtWidgets import QDialog, QMessageBox
from PyQt6.QtCore import pyqtSignal
from varda.common.entities import VardaRaster
from varda.workspaces.dual_image_workspace.dual_image_workspace import (
    DualImageWorkspaceConfig,
    DualImageWorkspace,
)
from varda.common.ui import VBoxBuilder, HBoxBuilder, ButtonBuilder
from varda.common.parameter_widgets import groupWidget


class NewDualImageWorkspaceDialog(QDialog):
    sigCreateWorkspace = pyqtSignal(object)

    def __init__(
        self,
        imageList,
        parent=None,
        *,
        primary: VardaRaster | None = None,
        secondary: VardaRaster | None = None,
    ):
        """``primary``/``secondary`` pre-select images in the dialog; unset slots
        keep their default (the first image in ``imageList``)."""
        super().__init__(parent)
        self.setWindowTitle("Create New Dual Image Workspace")
        if len(imageList) == 0:
            QMessageBox.warning(
                self, "No Images", "No images available to create a workspace."
            )
            self.reject()
            return

        self.dualImageWorkspaceConfig = DualImageWorkspaceConfig(imageList)
        if primary is not None:
            self.dualImageWorkspaceConfig.image1Param.set(primary)
        if secondary is not None:
            self.dualImageWorkspaceConfig.image2Param.set(secondary)
        self.accepted.connect(
            lambda: self.sigCreateWorkspace.emit(
                DualImageWorkspace(self.dualImageWorkspaceConfig)
            )
        )

        self.setLayout(
            VBoxBuilder()
            .withWidget(groupWidget(self.dualImageWorkspaceConfig))
            .withStretch()
            .withLayout(
                HBoxBuilder()
                .withWidget(ButtonBuilder("Finish").onClick(self.accept))
                .withStretch()
                .withWidget(ButtonBuilder("Cancel").onClick(self.reject))
            )
        )

    def connectOnAccept(self, callback):
        self.sigCreateWorkspace.connect(callback)
        return self


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    from varda.utilities import debug

    app = QApplication(sys.argv)
    dialog = NewDualImageWorkspaceDialog([debug.generate_random_image()])
    dialog.connectOnAccept(lambda workspace: workspace.show())
    dialog.show()
    sys.exit(app.exec())
