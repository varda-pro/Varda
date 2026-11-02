"""The workspaces sessions can save and restore and Duplicate Workspace can
copy. A new workspace implements ``Workspace`` (``workspaces/workspace.py``)
and is added to ``WORKSPACES``."""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableSequence, Sequence

from PyQt6.QtWidgets import QWidget

from varda.common.entities import VardaRaster
from varda.common.parameter import copyValues
from varda.rois.roi_json import roisFromJson, roisToJson
from varda.workspaces.crism_ratio.crism_ratio_workspace import CrismRatioWorkspace
from varda.workspaces.dual_image_workspace.dual_image_workspace import (
    DualImageWorkspace,
)
from varda.workspaces.find_data.find_data_workspace import FindDataWorkspace
from varda.workspaces.general_image_analysis import GeneralImageAnalysisWorkflow
from varda.workspaces.linked_images.linked_images_workspace import (
    LinkedImagesWorkspace,
)
from varda.workspaces.mosaic.mosaic_workspace import MosaicWorkspace
from varda.workspaces.spectral_hourglass.hourglass_workspace import (
    SpectralHourglassWorkspace,
)
from varda.workspaces.terrain.terrain_workspace import TerrainWorkspace
from varda.workspaces.workspace import Workspace

logger = logging.getLogger(__name__)

WORKSPACES: tuple[type[Workspace], ...] = (
    GeneralImageAnalysisWorkflow,
    DualImageWorkspace,
    SpectralHourglassWorkspace,
    MosaicWorkspace,
    FindDataWorkspace,
    TerrainWorkspace,
    LinkedImagesWorkspace,
    CrismRatioWorkspace,
)
_BY_KIND = {workspace.kind: workspace for workspace in WORKSPACES}


def registered(widget: object) -> Workspace | None:
    """``widget`` as a workspace sessions know, or None if it isn't one."""
    for workspace in WORKSPACES:
        if isinstance(widget, workspace):
            return widget
    return None


def buildWorkspace(
    kind: str,
    images: Sequence[VardaRaster],
    projectImages: MutableSequence[VardaRaster],
    state: Mapping | None = None,
    rois: dict | None = None,
) -> QWidget | None:
    """A new workspace of ``kind`` on ``images``, offered the (live) project
    images, with its own ``state`` and its ROIs (``roisToJson``) applied."""
    return _asWidget(_build(kind, images, projectImages, state, rois))


def duplicateWorkspace(
    widget: object, projectImages: MutableSequence[VardaRaster]
) -> QWidget | None:
    """A new workspace of the same kind on the same images, with its own
    copies of the ROIs, display settings and workspace-specific state."""
    source = registered(widget)
    if source is None:
        return None
    rois = source.roiCollection
    copy = _build(
        source.kind,
        source.sessionImages(),
        projectImages,
        source.sessionState(),
        roisToJson(rois) if rois is not None else None,
    )
    if copy is None:
        return None
    for original, copied in zip(source.displayRenderers(), copy.displayRenderers()):
        copyValues(original.settings, copied.settings)
    return _asWidget(copy)


def _build(
    kind: str,
    images: Sequence[VardaRaster],
    projectImages: MutableSequence[VardaRaster],
    state: Mapping | None,
    rois: dict | None,
) -> Workspace | None:
    workspaceClass = _BY_KIND.get(kind)
    if workspaceClass is None:
        logger.warning("Unknown workspace kind %r; skipped", kind)
        return None
    workspace = workspaceClass.fromSession(images, projectImages, dict(state or {}))
    if workspace is not None and rois and workspace.roiCollection is not None:
        roisFromJson(rois, workspace.roiCollection)
    return workspace


def _asWidget(workspace: Workspace | None) -> QWidget | None:
    assert workspace is None or isinstance(workspace, QWidget)  # every one is
    return workspace
