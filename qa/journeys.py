"""The steps of "first success", done through the UI: open an image, view
it, look at a pixel's spectrum, run one analysis from the Processing menu,
and export the result. Personas combine them, each in their own way.

Each function returns what the user got (or None) and leaves judging it to
the persona; problems it can see itself become findings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QToolButton,
    QWidget,
)

from qa.harness import QaRun, keyClick, mouseClick, plain
from varda.analysis.dialog import RunAnalysisDialog
from varda.common.entities import VardaRaster
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.image_loading.export_image_dialog import ExportImageDialog
from varda.image_rendering.raster_view.viewport_tools.tool_manager import (
    WrappingToolBar,
)
from varda.workspaces.general_image_analysis import GeneralImageAnalysisWorkflow

PCA_MENU = ("Processing", "Transforms", "Principal Component Analysis (PCA)")
# Dialogs that open without data (0.12.0's CRISM processing); each persona
# that reads or measures dialogs takes these too
CRISM_DIALOG_MENUS = (
    ("Processing", "CRISM", "Process CRISM Observation"),
    ("Processing", "CRISM", "Get CRISM Calibration Files"),
)


@dataclass
class Outcome:
    """How far a persona got towards first success."""

    opened: VardaRaster | None = None
    viewed: GeneralImageAnalysisWorkflow | None = None
    spectrum: bool = False
    result: VardaRaster | None = None
    exported: Path | None = None

    @property
    def success(self) -> bool:
        return bool(
            self.opened and self.viewed and self.spectrum and self.result
        ) and bool(self.exported and self.exported.exists())


def images(run: QaRun) -> list[VardaRaster]:
    assert run.app is not None
    return list(run.app.images)


def openImage(
    run: QaRun, path: str | Path, timeout: float = 60.0
) -> VardaRaster | None:
    """File > Import Image(s), pick ``path``. None when it didn't load
    (a message box, if any, is in ``run.messages``)."""
    before, messages = len(images(run)), len(run.messages)
    run.chooseFiles(path)
    if not run.useMenu("File", "Import Image(s)"):
        return None
    run.waitFor(
        lambda: len(images(run)) > before or len(run.messages) > messages,
        f"{Path(path).name} to open",
        timeout,
    )
    run.settle(0.3)
    if len(images(run)) > before:
        return images(run)[-1]
    return None


def viewImage(run: QaRun, image: VardaRaster) -> GeneralImageAnalysisWorkflow | None:
    """Double-click the image in the Image List."""
    gui = run.app.maingui if run.app else None
    assert gui is not None
    listWidget = gui.imageList
    row = next(
        (
            i
            for i in range(listWidget.count())
            if (entry := listWidget.item(i)) is not None
            and entry.data(Qt.ItemDataRole.UserRole) is image
        ),
        -1,
    )
    if row < 0:
        run.finding("confused", f"{image.name} isn't in the Image List", widget=gui)
        return None
    item = listWidget.item(row)
    assert item is not None
    listWidget.scrollToItem(item)
    center = listWidget.visualItemRect(item).center()
    viewport = listWidget.viewport()
    assert viewport is not None
    run.doubleClick(viewport, center)

    def opened() -> GeneralImageAnalysisWorkflow | None:
        found = [
            w
            for w in gui.childWindows
            if isinstance(w, GeneralImageAnalysisWorkflow)
            and w.config.image.value is image
        ]
        return found[-1] if found else None

    if not run.waitFor(
        lambda: opened() is not None, f"{image.name} to show", timeout=20
    ):
        return None
    run.settle(0.5)
    # The workspace showing *this* image must be the one in front: a new tab
    # behind the current one sends every click to the old image (release QA #3)
    if gui.currentWorkspace() is not opened():
        run.finding(
            "confused",
            f"The workspace for {image.name} opened behind the current tab",
            widget=gui,
            reproduce="Open two images; double-click the second in the Image List",
        )
        gui.centralTabs.setCurrentWidget(opened())  # the user finds the tab
        run.settle(0.3)
    workspace = gui.currentWorkspace()
    assert isinstance(workspace, GeneralImageAnalysisWorkflow)
    run.qaShot(gui, "viewing", f"{image.name} open in a workspace")
    return workspace


def mainViewport(
    workspace: GeneralImageAnalysisWorkflow,
) -> tuple[ImageViewport, WrappingToolBar]:
    """The image view the user works in, and its tool bar."""
    return (
        workspace.tripleRasterView.viewport1,
        workspace.toolManager1.getToolbar(),
    )


def revealToolButton(
    run: QaRun, toolbar: WrappingToolBar, action: QAction
) -> QWidget | None:
    """The tool bar button for ``action``; when the tool bar is too narrow
    to show it, open the » overflow first (a finding: the user must find it)."""
    button = toolbar.widgetForAction(action)
    if (
        button is not None
        and button.isVisible()
        and toolbar.rect().contains(button.geometry())
    ):
        return button
    overflow = toolbar.findChild(QToolButton, "qt_toolbar_ext_button")
    run.finding(
        "confused",
        f"{plain(action.text())} is hidden behind the tool bar's » button "
        f"at this window size ({run.mainWindow.width()}×{run.mainWindow.height()})",
        widget=toolbar.window(),
        reproduce="Open an image; look for the tool under the view",
    )
    if overflow is not None and overflow.isVisible():
        # Outside a main window the overflow is a menu: pick the tool in it
        run.menuAnswers.append(plain(action.text()))
        run.click(overflow, "» overflow button")
        run.settle(0.3)
        if action.isChecked():
            return None
    button = toolbar.widgetForAction(action)
    return button if button is not None and button.isVisible() else None


def imageCenter(viewport: ImageViewport) -> tuple[QWidget, QPoint]:
    """Where on screen the middle of the image is: the widget to click and
    the point in it."""
    view = viewport._gv  # noqa: SLF001 (only to find where the image is drawn)
    item = viewport._imageItem  # noqa: SLF001
    scenePoint = item.sceneBoundingRect().center()
    target = view.viewport()
    assert target is not None
    return target, view.mapFromScene(scenePoint)


def lookAtSpectrum(
    run: QaRun,
    workspace: GeneralImageAnalysisWorkflow,
    modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> bool:
    """Turn on Pixel Select in the tool bar and click the image (as its hint
    says: a plain click). True when a spectrum appeared."""
    viewport, toolbar = mainViewport(workspace)
    action = next(
        (a for a in toolbar.actions() if plain(a.text()) == "Pixel Select"), None
    )
    if action is None:
        run.finding("blocked", "No Pixel Select tool in the image's tool bar")
        return False
    run.log("tool tip", tool="Pixel Select", tip=action.toolTip())
    if not action.isChecked():
        button = revealToolButton(run, toolbar, action)
        if action.isChecked():
            pass  # picked from the » menu
        elif button is None:
            run.log(
                "no button to click; using the tool bar action", tool="Pixel Select"
            )
            action.trigger()
        else:
            run.click(button, "Pixel Select tool")
    before = len(workspace.pixelPlotWidget.pixelCurves)
    target, point = imageCenter(viewport)
    mouseClick(target, modifier, point)
    plotted = run.waitFor(
        lambda: len(workspace.pixelPlotWidget.pixelCurves) > before,
        "a pixel spectrum",
        timeout=5,
        severity=None,
    )
    run.settle(0.3)
    plot = workspace.pixelPlotWidget
    if plotted and not plot.isVisible():
        run.finding(
            "confused",
            "The spectrum is plotted in the Pixel Spectra tab, which stays hidden "
            "behind another tab: clicking the image seems to do nothing",
            widget=workspace,
            reproduce="Pixel Select, click the image; look at the plots",
        )
        run.click(workspace.pixelPlotDock.tabWidget(), "Pixel Spectra tab")
        run.settle(0.3)
    run.qaShot(workspace, "spectrum", "after clicking the image with Pixel Select")
    checkSpectrumPlot(run, workspace, repeated=not plotted)
    return plotted


def checkSpectrumPlot(
    run: QaRun, workspace: GeneralImageAnalysisWorkflow, repeated: bool
) -> None:
    """What a scientist reads off the plot: the y axis says what the values
    are (release QA 0.12.0 #5), and after clicking a pixel already plotted
    (``repeated``) a curve that turned thick yellow says it's selected
    (#9)."""
    plot = workspace.pixelPlotWidget
    if not plot.pixelCurves:
        return
    yLabel = plot.plotItem.getAxis("left").labelText
    run.log("spectrum y axis", label=yLabel)
    if not yLabel.strip():
        run.finding(
            "confused",
            "The spectrum plot's y axis has no label: what are these values?",
            widget=plot,
        )
    selected = plot.selectedCurve
    if repeated and selected is not None:
        label = plot.legend.getLabel(selected.plotDataItem)
        text = label.text if label is not None else ""
        run.log("a repeat click selected a curve", legend=text)
        if "selected" not in text.lower():
            run.finding(
                "confused",
                "Clicking a pixel already plotted turned its curve thick yellow, "
                "and nothing says why",
                widget=plot,
                reproduce="Pixel Select, click one pixel twice",
            )


def clickSamePixelAgain(run: QaRun, workspace: GeneralImageAnalysisWorkflow) -> int:
    """Click the middle of the image again (Pixel Select still on): how many
    curves that added. A pixel already plotted should add none (release QA
    #19: a second, identical curve and legend entry)."""
    viewport, _ = mainViewport(workspace)
    before = len(workspace.pixelPlotWidget.pixelCurves)
    target, point = imageCenter(viewport)
    mouseClick(target, Qt.KeyboardModifier.NoModifier, point)
    run.settle(0.5)
    return len(workspace.pixelPlotWidget.pixelCurves) - before


def removeImage(run: QaRun, image: VardaRaster) -> None:
    """Select the image in the Image List and press Delete (answering Yes)."""
    gui = run.app.maingui if run.app else None
    if gui is None:
        return
    listWidget = gui.imageList
    for row in range(listWidget.count()):
        entry = listWidget.item(row)
        if entry is not None and entry.data(Qt.ItemDataRole.UserRole) is image:
            listWidget.setCurrentRow(row)
    run.answers.append("Yes")  # "Remove from Project?"
    keyClick(listWidget, Qt.Key.Key_Delete)
    run.settle(0.5)


def runAnalysis(
    run: QaRun,
    image: VardaRaster,
    menu: tuple[str, ...] = PCA_MENU,
    timeout: float = 120.0,
) -> VardaRaster | None:
    """Choose an analysis from the Processing menu, pick the image, Run."""
    if not run.useMenu(*menu):
        return None
    dialog = run.newDialog(RunAnalysisDialog)
    if dialog is None:
        run.finding("blocked", f"{' > '.join(menu)} opened no dialog")
        return None
    pickImage(run, dialog, image)
    run.qaShot(dialog, "analysis-dialog", dialog.windowTitle())
    checkAnalysisDialog(run, dialog)
    before, messages = len(images(run)), len(run.messages)
    if not run.click(run.button(dialog, "Run"), "Run button"):
        dialog.reject()
        return None
    run.waitFor(
        lambda: len(images(run)) > before or len(run.messages) > messages,
        f"{dialog.windowTitle()} to finish",
        timeout,
    )
    run.settle(0.3)
    if len(images(run)) > before:
        run.qaShot(run.mainWindow, "analysis-done", "the result in the Image List")
        return images(run)[-1]
    return None


def formFields(dialog: QWidget) -> dict[str, QWidget]:
    """The dialog's form fields by the text of their labels."""
    fields: dict[str, QWidget] = {}
    for form in dialog.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            label = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            field = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            if label is None or field is None:
                continue
            labelWidget, fieldWidget = label.widget(), field.widget()
            if isinstance(labelWidget, QLabel) and fieldWidget is not None:
                fields[plain(labelWidget.text())] = fieldWidget
    return fields


def checkAnalysisDialog(run: QaRun, dialog: QWidget) -> None:
    """Its settings aren't hidden behind a scroll bar while the screen has
    room for them (release QA 0.12.0 #15), and a setting that does nothing
    yet isn't offered (#10: Weight Image Holds without a weight image)."""
    screen = dialog.screen()
    for area in dialog.findChildren(QScrollArea):
        bar = area.verticalScrollBar()
        if bar is None or bar.maximum() == 0 or screen is None:
            continue
        room = screen.availableGeometry().height() - dialog.frameGeometry().height()
        if room > bar.maximum() + 40:  # the window's title bar aside
            run.finding(
                "annoyed",
                f"The {dialog.windowTitle()} dialog hides {bar.maximum()} px of "
                f"its settings behind a scroll bar though the screen has "
                f"{room} px to spare",
                widget=dialog,
            )
    fields = formFields(dialog)
    weights, holds = fields.get("Weight Image"), fields.get("Weight Image Holds")
    if weights is None or holds is None:
        return
    combo = weights.findChild(QComboBox)
    if (
        combo is not None
        and combo.currentText().startswith("None")
        and holds.isEnabled()
    ):
        run.finding(
            "confused",
            "Weight Image Holds can be changed with no weight image chosen, "
            "where it does nothing",
            widget=dialog,
        )


def pickImage(run: QaRun, dialog: QWidget, image: VardaRaster) -> None:
    """Pick ``image`` in the dialog's image drop-down."""
    for combo in dialog.findChildren(QComboBox):
        names = [combo.itemText(i) for i in range(combo.count())]
        if image.name in names:
            run.chooseInCombo(combo, image.name)
            return


def exportImage(
    run: QaRun, image: VardaRaster, path: Path, timeout: float = 60.0
) -> Path | None:
    """File > Export Image…, pick the image, type where, Export."""
    if not run.useMenu("File", "Export Image"):
        return None
    dialog = run.newDialog(ExportImageDialog)
    if dialog is None:
        run.finding("blocked", "File > Export Image opened no dialog")
        return None
    pickImage(run, dialog, image)
    fields = [f for f in dialog.findChildren(QLineEdit) if f.isVisible()]
    pathField = next((f for f in fields if f.placeholderText()), fields[-1])
    run.typeInto(pathField, str(path))
    run.qaShot(dialog, "export-dialog", f"exporting {image.name}")
    messages = len(run.messages)
    if not run.click(run.button(dialog, "Export"), "Export button"):
        dialog.reject()
        return None
    written = run.waitFor(
        lambda: (path.exists() and not _busy()) or len(run.messages) > messages,
        f"{path.name} to be written",
        timeout,
    )
    run.settle(0.3)
    return path if written and path.exists() else None


def _busy() -> bool:
    return any(
        w.isVisible() and type(w).__name__ == "BusyDialog"
        for w in QApplication.topLevelWidgets()
    )


def firstSuccess(run: QaRun, path: Path, exportTo: Path) -> Outcome:
    """The whole journey, each step its own; stops where the user would."""
    outcome = Outcome()
    with run.step("open image", f"File > Import Image(s) > {path.name}"):
        outcome.opened = openImage(run, path)
        if outcome.opened is None:
            run.finding("blocked", f"{path.name} did not open", widget=run.mainWindow)
    if outcome.opened is None:
        return outcome
    with run.step("view image", "Double-click the image in the Image List"):
        outcome.viewed = viewImage(run, outcome.opened)
        if outcome.viewed is None:
            run.finding("blocked", "Double-clicking the image showed nothing")
    if outcome.viewed is not None:
        with run.step("pixel spectrum", "Pixel Select tool, click the image"):
            outcome.spectrum = lookAtSpectrum(run, outcome.viewed)
            if not outcome.spectrum:
                run.finding(
                    "blocked",
                    "A click with Pixel Select plotted no spectrum",
                    widget=outcome.viewed,
                )
    with run.step("run analysis", " > ".join(PCA_MENU)):
        outcome.result = runAnalysis(run, outcome.opened)
        if outcome.result is None:
            run.finding("blocked", "PCA gave no result", widget=run.mainWindow)
    if outcome.result is None:
        return outcome
    with run.step("export", f"File > Export Image… > {exportTo.name}"):
        outcome.exported = exportImage(run, outcome.result, exportTo)
        if outcome.exported is None:
            run.finding("blocked", f"Nothing was written to {exportTo}")
    run.log("first success", success=outcome.success)
    return outcome
