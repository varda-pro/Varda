"""The breaker: opens broken and misleading files, types junk into every
field, presses Esc everywhere, double-clicks, and pulls the image out from
under a running analysis. Flags every crash, traceback, hang, silent wrong
result or lost work. Then the platform's own traps: Windows paths and
keyring, Linux's case-sensitive file names, macOS's ⌘ versus Ctrl."""

from __future__ import annotations

import os
import sys
from pathlib import Path, PureWindowsPath

import keyring.backend
import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QAbstractSpinBox, QApplication, QLineEdit, QWidget

from qa import journeys
from qa.data import QaData, realProducts
from qa.harness import (
    LIBRARY_JARGON,
    QaRun,
    alive,
    keyClick,
    mouseClick,
    plain,
    typeText,
)
from varda.analysis.dialog import RunAnalysisDialog
from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.export_image_dialog import ExportImageDialog
from varda.image_loading.raster_writer import writeRaster

pytestmark = [pytest.mark.qa, pytest.mark.qt_no_exception_capture]

CTRL = Qt.KeyboardModifier.ControlModifier  # ⌘ on macOS
META = Qt.KeyboardModifier.MetaModifier  # the Control key on macOS
JUNK = ("abc", "", "   ", "-1", "1e309", "99999999999", "٣", "🙂", "0x10", "NaN")


def opens(run: QaRun, path: Path | str, label: str) -> VardaRaster | None:
    """Open ``path`` and judge how Varda took it: loaded, or refused with a
    readable message. Silence is a finding."""
    before = len(run.messages)
    image = journeys.openImage(run, path, timeout=60)
    said = run.messagesSince(before)
    run.log("opened", file=label, loaded=image is not None, said=[m.text for m in said])
    if image is None and not said:
        run.finding(
            "confused",
            f"Opening {label} did nothing: no image, no message",
            widget=run.mainWindow,
            reproduce=f"File > Import Image(s) > {label}",
        )
    for message in said:
        jargon = LIBRARY_JARGON.search(message.text)
        if jargon is not None:
            run.finding(
                "confused",
                f"Opening {label}, the message is the library's: {jargon.group(0)!r}",
                shot=message.screenshot,
                reproduce=f"File > Import Image(s) > {label}",
            )
    return image


def test_broken_files(qaRun: QaRun, qaData: QaData) -> None:
    run = qaRun
    for label, path in (
        ("random bytes named .tif", qaData.corrupt),
        ("a GeoTIFF cut in half", qaData.truncated),
        ("an ENVI header with zero bands", qaData.zeroBand),
        ("an empty .tif", qaData.empty),
        ("a file that doesn't exist", qaData.folder / "missing.tif"),
        ("a folder", qaData.folder),
    ):
        with run.step(f"open {label}", f"File > Import Image(s) > {Path(path).name}"):
            image = opens(run, path, label)
            if image is not None:
                # It claims to have loaded: does it survive being looked at?
                workspace = journeys.viewImage(run, image)
                if workspace is not None:
                    run.qaShot(workspace, f"broken-{Path(path).stem}", label)
                    run.mainWindow.closeWorkspace(workspace)
                if label in ("random bytes named .tif", "an empty .tif"):
                    run.finding(
                        "wrong result",
                        f"{label} opened as if it were an image",
                        widget=run.mainWindow,
                    )

    with run.step("no georeferencing", "Open no_crs.tif, view, spectrum"):
        image = opens(run, qaData.noCrs, "a GeoTIFF without georeferencing")
        if image is not None:
            workspace = journeys.viewImage(run, image)
            if workspace is not None:
                journeys.lookAtSpectrum(run, workspace)

    with run.step("Mars tagged as Earth", "Open mars_wgs84.tif (WGS 84 on Mars)"):
        before = len(run.messages)
        image = opens(run, qaData.marsWgs84, "a Mars image tagged WGS 84")
        if image is not None and len(run.messages) == before:
            crs = image.crs.name if image.crs is not None else "none"
            run.finding(
                "confused",
                "A Mars image whose file says Earth (WGS 84) opens without a "
                f"word; its distances and areas would use Earth's ellipsoid "
                f"(CRS: {crs}). A PDS3 label beside it says TARGET_NAME = MARS",
                widget=run.mainWindow,
                reproduce="Open a GeoTIFF tagged EPSG:4326 that is a Mars product",
            )

    with run.step("PDS3 label", "Open mars_pds3.lbl"):
        image = opens(run, qaData.marsLabel, "a PDS3 label (Mars)")
        if image is not None:
            crs = image.crs.to_wkt() if image.crs is not None else ""
            run.log("PDS3 CRS", wkt=crs[:300])
            if crs and "mars" not in crs.lower():
                run.finding(
                    "wrong result",
                    "A PDS3 label for MARS opens with a CRS that doesn't name Mars",
                    widget=run.mainWindow,
                )

    with run.step(
        "spaces and unicode", "Open 'My Data/Ünïcødé 火星 scans/my scene – ñ 火星.tif'"
    ):
        image = opens(run, qaData.awkward, "a path with spaces and unicode")
        if image is not None:
            target = qaData.awkward.with_name("résultat – 火星.tif")
            if journeys.exportImage(run, image, target) is None:
                run.finding(
                    "blocked", "Can't export into a folder with unicode in its name"
                )

    for real in realProducts():
        with run.step(f"real product {real.name}", f"Open {real}"):
            image = opens(run, real, real.name)
            if image is not None:
                journeys.viewImage(run, image)


