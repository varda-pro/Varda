"""The coordinate-system health check run when an image opens (spec §4.2):
what is wrong or worth knowing about an image's CRS, in plain words, with
what to do, and the fixes Varda applies without asking.

Two fixes are automatic. An Earth *geographic* CRS (just degrees) on an image
whose label names another body Varda knows is read as degrees on that body's
IAU 2015 sphere. An image with no CRS whose own label has a complete map
projection group (``label_crs.labelCrs``, nothing assumed) gets the label's
CRS, and its pixel grid when the file has none. A label CRS built with an
assumption is offered, and so is one that disagrees with the CRS GDAL built
(``label-crs-mismatch``): GDAL may be right. The label decides only when it
states the image's latitude and longitude range too, and the image's center
falls within that range under its label's CRS but not under its file's
(HiRISE's JPEG 2000 orthoimages, whose files take the label's center
latitude, the standard parallel, for the latitude of origin): then the
label's CRS is used automatically. Hints from Varda provenance never raise a
label issue, since a result's grid may differ from its inputs'. Everything
else is reported, never changed: Varda can't know which of the CRS and the
label is right."""

from __future__ import annotations

import logging
import math
from enum import Enum

import attrs
from affine import Affine
from attrs import frozen
from pyproj import CRS

from varda.geo.bodies import (
    BodyMatch,
    BodyShape,
    bodyNamed,
    identifyBody,
    isNonBodyTarget,
)
from varda.geo.describe import CrsDescription, describeCrs
from varda.geo.distortion import DistortionSummary, distortionOver
from varda.geo.equivalence import sameCrs
from varda.geo.label_crs import LabelCrs, labelCrs
from varda.geo.labels import LabelHints
from varda.geo.transform import geodeticLonLat, transformPoints
from varda.utilities.proj_lock import PROJ_LOCK

logger = logging.getLogger(__name__)

SET_CRS = "Set coordinate system…"
DISTORTION_SUGGESTION = (
    "Measurements are geodesic and unaffected; shapes and slopes on this map "
    "are distorted — reproject it (Processing → Geometry → Reproject Image) "
    "for figures and terrain"
)
_NO_LABEL = LabelHints.empty()
LABEL_FIX_HEADLINE = "No coordinate system in its file; built from its label."
PROVENANCE_SOURCE = "Varda provenance"
# The projections (PROJ's method names) whose longitude depends on map x
# alone, for the check of the label's longitude range
_CYLINDRICAL = frozenset(
    {
        "Equidistant Cylindrical",
        "Equidistant Cylindrical (Spherical)",
        "Mercator (variant A)",
        "Mercator (variant B)",
        "Mercator (Spherical)",
    }
)
# A span of edge longitudes this wide (degrees) is global: its edges can't
# tell east from west
_GLOBAL_SPAN_DEG = 359.0
# How far (pixels, else degrees) a label's longitude bound may be from an edge
BOUNDS_TOLERANCE_PX = 3.0
BOUNDS_TOLERANCE_DEG = 1e-4
# How far outside its label's latitude and longitude range an image's center
# may fall and still be within it: this fraction of the range, at least
# RANGE_SPARE_DEG
RANGE_SPARE = 0.25
RANGE_SPARE_DEG = 0.01

type Transform = tuple[float, float, float, float, float, float]


class Severity(Enum):
    INFO = "info"  # shown in Properties only
    TIP = "tip"  # badge + tooltip
    PROBLEM = "problem"  # badge + tooltip + a status-bar message at load


@frozen
class CrsFix:
    wkt: str  # the CRS to use instead
    reason: str  # one sentence, recorded in provenance
    automatic: bool  # applied at load without asking
    # the pixel-to-map transform to use too (Affine's a, b, c, d, e, f), when
    # the file has none and the label gives one (§4.8)
    transform: Transform | None = None


@frozen
class CrsIssue:
    # "no-crs", "unreadable-crs", "earth-crs-on-body", "unknown-body",
    # "ambiguous-body", "lon-direction-mismatch", "latitude-type-mismatch",
    # "high-distortion", "lon-0-360", "label-crs-mismatch",
    # "lon-bounds-westward", "irregular-body"
    code: str
    severity: Severity
    message: str  # one line, plain words
    suggestion: str  # one line: what to do
    fix: CrsFix | None = None


