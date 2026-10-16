from pathlib import Path

import numpy as np

from varda.crism.readers import readDetectorFrame, readTrr3

_FRS = "data-for-paper/FRS00031442/trdr/frs00031442_01_if168l_trr3.lbl"
_WA = "data-for-paper/ATO0004EC45/CRISM_CDR/wa/cdr490947778566_wa0000000l_3.lbl"


def realChunk(
    demoData: Path, lines: slice = slice(0, 64)
) -> tuple[np.ndarray, np.ndarray]:
    """Lines of the real FRS00031442 IR TRR3 ``(l, 640, 438)`` (NaN where invalid,
    ascending wavelength) and its ``(640, 438)`` per-column wavelengths in nm."""
    trr = readTrr3(demoData / _FRS)
    wavelengths = readDetectorFrame(demoData / _WA).alignedTo(trr.detectorRows)
    return trr.lines(lines), wavelengths
