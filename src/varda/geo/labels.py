"""What a planetary label says about an image's coordinate system: its target
body, latitude type and longitude conventions, read from a PDS3 label
(detached or attached), a PDS4 label or an ISIS cube label.

PDS3 labels (and attached ISIS labels) are plain ODL/PVL, read with a minimal
reader here: ``KEY = VALUE`` statements, quoted and multi-line values,
``GROUP``/``OBJECT`` nesting, and units in ``<…>`` stripped. PDS4 and ISIS
labels come from the ``xml:PDS4`` and ``json:ISIS3`` metadata domains GDAL
exposes. Nothing here raises: an unreadable label gives
``LabelHints.empty()`` with the reason in ``source``.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import cast

from attrs import frozen

from varda.geo.bodies import bodyNamed

# An attached label is the file's leading ODL, up to END, within this many bytes
ATTACHED_LABEL_BYTES = 64 * 1024
# A detached label larger than this isn't read
DETACHED_LABEL_BYTES = 4 * 1024 * 1024

PDS3_PROJECTION_GROUPS = ("IMAGE_MAP_PROJECTION", "MAP_PROJECTION")

_ATTACHED_START = re.compile(
    r"^\s*(?:PDS_VERSION_ID|ODL_VERSION_ID|CCSD3Z|Object\s*=\s*IsisCube)",
    re.IGNORECASE,
)
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_UNITS = re.compile(r"<[^<>]*>")

# An ODL tree: keys upper-cased, values as text or nested groups and objects
type Node = dict[str, str | Node]


@frozen
class LabelHints:
    """The coordinate conventions a label states (``None`` where it doesn't)."""

    target: str | None  # the body's name in Varda's spelling when it knows the body
    latitudeType: str | None  # "planetocentric" | "planetographic"
    longitudeDirection: str | None  # "east" | "west"
    longitudeDomain: str | None  # "0..360" | "-180..180"
    projectionGroup: dict[str, str]  # MAP_PROJECTION / Mapping / cart keys, upper-case
    source: str  # "PDS3 label x.lbl", "PDS4 label", "ISIS cube label", or why none

    @classmethod
    def empty(cls, source: str = "no label") -> LabelHints:
        return cls(None, None, None, None, {}, source)


def labelHints(
    path: str | None, gdalMetadata: Mapping[str, Mapping[str, str]] = {}
) -> LabelHints:
    """What the label of the image at ``path`` says about its coordinates:
    from GDAL's ``json:ISIS3`` or ``xml:PDS4`` domain when ``gdalMetadata``
    has one, else from a PDS3 ``.lbl``/``.LBL`` beside ``path`` (or ``path``
    itself), else from a label attached to the start of the file."""
    reasons: list[str] = []
    for domain, read in (("json:ISIS3", _isisJsonHints), ("xml:PDS4", _pds4Hints)):
        text = next(iter(gdalMetadata.get(domain, {}).values()), None)
        if text:
            try:
                return read(text)
            except Exception as error:
                reasons.append(f"unreadable {domain} metadata ({error})")
    if path:
        try:
            return _fileHints(Path(path))
        except Exception as error:
            reasons.append(f"no readable label for {Path(path).name} ({error})")
    return LabelHints.empty("; ".join(reasons) or "no label")


# --- PDS3 and attached ISIS labels ---


class _NoLabel(Exception):
    pass


def _fileHints(path: Path) -> LabelHints:
    for label in _detachedLabels(path):
        if label.stat().st_size > DETACHED_LABEL_BYTES:
            raise _NoLabel(f"{label.name} is larger than a label")
        text = label.read_bytes().decode("latin-1")
        return _odlHints(parseOdl(text), f"PDS3 label {label.name}")
    with path.open("rb") as file:
        head = file.read(ATTACHED_LABEL_BYTES).decode("latin-1")
    if not _ATTACHED_START.match(head):
        raise _NoLabel("no PDS3 label beside it or attached")
    return _odlHints(parseOdl(head), f"PDS3 label attached to {path.name}")


def _detachedLabels(path: Path) -> Iterator[Path]:
    if path.suffix.lower() == ".lbl":
        yield path
        return
    # listed rather than guessed, for its real name on case-insensitive disks
    wanted = path.with_suffix(".lbl").name.lower()
    for label in sorted(path.parent.iterdir()):
        if label.name.lower() == wanted and label.is_file():
            yield label


def _statements(text: str) -> Iterator[tuple[str, str]]:
    """``(KEY, raw value)`` pairs up to ``END``, with values that continue over
    lines (open quotes or brackets) joined; ``END_OBJECT`` and the like may
    have no value."""
    lines = _COMMENT.sub("", text).splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if line.upper() == "END":
            return
        key, _, value = line.partition("=")
        value = value.strip()
        while index < len(lines) and (value.count('"') % 2 or _open(value)):
            value = f"{value} {lines[index].strip()}"
            index += 1
        if key.strip():
            yield key.strip().upper(), value


def _open(value: str) -> bool:
    return value.count("(") + value.count("{") > value.count(")") + value.count("}")


def _clean(value: str) -> str:
    """A value's text: quotes removed, units stripped, whitespace collapsed."""
    if value.startswith('"'):
        return " ".join(value.strip('"').split())
    return " ".join(_UNITS.sub("", value).split()).strip("'")


def parseOdl(text: str) -> Node:
    """A minimal ODL/PVL reader: the label as a tree with upper-case keys;
    a repeated key keeps its first value, and repeated groups merge."""
    root: Node = {}
    stack = [root]
    for key, value in _statements(text):
        if key in ("OBJECT", "GROUP"):
            name = _clean(value).upper()
            child = stack[-1].get(name)
            if not isinstance(child, dict):
                child = {}
                stack[-1][name] = child
            stack.append(child)
        elif key in ("END_OBJECT", "END_GROUP"):
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].setdefault(key, _clean(value))
    return root


