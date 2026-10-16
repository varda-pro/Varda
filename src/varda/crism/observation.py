"""Discovery of one CRISM observation's IR and VNIR TRR3 cubes and DDRs."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Self

from attrs import frozen

from varda.crism.labels import readLabel
from varda.crism.products import ProductId
from varda.crism.readers import Ddr, Trr3, readDdr, readTrr3

_LABEL_SUFFIXES = (".xml", ".lbl")  # in order of preference


def _products(
    folder: Path, activity: str, productType: str, recursive: bool
) -> list[tuple[Path, ProductId]]:
    """The label of each product in ``folder`` with this activity and type,
    preferring ``.xml`` to ``.lbl`` for the same product, sorted by name."""
    files = folder.rglob("*") if recursive else folder.glob("*")
    best: dict[tuple[Path, str], tuple[Path, ProductId]] = {}
    for path in sorted(files):
        suffix = path.suffix.lower()
        if suffix not in _LABEL_SUFFIXES:
            continue
        try:
            product = ProductId.parse(path)
        except ValueError:
            continue
        if product.activity != activity or product.productType != productType:
            continue
        key = (path.parent, path.stem.lower())
        held = best.get(key)
        if held is None or suffix == _LABEL_SUFFIXES[0]:
            best[key] = (path, product)
    return sorted(best.values(), key=lambda item: item[0].name.lower())


def _matching(
    candidates: Iterable[tuple[Path, ProductId]], cube: ProductId, sensor: str
) -> Path | None:
    """The first candidate of the cube's observation, counter and macro."""
    for path, product in candidates:
        if (
            product.observationId == cube.observationId
            and product.classType == cube.classType
            and product.counter == cube.counter
            and product.macro == cube.macro
            and product.sensor == sensor
        ):
            return path
    return None


def _findDdr(cube: ProductId, folders: Iterable[Path], recursive: bool) -> Path | None:
    for folder in folders:
        if folder.is_dir():
            found = _matching(
                _products(folder, "DE", "DDR", recursive), cube, cube.sensor
            )
            if found is not None:
                return found
    return None


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
    def fromFolder(cls, folder: Path, counter: int | None = None) -> Self:
        """Search ``folder`` recursively for ``*_if*l_trr3``. Without a
        ``counter``, take the one whose IR cube has the most lines (the
        central one: FRT 07, FRS 01)."""
        folder = Path(folder)
        found = [
            (path, product)
            for path, product in _products(folder, "IF", "TRR", recursive=True)
            if product.sensor == "L"
        ]
        if not found:
            raise FileNotFoundError(f"No IR TRR3 cube (*_if*l_trr3) under {folder}")
        if counter is not None:
            found = [item for item in found if item[1].counter == counter]
            if not found:
                raise FileNotFoundError(
                    f"No IR TRR3 cube with counter {counter:02d} under {folder}"
                )
        # Labels only: the line count needs no pixels.
        path, product = max(found, key=lambda item: readLabel(item[0]).image.lines)
        ddr = _findDdr(product, [folder], recursive=True)
        if ddr is None:
            raise FileNotFoundError(
                f"No IR DDR (*_de*l_ddr*) for {path.name} under {folder}"
            )
        vnir = _matching(_products(folder, "IF", "TRR", True), product, "S")
        vnirDdr = _findDdr(product.replace(sensor="S"), [folder], recursive=True)
        return cls(
            product.observationName,
            product.counter,
            path,
            ddr,
            vnir,
            vnirDdr if vnir is not None else None,
        )

    @classmethod
    def fromFiles(cls, irCube: Path, irDdr: Path | None = None) -> Self:
        """An observation from its IR cube; the DDR is found by name beside
        the cube or in a sibling ``ddr`` folder when not given."""
        irCube = Path(irCube)
        product = ProductId.parse(irCube)
        here = irCube.parent
        siblings = [here, here.parent / "ddr"]
        if irDdr is None:
            irDdr = _findDdr(product, siblings, recursive=False)
            if irDdr is None:
                raise FileNotFoundError(f"No IR DDR found for {irCube.name}")
        vnirProduct = product.replace(sensor="S")
        vnir = _matching(
            [p for f in (here,) for p in _products(f, "IF", "TRR", False)],
            product,
            "S",
        )
        return cls(
            product.observationName,
            product.counter,
            irCube,
            Path(irDdr),
            vnir,
            _findDdr(vnirProduct, siblings, recursive=False) if vnir else None,
        )

    def readIr(self) -> tuple[Trr3, Ddr]:
        return readTrr3(self.irCube), readDdr(self.irDdr)
