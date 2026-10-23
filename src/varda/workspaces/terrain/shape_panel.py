"""3D Terrain's Shape model surface: a plate shape model in its own 3D view,
colored by plate data, with a readout of the plate under a click.

The model is read, oriented, displayed (decimated above the plate cap) and
indexed for ray casting in the background. A click on the page reports the
view ray; the readout comes from where that ray meets the full model
(``RayCaster``), so a decimated display or a body with overhangs never
changes it. Plate data must have been made for this model (its hash and
plate count, ``plate_data.attach``)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import attrs
import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from varda.common.ui import FlowLayout, PanelScrollArea
from varda.geo.bodies import BODIES, bodyNamed, smallBodies
from varda.shapes.formats import (
    METERS_NOTE,
    SHAPE_SUFFIXES,
    ShapeRead,
    readShape,
    writeObj,
    writeStl,
)
from varda.shapes.geophysics import GeophysicsSettings, geophysics
from varda.shapes.gravity import CLOSED_ONLY
from varda.shapes.mesh import ShapeModel, latLonRadius
from varda.shapes.plate_data import (
    PlateData,
    attach,
    AmbiguousColumns,
    readAltwgPlateTable,
    readPlateCsv,
    writePlateCsv,
)
from varda.shapes.raycast import RayCaster
from varda.terrain.shape_scene import (
    WEB_EXPORT_PLATES,
    ShapeDisplay,
    ShapeSettings,
    colorPayload,
    plateColors,
    shapeDisplay,
    shapePageHtml,
)
from varda.utilities.background import onMainThread, runInBackground
from varda.workspaces.terrain.geophysics_dialog import GeophysicsDialog
from varda.workspaces.terrain.still_dialog import StillDialog
from varda.workspaces.terrain.terrain_view import TerrainView

NO_BODY = "(not set)"
NO_COLOR = "None"
RGB = "RGB (three datasets)"
COLOR_MAPS = {
    "Viridis": "viridis",
    "Magma": "magma",
    "Cividis": "cividis",
    "Turbo": "turbo",
    "Coolwarm": "coolwarm",
    "Gray": "gray",
}
SIGMA_VIEWS = {"Off": "off", "Color by sigma": "color", "Fade uncertain plates": "fade"}
SHAPE_FILTER = "Shape models (" + " ".join(f"*{s}" for s in SHAPE_SUFFIXES) + ")"
PLATE_FILTER = "Plate data (*.csv *.fits *.fit)"


class ShapePanel(QWidget):
    """Controls on the left, the 3D view on the right."""

    pickedPlate = pyqtSignal(int)
    sigLoaded = pyqtSignal()  # a model was read and shown
    sigPlateDataChanged = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.model: ShapeModel | None = None
        self.path: Path | None = None
        self.unit: str = "km"
        self.notes: list[str] = []
        self.plateData: dict[str, PlateData] = {}
        # files to reopen with a session: {"path": …, "column": … (a table's)}
        self.plateDataFiles: list[dict] = []
        self._display: ShapeDisplay | None = None
        self._caster: RayCaster | None = None
        self._camera: dict | None = None
        self._loading = 0  # the latest load (older results are dropped)
        # a session's plate data and coloring, applied once its model is in
        self._pending: dict | None = None

        self.view = TerrainView()
        self.view.sigPick.connect(self.onPick)
        self.view.sigReady.connect(self._onReady)
        self.view.sigFailed.connect(self._onFailed)
        self._buildControls()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(PanelScrollArea(self.controls))
        splitter.addWidget(self.view)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 1100])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    # --- controls ---------------------------------------------------------------

    def _buildControls(self) -> None:
        self.controls = QWidget()
        openButton = QPushButton("Open Shape Model…")
        openButton.setToolTip(
            "A plate model: OBJ, Gaskell PLT or ICQ, FITS X/Y/Z planes, or a "
            "NAIF DSK (with the spice extra)"
        )
        openButton.clicked.connect(self._chooseShape)
        self.bodyBox = QComboBox()
        self.bodyBox.addItem(NO_BODY)
        names = [b.name for b in smallBodies()] + [
            name for name in BODIES if name not in {b.name for b in smallBodies()}
        ]
        self.bodyBox.addItems(names)
        self.bodyBox.setToolTip(
            "The body the model is of: its size checks the model's unit, and "
            "Shape Geophysics takes its density and rotation period from it"
        )
        self.info = QLabel("No shape model open.")
        self.info.setWordWrap(True)
        self.status = QLabel("")
        self.status.setWordWrap(True)

        addData = QPushButton("Add Plate Data…")
        addData.setToolTip(
            "Per-plate values made for this model: a plate CSV Varda wrote, or "
            "an ALTWG plate-ancillary FITS table"
        )
        addData.clicked.connect(self._choosePlateData)
        self.colorBox = QComboBox()
        self.colorBox.addItem(NO_COLOR)
        self.colorBox.setToolTip("The plate data that colors the plates")
        self.colorBox.currentIndexChanged.connect(lambda _: self._onColoring())
        self.rgbBoxes = [QComboBox() for _ in range(3)]
        for box in self.rgbBoxes:
            box.currentIndexChanged.connect(lambda _: self._recolor())
        self.colorMapBox = QComboBox()
        self.colorMapBox.addItems(list(COLOR_MAPS))
        self.colorMapBox.currentIndexChanged.connect(lambda _: self._recolor())
        self.sigmaBox = QComboBox()
        self.sigmaBox.addItems(list(SIGMA_VIEWS))
        self.sigmaBox.setToolTip(
            "Show the data's uncertainty: color the plates by their sigma, or "
            "gray out plates whose sigma is more than a fraction of their value"
        )
        self.sigmaBox.currentIndexChanged.connect(lambda _: self._onColoring())
        self.fadeAbove = QDoubleSpinBox()
        self.fadeAbove.setRange(0.01, 10.0)
        self.fadeAbove.setSingleStep(0.05)
        self.fadeAbove.setValue(0.5)
        self.fadeAbove.setPrefix("σ/|value| > ")
        self.fadeAbove.valueChanged.connect(lambda _: self._recolor())

        self.sunLight = QCheckBox("Light from the sun")
        self.sunLight.setToolTip(
            "Off: a headlight follows the camera. On: the sun shines from the "
            "subsolar point below"
        )
        self.sunLight.toggled.connect(lambda _: self._onLight())
        self.sunLat = QDoubleSpinBox()
        self.sunLat.setRange(-90, 90)
        self.sunLat.setSuffix("°")
        self.sunLon = QDoubleSpinBox()
        self.sunLon.setRange(0, 360)
        self.sunLon.setSuffix("° E")
        for box in (self.sunLat, self.sunLon):
            box.valueChanged.connect(lambda _: self._onLight())

        self.readout = QLabel("Click the model to read a plate.")
        self.readout.setWordWrap(True)
        self.readout.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        home = QPushButton("Reset View")
        home.clicked.connect(self.view.home)
        export = QPushButton("Export")
        menu = QMenu(export)
        menu.addAction("Shape as OBJ…", self.exportObj)
        menu.addAction("Shape as STL…", self.exportStl)
        menu.addAction("Plate Data as CSV…", self.exportPlateCsv)
        menu.addAction("Web Page…", self.exportWebPage)
        menu.addAction("Still…", self.exportStill)
        export.setMenu(menu)
        self.geophysicsButton = QPushButton("Shape Geophysics…")
        self.geophysicsButton.setToolTip(
            "Per-plate gravity, slope, geopotential elevation and tilt of a "
            "closed model, with bounds from the density's uncertainty"
        )
        self.geophysicsButton.setEnabled(False)
        self.geophysicsButton.clicked.connect(self.openGeophysics)

        self.form = form = QFormLayout()
        form.addRow("Body:", self.bodyBox)
        form.addRow(openButton)
        form.addRow(self.info)
        form.addRow(addData)
        form.addRow("Color by:", self.colorBox)
        for label, box in zip(("Red:", "Green:", "Blue:"), self.rgbBoxes, strict=True):
            form.addRow(label, box)
        form.addRow("Color map:", self.colorMapBox)
        form.addRow("Uncertainty:", self.sigmaBox)
        form.addRow("Fade when:", self.fadeAbove)
        form.addRow("", self.sunLight)
        form.addRow("Sun latitude:", self.sunLat)
        form.addRow("Sun longitude:", self.sunLon)
        buttons = FlowLayout()
        for button in (home, export, self.geophysicsButton):
            buttons.addWidget(button)
        layout = QVBoxLayout(self.controls)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(QLabel("Readout:"))
        layout.addWidget(self.readout)
        layout.addWidget(self.status)
        layout.addStretch(1)
        self._onColoring()
        self._onLight()

    def settings(self) -> ShapeSettings:
        choice = self.colorBox.currentText()
        rgb = None
        if choice == RGB:
            rgb = tuple(box.currentText() for box in self.rgbBoxes)
        return ShapeSettings(
            colorBy=choice if choice not in (NO_COLOR, RGB) else None,
            colorMap=COLOR_MAPS[self.colorMapBox.currentText()],
            rgb=rgb if rgb and all(rgb) else None,  # ty: ignore[invalid-argument-type]
            sigmaView=SIGMA_VIEWS[self.sigmaBox.currentText()],  # ty: ignore[invalid-argument-type]
            fadeAbove=self.fadeAbove.value(),
            light="sun" if self.sunLight.isChecked() else "headlight",
            sunBodyFrame=_direction(self.sunLat.value(), self.sunLon.value()),
        )

    def _onColoring(self) -> None:
        choice = self.colorBox.currentText()
        rgb = choice == RGB
        for box in self.rgbBoxes:
            self.form.setRowVisible(box, rgb)
        single = choice not in (NO_COLOR, RGB)
        self.form.setRowVisible(self.colorMapBox, single)
        self.form.setRowVisible(self.sigmaBox, single)
        self.form.setRowVisible(
            self.fadeAbove,
            single and self.sigmaBox.currentText() == "Fade uncertain plates",
        )
        self._recolor()

    def _onLight(self) -> None:
        sun = self.sunLight.isChecked()
        self.form.setRowVisible(self.sunLat, sun)
        self.form.setRowVisible(self.sunLon, sun)
        if self.view.ready:
            self.view.setSettings(self.settings().viewSettings())

    # --- opening ----------------------------------------------------------------

    def body(self) -> str | None:
        text = self.bodyBox.currentText()
        return None if text == NO_BODY else text

    def _chooseShape(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Shape Model", "", SHAPE_FILTER
        )
        if path:
            self.openShape(Path(path))

    def openShape(self, path: Path, unit: str | None = None) -> None:
        """Read, orient, display and index the model in ``path`` in the
        background; a model that looks like it's in meters asks first."""
        path = Path(path)
        if self.body() is None:
            guess = next(
                (bodyNamed(word) for word in _words(path.stem) if bodyNamed(word)), None
            )
            if guess is not None:
                self.bodyBox.setCurrentText(guess.name)
        body = self.body()
        self._loading += 1
        loading = self._loading
        self.status.setText(f"Reading {path.name}…")
        self.view.working(f"Reading {path.name}…")

        def work() -> tuple[ShapeRead, ShapeDisplay, RayCaster]:
            read = readShape(path, body, unit)  # ty: ignore[invalid-argument-type]
            # the derived values the panel shows, computed here, not on the GUI
            # thread
            model = read.model
            if model.isClosed:
                _ = model.volume
            _ = model.meanEdgeKm
            display = shapeDisplay(model, ShapeSettings().maxPlates)
            return read, display, RayCaster(read.model)

        runInBackground(
            work,
            lambda result: self._onRead(loading, path, unit, *result),
            lambda error: self._readFailed(loading, path, error),
            description="Reading a shape model",
            owner=self,
        )

    def _readFailed(self, loading: int, path: Path, error: Exception) -> None:
        if loading == self._loading:
            self.view.idle()
            self.status.setText(f"{path.name} couldn't be read: {error}")

    def _onRead(
        self,
        loading: int,
        path: Path,
        unit: str | None,
        read: ShapeRead,
        display: ShapeDisplay,
        caster: RayCaster,
    ) -> None:
        if loading != self._loading:
            return
        if read.suggestedUnit == "m":
            answer = QMessageBox.question(
                self,
                "Shape model units",
                f"{METERS_NOTE} {path.name}'s mean radius is about 1000 times "
                f"{self.body()}'s in km.",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.openShape(path, "m")
                return
        self.path, self.unit = path, unit or "km"
        self.model, self._display, self._caster = read.model, display, caster
        self.notes = [n for n in read.notes if n != METERS_NOTE]
        self.plateData, self.plateDataFiles = {}, []
        self._refreshColorChoices()
        self._showInfo()
        self.status.setText("")
        self.readout.setText("Click the model to read a plate.")
        self.geophysicsButton.setEnabled(read.model.isClosed)
        if self._pending is not None:
            pending, self._pending = self._pending, None
            for entry in pending["files"]:
                self.openPlateData(
                    Path(entry["path"]),
                    pending["colorBy"],
                    pending["rgb"],
                    entry.get("column"),
                )
        self._showPage()
        self.sigLoaded.emit()

    def _showInfo(self) -> None:
        model = self.model
        if model is None:
            return
        lines = [
            f"{model.name}: {model.plateCount:,} plates, {len(model.vertices):,} "
            f"vertices, mean edge {model.meanEdgeKm * 1000:,.1f} m",
        ]
        if model.isClosed:
            lines.append(f"closed, volume {model.volume:.6g} km³")
        else:
            lines.append("open (a patch or a model with holes)")
        if self._display is not None and self._display.isDecimated:
            lines.append(self._display.note)
        lines += self.notes
        self.info.setText("\n".join(lines))

    def _showPage(self) -> None:
        if self.model is None or self._display is None:
            return
        self.view.showPage(
            shapePageHtml(
                self.model,
                self.settings(),
                self.plateData,
                self._camera,
                display=self._display,
            )
        )

    def _onReady(self) -> None:
        self.view.setSettings(self.settings().viewSettings())

    def _onFailed(self, reason: str) -> None:
        self.status.setText(f"The 3D view can't be shown here: {reason}")

    # --- plate data ---------------------------------------------------------------

    def _choosePlateData(self) -> None:
        if self.model is None:
            self.status.setText("Open a shape model first.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Add Plate Data", "", PLATE_FILTER)
        if path:
            self.openPlateData(Path(path))

    def openPlateData(
        self,
        path: Path,
        colorBy: str | None = None,
        rgb: list[str] | None = None,
        column: str | None = None,
    ) -> None:
        """Add the datasets in ``path``, read in the background; a file made
        for another model is refused with the reason in the status line.
        ``colorBy`` and ``rgb`` (a session's coloring) are chosen once in."""
        model = self.model
        if model is None:
            return
        path = Path(path)
        self.status.setText(f"Reading {path.name}…")

        def work() -> list[PlateData]:
            if path.suffix.lower() == ".csv":
                return readPlateCsv(path, model)
            return [readAltwgPlateTable(path, model, column)]

        def done(datasets: list[PlateData]) -> None:
            if self.model is not model:
                return
            for data in datasets:
                self.addPlateData(data)
            entry = {"path": str(path)}
            if column is not None:
                entry["column"] = column
            self.plateDataFiles.append(entry)
            note = datasets[0].provenance.get("match") if datasets else None
            self.status.setText(
                f"Added {', '.join(d.name for d in datasets)}"
                + (f" ({note})" if note else "")
            )
            for box, name in zip(self.rgbBoxes, rgb or [], strict=False):
                if box.findText(name) >= 0:
                    box.setCurrentText(name)
            if colorBy is not None and self.colorBox.findText(colorBy) >= 0:
                self.colorBox.setCurrentText(colorBy)

        def failed(error: Exception) -> None:
            if not isinstance(error, AmbiguousColumns):
                self.status.setText(str(error))
                return
            chosen, ok = QInputDialog.getItem(
                self, "Plate data", f"{path.name}: which column holds the values?",
                error.columns, 0, False,
            )  # fmt: skip
            if ok and chosen:
                self.openPlateData(path, colorBy, rgb, chosen)
            else:
                self.status.setText(str(error))

        runInBackground(
            work, done, failed, description="Reading plate data", owner=self
        )

    def addPlateData(self, data: PlateData) -> None:
        """Raises ValueError for data made for another model."""
        if self.model is None:
            raise ValueError("Open a shape model first.")
        attach(self.model, data)
        self.plateData[data.name] = data
        self._refreshColorChoices()
        self.sigPlateDataChanged.emit()

    def _refreshColorChoices(self) -> None:
        current = self.colorBox.currentText()
        names = list(self.plateData)
        for box in [self.colorBox, *self.rgbBoxes]:
            box.blockSignals(True)
            keep = box.currentText()
            box.clear()
            if box is self.colorBox:
                box.addItem(NO_COLOR)
                box.addItems(names)
                if len(names) >= 3:
                    box.addItem(RGB)
            else:
                box.addItems(names)
            index = box.findText(current if box is self.colorBox else keep)
            box.setCurrentIndex(max(index, 0))
            box.blockSignals(False)
        self._onColoring()

    def _recolor(self) -> None:
        if self._display is None or not self.view.ready:
            return
        try:
            colors, legend = plateColors(self._display, self.settings(), self.plateData)
        except (KeyError, ValueError) as error:
            self.status.setText(str(error))
            return
        payload = json.dumps(colorPayload(colors, legend))
        self.view.evaluate(f"window.viewer.setColors({payload})", lambda _: None)

    # --- Shape Geophysics ----------------------------------------------------------------

    def openGeophysics(self) -> None:
        """The settings dialog, then the run; with no model open, the file
        chooser first."""
        if self.model is None:
            self.status.setText("Open a closed shape model for Shape Geophysics.")
            self.sigLoaded.connect(self._geophysicsOnceLoaded)
            self._chooseShape()
            return
        if not self.model.isClosed:
            self.status.setText(CLOSED_ONLY)
            return
        name = self.body()
        body = bodyNamed(name) if name else None
        dialog = GeophysicsDialog(body, self.model.plateCount, self)
        if dialog.exec():
            settings = dialog.settings()
            if settings is not None:
                self.runGeophysics(settings)

    def _geophysicsOnceLoaded(self) -> None:
        self.sigLoaded.disconnect(self._geophysicsOnceLoaded)
        self.openGeophysics()

    def runGeophysics(self, settings: GeophysicsSettings) -> None:
        """Run Shape Geophysics in the background; its datasets join the
        plate data and slope colors the plates."""
        model = self.model
        if model is None:
            return
        self.geophysicsButton.setEnabled(False)
        progress = onMainThread(
            lambda percent, message: self.status.setText(
                f"Shape Geophysics {percent}%: {message}"
            )
        )

        def done(results: list[PlateData]) -> None:
            self.geophysicsButton.setEnabled(True)
            if self.model is not model:
                return  # another model was opened meanwhile
            for data in results:
                self.addPlateData(data)
            self.colorBox.setCurrentText("slope")
            evaluated = results[0].provenance["evaluatedOn"]
            note = (
                f" (gravity evaluated on {evaluated:,} plates and carried to all)"
                if evaluated < model.plateCount
                else ""
            )
            self.status.setText(f"Shape Geophysics done{note}.")

        def failed(error: Exception) -> None:
            self.geophysicsButton.setEnabled(True)
            self.status.setText(f"Shape Geophysics failed: {error}")

        runInBackground(
            lambda: geophysics(model, settings, progress),
            done,
            failed,
            description="Shape Geophysics",
            owner=self,
        )

    # --- the readout ------------------------------------------------------------------

    def onPick(self, pick: dict) -> None:
        """The plate where the clicked view ray meets the full model."""
        if self.model is None or self._caster is None:
            return
        origin = np.asarray(pick.get("origin", [0, 0, 0]), np.float64)
        direction = np.asarray(pick.get("direction", [0, 0, 1]), np.float64)
        hits = self._caster.intersect(origin[None], direction[None])
        plate = int(hits.plate[0])
        if plate < 0:
            return
        x, y, z = hits.points[0]
        lat, lon, radius = latLonRadius(hits.points)[0]
        text = (
            f"Plate {plate} · X {x:.4f} Y {y:.4f} Z {z:.4f} km · lat {lat:.3f}° "
            f"lon {lon:.3f}° r {radius:.4f} km"
        )
        name = self.colorBox.currentText()
        data = self.plateData.get(name)
        if data is not None:
            value = f"{data.values[plate]:.6g}"
            if data.sigma is not None:
                value += f" ± {data.sigma[plate]:.3g}"
            text += f" · {data.name} {value} {data.unit}".rstrip()
        self.readout.setText(text)
        self.pickedPlate.emit(plate)

    # --- exports ------------------------------------------------------------------------

    def _savePath(self, title: str, suffix: str, kind: str) -> Path | None:
        if self.model is None:
            self.status.setText("Open a shape model first.")
            return None
        name = f"{self.model.name}{suffix}"
        path, _ = QFileDialog.getSaveFileName(self, title, name, f"{kind} (*{suffix})")
        return Path(path) if path else None

    def _inBackground(self, work, done: str, what: str) -> None:
        runInBackground(
            work,
            lambda _: self.status.setText(done),
            lambda error: self.status.setText(f"{what} failed: {error}"),
            description=what,
            owner=self,
        )

    def exportObj(self) -> None:
        path, model = (
            self._savePath("Export Shape as OBJ", ".obj", "Wavefront OBJ"),
            self.model,
        )
        if path is not None and model is not None:
            self._inBackground(
                lambda: writeObj(model, path), f"Saved {path.name}", "Saving OBJ"
            )

    def exportStl(self) -> None:
        path, model = (
            self._savePath("Export Shape as STL", ".stl", "STL solid"),
            self.model,
        )
        if path is not None and model is not None:
            self._inBackground(
                lambda: writeStl(model, path), f"Saved {path.name}", "Saving STL"
            )

    def exportPlateCsv(self) -> None:
        if not self.plateData:
            self.status.setText("There is no plate data to export.")
            return
        path, model = self._savePath("Export Plate Data", ".csv", "CSV"), self.model
        datasets = list(self.plateData.values())
        if path is not None and model is not None:
            self._inBackground(
                lambda: writePlateCsv(path, model, datasets),
                f"Saved {path.name}",
                "Saving plate data",
            )

    def exportWebPage(self) -> None:
        path, model = self._savePath("Export Web Page", ".html", "Web page"), self.model
        if path is None or model is None:
            return
        settings = self.settings()
        capped = attrs.evolve(settings, maxPlates=WEB_EXPORT_PLATES)
        plateData = dict(self.plateData)

        def save(camera: dict | None) -> None:
            self._inBackground(
                lambda: path.write_text(
                    shapePageHtml(model, capped, plateData, camera, panel=True),
                    encoding="utf-8",
                ),
                f"Saved {path.name} (at most {WEB_EXPORT_PLATES:,} plates)",
                "Saving the web page",
            )

        self.view.camera(save)

    def exportStill(self) -> None:
        if self.model is None or not self.view.ready:
            self.status.setText("Open a shape model first.")
            return
        dialog = StillDialog(self)
        if not dialog.exec():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Still", f"{self.model.name}.png", "PNG image (*.png)"
        )
        if not path:
            return
        width, height = dialog.stillSize()

        def done(png: bytes | None) -> None:
            if png is None:
                self.status.setText("The still couldn't be rendered.")
                return
            Path(path).write_bytes(png)
            self.status.setText(f"Saved {Path(path).name}")

        self.view.still(width, height, dialog.transparent.isChecked(), done)

    # --- sessions -------------------------------------------------------------------------

    def sessionState(self) -> dict:
        settings = self.settings()
        return {
            "path": None if self.path is None else str(self.path),
            "body": self.body(),
            "unit": self.unit,
            # each with "path", so Package Project carries the files
            "plateData": list(self.plateDataFiles),
            "colorBy": self.colorBox.currentText(),
            "rgb": [box.currentText() for box in self.rgbBoxes],
            "colorMap": self.colorMapBox.currentText(),
            "sigmaView": self.sigmaBox.currentText(),
            "fadeAbove": settings.fadeAbove,
            "sunLight": self.sunLight.isChecked(),
            "sun": [self.sunLat.value(), self.sunLon.value()],
        }

    def restoreSessionState(self, state: dict) -> None:
        if state.get("body"):
            self.bodyBox.setCurrentText(state["body"])
        self.colorMapBox.setCurrentText(state.get("colorMap", "Viridis"))
        self.sigmaBox.setCurrentText(state.get("sigmaView", "Off"))
        self.fadeAbove.setValue(float(state.get("fadeAbove", 0.5)))
        self.sunLight.setChecked(bool(state.get("sunLight", False)))
        lat, lon = state.get("sun", [0.0, 0.0])
        self.sunLat.setValue(lat)
        self.sunLon.setValue(lon)
        path = state.get("path")
        if not path:
            return
        self._pending = {
            "files": [e for e in state.get("plateData", []) if e.get("path")],
            "colorBy": state.get("colorBy", NO_COLOR),
            "rgb": state.get("rgb", []),
        }
        # the unit read last time (asked about then, if at all)
        self.openShape(Path(path), "m" if state.get("unit") == "m" else "km")


def _direction(latitude: float, longitude: float) -> tuple[float, float, float]:
    """A unit vector toward planetocentric ``latitude``, east ``longitude``."""
    lat, lon = math.radians(latitude), math.radians(longitude)
    return (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))


def _words(text: str) -> list[str]:
    return [
        w for w in text.replace("-", "_").replace(" ", "_").split("_") if len(w) >= 3
    ]
