"""The size of a 3D view's still, for the DEM and shape model pages."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QSpinBox,
    QWidget,
)

STILL_SIZES = {
    "HD (1920 × 1080)": (1920, 1080),
    "4K (3840 × 2160)": (3840, 2160),
    "8K (7680 × 4320)": (7680, 4320),
    "Square (3000 × 3000)": (3000, 3000),
}
MAX_STILL = 16384  # px on a side: WebGL's canvas limit


class StillDialog(QDialog):
    """The size of a still and whether its background is transparent."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Export Still")
        self.preset = QComboBox()
        self.preset.addItems([*STILL_SIZES, "Custom"])
        self.preset.setCurrentText("4K (3840 × 2160)")
        self.widthBox = QSpinBox()
        self.heightBox = QSpinBox()
        for box in (self.widthBox, self.heightBox):
            box.setRange(64, MAX_STILL)
            box.setSuffix(" px")
        self.transparent = QCheckBox("Transparent background")
        self.preset.currentTextChanged.connect(self._onPreset)
        self._onPreset(self.preset.currentText())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout(self)
        form.addRow("Size:", self.preset)
        form.addRow("Width:", self.widthBox)
        form.addRow("Height:", self.heightBox)
        form.addRow("", self.transparent)
        form.addRow(buttons)

    def _onPreset(self, name: str) -> None:
        size = STILL_SIZES.get(name)
        for box in (self.widthBox, self.heightBox):
            box.setEnabled(size is None)
        if size is not None:
            self.widthBox.setValue(size[0])
            self.heightBox.setValue(size[1])

    def stillSize(self) -> tuple[int, int]:
        return self.widthBox.value(), self.heightBox.value()
