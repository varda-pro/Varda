import logging
from collections.abc import Iterable
from pathlib import Path

from app_model.types import (
    Action,
    KeyBindingRule,
    KeyCode,
    KeyMod,
    MenuRule,
    StandardKeyBinding,
)
from PyQt6.QtCore import QStandardPaths, Qt
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QProgressDialog

from varda.utilities.resources import bundledIcon
from varda._actions._menu_ids import MenuGroup, MenuId
from varda.command_ids import (
    IMPORT_IMAGE_ID,
    OPEN_SESSION_ID,
    RESTORE_LAST_SESSION_ID,
)
from varda.common.di_types import ProjectImages
from varda.context_keys import EXPR_HAS_IMAGES
from varda.image_loading import ImageLoadingService
from varda.image_loading.export_image_dialog import ExportImageDialog
from varda.maingui import MainGUI
from varda.common.entities import VardaRaster
from varda.package_extraction import cleanExtractions, extractPackage
from varda.project_package import (
    capturePackage,
    formatBytes,
    planPackage,
    writePackage,
)
from varda.session import (
    BUNDLE_SUFFIX,
    SESSION_FILE_FILTER,
    captureSession,
    readSession,
    writeSession,
)
from varda.utilities.background import onMainThread, runInBackground
from varda.session_autosave import defaultAutosavePath
from varda.session_restore import RestoreReport, SessionRestorer

logger = logging.getLogger(__name__)

BUNDLE_FILE_FILTER = f"Packaged Varda project (*{BUNDLE_SUFFIX})"
OPEN_SESSION_FILE_FILTER = f"Varda sessions (*.varda *{BUNDLE_SUFFIX})"


def _appDataRoot() -> Path:
    return Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
    )


def cleanOldExtractions(
    images: Iterable[VardaRaster], keep: Path | None = None
) -> None:
    """Remove the extracted packages nothing uses: not the autosave's, not the
    open images', not ``keep``."""
    inUse = [i.filePath for i in images if i.filePath]
    try:
        cleanExtractions(
            _appDataRoot(), defaultAutosavePath(), inUse + ([keep] if keep else [])
        )
    except OSError as error:
        logger.warning("Cleaning old extracted packages failed: %s", error)


def exportImage(images: ProjectImages, mainGui: MainGUI) -> None:
    """Write one of the project's images to an ENVI or GeoTIFF file."""
    ExportImageDialog(list(images), parent=mainGui).open()


def saveSession(images: ProjectImages, mainGui: MainGUI) -> None:
    """Save the open images, workspaces and ROIs to a .varda file."""
    path, _ = QFileDialog.getSaveFileName(
        mainGui, "Save Session", "", SESSION_FILE_FILTER
    )
    if not path:
        return
    capture = captureSession(list(images), mainGui.childWindows)
    written = writeSession(capture.state, path)
    logger.info("Session saved to %s", written)
    if capture.unsavedImages:
        QMessageBox.warning(
            mainGui,
            "Some images were left out",
            "These images exist only in memory, so the session cannot refer to "
            "them. Export them first, or use Package Project to keep them "
            "inside a package:\n" + "\n".join(capture.unsavedImages),
        )


def packageProject(images: ProjectImages, mainGui: MainGUI) -> None:
    """Package the whole project into one .varda.zip that opens on any
    computer with Varda: the session, every image with its companion files,
    the files workspaces use, results held in memory and a manifest."""
    plan = planPackage(list(images), mainGui.childWindows)
    size = formatBytes(plan.totalBytes)
    notes = []
    if plan.isLarge:
        notes.append(
            f"The package will be about {size}: it may take a while to write, "
            "and to share (many services limit file sizes)."
        )
    if plan.missing:
        notes.append(
            "These files a workspace uses can't be found and will be left out:\n"
            + "\n".join(plan.missing)
        )
    if notes:
        answer = QMessageBox.warning(
            mainGui,
            "Package Project",
            "\n\n".join(notes) + "\n\nPackage the project anyway?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
    path, _ = QFileDialog.getSaveFileName(
        mainGui, f"Package Project (about {size})", "", BUNDLE_FILE_FILTER
    )
    if not path:
        return
    state = capturePackage(plan, list(images), mainGui.childWindows)
    progress = QProgressDialog("Packaging the project…", "", 0, 100, mainGui)
    progress.setWindowTitle("Package Project")
    progress.setCancelButton(None)
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)

    def report(percent: int, message: str) -> None:
        progress.setValue(percent)
        progress.setLabelText(message)

    def done(written: Path) -> None:
        progress.close()
        logger.info("Project packaged to %s", written)
        QMessageBox.information(
            mainGui,
            "Project packaged",
            f"Saved {written.name} ({formatBytes(written.stat().st_size)}). Anyone "
            "with Varda can open it with File → Open Session; its manifest lists "
            "every file with its checksum.",
        )

    def failed(error: Exception) -> None:
        progress.close()
        QMessageBox.critical(mainGui, "Cannot package the project", str(error))

    runInBackground(
        lambda: writePackage(plan, state, path, onMainThread(report)),
        done,
        failed,
        description="Packaging a project",
        owner=mainGui,
    )


