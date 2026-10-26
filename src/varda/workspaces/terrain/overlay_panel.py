"""The 3D Terrain workspace's overlays: images draped over part of the DEM
(not on its grid), each with its display settings, opacity and alignment.

Opacity and visibility change the view at once (sigLook); a new overlay, new
display settings or a new alignment need the textures built again
(sigRebuild); Auto Align is the workspace's job (sigAlignRequested), as it
needs the DEM and runs in the background.

Auto Align can match another image than the overlay ("Match using"): a
parameter map has little to match, but a scene simulated from the same
observation (its reflectance convolved to the reference's band, say) shares
its georeferencing, so the alignment found for it moves the overlay too.
Alignments can also be copied from one overlay to another."""

from __future__ import annotations

import math
from collections.abc import Callable

from attrs import define, field
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from varda.common.entities import VardaRaster
from varda.common.ui import MutedLabel
from varda.image_rendering.image_renderer import ImageRenderer, RenderMode
from varda.mosaicking.transforms import TransformModel
from varda.terrain.overlays import Alignment

HILLSHADE = "DEM hillshade"
MATCH_ITSELF = "This overlay"
DEFAULT_OPACITY = 0.85


@define(eq=False)
class TerrainOverlay:
    image: VardaRaster
    renderer: ImageRenderer
    opacity: float = DEFAULT_OPACITY
    visible: bool = True
    alignment: Alignment = field(factory=Alignment)
    reference: str = HILLSHADE  # what Auto Align matches against (an image's name)
    matchWith: str = ""  # the image Auto Align matches for it (a name; "": itself)

    @classmethod
    def of(cls, image: VardaRaster) -> TerrainOverlay:
        renderer = ImageRenderer(image=image)
        renderer.transparentNoData = True
        if image.bandCount >= 3:
            renderer.settings.mode.set(RenderMode.RGB)
        return cls(image, renderer)