@frozen
class CrsHealth:
    description: CrsDescription  # of the CRS as given
    body: BodyMatch  # the CRS's body; the label's when the CRS can't say
    issues: tuple[CrsIssue, ...]
    distortion: DistortionSummary | None  # §4.5


def checkCrs(
    crs: CRS | str | None,
    transform: Affine,
    width: int,
    height: int,
    hints: LabelHints = _NO_LABEL,
    rawCrsText: str | None = None,
    applyFix: bool = True,
) -> CrsHealth:
    """What is wrong or worth knowing about an image's ``crs`` (with its
    ``transform`` and size), given what its label says (``hints``) and the
    CRS text its file had when Varda couldn't read it (``rawCrsText``).

    When the automatic fix applies, the body, convention and distortion
    checks are of the fixed CRS, the one Varda uses. With ``applyFix``
    False (the user kept the file's CRS) they are of ``crs``, and the fix
    is offered instead (not automatic). Never raises."""
    try:
        return _check(crs, transform, width, height, hints, rawCrsText, applyFix)
    except Exception as error:  # a CRS PROJ chokes on in an unexpected way
        description = attrs.evolve(
            describeCrs(None), kind="unreadable", projection=str(error)
        )
        issue = CrsIssue(
            "unreadable-crs",
            Severity.PROBLEM,
            f"Varda couldn't check its coordinate system ({error}).",
            SET_CRS,
        )
        return CrsHealth(description, BodyMatch(None, str(error)), (issue,), None)


def _check(
    crs: CRS | str | None,
    transform: Affine,
    width: int,
    height: int,
    hints: LabelHints,
    rawCrsText: str | None,
    applyFix: bool,
) -> CrsHealth:
    description = describeCrs(crs, transform, width, height)
    own = identifyBody(crs)
    body = own if own.body is not None else identifyBody(crs, hints.target)
    label = labelCrs(hints) if _ownLabel(hints) else None
    issues: list[CrsIssue] = []
    effective: CRS | str
    if crs is None or description.kind == "unreadable":
        issue = _missing(crs, transform, hints, rawCrsText, label, applyFix)
        if issue is None or issue.fix is None or not issue.fix.automatic:
            return CrsHealth(description, body, (issue,) if issue else (), None)
        # the checks below are of the label's CRS and grid, which Varda uses
        issues.append(issue)
        effective = issue.fix.wkt
        if issue.fix.transform is not None:
            transform = Affine(*issue.fix.transform)
        used = describeCrs(effective, transform, width, height)
        body = identifyBody(effective, hints.target)
    else:
        effective, used = crs, description
        earth = _earthOnBody(crs, description, own, hints, applyFix)
        if earth is not None:
            issues.append(earth)
            if earth.fix is not None and earth.fix.automatic:
                effective = earth.fix.wkt
                used = describeCrs(effective, transform, width, height)
                body = identifyBody(effective, hints.target)
        mismatch = _labelMismatch(
            effective, used, transform, width, height, hints, label, applyFix
        )
        if mismatch is not None:
            issues.append(mismatch)
            if mismatch.fix is not None and mismatch.fix.automatic:
                effective = mismatch.fix.wkt
                used = describeCrs(effective, transform, width, height)
                body = identifyBody(effective, hints.target)
    if used.kind in ("geographic", "projected") and body.body is None:
        issues.append(_unknownBody(body))
    if body.body is not None and body.body.shape is BodyShape.IRREGULAR:
        issues.append(IRREGULAR_ISSUE)
    issues.extend(_conventions(used, hints))
    if label is not None and used.kind == "projected":
        westward = _westwardCheck(effective, transform, width, height, hints)
        if westward is not None:
            issues.append(westward)
    distortion = None
    if used.kind in ("geographic", "projected") and not transform.is_identity:
        distortion = distortionOver(effective, transform, width, height)
    if distortion is not None and distortion.worst.level != "ok":
        issues.append(_distorted(distortion))
    if used.longitudeDomain == "0..360":
        issues.append(
            CrsIssue(
                "lon-0-360",
                Severity.INFO,
                "Its longitudes run from 0 to 360° (not −180 to 180°).",
                "Nothing to do: Varda reads both conventions",
            )
        )
    return CrsHealth(description, body, tuple(issues), distortion)


