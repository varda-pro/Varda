"""The processing recipe: one parameter group per pipeline step, nested in
``Recipe``, with a versioned JSON round trip. Qt-free."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from varda.common.parameter import (
    BoolParameter,
    EnumParameter,
    FloatParameter,
    LabeledEnum,
    Parameter,
    ParameterGroup,
    StringParameter,
)
from varda.crism.calibration import TransmissionSource

RECIPE_VERSION = 2
VERSION_KEY = "recipeVersion"
# Version 1 offered options Phase 1 never implemented. A version-1 recipe
# loads when it sets them to what actually ran (they then change nothing) and
# is rejected, naming the key, otherwise.
_REMOVED_IN_2 = {("atmosphere", "continuumDegree"): 3, ("smile", "method"): "LINEAR"}


class IncidenceSource(LabeledEnum):
    AREOID_POLYNOMIAL = "Areoid incidence, polynomial fit"
    MOLA = "MOLA surface incidence"


class BetaMode(LabeledEnum):
    ELEVATION_PRIOR = "Per pixel, with an elevation prior"
    PER_PIXEL = "Per pixel"


class SmileMethod(LabeledEnum):
    LINEAR = "Linear"


class Resampling(LabeledEnum):
    NEAREST = "Nearest neighbor"
    AREA = "Area weighted"


def _enabled(default: bool, description: str) -> BoolParameter:
    return BoolParameter("Enabled", default, description)


class PhotometryStep(ParameterGroup):
    enabled = _enabled(True, "Divide by the cosine of the incidence angle.")
    incidence = EnumParameter(
        "Incidence angle",
        IncidenceSource,
        IncidenceSource.AREOID_POLYNOMIAL,
        "Where the incidence angle comes from: the areoid angle from the "
        "observation's geometry, or the angle on the MOLA surface.",
    )


class DespikeStep(ParameterGroup):
    enabled = _enabled(True, "Replace isolated spikes in the spectra.")
    threshold = FloatParameter(
        "Threshold",
        5.0,
        (3.0, 10.0),
        "sigma",
        "How far a value must stand out from its neighbors to count as a spike.",
    )


class AtmosphereStep(ParameterGroup):
    enabled = _enabled(True, "Correct the gas absorptions with a fitted transmission.")
    source = EnumParameter(
        "Transmission source",
        TransmissionSource,
        TransmissionSource.PDS_AT,
        "Which atmospheric transmission spectra to choose from.",
    )
    scan = StringParameter(
        "Scan",
        "",
        "Name of a specific scan to use. Leave empty to choose automatically.",
    )
    fitShift = BoolParameter(
        "Fit a spectral shift",
        True,
        "Fit a small wavelength shift of the transmission along with its strength.",
    )
    betaMode = EnumParameter(
        "Strength per pixel",
        BetaMode,
        BetaMode.ELEVATION_PRIOR,
        "How the transmission strength is fitted: for each pixel alone, or "
        "for each pixel with a prior from the surface elevation.",
    )
    artifactModel = BoolParameter(
        "Model instrument artifacts",
        True,
        "Fit the known artifact spectra of the volcano scans. Skipped, with the "
        "reason recorded, when no basis matches the observation's epoch.",
    )


class DestripeStep(ParameterGroup):
    enabled = _enabled(
        False,
        "Remove column striping. Off by default: it changes little on I/F "
        "that is already filtered, and can slightly harm it.",
    )
    splitHalf = BoolParameter(
        "Treat detector halves separately",
        True,
        "Estimate the striping separately for each half of the detector.",
    )


class SmileStep(ParameterGroup):
    enabled = _enabled(True, "Correct the spectral shift across the detector columns.")
    method = EnumParameter(
        "Interpolation",
        SmileMethod,
        SmileMethod.LINEAR,
        "How spectra are resampled onto a common wavelength grid.",
    )
    esc = BoolParameter(
        "Empirical shift correction",
        True,
        "Apply the empirical per-column shift offsets as well.",
    )
    applyFittedShift = BoolParameter(
        "Apply the fitted shift",
        False,
        "Also correct by the wavelength shift fitted in the atmosphere step.",
    )


class ProjectionStep(ParameterGroup):
    enabled = _enabled(True, "Map-project the cube onto a grid.")
    pixelSize = FloatParameter(
        "Pixel size",
        0.0,
        (0.0, 200.0),
        "m",
        "Size of the output pixels. 0 chooses automatically: 18 m, or 36 m for "
        "binned observations.",
    )
    resampling = EnumParameter(
        "Resampling",
        Resampling,
        Resampling.NEAREST,
        "How source pixels are combined into output pixels.",
    )
    snapToLattice = BoolParameter(
        "Snap to the MTRDR lattice",
        False,
        "Align the grid with the standard MTRDR pixel lattice.",
    )
    maxDistanceFactor = FloatParameter(
        "Empty threshold",
        2.0,
        (0.5, 4.0),
        "pixels",
        "Output pixels with no source pixel closer than this many pixels are "
        "left empty (nearest neighbor).",
    )


class OutputStep(ParameterGroup):
    enabled = _enabled(True, "Write the products.")
    sensorSpace = BoolParameter(
        "Also write sensor space",
        False,
        "Also write the cube before map projection.",
    )
    cog = BoolParameter(
        "Cloud Optimized GeoTIFF",
        False,
        "Also write the map-projected I/F as a Cloud Optimized GeoTIFF.",
    )


class Recipe(ParameterGroup):
    photometry = PhotometryStep()
    despike = DespikeStep()
    atmosphere = AtmosphereStep()
    destripe = DestripeStep()
    smile = SmileStep()
    projection = ProjectionStep()
    output = OutputStep()


def _plain(value: object) -> object:
    return value.name if isinstance(value, Enum) else value


def _groupToDict(group: ParameterGroup) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, param in group.params.items():
        if isinstance(param, ParameterGroup):
            values[key] = _groupToDict(param)
        else:
            values[key] = _plain(param.get())
    return values


def recipeToDict(recipe: Recipe) -> dict[str, object]:
    """The recipe's values as JSON-friendly data (enums by member name)."""
    return {VERSION_KEY: RECIPE_VERSION, **_groupToDict(recipe)}


