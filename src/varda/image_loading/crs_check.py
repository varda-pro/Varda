"""The coordinate-system health check on an opened image (spec §4.2): read
its label once, check its CRS, and apply the one automatic fix as the
image's ``crs`` override (and ``transform`` override, for a label's pixel
grid when the file has none), recorded as ``extraMetadata["crsFix"]``.

A fix in use is shown as one in the image's health (``CrsFix.automatic``
True, which the Image Properties dialog offers to Undo): the automatic fix,
and a fix the check offered that the user applied (``applyCrsFix``), which
is then INFO, worded as its reason. A CRS the user chose that differs from
the label's keeps its ``label-crs-mismatch``, as INFO worded as their
choice: still offered, never an error.

No GUI: loading runs it on its worker thread (``varda.geo`` locks PROJ
itself), and a restored session runs it again once the session's overrides
are back."""

from __future__ import annotations

import math
from collections.abc import Sequence

import attrs
from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.common.provenance import CRS_FIX_KEY, PROVENANCE_KEY
from varda.geo.describe import describeCrs
from varda.geo.equivalence import sameCrs
from varda.geo.health import (
    SET_CRS,
    CrsFix,
    CrsHealth,
    CrsIssue,
    Severity,
    Transform,
    checkCrs,
    fixRecord,
    recordText,
)
from varda.geo.label_crs import labelCrs
from varda.geo.labels import LabelHints, labelHints, provenanceHints

# The issues whose fix is the CRS built from the image's own label
LABEL_ISSUES = frozenset({"no-crs", "label-crs-mismatch"})


def imageLabelHints(image: VardaRaster) -> LabelHints:
    """What the image's own label says: GDAL's planetary label domains, or
    a PDS3 label beside the file or attached to it. Reads files."""
    source = image.dataSource
    # plugin sources may lack gdalMetadata (DataSource's newer members)
    gdalMetadata = getattr(source, "gdalMetadata", None)
    return labelHints(image.filePath, gdalMetadata() if gdalMetadata else {})


def checkImageCrs(image: VardaRaster) -> CrsHealth:
    """Check ``image``'s CRS and keep the result as ``image.crsHealth``.

    The file's CRS and pixel grid are checked against its label (read once,
    kept as ``image.labelHints``) and its Varda provenance. Without a
    ``crs`` override, an automatic fix becomes one (with a ``transform``
    override when it sets the grid), with its ``crsFix`` record. With one,
    the health is of the CRS and grid the image uses (a declined fix is
    offered again), and an image's ``crsFix`` record is kept while it still
    says what happened: a record of the CRS in use (a fix applied by hand,
    a CRS set by hand, a record from the file of an exported corrected
    image), or of the fix undone when the file's own CRS is in use. Any
    other record is dropped, or replaced by the automatic fix's."""
    if image.labelHints is None:
        image.labelHints = imageLabelHints(image)
    hints = provenanceHints(image.extraMetadata.get(PROVENANCE_KEY), image.labelHints)
    source = image.dataSource
    declared = source.crs
    size = (image.width, image.height)
    rawCrsText = getattr(source, "rawCrsText", None)
    health = checkCrs(declared, source.transform, *size, hints, rawCrsText)
    record = fixRecord(health, declared)
    overrides = image.metadataOverrides
    metadata = source.extraMetadata
    kept = metadata.get(CRS_FIX_KEY)
    kept = kept if isinstance(kept, dict) else None
    chosen = overrides.get("crs")
    if record is not None and "crs" not in overrides:  # as opened: the fix
        fix = next(i.fix for i in health.issues if i.fix and i.fix.automatic)
        image.setMetadataOverrides({**overrides, **_overridesOf(fix)})
        metadata[CRS_FIX_KEY] = record
        keep = True
    elif record is not None and _isFix(overrides, health):  # at load or by hand
        byHand = kept is not None and kept.get("automatic") is False
        if not (byHand and _describes(kept, chosen)):
            metadata[CRS_FIX_KEY] = record
        keep = True
    elif chosen is None:  # the file's own CRS
        if "crs" in overrides:  # kept explicitly: a fix declined is offered again
            health = checkCrs(
                declared, source.transform, *size, hints, rawCrsText, applyFix=False
            )
        undone = "crs" in overrides and kept is not None and kept.get("undone")
        keep = undone is True or _describes(kept, declared)
    else:  # another CRS: a fix offered, applied by hand, or the user's own
        health = _chosenHealth(image, chosen, health, hints)
        keep = _describes(kept, chosen)
    if not keep:
        metadata.pop(CRS_FIX_KEY, None)
    image.crsHealth = health
    return health


def chooseCrs(
    image: VardaRaster,
    crs: str,
    reason: str,
    transform: Transform | None = None,
) -> CrsHealth:
    """Use ``crs`` (WKT, PROJJSON, or any text PROJ reads) for ``image``
    instead of its file's, and with ``transform`` its pixel grid too
    (Affine's a, b, c, d, e, f), checked, and recorded as a ``crsFix`` that
    isn't automatic (the user's choice, with their ``reason``, and the
    ``transform`` in use: this one, or a grid kept from an earlier fix). Raises ValueError, changing nothing, when PROJ
    can't read ``crs`` or the transform isn't one (``setMetadataOverrides``
    parses them first)."""
    declared = image.dataSource.crs
    grid = {} if transform is None else {"transform": list(transform)}
    image.setMetadataOverrides({**image.metadataOverrides, "crs": crs, **grid})
    health = checkImageCrs(image)
    record: dict[str, str | bool | list[float]] = {
        "from": _recordText(declared),
        "to": _recordText(crs),
        "reason": reason,
        "automatic": False,
    }
    inUse = image.metadataOverrides.get("transform")
    if inUse is not None:
        record["transform"] = list(inUse)
    image.dataSource.extraMetadata[CRS_FIX_KEY] = record
    return health


