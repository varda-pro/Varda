"""The breaker: opens broken and misleading files, types junk into every
field, presses Esc everywhere, double-clicks, and pulls the image out from
under a running analysis. Flags every crash, traceback, hang, silent wrong
result or lost work. Then the platform's own traps: Windows paths and
keyring, Linux's case-sensitive file names, macOS's ⌘ versus Ctrl."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path, PureWindowsPath

import keyring.backend
from keyring.backends.chainer import ChainerBackend
import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QWidget,
)

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
from varda.all_images_view_list.imageview_list import ImageItemDelegate
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
        _marsTaggedAsEarth(run, qaData)

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


def _marsTaggedAsEarth(run: QaRun, qaData: QaData) -> None:
    """A Mars product whose GeoTIFF says WGS 84 (Earth), with a PDS3 label
    beside it saying MARS. Varda should say so three ways: a status-bar
    message, a dot on the thumbnail, and the thumbnail's tool tip."""
    said = len(run.statusMessages)
    image = opens(run, qaData.marsWgs84, "a Mars image tagged WGS 84")
    if image is None:
        return
    run.settle(0.5)
    gui = run.mainWindow
    status = [m for m in run.statusMessages[said:] if image.name in m]
    listWidget = gui.imageList
    row = next(
        (
            r
            for r in range(listWidget.count())
            if (entry := listWidget.item(r)) is not None
            and entry.data(Qt.ItemDataRole.UserRole) is image
        ),
        -1,
    )
    entry = listWidget.item(row) if row >= 0 else None
    tip = str(entry.data(Qt.ItemDataRole.ToolTipRole)) if entry else ""
    index = listWidget.indexFromItem(entry) if entry is not None else None
    badge = index is not None and ImageItemDelegate.hasBadge(index)
    crs = image.crs.name if image.crs is not None else "none"
    run.log("CRS warning", status=status, tip=tip, badge=badge, crs=crs)
    shot = run.qaShot(gui, "mars-wgs84", f"status: {status}; tip: {tip!r}")
    reproduce = "File > Import Image(s) > a WGS 84 GeoTIFF of Mars with its PDS3 label"
    if not (status or badge or "\n" in tip):
        run.finding(
            "confused",
            "A Mars image whose file says Earth (WGS 84) opens without a word; "
            f"its distances and areas would use Earth's ellipsoid (CRS: {crs})",
            shot=shot,
            reproduce=reproduce,
        )
        return
    for missing, shown in (
        ("a status-bar message", status),
        ("the dot on its thumbnail", badge),
        ("a coordinate-system line in its tool tip", "\n" in tip),
    ):
        if not shown:
            run.finding(
                "annoyed",
                f"A WGS 84-tagged Mars image is flagged, but without {missing}",
                shot=shot,
                reproduce=reproduce,
            )
    if status and not re.search(r"Mars|body|Earth", status[0], re.I):
        run.finding(
            "confused",
            f"The status bar flags the image but doesn't say what is wrong: "
            f"{status[0]!r}",
            shot=shot,
        )


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

    with run.step("one wavelength", "PCA with the same 'from' and 'to' wavelength"):
        _oneWavelength(run, image)


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
            shownBefore = field.text()
            box.setFocus()
            field.selectAll()
            keyClick(field, Qt.Key.Key_Delete)
            typeText(field, junk)
            keyClick(field, Qt.Key.Key_Tab)  # leave the field
            QApplication.processEvents()
            run.log("typed", into=type(box).__name__, junk=junk, shows=field.text())
            if not _tookOrRefused(box, junk, shownBefore, field.text()):
                run.finding(
                    "wrong result",
                    f"Typed {junk!r} into a number box and left it; it shows "
                    f"{field.text()!r}, a value never typed (it should take "
                    f"what was typed or refuse it whole)",
                    widget=dialog,
                    reproduce=f"PCA dialog: type {junk!r} into a number box, press Tab",
                )
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


def _tookOrRefused(
    box: QAbstractSpinBox, typed: str, shownBefore: str, shownAfter: str
) -> bool:
    """Whether a number box left after typing shows what was typed, or what
    it showed before (refused whole); never a value made of a part of it
    ("1e309" as 1309, "-1" as 1). Empty or blank text may put the value
    back."""
    if shownAfter == shownBefore:
        return True
    try:
        wanted = float(typed)
    except ValueError:
        return False
    number = shownAfter
    if isinstance(box, (QSpinBox, QDoubleSpinBox)):
        number = number.removeprefix(box.prefix()).removesuffix(box.suffix())
    try:
        return float(number.strip().replace(",", "")) == wanted
    except ValueError:
        return False