def _setParameter(key: str, param: Parameter, value: object) -> None:
    if isinstance(param, EnumParameter):
        current = param.get()
        assert isinstance(current, Enum)
        members = {member.name: member for member in type(current)}
        if not isinstance(value, str) or value not in members:
            raise ValueError(f"Invalid value {value!r} for recipe key {key!r}")
        param.set(members[value])
    else:
        expected = type(param.default)
        if isinstance(value, bool) != (expected is bool) or not isinstance(
            value, int | float | str
        ):
            raise ValueError(f"Invalid value {value!r} for recipe key {key!r}")
        param.set(float(value) if expected is float else value)


def _fillGroup(group: ParameterGroup, data: Mapping[str, object], path: str) -> None:
    for key in data:
        if key not in group.params:
            raise ValueError(f"Unknown recipe key {path}{key!r}")
    for key, value in data.items():
        param = group.params[key]
        if isinstance(param, ParameterGroup):
            if not isinstance(value, Mapping):
                raise ValueError(f"Recipe key {path}{key!r} must be a mapping")
            _fillGroup(param, {str(k): v for k, v in value.items()}, f"{path}{key}.")
        else:
            _setParameter(f"{path}{key}", param, value)


def recipeFromDict(data: Mapping[str, object]) -> Recipe:
    """A recipe from ``recipeToDict`` data. Missing keys keep their defaults.
    Raises ValueError for a newer version or an unknown key or value."""
    version = data.get(VERSION_KEY)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(f"Recipe has no valid {VERSION_KEY}")
    if version > RECIPE_VERSION:
        raise ValueError(
            f"Recipe version {version} is newer than this Varda supports "
            f"({RECIPE_VERSION})"
        )
    values = {key: v for key, v in data.items() if key != VERSION_KEY}
    if version < 2:
        values = _fromVersion1(values)
    recipe = Recipe()
    _fillGroup(recipe, values, "")
    return recipe


def _fromVersion1(data: Mapping[str, object]) -> dict[str, object]:
    """Version-1 data without the options version 2 removed (see
    ``_REMOVED_IN_2``); a value other than the one that ran raises."""
    values = dict(data)
    for (group, key), ran in _REMOVED_IN_2.items():
        raw = values.get(group)
        if not isinstance(raw, Mapping):
            continue
        section = {str(k): v for k, v in raw.items()}
        if key not in section:
            continue
        if section[key] != ran:
            raise ValueError(
                f"Recipe key {group}.{key!r} = {section[key]!r} is no longer "
                f"offered (it always ran as {ran!r}); remove it or set it to {ran!r}"
            )
        values[group] = {k: v for k, v in section.items() if k != key}
    return values
