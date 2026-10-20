"""Ratio Explorer: box-ratio spectra from viewport clicks, plotted like pixel spectra.

The RatioExplorerTool reports a numerator and a denominator box; this
controller turns them into a ratio spectrum on the workspace's pixel plot,
places the denominator on the numerator's sensor column when column lock is
on, and can save the pair as ROIs so a promising spot is not lost.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

import attrs
import numpy as np
from psygnal import SignalInstance
from PyQt6.QtCore import QObject, QPointF, pyqtSignal
from PyQt6.QtGui import QColor
from shapely.geometry import Polygon

from varda.common.keys import controlClick
from varda.common.entities import Color, ROIMode, VardaRaster
from varda.common.parameter import IntParameter, ParameterGroup
from varda.common.ui import ButtonBuilder, SectionBox, VBoxBuilder, MutedLabel
from varda.image_loading.crism_geometry import (
    columnGeometryFor,
    computeColumnLockedTranslation,
)
from varda.image_rendering.raster_view.viewport_protocol import ROIOverlayHandle
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    DENOMINATOR_COLOR,
    NUMERATOR_COLOR,
    RatioExplorerTool,
    RatioSelection,
    boxSize,
)
from varda.plotting.plot import VardaPlotWidget
from varda.rois.ratio import computeRatioSpectrum
from varda.rois.region_statistics import boxPolygonPixels, computeRegionStatistics
from varda.rois.roi_collection import ROICollection
from varda.common.parameter_widgets import groupWidget

NUMERATOR_ROI_COLOR = Color(0.0, 0.86, 0.0, 0.5)
DENOMINATOR_ROI_COLOR = Color(1.0, 0.0, 1.0, 0.5)


@attrs.frozen
class RatioComponents:
    """A measured selection on one image: the box means and their ratio."""

    image: VardaRaster
    wavelengths: np.ndarray
    numerator: np.ndarray
    denominator: np.ndarray
    ratio: np.ndarray
    selection: RatioSelection


class RatioExplorerConfig(ParameterGroup):
    boxWidth = IntParameter(
        "Box Width", 5, range=(1, 99), units="px", description="Width of both boxes"
    )
    boxHeight = IntParameter(
        "Box Height", 5, range=(1, 99), units="px", description="Height of both boxes"
    )


class SpectrumSink(Protocol):
    def addSpectra(self, entries: Sequence[tuple]): ...


class MirrorViewport(Protocol):
    """The part of a viewport needed to show a box on it."""

    # Fires when the viewport's local coordinates move (it shows a region of
    # the image and was panned).
    sigImageChanged: SignalInstance

    def pixelToLocalCoords(self, pixelCoords: np.ndarray) -> np.ndarray: ...

    def addROIOverlay(
        self, points: Sequence[QPointF], color: QColor
    ) -> ROIOverlayHandle: ...


class DenominatorOwner(Protocol):
    """The ROI manager's part in this: the column-lock toggle and the denominator."""

    @property
    def lockColumn(self) -> bool: ...

    def setDenominator(self, fid: int | None) -> None: ...


