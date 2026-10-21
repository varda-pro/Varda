"""The Processing menu: one entry per analysis, grouped into category submenus.

Entries are generated from the analysis registry, so adding an analysis is
just registering it with a ``category``. Analyses whose optional dependency
is missing stay visible but disabled, saying what they need, unless Varda
can install it (PyTorch): those stay enabled and offer the install.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from app_model.types import Action, MenuRule, SubmenuItem
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from varda._actions._menu_ids import MenuId
from varda.analysis.accuracy_assessment import accuracyRecord
from varda.accuracy_report_dialog import AccuracyReportDialog
from varda.analysis.analysis import SUBMENU, Analysis, categoryLevels
from varda.analysis.dialog import RunAnalysisDialog
from varda.analysis.eigenvalue_dialog import EigenvaluePlotDialog
from varda.analysis.fatt_dialog import FattDialog
from varda.analysis.fatt_map import FattMapAnalysis
from varda.analysis.registry import ANALYSES
from varda.analysis.ratio_cube_analysis import CATEGORY as CRISM_CATEGORY
from varda.analysis.runner import AnalysisRunner
from varda.analysis.transforms import TRANSFORM_METADATA_KEY, InverseTransformAnalysis
from varda.common.di_types import ProjectImages
from varda.common.entities import VardaRaster
from varda.crism import ProcessResult
from varda.crism_ui.calibration_dialog import CalibrationDialog
from varda.crism_ui.process_dialog import ProcessCrismDialog
from varda.context_keys import EXPR_HAS_IMAGES, EXPR_NEVER
from varda.image_loading import ImageLoadingService
from varda.library_manager.manager_window import openLibraryManager
from varda.maingui import MainGUI
from varda.optional_features.optional_features_dialog import openOptionalFeatures
from varda.workspaces.mosaic.cam2map_dialog import Cam2mapDialog
from varda.workspaces.terrain.terrain_workspace import SHAPE_SURFACE, TerrainWorkspace
from varda.workspaces.spectral_hourglass.new_hourglass_dialog import (
    NewSpectralHourglassDialog,
)


def openRunAnalysisDialog(
    images: ProjectImages, mainGui: MainGUI, *, analysis: type[Analysis]
) -> None:
    """Show the dialog for one analysis; the run lands in the project images."""
    if not analysis.isAvailable() and analysis.installable:
        offerInstall(images, mainGui, analysis)
        return
    if not analysis.isAvailable():
        QMessageBox.information(
            mainGui,
            f"{analysis.name} is unavailable",
            f"{analysis.name} needs {analysis.requirement}; see Help → Documentation.",
        )
        return
    runner = AnalysisRunner(images, parent=mainGui)
    # Analyses that use ROIs get the current workspace's collection (the
    # workspaces assume their images are co-registered with it).
    rois = getattr(mainGui.currentWorkspace(), "roiCollection", None)
    # A result that carries an accuracy report (Accuracy Assessment, or a
    # classifier's hold-out validation) shows it
    runner.sigFinished.connect(
        lambda result: showAccuracyReport(images, mainGui, result)
    )
    RunAnalysisDialog(
        list(images), [analysis], analysis=analysis, rois=rois, parent=mainGui
    ).connectOnRun(runner.run).open()


def showAccuracyReport(
    images: ProjectImages, mainGui: MainGUI, result: VardaRaster
) -> None:
    if accuracyRecord(result) is not None:
        openAccuracyReport(images, mainGui, result)


def openLatestAccuracyReport(images: ProjectImages, mainGui: MainGUI) -> None:
    latest = next(
        (i for i in reversed(list(images)) if accuracyRecord(i) is not None), None
    )
    openAccuracyReport(images, mainGui, latest)


def openAccuracyReport(
    images: ProjectImages, mainGui: MainGUI, image: VardaRaster | None
) -> None:
    """The accuracy report of ``image``; non-modal, so the error map or the
    class map can be inspected alongside."""
    dialog = AccuracyReportDialog(list(images), image=image, parent=mainGui)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.show()


def offerInstall(
    images: ProjectImages, mainGui: MainGUI, analysis: type[Analysis]
) -> None:
    """Open Optional Features for the analysis's requirement; once it is
    installed and the dialog closes, carry on to the analysis."""
    dialog = openOptionalFeatures(
        mainGui,
        reason=f"{analysis.name} needs {analysis.requirement}, which isn't installed yet.",
    )
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

    def carryOn() -> None:
        if analysis.isAvailable():
            openRunAnalysisDialog(images, mainGui, analysis=analysis)

    dialog.finished.connect(carryOn)
    dialog.open()


def openOptionalFeaturesDialog(mainGui: MainGUI) -> None:
    dialog = openOptionalFeatures(mainGui)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.open()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def categoryMenuId(root: str, category: str) -> str:
    return f"{root}/{_slug(category)}"


def submenuItem(root: str, level: str, order: int) -> tuple[str, SubmenuItem]:
    """``level``'s submenu, in its parent's menu (``root`` at the top)."""
    parent = level.rpartition(SUBMENU)[0]
    return (
        categoryMenuId(root, parent) if parent else root,
        SubmenuItem(
            submenu=categoryMenuId(root, level),
            title=level.rpartition(SUBMENU)[2],
            order=order,
        ),
    )


def processingSubmenus(
    root: str, analyses: Sequence[type[Analysis]]
) -> list[tuple[str, SubmenuItem]]:
    """One submenu per category, nested by its path ("Filtering/Spatial"),
    in first-appearance order."""
    levels = dict.fromkeys(
        level for analysis in analyses for level in categoryLevels(analysis.category)
    )
    return [submenuItem(root, level, i) for i, level in enumerate(levels)]


def processingActions(
    root: str,
    idPrefix: str,
    analyses: Sequence[type[Analysis]],
    makeCallback: Callable[[type[Analysis]], Callable],
) -> list[Action]:
    """One action per analysis, filed under its category's submenu."""
    actions = []
    for analysis in analyses:
        available = analysis.isAvailable()
        title = (
            f"{analysis.name.rstrip(' .…')}…"  # one ellipsis, even if the name has one
        )
        tooltip = analysis.description
        if analysis.installable:
            tooltip += f" Varda downloads {analysis.requirement} the first time."
        elif not available:
            title += f" (needs {analysis.requirement})"
        actions.append(
            Action(
                id=f"{idPrefix}.{analysis.analysisId}",
                title=title,
                tooltip=tooltip,
                callback=makeCallback(analysis),
                enablement=(
                    EXPR_HAS_IMAGES if available or analysis.installable else EXPR_NEVER
                ),
                menus=[MenuRule(id=categoryMenuId(root, analysis.category))],
            )
        )
    return actions


