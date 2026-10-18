"""The coordinate-system health check run when an image opens (spec §4.2):
what is wrong or worth knowing about an image's CRS, in plain words, with
what to do, and the one fix Varda applies without asking.

The only automatic fix: an Earth *geographic* CRS (just degrees) on an image
whose label names another body Varda knows is read as degrees on that body's
IAU 2015 sphere. Everything else is reported, never changed: Varda can't know
which of the CRS and the label is right."""

from __future__ import annotations

from enum import Enum

import attrs
from affine import Affine
from attrs import frozen
from pyproj import CRS

from varda.geo.bodies import BodyMatch, bodyNamed, identifyBody
from varda.geo.describe import CrsDescription, describeCrs
from varda.geo.distortion import DistortionSummary, distortionOver
from varda.geo.labels import LabelHints
from varda.utilities.proj_lock import PROJ_LOCK

SET_CRS = "Set coordinate system…"
DISTORTION_SUGGESTION = (
    "Measurements are geodesic and unaffected; shapes and slopes on this map "
    "are distorted — reproject (Processing → Reproject Image, coming in a "
    "later version) for figures and terrain"
)
_NO_LABEL = LabelHints.empty()


class Severity(Enum):
    INFO = "info"  # shown in Properties only
    TIP = "tip"  # badge + tooltip
    PROBLEM = "problem"  # badge + tooltip + a status-bar message at load


@frozen
class CrsFix:
    wkt: str  # the CRS to use instead
    reason: str  # one sentence, recorded in provenance
    automatic: bool  # applied at load without asking


@frozen
class CrsIssue:
    # "no-crs", "unreadable-crs", "earth-crs-on-body", "unknown-body",
    # "ambiguous-body", "lon-direction-mismatch", "latitude-type-mismatch",
    # "high-distortion", "lon-0-360" ("label-crs-mismatch" is Phase 2)
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
) -> CrsHealth:
    """What is wrong or worth knowing about an image's ``crs`` (with its
    ``transform`` and size), given what its label says (``hints``) and the
    CRS text its file had when Varda couldn't read it (``rawCrsText``).

    When the automatic fix applies, the convention and distortion checks
    are of the fixed CRS, the one Varda uses. Never raises."""
    try:
        return _check(crs, transform, width, height, hints, rawCrsText)
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
) -> CrsHealth:
    description = describeCrs(crs, transform, width, height)
    own = identifyBody(crs)
    body = own if own.body is not None else identifyBody(crs, hints.target)
    if crs is None or description.kind == "unreadable":
        issue = _missing(crs, transform, hints, rawCrsText)
        return CrsHealth(description, body, (issue,) if issue else (), None)
    issues: list[CrsIssue] = []
    effective, used = crs, description
    earth = _earthOnBody(crs, description, own, hints)
    if earth is not None:
        issues.append(earth)
        if earth.fix is not None and earth.fix.automatic:
            effective = earth.fix.wkt
            used = describeCrs(effective, transform, width, height)
    if used.kind in ("geographic", "projected") and body.body is None:
        issues.append(_unknownBody(body))
    issues.extend(_conventions(used, hints))
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


def _missing(
    crs: CRS | str | None,
    transform: Affine,
    hints: LabelHints,
    rawCrsText: str | None,
) -> CrsIssue | None:
    """No CRS, or one Varda can't read."""
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
    return CrsIssue("no-crs", Severity.PROBLEM, message, SET_CRS)


def _earthOnBody(
    crs: CRS | str,
    description: CrsDescription,
    own: BodyMatch,
    hints: LabelHints,
) -> CrsIssue | None:
    """An Earth CRS on an image whose label names another body: fixed
    automatically when the CRS is just degrees and Varda knows the body."""
    target = hints.target
    if own.body is None or own.body.name != "Earth" or not target:
        return None
    if target.lower() == "earth":
        return None
    message = f"Its CRS says Earth, but its label says {target}."
    body = bodyNamed(target)
    if description.kind != "geographic" or body is None:
        return CrsIssue("earth-crs-on-body", Severity.PROBLEM, message, SET_CRS)
    with PROJ_LOCK:
        name = CRS.from_user_input(crs).name
        wkt = body.sphereCrs().to_wkt()
    fix = CrsFix(
        wkt=wkt,
        reason=(
            f"Its CRS says Earth ({name}) but its label's target is {body.name}; "
            f"read as degrees on the IAU 2015 {body.name} sphere"
        ),
        automatic=True,
    )
    suggestion = (
        f"Varda reads it as degrees on the IAU 2015 {body.name} sphere; "
        f"{SET_CRS} to use another"
    )
    return CrsIssue("earth-crs-on-body", Severity.PROBLEM, message, suggestion, fix)


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