class OverlayPanel(QWidget):
    sigRebuild = pyqtSignal()
    sigLook = pyqtSignal(int, float, bool)  # overlay, opacity, visible
    sigAlignRequested = pyqtSignal(int)
    sigSelected = pyqtSignal(object)  # the selected TerrainOverlay, or None
    sigDisplayRequested = pyqtSignal()  # show the Overlay Display dock

    def __init__(
        self,
        candidates: Callable[[], list[VardaRaster]],
        references: Callable[[], list[VardaRaster]],
        parent: QWidget | None = None,
    ):
        """``candidates``: images that may be added (georeferenced, on the
        DEM's body); ``references``: images on the DEM's grid to align to."""
        super().__init__(parent)
        self.overlays: list[TerrainOverlay] = []
        self.mapUnit = "m"  # the DEM's map unit: shifts and RMS are in it
        self._candidates = candidates
        self._references = references

        self.list = QListWidget()
        self.list.setToolTip(
            "Tick to show; select one to set its opacity and alignment"
        )
        self.list.itemChanged.connect(self._onItemChanged)
        self.list.currentRowChanged.connect(lambda _: self.showSelected())
        # a push button with a menu is sized for the menu's arrow
        self.addButton = QPushButton("Add Overlay")
        self.addMenu = QMenu(self.addButton)
        self.addMenu.aboutToShow.connect(self._fillAddMenu)
        self.addButton.setMenu(self.addMenu)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.removeSelected)

        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.valueChanged.connect(self._onOpacity)
        display = QPushButton("Display…")
        display.setToolTip(
            "Its bands, stretch and color map, with histograms (the Overlay "
            "Display panel)"
        )
        display.clicked.connect(self._editDisplay)
        self.referenceBox = QComboBox()
        self.referenceBox.setToolTip(
            "What Auto Align matches the overlay against: an image on the DEM's "
            "grid (its orthoimage is best) or a hillshade of the DEM"
        )
        self.referenceBox.currentTextChanged.connect(self._onReference)
        self.matchBox = QComboBox()
        self.matchBox.setToolTip(
            "The image Auto Align matches: the overlay itself, or another image "
            "with the same georeferencing (e.g. a scene simulated from the same "
            "observation, which matches better than a parameter map); the "
            "alignment found moves the overlay"
        )
        self.matchBox.currentTextChanged.connect(self._onMatch)
        self.modelBox = QComboBox()
        for model in TransformModel:
            self.modelBox.addItem(model.value, model)
        self.modelBox.setToolTip("How the overlay may move to fit")
        self.modelBox.currentIndexChanged.connect(self._onModel)
        self.alignButton = QPushButton("Auto Align")
        self.alignButton.setToolTip(
            "Match the overlay's best band against the reference and fit the model"
        )
        self.alignButton.clicked.connect(self._requestAlign)
        self.shiftEast, self.shiftNorth = QDoubleSpinBox(), QDoubleSpinBox()
        for box in (self.shiftEast, self.shiftNorth):
            box.setRange(-1e6, 1e6)
            box.setToolTip("A shift by hand, in the DEM's map units")
            box.editingFinished.connect(self._onShift)
        self.setMapUnit(self.mapUnit)
        copy = QPushButton("Copy")
        copy.setToolTip("Copy this overlay's alignment, to paste on another")
        copy.clicked.connect(self.copyAlignment)
        self.pasteButton = QPushButton("Paste")
        self.pasteButton.setToolTip("Give this overlay the copied alignment")
        self.pasteButton.setEnabled(False)
        self.pasteButton.clicked.connect(self.pasteAlignment)
        self._copied: dict | None = None  # a copied alignment's state
        reset = QPushButton("Reset")
        reset.setToolTip("Back to the overlay's own georeferencing")
        reset.clicked.connect(self._resetAlignment)
        self.alignment = MutedLabel("")
        self.alignment.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.addButton)
        buttons.addWidget(remove)
        buttons.addStretch()
        self.details = QWidget()
        form = QFormLayout(self.details)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Opacity:", self.opacity)
        form.addRow("", display)
        form.addRow("Align to:", self.referenceBox)
        form.addRow("Match using:", self.matchBox)
        form.addRow("Model:", self.modelBox)
        form.addRow("", self.alignButton)
        shift = QHBoxLayout()
        shift.addWidget(self.shiftEast)
        shift.addWidget(self.shiftNorth)
        form.addRow("Shift E, N:", shift)
        alignment = QHBoxLayout()
        for button in (copy, self.pasteButton, reset):
            alignment.addWidget(button)
        form.addRow("Alignment:", alignment)
        form.addRow(self.alignment)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        layout.addWidget(self.details)
        self.showSelected()

    def setMapUnit(self, unit: str) -> None:
        """Label shifts and RMS with the DEM's map unit (``mapUnit``): ° for
        a geographic DEM, m for one projected in meters."""
        self.mapUnit = unit
        degrees = unit == "°"
        for box in (self.shiftEast, self.shiftNorth):
            box.setDecimals(6 if degrees else 1)
            box.setSingleStep(1e-4 if degrees else 5)
            box.setSuffix(unit if degrees else f" {unit}")
        overlay = self.selected()
        if overlay is not None:
            self.showAlignment(overlay)

    # --- the list ------------------------------------------------------------------

    def _fillAddMenu(self) -> None:
        self.addMenu.clear()
        shown = {id(o.image) for o in self.overlays}
        images = [i for i in self._candidates() if id(i) not in shown]
        if not images:
            action = self.addMenu.addAction(
                "No other georeferenced images of this body"
            )
            assert action is not None
            action.setEnabled(False)
        for image in images:
            self.addMenu.addAction(image.name, lambda image=image: self.add(image))

    def add(self, image: VardaRaster) -> TerrainOverlay:
        overlay = TerrainOverlay.of(image)
        references = [r.name for r in self._references() if r.bandCount == 1]
        overlay.reference = references[0] if references else HILLSHADE
        self.overlays.append(overlay)
        self._addItem(overlay)
        self.list.setCurrentRow(len(self.overlays) - 1)
        self.sigRebuild.emit()
        self.sigDisplayRequested.emit()  # its bands and stretch, at hand
        return overlay

    def _addItem(self, overlay: TerrainOverlay) -> None:
        item = QListWidgetItem(overlay.image.name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if overlay.visible else Qt.CheckState.Unchecked
        )
        self.list.blockSignals(True)
        self.list.addItem(item)
        self.list.blockSignals(False)

    def setOverlays(self, overlays: list[TerrainOverlay]) -> None:
        self.overlays = list(overlays)
        self.list.clear()
        for overlay in self.overlays:
            self._addItem(overlay)
        if self.overlays:
            self.list.setCurrentRow(0)
        self.showSelected()

    def removeSelected(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.overlays):
            del self.overlays[row]
            self.list.takeItem(row)
            self.showSelected()
            self.sigRebuild.emit()

    def selected(self) -> TerrainOverlay | None:
        row = self.list.currentRow()
        return self.overlays[row] if 0 <= row < len(self.overlays) else None

    def _onItemChanged(self, item: QListWidgetItem) -> None:
        row = self.list.row(item)
        overlay = self.overlays[row]
        overlay.visible = item.checkState() == Qt.CheckState.Checked
        self.sigLook.emit(row, overlay.opacity, overlay.visible)

    # --- the selected overlay -----------------------------------------------------------

    def showSelected(self) -> None:
        overlay = self.selected()
        self.sigSelected.emit(overlay)
        self.details.setEnabled(overlay is not None)
        if overlay is None:
            self.alignment.setText("")
            return
        for widget in (
            self.opacity,
            self.referenceBox,
            self.matchBox,
            self.modelBox,
            self.shiftEast,
            self.shiftNorth,
        ):
            widget.blockSignals(True)
        self.opacity.setValue(round(overlay.opacity * 100))
        self.matchBox.clear()
        self.matchBox.addItem(MATCH_ITSELF)
        self.matchBox.addItems(
            [i.name for i in self._candidates() if i is not overlay.image]
        )
        self.matchBox.setCurrentText(overlay.matchWith or MATCH_ITSELF)
        self.referenceBox.clear()
        self.referenceBox.addItems(
            [*[r.name for r in self._references() if r.bandCount == 1], HILLSHADE]
        )
        self.referenceBox.setCurrentText(overlay.reference)
        self.modelBox.setCurrentIndex(self.modelBox.findData(overlay.alignment.model))
        self.shiftEast.setValue(overlay.alignment.shift[0])
        self.shiftNorth.setValue(overlay.alignment.shift[1])
        for widget in (
            self.opacity,
            self.referenceBox,
            self.matchBox,
            self.modelBox,
            self.shiftEast,
            self.shiftNorth,
        ):
            widget.blockSignals(False)
        self.showAlignment(overlay)

    def showAlignment(self, overlay: TerrainOverlay, message: str = "") -> None:
        if overlay is not self.selected():
            return
        a = overlay.alignment
        fitted = (
            f"Fitted to {a.points} tie points, RMS {a.rms:.3g}"
            f"{'' if self.mapUnit == '°' else ' '}{self.mapUnit}."
            if a.points
            else "Placed by its georeferencing (not aligned yet)."
        )
        if a.points and a.band is not None:
            matched = f"Matched on {a.matchedOn}, {a.bandLabel}" + (
                f" (SNR {a.bandSnr:.3g})" if math.isfinite(a.bandSnr) else ""
            )
            if math.isfinite(a.phaseSnr):
                matched += f"; median phase-plane SNR {a.phaseSnr:.2f}"
            if a.sun is not None:
                azimuth, elevation, r = a.sun
                matched += f"; hillshade lit from {azimuth:g}° at {elevation:g}°" + (
                    f" (r {r:.2f})" if math.isfinite(r) else ""
                )
            fitted += f" {matched}."
        self.alignment.setText(f"{message} {fitted}".strip())

    def _onOpacity(self, value: int) -> None:
        overlay = self.selected()
        if overlay is not None:
            overlay.opacity = value / 100
            self.sigLook.emit(self.list.currentRow(), overlay.opacity, overlay.visible)

    def _onReference(self, name: str) -> None:
        overlay = self.selected()
        if overlay is not None and name:
            overlay.reference = name

    def _onMatch(self, name: str) -> None:
        overlay = self.selected()
        if overlay is not None and name:
            overlay.matchWith = "" if name == MATCH_ITSELF else name

    def copyAlignment(self) -> None:
        overlay = self.selected()
        if overlay is not None:
            self._copied = overlay.alignment.state()
            self.pasteButton.setEnabled(True)

    def pasteAlignment(self) -> None:
        overlay = self.selected()
        if overlay is not None and self._copied is not None:
            overlay.alignment = Alignment.fromState(self._copied)
            self.showSelected()
            self.sigRebuild.emit()

    def _onModel(self, _index: int) -> None:
        overlay = self.selected()
        if overlay is not None:
            overlay.alignment.model = self.modelBox.currentData()

    def _onShift(self) -> None:
        overlay = self.selected()
        if overlay is None:
            return
        shift = (self.shiftEast.value(), self.shiftNorth.value())
        if shift != tuple(overlay.alignment.shift):
            overlay.alignment.shift = shift
            self.sigRebuild.emit()

    def _resetAlignment(self) -> None:
        overlay = self.selected()
        if overlay is not None:
            overlay.alignment = Alignment(overlay.alignment.model)
            self.showSelected()
            self.sigRebuild.emit()

    def _requestAlign(self) -> None:
        if self.selected() is not None:
            self.sigAlignRequested.emit(self.list.currentRow())

    def _editDisplay(self) -> None:
        if self.selected() is not None:
            self.sigDisplayRequested.emit()