def _openCube(run: QaRun, qaData: QaData) -> VardaRaster | None:
    with run.step("open"):
        image = journeys.openImage(run, qaData.cube)
    if image is None:
        run.finding("blocked", "The test cube didn't open")
    return image


def test_junk_and_escape(qaRun: QaRun, qaData: QaData) -> None:
    run = qaRun
    image = _openCube(run, qaData)
    if image is None:
        return
    with run.step("Esc everywhere", "Open each dialog and press Esc"):
        for menu in (
            journeys.PCA_MENU,
            ("File", "Export Image"),
            ("Help", "Cite Varda"),
        ):
            if not run.useMenu(*menu, shot=False):
                continue
            dialog = run.newDialog()
            if dialog is None:
                continue
            keyClick(dialog, Qt.Key.Key_Escape)
            run.settle(0.2)
            if alive(dialog) and dialog.isVisible():
                run.finding(
                    "annoyed",
                    f"Esc doesn't close {dialog.windowTitle()}",
                    widget=dialog,
                )
                dialog.reject()

    with run.step("junk in the analysis settings", " > ".join(journeys.PCA_MENU)):
        _junkInSettings(run, image)


def test_junk_export_paths(qaRun: QaRun, qaData: QaData) -> None:
    run = qaRun
    image = _openCube(run, qaData)
    if image is not None:
        with run.step("junk export paths", "File > Export Image…, type junk paths"):
            _junkPaths(run, image)


def test_remove_while_running(qaRun: QaRun, qaData: QaData) -> None:
    run = qaRun
    with run.step(
        "pull the image out from under PCA", "Run PCA on a large cube, then remove it"
    ):
        _removeWhileRunning(run)


def _junkInSettings(run: QaRun, image: VardaRaster) -> None:
    if not run.useMenu(*journeys.PCA_MENU, shot=False):
        return
    dialog = run.newDialog(RunAnalysisDialog)
    if not isinstance(dialog, RunAnalysisDialog):
        return
    journeys.pickImage(run, dialog, image)
    for box in [b for b in dialog.findChildren(QAbstractSpinBox) if b.isVisible()]:
        field = box.findChild(QLineEdit)
        if field is None:
            continue
        for junk in JUNK:
            box.setFocus()
            field.selectAll()
            keyClick(field, Qt.Key.Key_Delete)
            typeText(field, junk)
            keyClick(field, Qt.Key.Key_Tab)  # leave the field
            QApplication.processEvents()
            run.log("typed", into=type(box).__name__, junk=junk, shows=field.text())
    run.qaShot(dialog, "junk-settings", "after typing junk into every number box")
    before, messages = len(journeys.images(run)), len(run.messages)
    runButton = run.button(dialog, "Run")
    if runButton is not None and runButton.isEnabled():
        run.click(runButton, "Run")
        run.waitFor(
            lambda: len(journeys.images(run)) > before or len(run.messages) > messages,
            "PCA after junk settings",
            timeout=120,
            severity=None,
        )
        run.settle(0.3)
        results = journeys.images(run)[before:]
        said = run.messagesSince(messages)
        run.log(
            "after junk settings",
            results=[f"{r.name}: {r.bandCount} bands" for r in results],
            said=[m.text for m in said],
        )
        if not results and not said:
            run.finding(
                "confused",
                "Run with the junk-typed settings did nothing and said nothing",
                widget=run.mainWindow,
            )
    elif dialog.isVisible():
        run.log("Run refused the junk", reason=dialog.statusLabel.text())
        dialog.reject()


