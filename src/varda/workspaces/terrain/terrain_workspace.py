"""The 3D Terrain workspace: a DEM as a lit, shadowed surface, with shaded
relief, elevation colors or an image on its grid draped over it; vertical
exaggeration, the sun's direction, saved camera views, high-resolution
stills, and a self-contained web page to share."""

from __future__ import annotations

import base64
import json
import shutil
import tempfile
from collections.abc import MutableSequence, Sequence
from pathlib import Path

import attrs
import geopandas as gpd
import numpy as np
import shapely
from pyproj import CRS
from rasterio.crs import CRS as RasterioCRS
from rasterio.transform import array_bounds
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from varda.common.entities import VardaRaster
from varda.common.grid import compatibleImages, sameGridChoices
from varda.common.observable_list import ObservableList
from varda.common.palette import classColor
from varda.common.ui import MutedLabel, PanelScrollArea, notListedLabel
from varda.image_rendering.image_renderer import ImageRenderer, RenderMode
from varda.lines.line_collection import (
    LineCollection,
    LineKind,
    VardaLine,
    sharedLines,
)
from varda.plotting.profile_plot import ProfilePlotWidget
from varda.image_rendering.render_state import restoreSettings, settingsState
from varda.image_rendering.stretch_algorithms import LinearPercentileStretch
from varda.terrain.overlays import (
    Alignment,
    align,
    asRaster,
    bandArray,
    bandLabel,
    bestBand,
    hillshade,
    toProjection,
)
from varda.terrain.overlays import drape as drapeOverlay
from varda.terrain.animation import FORMATS as ANIMATION_FORMATS
from varda.terrain.animation import writeAnimation
from varda.terrain.solid import printSolid, stlBytes
from varda.terrain.vectors import PageLine, placeLines, readLines
from varda.terrain.scene import (
    FILL_METHOD,
    Legend,
    PageOverlay,
    Terrain,
    TerrainSettings,
    buildTerrain,
    groundSize,
    mapUnit,
    pageHtml,
    pngDataUrl,
    verticalUnit,
)
from varda.utilities.background import runInBackground
from varda.workspaces.raster_overlay import COLOR_MAPS, colorMap
from varda.workspaces.find_data.basemap import imageBody
from varda.workspaces.terrain.overlay_display import OverlayDisplay
from varda.workspaces.terrain.overlay_panel import OverlayPanel, TerrainOverlay
from varda.workspaces.terrain.terrain_view import TerrainView

PLAIN, ELEVATION = "None (shaded relief)", "Elevation colors"
DETAIL = {"Low": 512, "Medium": 1024, "High": 2048}
BACKGROUNDS = {"Dark": "#101418", "Black": "#000000", "White": "#ffffff"}
HEIGHT_UNIT_NAMES = {"Meters": "m", "Kilometers": "km", "Feet": "ft"}
EDGES = {
    "Smooth": "smooth",
    "Stepped": "stepped",
    "Trimmed to a rectangle": "rectangle",
}
SIDE_COLORS = {
    "Earth": "#a08e78",
    "Light gray": "#bdbdbd",
    "White": "#f0f0f0",
    "Dark gray": "#4a4d52",
    "Black": "#16181b",
}
CUSTOM = "Custom…"
STILL_SIZES = {
    "HD (1920 × 1080)": (1920, 1080),
    "4K (3840 × 2160)": (3840, 2160),
    "8K (7680 × 4320)": (7680, 4320),
    "Square (3000 × 3000)": (3000, 3000),
}
MAX_STILL = 16384  # px on a side: WebGL's canvas limit
MAX_PROFILES = 20  # curves in the Profiles panel (a file of contacts has hundreds)


def wktOf(image: VardaRaster) -> str:
    """The image's map projection as WKT, for worker threads (which mustn't
    share pyproj objects)."""
    return CRS.from_user_input(image.crs).to_wkt()


def mapBounds(image: VardaRaster) -> tuple[float, float, float, float]:
    """(west, south, east, north) in the image's map units."""
    west, south, east, north = array_bounds(image.height, image.width, image.transform)
    return float(west), float(south), float(east), float(north)


def demLines(layers: Sequence[PageLine], width: int, height: int) -> list[VardaLine]:
    """Layers of lines (fractions of the DEM's extent) as lines in its pixel
    coordinates, one per path, to profile."""
    lines = []
    for layer in layers:
        for i, path in enumerate(layer.paths):
            uv = np.asarray(path, float)
            name = layer.name if len(layer.paths) == 1 else f"{layer.name} {i + 1}"
            vertices = np.column_stack([uv[:, 0] * width, (1 - uv[:, 1]) * height])
            lines.append(
                VardaLine(len(lines), name, layer.color, LineKind.POLYLINE, vertices)
            )
    return lines


def swatch(color: str) -> QIcon:
    pixmap = QPixmap(14, 14)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


LEGEND_STOPS = 16  # color bar samples of the color map


def drapeRgba(image: VardaRaster, elevation: str | None = None) -> np.ndarray:
    """An image as RGBA (no data transparent): its usual rendering, or for
    ``elevation`` (a color map name) its first band through that map,
    stretched 2-98 %."""
    return renderDrape(image, elevation)[0]


def renderDrape(
    image: VardaRaster, elevation: str | None = None
) -> tuple[np.ndarray, Legend | None]:
    """``drapeRgba``, and for ``elevation`` the color bar that goes with it
    (the map's colors over the stretch's range)."""
    renderer = ImageRenderer(image=image)
    renderer.transparentNoData = True
    settings = renderer.settings
    with settings.sigParameterChanged.blocked():
        if elevation is not None:
            settings.mode.set(RenderMode.MONO)
            settings.mono.band.set(0)
            settings.mono.colorMap.set(colorMap(elevation))
        elif image.bandCount >= 3:
            settings.mode.set(RenderMode.RGB)
        percentile = settings.stretch.option("Linear Percentile")
        assert isinstance(percentile, LinearPercentileStretch)
        percentile.config.lowPercent.set(2)
        percentile.config.highPercent.set(98)
        settings.stretch.selectByName("Linear Percentile")
    rgba = np.asarray(renderer.render())
    legend = None
    if elevation is not None:
        low, high = (float(np.ravel(v)[0]) for v in renderer.getMinMaxValues())
        lut = colorMap(elevation).getLookupTable(0, 1, LEGEND_STOPS, alpha=False)
        colors = tuple("#%02x%02x%02x" % tuple(int(c) for c in rgb[:3]) for rgb in lut)
        legend = Legend(colors, low, high)
    return rgba, legend


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


ANIMATION_SIZES = {
    "HD (1280 × 720)": (1280, 720),
    "Full HD (1920 × 1080)": (1920, 1080),
    "Square (1080 × 1080)": (1080, 1080),
}