IRREGULAR_ISSUE = CrsIssue(
    "irregular-body",
    Severity.TIP,
    "Irregular body: distances and areas on a sphere or ellipsoid are "
    "approximate; use a shape model.",
    "Open a shape model in 3D Terrain (Surface → Shape model) to measure on the body",
)


def _ownLabel(hints: LabelHints) -> bool:
    """Whether ``hints`` come from the image's own label (not only from Varda
    provenance, which carries a target, never a projection group)."""
    return not hints.source.startswith(PROVENANCE_SOURCE)


def _missing(
    crs: CRS | str | None,
    transform: Affine,
    hints: LabelHints,
    rawCrsText: str | None,
    label: LabelCrs | None,
    applyFix: bool,
) -> CrsIssue | None:
    """No CRS, or one Varda can't read; with no CRS at all, the one the
    image's own label (``label``) describes, applied when it's complete and
    gives a pixel grid (``applyFix``), else offered."""
    if crs is not None or rawCrsText:
        return CrsIssue(
            "unreadable-crs",
            Severity.PROBLEM,
            "Its coordinate system can't be read, so Varda treats it as unreferenced.",
            f"{SET_CRS}; Image Properties shows the text it couldn't read",
        )
    if not transform.is_identity:
        message = (
            "Its map coordinates have no coordinate system, so Varda treats it "
            "as unreferenced."
        )
    elif hints.projectionGroup:
        message = (
            "Its label describes a map projection, but it has no coordinate "
            "system, so Varda treats it as unreferenced."
        )
    else:
        return None
    if label is None:
        return CrsIssue("no-crs", Severity.PROBLEM, message, SET_CRS)
    if label.wkt is None:
        # what the CRS needs; the pixel grid's keys only when that's all
        crsNeeds = [m for m in label.missing if "pixel grid" not in m]
        needs = "; ".join(crsNeeds or label.missing)
        suggestion = (
            f"Its label lacks what Varda needs to build one ({needs}); {SET_CRS}"
        )
        return CrsIssue("no-crs", Severity.PROBLEM, message, suggestion)
    grid = label.transform if transform.is_identity else None
    gridMissing = transform.is_identity and grid is None
    automatic = label.complete and not gridMissing
    source = label.source.split(";")[0]
    surface = describeCrs(label.wkt).surface
    fix = CrsFix(
        wkt=label.wkt,
        reason=(
            "Its file has no coordinate system; built from its "
            f"{source} map projection group ({label.projection}, {surface})"
        ),
        automatic=automatic and applyFix,
        transform=grid,
    )
    if automatic and applyFix:
        message = (
            "Its file has no coordinate system; Varda built it from its label's "
            "map projection."
        )
        suggestion = f"Varda uses it; {SET_CRS} to use another"
        return CrsIssue("no-crs", Severity.PROBLEM, message, suggestion, fix)
    caveats = [f"assumes {line}" for line in label.assumed]
    if gridMissing:
        needs = ", ".join(m for m in label.missing if "pixel grid" in m)
        caveats.append(f"has no pixel grid without {needs}")
    if not automatic:
        suggestion = (
            f"Apply to use the CRS built from its label, which {'; '.join(caveats)}, "
            f"or {SET_CRS}"
        )
    else:  # complete, but the user kept the file's (no) CRS
        message = f"{message.removesuffix('.')}; you chose not to use its label's."
        suggestion = f"Apply to use the CRS built from its label, or {SET_CRS}"
    return CrsIssue("no-crs", Severity.PROBLEM, message, suggestion, fix)