def _junkPaths(run: QaRun, image: VardaRaster) -> None:
    cwd = Path.cwd()
    paths = [
        "relative name.tif",
        str(run.folder / "no such folder" / "x.tif"),
        str(run.folder / "wrong extension.xyz"),
        str(run.folder / "UPPER CASE.TIF"),
        str(run.folder / "trailing dot."),
        "🙂",
    ]
    if sys.platform == "win32":
        paths += ["CON.tif", str(run.folder / "bad<>|?*.tif")]
    for typed in paths:
        if not run.useMenu("File", "Export Image", shot=False):
            return
        dialog = run.newDialog(ExportImageDialog)
        if dialog is None:
            return
        journeys.pickImage(run, dialog, image)
        field = next(f for f in dialog.findChildren(QLineEdit) if f.placeholderText())
        run.typeInto(field, typed)
        before = len(run.messages)
        exportButton = run.button(dialog, "Export")
        if exportButton is None or not exportButton.isEnabled():
            dialog.reject()
            continue
        mouseClick(exportButton)
        run.waitFor(lambda: not _busy(), f"exporting to {typed!r}", 30, None)
        run.settle(0.5)
        said = run.messagesSince(before)
        written = _whereWritten(typed, cwd)
        run.log(
            "export junk",
            typed=typed,
            written=str(written),
            said=[m.text for m in said],
        )
        if written is not None and not Path(typed).is_absolute():
            run.finding(
                "confused",
                f"Export to {typed!r} wrote {written}: a folder the user never chose",
                reproduce=f"File > Export Image…, type {typed!r}, Export",
            )
            _remove(written)
        elif written is None and not said:
            run.finding(
                "confused",
                f"Export to {typed!r} wrote nothing and said nothing",
                reproduce=f"File > Export Image…, type {typed!r}, Export",
            )


def _whereWritten(typed: str, cwd: Path) -> Path | None:
    path = Path(typed) if Path(typed).is_absolute() else cwd / typed
    for candidate in (path, path.with_suffix(".img"), Path(str(path) + ".img")):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _remove(path: Path) -> None:
    for sibling in path.parent.glob(path.stem + "*"):
        if sibling.is_file():
            sibling.unlink()


def _busy() -> bool:
    return any(
        w.isVisible() and type(w).__name__ == "BusyDialog"
        for w in QApplication.topLevelWidgets()
    )


def _removeWhileRunning(run: QaRun) -> None:
    rng = np.random.default_rng(5)
    data = rng.uniform(0.1, 0.4, (500, 500, 100)).astype(np.float32)
    source = ArrayDataSource(data, wavelengths=np.linspace(400, 2500, 100))
    path = writeRaster(
        VardaRaster(dataSource=source, name="big"), run.folder / "big.img"
    )
    image = journeys.openImage(run, path)
    if image is None:
        return
    workspace = journeys.viewImage(run, image)
    if not run.useMenu(*journeys.PCA_MENU, shot=False):
        return
    dialog = run.newDialog(RunAnalysisDialog)
    if dialog is None:
        return
    journeys.pickImage(run, dialog, image)
    run.click(run.button(dialog, "Run"), "Run")
    # Straight away: close the workspace, and remove the image (Delete key)
    if workspace is not None:
        run.useMenu("Workspace", "Close Workspace", shot=False)
    gui = run.app.maingui if run.app else None
    if gui is not None:
        listWidget = gui.imageList
        for row in range(listWidget.count()):
            entry = listWidget.item(row)
            if entry is not None and entry.data(Qt.ItemDataRole.UserRole) is image:
                listWidget.setCurrentRow(row)
        run.answers.append("Yes")  # "Remove from Project?"
        keyClick(listWidget, Qt.Key.Key_Delete)
    run.settle(0.5)
    finished = run.waitFor(
        lambda: not _busy(),
        "PCA after its image was removed",
        timeout=180,
        severity="blocked",
    )
    run.settle(1.0)
    run.answers.clear()
    _remove(path)  # 100 MB: not for the uploaded report
    names = [i.name for i in journeys.images(run)]
    run.log("after removing mid-run", finished=finished, images=names)
    run.qaShot(run.mainWindow, "removed-mid-run", "image removed while PCA ran")
    if any(n.startswith("big") and "PCA" in n for n in names) and "big" not in names:
        run.finding(
            "annoyed",
            "PCA of an image the user removed while it ran still lands in the "
            "project (removing it doesn't cancel the run)",
            widget=run.mainWindow,
        )


def test_platform_traps(qaRun: QaRun, qaData: QaData) -> None:
    """The traps of the platform this runs on; the others are skipped."""
    run = qaRun
    if sys.platform == "win32":
        _windows(run, qaData)
    elif sys.platform == "darwin":
        _macos(run, qaData)
    else:
        _linux(run, qaData)