class RatioExplorerController(QObject):
    sigComponents = pyqtSignal(object)  # RatioComponents, one per measured image

    def __init__(
        self,
        collection: ROICollection,
        roiManager: DenominatorOwner,
        docks: SpectrumSink,
        config: RatioExplorerConfig,
        parent: QObject | None = None,
        *,
        imagesFor: Callable[[VardaRaster], Sequence[VardaRaster]] | None = None,
        labelWithImageName: bool = False,
        showComponents: bool = False,
        nameSuffix: Callable[[], str] = lambda: "",
        describe: Callable[[], str] = lambda: "",
        onSaveRequested: Callable[[bool], None] | None = None,
        adjustSpectra: Callable[
            [VardaRaster, np.ndarray, np.ndarray, np.ndarray],
            tuple[np.ndarray, np.ndarray],
        ]
        | None = None,
    ) -> None:
        """``imagesFor(clickedImage)`` names the image(s) a selection is measured
        on (default: the clicked image itself); a workspace with co-registered
        images uses it to apply its Spectrum Source setting. ``showComponents``
        also plots the numerator and denominator spectra next to the ratio.
        ``nameSuffix()`` is added to the names of saved ROIs (e.g. a verdict),
        and ``describe()`` goes in their ``description`` column when not empty.
        ``onSaveRequested(shift)`` takes over S (e.g. to ask for a class);
        without it S saves at once with :meth:`saveCurrentBoxes`.
        ``adjustSpectra(image, wavelengths, numerator, denominator)`` returns
        the two box means corrected (e.g. CRISM's detector join) before they
        are ratioed, plotted and checked."""
        super().__init__(parent)
        self.config = config
        self._collection = collection
        self._roiManager = roiManager
        self._docks = docks
        self._imagesFor = imagesFor or (lambda clicked: [clicked])
        self._labelWithImageName = labelWithImageName
        self._showComponents = showComponents
        self._nameSuffix = nameSuffix
        self._describe = describe
        self._onSaveRequested = onSaveRequested
        self._adjustSpectra = adjustSpectra
        self._current: tuple[VardaRaster, RatioSelection] | None = None
        self._saveCount = 0
        self._mirrorViewports: list[MirrorViewport] = []
        # per mirrored viewport: (numerator overlay, denominator overlay)
        self._mirrors: dict[int, list[ROIOverlayHandle | None]] = {}
        # what the mirrors currently show, to re-map them when a view pans
        self._lastMirror: tuple[object, RatioSelection] | None = None

    def setMirrorViewports(self, viewports: Sequence[MirrorViewport]) -> None:
        """Viewports that should also show the boxes (the images are assumed
        co-registered). The tool's own viewport draws its own and is skipped."""
        self._clearMirrors()
        for viewport in self._mirrorViewports:
            viewport.sigImageChanged.disconnect(self._redrawMirrors)
        self._mirrorViewports = list(viewports)
        # A viewport showing a region of the image moves its local coordinates
        # when panned: re-map the boxes then.
        for viewport in self._mirrorViewports:
            viewport.sigImageChanged.connect(self._redrawMirrors)

    def _redrawMirrors(self) -> None:
        if self._lastMirror is not None:
            self._mirrorSelection(*self._lastMirror)

    def bindTool(
        self, tool: RatioExplorerTool, boxImage: VardaRaster | None = None
    ) -> None:
        """Drive a freshly activated tool: box size, placement, plotting, saving.
        ``boxImage`` is the image the tool's boxes are in pixels of, when that
        isn't the image its viewport shows (a browse view of another grid)."""
        image = boxImage or tool.viewport.imageEntity
        tool.setBoxSizeProvider(
            lambda: (int(self.config.boxWidth.value), int(self.config.boxHeight.value))
        )
        tool.setDenominatorPlacer(functools.partial(self._placeDenominator, image))
        tool.setDenominatorFollower(
            functools.partial(self._followNumerator, tool, image)
        )
        tool.sigSelectionChanged.connect(functools.partial(self._onSelection, image))
        tool.sigSelectionChanged.connect(
            functools.partial(self._mirrorSelection, tool.viewport)
        )
        tool.sigDeactivated.connect(self._clearMirrors)
        tool.sigSaveRequested.connect(self._saveRequested)

    def _mirrorSelection(self, ownViewport: object, selection: RatioSelection) -> None:
        self._lastMirror = (ownViewport, selection)
        for viewport in self._mirrorViewports:
            if viewport is ownViewport:
                continue
            handles = self._mirrors.setdefault(id(viewport), [None, None])
            boxes = (
                (selection.numerator, NUMERATOR_COLOR),
                (selection.denominator, DENOMINATOR_COLOR),
            )
            for i, (polygon, color) in enumerate(boxes):
                handles[i] = _drawMirroredBox(viewport, handles[i], polygon, color)

    def _clearMirrors(self) -> None:
        for handles in self._mirrors.values():
            for handle in handles:
                if handle is not None:
                    handle.remove()
        self._mirrors.clear()
        self._lastMirror = None

    def createSidebarSection(self) -> SectionBox:
        hint = MutedLabel(
            f"{controlClick('left-click')}: numerator · "
            f"{controlClick('right-click')}: denominator · S: save"
        )
        hint.setWordWrap(True)
        return SectionBox(
            "Ratio Explorer",
            VBoxBuilder()
            .withWidget(groupWidget(self.config))
            .withWidget(
                # as S: clicked's bool is the "shift" flag, never a name
                ButtonBuilder("Save Boxes as ROIs").onClick(self._saveRequested)
            )
            .withWidget(hint),
        )

    def _saveRequested(self, shift: bool) -> None:
        if self._onSaveRequested is not None:
            self._onSaveRequested(shift)
        else:
            self.saveCurrentBoxes()

    def currentPair(self) -> RatioSelection | None:
        """The selection S would save: a numerator and a denominator."""
        if self._current is None:
            return None
        selection = self._current[1]
        if selection.numerator is None or selection.denominator is None:
            return None
        return selection

    @property
    def saveCount(self) -> int:
        """How many pairs were saved; the next is pair ``saveCount + 1``."""
        return self._saveCount

    def saveCurrentBoxes(
        self,
        names: tuple[str, str] | None = None,
        properties: tuple[Mapping[str, str], Mapping[str, str]] = ({}, {}),
        colors: tuple[Color, Color] = (NUMERATOR_ROI_COLOR, DENOMINATOR_ROI_COLOR),
    ) -> None:
        """Add the current numerator/denominator boxes to the ROI collection and
        make the denominator the ratio reference, so the pair can be re-plotted.
        ``names`` are the two ROIs' names (their classes; by default
        ``Ratio N numerator`` and ``Ratio N denominator`` with the suffix), and
        ``properties`` their user columns (added to the collection when new),
        ``colors`` their colors. An ROI joining an existing class takes the
        class's color instead."""
        if self._current is None or self.currentPair() is None:
            return
        image, selection = self._current
        assert selection.numerator is not None and selection.denominator is not None
        self._saveCount += 1
        n = self._saveCount
        if names is None:
            suffix = self._nameSuffix()
            names = (f"Ratio {n} numerator{suffix}", f"Ratio {n} denominator{suffix}")
        description = self._describe()
        rows = [dict(p) for p in properties]
        for row in rows:
            if description:
                row["description"] = description
            for column in row:
                if column not in self._collection.userColumns:
                    self._collection.addColumn(column)
        fids = [
            self._collection.addROI(
                self._geometryFor(image, polygon),
                name,
                self._collection.classColor(name) or color,
                ROIMode.RECTANGLE,
                **row,
            )
            for polygon, name, color, row in zip(
                (selection.numerator, selection.denominator),
                names,
                colors,
                rows,
                strict=True,
            )
        ]
        self._roiManager.setDenominator(fids[1])

    def _lockedBox(
        self, image: VardaRaster, numerator: np.ndarray, row: int, col: int
    ) -> np.ndarray | None:
        """The numerator's box moved onto the clicked row in its own sensor
        column, or None when the lock is off or can't be satisfied (no detector
        columns, or the column's strip doesn't reach that row)."""
        if not self._roiManager.lockColumn:
            return None
        geometry = columnGeometryFor(image)
        if geometry is None or geometry.ir_sample.shape != (image.height, image.width):
            return None
        shift = computeColumnLockedTranslation(
            numerator, clickRow=row, clickCol=col, geometry=geometry
        )
        return None if shift is None else numerator + np.array(shift)

    def _placeDenominator(
        self, image: VardaRaster, numerator: np.ndarray, row: int, col: int
    ) -> np.ndarray:
        """Same-size box at the click; on the numerator's sensor column if locked."""
        locked = self._lockedBox(image, numerator, row, col)
        if locked is not None:
            return locked
        return boxPolygonPixels(col, row, *boxSize(numerator))

    def _followNumerator(
        self,
        tool: RatioExplorerTool,
        image: VardaRaster,
        numerator: np.ndarray,
        denominator: np.ndarray,
    ) -> np.ndarray | None:
        """With column lock on, the denominator moves into the new numerator's
        column, keeping its row. Where the lock can't be satisfied it stays put,
        and the status text says so."""
        if not self._roiManager.lockColumn:
            return None
        col, row = centerPixel(denominator)
        locked = self._lockedBox(image, numerator, row, col)
        if locked is None:
            tool.showText(
                "Denominator left in place: its row has no pixels in the "
                "numerator's sensor column (or there is no geometry file)",
                timeout=4000,
            )
        return locked

    def _onSelection(self, image: VardaRaster, selection: RatioSelection) -> None:
        self._current = (image, selection)
        if selection.numerator is None or selection.denominator is None:
            return
        entries = []
        for source in self._imagesFor(image):
            numerator = np.asarray(
                computeRegionStatistics(selection.numerator, source)["mean"]
            )
            denominator = np.asarray(
                computeRegionStatistics(selection.denominator, source)["mean"]
            )
            wavelengths = VardaPlotWidget.getPlottableWavelengths(
                source, len(numerator)
            )
            if self._adjustSpectra is not None:
                numerator, denominator = self._adjustSpectra(
                    source, wavelengths, numerator, denominator
                )
            ratio = computeRatioSpectrum(numerator, denominator)
            entries.append((wavelengths, ratio, self._label(source, selection)))
            if self._showComponents:
                entries.append(
                    (
                        wavelengths,
                        numerator,
                        self._label(source, selection, "numerator"),
                    )
                )
                entries.append(
                    (
                        wavelengths,
                        denominator,
                        self._label(source, selection, "denominator"),
                    )
                )
            self.sigComponents.emit(
                RatioComponents(
                    source, wavelengths, numerator, denominator, ratio, selection
                )
            )
        # one batch, so Replace mode keeps every image of this selection
        self._docks.addSpectra(entries)

    def remeasure(self) -> None:
        """Measure the current selection again (a correction was switched)."""
        if self._current is not None:
            self._onSelection(*self._current)

    def _label(
        self,
        image: VardaRaster,
        selection: RatioSelection,
        part: str = "ratio",
    ) -> str:
        assert selection.numerator is not None and selection.denominator is not None
        nc, nr = centerPixel(selection.numerator)
        dc, dr = centerPixel(selection.denominator)
        boxes = {
            "ratio": f"({nc}, {nr}) / ({dc}, {dr})",
            "numerator": f"({nc}, {nr})",
            "denominator": f"({dc}, {dr})",
        }[part]
        if self._labelWithImageName:
            return f"{image.name} {part} {boxes}"
        return f"{part.capitalize()} {boxes}"

    @staticmethod
    def _geometryFor(image: VardaRaster, pixels: np.ndarray) -> Polygon:
        if image.hasGeospatialData:
            return Polygon(image.imageToGeo(pixels))
        return Polygon(pixels)


def _drawMirroredBox(
    viewport: MirrorViewport,
    handle: ROIOverlayHandle | None,
    polygon: np.ndarray | None,
    color: QColor,
) -> ROIOverlayHandle | None:
    if polygon is None:
        if handle is not None:
            handle.remove()
        return None
    points = [
        QPointF(float(c), float(r)) for c, r in viewport.pixelToLocalCoords(polygon)
    ]
    if handle is None:
        return viewport.addROIOverlay(points, color)
    handle.setPoints(points)
    return handle


def centerPixel(polygon: np.ndarray) -> tuple[int, int]:
    """The pixel a ``boxPolygonPixels`` box was centered on (inverse of that function)."""
    width, height = boxSize(polygon)
    return int(polygon[0, 0]) + (width - 1) // 2, int(polygon[0, 1]) + (height - 1) // 2
