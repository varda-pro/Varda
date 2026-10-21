"""Per-plate data on a shape model: a named value per plate, its unit and
optionally its 1σ, tied to one model by its content hash and plate count.
No Qt.

Data made for one resolution of a body never lands on another: same plate
count with another hash (a regenerated or shifted model) is refused as
firmly as another count. The CSV Varda writes carries the hash; ALTWG
plate-ancillary FITS tables carry none, so they are matched by plate count
and the data's provenance says so.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import numpy as np
from astropy.io import fits
from attrs import field, frozen

from varda.shapes.mesh import ShapeModel

# ALTWG table columns that locate a plate rather than hold its value
_LOCATION_COLUMNS = re.compile(
    r"^(facet|plate|id|index|lat|latitude|lon|longitude|rad|radius|x|y|z)(_?id)?$",
    re.IGNORECASE,
)
_SIGMA_COLUMN = re.compile(r"^(sigma|.*_sigma|uncertainty)$", re.IGNORECASE)
_HEADER_COLUMN = re.compile(r"^(?P<name>.*?)(?: \[(?P<unit>[^\]]*)\])?$")
COUNT_ONLY_NOTE = "matched by plate count only: the table names no shape model"


@frozen(eq=False)
class PlateData:
    """``values`` (and ``sigma``, 1σ, when known) per plate of the shape
    model with ``shapeHash`` and ``plateCount``, in ``unit``; ``provenance``
    says how they were made (settings, method, source file)."""

    name: str
    unit: str
    values: np.ndarray
    sigma: np.ndarray | None
    shapeHash: str
    plateCount: int
    provenance: dict = field(factory=dict)


def plateData(
    model: ShapeModel,
    name: str,
    unit: str,
    values: np.ndarray,
    sigma: np.ndarray | None = None,
    provenance: dict | None = None,
) -> PlateData:
    """Data for ``model``'s plates; raises ValueError unless there is one
    value (and sigma) per plate."""
    values = np.asarray(values, np.float64).ravel()
    for array in (values, sigma):
        if array is not None and np.asarray(array).size != model.plateCount:
            raise ValueError(
                f"{name} has {np.asarray(array).size} values; the shape model "
                f"has {model.plateCount} plates."
            )
    return PlateData(
        name,
        unit,
        values,
        None if sigma is None else np.asarray(sigma, np.float64).ravel(),
        model.contentHash,
        model.plateCount,
        dict(provenance or {}),
    )


def attach(model: ShapeModel, data: PlateData) -> None:
    """Raises ValueError, naming both models, unless ``data`` was made for
    ``model`` (its hash and plate count)."""
    if data.shapeHash == model.contentHash and data.plateCount == model.plateCount:
        return
    raise ValueError(
        f"{data.name} was made for another shape model ({data.plateCount} "
        f"plates, hash {data.shapeHash[:8]}…); this one has {model.plateCount} "
        f"plates, hash {model.contentHash[:8]}…."
    )


def writePlateCsv(path: Path, model: ShapeModel, datasets: list[PlateData]) -> None:
    """``datasets`` as CSV: ``#`` lines naming the shape (name, hash, plate
    count) and each dataset's provenance, then one row per plate (0-based
    plate index, each value and its sigma)."""
    for data in datasets:
        attach(model, data)
    columns = ["plate"]
    arrays: list[np.ndarray] = [np.arange(model.plateCount)]
    for data in datasets:
        columns.append(f"{data.name} [{data.unit}]")
        arrays.append(data.values)
        if data.sigma is not None:
            columns.append(f"{data.name} sigma")
            arrays.append(data.sigma)
    provenance = {data.name: data.provenance for data in datasets}
    with Path(path).open("w", encoding="utf-8", newline="") as file:
        file.write(f"# shape: {model.name}\n")
        file.write(f"# shapeHash: {model.contentHash}\n")
        file.write(f"# plates: {model.plateCount}\n")
        file.write(f"# provenance: {json.dumps(provenance, ensure_ascii=False)}\n")
        writer = csv.writer(file)
        writer.writerow(columns)
        for row in zip(*arrays, strict=True):
            writer.writerow([int(row[0])] + [repr(float(value)) for value in row[1:]])


def readPlateCsv(path: Path, model: ShapeModel) -> list[PlateData]:
    """The datasets in a CSV Varda wrote, for ``model``; raises ValueError
    when they were made for another model (or the file isn't one)."""
    with Path(path).open("r", encoding="utf-8") as file:
        lines = file.read().splitlines()
    meta = {}
    while lines and lines[0].startswith("#"):
        key, _, value = lines.pop(0)[1:].partition(":")
        meta[key.strip()] = value.strip()
    if "shapeHash" not in meta or "plates" not in meta or not lines:
        raise ValueError(f"{Path(path).name} isn't a plate data file Varda wrote.")
    provenance = json.loads(meta.get("provenance", "{}"))
    rows = list(csv.reader(lines))
    header, body = (
        rows[0],
        np.array(rows[1:], dtype=np.float64).reshape(-1, len(rows[0])),
    )
    shapeHash, count = meta["shapeHash"], int(meta["plates"])
    datasets: list[PlateData] = []
    order = np.argsort(body[:, 0])
    for column, title in enumerate(header[1:], start=1):
        if (
            title.endswith(" sigma")
            and datasets
            and title == f"{datasets[-1].name} sigma"
        ):
            last = datasets[-1]
            datasets[-1] = PlateData(
                last.name, last.unit, last.values, body[order, column],
                last.shapeHash, last.plateCount, last.provenance,
            )  # fmt: skip
            continue
        match = _HEADER_COLUMN.match(title)
        assert match is not None  # the pattern matches any text
        name, unit = match["name"], match["unit"] or ""
        datasets.append(
            PlateData(
                name,
                unit,
                body[order, column],
                None,
                shapeHash,
                count,
                provenance.get(name, {}),
            )  # fmt: skip
        )
    for data in datasets:
        attach(model, data)
    return datasets


def readAltwgPlateTable(path: Path, model: ShapeModel) -> PlateData:
    """An ALTWG plate-ancillary FITS table's value column (the one that
    isn't a plate's index or location, nor SIGMA) and its SIGMA column, for
    ``model``; raises ValueError when its row count isn't the model's plate
    count or it has no value column. Rows are put in plate order by a
    FACET/PLATE/ID column when there is one."""
    path = Path(path)
    with fits.open(path, memmap=False) as hdus:
        table = next(
            (hdu for hdu in hdus if isinstance(hdu, (fits.BinTableHDU, fits.TableHDU))),
            None,
        )
        if table is None:
            raise ValueError(f"{path.name} has no table.")
        columns = table.columns
        data = table.data
        names = list(columns.names)
        rows = len(data)
        if rows != model.plateCount:
            raise ValueError(
                f"{path.name} has {rows} plates; the shape model has "
                f"{model.plateCount} plates."
            )
        valueName = next(
            (
                n
                for n in names
                if not _LOCATION_COLUMNS.match(n) and not _SIGMA_COLUMN.match(n)
            ),
            None,
        )
        if valueName is None:
            raise ValueError(
                f"{path.name} has no value column (only {', '.join(names)})."
            )
        sigmaName = next((n for n in names if _SIGMA_COLUMN.match(n)), None)
        indexName = next(
            (
                n
                for n in names
                if n.lower() in ("facet", "plate", "id", "plate_id", "facet_id")
            ),
            None,
        )
        order = np.arange(rows)
        if indexName is not None:
            order = np.argsort(np.asarray(data[indexName]))
        values = np.asarray(data[valueName], np.float64)[order]
        sigma = (
            None
            if sigmaName is None
            else np.asarray(data[sigmaName], np.float64)[order]
        )
        unit = columns[valueName].unit or ""
        # the cards some products use to name their shape model, kept for the record
        named = {
            key: str(table.header[key])
            for key in ("SHAPEMOD", "OBJ_FILE", "SHAPE")
            if key in table.header
        }
    provenance = {"source": path.name, "column": valueName, "match": COUNT_ONLY_NOTE}
    provenance |= named
    return plateData(model, valueName, unit, values, sigma, provenance)