def _menuCallback(analysis: type[Analysis]) -> Callable:
    def run(images: ProjectImages, mainGui: MainGUI) -> None:
        openRunAnalysisDialog(images, mainGui, analysis=analysis)

    return run


def _latestTransformResult(images: ProjectImages) -> VardaRaster | None:
    return next(
        (
            image
            for image in reversed(list(images))
            if TRANSFORM_METADATA_KEY in image.extraMetadata
        ),
        None,
    )


def openEigenvaluePlot(images: ProjectImages, mainGui: MainGUI) -> None:
    """Inspect a transform result's eigenvalues; non-modal so the component
    images can be browsed alongside."""
    dialog = EigenvaluePlotDialog(
        list(images), image=_latestTransformResult(images), parent=mainGui
    )
    dialog.sigInverseRequested.connect(
        lambda image, count: openInverseTransformDialog(images, mainGui, image, count)
    )
    dialog.show()


def openInverseTransformDialog(
    images: ProjectImages, mainGui: MainGUI, image: VardaRaster, components: int
) -> None:
    """The Inverse Transform dialog for ``image`` with the component count preset."""
    runner = AnalysisRunner(images, parent=mainGui)
    dialog = RunAnalysisDialog(
        list(images),
        [InverseTransformAnalysis],
        image=image,
        analysis=InverseTransformAnalysis,
        parent=mainGui,
    )
    analysis = dialog.selectedAnalysis()
    assert isinstance(analysis, InverseTransformAnalysis)
    analysis.components.set(components)
    dialog.connectOnRun(runner.run).open()