def _findGroup(node: Node, names: tuple[str, ...]) -> Node | None:
    """The first group or object called one of ``names``, depth first."""
    for key, value in node.items():
        if isinstance(value, dict):
            if key in names:
                return value
            found = _findGroup(value, names)
            if found is not None:
                return found
    return None


def _findValue(node: Node, key: str) -> str | None:
    """``key``'s value at this level, else in the first group that has it."""
    value = node.get(key)
    if isinstance(value, str):
        return value
    for child in node.values():
        if isinstance(child, dict) and (found := _findValue(child, key)) is not None:
            return found
    return None


def _flat(node: Node | None) -> dict[str, str]:
    return {k: v for k, v in (node or {}).items() if isinstance(v, str)}


def _odlHints(root: Node, source: str) -> LabelHints:
    if isinstance(root.get("ISISCUBE"), dict):
        return _isisHints(root)
    group = _flat(_findGroup(root, PDS3_PROJECTION_GROUPS))
    return LabelHints(
        target=_target(_findValue(root, "TARGET_NAME")),
        latitudeType=_latitudeType(group.get("COORDINATE_SYSTEM_NAME")),
        longitudeDirection=_direction(group.get("POSITIVE_LONGITUDE_DIRECTION")),
        longitudeDomain=_domain(
            group.get("WESTERNMOST_LONGITUDE"), group.get("EASTERNMOST_LONGITUDE")
        ),
        projectionGroup=group,
        source=source,
    )


# --- ISIS ---


def _isisJsonHints(text: str) -> LabelHints:
    label = json.loads(text)
    if not isinstance(label, dict):
        raise ValueError("not an ISIS label")
    return _isisHints(_jsonNode(label))


def _jsonNode(obj: dict[str, object]) -> Node:
    """GDAL's ``json:ISIS3`` label as an ODL tree: ``{"value", "unit"}``
    pairs become their value, and GDAL's ``_type`` keys are dropped."""
    node: Node = {}
    for key, value in obj.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict):
            fields = cast("dict[str, object]", value)
            if "value" not in fields:
                node[key.upper()] = _jsonNode(fields)
                continue
            value = fields["value"]
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        node[key.upper()] = str(value)
    return node


def _isisHints(root: Node) -> LabelHints:
    mapping = _findGroup(root, ("MAPPING",))
    group = _flat(mapping)
    domain = {"360": "0..360", "180": "-180..180"}.get(
        group.get("LONGITUDEDOMAIN", "").strip()
    )
    return LabelHints(
        target=_target(group.get("TARGETNAME") or _findValue(root, "TARGETNAME")),
        latitudeType=_latitudeType(group.get("LATITUDETYPE")),
        longitudeDirection=_direction(group.get("LONGITUDEDIRECTION")),
        longitudeDomain=domain,
        projectionGroup=group,
        source="ISIS cube label",
    )


# --- PDS4 ---


def _pds4Hints(text: str) -> LabelHints:
    root = ET.fromstring(text.encode())
    target = None
    cart: dict[str, str] = {}
    for element in root.iter():
        namespace, _, name = element.tag.rpartition("}")
        if target is None and name.lower() == "target_identification":
            target = next(
                (c.text for c in element if c.tag.rpartition("}")[2] == "name"), None
            )
        elif "/cart/" in namespace and len(element) == 0 and element.text:
            cart.setdefault(name.upper(), element.text.strip())
    return LabelHints(
        target=_target(target),
        latitudeType=_latitudeType(cart.get("LATITUDE_TYPE")),
        longitudeDirection=_direction(cart.get("LONGITUDE_DIRECTION")),
        longitudeDomain=_domain(
            cart.get("WEST_BOUNDING_COORDINATE"), cart.get("EAST_BOUNDING_COORDINATE")
        ),
        projectionGroup=cart,
        source="PDS4 label",
    )


# --- Normalizing values ---


def _target(text: str | None) -> str | None:
    """The body's name in Varda's spelling ("MARS" → "Mars"), else as written."""
    text = (text or "").strip()
    if not text:
        return None
    body = bodyNamed(text)
    return body.name if body is not None else text


def _latitudeType(text: str | None) -> str | None:
    text = (text or "").lower()
    if "centric" in text:
        return "planetocentric"
    if "graphic" in text:
        return "planetographic"
    return None


def _direction(text: str | None) -> str | None:
    text = (text or "").lower().replace(" ", "")
    if text.endswith("east"):
        return "east"
    if text.endswith("west"):
        return "west"
    return None


def _domain(*longitudes: str | None) -> str | None:
    """ "0..360" when a longitude is past 180°, "-180..180" when one is
    negative, else ``None`` (the range fits either)."""
    values: list[float] = []
    for text in longitudes:
        try:
            values.append(float(text or "nan"))
        except ValueError:
            continue
    if any(value > 180 for value in values):
        return "0..360"
    if any(value < 0 for value in values):
        return "-180..180"
    return None
