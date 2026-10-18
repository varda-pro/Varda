"""ROI manager widget: table + controls, owning ROI plotting + denominator state."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PyQt6 import sip
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QWidget,
    QVBoxLayout,
    QCheckBox,
    QPushButton,
    QMessageBox,
)

from shapely.geometry import Polygon

from varda.image_loading.crism_geometry import (
    ColumnGeometry,
    columnGeometryFor,
    computeColumnLockedTranslation,
    geometryOverride,
    wholePixels,
)
from varda.image_loading.crism_geometry_chooser import chooseGeometryButton
from varda.common.ui import FlowLayout
from varda.plotting.plot import VardaPlotWidget
from varda.rois.add_column_dialog import AddColumnDialog
from varda.rois.roi_calculations import CalculatedColumns
from varda.rois.mask_roi_dialog import NewClassFromMaskDialog
from varda.rois.roi_collection import ROICollection
from varda.rois.roi_drawing_state import ROIDrawingState
from varda.rois.roi_drawing_widgets import ROIClassPicker, ROIShapePicker
from varda.rois.roi_table_model import ROITableModel
from varda.rois.roi_table_view import ROITableView
from varda.spectral_libraries.spectrum import SpectrumOrigin

if TYPE_CHECKING:
    from varda.common.entities import VardaRaster

logger = logging.getLogger(__name__)


class ROIManagerWidget(QWidget):
    """Table + controls for ROIs. Owns spectral plotting and denominator state.

    The workspace supplies the spectral ``image`` and the ``plotWidget`` to draw
    into; this widget computes mean and ratio spectra and plots them directly, so
    the plotting logic is not duplicated across workspaces.
    """

    sigSelectionChanged = pyqtSignal(object)  # emits fid (int) or None
    sigDenominatorChanged = pyqtSignal(object)  # emits fid (int) or None
    sigTemplateChanged = pyqtSignal(object)  # emits fid (int) or None

    def __init__(
        self,
        collection: ROICollection,
        image: VardaRaster,
        plotWidget: VardaPlotWidget,
        parent: QWidget | None = None,
        drawingState: ROIDrawingState | None = None,
        images: Sequence[VardaRaster] = (),
    ) -> None:
        """``images``: the project's images, offered (when on the same pixel
        grid as ``image``) for zonal-statistic columns."""
        super().__init__(parent)
        self._collection = collection
        self._image = image
        self._plotWidget = plotWidget
        self._denominatorFid: int | None = None
        self._templateFid: int | None = None

        # Model / View
        self._images = images
        self.calculatedColumns = CalculatedColumns(collection)
        self._model = ROITableModel(
            collection, parent=self, calculated=self.calculatedColumns
        )
        self._table = ROITableView(self._model, parent=self)

        # Buttons
        self.selectAllButton = QPushButton("Select All")
        self.selectAllButton.clicked.connect(lambda: self._table.selectAll())
        self.deselectButton = QPushButton("Deselect")
        self.deselectButton.clicked.connect(lambda: self._table.clearSelection())

        # Hide All while any ROI is shown, Show All once they are all hidden
        self.visibilityButton = QPushButton()
        self.visibilityButton.setToolTip(
            "Hide or show every ROI on the image; untick one in the table to hide it"
        )
        self.visibilityButton.clicked.connect(self._toggleAllVisible)
        collection.sigVisibilityChanged.connect(
            lambda _: self._updateVisibilityButton()
        )
        collection.sigCollectionChanged.connect(self._updateVisibilityButton)
        self._updateVisibilityButton()

        self._deleteBtn = QPushButton("Delete Selected")
        self._deleteBtn.clicked.connect(self._deleteSelected)

        self.fromMaskButton = QPushButton("From Threshold or Mask…")
        self.fromMaskButton.setToolTip(
            "New ROI class from where a band is inside a range, or a mask is set"
        )
        self.fromMaskButton.clicked.connect(self._newClassFromMask)

        self._addColumnBtn = QPushButton("Add Column...")
        self._addColumnBtn.clicked.connect(self._addColumn)

        self._exportBtn = QPushButton("Export...")
        self._exportBtn.clicked.connect(self._exportCollection)

        self._plotBtn = QPushButton("Plot Spectrum")
        self._plotBtn.clicked.connect(self._plotSelected)
        self._plotBtn.setEnabled(False)

        # "Lock to sensor column" toggle for template placement, enabled only
        # when the image's CRISM detector columns are known; where they
        # aren't, the geometry file can be chosen by hand.
        self._lockColumnCheck = QCheckBox("Lock to sensor column")
        self.chooseGeometryButton = chooseGeometryButton(image, parent=self)
        self._refreshColumnLock()
        image.sigMetadataChanged.connect(self._refreshColumnLock)  # held weakly

        # Layout
        btnRow = FlowLayout()  # wraps in a narrow dock
        for widget in (
            self.selectAllButton,
            self.deselectButton,
            self.visibilityButton,
            self._deleteBtn,
            self.fromMaskButton,
            self._addColumnBtn,
            self._exportBtn,
            self._plotBtn,
            self._lockColumnCheck,
            self.chooseGeometryButton,
        ):
            btnRow.addWidget(widget)

        layout = QVBoxLayout(self)
        if drawingState is not None:
            layout.addWidget(self._drawingBox(drawingState))
        layout.addLayout(btnRow)
        layout.addWidget(self._table)

        # Forward table selection changes as fid
        selModel = self._table.selectionModel()
        if selModel is not None:
            selModel.selectionChanged.connect(self._onSelectionChanged)

        # Wire row context-menu actions
        self._table.sigPlotSpectrumRequested.connect(self.plotSpectrum)
        self._table.sigPlotRatioRequested.connect(self.plotRatioSpectrum)
        self._table.sigDenominatorSetRequested.connect(self.setDenominator)
        self._table.sigDenominatorClearRequested.connect(
            lambda: self.setDenominator(None)
        )
        self._table.sigTemplateSetRequested.connect(self.setTemplate)
        self._table.sigTemplateClearRequested.connect(lambda: self.setTemplate(None))

        # Keep denominator state consistent if its ROI is deleted
        collection.sigROIRemoved.connect(self._onROIRemoved)

    @staticmethod
    def _drawingBox(state: ROIDrawingState) -> QGroupBox:
        box = QGroupBox("Draw")
        hint = QLabel(
            "Turn on <b>Draw ROI</b> under a view and draw. Shapes join the "
            "selected class until you start a new one."
        )
        hint.setWordWrap(True)
        form = QFormLayout(box)
        form.addRow(hint)
        form.addRow("Shape:", ROIShapePicker(state))
        form.addRow(ROIClassPicker(state))
        return box

    @property
    def table(self) -> ROITableView:
        return self._table

    @property
    def model(self) -> ROITableModel:
        return self._model

    @property
    def denominatorFid(self) -> int | None:
        return self._denominatorFid

    def selectedFids(self) -> list[int]:
        """The fids of every selected row, in table order."""
        selection = self._table.selectionModel()
        if selection is None:
            return []
        rows = sorted(index.row() for index in selection.selectedRows())
        fids = [self._model.fidForRow(row) for row in rows]
        return [fid for fid in fids if fid is not None]

    def selectedFid(self) -> int | None:
        """Return the fid of the currently selected row, or None."""
        idxs = self._table.selectionModel().selectedRows()
        if not idxs:
            return None
        return self._model.fidForRow(idxs[0].row())

    # --- Plotting / ratio ---

    def setDenominator(self, fid: int | None) -> None:
        """Set (or clear, with None) the ratio reference ROI."""
        if fid == self._denominatorFid:
            return
        self._denominatorFid = fid
        self._model.setDenominatorFid(fid)
        self.sigDenominatorChanged.emit(fid)

    @property
    def templateFid(self) -> int | None:
        return self._templateFid

    def _refreshColumnLock(self) -> None:
        """Enable the column lock when the detector columns are known; offer
        to choose the geometry file when they aren't, or one was chosen."""
        if sip.isdeleted(self):  # the image outlived this widget
            return
        geometry = self._columnGeometry()
        self._lockColumnCheck.setEnabled(geometry is not None)
        if geometry is None:
            self._lockColumnCheck.setChecked(False)
            self._lockColumnCheck.setToolTip(
                "No CRISM detector columns for this image: no geometry file "
                "with an IR Sample band beside it or its source, and it is "
                "not an unprojected CRISM cube. Choose the IN file by hand."
            )
        else:
            self._lockColumnCheck.setToolTip(
                f"Detector columns from {geometry.source} ({geometry.route})"
            )
        self.chooseGeometryButton.setVisible(
            geometry is None or bool(geometryOverride(self._image))
        )

    @property
    def lockColumn(self) -> bool:
        """Whether template placement should snap to the same sensor column."""
        return self._lockColumnCheck.isChecked()

    def setTemplate(self, fid: int | None) -> None:
        """Set (or clear, with None) the ROI used as a placement template."""
        if fid == self._templateFid:
            return
        self._templateFid = fid
        self._model.setTemplateFid(fid)
        self.sigTemplateChanged.emit(fid)

    def placeTemplate(self, clickRow: int, clickCol: int) -> None:
        """Stamp a copy of the template ROI centered on the clicked pixel.

        When ``self.lockColumn`` is set and a CRISM DDR resolves, the horizontal
        shift is chosen so the copy sits on the template's detector column;
        otherwise a plain centroid-to-click translation is used.
        """
        if self._templateFid is None:
            QMessageBox.information(
                self,
                "No template set",
                "Right-click an ROI and choose 'Set as Template' first, then "
                "right-click the image to place a copy.",
            )
            return

        template = self._collection.getROI(self._templateFid)
        pixelCoords = self._collection.getPixelCoordinates(
            self._templateFid
        )  # (N,2) col,row
        # Polygon coordinates are pixel corners, so center the copy on the
        # clicked pixel's center (c + 0.5), not its top-left corner. Use the
        # true centroid (the stored ring repeats its first vertex, which would
        # bias a vertex mean) and shift by whole pixels so the copy is an exact
        # pixel-set translate of the template.
        centroid = Polygon(pixelCoords).centroid
        dx = wholePixels((float(clickCol) + 0.5) - centroid.x)
        dy = wholePixels((float(clickRow) + 0.5) - centroid.y)

        if self.lockColumn:
            colGeom = self._columnGeometry()
            if colGeom is not None:
                locked = computeColumnLockedTranslation(
                    pixelCoords, clickRow=clickRow, clickCol=clickCol, geometry=colGeom
                )
                if locked is not None:
                    dx, dy = locked

        newPixels = pixelCoords + np.array([dx, dy])
        if self._image.hasGeospatialData:
            geometry = Polygon(self._image.imageToGeo(newPixels))
        else:
            geometry = Polygon(newPixels)

        self._collection.addROI(
            geometry=geometry,
            name=f"{template.name} copy",
            color=template.color,
            roiType=template.roiType,
        )

    def plotSpectrum(self, fid: int) -> None:
        """Plot the mean spectrum of an ROI, shaded with a +/- std-dev band."""
        stats = self._collection.getROIStatistics(fid, self._image)
        if stats["pixel_count"] == 0:
            logger.warning("ROI fid=%d has no pixels", fid)
            return
        mean = np.asarray(stats["mean"])
        std = np.asarray(stats["std"])
        wavelengths = self._plotWidget.xValuesFor(self._image, len(mean))
        roi = self._collection.getROI(fid)

        fillColor = roi.color.toQColor()
        fillColor.setAlpha(50)
        curve = self._plotWidget.plotWithFill(
            wavelengths,
            mean,
            yLower=mean - std,
            yUpper=mean + std,
            fillBrush=pg.mkBrush(fillColor),
            color=roi.color,
            name=roi.name,
            std=std,
            count=np.asarray(stats["valid_count"]),
        )
        curve.origin = SpectrumOrigin(
            self._image.name,
            self._image.filePath or "",
            f"ROI “{roi.name}”",
            int(np.asarray(stats["pixel_count"])),
        )

    def plotRatioSpectrum(self, fid: int) -> None:
        """Plot an ROI's mean spectrum divided by the denominator's mean."""
        if self._denominatorFid is None:
            QMessageBox.information(
                self,
                "No denominator set",
                "Right-click an ROI and choose 'Set as Denominator' first, "
                "then plot a ratio spectrum.",
            )
            return
        ratio = self._collection.getRatioSpectrum(
            fid, self._denominatorFid, self._image
        )
        numerator = self._collection.getROI(fid)
        denominator = self._collection.getROI(self._denominatorFid)
        wavelengths = VardaPlotWidget.getPlottableWavelengths(
            self._image, len(ratio.values)
        )
        self._plotWidget.plot(
            wavelengths,
            ratio.values,
            color=numerator.color,
            name=f"{numerator.name} / {denominator.name}",
        )

    # --- Internal handlers ---

    def _onSelectionChanged(self, selected, _deselected) -> None:
        if not selected.indexes():
            self.sigSelectionChanged.emit(None)
            self._plotBtn.setEnabled(False)
            return
        fid = self._model.fidForRow(selected.indexes()[0].row())
        self.sigSelectionChanged.emit(fid)
        self._plotBtn.setEnabled(fid is not None)

    def _onROIRemoved(self, fid: int) -> None:
        if fid == self._denominatorFid:
            self.setDenominator(None)
        if fid == self._templateFid:
            self.setTemplate(None)

    def _columnGeometry(self) -> ColumnGeometry | None:
        """The image's detector columns (backplane or unprojected CRISM grid)."""
        image = self._image
        geometry = columnGeometryFor(image)
        if geometry is None or geometry.ir_sample.shape != (image.height, image.width):
            return None
        return geometry

    def _plotSelected(self) -> None:
        fid = self.selectedFid()
        if fid is not None:
            self.plotSpectrum(fid)

    def _anyVisible(self) -> bool:
        return any(self._collection.isVisible(fid) for fid in self._collection.fids)

    def _toggleAllVisible(self) -> None:
        self._collection.setAllVisible(not self._anyVisible())

    def _updateVisibilityButton(self) -> None:
        anyShown = self._anyVisible() or len(self._collection) == 0
        self.visibilityButton.setText("Hide All" if anyShown else "Show All")
        self.visibilityButton.setEnabled(len(self._collection) > 0)

    def _deleteSelected(self) -> None:
        fids = self.selectedFids()
        if len(fids) > 1:
            answer = QMessageBox.question(
                self, "Delete ROIs", f"Delete the {len(fids)} selected ROIs?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        for fid in fids:
            self._collection.removeROI(fid)

    def _newClassFromMask(self) -> None:
        NewClassFromMaskDialog(
            self._collection, self._image, [self._image, *self._images], parent=self
        ).exec()

    def _addColumn(self) -> None:
        images = [self._image, *self._images]
        AddColumnDialog(self.calculatedColumns, images, parent=self).exec()

    def _exportCollection(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export ROIs",
            "",
            "GeoJSON (*.geojson);;GeoPackage (*.gpkg);;Shapefile (*.shp)",
        )
        if path:
            self._collection.toFile(path)
