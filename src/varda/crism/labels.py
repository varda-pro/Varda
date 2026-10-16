"""Reads CRISM PDS3 (``.lbl``, via pvl) and PDS4 (``.xml``) labels into one model.

Only the data file holding the image (and its row-number table) is described;
other files a label merely names are never opened.
"""

import warnings
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

import numpy as np
import pvl
from attrs import define, field
from pvl.collections import Quantity

type LabelValue = str | int | float | list[LabelValue]
type Interleave = Literal["BIL", "BSQ", "BIP"]

_PDS3_INTERLEAVE: dict[str, Interleave] = {
    "LINE_INTERLEAVED": "BIL",
    "BAND_SEQUENTIAL": "BSQ",
    "SAMPLE_INTERLEAVED": "BIP",
}
_PDS4_INTERLEAVE: dict[tuple[str, ...], Interleave] = {
    ("Line", "Band", "Sample"): "BIL",
    ("Band", "Line", "Sample"): "BSQ",
    ("Line", "Sample", "Band"): "BIP",
}
_PDS3_DTYPES = {
    "PC_REAL": "<f", "IEEE_REAL": ">f", "MSB_INTEGER": ">i", "LSB_INTEGER": "<i",
    "MSB_UNSIGNED_INTEGER": ">u", "LSB_UNSIGNED_INTEGER": "<u",
    "PC_INTEGER": "<i", "PC_UNSIGNED_INTEGER": "<u", "SUN_INTEGER": ">i",
    "SUN_UNSIGNED_INTEGER": ">u", "MAC_INTEGER": ">i", "MAC_UNSIGNED_INTEGER": ">u",
}  # fmt: skip
_PDS4_DTYPES = {
    "IEEE754LSBSingle": "<f4", "IEEE754MSBSingle": ">f4",
    "IEEE754LSBDouble": "<f8", "IEEE754MSBDouble": ">f8",
    "SignedLSB2": "<i2", "SignedMSB2": ">i2", "UnsignedLSB2": "<u2",
    "UnsignedMSB2": ">u2", "SignedLSB4": "<i4", "SignedMSB4": ">i4",
    "UnsignedLSB4": "<u4", "UnsignedMSB4": ">u4", "UnsignedByte": "u1",
    "SignedByte": "i1",
}  # fmt: skip


@define(frozen=True)
class ArrayLayout:
    """Where an image cube lives in its data file and how it is laid out."""

    dataFile: Path
    offset: int
    lines: int
    samples: int
    bands: int
    interleave: Interleave
    dtype: np.dtype
    rownumOffset: int | None = None

    def memmap(self) -> np.ndarray:
        """The cube in stored axis order: BIL (line, band, sample), BSQ (band,
        line, sample), BIP (line, sample, band)."""
        shape = {
            "BIL": (self.lines, self.bands, self.samples),
            "BSQ": (self.bands, self.lines, self.samples),
            "BIP": (self.lines, self.samples, self.bands),
        }[self.interleave]
        return np.memmap(
            self.dataFile, dtype=self.dtype, mode="r", offset=self.offset, shape=shape
        )


@define(frozen=True)
class ProductLabel:
    path: Path
    keywords: dict[str, LabelValue]
    image: ArrayLayout
    bandNames: tuple[str, ...] = field(default=())

    def get(self, key: str, default: LabelValue | None = None) -> LabelValue | None:
        return self.keywords.get(key, default)


def readLabel(path: Path) -> ProductLabel:
    """Read a ``.lbl``/``.LBL`` (PDS3) or ``.xml`` (PDS4) label."""
    path = Path(path)
    if path.suffix.lower() == ".xml":
        return _readPds4(path)
    return _readPds3(path)


def _findFile(folder: Path, name: str) -> Path:
    """Resolve a data file name case-insensitively within the label's folder."""
    lowered = name.lower()
    for candidate in folder.iterdir():
        if candidate.name.lower() == lowered:
            return candidate
    return folder / name


def _normalize(value: object) -> LabelValue:
    if isinstance(value, Quantity):
        return _normalize(value.value)
    if isinstance(value, (set, frozenset, list, tuple)):
        items = list(value)
        if isinstance(value, (set, frozenset)):
            items.sort(key=str)
        return [_normalize(item) for item in items]
    if isinstance(value, (bool, int, float, str)):
        return value if not isinstance(value, bool) else int(value)
    return str(value)


def _pointer(value: object) -> tuple[str, int]:
    """A PDS3 ``^`` pointer as (file name, 0-based record).

    Supports ``"FILE"`` and ``("FILE", record)``; other forms raise ValueError.
    """
    if isinstance(value, str):
        return value, 0
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    ):
        return value[0], value[1] - 1
    raise ValueError(f"unsupported PDS3 pointer: {value!r}")


