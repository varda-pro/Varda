"""How an image was made, kept with it and written into its files.

A provenance record is plain JSON-friendly data in an image's
``extraMetadata["provenance"]``::

    {
      "schema": 1,
      "process": "Band Math", "processId": "band_math",
      "parameters": {...},                       # every setting
      "inputs": [{"name", "path", "provenance"?, "crismColumnsFile"?,
                  "crismIncidenceFile"?, "labelHints"?, "crsFix"?}],
      "vardaVersion": "0.9.0", "created": "2026-10-17T12:00:00+00:00",
      "plugin": "name version",                  # when a plugin made it
      "software": {"spectral": "0.24"},          # libraries/tools that did the work
      "results": {"validation": {...}},          # figures measured on the result
      "source": {...},                           # a downloaded file: where from
    }

Inputs carry their own records, so a chain of results survives in memory and
on disk. ``writeRaster`` stores the record, and the scientific metadata worth
keeping (``KEPT_METADATA_KEYS``), as JSON in the file's ``VARDA`` metadata
domain (inside a GeoTIFF; in ``<file>.aux.xml`` for ENVI); the readers put
them back. Qt-free, so the readers and writers can use it.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from varda.geo.labels import hintsRecord, labelHints
from varda.version import vardaVersion

if TYPE_CHECKING:
    from varda.common.entities import VardaRaster

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
PROVENANCE_KEY = "provenance"
# An input's label facts (``geo.labels.hintsRecord``): the body survives into
# what is made from it, for the CRS health check
LABEL_HINTS_KEY = "labelHints"
# A corrected coordinate system (``image_loading.crs_check``): {"from", "to",
# "reason", "automatic"}, in the image's metadata, its files and the records
# of what is made from it
CRS_FIX_KEY = "crsFix"
# GDAL metadata domain and its two keys (JSON text)
VARDA_NAMESPACE = "VARDA"
METADATA_TAG = "metadata"
# Figures a process measured on its result (accuracy, hold-out validation, the
# training data used): kept in files, and copied into the provenance record
RESULT_KEYS = ("accuracy", "validation", "trainingData")
# A CRISM image's hand-chosen geometry files (``crism_geometry``): the IN
# file (detector columns) and the DDR/DE file (incidence). Kept in files and
# named in the records of what is made from the image
CRISM_GEOMETRY_KEYS = ("crismColumnsFile", "crismIncidenceFile")
# Scientific metadata kept in files: class names and colors (class maps), what
# Eigenvalue Plot and Inverse Transform need, classifier training figures,
# the mosaic recipe, displacement and FATT settings, and analyses' quality
# figures (convergence, coverage, cluster statistics, noise models)
KEPT_METADATA_KEYS = (
    "classes",
    "classColors",
    "explainedVariance",
    "transform",
    "layerSizes",
    "finalLoss",
    "patchSize",
    "components",
    "seed",
    "device",
    "torchVersion",
    "mapMethod",
    "mosaic",
    "displacement",
    "fatt",
    "shadowMask",
    "sensorBands",
    "ratioCube",
    "kmeans",
    "componentSelection",
    "unmixing",
    "iovf",
    "reprojection",
    CRS_FIX_KEY,
    *CRISM_GEOMETRY_KEYS,
    *RESULT_KEYS,
)
# Beside a downloaded file: where it came from (catalogs/downloads.py)
SOURCE_SUFFIX = ".varda-source.json"
_BRIEF = 60  # characters of a value in the one-line description


def plain(value):
    """``value`` as JSON-friendly data: enums by name, arrays as lists, images
    and colors by name, anything else unknown as text."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [plain(v) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain(dataclasses.asdict(value))  # e.g. a Vec2: {"x", "y"}
    name = getattr(value, "name", None)
    if callable(name):  # e.g. a QColor: its hex name
        return name()
    if isinstance(name, str):  # e.g. a VardaRaster or a library spectrum
        return name
    return str(value)


def softwareVersions(names: Iterable[str]) -> dict[str, str]:
    """The installed versions of these distributions (those found)."""
    versions = {}
    for name in names:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return versions


def inputRecord(image: VardaRaster) -> dict:
    """An input as a record names it, with its own record, its hand-chosen
    CRISM geometry files, its corrected CRS and the facts its label states
    when it has them. The label is the one read at load (``labelHints``), so
    the GUI thread reads no files; it's read here only for an image that
    wasn't loaded from a file."""
    entry: dict = {"name": image.name, "path": image.filePath}
    hints = image.labelHints
    facts = hintsRecord(hints if hints is not None else labelHints(image.filePath))
    if facts is not None:
        entry[LABEL_HINTS_KEY] = facts
    record = image.extraMetadata.get(PROVENANCE_KEY)
    if record:
        entry[PROVENANCE_KEY] = record
    if image.extraMetadata.get(CRS_FIX_KEY):
        entry[CRS_FIX_KEY] = image.extraMetadata[CRS_FIX_KEY]
    for key in CRISM_GEOMETRY_KEYS:
        if image.extraMetadata.get(key):
            entry[key] = image.extraMetadata[key]
    return entry