def _oneWavelength(run: QaRun, image: VardaRaster) -> None:
    """From and to the same wavelength: one band at most, so PCA must refuse,
    never run on every band (release QA #6, a wrong result)."""
    if not run.useMenu(*journeys.PCA_MENU, shot=False):
        return
    dialog = run.newDialog(RunAnalysisDialog)
    if not isinstance(dialog, RunAnalysisDialog):
        return
    journeys.pickImage(run, dialog, image)
    boxes = [b for b in dialog.findChildren(QDoubleSpinBox) if b.isVisible()][:2]
    if len(boxes) < 2:
        dialog.reject()
        return
    value = boxes[0].cleanText()
    for box in boxes:
        field = box.findChild(QLineEdit)
        if field is None:
            continue
        box.setFocus()
        field.selectAll()
        keyClick(field, Qt.Key.Key_Delete)
        typeText(field, value)
        keyClick(field, Qt.Key.Key_Tab)
    run.settle(0.3)
    runButton = run.button(dialog, "Run")
    run.qaShot(dialog, "one-wavelength", f"from {value} to {value}")
    run.log(
        "one wavelength",
        value=value,
        runEnabled=bool(runButton and runButton.isEnabled()),
        status=dialog.statusLabel.text(),
    )
    if runButton is None or not runButton.isEnabled():
        dialog.reject()
        return
    before = len(journeys.images(run))
    run.click(runButton, "Run")
    run.waitFor(
        lambda: len(journeys.images(run)) > before, "PCA on one wavelength", 120, None
    )
    run.settle(0.3)
    results = journeys.images(run)[before:]
    if results and results[0].bandCount > 1:
        run.finding(
            "wrong result",
            f"PCA from {value} to {value} ran on {results[0].bandCount} components: "
            "the same 'from' and 'to' silently used every band",
            widget=run.mainWindow,
            reproduce="Processing > Transforms > PCA; set from and to to one value",
        )


# Names refused on every system so files stay portable; outside Windows the
# message should say why (release QA 0.12.0 #18)
REFUSED_EVERYWHERE = ("CON.tif", "bad<>|?*.tif")
PORTABLE = re.compile(r"portable|any computer|every system", re.I)


def _junkPaths(run: QaRun, image: VardaRaster) -> None:
    cwd = Path.cwd()
    paths = [
        "relative name.tif",
        str(run.folder / "no such folder" / "x.tif"),
        str(run.folder / "wrong extension.xyz"),
        str(run.folder / "UPPER CASE.TIF"),
        str(run.folder / "trailing dot."),
        "🙂",
        # Names Windows forbids or reserves: refused everywhere, before writing
        # (release QA #16: Windows said "failed: No error")
        REFUSED_EVERYWHERE[0],
        str(run.folder / REFUSED_EVERYWHERE[1]),
    ]
    for typed in paths:
        if not run.useMenu("File", "Export Image", shot=False):
            return
        dialog = run.newDialog(ExportImageDialog)
        if dialog is None:
            return
        journeys.pickImage(run, dialog, image)
        field = next(f for f in dialog.findChildren(QLineEdit) if f.placeholderText())
        run.typeInto(field, typed)
        before, status = len(run.messages), len(run.statusMessages)
        exportButton = run.button(dialog, "Export")
        if exportButton is None or not exportButton.isEnabled():
            dialog.reject()
            continue
        mouseClick(exportButton)
        run.waitFor(lambda: not _busy(), f"exporting to {typed!r}", 30, None)
        run.settle(0.5)
        said = run.messagesSince(before)
        confirmed = run.statusMessages[status:]
        resolved = Path(field.text()) if alive(field) else None
        written = _whereWritten(typed, cwd, resolved)
        run.log(
            "export junk",
            typed=typed,
            written=str(written),
            said=[m.text for m in said],
            status=confirmed,
        )
        if alive(dialog) and dialog.isVisible():
            dialog.reject()
        reproduce = f"File > Export Image…, type {typed!r}, Export"
        if written is not None and written.parent == cwd and cwd != run.folder:
            run.finding(
                "confused",
                f"Export to {typed!r} wrote {written}: a folder the user never chose",
                reproduce=reproduce,
            )
        elif written is None and not said:
            run.finding(
                "confused",
                f"Export to {typed!r} wrote nothing and said nothing",
                reproduce=reproduce,
            )
        if written is not None and ".." in written.name:
            run.finding(
                "annoyed",
                f"Export to {typed!r} wrote {written.name!r} (a doubled dot)",
                reproduce=reproduce,
            )
        told = [m for m in confirmed if written is not None and written.name in m]
        if written is not None and not any(str(written.parent) in m for m in told):
            run.finding(
                "confused",
                f"Export to {typed!r} doesn't say where the file went ({written})",
                reproduce=reproduce,
            )
        elif told and not told[-1].startswith(f"Exported {written.name}"):
            # a narrow window cuts the end off (release QA 0.12.0 #17)
            run.finding(
                "annoyed",
                f"The status bar names the folder before the file: {told[-1]!r}",
                reproduce=reproduce,
            )
        if written is None and Path(typed).name in REFUSED_EVERYWHERE:
            for message in said:
                if sys.platform != "win32" and not PORTABLE.search(message.text):
                    run.finding(
                        "confused",
                        f"Export refuses {typed!r} with a Windows rule on "
                        f"{sys.platform}: {message.text[:160]!r}",
                        shot=message.screenshot,
                        reproduce=reproduce,
                    )
        for message in said:
            if "No error" in message.text or LIBRARY_JARGON.search(message.text):
                run.finding(
                    "confused",
                    f"Export to {typed!r} says {message.text[:160]!r}",
                    shot=message.screenshot,
                    reproduce=reproduce,
                )
        if written is not None:
            _remove(written)


