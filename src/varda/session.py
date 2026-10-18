"""Sessions: the open images, the workspaces showing them, and their ROIs, as a
JSON ``.varda`` file.

Only images that live in a file can be saved (the session stores paths, not
pixels); in-memory results such as analysis outputs have to be exported first
and are reported back to the caller instead.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

import attrs

from varda.common.entities import VardaRaster
from varda.common.provenance import CRISM_GEOMETRY_KEYS
from varda.rois.roi_json import roisToJson
from varda.workspaces.workspace_kinds import describeWorkspace

SESSION_SUFFIX = ".varda"
SESSION_FILE_FILTER = "Varda session (*.varda)"
_FORMAT = "varda-session"
_VERSION = 1  # bump when the format changes, and add a MIGRATIONS entry

# version n -> a function turning an n document into an n + 1 document
MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


@attrs.frozen
class WorkspaceState:
    kind: str  # "general", "dual" or "hourglass"
    images: tuple[str, ...] = attrs.field(
        converter=tuple
    )  # (image,) or (primary, secondary)
    rois: dict = attrs.field(factory=dict)  # GeoJSON FeatureCollection, see roisToJson
    extra: dict = attrs.field(factory=dict)  # workspace-specific state (JSON-able)


@attrs.frozen
class SessionState:
    images: tuple[str, ...] = attrs.field(converter=tuple)  # file paths in list order
    workspaces: tuple[WorkspaceState, ...] = attrs.field(converter=tuple)
    # path -> {"name": ..., "overrides": {...}, "crismColumnsFile"?: path,
    # "crismIncidenceFile"?: path}: metadata edited in Varda, and the CRISM
    # geometry files chosen by hand
    metadata: dict = attrs.field(factory=dict)


@attrs.frozen
class SessionCapture:
    state: SessionState
    # names of images that exist only in memory and were left out
    unsavedImages: tuple[str, ...]


def captureSession(
    images: Sequence[VardaRaster],
    workspaces: Iterable[object],
    writtenPaths: Mapping[int, str] | None = None,
) -> SessionCapture:
    """Describe the project: every file-backed image and every workspace whose
    images are all file-backed. ``writtenPaths`` (id(image) -> path) gives
    files just written for images that existed only in memory."""
    pathOf: dict[int, str] = {}
    unsaved = []
    for image in images:
        path = image.filePath or (writtenPaths or {}).get(id(image))
        if path:
            pathOf[id(image)] = path
        else:
            unsaved.append(image.name)

    states = []
    for workspace in workspaces:
        described = describeWorkspace(workspace)
        if described is None:
            continue
        kind, shown = described
        if any(id(image) not in pathOf for image in shown):
            continue
        rois = getattr(workspace, "roiCollection", None)
        sessionState = getattr(workspace, "sessionState", None)
        states.append(
            WorkspaceState(
                kind,
                tuple(pathOf[id(image)] for image in shown),
                roisToJson(rois) if rois is not None else {},
                sessionState() if callable(sessionState) else {},
            )
        )
    edited = {
        pathOf[id(image)]: _imageMetadata(image)
        for image in images
        if id(image) in pathOf and (image.metadataOverrides or _geometryFiles(image))
    }
    return SessionCapture(
        SessionState(tuple(pathOf.values()), tuple(states), edited), tuple(unsaved)
    )


def _imageMetadata(image: VardaRaster) -> dict:
    entry = {"name": image.name, "overrides": image.metadataOverrides}
    return {**entry, **_geometryFiles(image)}


def _geometryFiles(image: VardaRaster) -> dict[str, str]:
    """The CRISM geometry files chosen by hand for the image, by key."""
    return {
        key: str(image.extraMetadata[key])
        for key in CRISM_GEOMETRY_KEYS
        if image.extraMetadata.get(key)
    }


def writeSession(
    state: SessionState, path: str | Path, relativeTo: str | Path | None = None
) -> Path:
    """Write the session as JSON; the ``.varda`` suffix is added if missing.
    Image paths inside ``relativeTo`` are stored relative to the session file,
    so the folder (or bundle) can be moved."""
    target = Path(path)
    if target.suffix != SESSION_SUFFIX:
        target = target.with_suffix(SESSION_SUFFIX)

    def stored(imagePath: str) -> str:
        if relativeTo is None:
            return imagePath
        try:
            return (
                Path(imagePath)
                .resolve()
                .relative_to(Path(target).resolve().parent)
                .as_posix()
            )
        except ValueError:
            return imagePath  # outside: keep it absolute

    workspaces = []
    for workspace in state.workspaces:
        entry = attrs.asdict(workspace)
        entry["images"] = [stored(p) for p in workspace.images]
        workspaces.append(entry)
    document = {
        "format": _FORMAT,
        "version": _VERSION,
        "images": [stored(p) for p in state.images],
        "workspaces": workspaces,
        "metadata": {
            stored(p): _withPath(entry, stored) for p, entry in state.metadata.items()
        },
    }
    target.write_text(json.dumps(document, indent=2))
    return target


def readSession(path: str | Path) -> SessionState:
    document = json.loads(Path(path).read_text())
    if not isinstance(document, dict) or document.get("format") != _FORMAT:
        raise ValueError(f"{path} is not a Varda session file.")
    document = _upgrade(document, path)
    folder = Path(path).resolve().parent

    def resolved(imagePath: str) -> str:
        """Relative paths are relative to the session file."""
        return imagePath if Path(imagePath).is_absolute() else str(folder / imagePath)

    def resolvedExtra(extra: dict) -> dict:
        resolvedState = resolvedFiles(extra)
        assert isinstance(resolvedState, dict)
        return resolvedState

    def resolvedFiles(value: object) -> object:
        """A workspace's state with relative ``"path"`` entries (files a
        package carries) resolved like the images."""
        if isinstance(value, dict):
            return {
                key: (
                    resolved(item)
                    if key == "path" and isinstance(item, str)
                    else resolvedFiles(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [resolvedFiles(item) for item in value]
        return value

    return SessionState(
        tuple(resolved(p) for p in document["images"]),
        tuple(
            WorkspaceState(
                entry["kind"],
                tuple(resolved(p) for p in entry["images"]),
                entry.get("rois", {}),
                resolvedExtra(entry.get("extra", {})),
            )
            for entry in document["workspaces"]
        ),
        {
            resolved(p): _withPath(entry, resolved)
            for p, entry in document.get("metadata", {}).items()
        },
    )


def _withPath(entry: dict, convert: Callable[[str], str]) -> dict:
    """An image's metadata entry with its geometry files' paths converted
    like the images' (relative to the session file when stored)."""
    return {
        **entry,
        **{key: convert(entry[key]) for key in CRISM_GEOMETRY_KEYS if entry.get(key)},
    }


def _upgrade(document: dict, path: str | Path) -> dict:
    """Bring a session document up to the current format, or refuse one
    written by a newer Varda."""
    version = int(document.get("version", 1))
    if version > _VERSION:
        raise ValueError(
            f"{path} was saved by a newer version of Varda (session format "
            f"{version}; this one reads up to {_VERSION}). Update Varda to open it."
        )
    while version < _VERSION:
        document = MIGRATIONS[version](document)
        version += 1
    return document


BUNDLE_SUFFIX = ".varda.zip"
BUNDLE_SESSION_NAME = "session.varda"
BUNDLE_DATA_FOLDER = "data"


def openSessionBundle(bundle: str | Path, folder: str | Path) -> Path:
    """Extract a bundle into ``folder``; returns the session file to restore."""
    target = Path(folder)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle) as archive:
        for name in archive.namelist():  # refuse paths that would escape the folder
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError(f"{Path(bundle).name} contains an unsafe path: {name}")
        archive.extractall(target)
    return target / BUNDLE_SESSION_NAME
