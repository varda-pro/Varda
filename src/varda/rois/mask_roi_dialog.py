"""New ROI Class from a Threshold or a Mask: the pixels where a band is
inside (or outside) a range, or where a mask image is set (its first band,
nonzero; a mask on another grid is resampled onto the image's by nearest
neighbor), become ROIs of one class, one per connected region (ENVI's band
threshold to ROI)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from rasterio.enums import Resampling
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from varda.common.entities import Color, VardaRaster
from varda.common.grid import onGridOf, placeableChoices, resamplingText
from varda.common.palette import classColor
from varda.common.ui import MutedLabel, notListedLabel
from varda.rois.mask_rois import addMaskAsRois, thresholdMask
from varda.rois.roi_collection import ROICollection


class NewClassFromMaskDialog(QDialog):
    def __init__(
        self,
        rois: ROICollection,
        image: VardaRaster,
        images: Sequence[VardaRaster] = (),
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("New ROI Class from a Threshold or a Mask")
        self.rois, self.image = rois, image
        choices = placeableChoices(list(images), image)
        self.masks = choices.images

        self.fromThreshold = QRadioButton("A band's range")
        self.fromMask = QRadioButton(
            "A mask image (where its first band is set, i.e. nonzero)"
        )
        self.fromThreshold.setChecked(True)
        self.fromMask.setEnabled(bool(self.masks))
        group = QButtonGroup(self)
        group.addButton(self.fromThreshold)
        group.addButton(self.fromMask)
        group.buttonToggled.connect(lambda *_: self._update())

        self.band = QSpinBox()
        self.band.setRange(1, image.bandCount)
        self.low, self.high = QDoubleSpinBox(), QDoubleSpinBox()
        for box in (self.low, self.high):
            box.setRange(-1e12, 1e12)
            box.setDecimals(6)
            box.valueChanged.connect(lambda _: self._update())
        self.side = QComboBox()
        self.side.addItems(["Inside the range", "Outside the range"])
        self.side.currentIndexChanged.connect(lambda _: self._update())
        self.band.valueChanged.connect(lambda _: self._fitRange())
        self.maskBox = QComboBox()
        for mask in self.masks:
            self.maskBox.addItem(mask.name)
        self.maskBox.currentIndexChanged.connect(lambda _: self._update())
        self.notListed = notListedLabel(choices.note)
        self.resampled = MutedLabel("")
        self.resampled.setWordWrap(True)

        self.name = QLineEdit(f"Threshold {len(rois.classes()) + 1}")
        self.color = QColor(classColor(len(rois.classes())))
        self.colorButton = QPushButton()
        self.colorButton.clicked.connect(self._chooseColor)
        self._showColor()
        self.minPixels = QSpinBox()
        self.minPixels.setRange(1, 1_000_000)
        self.minPixels.setValue(4)
        self.minPixels.setSuffix(" pixels")
        self.minPixels.setToolTip("Smaller regions (specks) are left out")
        self.count = MutedLabel("")

        rangeRow = QHBoxLayout()
        rangeRow.addWidget(self.low)
        rangeRow.addWidget(self.high)
        form = QFormLayout()
        form.addRow(self.fromThreshold)
        form.addRow("Band:", self.band)
        form.addRow("From, to:", rangeRow)
        form.addRow("Pixels:", self.side)
        form.addRow(self.fromMask)
        form.addRow("Mask:", self.maskBox)
        form.addRow(self.resampled)
        form.addRow(self.notListed)
        form.addRow("Class name:", self.name)
        form.addRow("Color:", self.colorButton)
        form.addRow("Smallest region:", self.minPixels)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.count)
        layout.addWidget(
            MutedLabel(
                "Each connected region becomes an ROI of the class; holes inside a "
                "region are filled."
            )
        )
        layout.addWidget(buttons)
        self._fitRange()

    def _fitRange(self) -> None:
        """Start the range at the band's minimum and maximum."""
        raw = self.image.getBands([self.band.value() - 1])
        values = np.ma.filled(np.ma.asarray(raw, dtype=np.float64), np.nan)
        if np.isfinite(values).any():
            for box, value in (
                (self.low, np.nanmin(values)),
                (self.high, np.nanmax(values)),
            ):
                box.blockSignals(True)
                box.setValue(float(value))
                box.blockSignals(False)
        self._update()

    def _chooseColor(self) -> None:
        chosen = QColorDialog.getColor(self.color, self, "Class Color")
        if chosen.isValid():
            self.color = chosen
            self._showColor()

    def _showColor(self) -> None:
        self.colorButton.setStyleSheet(f"background-color: {self.color.name()};")

    def chosenPixels(self) -> np.ndarray:
        """The chosen pixels on the image's grid."""
        if self.fromMask.isChecked() and self.masks:
            values = onGridOf(self.chosenMask(), self.image, Resampling.nearest, [0])
            return np.isfinite(values[..., 0]) & (values[..., 0] != 0)
        return thresholdMask(
            self.image,
            self.band.value() - 1,
            self.low.value(),
            self.high.value(),
            inside=self.side.currentIndex() == 0,
        )

    def chosenMask(self) -> VardaRaster:
        return self.masks[self.maskBox.currentIndex()]

    def _update(self) -> None:
        threshold = self.fromThreshold.isChecked()
        for widget in (self.band, self.low, self.high, self.side):
            widget.setEnabled(threshold)
        self.maskBox.setEnabled(not threshold)
        resampled = (
            resamplingText(self.chosenMask(), self.image, Resampling.nearest)
            if self.masks
            else ""
        )
        self.resampled.setText(resampled)
        self.resampled.setVisible(bool(resampled))
        chosen = int(self.chosenPixels().sum())
        total = self.image.width * self.image.height
        self.count.setText(
            f"{chosen:,} of {total:,} pixels ({100 * chosen / total:.1f}%)"
        )

    def accept(self) -> None:
        mask = self.chosenPixels()
        name = self.name.text().strip()
        if not name:
            QMessageBox.information(self, self.windowTitle(), "Give the class a name.")
            return
        if not mask.any():
            QMessageBox.information(self, self.windowTitle(), "No pixels are chosen.")
            return
        added, dropped = addMaskAsRois(
            self.rois,
            mask,
            self.image,
            name,
            Color.fromQColor(self.color),
            self.minPixels.value(),
        )
        if dropped:
            QMessageBox.information(
                self,
                self.windowTitle(),
                f"Added {added} ROIs to {name}; {dropped} smaller regions were left "
                "out (raise the smallest region to drop more, lower it to keep them).",
            )
        super().accept()
