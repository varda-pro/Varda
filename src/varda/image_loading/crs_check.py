"""The coordinate-system health check on an opened image (spec §4.2): read
its label once, check its CRS, and apply the one automatic fix as the
image's ``crs`` override, recorded as ``extraMetadata["crsFix"]``.

Qt-free; loading runs it on its worker thread (``varda.geo`` locks PROJ
itself), and a restored session runs it again once the session's overrides
are back."""

from __future__ import annotations

from varda.common.entities import VardaRaster
from varda.common.provenance import CRS_FIX_KEY, PROVENANCE_KEY
from varda.geo.equivalence import sameCrs
from varda.geo.health import CrsHealth, checkCrs, fixRecord
from varda.geo.labels import LabelHints, labelHints, provenanceHints


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
    override, an automatic fix becomes one, with its ``crsFix`` record. An
    override that is the fix keeps the record; any other (a CRS chosen by
    hand, or None for the file's own) drops it and is checked itself."""
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
    if record is None:
        chosen = overrides.get("crs")
        if chosen is not None:
            health = checkCrs(chosen, *size, hints)
    elif "crs" not in overrides or _isFix(overrides["crs"], health):
        if "crs" not in overrides:
            fixed = next(i.fix for i in health.issues if i.fix and i.fix.automatic)
            image.setMetadataOverrides({**overrides, "crs": fixed.wkt})
        metadata[CRS_FIX_KEY] = record
    else:  # the user's choice: the fix's record no longer applies
        metadata.pop(CRS_FIX_KEY, None)
        if overrides["crs"] is not None:
            health = checkCrs(overrides["crs"], *size, hints)
    image.crsHealth = health
    return health


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