def openSession(images: ProjectImages, mainGui: MainGUI) -> None:
    """Load a .varda session (or a self-contained .varda.zip) and reopen its
    workspaces, alongside whatever is already open."""
    path, _ = QFileDialog.getOpenFileName(
        mainGui, "Open Session", "", OPEN_SESSION_FILE_FILTER
    )
    if path:
        _restoreSession(Path(path), images, mainGui)


def restoreLastSession(images: ProjectImages, mainGui: MainGUI) -> None:
    """Reopen the autosaved session (written every few minutes and on quit)."""
    path = defaultAutosavePath()
    if not path.exists():
        QMessageBox.information(
            mainGui,
            "No autosaved session yet",
            f"Varda autosaves the session every few minutes to\n{path}\n"
            "once an image is open. Nothing has been saved yet.",
        )
        return
    _restoreSession(path, images, mainGui)


def _restoreSession(path: Path, images: ProjectImages, mainGui: MainGUI) -> None:
    try:
        if path.name.lower().endswith(".zip"):
            path = extractPackage(path, _appDataRoot())
            cleanOldExtractions(images, keep=path.parent)
        state = readSession(path)
    except (OSError, ValueError, KeyError) as error:
        QMessageBox.critical(mainGui, "Cannot open session", str(error))
        return

    def report(result: RestoreReport) -> None:
        if result.failed:
            QMessageBox.warning(
                mainGui,
                "Some images could not be loaded",
                "Workspaces using them were skipped:\n" + "\n".join(result.failed),
            )

    restorer = SessionRestorer(images, mainGui, parent=mainGui)
    restorer.sigFinished.connect(report)
    restorer.restore(state)


def importImage(images: ProjectImages) -> None:
    """Prompt for one or more image files and load them into the project."""
    ImageLoadingService.load_images(on_success_callback=images.append)


def importImagePaths(filePaths: list[str], images: ProjectImages) -> None:
    """Load the given image files into the project (e.g. files dropped on the window)."""
    ImageLoadingService.load_images(
        file_paths=filePaths, on_success_callback=images.append
    )


def exitApp() -> None:
    QApplication.instance().quit()


FILE_ACTIONS: list[Action] = [
    Action(
        id=IMPORT_IMAGE_ID,
        title="Import Image(s)…",
        icon=bundledIcon("folder-open"),
        callback=importImage,
        menus=[MenuRule(id=MenuId.FILE, group=MenuGroup.FILE_IO, order=1)],
        # Open is where users look for opening an image
        keybindings=[StandardKeyBinding.Open.to_keybinding_rule()],
    ),
    Action(
        id="varda.file.export_image",
        title="Export Image…",
        icon=bundledIcon("floppy-disk"),
        callback=exportImage,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=MenuId.FILE, group=MenuGroup.FILE_IO, order=2)],
    ),
    Action(
        id=OPEN_SESSION_ID,
        title="Open Session…",
        icon=bundledIcon("folder-tree"),
        callback=openSession,
        menus=[MenuRule(id=MenuId.FILE, group=MenuGroup.FILE_SESSION, order=1)],
        keybindings=[
            KeyBindingRule(primary=KeyMod.CtrlCmd | KeyMod.Shift | KeyCode.KeyO)
        ],
    ),
    Action(
        id="varda.file.save_session",
        title="Save Session…",
        icon=bundledIcon("bookmark"),
        callback=saveSession,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=MenuId.FILE, group=MenuGroup.FILE_SESSION, order=2)],
        keybindings=[StandardKeyBinding.Save.to_keybinding_rule()],
    ),
    Action(
        id="varda.file.package_project",
        title="Package Project…",
        tooltip=(
            "One zip with everything: the session, every image and its companion "
            "files, results held in memory, and a manifest of checksums, to open "
            "on any computer with Varda"
        ),
        callback=packageProject,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=MenuId.FILE, group=MenuGroup.FILE_SESSION, order=3)],
    ),
    Action(
        id=RESTORE_LAST_SESSION_ID,
        title="Restore Last Session",
        icon=bundledIcon("clock-rotate-left"),
        callback=restoreLastSession,
        menus=[MenuRule(id=MenuId.FILE, group=MenuGroup.FILE_SESSION, order=4)],
    ),
    Action(
        id="varda.file.exit",
        title="Exit",
        icon=bundledIcon("xmark"),
        callback=exitApp,
        menus=[MenuRule(id=MenuId.FILE, group=MenuGroup.FILE_EXIT, order=1)],
        keybindings=[StandardKeyBinding.Quit.to_keybinding_rule()],
    ),
]
