"""How a Processing-menu result was made: the analysis, its parameters, its
inputs (with their own records), the software and the time, as a provenance
record (``common.provenance``) kept in the result's metadata and its files."""

from __future__ import annotations

from varda.analysis.analysis import Analysis
from varda.common.entities import VardaRaster
from varda.common.parameter import (
    EnumParameter,
    ImageParameter,
    Parameter,
    ParameterGroup,
    Vec2Parameter,
)
from varda.common.provenance import PROVENANCE_KEY as PROVENANCE_KEY
from varda.common.provenance import RESULT_KEYS, makeProvenance, plain, setProvenance
from varda.common.provenance import provenanceText as provenanceText


def parameterValues(group: ParameterGroup) -> dict:
    """Every setting as a methods section quotes it: by the name the dialog
    shows, a choice by its label, a pair by its value names with the unit it
    is shown in (``{"from": 1000.0, "to": 2600.0, "units": "nm"}``)."""
    values = {}
    for param in group.params.values():
        if isinstance(param, ParameterGroup):
            values[type(param).__name__] = parameterValues(param)
        elif isinstance(param, Parameter):
            values[param.name] = parameterValue(param)
    return values


def parameterValue(param: Parameter) -> object:
    if isinstance(param, EnumParameter):
        return param.label()
    if isinstance(param, Vec2Parameter):
        value = param.get()
        first, second = (name.lower() for name in param.valueNames)
        pair: dict = {first: plain(value.x), second: plain(value.y)}
        if param.suffix.strip():
            pair["units"] = param.suffix.strip()
        return pair
    return plain(param.get())


def imageInputs(group: ParameterGroup) -> list[VardaRaster]:
    """The other images an analysis's settings use (weights, a second image)."""
    found = []
    for param in group.params.values():
        if isinstance(param, ParameterGroup):
            found += imageInputs(param)
        elif isinstance(param, ImageParameter) and param.get() is not None:
            found.append(param.get())
    return found


def recordProvenance(
    result: VardaRaster, analysis: Analysis, image: VardaRaster
) -> None:
    inputs = [image]
    inputs += [other for other in imageInputs(analysis) if other is not image]
    setProvenance(
        result,
        makeProvenance(
            analysis.name,
            parameterValues(analysis),
            inputs,
            processId=analysis.analysisId,
            plugin=analysis.plugin,
            software=analysis.softwareVersions(),
            results={
                key: result.extraMetadata[key]
                for key in RESULT_KEYS
                if key in result.extraMetadata
            },
        ),
    )