def _labelMismatch(
    crs: CRS | str,
    used: CrsDescription,
    transform: Affine,
    width: int,
    height: int,
    hints: LabelHints,
    label: LabelCrs | None,
    applyFix: bool,
) -> CrsIssue | None:
    """The CRS in use (after any Earth fix) against the one its own label
    describes: offered, since GDAL may be right, unless the label's latitude
    and longitude range settles it (``_rangeDecides``): then automatic (when
    ``applyFix``)."""
    if label is None or label.wkt is None or sameCrs(crs, label.wkt):
        return None
    labelText = describeCrs(label.wkt).text
    grid = label.transform if transform.is_identity else None
    decided = label.complete and _rangeDecides(
        crs, label.wkt, transform, width, height, hints
    )
    source = label.source.split(";")[0]
    fix = CrsFix(
        wkt=label.wkt,
        reason=(
            f"Its label ({source}) describes {labelText}, but its CRS was "
            f"{used.text}; the label's map projection group used"
            + (
                f", the only one that puts it within its label's range ({_rangeText(hints)})"
                if decided
                else ""
            )
        ),
        automatic=decided and applyFix,
        transform=grid,
    )
    if decided:
        message = (
            f"Its file's CRS puts it outside the range its label states "
            f"({_rangeText(hints)}); Varda uses its label's ({labelText})."
        )
        if applyFix:
            suggestion = f"Varda uses it; {SET_CRS} to use another"
        else:
            message = f"{message.removesuffix('.')}; you chose to keep its file's."
            suggestion = f"Apply to use its label's CRS, or {SET_CRS}"
        return CrsIssue(
            "label-crs-mismatch", Severity.PROBLEM, message, suggestion, fix
        )
    assumes = f" (it assumes {'; '.join(label.assumed)})" if label.assumed else ""
    return CrsIssue(
        "label-crs-mismatch",
        Severity.PROBLEM,
        f"Its label describes {labelText}, but its CRS is {used.text}.",
        "Check a known feature's position, then Apply to use its label's CRS"
        f"{assumes}, or keep this one: GDAL may be right",
        fix,
    )


def _rangeDecides(
    crs: CRS | str,
    labelWkt: str,
    transform: Affine,
    width: int,
    height: int,
    hints: LabelHints,
) -> bool:
    """Whether the latitude and longitude range the label states (with
    ``RANGE_SPARE``) holds the image's center under the label's CRS and not
    under ``crs``. False when the label states no range or either center
    can't be found."""
    if transform.is_identity or width <= 0 or height <= 0:
        return False
    inLabel = _centerInRange(labelWkt, transform, width, height, hints)
    return (
        inLabel is True
        and _centerInRange(crs, transform, width, height, hints) is False
    )


def _centerInRange(
    crs: CRS | str, transform: Affine, width: int, height: int, hints: LabelHints
) -> bool | None:
    """Whether the image's center, under ``crs``, is within the label's
    latitude and longitude range (``RANGE_SPARE``); None when the label
    states none or the center isn't a place."""
    longitudes, latitudes = _labelBounds(hints), _labelLatitudes(hints)
    if longitudes is None or latitudes is None:
        return None
    x, y = transform * (width / 2, height / 2)
    try:
        with PROJ_LOCK:
            parsed = CRS.from_user_input(crs)
            (lon,), (lat,) = transformPoints(parsed, geodeticLonLat(parsed), [x], [y])
    except Exception:
        return None
    lon, lat = float(lon), float(lat)
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    south, north = sorted(latitudes)
    spare = max(RANGE_SPARE * (north - south), RANGE_SPARE_DEG)
    if not south - spare <= lat <= north + spare:
        return False
    east = hints.longitudeDirection != "west"
    west, eastern = (b if east else -b for b in longitudes)  # east-positive
    span = (eastern - west) % 360.0 or 360.0
    spare = max(RANGE_SPARE * span, RANGE_SPARE_DEG)
    offset = (lon - west) % 360.0
    return offset <= span + spare or offset >= 360.0 - spare


