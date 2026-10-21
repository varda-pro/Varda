"""Eigenvalue Plot: inspect a PCA / MNF / ICA result's eigenvalues to decide
how many components carry signal, then jump to Inverse Transform with that
number."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QFormLayout, QLabel, QTableView, QWidget

from varda.analysis.eigenvalue_plot import EigenvaluePlot
from varda.analysis.linear_algebra import suggestedComponents, varianceShareText
from varda.analysis.transforms import TRANSFORM_METADATA_KEY
from varda.common.entities import VardaRaster
from varda.common.parameter import ImageParameter, IntParameter
from varda.common.ui import ButtonBuilder, HBoxBuilder, SectionBox, VBoxBuilder
from varda.common.parameter_widgets import parameterWidget

_COLUMNS = ("Component", "Eigenvalue", "Variance %", "Cumulative %")


class EigenvalueTableModel(QAbstractTableModel):
    def __init__(self, eigenvalues, explainedVariance, parent=None) -> None:
        super().__init__(parent)
        self._eigenvalues = np.asarray(eigenvalues, dtype=np.float64)
        shares = np.asarray(explainedVariance, dtype=np.float64)
        self._shares = shares
        self._cumulative = np.cumsum(shares)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self._eigenvalues.size

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return _COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = index.row()
        return (
            str(row + 1),
            f"{self._eigenvalues[row]:.5g}",
            varianceShareText(self._shares[row]),
            f"{100 * self._cumulative[row]:.1f}%",
        )[index.column()]


class EigenvaluePlotDialog(QDialog):
    sigInverseRequested = pyqtSignal(object, int)  # (transform result, components)

    def __init__(
        self,
        images: Sequence[VardaRaster],
        *,
        image: VardaRaster | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Eigenvalue Plot")
        self.resize(640, 560)
        self.plot: EigenvaluePlot | None = None

        self.imageParam = ImageParameter("Image", "A PCA, MNF or ICA result")
        self.imageParam.setProvider(lambda: list(images))
        if image is not None:
            self.imageParam.set(image)
        self.components = IntParameter(
            "Components to keep",
            1,
            range=(1, 1000),
            description="Drag the cutoff on the plot or set it here",
        )
        self.statusLabel = QLabel()
        self.statusLabel.setWordWrap(True)
        self.plotBox = SectionBox("Eigenvalues")
        self.table = QTableView()
        self.table.setMinimumHeight(140)
        self.keepButton = ButtonBuilder("Inverse Transform…").onClick(
            self._requestInverse
        )

        form = QFormLayout()
        form.addRow("Image", parameterWidget(self.imageParam))
        form.addRow("Components to keep", parameterWidget(self.components))
        self.setLayout(
            VBoxBuilder()
            .withLayout(form)
            .withWidget(self.statusLabel)
            .withWidget(self.plotBox)
            .withWidget(self.table)
            .withLayout(
                HBoxBuilder()
                .withWidget(self.keepButton)
                .withStretch()
                .withWidget(ButtonBuilder("Close").onClick(self.close))
            )
        )

        self.imageParam.sigParameterChanged.connect(lambda _: self._showImage())
        self.components.sigParameterChanged.connect(lambda _: self._updateKeepButton())
        self._showImage()

    def selectedImage(self) -> VardaRaster:
        return self.imageParam.get()

    def _showImage(self) -> None:
        image = self.selectedImage()
        info = image.extraMetadata.get(TRANSFORM_METADATA_KEY) if image else None
        if info is None:
            self.plot = None
            self.plotBox.setContent(None)
            self.table.setModel(None)
            self.statusLabel.setText(
                "This image is not the result of a PCA, MNF or ICA transform; "
                "run one first (Processing → Transforms) and pick its result here."
            )
            self.keepButton.setEnabled(False)
            return
        eigenvalues = np.asarray(info["eigenvalues"], dtype=np.float64)
        kind = info["kind"]
        self.components.setRange(
            (1, eigenvalues.size), value=suggestedComponents(eigenvalues, kind)
        )
        self.plot = EigenvaluePlot(eigenvalues, self.components, logScale=kind != "MNF")
        self.plotBox.setContent(self.plot)
        self.table.setModel(
            EigenvalueTableModel(eigenvalues, info["explainedVariance"], parent=self)
        )
        self.statusLabel.setText(
            f"{kind} of {info['sourceName']}: {eigenvalues.size} components. "
            + (
                "MNF eigenvalues are SNR + 1; components near 1 are noise."
                if kind == "MNF"
                else "Look for where the eigenvalues flatten into a noise floor."
            )
        )
        self.keepButton.setEnabled(True)
        self._updateKeepButton()

    def _updateKeepButton(self) -> None:
        self.keepButton.setText(
            f"Inverse Transform with {self.components.get()} components…"
        )

    def _requestInverse(self) -> None:
        self.sigInverseRequested.emit(self.selectedImage(), int(self.components.get()))
