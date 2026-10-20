"""Overlay a co-registered project image on a workspace's views.

Any project image on the same pixel grid as the workspace's image can be laid
over it: a classification shows its classes in their colors (or one class's
probability), any other result one band through a color map, with its own
stretch. Pixels without data stay see-through. The bar sits under the main
view and drives every view it is given (the triple view's zoom panels follow
the main view). Clicking a class's swatch in the legend changes its color,
kept in the result's metadata, so every view showing it follows.
"""

from __future__ import annotations

import weakref
from collections.abc import Sequence
from html import escape

import numpy as np
import pyqtgraph as pg
from psygnal import Signal
from PyQt6 import sip
from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtGui import QAction, QActionGroup, QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QSizePolicy,
    QLabel,
    QMenu,
    QSlider,
    QToolButton,
    QWidget,
)

from varda.common.entities import VardaRaster
from varda.common.grid import sameGridChoices
from varda.common.observable_list import ObservableList
from varda.common.palette import classColor
from varda.common.parameter_widgets import connectWhileAlive
from varda.common.ui import ElidingComboBox, FlowLayout, MutedLabel
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering.image_renderer import ImageRenderer, RenderMode
from varda.image_rendering.render_parameters import StretchParameter
from varda.image_rendering.stretch_algorithms import LinearPercentileStretch
from varda.image_rendering.raster_view.image_viewport import ImageViewport

NO_OVERLAY = "No overlay"
CLASSES = "Classes"
# Color maps for a single-band overlay: menu name, pyqtgraph's name
COLOR_MAPS = {
    "Viridis": "viridis",
    "Magma": "magma",
    "Inferno": "inferno",
    "Plasma": "plasma",
    "Cividis": "cividis",
    "Turbo": "turbo",
    "Gray": "CET-L1",
    "Blue-White-Red": "CET-D1",  # diverging, for signed results
}
# Stretches for a single-band overlay: menu name, percentiles (None: min-max)
STRETCHES: dict[str, tuple[int, int] | None] = {
    "Full range": None,
    "2-98 %": (2, 98),
    "5-95 %": (5, 95),
}
CLASS_LINK = "class:"  # legend swatch links: class:<index>


class _ClassColorEvents:
    """Tells every overlay bar when a result's class colors change."""

    changed = Signal(object)  # the raster


classColorEvents = _ClassColorEvents()


def isClassMap(raster: VardaRaster) -> bool:
    return bool(raster.bandNames) and raster.bandNames[0] == "Class"


def overlayLayers(raster: VardaRaster) -> list[str]:
    names = list(raster.bandNames)
    return [CLASSES, *names[1:]] if isClassMap(raster) else names


def classColors(raster: VardaRaster) -> list[str]:
    names = raster.extraMetadata.get("classes", [])
    stored = list(raster.extraMetadata.get("classColors", []))
    return stored + [classColor(k) for k in range(len(stored), len(names))]


def setClassColor(raster: VardaRaster, index: int, color: str) -> None:
    """Change one class's color in the result's metadata, and tell every
    overlay showing it."""
    colors = classColors(raster)
    colors[index] = color
    raster.extraMetadata["classColors"] = colors
    classColorEvents.changed.emit(raster)


def colorMap(name: str, reverse: bool = False) -> pg.ColorMap:
    cmap = pg.colormap.get(COLOR_MAPS[name])
    if reverse:
        cmap.reverse()
    return cmap


def _like(raster: VardaRaster, data: np.ndarray, name: str) -> VardaRaster:
    bands = [f"{name} {i + 1}" for i in range(data.shape[2])]
    source = ArrayDataSource(
        data.astype(np.float32),
        bandNames=bands,
        transform=raster.transform,
        crs=raster.crs,
        defaultBands=np.arange(3) if data.shape[2] == 3 else None,
    )
    return VardaRaster(source, name=name)