def applyCrsFix(image: VardaRaster, fix: CrsFix) -> CrsHealth:
    """Use the ``fix`` the check offered (``chooseCrs`` with its CRS, reason
    and pixel grid). A fix built from the label is recorded with
    ``"source": "label"``, as the automatic one is."""
    before = image.crsHealth
    code = next(
        (i.code for i in (before.issues if before else ()) if i.fix == fix), None
    )
    health = chooseCrs(image, fix.wkt, fix.reason, fix.transform)
    if code in LABEL_ISSUES:
        image.dataSource.extraMetadata[CRS_FIX_KEY]["source"] = "label"
    return health


def undoCrsFix(image: VardaRaster) -> CrsHealth:
    """Keep the file's own CRS instead of the fix in use, and its own pixel
    grid when the fix set one (the ``transform`` override its record names;
    another, e.g. a session's corrected grid, stays). The fix is then
    offered again, with its ``crsFix`` record kept as ``undone``."""
    record = image.dataSource.extraMetadata.get(CRS_FIX_KEY)
    grid = record.get("transform") if isinstance(record, dict) else None
    overrides = image.metadataOverrides
    fixGrid = grid is not None and _sameGrid(overrides.get("transform"), grid)
    rest = {k: v for k, v in overrides.items() if k != "transform" or not fixGrid}
    image.setMetadataOverrides({**rest, "crs": None})
    health = checkImageCrs(image)
    if isinstance(record, dict):
        image.dataSource.extraMetadata[CRS_FIX_KEY] = {**record, "undone": True}
    return health


def _overridesOf(fix: CrsFix) -> dict[str, str | list[float]]:
    """The overrides that put ``fix`` in use: its CRS, and its grid if any."""
    grid = {} if fix.transform is None else {"transform": list(fix.transform)}
    return {"crs": fix.wkt, **grid}


def _chosenHealth(
    image: VardaRaster, chosen: str, opened: CrsHealth, hints: LabelHints
) -> CrsHealth:
    """The health of the CRS (and grid) ``chosen`` for ``image``. When it's a
    fix the check of the file (``opened``) offered, that fix is listed first
    as in use (to undo); otherwise a ``label-crs-mismatch`` is worded as the
    user's choice, not an error."""
    health = checkCrs(chosen, image.transform, image.width, image.height, hints)
    overrides = image.metadataOverrides
    offered = next(
        (
            issue
            for issue in opened.issues
            if issue.fix is not None
            and not issue.fix.automatic
            and _inUse(overrides, issue.fix)
        ),
        None,
    )
    if offered is not None and offered.fix is not None:
        applied = CrsIssue(
            offered.code,
            Severity.INFO,
            offered.fix.reason,
            f"Undo to use its file's coordinate system, or {SET_CRS} to use another",
            attrs.evolve(offered.fix, automatic=True),
        )
        return attrs.evolve(health, issues=(applied, *health.issues))
    issues = [
        _yourChoice(issue, health, hints)
        if issue.code == "label-crs-mismatch"
        else issue
        for issue in health.issues
    ]
    return attrs.evolve(health, issues=tuple(issues))


def _yourChoice(issue: CrsIssue, health: CrsHealth, hints: LabelHints) -> CrsIssue:
    """A ``label-crs-mismatch`` against a CRS the user chose: INFO, worded as
    their choice, with the label's CRS still offered."""
    if issue.fix is None:
        return issue
    label = labelCrs(hints)
    assumed = label.assumed if label is not None else ()
    assumes = f" (it assumes {'; '.join(assumed)})" if assumed else ""
    labelText = describeCrs(issue.fix.wkt).text
    return attrs.evolve(
        issue,
        severity=Severity.INFO,
        message=f"You chose {health.description.text}; its label describes {labelText}.",
        suggestion=f"Apply to use its label's CRS instead{assumes}, or keep yours",
    )


def _recordText(crs: CRS | str | None) -> str:
    """``crs`` as ``crsFix`` records it (``health.recordText``: its authority
    code, else its WKT, else its PROJJSON when WKT would drop a west
    longitude axis); "none" for none."""
    return "none" if crs is None else recordText(crs)


def _describes(record: dict | None, crs: CRS | str | None) -> bool:
    """Whether ``record`` (a ``crsFix``, not undone) is of ``crs`` in use."""
    if record is None or crs is None or record.get("undone"):
        return False
    return sameCrs(crs, str(record.get("to")))


def _isFix(overrides: dict, health: CrsHealth) -> bool:
    """Whether the ``crs`` (and ``transform``) overrides are the automatic
    fix's (an equivalent CRS, so a session saved with another PROJ
    version's WKT counts)."""
    return any(
        _inUse(overrides, issue.fix)
        for issue in health.issues
        if issue.fix is not None and issue.fix.automatic
    )


def _inUse(overrides: dict, fix: CrsFix) -> bool:
    """Whether ``overrides`` put ``fix`` in use: its CRS (equivalent), and
    its pixel grid, or the file's when it has none."""
    chosen = overrides.get("crs")
    if chosen is None or not sameCrs(chosen, fix.wkt):
        return False
    return _sameGrid(overrides.get("transform"), fix.transform)


def _sameGrid(override: Sequence[float] | None, grid: Sequence[float] | None) -> bool:
    if override is None or grid is None:
        return override is None and grid is None
    return len(override) == len(grid) and all(
        math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-9) for a, b in zip(override, grid)
    )
