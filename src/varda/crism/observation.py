"""Discovery of one CRISM observation's IR and VNIR TRR3 cubes and DDRs."""

from __future__ import annotations

import logging
from pathlib import Path

from attrs import frozen

from varda.crism.labels import readLabel
from varda.crism.products import ProductId
from varda.crism.readers import Ddr, Trr3, readDdr, readTrr3

_log = logging.getLogger(__name__)
_LABEL_SUFFIXES = (".xml", ".lbl")  # in order of preference


Found = list[tuple[Path, ProductId]]


def _scan(folder: Path, recursive: bool) -> Found:
    """Every IF (TRR) and DE (DDR) product label in ``folder``, preferring
    ``.xml`` to ``.lbl`` for the same product, sorted by name."""
    if not folder.is_dir():
        return []
    best: dict[tuple[Path, str], tuple[Path, ProductId]] = {}
    for path in folder.rglob("*") if recursive else folder.glob("*"):
        suffix = path.suffix.lower()
        if suffix not in _LABEL_SUFFIXES:
            continue
        try:
            product = ProductId.parse(path)
        except ValueError:
            continue
        if (product.activity, product.productType) not in (
            ("IF", "TRR"),
            ("DE", "DDR"),
        ):
            continue
        key = (path.parent, path.stem.lower())
        held = best.get(key)
        if held is None or suffix == _LABEL_SUFFIXES[0]:
            best[key] = (path, product)
    return sorted(best.values(), key=lambda item: str(item[0]).lower())


def _find(found: Found, cube: ProductId, activity: str, sensor: str) -> Path | None:
    """The first product of the cube's observation, counter and macro."""
    for path, product in found:
        if (
            product.activity == activity
            and product.sensor == sensor
            and product.classType == cube.classType
            and product.observationId == cube.observationId
            and product.counter == cube.counter
            and product.macro == cube.macro
        ):
            return path
    return None


def _build(found: Found, path: Path, product: ProductId) -> CrismObservation | None:
    """The observation of an IR cube, or None when it has no IR DDR."""
    irDdr = _find(found, product, "DE", "L")
    if irDdr is None:
        return None
    vnir = _find(found, product, "IF", "S")
    vnirDdr = _find(found, product, "DE", "S") if vnir is not None else None
    return CrismObservation(
        product.observationName, product.counter, path, irDdr, vnir, vnirDdr
    )


def _irCubes(found: Found) -> Found:
    return [(p, i) for p, i in found if i.activity == "IF" and i.sensor == "L"]


def _central(cubes: Found) -> tuple[Path, ProductId]:
    """The cube with the most lines (labels only, no pixels)."""
    return max(cubes, key=lambda item: readLabel(item[0]).image.lines)


@frozen
class CrismObservation:
    """The files of one observation. The VNIR pair is optional."""

    name: str
    counter: int
    irCube: Path
    irDdr: Path
    vnirCube: Path | None = None
    vnirDdr: Path | None = None

    @classmethod
    def fromFolder(cls, folder: Path, counter: int | None = None) -> CrismObservation:
        """The one observation under ``folder`` (searched recursively). Without
        a ``counter``, the one whose IR cube has the most lines (the central
        one: FRT 07, FRS 01). A folder of several observations is an error:
        use ``discover``."""
        folder = Path(folder)
        found = _scan(folder, recursive=True)
        cubes = _irCubes(found)
        if not cubes:
            raise FileNotFoundError(f"No IR TRR3 cube (*_if*l_trr3) under {folder}")
        names = sorted({i.observationName for _, i in cubes})
        if len(names) > 1:
            raise ValueError(
                f"{folder} holds {len(names)} observations ({', '.join(names)}); "
                "use CrismObservation.discover to get them all"
            )
        if counter is not None:
            cubes = [item for item in cubes if item[1].counter == counter]
            if not cubes:
                raise FileNotFoundError(
                    f"No IR TRR3 cube with counter {counter:02d} under {folder}"
                )
        path, product = _central(cubes)
        observation = _build(found, path, product)
        if observation is None:
            raise FileNotFoundError(
                f"No IR DDR (*_de*l_ddr*) for {path.name} under {folder}"
            )
        return observation

    @classmethod
    def discover(cls, folder: Path) -> list[CrismObservation]:
        """One observation (its central counter) per observation under
        ``folder``, sorted by name. Observations without an IR DDR are
        skipped; VNIR-only ones have no IR cube and are not found."""
        found = _scan(Path(folder), recursive=True)
        byObservation: dict[str, Found] = {}
        for path, product in _irCubes(found):
            byObservation.setdefault(product.observationName, []).append(
                (path, product)
            )
        observations: list[CrismObservation] = []
        for name in sorted(byObservation):
            path, product = _central(byObservation[name])
            observation = _build(found, path, product)
            if observation is None:
                _log.info("Skipping %s: no IR DDR for %s", name, path.name)
            else:
                observations.append(observation)
        return observations

    @classmethod
    def fromFiles(cls, irCube: Path, irDdr: Path | None = None) -> CrismObservation:
        """An observation from its IR cube; the DDR is found by name beside
        the cube or in a sibling ``ddr`` folder when not given."""
        irCube = Path(irCube)
        product = ProductId.parse(irCube)
        found = _scan(irCube.parent, False) + _scan(irCube.parent.parent / "ddr", False)
        observation = _build(found, irCube, product)
        if irDdr is None:
            if observation is None:
                raise FileNotFoundError(f"No IR DDR found for {irCube.name}")
            return observation
        vnir = _find(found, product, "IF", "S")
        vnirDdr = _find(found, product, "DE", "S") if vnir is not None else None
        return cls(
            product.observationName, product.counter, irCube, Path(irDdr), vnir, vnirDdr
        )

    def readIr(self) -> tuple[Trr3, Ddr]:
        return readTrr3(self.irCube), readDdr(self.irDdr)