def openFatt(images: ProjectImages, mainGui: MainGUI) -> None:
    """The FATT window; non-modal so the image can be inspected alongside.
    ROI classes of the current workspace can be the spectra to analyze."""
    rois = getattr(mainGui.currentWorkspace(), "roiCollection", None)
    dialog = FattDialog(list(images), rois=rois, parent=mainGui)
    dialog.sigMapRequested.connect(
        lambda image, analysis: openFattMapDialog(images, mainGui, image, analysis)
    )
    dialog.show()


def openFattMapDialog(
    images: ProjectImages,
    mainGui: MainGUI,
    image: VardaRaster,
    analysis: FattMapAnalysis,
) -> None:
    """The run dialog for a FATT detection map set up in the FATT window."""
    runner = AnalysisRunner(images, parent=mainGui)
    RunAnalysisDialog(
        list(images),
        [FattMapAnalysis],
        image=image,
        analysis=FattMapAnalysis,
        instance=analysis,
        parent=mainGui,
    ).connectOnRun(runner.run).open()


def openCam2map(images: ProjectImages, mainGui: MainGUI) -> None:
    """Map-project a raw ISIS cube; the result joins the project."""
    dialog = Cam2mapDialog(parent=mainGui)
    dialog.sigProjected.connect(
        lambda path: ImageLoadingService.load_images(
            file_paths=[path], on_success_callback=images.append
        )
    )
    dialog.show()


def openShapeGeophysics(images: ProjectImages, mainGui: MainGUI) -> None:
    """A 3D Terrain workspace on a shape model, then Shape Geophysics."""
    workspace = TerrainWorkspace(images)
    mainGui.addTab(workspace)
    workspace.surfaceBox.setCurrentText(SHAPE_SURFACE)
    assert workspace.shapePanel is not None
    workspace.shapePanel.openGeophysics()


def openSpectralLibraries(mainGui: MainGUI) -> None:
    """The Spectral Library Manager; non-modal, one at a time."""
    openLibraryManager(mainGui)


def openCrismProcess(images: ProjectImages, mainGui: MainGUI) -> None:
    """Process CRISM observations; each map-projected product joins the project."""
    dialog = ProcessCrismDialog(parent=mainGui)

    def load(result: ProcessResult) -> None:
        if result.mapProjected is not None:
            ImageLoadingService.load_images(
                file_paths=[str(result.mapProjected)],
                on_success_callback=images.append,
            )

    dialog.sigProcessed.connect(load)
    dialog.show()


def openCrismCalibration(mainGui: MainGUI) -> None:
    CalibrationDialog(parent=mainGui).show()


def openSpectralHourglass(images: ProjectImages, mainGui: MainGUI) -> None:
    NewSpectralHourglassDialog(images, parent=mainGui).connectOnAccept(
        mainGui.addTab
    ).open()


EIGENVALUE_PLOT_ACTION_ID = "varda.processing.eigenvalue_plot"
ACCURACY_REPORT_ACTION_ID = "varda.processing.accuracy_report"
SPECTRAL_HOURGLASS_ACTION_ID = "varda.processing.spectral_hourglass"
FATT_ACTION_ID = "varda.processing.fatt"
CAM2MAP_ACTION_ID = "varda.processing.cam2map"
OPTIONAL_FEATURES_ACTION_ID = "varda.processing.optional_features"
SPECTRAL_LIBRARIES_ACTION_ID = "varda.processing.spectral_libraries"
SHAPE_GEOPHYSICS_ACTION_ID = "varda.processing.shape_geophysics"
SHAPE_MODELS = "Shape Models"
CRISM_PROCESS_ACTION_ID = "varda.processing.crism_process"
CRISM_CALIBRATION_ACTION_ID = "varda.processing.crism_calibration"
CRISM_MENU = categoryMenuId(MenuId.PROCESSING, CRISM_CATEGORY)
# Processing a TRR3 comes first, above the analyses of a processed cube
CRISM_PROCESSING_GROUP = "0_processing"