def makeProvenance(
    process: str,
    parameters: Mapping,
    inputs: Sequence[VardaRaster],
    *,
    processId: str = "",
    plugin: str = "",
    software: Mapping[str, str] | None = None,
    results: Mapping | None = None,
) -> dict:
    """A provenance record (see the module docstring)."""
    record = {
        "schema": SCHEMA_VERSION,
        "process": process,
        "processId": processId,
        "parameters": plain(parameters),
        "inputs": [inputRecord(image) for image in inputs],
        "vardaVersion": vardaVersion(),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if plugin:
        record["plugin"] = plugin
    if software:
        record["software"] = dict(software)
    if results:
        record["results"] = plain(results)
    return record


def setProvenance(image: VardaRaster, record: dict) -> None:
    """Keep ``record`` with ``image`` (in its data source's metadata, which
    edits in Image Properties don't replace)."""
    image.dataSource.extraMetadata[PROVENANCE_KEY] = record


# --- in files -------------------------------------------------------------------------


def fileTags(extraMetadata: Mapping) -> dict[str, str]:
    """The ``VARDA`` domain's tags for an image's metadata: its record and its
    kept scientific metadata, as JSON ({} when it has neither)."""
    tags = {}
    record = extraMetadata.get(PROVENANCE_KEY)
    if record:
        tags[PROVENANCE_KEY] = json.dumps(plain(record))
    kept = {
        k: plain(extraMetadata[k]) for k in KEPT_METADATA_KEYS if k in extraMetadata
    }
    if kept:
        tags[METADATA_TAG] = json.dumps(kept)
    return tags


def _json(text: str, what: str, origin: str) -> dict | None:
    try:
        value = json.loads(text)
    except ValueError:
        logger.warning("Ignoring unreadable Varda %s in %s", what, origin)
        return None
    return value if isinstance(value, dict) else None


def readSourceRecord(paths: Iterable[str | Path | None]) -> dict | None:
    """The download record beside the first of ``paths`` that has one."""
    for path in paths:
        if not path:
            continue
        sidecar = Path(str(path) + SOURCE_SUFFIX)
        if sidecar.is_file():
            try:
                return _json(sidecar.read_text(), "download record", str(sidecar))
            except OSError:
                return None
    return None


def fileMetadata(
    tags: Mapping[str, str], paths: Iterable[str | Path | None] = ()
) -> dict:
    """What a file's ``VARDA`` domain ``tags`` (and a download record beside
    one of ``paths``) add to its image's metadata: the record and the kept
    keys. {} for a file Varda neither wrote nor downloaded."""
    origin = next((str(p) for p in paths if p), "a file")
    found: dict = {}
    if METADATA_TAG in tags:
        kept = _json(tags[METADATA_TAG], "metadata", origin) or {}
        found.update({k: v for k, v in kept.items() if k in KEPT_METADATA_KEYS})
    record = (
        _json(tags[PROVENANCE_KEY], "provenance", origin)
        if PROVENANCE_KEY in tags
        else None
    )
    source = readSourceRecord(paths)
    if source is not None:
        record = {**(record or {"schema": SCHEMA_VERSION}), "source": source}
    if record:
        found[PROVENANCE_KEY] = record
    return found


# --- one line, for descriptions ---------------------------------------------------------


def _brief(value) -> str:
    text = str(value)
    return text if len(text) <= _BRIEF else text[: _BRIEF - 1] + "…"


def provenanceText(image) -> str:
    """One line for a file description, or "" when the image has no record."""
    record = image.extraMetadata.get(PROVENANCE_KEY)
    if not record:
        return ""
    if "process" not in record:  # only where it was downloaded from
        source = record.get("source", {})
        return (
            f"Downloaded from {source.get('catalog', '?')} "
            f"({source.get('product', '?')}) {source.get('url', '')} "
            f"on {source.get('downloaded', '?')}"
        )
    parameters = ", ".join(
        f"{name}={_brief(value)}"
        for name, value in record.get("parameters", {}).items()
    )
    inputs = ", ".join(
        i.get("path") or i.get("name", "?") for i in record.get("inputs", [])
    )
    text = (
        f"Made with Varda {record.get('vardaVersion', '?')}: {record['process']} "
        f"({parameters}) from {inputs or 'nothing'} on {record.get('created', '?')}"
    )
    if record.get("plugin"):
        text += f" by the plugin {record['plugin']}"
    return text
