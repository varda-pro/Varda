"""The kinds of workspace that can be saved in a session, restored from one,
or duplicated: how to recognize each, rebuild it, and reach its display."""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableSequence, Sequence

from PyQt6.QtWidgets import QWidget

from varda.common.entities import VardaRaster
from varda.common.parameter import copyValues
from varda.image_rendering.image_renderer import ImageRenderer
from varda.rois.roi_json import roisFromJson, roisToJson
from varda.workspaces.crism_ratio.crism_ratio_workspace import CrismRatioWorkspace
from varda.workspaces.dual_image_workspace.dual_image_workspace import (
    DualImageWorkspace,
    DualImageWorkspaceConfig,
)
from varda.workspaces.general_image_analysis import (
    GeneralImageAnalysisConfig,
    GeneralImageAnalysisWorkflow,
)
from varda.workspaces.find_data.find_data_workspace import FindDataWorkspace
from varda.workspaces.terrain.terrain_workspace import TerrainWorkspace
from varda.workspaces.linked_images.linked_images_workspace import (
    LinkedImagesWorkspace,
)
from varda.workspaces.linked_images.linking import Link
from varda.workspaces.mosaic.mosaic_workspace import MosaicWorkspace
from varda.workspaces.spectral_hourglass.hourglass_workspace import (
    SpectralHourglassWorkspace,
)

logger = logging.getLogger(__name__)


def describeWorkspace(workspace: object) -> tuple[str, list[VardaRaster]] | None:
    """The workspace's kind and the images it shows, or None if it is not one
    of the kinds sessions know."""
    if isinstance(workspace, GeneralImageAnalysisWorkflow):
        return "general", [workspace.config.image.get()]
    if isinstance(workspace, DualImageWorkspace):
        return "dual", [workspace.image1, workspace.image2]
    if isinstance(workspace, SpectralHourglassWorkspace):
        return "hourglass", [workspace.model.image]
    if isinstance(workspace, MosaicWorkspace):
        return "mosaic", list(workspace.images)
    if isinstance(workspace, FindDataWorkspace):
        return "find_data", []
    if isinstance(workspace, TerrainWorkspace):
        return "terrain", workspace.sessionImages()
    if isinstance(workspace, LinkedImagesWorkspace):
        return "linked", list(workspace.images)
    if isinstance(workspace, CrismRatioWorkspace):
        return "crism_ratio", [workspace.browse, workspace.data]
    return None


def buildWorkspace(
    kind: str,
    images: Sequence[VardaRaster],
    projectImages: MutableSequence[VardaRaster],
    extra: Mapping | None = None,
) -> QWidget | None:
    """A new workspace of ``kind`` on ``images``, offered the (live) project
    images, with its workspace-specific state ``extra`` applied."""
    if kind == "general":
        config = GeneralImageAnalysisConfig(projectImages)
        config.image.set(images[0])
        workspace = GeneralImageAnalysisWorkflow(config)
        workspace.restoreSessionState(dict(extra or {}))
        return workspace
    if kind == "dual" and len(images) == 2:
        config = DualImageWorkspaceConfig(projectImages)
        config.image1Param.set(images[0])
        config.image2Param.set(images[1])
        workspace = DualImageWorkspace(config)
        workspace.restoreSessionState(dict(extra or {}))
        return workspace
    if kind == "hourglass":
        workspace = SpectralHourglassWorkspace(images[0], projectImages)
        workspace.restoreSessionState(dict(extra or {}))
        return workspace
    if kind == "mosaic":
        workspace = MosaicWorkspace(images, projectImages)
        workspace.restoreSessionState(dict(extra or {}))
        return workspace
    if kind == "find_data":
        workspace = FindDataWorkspace(projectImages)
        workspace.restoreSessionState(dict(extra or {}))
        return workspace
    if kind == "terrain" and images:
        workspace = TerrainWorkspace(projectImages, images[0])
        workspace.restoreSessionState(dict(extra or {}), images[1:])
        return workspace
    if kind == "linked" and len(images) >= 2:
        state = dict(extra or {})
        workspace = LinkedImagesWorkspace(
            images, projectImages, Link[state.get("link", Link.PIXEL.name)]
        )
        workspace.restoreSessionState(state)
        return workspace
    if kind == "crism_ratio" and len(images) != 2:
        logger.warning("CRISM Ratio workspace needs its browse and I/F images; skipped")
        return None
    if kind == "crism_ratio":
        state = dict(extra or {})
        workspace = CrismRatioWorkspace(
            images[0],
            images[1],
            projectImages,
            Link[state.get("link", Link.PIXEL.name)],
        )
        workspace.restoreSessionState(state)
        return workspace
    logger.warning("Unknown workspace kind %r; skipped", kind)
    return None


def displayRenderers(workspace: object) -> list[ImageRenderer]:
    """The renderers whose band and stretch settings make up the workspace's
    display, in a fixed order per kind."""
    if isinstance(workspace, GeneralImageAnalysisWorkflow):
        return [workspace.imageRenderer]
    if isinstance(workspace, DualImageWorkspace):
        return [workspace.primaryRenderer, workspace.secondaryRenderer]
    if isinstance(workspace, LinkedImagesWorkspace):
        return list(workspace.renderers)
    if isinstance(workspace, CrismRatioWorkspace):
        return [workspace.browseRenderer, workspace.dataRenderer]
    return []


def duplicateWorkspace(
    workspace: object, projectImages: MutableSequence[VardaRaster]
) -> QWidget | None:
    """A new workspace of the same kind on the same images, with its own
    copies of the ROIs, display settings and workspace-specific state."""
    described = describeWorkspace(workspace)
    if described is None:
        return None
    kind, images = described
    sessionState = getattr(workspace, "sessionState", None)
    copy = buildWorkspace(
        kind, images, projectImages, sessionState() if callable(sessionState) else {}
    )
    if copy is None:
        return None
    rois = getattr(workspace, "roiCollection", None)
    copiedRois = getattr(copy, "roiCollection", None)
    if rois is not None and copiedRois is not None:
        roisFromJson(roisToJson(rois), copiedRois)
    for source, target in zip(displayRenderers(workspace), displayRenderers(copy)):
        copyValues(source.settings, target.settings)
    return copy