def _whereWritten(typed: str, cwd: Path, resolved: Path | None) -> Path | None:
    """The file an export wrote: where the dialog said it would write, or
    the working directory (where a bare name used to land)."""
    path = Path(typed) if Path(typed).is_absolute() else cwd / typed
    candidates = [path, path.with_suffix(".img"), Path(str(path) + ".img")]
    if resolved is not None:
        candidates = [resolved, resolved.with_suffix(".img"), *candidates]
    for candidate in candidates:
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
    journeys.removeImage(run, image)
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
        # keyring's chainer defers to its members; the name lists them
        if "WinVault" not in backend:
            run.finding(
                "confused",
                f"AI keys would go to {backend}, not Windows Credential Manager",
            )


def _macos(run: QaRun, qaData: QaData) -> None:
    with run.step("⌘ shortcuts (macOS)", "File menu: read the shortcuts; press ⌘O"):
        action = run.menuAction("File", "Import Image(s)")
        shown = (
            action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            if action
            else ""
        )
        run.log("shortcut shown", item="Import Image(s)", shortcut=shown)
        if "Ctrl" in shown:
            run.finding("confused", f"A Mac menu shows {shown!r} instead of ⌘")
        elif not shown:
            run.finding("annoyed", "Import Image(s) shows no shortcut in the menu")
        asked = len(run.fileDialogs)
        run.chooseFiles()  # Cancel the file dialog
        run.mainWindow.activateWindow()
        keyClick(run.mainWindow, Qt.Key.Key_O, CTRL)  # ⌘O, where users look
        run.settle(0.3)
        if len(run.fileDialogs) == asked:
            run.finding(
                "confused",
                f"⌘O (shown as {shown!r}) doesn't open Import Image(s)",
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
        # A Mac user who reads "Ctrl" presses the Control key
        if not journeys.lookAtSpectrum(run, workspace, META):
            if tips:
                run.finding(
                    "confused",
                    f"The tool tip says {tips[0]!r}; on a Mac the Control key "
                    "does nothing (macOS treats Control-click as a right-click) "
                    "and it takes ⌘-click",
                    widget=workspace,
                    reproduce="macOS: Pixel Select, Control-click the image",
                )
            if not journeys.lookAtSpectrum(run, workspace, CTRL):
                run.finding(
                    "blocked", "⌘-click plots no spectrum either", widget=workspace
                )
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
        # With no keychain, Varda keeps a key for the session and says why
        # in AI settings (#254); the backend is only logged
        run.log(
            "keychain",
            present=not ("fail" in backend.lower() or "null" in backend.lower()),
        )


def _realKeyring() -> str:
    """The backend keyring would pick on this machine. The tests' in-memory
    one (conftest.MemoryKeyring) is a KeyringBackend subclass too, so
    keyring offers it: leave out backends defined in test code. keyring's
    ChainerBackend only defers to the other backends (on Windows to
    WinVaultKeyring), so it is named with its members: ``chainer[WinVault…]``."""

    def name(b: keyring.backend.KeyringBackend) -> str:
        return type(b).__module__ + "." + type(b).__name__

    def real(b: keyring.backend.KeyringBackend) -> bool:
        return not type(b).__module__.split(".")[-1].startswith(("conftest", "test_"))

    backends = sorted(
        (b for b in keyring.backend.get_all_keyring() if real(b)),
        key=lambda b: b.priority,
    )
    if not backends:
        return "none"
    best = backends[-1]
    if isinstance(best, ChainerBackend):
        members = [name(b) for b in ChainerBackend.backends if real(b)]
        return f"{name(best)}[{', '.join(members)}]"
    return name(best)
