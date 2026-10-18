"""The coordinate-system health check on an opened image (spec §4.2): read
its label once, check its CRS, and apply the one automatic fix as the
image's ``crs`` override, recorded as ``extraMetadata["crsFix"]``.

No GUI: loading runs it on its worker thread (``varda.geo`` locks PROJ
itself), and a restored session runs it again once the session's overrides
are back."""

from __future__ import annotations

from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.common.provenance import CRS_FIX_KEY, PROVENANCE_KEY
from varda.geo.describe import describeCrs
from varda.geo.equivalence import sameCrs
from varda.geo.health import CrsHealth, checkCrs, fixRecord
from varda.geo.labels import LabelHints, labelHints, provenanceHints
from varda.utilities.proj_lock import PROJ_LOCK


def imageLabelHints(image: VardaRaster) -> LabelHints:
    """What the image's own label says: GDAL's planetary label domains, or
    a PDS3 label beside the file or attached to it. Reads files."""
    source = image.dataSource
    # plugin sources may lack gdalMetadata (DataSource's newer members)
    gdalMetadata = getattr(source, "gdalMetadata", None)
    return labelHints(image.filePath, gdalMetadata() if gdalMetadata else {})


def checkImageCrs(image: VardaRaster) -> CrsHealth:
    """Check ``image``'s CRS and keep the result as ``image.crsHealth``.

    The file's CRS is checked against its label (read once, kept as
    ``image.labelHints``) and its Varda provenance. Without a ``crs``
    override, an automatic fix becomes one, with its ``crsFix`` record.
    With one, the health is of the CRS the image uses (a declined fix is
    offered again), and an image's ``crsFix`` record is kept while it still
    says what happened: a record of the CRS in use (the fix applied by
    hand, a CRS set by hand, a record from the file of an exported
    corrected image), or of the fix undone when the file's own CRS is in
    use. Any other record is dropped, or replaced by the automatic fix's."""
    if image.labelHints is None:
        image.labelHints = imageLabelHints(image)
    hints = provenanceHints(image.extraMetadata.get(PROVENANCE_KEY), image.labelHints)
    source = image.dataSource
    declared = source.crs
    size = (image.transform, image.width, image.height)
    health = checkCrs(declared, *size, hints, getattr(source, "rawCrsText", None))
    record = fixRecord(health, declared) if declared is not None else None
    overrides = image.metadataOverrides
    metadata = source.extraMetadata
    kept = metadata.get(CRS_FIX_KEY)
    kept = kept if isinstance(kept, dict) else None
    keep = False
    if record is None:
        chosen = overrides.get("crs")
        if chosen is not None:
            health = checkCrs(chosen, *size, hints)
        keep = _describes(kept, chosen if chosen is not None else declared)
    elif "crs" not in overrides:  # as opened: the automatic fix
        fixed = next(i.fix for i in health.issues if i.fix and i.fix.automatic)
        image.setMetadataOverrides({**overrides, "crs": fixed.wkt})
        metadata[CRS_FIX_KEY] = record
        keep = True
    elif _isFix(overrides["crs"], health):  # the fix, at load or by hand
        byHand = kept is not None and kept.get("automatic") is False
        if not (byHand and _describes(kept, overrides["crs"])):
            metadata[CRS_FIX_KEY] = record
        keep = True
    else:  # another CRS, or the file's own (the fix declined: offered again)
        chosen = overrides["crs"]
        if chosen is None:
            health = checkCrs(declared, *size, hints, applyFix=False)
            keep = kept is not None and kept.get("undone") is True
        else:
            health = checkCrs(chosen, *size, hints)
            keep = _describes(kept, chosen)
    if not keep:
        metadata.pop(CRS_FIX_KEY, None)
    image.crsHealth = health
    return health


def chooseCrs(image: VardaRaster, crs: str, reason: str) -> CrsHealth:
    """Use ``crs`` (WKT, or any text PROJ reads) for ``image`` instead of its
    file's, checked, and recorded as a ``crsFix`` that isn't automatic (the
    user's choice, with their ``reason``). Raises ValueError, changing
    nothing, when PROJ can't read ``crs`` (``setMetadataOverrides`` parses
    it first)."""
    declared = image.dataSource.crs
    image.setMetadataOverrides({**image.metadataOverrides, "crs": crs})
    health = checkImageCrs(image)
    image.dataSource.extraMetadata[CRS_FIX_KEY] = {
        "from": _recordText(declared),
        "to": _recordText(crs),
        "reason": reason,
        "automatic": False,
    }
    return health


def undoCrsFix(image: VardaRaster) -> CrsHealth:
    """Keep the file's own CRS instead of the fix in use (the fix is then
    offered again), with the fix's ``crsFix`` record kept as ``undone``."""
    record = image.dataSource.extraMetadata.get(CRS_FIX_KEY)
    image.setMetadataOverrides({**image.metadataOverrides, "crs": None})
    health = checkImageCrs(image)
    if isinstance(record, dict):
        image.dataSource.extraMetadata[CRS_FIX_KEY] = {**record, "undone": True}
    return health


def _recordText(crs: CRS | str | None) -> str:
    """``crs`` as ``crsFix`` records it: its authority code, else its WKT;
    "none" for none."""
    if crs is None:
        return "none"
    authority = describeCrs(crs).authority
    if authority is not None:
        return authority
    with PROJ_LOCK:
        return CRS.from_user_input(crs).to_wkt()


def _describes(record: dict | None, crs: CRS | str | None) -> bool:
    """Whether ``record`` (a ``crsFix``, not undone) is of ``crs`` in use."""
    if record is None or crs is None or record.get("undone"):
        return False
    return sameCrs(crs, str(record.get("to")))


def _isFix(chosen: str | None, health: CrsHealth) -> bool:
    """Whether the ``crs`` override ``chosen`` is the automatic fix's CRS
    (equivalent, so a session saved with another PROJ version's WKT counts)."""
    if chosen is None:
        return False
    return any(
        sameCrs(chosen, issue.fix.wkt)
        for issue in health.issues
        if issue.fix is not None and issue.fix.automatic
    )