class AnimationDialog(QDialog):
    """A flyover's frames, sweep, speed, size and format."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Export Animation")
        self.framesBox = QSpinBox()
        self.framesBox.setRange(12, 1440)
        self.framesBox.setValue(180)
        self.degreesBox = QSpinBox()
        self.degreesBox.setRange(10, 360)
        self.degreesBox.setValue(360)
        self.degreesBox.setSuffix("°")
        self.degreesBox.setToolTip("How far round the camera goes")
        self.fpsBox = QComboBox()
        self.fpsBox.addItems(["24", "30", "60"])
        self.fpsBox.setCurrentText("30")
        self.sizeBox = QComboBox()
        self.sizeBox.addItems(list(ANIMATION_SIZES))
        self.formatBox = QComboBox()
        self.formatBox.addItems(list(ANIMATION_FORMATS))
        self.formatBox.setToolTip(
            "WebP is smaller and sharper than GIF; PNG frames go into a video editor"
        )
        self.length = MutedLabel("")
        for box in (self.framesBox,):
            box.valueChanged.connect(lambda _: self._showLength())
        self.fpsBox.currentTextChanged.connect(lambda _: self._showLength())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout(self)
        form.addRow("Frames:", self.framesBox)
        form.addRow("Sweep:", self.degreesBox)
        form.addRow("Frames per second:", self.fpsBox)
        form.addRow("Size:", self.sizeBox)
        form.addRow("Format:", self.formatBox)
        form.addRow(self.length)
        form.addRow(buttons)
        self._showLength()

    def _showLength(self) -> None:
        seconds = self.framesBox.value() / float(self.fpsBox.currentText())
        self.length.setText(f"{seconds:.1f} seconds long.")

    def choices(self) -> tuple[int, float, float, tuple[int, int], str]:
        """(frames, degrees, fps, (width, height), suffix; "" for PNG frames)."""
        return (
            self.framesBox.value(),
            float(self.degreesBox.value()),
            float(self.fpsBox.currentText()),
            ANIMATION_SIZES[self.sizeBox.currentText()],
            ANIMATION_FORMATS[self.formatBox.currentText()],
        )


PRINT_DETAIL = {"Draft (256)": 256, "Standard (512)": 512, "Fine (1024)": 1024}


class PrintDialog(QDialog):
    """The print's size, exaggeration, plinth and detail, with the size it
    will come out."""

    def __init__(
        self,
        terrain: Terrain,
        exaggeration: float,
        metersPerHeightUnit: float = 1.0,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Export for 3D Printing")
        self.terrain = terrain
        self.metersPerHeightUnit = metersPerHeightUnit
        self.widthBox = QDoubleSpinBox()
        self.widthBox.setRange(10, 2000)
        self.widthBox.setValue(150)
        self.widthBox.setSuffix(" mm")
        self.widthBox.setToolTip(
            "Across, west to east (between the outermost grid points)"
        )
        self.exaggerationBox = QDoubleSpinBox()
        self.exaggerationBox.setRange(0.1, 100)
        self.exaggerationBox.setSingleStep(0.5)
        self.exaggerationBox.setValue(exaggeration)
        self.exaggerationBox.setSuffix(" ×")
        self.baseBox = QDoubleSpinBox()
        self.baseBox.setRange(0.5, 100)
        self.baseBox.setValue(3)
        self.baseBox.setSuffix(" mm")
        self.baseBox.setToolTip("The plinth below the lowest point")
        self.detailBox = QComboBox()
        self.detailBox.addItems(list(PRINT_DETAIL))
        self.detailBox.setCurrentText("Standard (512)")
        self.detailBox.setToolTip(
            "Points on the longer side; printers rarely resolve more"
        )
        self.sizeLabel = MutedLabel("")
        self.filledNote = MutedLabel(
            f"The surface has {terrain.filled:,} points of filled gaps: invented "
            "heights, not measured. The print fills its own grid the same way "
            "and names how many points in the STL's header."
        )
        self.filledNote.setWordWrap(True)
        self.filledNote.setVisible(terrain.filled > 0)
        for box in (self.widthBox, self.exaggerationBox, self.baseBox):
            box.valueChanged.connect(lambda _: self._showSize())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout(self)
        form.addRow("Width:", self.widthBox)
        form.addRow("Exaggeration:", self.exaggerationBox)
        form.addRow("Base:", self.baseBox)
        form.addRow("Detail:", self.detailBox)
        form.addRow(self.sizeLabel)
        form.addRow(self.filledNote)
        form.addRow(buttons)
        self._showSize()

    def printedSize(self) -> tuple[float, float, float]:
        """(width, depth, height) in mm."""
        width = self.widthBox.value()
        columns, rows = self.terrain.columns, self.terrain.rows
        span = float(np.ptp(columns)) or 1.0
        mmPerM = width / (self.terrain.widthM * span)
        low, high = self.terrain.zRange
        height = (high - low) * self.metersPerHeightUnit * mmPerM
        height = height * self.exaggerationBox.value() + self.baseBox.value()
        return width, self.terrain.heightM * float(np.ptp(rows)) * mmPerM, height

    def _showSize(self) -> None:
        width, depth, height = self.printedSize()
        self.sizeLabel.setText(
            f"Prints {width:.0f} × {depth:.0f} × {height:.1f} mm (W × D × H)."
        )

    def choices(self) -> tuple[float, float, float, int]:
        """(width mm, exaggeration, base mm, grid points on the longer side)."""
        return (
            self.widthBox.value(),
            self.exaggerationBox.value(),
            self.baseBox.value(),
            PRINT_DETAIL[self.detailBox.currentText()],
        )


class TerrainWorkspace(QMainWindow):
    def __init__(
        self,
        projectImages: MutableSequence[VardaRaster],
        dem: VardaRaster | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("3D Terrain")
        self.projectImages = projectImages
        self.settings = TerrainSettings()
        self.terrain: Terrain | None = None
        self.views: dict[str, dict] = {}  # name -> camera
        self._camera: dict | None = None  # kept across page rebuilds
        self._building = 0  # the latest build (older results are dropped)
        # the last built page's parts, for Export Web Page
        self._texture: str | None = None
        self._legend: Legend | None = None
        # (where they come from: a file or lines kept in the session; the lines)
        self.lineLayers: list[tuple[dict, PageLine]] = []
        self._overlays: list[PageOverlay] = []
        self._restoring = False

        self.view = TerrainView()
        self.view.sigReady.connect(self._onReady)
        self.view.sigFailed.connect(self._onFailed)
        self._buildPanel()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        # the panel is tall: small screens scroll it (never cut off its side)
        splitter.addWidget(PanelScrollArea(self.panel))
        splitter.addWidget(self.view)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 1100])
        self.setCentralWidget(splitter)
        self.displayDock = QDockWidget("Overlay Display", self)
        self.displayDock.setObjectName("overlayDisplay")
        self.displayDock.setWidget(self.overlayDisplay)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.displayDock)
        self.displayDock.hide()  # until an overlay is added or Display… is clicked
        self.resizeDocks([self.displayDock], [380], Qt.Orientation.Horizontal)
        # The DEM along the lines
        self.profilePlot: ProfilePlotWidget | None = None
        self.profileDock = QDockWidget("Profiles", self)
        self.profileDock.setObjectName("profiles")
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.profileDock)
        self.profileDock.hide()  # until lines are added

        self._refreshImages(select=dem)
        if isinstance(projectImages, ObservableList):
            projectImages.sigDataChanged.connect(lambda _: self._refreshImages())

    # --- the panel ------------------------------------------------------------------

    def _buildPanel(self) -> None:
        self.demBox = QComboBox()
        self.demBox.setToolTip("The elevation image (a DEM)")
        self.demBox.currentIndexChanged.connect(lambda _: self._onDem())
        self.bandBox = QComboBox()
        self.bandBox.currentIndexChanged.connect(lambda _: self.rebuild())
        self.drapeBox = QComboBox()
        self.drapeBox.setToolTip(
            "What covers the surface: plain shaded relief, the elevations "
            "colored, or a loaded image on the DEM's grid (e.g. its orthoimage)"
        )
        self.drapeBox.currentIndexChanged.connect(lambda _: self._onDrape())
        # the images a drape can't be (not on the DEM's grid), and why
        self.drapeNotListed = notListedLabel()
        self.colorMapBox = QComboBox()
        self.colorMapBox.addItems(list(COLOR_MAPS))
        self.colorMapBox.currentIndexChanged.connect(lambda _: self.rebuild())
        self.detailBox = QComboBox()
        self.detailBox.addItems(list(DETAIL))
        self.detailBox.setCurrentText("Medium")
        self.detailBox.setToolTip("How finely the surface follows the DEM")
        self.detailBox.currentIndexChanged.connect(lambda _: self.rebuild())
        self.fillGaps = QCheckBox("Fill gaps")
        self.fillGaps.setChecked(True)
        self.fillGaps.setToolTip(
            "Fill no-data gaps inside the DEM (voids on steep slopes) from the "
            "heights around them; no data around the outside stays empty"
        )
        self.fillGaps.toggled.connect(lambda _: self.rebuild())
        self.showFilled = QCheckBox("Show filled gaps")
        self.showFilled.setToolTip(
            "Tint the surface where gaps were filled: those heights are "
            "invented (interpolated), not measured"
        )
        self.showFilled.setEnabled(False)  # until a surface has filled gaps
        self.showFilled.toggled.connect(lambda _: self._onSettings())
        self.edges = QComboBox()
        self.edges.addItems(list(EDGES))
        self.edges.setToolTip(
            "The surface's outline where the data ends: Smooth cuts the cells' "
            "corners diagonally, Stepped follows the grid cell by cell, and "
            "Trimmed keeps the largest rectangle with data everywhere, for "
            "straight sides. For the view and 3D printing alike."
        )
        self.edges.currentIndexChanged.connect(lambda _: self._onSettings())

        self.exaggeration = QDoubleSpinBox()
        self.exaggeration.setRange(0.1, 50)
        self.exaggeration.setSingleStep(0.5)
        self.exaggeration.setSuffix(" ×")
        self.heightUnit = QComboBox()
        self.heightUnit.addItems(list(HEIGHT_UNIT_NAMES))
        self.heightUnit.setToolTip(
            "The unit of the DEM's heights, converted to meters (the ground's "
            "unit) for the view, the exaggeration and 3D printing; set from the "
            "DEM's vertical CRS when it declares one, otherwise meters"
        )
        self.heightUnit.currentIndexChanged.connect(lambda _: self._onSettings())
        self.sunAzimuth = QSpinBox()
        self.sunAzimuth.setRange(0, 359)
        self.sunAzimuth.setWrapping(True)
        self.sunAzimuth.setSuffix("°")
        self.sunAzimuth.setToolTip("Clockwise from north")
        self.sunElevation = QSpinBox()
        self.sunElevation.setRange(1, 90)
        self.sunElevation.setSuffix("°")
        self.shadows = QCheckBox("Cast shadows")
        self.ambient = QDoubleSpinBox()
        self.ambient.setRange(0, 1.5)
        self.ambient.setSingleStep(0.05)
        self.ambient.setToolTip("Light from the sky, which lifts the shadows")
        self.background = QComboBox()
        self.background.addItems(list(BACKGROUNDS))
        self.northArrow = QCheckBox("North arrow")
        self.scaleBar = QCheckBox("Scale bar")
        self.scaleBar.setToolTip(
            "Ground distance at the view's center (a perspective view has no "
            "single scale); with the vertical exaggeration"
        )
        self.colorBar = QCheckBox("Color bar")
        self.colorBar.setToolTip("For the elevation colors drape")
        for box in (self.northArrow, self.scaleBar, self.colorBar):
            box.toggled.connect(lambda _: self._onSettings())
        self.sides = QCheckBox("Sides (block diagram)")
        self.sides.setToolTip("Walls from the surface down to a base, as a cut block")
        self.baseDepth = QDoubleSpinBox()
        self.baseDepth.setRange(0, 1e5)
        self.baseDepth.setDecimals(0)
        self.baseDepth.setSingleStep(50)
        self.baseDepth.setSuffix(" m")
        self.baseDepth.setSpecialValueText("Automatic")
        self.baseDepth.setToolTip(
            "How far below the lowest point the base is (Automatic: a quarter of "
            "the relief); it's exaggerated with the heights"
        )
        self.sideColor = QComboBox()
        for name, color in SIDE_COLORS.items():
            self.sideColor.addItem(swatch(color), name, color)
        self.sideColor.addItem(CUSTOM, None)
        self.sideColor.activated.connect(self._onSideColor)
        self.sides.toggled.connect(lambda _: self._onSettings())
        self.baseDepth.valueChanged.connect(lambda _: self._onSettings())
        for box in (self.exaggeration, self.ambient):
            box.valueChanged.connect(lambda _: self._onSettings())
        for box in (self.sunAzimuth, self.sunElevation):
            box.valueChanged.connect(lambda _: self._onSettings())
        self.shadows.toggled.connect(lambda _: self._onSettings())
        self.background.currentIndexChanged.connect(lambda _: self._onSettings())

        self.viewList = QListWidget()
        self.viewList.setToolTip("Saved camera views; double-click one to go to it")
        self.viewList.itemDoubleClicked.connect(self._goToView)
        saveView = QPushButton("Save View…")
        saveView.clicked.connect(self._saveView)
        deleteView = QPushButton("Delete")
        deleteView.clicked.connect(self._deleteView)
        home = QPushButton("Reset View")
        home.clicked.connect(self.view.home)
        still = QPushButton("Export Still…")
        still.setToolTip("Render the view as a high-resolution PNG for a figure")
        still.clicked.connect(self.exportStill)
        web = QPushButton("Export Web Page…")
        web.setToolTip(
            "Save an interactive 3D page that opens in any web browser, to share "
            "or to publish with a paper"
        )
        web.clicked.connect(self.exportWebPage)
        printButton = QPushButton("Export for 3D Printing…")
        printButton.setToolTip(
            "Save the terrain as a closed solid (STL) with sides and a base, to print"
        )
        printButton.clicked.connect(self.exportForPrinting)

        self.form = form = QFormLayout()
        form.addRow("DEM:", self.demBox)
        form.addRow("Band:", self.bandBox)
        form.addRow("Drape:", self.drapeBox)
        form.addRow(self.drapeNotListed)
        form.addRow("Color map:", self.colorMapBox)
        form.addRow("Detail:", self.detailBox)
        form.addRow("", self.fillGaps)
        form.addRow("", self.showFilled)
        form.addRow("Edges:", self.edges)
        form.addRow("Exaggeration:", self.exaggeration)
        form.addRow("Height unit:", self.heightUnit)
        form.addRow("Sun azimuth:", self.sunAzimuth)
        form.addRow("Sun elevation:", self.sunElevation)
        form.addRow("", self.shadows)
        form.addRow("Sky light:", self.ambient)
        form.addRow("Background:", self.background)
        form.addRow("", self.sides)
        form.addRow("Base depth:", self.baseDepth)
        form.addRow("Side color:", self.sideColor)
        annotations = QHBoxLayout()
        for box in (self.northArrow, self.scaleBar, self.colorBar):
            annotations.addWidget(box)
        form.addRow("Show:", annotations)
        self._showSettings()
        viewButtons = QHBoxLayout()
        for button in (saveView, deleteView, home):
            viewButtons.addWidget(button)
        exports = QVBoxLayout()
        exportRow = QHBoxLayout()
        exportRow.addWidget(still)
        exportRow.addWidget(web)
        exports.addLayout(exportRow)
        exports.addWidget(printButton)
        animation = QPushButton("Export Animation…")
        animation.setToolTip("A flyover: the camera circles the view's center")
        animation.clicked.connect(self.exportAnimation)
        exports.addWidget(animation)
        self.lineList = QListWidget()
        self.lineList.setToolTip(
            "Lines draped on the surface, with their DEM profiles in the "
            "Profiles panel; tick to show, double-click to change the color"
        )
        self.lineList.setMaximumHeight(90)
        self.lineList.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.lineList.itemSelectionChanged.connect(self._updateProfiles)
        self.lineList.itemChanged.connect(self._onLineItem)
        self.lineList.itemDoubleClicked.connect(self._recolorLine)
        addLines = QPushButton("Add Lines")  # sized for its menu's arrow
        addLines.setToolTip(
            "Lines drawn in an image workspace (Draw Line), or a vector file "
            "(GeoJSON, GeoPackage, shapefile, KML): a traverse, mapped contacts"
        )
        self.addLinesMenu = QMenu(addLines)
        self.addLinesMenu.aboutToShow.connect(self._fillLinesMenu)
        addLines.setMenu(self.addLinesMenu)
        removeLines = QPushButton("Remove")
        removeLines.clicked.connect(self._removeLines)
        lineButtons = QHBoxLayout()
        lineButtons.addWidget(addLines)
        lineButtons.addWidget(removeLines)
        lineButtons.addStretch()
        self.overlayPanel = OverlayPanel(self._overlayCandidates, self._references)
        self.overlayPanel.sigRebuild.connect(self.rebuild)
        self.overlayPanel.sigLook.connect(self._onOverlayLook)
        self.overlayPanel.sigAlignRequested.connect(self.autoAlign)
        # The selected overlay's settings and histograms, in a dock
        self.overlayDisplay = OverlayDisplay()
        self.overlayDisplay.sigChanged.connect(self.rebuild)
        self.overlayPanel.sigSelected.connect(self.overlayDisplay.setOverlay)
        self.overlayPanel.sigDisplayRequested.connect(self._showDisplay)
        self.status = MutedLabel("")
        self.status.setWordWrap(True)
        self.panel = QWidget()
        layout = QVBoxLayout(self.panel)
        layout.addLayout(form)
        layout.addWidget(QLabel("<b>Overlays</b>"))
        layout.addWidget(self.overlayPanel)
        layout.addWidget(QLabel("<b>Lines</b>"))
        layout.addWidget(self.lineList)
        layout.addLayout(lineButtons)
        layout.addWidget(QLabel("<b>Views</b>"))
        layout.addWidget(self.viewList, 1)
        layout.addLayout(viewButtons)
        layout.addLayout(exports)
        layout.addWidget(self.status)

    def dem(self) -> VardaRaster | None:
        return self.demBox.currentData()

    def _overlayCandidates(self) -> list[VardaRaster]:
        """Georeferenced images of the DEM's body, other than the DEM."""
        dem = self.dem()
        if dem is None or not dem.hasGeospatialData:
            return []
        body = imageBody(dem)
        return [
            image
            for image in self.projectImages
            if image is not dem and image.hasGeospatialData and imageBody(image) == body
        ]

    def _references(self) -> list[VardaRaster]:
        dem = self.dem()
        return [] if dem is None else compatibleImages(list(self.projectImages), dem)

    def _showDisplay(self) -> None:
        self.displayDock.show()
        self.displayDock.raise_()

    def _onOverlayLook(self, index: int, opacity: float, visible: bool) -> None:
        if self.view.ready:
            look = json.dumps({"opacity": opacity, "visible": visible})
            self.view.evaluate(
                f"window.viewer.setOverlay({index}, {look})", lambda _: None
            )

    def autoAlign(self, index: int) -> None:
        """Match overlay ``index`` against its reference and fit its model, in
        the background; the textures are then built again."""
        dem = self.dem()
        if dem is None or not 0 <= index < len(self.overlayPanel.overlays):
            return
        overlay = self.overlayPanel.overlays[index]
        reference = next(
            (r for r in self._references() if r.name == overlay.reference), None
        )
        # the image matched: the overlay, or one with its georeferencing
        image = next(
            (i for i in self._overlayCandidates() if i.name == overlay.matchWith),
            overlay.image,
        )
        demWkt, overlayWkt = wktOf(dem), wktOf(image)
        width, _ = groundSize(dem)
        pixelMeters = width / dem.width
        demBand = int(self.bandBox.currentData() or 0)
        metersPerHeightUnit = self.settings.metersPerHeightUnit
        demTransform, overlayTransform = dem.transform, image.transform
        model = overlay.alignment.model
        self.overlayPanel.showAlignment(
            overlay,
            "Aligning…" if image is overlay.image else f"Aligning on {image.name}…",
        )

        def work() -> Alignment:
            crs = RasterioCRS.from_wkt(demWkt)
            if reference is None:  # a hillshade of the DEM
                heights = bandArray(dem, demBand) * metersPerHeightUnit
                referenceData = hillshade(heights, pixelMeters)
            else:
                referenceData = bandArray(reference, 0)
            fixed = asRaster(referenceData[..., None], demTransform, crs, "reference")
            band, snr = bestBand(image)  # the overlay's
            moved, movedTransform = toProjection(
                bandArray(image, band)[..., None], overlayTransform, overlayWkt, demWkt
            )
            alignment = align(
                fixed, asRaster(moved, movedTransform, crs, image.name), model
            )
            return attrs.evolve(
                alignment,
                matchedOn=image.name,
                band=band,
                bandLabel=bandLabel(image, band),
                bandSnr=snr,
            )

        def done(alignment: Alignment) -> None:
            overlay.alignment = alignment
            self.overlayPanel.showAlignment(overlay, "Aligned.")
            self.overlayPanel.showSelected()
            self.rebuild()

        def failed(error: Exception) -> None:
            self.overlayPanel.showAlignment(overlay, str(error))

        runInBackground(
            work, done, failed, description="Aligning an overlay", owner=self
        )

    def drapeImage(self) -> VardaRaster | None:
        data = self.drapeBox.currentData()
        return data if isinstance(data, VardaRaster) else None

    def _refreshImages(self, select: VardaRaster | None = None) -> None:
        current = select or self.dem()
        self.demBox.blockSignals(True)
        self.demBox.clear()
        for image in self.projectImages:
            self.demBox.addItem(image.name, image)
        index = next(
            (
                i
                for i in range(self.demBox.count())
                if self.demBox.itemData(i) is current
            ),
            -1,
        )
        if index < 0:  # a single-band image is most likely the DEM
            index = next(
                (
                    i
                    for i, image in enumerate(self.projectImages)
                    if image.bandCount == 1
                ),
                0,
            )
        self.demBox.setCurrentIndex(index)
        self.demBox.blockSignals(False)
        if self.dem() is not current or select is not None:
            self._onDem()
        else:
            self._refreshDrapes()

    def _onDem(self) -> None:
        dem = self.dem()
        self.bandBox.blockSignals(True)
        self.bandBox.clear()
        if dem is not None:
            names = list(dem.bandNames or [])
            for band in range(dem.bandCount):
                self.bandBox.addItem(
                    names[band] if band < len(names) else f"Band {band + 1}", band
                )
        self.form.setRowVisible(self.bandBox, dem is not None and dem.bandCount > 1)
        self.bandBox.blockSignals(False)
        crs = CRS.from_user_input(dem.crs) if dem is not None and dem.crs else None
        self.overlayPanel.setMapUnit(mapUnit(crs) if crs is not None else "px")
        if not self._restoring:  # the DEM's own vertical unit, or meters
            unit = (verticalUnit(crs) if crs is not None else None) or "m"
            self.settings = attrs.evolve(self.settings, heightUnit=unit)
            self._showSettings()
        self._refreshDrapes()
        self._rereadLines()
        self.rebuild()

    def _refreshDrapes(self) -> None:
        dem = self.dem()
        current = self.drapeBox.currentData()
        self.drapeBox.blockSignals(True)
        self.drapeBox.clear()
        self.drapeBox.addItem(PLAIN, PLAIN)
        self.drapeBox.addItem(ELEVATION, ELEVATION)
        choices = (
            sameGridChoices(list(self.projectImages), dem) if dem is not None else None
        )
        for image in choices.images if choices else []:
            self.drapeBox.addItem(image.name, image)
        note = choices.note if choices else ""
        self.drapeNotListed.setText(note)
        self.drapeNotListed.setVisible(bool(note))
        index = next(
            (
                i
                for i in range(self.drapeBox.count())
                if self.drapeBox.itemData(i) is current
            ),
            1,
        )
        self.drapeBox.setCurrentIndex(index)
        self.drapeBox.blockSignals(False)
        self.form.setRowVisible(
            self.colorMapBox, self.drapeBox.currentData() == ELEVATION
        )

    def _onDrape(self) -> None:
        self.form.setRowVisible(
            self.colorMapBox, self.drapeBox.currentData() == ELEVATION
        )
        self.rebuild()

    # --- settings ----------------------------------------------------------------------

    def _showSettings(self) -> None:
        widgets = (
            self.exaggeration,
            self.heightUnit,
            self.sunAzimuth,
            self.sunElevation,
            self.shadows,
            self.ambient,
            self.background,
            self.sides,
            self.baseDepth,
            self.sideColor,
            self.edges,
            self.northArrow,
            self.scaleBar,
            self.colorBar,
            self.showFilled,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.showFilled.setChecked(self.settings.showFilled)
        self.northArrow.setChecked(self.settings.northArrow)
        self.scaleBar.setChecked(self.settings.scaleBar)
        self.colorBar.setChecked(self.settings.colorBar)
        self.sides.setChecked(self.settings.sides)
        self.baseDepth.setValue(self.settings.baseDepth)
        self._showSideColor(self.settings.sideColor)
        self.edges.setCurrentText(
            next((n for n, v in EDGES.items() if v == self.settings.edges), "Smooth")
        )
        for row in (self.baseDepth, self.sideColor):
            self.form.setRowVisible(row, self.settings.sides)
        self.exaggeration.setValue(self.settings.exaggeration)
        self.heightUnit.setCurrentText(
            next(
                (
                    n
                    for n, u in HEIGHT_UNIT_NAMES.items()
                    if u == self.settings.heightUnit
                ),
                "Meters",
            )
        )
        self.baseDepth.setSuffix(f" {self.settings.heightUnit}")
        self.sunAzimuth.setValue(int(self.settings.sunAzimuth) % 360)
        self.sunElevation.setValue(int(self.settings.sunElevation))
        self.shadows.setChecked(self.settings.shadows)
        self.ambient.setValue(self.settings.ambient)
        name = next(
            (n for n, c in BACKGROUNDS.items() if c == self.settings.background), "Dark"
        )
        self.background.setCurrentText(name)
        for widget in widgets:
            widget.blockSignals(False)

    def _showSideColor(self, color: str) -> None:
        """Select ``color``'s preset, or show it as a custom color."""
        index = self.sideColor.findData(color)
        if index < 0:
            custom = self.sideColor.count() - 1  # the Custom… item
            if self.sideColor.count() > len(SIDE_COLORS) + 1:  # an earlier custom
                self.sideColor.removeItem(len(SIDE_COLORS))
                custom -= 1
            self.sideColor.insertItem(custom, swatch(color), color, color)
            index = custom
        self.sideColor.setCurrentIndex(index)

    def _onSideColor(self, index: int) -> None:
        color = self.sideColor.itemData(index)
        if color is None:  # Custom…
            chosen = QColorDialog.getColor(
                QColor(self.settings.sideColor), self, "Side Color"
            )
            if not chosen.isValid():
                self._showSideColor(self.settings.sideColor)
                return
            color = chosen.name()
        self.sideColor.blockSignals(True)
        self._showSideColor(color)
        self.sideColor.blockSignals(False)
        self._onSettings()

    def _onSettings(self) -> None:
        trimmed = self.settings.edges == "rectangle"
        unit = self.settings.heightUnit
        self.settings = attrs.evolve(
            self.settings,
            exaggeration=self.exaggeration.value(),
            heightUnit=HEIGHT_UNIT_NAMES[self.heightUnit.currentText()],
            sunAzimuth=self.sunAzimuth.value(),
            sunElevation=self.sunElevation.value(),
            shadows=self.shadows.isChecked(),
            background=BACKGROUNDS[self.background.currentText()],
            ambient=self.ambient.value(),
            sides=self.sides.isChecked(),
            baseDepth=self.baseDepth.value(),
            sideColor=self.sideColor.currentData() or self.settings.sideColor,
            edges=EDGES[self.edges.currentText()],
            northArrow=self.northArrow.isChecked(),
            scaleBar=self.scaleBar.isChecked(),
            colorBar=self.colorBar.isChecked(),
            showFilled=self.showFilled.isChecked(),
        )
        for row in (self.baseDepth, self.sideColor):
            self.form.setRowVisible(row, self.settings.sides)
        self.baseDepth.setSuffix(f" {self.settings.heightUnit}")
        if (self.settings.edges == "rectangle") != trimmed:
            self.rebuild()  # trimming changes the surface itself
        elif self.settings.heightUnit != unit:
            self.rebuild()  # the color bar's label
        else:
            self.view.setSettings(self.settings.toDict())

    # --- building the page ---------------------------------------------------------------

    def rebuild(self) -> None:
        """Build the page again for the DEM, band, drape or detail (keeping
        the camera); settings alone don't need this."""
        dem = self.dem()
        if dem is None or self._restoring:
            return
        if self.view.ready:  # keep where the camera is
            self.view.camera(lambda camera: self._build(dem, camera))
        else:
            self._build(dem, self._camera)

    def _build(self, dem: VardaRaster, camera: dict | None) -> None:
        self._camera = camera or self._camera
        self._building += 1
        build = self._building
        band = int(self.bandBox.currentData() or 0)
        drape = self.drapeBox.currentData()
        rgba, self._legend = None, None
        try:  # rendering the drape uses Qt-side objects: here, on the GUI thread
            if drape == ELEVATION:
                rgba, legend = renderDrape(dem, self.colorMapBox.currentText())
                if legend is not None:
                    unit = self.settings.heightUnit
                    legend = attrs.evolve(legend, label=f"Elevation ({unit})")
                self._legend = legend
            elif drape != PLAIN:
                rgba = drapeRgba(drape)
        except Exception as error:  # a broken image shouldn't break the workspace
            self.status.setText(f"The drape couldn't be drawn: {error}")
        maxSide = DETAIL[self.detailBox.currentText()]
        title = dem.name
        # pyproj objects aren't thread-safe: what needs them is taken here
        size = groundSize(dem)
        demWkt = wktOf(dem)
        bounds = mapBounds(dem)
        layers = []
        for overlay in self.overlayPanel.overlays:
            try:
                layers.append(
                    (
                        overlay.image.name,
                        np.asarray(overlay.renderer.render()),
                        overlay.image.transform,
                        wktOf(overlay.image),
                        Alignment.fromState(overlay.alignment.state()),
                        overlay.opacity,
                        overlay.visible,
                    )
                )
            except Exception as error:  # one broken overlay shouldn't stop the rest
                self.status.setText(f"{overlay.image.name} couldn't be drawn: {error}")
        self.status.setText("Building the surface…")

        fill = self.fillGaps.isChecked()
        trim = self.settings.edges == "rectangle"

        def work() -> tuple[Terrain, str | None, list[PageOverlay]]:
            terrain = buildTerrain(dem, band, maxSide, size, fill, trim)
            texture = None if rgba is None else pngDataUrl(rgba)
            crs = RasterioCRS.from_wkt(demWkt)
            draped = []
            for name, layer, transform, wkt, alignment, opacity, visible in layers:
                moved, movedTransform = toProjection(
                    layer.astype(np.float32), transform, wkt, demWkt
                )
                result = drapeOverlay(moved, movedTransform, crs, alignment, bounds)
                if result is not None:
                    draped.append(
                        PageOverlay(
                            name,
                            pngDataUrl(result.rgba),
                            result.uvBounds,
                            opacity,
                            visible,
                        )
                    )
            return terrain, texture, draped

        runInBackground(
            work,
            lambda result: self._show(build, title, *result),
            lambda error: self.status.setText(
                f"The surface couldn't be built: {error}"
            ),
            description="Building a 3D surface",
            owner=self,
        )

    def _show(
        self,
        build: int,
        title: str,
        terrain: Terrain,
        texture: str | None,
        overlays: list[PageOverlay],
    ) -> None:
        if build != self._building:
            return  # a newer build is on its way
        self.terrain = terrain
        self.showFilled.setEnabled(terrain.filled > 0)
        self._texture, self._overlays = texture, overlays
        self.view.showPage(
            pageHtml(
                terrain,
                texture,
                self.settings,
                self._camera,
                title,
                overlays=overlays,
                legend=self._legend,
                lines=[line for _, line in self.lineLayers],
            )
        )

    def _onReady(self) -> None:
        if self.terrain is None:
            return
        low, high = self.terrain.zRange
        rows, cols = self.terrain.heights.shape
        self.status.setText(
            f"{self.terrain.widthM / 1000:.3g} × {self.terrain.heightM / 1000:.3g} km, "
            f"heights {low:.6g} to {high:.6g} {self.settings.heightUnit}; "
            f"a {cols} × {rows} grid"
            + (f" (every {self.terrain.step} pixels)" if self.terrain.step > 1 else "")
            + (
                f"; {self.terrain.filled:,} points of gaps filled"
                if self.terrain.filled
                else ""
            )
        )
        self.view.setSettings(self.settings.toDict())  # changed while it loaded

    def _onFailed(self, reason: str) -> None:
        self.status.setText(
            "The 3D view needs WebGL 2, which this computer's graphics don't "
            f"offer ({reason}). Updating the graphics driver may help."
        )

    # --- views ------------------------------------------------------------------------

    def _saveView(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Save View", "Name:", text=f"View {len(self.views) + 1}"
        )
        if ok and name:
            self.view.camera(lambda camera: self._keepView(name, camera))

    def _keepView(self, name: str, camera: dict | None) -> None:
        if camera is None:
            return
        if name not in self.views:
            self.viewList.addItem(QListWidgetItem(name))
        self.views[name] = camera

    def _goToView(self, item: QListWidgetItem) -> None:
        camera = self.views.get(item.text())
        if camera is not None:
            self.view.setCamera(camera)

    def _deleteView(self) -> None:
        for item in self.viewList.selectedItems():
            self.views.pop(item.text(), None)
            self.viewList.takeItem(self.viewList.row(item))

    # --- exports ---------------------------------------------------------------------

    def exportStill(self) -> None:
        if not self.view.ready:
            return
        dialog = StillDialog(self)
        if not dialog.exec():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Still", f"{self.windowTitle()}.png", "PNG image (*.png)"
        )
        if path:
            self.saveStill(path, *dialog.stillSize(), dialog.transparent.isChecked())

    def saveStill(self, path: str, width: int, height: int, transparent: bool) -> None:
        self.status.setText("Rendering the still…")

        def done(png: bytes | None) -> None:
            if png is None:
                self.status.setText("The still couldn't be rendered.")
                return
            with open(path, "wb") as f:
                f.write(png)
            self.status.setText(f"Saved a {width} × {height} still to {path}.")

        self.view.still(width, height, transparent, done)

    def exportWebPage(self) -> None:
        if self.terrain is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Web Page", f"{self.windowTitle()}.html", "Web page (*.html)"
        )
        if path:
            self.view.camera(lambda camera: self.saveWebPage(path, camera))

    def saveWebPage(self, path: str, camera: dict | None) -> None:
        """The view as a self-contained page with controls for its readers."""
        terrain = self.terrain
        if terrain is None:
            return
        dem = self.dem()
        title = dem.name if dem is not None else "Varda 3D view"
        looks = {o.image.name: o for o in self.overlayPanel.overlays}
        overlays = [
            attrs.evolve(
                o, opacity=looks[o.name].opacity, visible=looks[o.name].visible
            )
            if o.name in looks
            else o
            for o in self._overlays
        ]
        html = pageHtml(
            terrain,
            self._texture,
            self.settings,
            camera,
            title,
            True,
            overlays,
            self._legend,
            [line for _, line in self.lineLayers],
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        self.status.setText(f"Saved an interactive web page to {path}.")

    # --- lines ------------------------------------------------------------------------

    def _chooseLines(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Add Lines",
            "",
            "Vector files (*.geojson *.json *.gpkg *.shp *.kml *.fgb);;All files (*)",
        )
        if path:
            self.addLines(path)

    def _fillLinesMenu(self) -> None:
        """From a file, or the lines of an open image workspace on this body."""
        menu = self.addLinesMenu
        menu.clear()
        menu.addAction("From a File…", self._chooseLines)
        dem = self.dem()
        body = imageBody(dem) if dem is not None else None
        shared = [
            (image, lines)
            for image, lines in sharedLines()
            if image.hasGeospatialData and imageBody(image) == body
        ]
        if shared:
            menu.addSection("Drawn in a workspace")
        for image, lines in shared:
            sub = menu.addMenu(image.name)
            assert sub is not None
            sub.addAction(
                f"All Lines ({len(lines)})",
                lambda image=image, lines=lines: self.addWorkspaceLines(image, lines),
            )
            sub.addSeparator()
            for line in lines.lines():
                sub.addAction(
                    line.name,
                    lambda image=image, lines=lines, fid=line.fid: (
                        self.addWorkspaceLines(image, lines, [fid])
                    ),
                )

    def addLines(
        self, path: str, color: str | None = None, visible: bool = True
    ) -> bool:
        """Drape a vector file's lines; False (with a message) if they can't be."""
        return self.addLineLayers([({"path": path}, color, visible)])

    def addWorkspaceLines(
        self,
        image: VardaRaster,
        lines: LineCollection,
        fids: list[int] | None = None,
    ) -> bool:
        """Drape lines drawn on another image (all, or ``fids``), each in its
        own color. They are copied: the session keeps them in map coordinates."""
        frame = lines.toFrame(image, fids)
        wkt = wktOf(image)
        return self.addLineLayers(
            [
                (
                    {
                        "name": name,
                        "crs": wkt,
                        "paths": [np.asarray(geometry.coords)[:, :2].tolist()],
                    },
                    color,
                    True,
                )
                for name, color, geometry in zip(
                    frame["name"], frame["color"], frame.geometry
                )
            ]
        )

    def addLineLayers(self, layers: list[tuple[dict, str | None, bool]]) -> bool:
        """Drape layers of lines: (source, color, visible), a source being a
        file (``path``) or lines kept in the session (``name``, ``crs`` as
        WKT, ``paths`` in its map units). False (with a message) if any can't be."""
        dem = self.dem()
        if dem is None or not dem.hasGeospatialData:
            self.status.setText("Lines need a georeferenced DEM.")
            return False
        placed = True
        for source, color, visible in layers:
            color = color or classColor(len(self.lineLayers) + 3)
            try:
                line = self._placeLines(dem, source, color)
            except Exception as error:  # unreadable files, or lines off the DEM
                name = Path(source["path"]).name if "path" in source else source["name"]
                self.status.setText(f"{name}: {error}")
                placed = False
                continue
            self.lineLayers.append((source, attrs.evolve(line, visible=visible)))
        self._showLineList()
        if self.lineLayers and not self._restoring:
            self.profileDock.show()
        self.rebuild()
        return placed

    @staticmethod
    def _placeLines(dem: VardaRaster, source: dict, color: str) -> PageLine:
        crs, bounds = CRS.from_user_input(dem.crs), mapBounds(dem)
        if "path" in source:
            return readLines(source["path"], crs, bounds, color)
        frame = gpd.GeoDataFrame(
            geometry=[shapely.LineString(path) for path in source["paths"]],
            crs=source.get("crs"),
        )
        return placeLines(frame, crs, bounds, color, source["name"])

    def _rereadLines(self) -> None:
        """Place the lines again on a new DEM (dropping those off it)."""
        layers, self.lineLayers = self.lineLayers, []
        dem = self.dem()
        for source, line in layers:
            if dem is None or not dem.hasGeospatialData:
                break
            try:
                moved = self._placeLines(dem, source, line.color)
            except Exception:  # not on this DEM
                continue
            self.lineLayers.append((source, attrs.evolve(moved, visible=line.visible)))
        self._showLineList()

    def _showLineList(self) -> None:
        self.lineList.blockSignals(True)
        self.lineList.clear()
        for source, line in self.lineLayers:
            item = QListWidgetItem(line.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if line.visible else Qt.CheckState.Unchecked
            )
            item.setForeground(QColor(line.color))
            item.setToolTip(source.get("path", "Drawn in a workspace"))
            self.lineList.addItem(item)
        self.lineList.blockSignals(False)
        self._updateProfiles()

    def _updateProfiles(self) -> None:
        """The DEM along the selected lines (or every shown one when none is
        selected), on the DEM's pixel grid."""
        dem = self.dem()
        if dem is None:
            return
        if self.profilePlot is None or self.profilePlot.image is not dem:
            self.profilePlot = ProfilePlotWidget(
                dem, emptyText="Add lines to see the DEM's profile along them."
            )
            self.profileDock.setWidget(self.profilePlot)
        rows = {self.lineList.row(item) for item in self.lineList.selectedItems()}
        chosen = [
            line
            for row, (_, line) in enumerate(self.lineLayers)
            if (row in rows if rows else line.visible)
        ]
        self.profilePlot.setLines(
            demLines(chosen, dem.width, dem.height)[:MAX_PROFILES]
        )

    def _onLineItem(self, item: QListWidgetItem) -> None:
        row = self.lineList.row(item)
        source, line = self.lineLayers[row]
        visible = item.checkState() == Qt.CheckState.Checked
        self.lineLayers[row] = (source, attrs.evolve(line, visible=visible))
        self._updateProfiles()
        if self.view.ready:
            look = json.dumps({"visible": visible})
            self.view.evaluate(f"window.viewer.setLines({row}, {look})", lambda _: None)

    def _recolorLine(self, item: QListWidgetItem) -> None:
        row = self.lineList.row(item)
        source, line = self.lineLayers[row]
        color = QColorDialog.getColor(QColor(line.color), self, f"Color of {line.name}")
        if color.isValid():
            self.lineLayers[row] = (source, attrs.evolve(line, color=color.name()))
            self._showLineList()
            self.rebuild()

    def _removeLines(self) -> None:
        row = self.lineList.currentRow()
        if 0 <= row < len(self.lineLayers):
            del self.lineLayers[row]
            self._showLineList()
            self.rebuild()

    # --- animation ----------------------------------------------------------------------

    def exportAnimation(self) -> None:
        if not self.view.ready:
            return
        dialog = AnimationDialog(self)
        if not dialog.exec():
            return
        frames, degrees, fps, (width, height), suffix = dialog.choices()
        if suffix:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Animation",
                f"{self.windowTitle()}{suffix}",
                f"Animation (*{suffix})",
            )
        else:
            path = QFileDialog.getExistingDirectory(self, "Folder for the Frames")
        if path:
            self.saveAnimation(path, frames, degrees, fps, width, height)

    def saveAnimation(
        self,
        path: str,
        frames: int,
        degrees: float,
        fps: float,
        width: int,
        height: int,
    ) -> None:
        """Render an orbit frame by frame, then write it to ``path``: an
        animated .webp or .gif, or (a folder) numbered PNG frames."""
        target = Path(path)
        folder = (
            target
            if target.is_dir()
            else Path(tempfile.mkdtemp(prefix="varda-flyover-"))
        )
        written: list[Path] = []
        self.view.evaluate("window.viewer.orbitBegin()", lambda _: None)

        def render(index: int) -> None:
            if index == frames:
                self.view.evaluate("window.viewer.orbitEnd()", lambda _: None)
                finish()
                return
            self.status.setText(f"Rendering frame {index + 1} of {frames}…")
            angle = degrees * index / frames
            self.view.evaluate(
                f"window.viewer.orbitStill({angle}, {int(width)}, {int(height)})",
                lambda url, index=index: keep(index, url),
            )

        def keep(index: int, url) -> None:
            if not isinstance(url, str) or "," not in url:
                self.view.evaluate("window.viewer.orbitEnd()", lambda _: None)
                self.status.setText("The animation couldn't be rendered.")
                return
            frame = folder / f"frame_{index:04d}.png"
            frame.write_bytes(base64.b64decode(url.split(",", 1)[1]))
            written.append(frame)
            render(index + 1)

        def finish() -> None:
            if target.is_dir():
                self.status.setText(f"Saved {len(written)} frames to {target}.")
                return
            self.status.setText("Writing the animation…")

            def work() -> Path:
                try:
                    return writeAnimation(written, target, fps)
                finally:
                    shutil.rmtree(folder, ignore_errors=True)

            runInBackground(
                work,
                lambda out: self.status.setText(
                    f"Saved a {len(written)}-frame animation to {out}."
                ),
                lambda error: self.status.setText(
                    f"The animation couldn't be saved: {error}"
                ),
                description="Writing an animation",
                owner=self,
            )

        render(0)

    def exportForPrinting(self) -> None:
        dem = self.dem()
        if dem is None or self.terrain is None:
            return
        dialog = PrintDialog(
            self.terrain,
            self.settings.exaggeration,
            self.settings.metersPerHeightUnit,
            self,
        )
        if not dialog.exec():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export for 3D Printing", f"{dem.name}.stl", "STL solid (*.stl)"
        )
        if path:
            self.savePrint(path, *dialog.choices())

    def savePrint(
        self, path: str, widthMm: float, exaggeration: float, baseMm: float, detail: int
    ) -> None:
        """Write a closed solid of the DEM (millimeters) to ``path`` as STL."""
        dem = self.dem()
        if dem is None:
            return
        band = int(self.bandBox.currentData() or 0)
        size = groundSize(dem)  # here: pyproj objects aren't thread-safe
        fill = self.fillGaps.isChecked()
        edges = self.settings.edges
        metersPerHeightUnit = self.settings.metersPerHeightUnit
        self.status.setText("Building the solid…")

        def work() -> tuple[int, int]:
            terrain = buildTerrain(dem, band, detail, size, fill, edges == "rectangle")
            mesh = printSolid(
                terrain.heights,
                terrain.widthM,
                terrain.heightM,
                widthMm,
                exaggeration,
                baseMm,
                smooth=edges != "stepped",
                columns=terrain.columns,
                rows=terrain.rows,
                metersPerHeightUnit=metersPerHeightUnit,
            )
            note = f"{terrain.filled} gap points filled" if terrain.filled else ""
            with open(path, "wb") as f:
                f.write(stlBytes(mesh, note))
            return len(mesh.triangles), terrain.filled

        def done(counts: tuple[int, int]) -> None:
            triangles, filled = counts
            self.status.setText(
                f"Saved a solid of {triangles:,} triangles to {path}"
                + (
                    f"; {filled:,} points of gaps filled (invented heights)."
                    if filled
                    else "."
                )
            )

        runInBackground(
            work,
            done,
            lambda error: self.status.setText(f"The solid couldn't be saved: {error}"),
            description="Writing an STL",
            owner=self,
        )

    # --- sessions ------------------------------------------------------------------------

    def sessionImages(self) -> list[VardaRaster]:
        """The images a session must keep for this workspace: the DEM, the
        drape image (if any), then the overlays."""
        dem, drape = self.dem(), self.drapeImage()
        images = [image for image in (dem, drape) if image is not None]
        return images + [o.image for o in self.overlayPanel.overlays]

    def sessionState(self) -> dict:
        drape = self.drapeBox.currentData()
        return {
            "band": int(self.bandBox.currentData() or 0),
            "drape": drape if isinstance(drape, str) else "image",
            "overlays": [
                {
                    "opacity": o.opacity,
                    "visible": o.visible,
                    "reference": o.reference,
                    "matchWith": o.matchWith,
                    "alignment": o.alignment.state(),
                    "display": settingsState(o.renderer.settings),
                }
                for o in self.overlayPanel.overlays
            ],
            "colorMap": self.colorMapBox.currentText(),
            "detail": self.detailBox.currentText(),
            "fillGaps": self.fillGaps.isChecked(),
            # how many grid points of the surface have invented heights
            "filled": {
                "count": 0 if self.terrain is None else self.terrain.filled,
                "method": FILL_METHOD,
            },
            "settings": self.settings.toDict(),
            "views": dict(self.views),
            "lines": [
                {**source, "color": line.color, "visible": line.visible}
                for source, line in self.lineLayers
            ],
            "camera": self._camera,
        }

    def restoreSessionState(
        self, state: dict, images: Sequence[VardaRaster] = ()
    ) -> None:
        """``images``: the session's images after the DEM (``sessionImages``)."""
        images = list(images)
        drape = images.pop(0) if state.get("drape") == "image" and images else None
        self._restoring = True
        try:
            overlays = []
            for image, saved in zip(images, state.get("overlays", [])):
                overlay = TerrainOverlay.of(image)
                overlay.opacity = float(saved.get("opacity", overlay.opacity))
                overlay.visible = bool(saved.get("visible", True))
                overlay.reference = saved.get("reference", overlay.reference)
                overlay.matchWith = saved.get("matchWith", "")
                overlay.alignment = Alignment.fromState(saved.get("alignment", {}))
                restoreSettings(overlay.renderer.settings, saved.get("display", {}))
                overlays.append(overlay)
            self.overlayPanel.setOverlays(overlays)
            self.settings = TerrainSettings.fromDict(state.get("settings", {}))
            self._showSettings()
            index = self.bandBox.findData(state.get("band", 0))
            self.bandBox.setCurrentIndex(max(index, 0))
            self.colorMapBox.setCurrentText(state.get("colorMap", "Viridis"))
            self.detailBox.setCurrentText(state.get("detail", "Medium"))
            self.fillGaps.setChecked(bool(state.get("fillGaps", True)))
            choice = state.get("drape", ELEVATION)
            target = drape if choice == "image" else choice
            for i in range(self.drapeBox.count()):
                if (
                    self.drapeBox.itemData(i) == target
                    or self.drapeBox.itemData(i) is target
                ):
                    self.drapeBox.setCurrentIndex(i)
            self.form.setRowVisible(
                self.colorMapBox, self.drapeBox.currentData() == ELEVATION
            )
            self.addLineLayers(
                [
                    (
                        {
                            k: v
                            for k, v in saved.items()
                            if k not in ("color", "visible")
                        },
                        saved.get("color"),
                        saved.get("visible", True),
                    )
                    for saved in state.get("lines", [])
                ]
            )
            for name, camera in state.get("views", {}).items():
                self._keepView(name, camera)
            self._camera = state.get("camera")
        finally:
            self._restoring = False
        self.rebuild()
