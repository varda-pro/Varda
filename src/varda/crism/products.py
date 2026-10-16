"""CRISM product names (DPSIS section 2.3.4): the observation products
(TRDR, DDR, MTRDR), the calibration files (CDR) and the volcano-scan ADRs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Self

from attrs import evolve, frozen

_EXTENSION = r"(?:\.[A-Za-z0-9]+)?"

_PRODUCT = re.compile(
    r"(?P<classType>[a-z]{3})(?P<observationId>[0-9a-f]{8})_(?P<counter>\d{2})"
    r"_(?P<activity>[a-z]{2})(?P<macro>\d{3})(?P<sensor>[a-z])"
    r"_(?P<productType>[a-z]{3})(?P<version>\w)" + _EXTENSION,
    re.IGNORECASE,
)
_CDR4 = re.compile(
    r"CDR4(?P<partition>\d)(?P<sclk>\d{10})_(?P<kind>[A-Z]{2})(?P<frameRate>\d)"
    r"(?P<binning>\d)(?P<exposure>\d{3})(?P<wavelengthFilter>\d)(?P<side>\d)"
    r"(?P<sensor>[A-Z])_(?P<version>\d+)" + _EXTENSION,
    re.IGNORECASE,
)
_CDR6 = re.compile(
    r"CDR6_(?P<partition>\d)_(?P<sclk>\d{10})_(?P<kind>[A-Z]{2})"
    r"_(?P<sensor>[A-Z])_(?P<version>\d+)" + _EXTENSION,
    re.IGNORECASE,
)
_VS_ADR = re.compile(
    r"ADR(?P<partition>\d)(?P<sclk>\d{10})_(?P<scanId>[0-9A-F]{5})"
    r"_VS(?P<binning>\d)(?P<wavelengthFilter>\d)(?P<sensor>[A-Z])_(?P<version>\d+)"
    + _EXTENSION,
    re.IGNORECASE,
)


def _fields(pattern: re.Pattern[str], name: str | Path, what: str) -> dict[str, str]:
    base = Path(name).name
    match = pattern.fullmatch(base)
    if match is None:
        raise ValueError(f"Not a CRISM {what} name: {base!r}")
    return match.groupdict()


@frozen
class ProductId:
    """An observation product, e.g. ``frs00031442_01_if168l_trr3``."""

    classType: str
    observationId: int
    counter: int
    activity: str
    macro: int
    sensor: str
    productType: str
    version: str

    @classmethod
    def parse(cls, name: str | Path) -> Self:
        f = _fields(_PRODUCT, name, "product")
        return cls(
            classType=f["classType"].upper(),
            observationId=int(f["observationId"], 16),
            counter=int(f["counter"]),
            activity=f["activity"].upper(),
            macro=int(f["macro"]),
            sensor=f["sensor"].upper(),
            productType=f["productType"].upper(),
            version=f["version"],
        )

    @property
    def observationName(self) -> str:
        return f"{self.classType}{self.observationId:08X}"

    @property
    def stem(self) -> str:
        return (
            f"{self.observationName}_{self.counter:02d}_{self.activity}"
            f"{self.macro:03d}{self.sensor}_{self.productType}{self.version}"
        ).lower()

    def replace(self, **changes: str | int) -> Self:
        return evolve(self, **changes)


@frozen
class CdrId:
    """A calibration data record: CDR4 (per-frame-geometry products) or CDR6
    (lookup tables). Fields a CDR6 name lacks are None."""

    level: int
    partition: int
    sclk: int
    kind: str
    frameRate: int | None
    binning: int | None
    exposure: int | None
    wavelengthFilter: int | None
    side: int | None
    sensor: str
    version: int

    @classmethod
    def parse(cls, name: str | Path) -> Self:
        base = Path(name).name
        if _CDR6.fullmatch(base):
            f = _fields(_CDR6, base, "CDR")
            return cls(
                level=6,
                partition=int(f["partition"]),
                sclk=int(f["sclk"]),
                kind=f["kind"].upper(),
                frameRate=None,
                binning=None,
                exposure=None,
                wavelengthFilter=None,
                side=None,
                sensor=f["sensor"].upper(),
                version=int(f["version"]),
            )
        f = _fields(_CDR4, base, "CDR")
        return cls(
            level=4,
            partition=int(f["partition"]),
            sclk=int(f["sclk"]),
            kind=f["kind"].upper(),
            frameRate=int(f["frameRate"]),
            binning=int(f["binning"]),
            exposure=int(f["exposure"]),
            wavelengthFilter=int(f["wavelengthFilter"]),
            side=int(f["side"]),
            sensor=f["sensor"].upper(),
            version=int(f["version"]),
        )


@frozen
class VsAdrId:
    """A volcano-scan ADR, e.g. ``ADR50924300803_28D04_VS00L_9``."""

    partition: int
    sclk: int
    scanId: str
    binning: int
    wavelengthFilter: int
    sensor: str
    version: int

    @classmethod
    def parse(cls, name: str | Path) -> Self:
        f = _fields(_VS_ADR, name, "volcano-scan ADR")
        return cls(
            partition=int(f["partition"]),
            sclk=int(f["sclk"]),
            scanId=f["scanId"].upper(),
            binning=int(f["binning"]),
            wavelengthFilter=int(f["wavelengthFilter"]),
            sensor=f["sensor"].upper(),
            version=int(f["version"]),
        )
