"""Rebuild a saved session: reload its images one after another (the loading
service works asynchronously), then reopen its workspaces with their ROIs."""

from __future__ import annotations

import logging
from collections.abc import Callable

import attrs
from PyQt6.QtCore import QObject, pyqtSignal

from varda.common.di_types import ProjectImages
from varda.common.entities import VardaRaster
from varda.common.provenance import CRS_FIX_KEY
from varda.image_loading import ImageLoadingService
from varda.image_loading.crism_geometry import GEOMETRY_KEYS, setGeometryOverride
from varda.image_loading.crs_check import checkImageCrs
from varda.rois.roi_json import roisFromJson
from varda.session import SessionState
from varda.workspaces.workspace_kinds import buildWorkspace

logger = logging.getLogger(__name__)
_NO_CRS = object()  # no "crs" override

# (path, onSuccess(image), onFailure(message)) — the loading service's shape
ImageLoader = Callable[
    [str, Callable[[VardaRaster], None], Callable[[str], None]], None
]


@attrs.frozen
class RestoreReport:
    loaded: tuple[str, ...]  # paths loaded for the session
    failed: tuple[str, ...]  # paths that could not be loaded
    workspaces: int  # workspaces reopened


class SessionRestorer(QObject):
    sigFinished = pyqtSignal(object)  # RestoreReport

    def __init__(
        self,
        images: ProjectImages,
        mainGui,
        loadImage: ImageLoader = ImageLoadingService.load_image_data,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._images = images
        self._mainGui = mainGui
        self._loadImage = loadImage
        self._state = SessionState((), ())
        self._pending: list[str] = []
        self._loaded: list[str] = []
        self._failed: list[str] = []

    def restore(self, state: SessionState) -> None:
        self._state = state
        # images already in the project (matched by path) are not loaded twice
        self._pending = [path for path in state.images if self._byPath(path) is None]
        self._loaded, self._failed = [], []
        self._loadNext()

    def _byPath(self, path: str) -> VardaRaster | None:
        return next((image for image in self._images if image.filePath == path), None)

    def _loadNext(self) -> None:
        if not self._pending:
            self._openWorkspaces()
            return
        path = self._pending.pop(0)

        def onSuccess(image: VardaRaster) -> None:
            self._images.append(image)
            self._loaded.append(path)
            self._loadNext()

        def onFailure(message: str) -> None:
            logger.warning("Session image %s could not be loaded: %s", path, message)
            self._failed.append(path)
            self._loadNext()

        self._loadImage(path, onSuccess, onFailure)

    def _openWorkspaces(self) -> None:
        for path, entry in self._state.metadata.items():  # edited in Varda
            image = self._byPath(path)
            if image is not None:
                _restoreEdits(image, entry)
                for need, key in GEOMETRY_KEYS.items():
                    if entry.get(key):
                        setGeometryOverride(image, need, entry[key])
        count = 0
        for workspace in self._state.workspaces:
            images = [self._byPath(path) for path in workspace.images]
            if any(image is None for image in images):
                continue
            widget = buildWorkspace(
                workspace.kind,
                [image for image in images if image],
                self._images,
                workspace.extra,
            )
            if widget is None:
                continue
            rois = getattr(widget, "roiCollection", None)
            if rois is not None and workspace.rois:
                roisFromJson(workspace.rois, rois)
            self._mainGui.addTab(widget)
            count += 1
        self.sigFinished.emit(
            RestoreReport(tuple(self._loaded), tuple(self._failed), count)
        )


def _restoreEdits(image: VardaRaster, entry: dict) -> None:
    """Put the session's edits (``entry``) back on ``image``, with its
    ``crsFix`` record, and check its CRS again when the session chose
    another than load did. A session CRS this PROJ can't read gives way to
    the one chosen at load (and its record)."""
    loaded = image.metadataOverrides.get("crs", _NO_CRS)
    overrides = _restoredOverrides(image, entry.get("overrides", {}))
    try:
        image.setMetadataOverrides(overrides, entry.get("name"))
    except ValueError as error:
        logger.warning("Session edits of %s: %s", image.filePath, error)
        overrides = {k: v for k, v in overrides.items() if k != "crs"}
        if loaded is not _NO_CRS:
            overrides["crs"] = loaded
        try:
            image.setMetadataOverrides(overrides, entry.get("name"))
        except ValueError as again:  # band counts that don't match, say
            logger.warning(
                "Session edits of %s not restored: %s", image.filePath, again
            )
            return
    else:
        record = entry.get(CRS_FIX_KEY)
        if isinstance(record, dict):  # kept by the check when it still applies
            image.dataSource.extraMetadata[CRS_FIX_KEY] = dict(record)
            loaded = _NO_CRS  # check again, below
    if overrides.get("crs", _NO_CRS) != loaded or image.crsHealth is None:
        checkImageCrs(image)


def _restoredOverrides(image: VardaRaster, saved: dict) -> dict:
    """The session's edits in place of the image's. A session that doesn't
    say which CRS (one saved before Varda checked CRSs) keeps the CRS chosen
    at load (the automatic fix); one that does wins, None (the file's own)
    included."""
    current = image.metadataOverrides
    if "crs" in saved or "crs" not in current:
        return dict(saved)
    return {**saved, "crs": current["crs"]}