# The CRISM submenu comes from the Ratio Cube analysis's category (CRISM_CATEGORY)
PROCESSING_SUBMENUS = processingSubmenus(MenuId.PROCESSING, ANALYSES) + [
    submenuItem(MenuId.PROCESSING, SHAPE_MODELS, len(ANALYSES))
]
PROCESSING_ACTIONS = processingActions(
    MenuId.PROCESSING, "varda.processing", ANALYSES, _menuCallback
) + [
    Action(
        id=ACCURACY_REPORT_ACTION_ID,
        title="Accuracy Report…",
        tooltip=(
            "Show, and save as CSV, the confusion matrix and accuracy of an "
            "Accuracy Assessment result or of a classifier's hold-out validation"
        ),
        callback=openLatestAccuracyReport,
        enablement=EXPR_HAS_IMAGES,
        menus=[
            MenuRule(id=categoryMenuId(MenuId.PROCESSING, "Classification"), order=99)
        ],
    ),
    Action(
        id=EIGENVALUE_PLOT_ACTION_ID,
        title="Eigenvalue Plot…",
        tooltip=(
            "Plot and tabulate a PCA / MNF / ICA result's eigenvalues to decide "
            "how many components to keep"
        ),
        callback=openEigenvaluePlot,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=categoryMenuId(MenuId.PROCESSING, "Transforms"), order=99)],
    ),
    Action(
        id=SPECTRAL_HOURGLASS_ACTION_ID,
        title="Spectral Hourglass Workspace…",
        tooltip=(
            "The interactive endmember workflow: MNF, pixel purity, n-D "
            "visualizer, library identification and mapping"
        ),
        callback=openSpectralHourglass,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=categoryMenuId(MenuId.PROCESSING, "Endmembers"), order=0)],
    ),
    Action(
        id=FATT_ACTION_ID,
        title="Factor Analysis Target Transformation…",
        tooltip=(
            "Which library spectra the image's factor space reproduces (FATT), "
            "and where they fit, by dynamic aperture factor analysis (DAFA) and "
            "target transformation (TT)"
        ),
        callback=openFatt,
        enablement=EXPR_HAS_IMAGES,
        menus=[MenuRule(id=categoryMenuId(MenuId.PROCESSING, "Endmembers"), order=1)],
    ),
    Action(
        id=CAM2MAP_ACTION_ID,
        title="Map-Project with ISIS (cam2map)…",
        tooltip=(
            "Map-project a raw camera cube with ISIS cam2map, so it can be "
            "mosaicked (needs ISIS; see Optional Features)"
        ),
        callback=openCam2map,
        menus=[MenuRule(id=categoryMenuId(MenuId.PROCESSING, "Geometry"), order=99)],
    ),
    Action(
        id=SPECTRAL_LIBRARIES_ACTION_ID,
        title="Spectral Libraries…",
        tooltip=(
            "Browse the bundled spectral libraries, build your own from image "
            "spectra, load ENVI/CSV/USGS libraries, and export them"
        ),
        callback=openSpectralLibraries,
        menus=[MenuRule(id=MenuId.PROCESSING, group="8_libraries")],
    ),
    Action(
        id=SHAPE_GEOPHYSICS_ACTION_ID,
        title="Shape Geophysics…",
        tooltip=(
            "Gravity, slope, geopotential elevation and tilt per plate of a "
            "small body's shape model, with bounds from its density's uncertainty"
        ),
        callback=openShapeGeophysics,
        menus=[MenuRule(id=categoryMenuId(MenuId.PROCESSING, SHAPE_MODELS))],
    ),
    Action(
        id=CRISM_PROCESS_ACTION_ID,
        title="Process CRISM Observation…",
        tooltip=(
            "Turn CRISM TRR3 (targeted reduced data record) observations into "
            "map-projected I/F with the atmosphere, spikes and spectral smile "
            "corrected"
        ),
        callback=openCrismProcess,
        menus=[MenuRule(id=CRISM_MENU, group=CRISM_PROCESSING_GROUP, order=0)],
    ),
    Action(
        id=CRISM_CALIBRATION_ACTION_ID,
        title="Get CRISM Calibration Files…",
        tooltip="See which calibration files an observation needs, and fetch the missing ones",
        callback=openCrismCalibration,
        menus=[MenuRule(id=CRISM_MENU, group=CRISM_PROCESSING_GROUP, order=1)],
    ),
    Action(
        id=OPTIONAL_FEATURES_ACTION_ID,
        title="Optional Features…",
        tooltip=(
            "Install, update or remove PyTorch for the CNN and ViT classifiers, "
            "and set up the Ames Stereo Pipeline for mosaicking"
        ),
        callback=openOptionalFeaturesDialog,
        menus=[MenuRule(id=MenuId.PROCESSING, group="9_setup")],
    ),
]
