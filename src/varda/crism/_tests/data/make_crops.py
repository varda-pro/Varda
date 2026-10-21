"""Make the small real-data crops the CRISM unit tests read.

Run once by hand (the crops are committed):

    VARDA_DEMO_DATA=/path/to/varda-demo-data uv run python src/varda/crism/_tests/data/make_crops.py

Reads from ``$VARDA_DEMO_DATA/data-for-paper`` and writes beside this script.
"""

import os
import re
import shutil
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
LINES = (80, 95)  # inclusive, zero based
SAMPLES = (260, 379)  # inclusive, zero based
FRAME_SAMPLES = 640
BANDS = 438
ROWNUM_BYTES = BANDS * 2

TRR3 = "FRS00031442/trdr/frs00031442_01_if168l_trr3"
DDR = "FRS00031442/ddr/frs00031442_01_de168l_ddr1"
CDRS = (
    "ATO0004EC45/CRISM_CDR/wa/cdr490947778566_wa0000000l_3",
    "ATO0004EC45/CRISM_CDR/at/cdr420862848019_at0000000l_7",
)
MTRDR = (
    "FRT00005850/mtrdr/frt00005850_07_if167j_mtr3.hdr",
    "FRT00005850/mtrdr/frt00005850_07_if167j_mtr3.lbl",
    "FRT00005850/mtrdr/frt00005850_07_wv167j_mtr3.tab",
)


def sub(pattern: str, replacement: str, text: str) -> str:
    """Replace exactly one match of ``pattern``; a silent miss is a bug."""
    result, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    assert count == 1, f"label anchor not found: {pattern}"
    return result


def cropLabel(
    text: str,
    product: str,
    lines: int,
    recordBytes: int,
    fileRecords: int,
    rownumPointer: int | None,
) -> str:
    text = sub(r"^(\s*LINES\s*=\s*)\d+", rf"\g<1>{lines}", text)
    text = sub(
        r"^(\s*LINE_SAMPLES\s*=\s*)\d+", rf"\g<1>{SAMPLES[1] - SAMPLES[0] + 1}", text
    )
    text = sub(r"^(\s*RECORD_BYTES\s*=\s*)\d+", rf"\g<1>{recordBytes}", text)
    text = sub(r"^(\s*FILE_RECORDS\s*=\s*)\d+", rf"\g<1>{fileRecords}", text)
    if rownumPointer is not None:
        text = sub(
            r'^(\s*\^ROWNUM_TABLE\s*=\s*\(\s*"[^"]+"\s*,\s*)\d+',
            rf"\g<1>{rownumPointer}",
            text,
        )
    window = f"lines {LINES[0]}-{LINES[1]}, " if lines > 1 else ""
    note = f"/* Varda test crop: {window}samples {SAMPLES[0]}-{SAMPLES[1]} of {product} */\n"
    first, rest = text.split("\n", 1)
    assert first.startswith("PDS_VERSION_ID"), (
        "label does not start with PDS_VERSION_ID"
    )
    return first + "\n" + note + rest


def padTo(data: bytes, recordBytes: int) -> bytes:
    return data + bytes(-len(data) % recordBytes)


def cropFrame(
    source: Path, lines: tuple[int, int], bands: int, bil: bool, tableBytes: int
) -> tuple[bytes, bytes]:
    """The cropped image bytes and the trailing table bytes of a PDS3 float32 image."""
    allLines = (source.stat().st_size - tableBytes) // (bands * FRAME_SAMPLES * 4)
    raw = np.memmap(source, dtype="<f4", mode="r", shape=None)
    imageValues = allLines * bands * FRAME_SAMPLES
    table = raw[imageValues:].tobytes()[:tableBytes]
    if bil:
        cube = raw[:imageValues].reshape(allLines, bands, FRAME_SAMPLES)
        crop = cube[lines[0] : lines[1] + 1, :, SAMPLES[0] : SAMPLES[1] + 1]
    else:
        cube = raw[:imageValues].reshape(bands, allLines, FRAME_SAMPLES)
        crop = cube[:, lines[0] : lines[1] + 1, SAMPLES[0] : SAMPLES[1] + 1]
    return np.ascontiguousarray(crop).astype("<f4").tobytes(), table


def writeCrop(
    root: Path,
    stem: str,
    lines: tuple[int, int],
    bands: int,
    bil: bool,
    hasTable: bool,
) -> None:
    source = root / f"{stem}.img"
    label = (root / f"{stem}.lbl").read_text(encoding="latin-1")
    name = Path(stem).name
    nLines = lines[1] - lines[0] + 1
    recordBytes = (SAMPLES[1] - SAMPLES[0] + 1) * 4
    image, table = cropFrame(source, lines, bands, bil, ROWNUM_BYTES if hasTable else 0)
    payload = padTo(image + table, recordBytes)
    cropLabelText = cropLabel(
        label,
        name.upper(),
        nLines,
        recordBytes,
        len(payload) // recordBytes,
        nLines * bands + 1 if hasTable else None,
    )
    (HERE / f"{name}.img").write_bytes(payload)
    (HERE / f"{name}.lbl").write_text(cropLabelText, encoding="latin-1")


def main() -> None:
    root = Path(os.environ["VARDA_DEMO_DATA"]) / "data-for-paper"
    writeCrop(root, TRR3, LINES, BANDS, bil=True, hasTable=True)
    writeCrop(root, DDR, LINES, 14, bil=False, hasTable=False)
    for cdr in CDRS:
        writeCrop(root, cdr, (0, 0), BANDS, bil=True, hasTable=True)
    for relative in MTRDR:
        shutil.copyfile(root / relative, HERE / Path(relative).name)


if __name__ == "__main__":
    main()