def _rangeText(hints: LabelHints) -> str:
    """The label's range, as people read it: latitudes, then longitudes."""
    latitudes, longitudes = _labelLatitudes(hints), _labelBounds(hints)
    assert latitudes is not None and longitudes is not None
    south, north = sorted(latitudes)
    word = "W" if hints.longitudeDirection == "west" else "E"
    first, second = longitudes
    return (
        f"latitude {south:g}° to {north:g}°, longitude {first:g}° to {second:g}° {word}"
    )


def _westwardCheck(
    crs: CRS | str,
    transform: Affine,
    width: int,
    height: int,
    hints: LabelHints,
) -> CrsIssue | None:
    """``_westwardBounds``, which is advisory: when it fails, only it is
    skipped (and logged), never the rest of the check."""
    try:
        return _westwardBounds(crs, transform, width, height, hints)
    except Exception:
        logger.warning("Skipped the label longitude range check", exc_info=True)
        return None


def _westwardBounds(
    crs: CRS | str,
    transform: Affine,
    width: int,
    height: int,
    hints: LabelHints,
) -> CrsIssue | None:
    """Varda (as PDS3, ISIS and GDAL) reads map x, and so sample number, as
    increasing eastward. This checks that per file: the longitudes of the
    image's left and right edges against the longitude range its label
    states (PDS3 ``WESTERNMOST``/``EASTERNMOST_LONGITUDE``, ISIS
    ``MinimumLongitude``/``MaximumLongitude``, PDS4 ``west``/
    ``east_bounding_coordinate``). An issue only when the range fits the
    edges read westward (mirrored about the center meridian) and not
    eastward. Only for cylindrical projections, whose longitude depends on x
    alone."""
    bounds = _labelBounds(hints)
    if bounds is None or transform.is_identity or width <= 0 or height <= 0:
        return None
    with PROJ_LOCK:
        parsed = CRS.from_user_input(crs)
        operation = parsed.coordinate_operation
        if operation is None:
            return None
        if operation.method_name not in _CYLINDRICAL:
            return None
        falseEasting = next(
            (float(p.value) for p in operation.params if p.name == "False easting"),
            0.0,
        )
        lonLat = geodeticLonLat(parsed)
    left, y = transform * (0, height / 2)
    right, _ = transform * (width, height / 2)
    step, _ = transform * (1, height / 2)
    mirrored = (2 * falseEasting - left, 2 * falseEasting - right)
    xs = [left, right, *mirrored, step]
    lons, _ = transformPoints(parsed, lonLat, xs, [y] * len(xs))
    if not all(math.isfinite(lon) for lon in lons):
        return None
    # the edges' span from one pixel's (unwrapped, so a global image is seen)
    pixel = abs(_wrapped(lons[4] - lons[0]))
    span = pixel * width
    if span == 0 or span >= _GLOBAL_SPAN_DEG:
        return None
    # the label may give pixel edges, pixel centers or its valid data's
    # extent, rounded: CRISM FRT000107A7's are 1.5 px inside its edges
    tolerance = max(BOUNDS_TOLERANCE_PX * pixel, BOUNDS_TOLERANCE_DEG)
    east = hints.longitudeDirection != "west"
    first, second = (b if east else -b for b in bounds)  # east-positive

    def fits(a: float, b: float) -> bool:
        return any(
            abs(_wrapped(p - first)) <= tolerance
            and abs(_wrapped(q - second)) <= tolerance
            for p, q in ((a, b), (b, a))
        )

    if fits(lons[0], lons[1]) or not fits(lons[2], lons[3]):
        return None
    word = "east" if east else "west"
    low, high = sorted(bounds)
    return CrsIssue(
        "lon-bounds-westward",
        Severity.PROBLEM,
        f"Its label's longitude range ({low:g}° to {high:g}° {word}) fits its map "
        "only if its samples run westward; Varda reads them eastward.",
        "Check a known feature's position: the image may be mirrored about its "
        f"center meridian; if so, {SET_CRS} or fix its label",
    )


# The label's longitude range: PDS3, ISIS, PDS4
_BOUNDS = (
    ("WESTERNMOST_LONGITUDE", "EASTERNMOST_LONGITUDE"),
    ("MINIMUMLONGITUDE", "MAXIMUMLONGITUDE"),
    ("WEST_BOUNDING_COORDINATE", "EAST_BOUNDING_COORDINATE"),
)