def _windows(run: QaRun, qaData: QaData) -> None:
    with run.step(
        "backslashes and spaces (Windows)", r"Open C:\...\My Data\...\my scene – ñ.tif"
    ):
        backslashed = str(PureWindowsPath(qaData.awkward))
        opens(run, backslashed, "a backslashed path with spaces")
    with run.step("lower-case drive letter (Windows)"):
        path = str(qaData.geotiff)
        lowered = path[0].lower() + path[1:] if path[1:2] == ":" else path
        opens(run, lowered, f"{lowered[:3]}… (lower-case drive)")
    with run.step(
        "case-insensitive name (Windows)", "Open QA_SCENE.TIF for qa_scene.tif"
    ):
        upper = qaData.geotiff.with_name(qaData.geotiff.name.upper())
        image = opens(run, upper, upper.name)
        if image is not None:
            run.log("loaded under", name=image.name)
    with run.step("keyring (Windows)", "Where AI keys would be kept"):
        backend = _realKeyring()
        run.log("keyring backend", backend=backend)
        if "Windows" not in backend and "WinVault" not in backend:
            run.finding(
                "confused",
                f"AI keys would go to {backend}, not Windows Credential Manager",
            )


def _macos(run: QaRun, qaData: QaData) -> None:
    with run.step("⌘ shortcuts (macOS)", "File menu: read the shortcuts; press ⌘N"):
        action = run.menuAction("File", "Import Image(s)")
        shown = (
            action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            if action
            else ""
        )
        run.log("shortcut shown", item="Import Image(s)", shortcut=shown)
        if "Ctrl" in shown:
            run.finding("confused", f"A Mac menu shows {shown!r} instead of ⌘")
        asked = len(run.fileDialogs)
        run.chooseFiles()  # Cancel the file dialog
        run.mainWindow.activateWindow()
        keyClick(run.mainWindow, Qt.Key.Key_N, CTRL)  # ⌘N
        run.settle(0.3)
        if len(run.fileDialogs) == asked:
            run.finding(
                "confused",
                f"⌘N (shown as {shown!r}) doesn't open Import Image(s)",
                widget=run.mainWindow,
            )
            run.fileAnswers.clear()
    with run.step(
        "Control-click (macOS)", "Pixel Select, then Control-click the image"
    ):
        image = journeys.openImage(run, qaData.cube)
        workspace = journeys.viewImage(run, image) if image is not None else None
        if workspace is None:
            return
        _, toolbar = journeys.mainViewport(workspace)
        tips = [a.toolTip() for a in toolbar.actions() if "Ctrl" in a.toolTip()]
        # A Mac user reads "Ctrl+Click" and presses the Control key
        if not journeys.lookAtSpectrum(run, workspace, META) and tips:
            run.finding(
                "confused",
                f"The tool tip says {tips[0]!r}; on a Mac the Control key does "
                "nothing (macOS treats Control-click as a right-click) and it "
                "takes ⌘-click",
                widget=workspace,
                reproduce="macOS: Pixel Select, Control-click the image",
            )
        if not journeys.lookAtSpectrum(run, workspace, CTRL):
            run.finding("blocked", "⌘-click plots no spectrum either", widget=workspace)
    with run.step("Ctrl in the text (macOS)"):
        ctrlTexts = sorted(
            {
                plain(w.toolTip())
                for w in run.mainWindow.findChildren(QWidget)
                if "Ctrl" in w.toolTip()
            }
        )
        run.log("Ctrl in tool tips", texts=ctrlTexts)
        if ctrlTexts:
            run.finding(
                "annoyed",
                f"{len(ctrlTexts)} tool tips say Ctrl on a Mac, e.g. {ctrlTexts[0]!r}",
            )


def _linux(run: QaRun, qaData: QaData) -> None:
    with run.step("case-sensitive names (Linux)", "Open scene.tif and Scene.tif"):
        if qaData.caseTwin is None:
            run.log("skipped: this file system is case-insensitive")
            return
        lower = opens(run, qaData.caseTwin.with_name("scene.tif"), "scene.tif")
        upper = opens(run, qaData.caseTwin, "Scene.tif")
        if lower is not None and upper is not None:
            same = float(np.nanmean(lower.getData())) == float(
                np.nanmean(upper.getData())
            )
            run.log("twins", lower=lower.name, upper=upper.name, same=same)
            if same:
                run.finding(
                    "wrong result",
                    "scene.tif and Scene.tif (different files) opened as the same image",
                    widget=run.mainWindow,
                )
    with run.step("keyring (Linux)", "Where AI keys would be kept"):
        backend = _realKeyring()
        run.log(
            "keyring backend",
            backend=backend,
            session=os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
        )
        if "fail" in backend.lower() or "null" in backend.lower():
            run.finding(
                "confused",
                f"No keychain service: AI keys can't be saved (keyring: {backend})",
            )


def _realKeyring() -> str:
    """The backend keyring would pick on this machine (the tests themselves
    use an in-memory one)."""
    backends = sorted(keyring.backend.get_all_keyring(), key=lambda b: b.priority)
    return (
        type(backends[-1]).__module__ + "." + type(backends[-1]).__name__
        if backends
        else "none"
    )