def overlayRaster(raster: VardaRaster, layer: str) -> tuple[VardaRaster, bool]:
    """(image to render, whether it is RGB) for one layer of ``raster``."""
    if layer == CLASSES:
        classes = raster.getData()[:, :, 0]
        rgb = np.full(classes.shape + (3,), np.nan, dtype=np.float32)
        for k, color in enumerate(classColors(raster)):
            c = pg.mkColor(color)
            rgb[classes == k] = (c.redF(), c.greenF(), c.blueF())
        return _like(raster, rgb, CLASSES), True
    band = list(raster.bandNames).index(layer)
    return _like(raster, raster.getData()[:, :, band : band + 1], layer), False


class OverlayBar(QWidget):
    """Overlay picker, layer picker (when there's a choice) and opacity. A class
    map's legend goes over the first view's image, toggled from the bar."""

    def __init__(
        self,
        images: Sequence[VardaRaster],
        base: VardaRaster,
        viewports: Sequence[ImageViewport],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.images = images
        self.base = base
        self.viewports = list(viewports)
        self.renderer: ImageRenderer | None = None
        self._candidates: list[VardaRaster] = []
        # what the image box lists, (image, name): rebuilt only when it changes
        self._listed: list[tuple[VardaRaster, str]] | None = None
        self._watched: weakref.WeakSet[VardaRaster] = weakref.WeakSet()

        self.imageBox = ElidingComboBox()
        self.imageBox.setToolTip(
            "Lay another image of this scene (e.g. a classification) over the view"
        )
        # long names are elided, with the full name in the tooltip
        self.layerBox = ElidingComboBox()
        # the images left out (overlays need this image's grid): a count in
        # the bar, each image and why in its tooltip
        self.notListed = MutedLabel()
        self.opacityLabel = QLabel("Opacity:")
        self.opacitySlider = QSlider(Qt.Orientation.Horizontal)
        self.opacitySlider.setRange(0, 100)
        self.opacitySlider.setValue(60)
        self.opacitySlider.setFixedWidth(100)
        self.opacitySlider.setToolTip("Overlay opacity")
        self.legendButton = QToolButton()
        self.legendButton.setText("Legend")
        self.legendButton.setCheckable(True)
        self.legendButton.setChecked(True)
        self.legendButton.setToolTip(
            "Show the class colors over the image; click a swatch to change its color"
        )
        # A band overlay's color map and stretch, in one menu to keep the bar short
        self.colorsButton = QToolButton()
        self.colorsButton.setText("Colors")
        self.colorsButton.setToolTip("The overlay's color map and the values it spans")
        self.colorsButton.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.colorsButton)
        self.colorMapActions = self._choices(menu, COLOR_MAPS)
        self.reverseAction = QAction("Reverse", menu)
        self.reverseAction.setCheckable(True)
        menu.addAction(self.reverseAction)
        self.reverseAction.toggled.connect(lambda _: self._applyColors())
        menu.addSection("Stretch")
        self.stretchActions = self._choices(menu, STRETCHES)
        self.colorsButton.setMenu(menu)

        # Wraps onto another row in a narrow view instead of clipping
        layout = FlowLayout(self, margins=0)
        layout.setContentsMargins(4, 0, 4, 0)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        layout.addWidget(QLabel("Overlay:"))
        layout.addWidget(self.imageBox)
        layout.addWidget(self.notListed)
        layout.addWidget(self.layerBox)
        layout.addWidget(self.colorsButton)
        layout.addWidget(self.opacityLabel)
        layout.addWidget(self.opacitySlider)
        layout.addWidget(self.legendButton)

        self.imageBox.currentIndexChanged.connect(self._onImageChosen)
        self.layerBox.currentIndexChanged.connect(lambda _: self._apply())
        self.opacitySlider.valueChanged.connect(self._applyOpacity)
        self.legendButton.toggled.connect(lambda _: self._showLegend())
        if self.viewports:
            self.viewports[0].sigLegendLink.connect(self._onLegendLink)
        # psygnal holds the bar weakly, so a closed workspace drops out by itself
        classColorEvents.changed.connect(self._onClassColorsChanged)
        if isinstance(images, ObservableList):
            # a bound method: Qt disconnects it when the bar is deleted, while
            # the project's list lives on
            images.sigDataChanged.connect(self._refreshImages)
        self._refreshImages()
        self._updateControls()

    def _choices(self, menu: QMenu, names) -> QActionGroup:
        """One checkable action per name, the first checked, one at a time."""
        group = QActionGroup(menu)
        for name in names:
            action = QAction(name, menu)
            action.setCheckable(True)
            group.addAction(action)
            menu.addAction(action)
        group.actions()[0].setChecked(True)
        group.triggered.connect(lambda _: self._applyColors())
        return group

    # --- public ----------------------------------------------------------------

    def setColors(
        self,
        colorMap: str | None = None,
        reverse: bool | None = None,
        stretch: str | None = None,
    ) -> None:
        """Choose the band overlay's color map, direction and stretch."""
        for group, name in (
            (self.colorMapActions, colorMap),
            (self.stretchActions, stretch),
        ):
            if name is not None:
                next(a for a in group.actions() if a.text() == name).setChecked(True)
        if reverse is not None:
            with QSignalBlocker(self.reverseAction):
                self.reverseAction.setChecked(reverse)
        self._applyColors()

    def colorChoices(self) -> tuple[str, bool, str]:
        """(color map, reversed, stretch)."""
        colorMap = self.colorMapActions.checkedAction()
        stretch = self.stretchActions.checkedAction()
        assert colorMap is not None and stretch is not None
        return colorMap.text(), self.reverseAction.isChecked(), stretch.text()

    def currentOverlay(self) -> VardaRaster | None:
        index = self.imageBox.currentIndex()
        return self._candidates[index - 1] if index > 0 else None

    def setOverlay(self, image: VardaRaster | None) -> None:
        index = 0 if image is None else 1 + self._candidates.index(image)
        self.imageBox.setCurrentIndex(index)

    # --- internals -------------------------------------------------------------

    def _refreshImages(self) -> None:
        """List the project's images on this grid. The views are redrawn only
        when the overlay shown is gone from the list (removed, or no longer
        on this grid); every other change to the project leaves them be."""
        current = self.currentOverlay()
        choices = sameGridChoices(list(self.images), self.base)
        left = len(choices.excluded)
        images = "1 image" if left == 1 else f"{left} images"
        self.notListed.setText(
            f"{images} can't be overlaid: different pixel grid" if left else ""
        )
        self.notListed.setToolTip(choices.note)
        self.notListed.setVisible(bool(left))
        listed = [(image, image.name) for image in choices.images]
        if listed == self._listed:
            return
        self._listed = listed
        self._candidates = choices.images
        with QSignalBlocker(self.imageBox):
            self.imageBox.clear()
            self.imageBox.addItem(NO_OVERLAY)
            for image in self._candidates:
                self.imageBox.addItem(image.name)
                self.imageBox.setItemData(
                    self.imageBox.count() - 1, image.name, Qt.ItemDataRole.ToolTipRole
                )
                if image not in self._watched:
                    self._watched.add(image)
                    connectWhileAlive(
                        image.sigMetadataChanged, self._onImageEdited, self
                    )
            stillListed = current in self._candidates
            self.setOverlay(current if stillListed else None)
        if current is not None and not stillListed:
            self._onImageChosen()

    def _onImageEdited(self) -> None:
        """An image was edited: list it under its new name and, when it's the
        overlay shown, show it again (its no-data value, band names), on the
        same layer if it still has it."""
        edited = Signal.sender()
        self._refreshImages()
        if edited is not self.currentOverlay():
            return
        layer = self.layerBox.currentText()
        self._fillLayers()
        with QSignalBlocker(self.layerBox):
            index = self.layerBox.findText(layer)
            if index >= 0:
                self.layerBox.setCurrentIndex(index)
        self._apply()

    def _onImageChosen(self, *_args) -> None:
        self._fillLayers()
        self._apply()

    def _fillLayers(self) -> None:
        overlay = self.currentOverlay()
        with QSignalBlocker(self.layerBox):
            self.layerBox.clear()
            if overlay is not None:
                self.layerBox.addItems(overlayLayers(overlay))

    def _updateControls(self) -> None:
        """Show the controls (and legend) the chosen overlay and layer need."""
        overlay = self.currentOverlay()
        layer = self.layerBox.currentText()
        hasOverlay = overlay is not None and bool(layer)
        self.layerBox.setVisible(hasOverlay and self.layerBox.count() > 1)
        self.opacityLabel.setVisible(hasOverlay)
        self.opacitySlider.setVisible(hasOverlay)
        self.legendButton.setVisible(hasOverlay and layer == CLASSES)
        self.colorsButton.setVisible(hasOverlay and layer != CLASSES)
        self._showLegend()

    def _apply(self) -> None:
        overlay = self.currentOverlay()
        layer = self.layerBox.currentText()
        self._updateControls()
        for viewport in self.viewports:
            if viewport.hasOverlayImage():
                viewport.removeOverlayImage()
        self.renderer = None
        if overlay is None or not layer:
            self._refreshViews()
            return
        raster, isRgb = overlayRaster(overlay, layer)
        renderer = ImageRenderer(image=raster)
        renderer.transparentNoData = True
        if isRgb:
            renderer.settings.mode.set(RenderMode.RGB)
            renderer.setManualStretch(0.0, 1.0)
        else:
            self._setColors(renderer)
        renderer.settings.opacity.set(self.opacitySlider.value() / 100)
        self.renderer = renderer
        for viewport in self.viewports:
            viewport.overlayImage(renderer)
        self._refreshViews()

    def _setColors(self, renderer: ImageRenderer) -> None:
        """The chosen color map and stretch on a single-band overlay."""
        settings = renderer.settings
        mapName, reverse, stretchName = self.colorChoices()
        with settings.sigParameterChanged.blocked():
            settings.mono.colorMap.set(colorMap(mapName, reverse))
            percentiles = STRETCHES[stretchName]
            if percentiles is None:
                settings.stretch.selectByName(StretchParameter.DEFAULT_NAME)
            else:
                percentile = settings.stretch.option("Linear Percentile")
                assert isinstance(percentile, LinearPercentileStretch)
                percentile.config.lowPercent.set(percentiles[0])
                percentile.config.highPercent.set(percentiles[1])
                settings.stretch.selectByName("Linear Percentile")
        settings.sigParameterChanged.emit(settings)

    def _applyColors(self) -> None:
        if self.renderer is not None and self.layerBox.currentText() != CLASSES:
            self._setColors(self.renderer)
            self._refreshViews()

    def _onLegendLink(self, link: str) -> None:
        overlay = self.currentOverlay()
        if overlay is None or not link.startswith(CLASS_LINK):
            return
        index = int(link.removeprefix(CLASS_LINK))
        names = overlay.extraMetadata.get("classes", [])
        current = QColor(classColors(overlay)[index])
        color = QColorDialog.getColor(
            current, self, f"Color of {names[index] if index < len(names) else 'class'}"
        )
        if color.isValid():
            setClassColor(overlay, index, color.name())

    def _onClassColorsChanged(self, raster: VardaRaster) -> None:
        if sip.isdeleted(self):  # Qt deleted it; Python hasn't yet
            return
        if raster is self.currentOverlay():
            self._apply()

    def _showLegend(self) -> None:
        """The class key over the first view, when a class map is shown and
        the Legend button is on."""
        if not self.viewports:
            return
        overlay = self.currentOverlay()
        show = (
            overlay is not None
            and self.layerBox.currentText() == CLASSES
            and self.legendButton.isChecked()
        )
        self.viewports[0].setLegend(
            self._legendHtml(overlay) if show else "", clickable=True
        )

    def _refreshViews(self) -> None:
        for viewport in self.viewports:
            viewport.refresh()

    def _applyOpacity(self, value: int) -> None:
        if self.renderer is not None:
            self.renderer.settings.opacity.set(value / 100)
            self._refreshViews()

    @staticmethod
    def _legendHtml(raster: VardaRaster) -> str:
        """One class per line, so a long list grows down the image; each
        swatch is a link that changes its color."""
        names = raster.extraMetadata.get("classes", [])
        return "<br>".join(
            f'<a href="{CLASS_LINK}{k}" style="color:{color}; text-decoration:none">'
            f"&#9632;</a>&nbsp;{escape(str(name))}"
            for k, (name, color) in enumerate(zip(names, classColors(raster)))
        )