def _readPds3(path: Path) -> ProductLabel:
    with warnings.catch_warnings():
        # pvl itself builds its deprecated Units object when decoding <UNIT> values.
        warnings.filterwarnings(
            "ignore", message="The pvl.collections.Units object is deprecated"
        )
        warnings.filterwarnings(
            "ignore", message=r"the \(type, exc, tb\) signature of throw\(\)"
        )
        label = pvl.load(path)
    fileObject = next(
        (obj for name, obj in label.items() if name == "FILE" and "^IMAGE" in obj),
        None,
    )
    if fileObject is None:
        raise ValueError(f"{path.name}: no FILE object holds an IMAGE")
    image = fileObject["IMAGE"]
    recordBytes = int(fileObject.get("RECORD_BYTES", 0))
    dataName, imageRecord = _pointer(fileObject["^IMAGE"])
    rownumOffset: int | None = None
    if "^ROWNUM_TABLE" in fileObject:
        rownumOffset = _pointer(fileObject["^ROWNUM_TABLE"])[1] * recordBytes
    kind = _PDS3_DTYPES[str(image["SAMPLE_TYPE"])]
    dtype = np.dtype(f"{kind}{int(image['SAMPLE_BITS']) // 8}")
    names = _normalize(image.get("BAND_NAME"))
    layout = ArrayLayout(
        dataFile=_findFile(path.parent, dataName),
        offset=imageRecord * recordBytes,
        lines=int(image["LINES"]),
        samples=int(image["LINE_SAMPLES"]),
        bands=int(image.get("BANDS", 1)),
        interleave=_PDS3_INTERLEAVE[
            str(image.get("BAND_STORAGE_TYPE", "BAND_SEQUENTIAL"))
        ],
        dtype=dtype,
        rownumOffset=rownumOffset,
    )
    keywords = {
        name: _normalize(value)
        for name, value in label.items()
        if name != "FILE" and not isinstance(value, pvl.PVLModule | pvl.PVLGroup)
    }
    bandNames = tuple(str(n) for n in names) if isinstance(names, list) else ()
    return ProductLabel(path, keywords, layout, bandNames)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next((c for c in element if _local(c.tag) == name), None)


def _text(element: ElementTree.Element, name: str) -> str:
    child = _child(element, name)
    if child is None or child.text is None:
        raise ValueError(f"PDS4 label: <{name}> is missing")
    return child.text.strip()


def _axes(array: ElementTree.Element) -> dict[str, int]:
    return {
        _text(axis, "axis_name"): int(_text(axis, "elements"))
        for axis in array
        if _local(axis.tag) == "Axis_Array"
    }


def _readPds4(path: Path) -> ProductLabel:
    root = ElementTree.parse(path).getroot()
    fileArea = next(
        (
            area
            for area in root
            if _local(area.tag) == "File_Area_Observational"
            and any(_local(c.tag).startswith("Array_") for c in area)
        ),
        None,
    )
    if fileArea is None:
        raise ValueError(f"{path.name}: no File_Area_Observational with an array")
    fileElement = _child(fileArea, "File")
    if fileElement is None:
        raise ValueError(f"{path.name}: File_Area_Observational has no File")
    dataName = _text(fileElement, "file_name")
    arrays = [c for c in fileArea if _local(c.tag).startswith("Array_")]
    first = arrays[0]
    elementArray = _child(first, "Element_Array")
    if elementArray is None:
        raise ValueError(f"{path.name}: array has no Element_Array")
    dtype = np.dtype(_PDS4_DTYPES[_text(elementArray, "data_type")])
    names: tuple[str, ...] = ()
    if _local(first.tag) == "Array_3D_Image":
        axes = _axes(first)
        order = [
            _text(axis, "axis_name")
            for axis in sorted(
                (a for a in first if _local(a.tag) == "Axis_Array"),
                key=lambda a: int(_text(a, "sequence_number")),
            )
        ]
        interleave = _PDS4_INTERLEAVE[tuple(order)]
        bands = axes["Band"]
    else:  # concatenated 2D backplanes, as in the DDR
        axes = _axes(first)
        interleave: Interleave = "BSQ"
        bands = len(arrays)
        names = tuple(_text(a, "name") for a in arrays)
    missing = next(
        (
            _text(c, "missing_constant")
            for c in first.iter()
            if _local(c.tag) == "Special_Constants"
            and _child(c, "missing_constant") is not None
        ),
        None,
    )
    rownumOffset: int | None = None
    for other in fileArea:
        if _local(other.tag).startswith("Table_") and "row" in _text(
            other, "local_identifier"
        ):
            rownumOffset = int(_text(other, "offset"))
    keywords: dict[str, LabelValue] = {}
    if missing is not None:
        keywords["MISSING_CONSTANT"] = float(missing)
    layout = ArrayLayout(
        dataFile=_findFile(path.parent, dataName),
        offset=int(_text(first, "offset")),
        lines=axes["Line"],
        samples=axes["Sample"],
        bands=bands,
        interleave=interleave,
        dtype=dtype,
        rownumOffset=rownumOffset,
    )
    return ProductLabel(path, keywords, layout, names)