# The label's latitude range: PDS3, ISIS, PDS4
_LATITUDES = (
    ("MINIMUM_LATITUDE", "MAXIMUM_LATITUDE"),
    ("MINIMUMLATITUDE", "MAXIMUMLATITUDE"),
    ("SOUTH_BOUNDING_COORDINATE", "NORTH_BOUNDING_COORDINATE"),
)


def _labelLatitudes(hints: LabelHints) -> tuple[float, float] | None:
    return _pairOf(hints, _LATITUDES)


def _labelBounds(hints: LabelHints) -> tuple[float, float] | None:
    return _pairOf(hints, _BOUNDS)


def _pairOf(
    hints: LabelHints, names: tuple[tuple[str, str], ...]
) -> tuple[float, float] | None:
    group = hints.projectionGroup
    for keys in names:
        values = [_number(group.get(key)) for key in keys]
        if values[0] is not None and values[1] is not None:
            return values[0], values[1]
    return None


def _number(text: str | None) -> float | None:
    try:
        value = float((text or "").strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _wrapped(degrees: float) -> float:
    """``degrees`` in [−180, 180)."""
    return (degrees + 180.0) % 360.0 - 180.0


def _earthOnBody(
    crs: CRS | str,
    description: CrsDescription,
    own: BodyMatch,
    hints: LabelHints,
    applyFix: bool,
) -> CrsIssue | None:
    """An Earth CRS on an image whose label names another body: fixed
    automatically when the CRS is just degrees and Varda knows the body
    (offered instead when ``applyFix`` is False: the user kept it). A
    target that names no body (SKY, CALIBRATION, N/A; ``NON_BODY_TARGETS``)
    is no evidence, so it's ignored."""
    target = hints.target
    if own.body is None or own.body.name != "Earth" or not target:
        return None
    if isNonBodyTarget(target):
        return None
    body = bodyNamed(target)
    if body is not None and body.name == "Earth":
        return None
    name = body.name if body is not None else target.title()
    message = f"Its CRS says Earth, but its label says {name}."
    if description.kind != "geographic" or body is None:
        return CrsIssue("earth-crs-on-body", Severity.PROBLEM, message, SET_CRS)
    with PROJ_LOCK:
        parsed = CRS.from_user_input(crs)
        name = parsed.name
        if not name or name.lower() == "unknown":  # e.g. a PROJ string
            name = parsed.datum.name if parsed.datum is not None else "no name"
        wkt = body.sphereCrs().to_wkt()
    fix = CrsFix(
        wkt=wkt,
        reason=(
            f"Its CRS says Earth ({name}) but its label's target is {body.name}; "
            f"read as degrees on the IAU 2015 {body.name} sphere"
        ),
        automatic=applyFix,
    )
    sphere = f"degrees on the IAU 2015 {body.name} sphere"
    if applyFix:
        suggestion = f"Varda reads it as {sphere}; {SET_CRS} to use another"
    else:
        message = f"{message.removesuffix('.')}; you chose to keep the file's CRS."
        suggestion = f"Apply to read it as {sphere}, or {SET_CRS}"
    return CrsIssue("earth-crs-on-body", Severity.PROBLEM, message, suggestion, fix)


def flagged(health: CrsHealth | None) -> bool:
    """Whether ``health`` has a tip or a problem: the image list's dot."""
    return headline(health) is not None


def headline(health: CrsHealth | None) -> str | None:
    """The finding to show first, in one line (the image list's tooltip, the
    status bar): the automatic fix as what Varda read, else the first
    problem, else the first tip; None for nothing (or only info)."""
    if health is None:
        return None
    for severity in (Severity.PROBLEM, Severity.TIP):
        for issue in health.issues:
            if issue.severity is not severity:
                continue
            body = health.body.body
            automatic = issue.fix is not None and issue.fix.automatic
            if issue.code == "earth-crs-on-body" and automatic and body is not None:
                return f"Its CRS says Earth; read as {body.name} (from its label)."
            if issue.code == "no-crs" and automatic:
                return LABEL_FIX_HEADLINE
            return issue.message
    return None


type FixRecord = dict[str, str | bool | list[float]]


def fixRecord(health: CrsHealth, declared: CRS | str | None) -> FixRecord | None:
    """The automatic fix in ``health`` (of the ``declared`` CRS, None for
    none) as provenance records it (``crsFix``): the CRS declared and the
    CRS used (``recordText``; "none" for none), the reason, ``automatic``,
    and for a fix built from the label, ``"source": "label"`` and the
    ``transform`` it sets, if any. ``None`` when there's no automatic fix."""
    issue = next(
        (i for i in health.issues if i.fix is not None and i.fix.automatic), None
    )
    if issue is None or issue.fix is None:
        return None
    fix = issue.fix
    declaredText = "none" if declared is None else health.description.authority
    record: FixRecord = {
        "from": declaredText or recordText(declared or ""),
        "to": recordText(fix.wkt),
        "reason": fix.reason,
        "automatic": True,
    }
    if issue.code in ("no-crs", "label-crs-mismatch"):
        record["source"] = "label"
    if fix.transform is not None:
        record["transform"] = list(fix.transform)
    return record


def recordText(crs: CRS | str) -> str:
    """``crs`` as a ``crsFix`` record writes it: its authority code, else its
    WKT, else (when WKT would lose something, e.g. the west longitude axis
    of a projected CRS built from a west-positive label) its PROJJSON.
    Holds PROJ_LOCK."""
    authority = describeCrs(crs).authority
    if authority is not None:
        return authority
    with PROJ_LOCK:
        parsed = CRS.from_user_input(crs)
        wkt = parsed.to_wkt()
        kept = CRS.from_wkt(wkt)
        if sameCrs(kept, parsed) and _baseAxes(kept) == _baseAxes(parsed):
            return wkt
        return parsed.to_json()


def _baseAxes(crs: CRS) -> set[str]:
    """The directions of ``crs``'s geodetic CRS's axes, in any order
    (``sameCrs`` compares a projected CRS's own axes only)."""
    geodetic = crs.geodetic_crs
    return {axis.direction for axis in geodetic.axis_info} if geodetic else set()


def _unknownBody(body: BodyMatch) -> CrsIssue:
    if body.candidates:
        return CrsIssue(
            "ambiguous-body",
            Severity.TIP,
            f"Its radius alone can't tell which body it's on: {body.evidence}.",
            f"Open it with its label, or {SET_CRS} with the body's own",
        )
    return CrsIssue(
        "unknown-body",
        Severity.TIP,
        f"Varda doesn't know which body it's on: {body.evidence}.",
        f"Measurements use its own surface; {SET_CRS} if it's on a known body",
    )


def _conventions(description: CrsDescription, hints: LabelHints) -> list[CrsIssue]:
    """The label's longitude direction and latitude type against the CRS's."""
    issues = []
    label, own = hints.longitudeDirection, description.longitudeDirection
    if label and own and label != own:
        issues.append(
            CrsIssue(
                "lon-direction-mismatch",
                Severity.PROBLEM,
                f"Its label says longitude increases to the {label}, but its "
                f"CRS says {own}.",
                f"Check a known feature's longitude, then {SET_CRS} if the CRS "
                "is wrong",
            )
        )
    label, own = hints.latitudeType, description.latitudeType
    if label and own and label != own:
        issues.append(
            CrsIssue(
                "latitude-type-mismatch",
                Severity.TIP,
                f"Its label says {label} latitude, but its CRS's latitude is {own}.",
                f"Check which latitude the data use, then {SET_CRS} if the CRS "
                "is wrong",
            )
        )
    return issues


def _distorted(distortion: DistortionSummary) -> CrsIssue:
    if distortion.worst.level == "strong":
        message = (
            f"Shapes and slopes are badly distorted on this map: {distortion.text}."
        )
    else:
        message = f"Its projection distorts the map: {distortion.text}."
    return CrsIssue("high-distortion", Severity.TIP, message, DISTORTION_SUGGESTION)
