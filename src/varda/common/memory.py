"""Whether a job's memory fits the machine, checked before it starts.

An analysis that needs more memory than is free makes the system swap; one
that needs more than is installed can make the operating system end other
programs (it did, on a 16 GB laptop, before the FATT map was bounded). The
check warns and lets the user decide; it never refuses. Qt-free.
"""

from __future__ import annotations

from enum import Enum

import psutil

# Over this share of the free memory, a job is tight: other programs, and the
# estimate's own error, need the rest
TIGHT_SHARE = 0.8


class MemoryFit(Enum):
    FITS = "fits"
    TIGHT = "tight"  # over TIGHT_SHARE of the free memory
    EXCEEDS_TOTAL = "exceeds total"  # more than the machine has installed


def availableMemory() -> int:
    """Bytes free for a new job now (without swapping)."""
    return int(psutil.virtual_memory().available)


def totalMemory() -> int:
    """Bytes of memory installed."""
    return int(psutil.virtual_memory().total)


def assessMemory(estimate: int, available: int, total: int) -> MemoryFit:
    if estimate > total:
        return MemoryFit.EXCEEDS_TOTAL
    if estimate > TIGHT_SHARE * available:
        return MemoryFit.TIGHT
    return MemoryFit.FITS


def formatBytes(size: float) -> str:
    """Memory as people say it: 16 GB of RAM is 16 × 2³⁰ bytes."""
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} GB".replace(".0 GB", " GB")


def memoryWarning(
    jobName: str, estimate: int, available: int, total: int
) -> tuple[str, str]:
    """The title and text of the warning for a job that doesn't fit."""
    need, free, installed = (formatBytes(b) for b in (estimate, available, total))
    if estimate > total:
        text = (
            f"{jobName} may need about {need} of memory, more than this computer "
            f"has ({installed}). Running it may freeze the computer or make it "
            "close other programs, or crash Varda."
        )
    else:
        text = (
            f"{jobName} may need about {need} of memory; {free} is free now. "
            "It may run slowly, and other programs may slow down or close."
        )
    text += (
        "\n\nTo use less, crop or subset the image (fewer pixels or bands) "
        "first, or close other programs. The estimate is an upper bound."
    )
    return f"{jobName}: memory", text
