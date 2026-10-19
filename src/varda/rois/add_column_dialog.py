"""Add Column: an empty column to type values into, or a calculated one.

Calculated columns come from each ROI's geometry, from a statistic of a raster
band inside it (zonal statistics; any image on the ROIs' pixel grid), or from
an expression over other columns. They stay current as ROIs change.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from varda.common.entities import VardaRaster
from varda.common.grid import sameGridChoices
from varda.common.ui import notListedLabel
from varda.geo.distortion import distortionOver
from varda.rois.roi_calculations import (
    BODY_MEASURES,
    CONSTANTS,
    FUNCTIONS,
    GEOMETRY_MEASURES,
    MAP_MEASURES,
    AREA_STATISTICS,
    NO_MAP_COORDINATES,
    STATISTICS,
    CalculatedColumns,
    Calculation,
    Expression,
    ExpressionError,
    GeometryMeasure,
    ZonalStatistic,
    expressionVariables,
    mapUnits,
    parseExpression,
    suggestName,
)


class AddColumnDialog(QDialog):
    EMPTY = "Empty (type values in)"
    GEOMETRY = "Geometry"
    ZONAL = "Zonal statistic of a raster"
    EXPRESSION = "Expression"
    KINDS = (EMPTY, GEOMETRY, ZONAL, EXPRESSION)

    def __init__(
        self,
        calculated: CalculatedColumns,
        images: Sequence[VardaRaster],
        parent: QWidget | None = None,
    ):
        """``images``: the workspace image first, then any others; those not
        on its pixel grid are left out."""
        super().__init__(parent)
        self.setWindowTitle("Add Column")
        self.calculated = calculated
        self.collection = calculated.collection
        base = images[0]
        self.base = base
        # zonal statistics need the ROIs' pixel grid: no resampling
        self.gridChoices = sameGridChoices(images[1:], base)
        self.images = [base, *self.gridChoices.images]
        self._suggested = ""  # replaced by the next suggestion unless edited

        self.nameEdit = QLineEdit()
        self.kindBox = QComboBox()
        self.kindBox.addItems(self.KINDS)
        self.pages = QStackedWidget()
        self.pages.addWidget(QLabel("A column to type values into."))
        self.pages.addWidget(self._geometryPage())
        self.pages.addWidget(self._zonalPage())
        self.pages.addWidget(self._expressionPage())
        self.previewLabel = QLabel()
        self.previewLabel.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Kind:", self.kindBox)
        form.addRow(self.pages)
        form.addRow("Name:", self.nameEdit)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.previewLabel)
        layout.addWidget(self.buttons)
        self.setMinimumWidth(440)

        self.kindBox.currentIndexChanged.connect(self._onKindChanged)
        self.nameEdit.textChanged.connect(lambda _: self._update())
        self._onKindChanged(0)

    # --- pages ---------------------------------------------------------------

    def _geometryPage(self) -> QWidget:
        page = QWidget()
        self.measureBox = QComboBox()
        units = mapUnits(self.collection)
        for kind, (label, _) in GEOMETRY_MEASURES.items():
            if kind in MAP_MEASURES and units:
                label = label.replace("map units", units)
            self.measureBox.addItem(label, kind)
            item = self.measureBox.model().item(self.measureBox.count() - 1)  # ty: ignore[unresolved-attribute]
            if kind in MAP_MEASURES and units is None:
                item.setEnabled(False)
                item.setToolTip("The image has no map coordinates")
            elif kind in ("area_map", "perimeter_map"):
                item.setToolTip(self._planarTip())
            elif kind in BODY_MEASURES:
                item.setToolTip(
                    "Geodesic on the body's surface in the image's coordinate "
                    "system, edges densified to 1 pixel: no projection "
                    "distortion, whatever the projection"
                )
        self.measureBox.currentIndexChanged.connect(lambda _: self._update())
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Measure:", self.measureBox)
        return page

    def _planarTip(self) -> str:
        """Planar measures carry the projection's distortion: say how much."""
        summary = distortionOver(
            self.base.crs, self.base.transform, self.base.width, self.base.height
        )
        distortion = (
            f"Over this image the projection has {summary.text}. "
            if summary is not None
            else ""
        )
        return (
            f"Measured on the flat map (planar). {distortion}"
            "Area and Perimeter on the body are free of it."
        )

    def _zonalPage(self) -> QWidget:
        page = QWidget()
        self.imageBox = QComboBox()
        for image in self.images:
            self.imageBox.addItem(image.name)
        self.bandBox = QComboBox()
        self.statisticBox = QComboBox()
        for statistic in STATISTICS:
            self.statisticBox.addItem(statistic, statistic)
        for statistic, label in AREA_STATISTICS.items():
            self.statisticBox.addItem(label, statistic)
        self.imageBox.currentIndexChanged.connect(self._fillBands)
        self.bandBox.currentIndexChanged.connect(lambda _: self._update())
        self.statisticBox.currentIndexChanged.connect(lambda _: self._update())
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Raster:", self.imageBox)
        self.notListed = notListedLabel(self.gridChoices.note)
        form.addRow(self.notListed)
        form.addRow("Band:", self.bandBox)
        form.addRow("Statistic:", self.statisticBox)
        self._fillBands()
        return page

    def _expressionPage(self) -> QWidget:
        page = QWidget()
        self.expressionEdit = QLineEdit()
        self.expressionEdit.setPlaceholderText("e.g. mean_b10 / mean_b20")
        self.expressionEdit.textChanged.connect(lambda _: self._update())
        self.variablesLabel = QLabel()
        self.variablesLabel.setWordWrap(True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.expressionEdit)
        layout.addWidget(self.variablesLabel)
        return page

    # --- state ---------------------------------------------------------------

    def setKind(self, kind: str) -> None:
        self.kindBox.setCurrentText(kind)

    def calculation(self) -> Calculation | None:
        kind = self.kindBox.currentText()
        if kind == self.GEOMETRY:
            return GeometryMeasure(self.measureBox.currentData())
        if kind == self.ZONAL:
            image = self.images[self.imageBox.currentIndex()]
            return ZonalStatistic(
                image, self.bandBox.currentIndex(), self.statisticBox.currentData()
            )
        if kind == self.EXPRESSION:
            return Expression(self.expressionEdit.text())
        return None

    def _fillBands(self, *_args) -> None:
        image = self.images[self.imageBox.currentIndex()]
        self.bandBox.blockSignals(True)
        self.bandBox.clear()
        self.bandBox.addItems([str(name) for name in image.bandNames])
        self.bandBox.blockSignals(False)
        self._enableAreaStatistics(image)
        self._update()

    def _enableAreaStatistics(self, image: VardaRaster) -> None:
        """Area statistics need the image's map coordinates."""
        model = self.statisticBox.model()
        for statistic in AREA_STATISTICS:
            item = model.item(self.statisticBox.findData(statistic))  # ty: ignore[unresolved-attribute]
            item.setEnabled(image.crs is not None)
            item.setToolTip(
                "Each pixel with data counts whole, weighted by its ground area "
                "on the body (not the drawn shape: see Area on the body)"
                if image.crs is not None
                else NO_MAP_COORDINATES.capitalize()
            )
        if self.statisticBox.currentData() in AREA_STATISTICS and image.crs is None:
            self.statisticBox.setCurrentIndex(0)

    def _onKindChanged(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if self.kindBox.currentText() == self.EXPRESSION:
            names = ", ".join(expressionVariables(self.collection)) or "none yet"
            self.variablesLabel.setText(
                f"Columns: {names}<br>Functions: {', '.join(FUNCTIONS)}; "
                f"constants: {', '.join(CONSTANTS)}; operators: + - * / ** %"
            )
        self._update()

    def _update(self) -> None:
        if not hasattr(self, "previewLabel"):
            return  # still building the pages
        calculation = self.calculation()
        if calculation is not None and self.nameEdit.text() == self._suggested:
            self._suggested = suggestName(calculation, mapUnits(self.collection))
            self.nameEdit.blockSignals(True)
            self.nameEdit.setText(self._suggested)
            self.nameEdit.blockSignals(False)
        problem = self._problem(calculation)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(problem is None and bool(self.nameEdit.text().strip()))
        if problem is not None:
            self.previewLabel.setText(f"<span style='color:#d33'>{problem}</span>")
        elif calculation is None:
            self.previewLabel.setText("")
        else:
            self.previewLabel.setText(self._previewText(calculation))

    def _problem(self, calculation: Calculation | None) -> str | None:
        if isinstance(calculation, Expression):
            if not calculation.text.strip():
                return "Type an expression."
            try:
                parseExpression(
                    calculation.text, set(expressionVariables(self.collection))
                )
            except ExpressionError as error:
                return str(error).capitalize()
        return None

    def _previewText(self, calculation: Calculation) -> str:
        value = self.calculated.preview(calculation)
        if value is None:
            return "Values appear as ROIs are drawn."
        shown = "blank (no value)" if math.isnan(value) else f"{value:.6g}"
        return f"First ROI: {shown}. Updates as ROIs are drawn or edited."

    def accept(self) -> None:
        name = self.nameEdit.text().strip()
        calculation = self.calculation()
        try:
            if calculation is None:
                self.collection.addColumn(name)
            else:
                self.calculated.add(name, calculation)
        except ValueError as error:
            QMessageBox.warning(self, "Cannot add column", str(error))
            return
        super().accept()
