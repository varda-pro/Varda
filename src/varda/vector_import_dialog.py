"""Import… for the ROI, Points and Lines managers: choose a vector file (from
the folder last imported from), read it in the background, choose its layer,
coordinates and name/class columns with a preview, then add its features and
say what was imported and skipped. The logic is in ``vector_import``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pyproj import CRS
from pyproj.exceptions import CRSError
from PyQt6.QtCore import QObject, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from varda.catalogs.bodies import bodyOf
from varda.common.entities import VardaRaster
from varda.common.ui import (
    ElidingComboBox,
    MutedLabel,
    PanelScrollArea,
    openOwnedDialog,
)
from varda.utilities.background import runInBackground
from varda.utilities.proj_lock import PROJ_LOCK
from varda.vector_import import (
    FAMILIES,
    IMPORT_FILE_FILTER,
    Coordinates,
    ImportOptions,
    ImportPlan,
    Target,
    VectorImportError,
    counted,
    VectorSource,
    defaultOptions,
    planImport,
    readVectorFile,
)

FOLDER_KEY = "vectorImport/folder"
PREVIEW_NAMES = 5


def _columnCombo(columns: list[str], chosen: str | None) -> ElidingComboBox:
    combo = ElidingComboBox()
    combo.addItem("(none)", None)
    for column in columns:
        combo.addItem(column, column)
    combo.setCurrentIndex(max(combo.findData(chosen), 0) if chosen else 0)
    return combo


class VectorImportDialog(QDialog):
    """The import options for one file, with a preview of what they import."""

    sigImport = pyqtSignal(object)  # the ImportPlan accepted

    def __init__(
        self,
        source: VectorSource,
        image: VardaRaster,
        target: Target,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Import {target.value}")
        self.image = image
        self.target = target
        self.source = source
        self.plan: ImportPlan | None = None
        defaults = defaultOptions(source, image, target)

        self.fileLabel = QLabel(source.path.name)
        self.fileLabel.setWordWrap(True)
        self.fileLabel.setToolTip(str(source.path))
        self.layerCombo = ElidingComboBox()
        self.layerCombo.addItems(source.layers)
        self.layerCombo.setCurrentText(source.layer or "")
        self.layerCombo.currentTextChanged.connect(self._reread)
        columns = source.columns
        self.xCombo = ElidingComboBox()
        self.yCombo = ElidingComboBox()
        if source.isCsv:
            allColumns = [source.xColumn or "", source.yColumn or "", *columns]
            for combo, chosen in (
                (self.xCombo, source.xColumn),
                (self.yCombo, source.yColumn),
            ):
                combo.addItems(allColumns)
                combo.setCurrentText(chosen or "")
                combo.currentTextChanged.connect(self._reread)

        self.coordinatesCombo = ElidingComboBox()
        body = "the image's body"
        if image.hasGeospatialData:
            with PROJ_LOCK:
                body = bodyOf(image.crs)
        for mode in Coordinates:
            label = f"Degrees on {body}" if mode is Coordinates.BODY else mode.value
            self.coordinatesCombo.addItem(label, mode)
        self.coordinatesCombo.setCurrentIndex(
            self.coordinatesCombo.findData(defaults.coordinates)
        )
        self.coordinatesCombo.currentIndexChanged.connect(self._refresh)
        self.crsEdit = QLineEdit()
        self.crsEdit.setPlaceholderText("EPSG:4326, WKT or PROJ")
        self.crsEdit.editingFinished.connect(self._refresh)
        self.crsNote = MutedLabel(f"Detected: {source.crsNote}")
        self.crsNote.setWordWrap(True)

        self.nameCombo = _columnCombo(columns, defaults.nameColumn)
        self.nameCombo.currentIndexChanged.connect(self._refresh)
        self.classCombo = _columnCombo(columns, defaults.classColumn)
        self.classCombo.currentIndexChanged.connect(self._refresh)
        attributesNote = MutedLabel(
            "ROIs that share a class are one class in the ROI table; the other "
            "columns become ROI table columns."
            if target is Target.ROIS
            else "The other columns are kept as the "
            f"{target.value}' properties and exported with them."
        )
        attributesNote.setWordWrap(True)

        self.preview = QLabel()
        self.preview.setWordWrap(True)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow("File:", self.fileLabel)
        if len(source.layers) > 1:
            form.addRow("Layer:", self.layerCombo)
        if source.isCsv:
            form.addRow("X column:", self.xCombo)
            form.addRow("Y column:", self.yCombo)
        form.addRow("Coordinates:", self.coordinatesCombo)
        form.addRow("CRS:", self.crsEdit)
        form.addRow(self.crsNote)
        form.addRow("Name from:", self.nameCombo)
        if target is Target.ROIS:
            form.addRow("Class from:", self.classCombo)
        form.addRow(attributesNote)
        form.addRow(self.preview)
        panel = QWidget()
        panel.setLayout(form)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        importButton = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert importButton is not None
        importButton.setText("Import")
        self.importButton = importButton
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(PanelScrollArea(panel), 1)
        layout.addWidget(self.buttons)
        self.resize(520, 520)
        self._refresh()

    def options(self) -> ImportOptions:
        """The options as chosen (raises VectorImportError for a CRS Varda
        doesn't recognize)."""
        mode = self.coordinatesCombo.currentData()
        otherCrs = None
        text = self.crsEdit.text().strip()
        if mode is Coordinates.OTHER and text:
            try:
                with PROJ_LOCK:
                    otherCrs = CRS.from_user_input(text)
            except CRSError as error:
                raise VectorImportError(
                    f"Varda doesn't recognize the coordinate system “{text}”."
                ) from error
        return ImportOptions(
            mode,
            otherCrs,
            self.nameCombo.currentData(),
            self.classCombo.currentData() if self.target is Target.ROIS else None,
        )

    def _refresh(self) -> None:
        self.crsEdit.setEnabled(
            self.coordinatesCombo.currentData() is Coordinates.OTHER
        )
        try:
            self.plan = planImport(self.source, self.image, self.options(), self.target)
        except VectorImportError as error:
            self.plan = None
            self.preview.setText(f"<b>Can't import:</b> {error}")
        else:
            self.preview.setText(self._previewText(self.plan))
        self.importButton.setEnabled(self.plan is not None and bool(self.plan.features))

    def _previewText(self, plan: ImportPlan) -> str:
        counts = ", ".join(
            counted(count, kind) for kind, count in self.source.countsByType().items()
        )
        inside = len(plan.features)
        lines = [
            f"In the file: {counts or 'no features'}.",
            f"On the image: {counted(inside, FAMILIES[self.target])}, imported "
            f"as {self.target.value}.",
        ]
        if plan.outside:
            lines.append(f"Outside the image (skipped): {plan.outside}.")
            if not inside:
                lines.append(
                    "<b>None are on the image: check the Coordinates setting.</b>"
                )
        if plan.unplaced:
            lines.append(f"Couldn't be converted (skipped): {plan.unplaced}.")
        names = [f.name or plan.defaultName for f in plan.features[:PREVIEW_NAMES]]
        if names:
            more = ", …" if len(plan.features) > PREVIEW_NAMES else ""
            lines.append(f"First names: {', '.join(names)}{more}")
        lines += plan.notes
        return "<br>".join(lines)

    def _reread(self) -> None:
        """Read the chosen layer, or the CSV with the chosen columns."""
        path = self.source.path
        layer = self.layerCombo.currentText() or None
        x = y = None
        if self.source.isCsv:
            x, y = self.xCombo.currentText(), self.yCombo.currentText()
        self.importButton.setEnabled(False)
        self.preview.setText("Reading…")
        runInBackground(
            lambda: readVectorFile(path, layer, x, y),
            self._setSource,
            self._readFailed,
            description=f"Reading {path.name}",
            owner=self,
        )

    def _setSource(self, source: VectorSource) -> None:
        self.source = source
        self.crsNote.setText(f"Detected: {source.crsNote}")
        defaults = defaultOptions(source, self.image, self.target)
        self.coordinatesCombo.setCurrentIndex(
            self.coordinatesCombo.findData(defaults.coordinates)
        )
        for combo, chosen in (
            (self.nameCombo, defaults.nameColumn),
            (self.classCombo, defaults.classColumn),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(none)", None)
            for column in source.columns:
                combo.addItem(column, column)
            combo.setCurrentIndex(max(combo.findData(chosen), 0) if chosen else 0)
            combo.blockSignals(False)
        self._refresh()

    def _readFailed(self, error: Exception) -> None:
        self.plan = None
        self.importButton.setEnabled(False)
        self.preview.setText(f"<b>Can't import:</b> {error}")

    def accept(self) -> None:
        if self.plan is not None:
            self.sigImport.emit(self.plan)
        super().accept()


class VectorImporter(QObject):
    """The Import… button of a manager: the file dialog, the background read,
    the import dialog, then ``add`` with the plan and a summary message."""

    def __init__(
        self,
        target: Target,
        image: VardaRaster | None,
        add: Callable[[ImportPlan], object],
        parent: QWidget,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings if settings is not None else QSettings()
        self.target = target
        self.image = image
        self._add = add
        self._parent = parent
        self.dialog: VectorImportDialog | None = None
        self.message: QMessageBox | None = None

    def chooseFile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self._parent,
            f"Import {self.target.value}",
            self.folder(),
            IMPORT_FILE_FILTER,
        )
        if path:
            self.settings.setValue(FOLDER_KEY, str(Path(path).parent))
            self.openFile(path)

    def folder(self) -> str:
        """The folder the last import was from (the home folder at first)."""
        return str(self.settings.value(FOLDER_KEY, "") or Path.home())

    def openFile(self, path: str | Path) -> None:
        """Read the file in the background, then show the import dialog."""
        runInBackground(
            lambda: readVectorFile(path),
            self._showDialog,
            self._readFailed,
            description=f"Reading {Path(path).name}",
            owner=self._parent,
        )

    def _showDialog(self, source: VectorSource) -> None:
        if self.image is None:
            return
        self.dialog = VectorImportDialog(source, self.image, self.target, self._parent)
        self.dialog.sigImport.connect(self._import)
        openOwnedDialog(self.dialog)

    def _import(self, plan: ImportPlan) -> None:
        self._add(plan)
        self._tell(QMessageBox.Icon.Information, plan.summary())

    def _readFailed(self, error: Exception) -> None:
        self._tell(QMessageBox.Icon.Warning, str(error))

    def _tell(self, icon: QMessageBox.Icon, text: str) -> None:
        self.message = QMessageBox(
            icon,
            f"Import {self.target.value}",
            text,
            QMessageBox.StandardButton.Ok,
            self._parent,
        )
        openOwnedDialog(self.message)
