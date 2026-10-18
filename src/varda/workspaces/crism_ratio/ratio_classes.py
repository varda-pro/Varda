"""The classes of what the CRISM Ratio workspace saves: a short class name
from a library spectrum, and a color for each new class."""

from __future__ import annotations

import re
from collections.abc import Collection

from varda.common.entities import Color
from varda.spectral_libraries.spectrum import LibrarySpectrum

# the class of every saved denominator ROI
DENOMINATOR_CLASS = "Denominator"
# light gray: bland regions stand apart from every mineral class
DENOMINATOR_CLASS_COLOR = "#d9d9d9"
# Tableau 10, without its green and purple (too close to the green numerator
# box and the magenta denominator box) and its gray (the Denominator class)
CLASS_COLORS = (
    "#4e79a7",  # blue
    "#f28e2b",  # orange
    "#e15759",  # red
    "#76b7b2",  # teal
    "#edc948",  # yellow
    "#ff9da7",  # pink
    "#9c755f",  # brown
)
ROI_ALPHA = "80"  # half opaque, like the boxes

# metadata keys that name what a library spectrum is, in order of preference
CLASS_KEYS = ("mineral", "class", "category")
# Fo91, An60, En45: kept in a short name (a composition, not a sample ID)
_COMPOSITION = re.compile(r"(Fo|Fa|En|Fs|Wo|An|Ab|Or)\d+")


def roiColor(hexColor: str) -> Color:
    return Color.fromHexString(hexColor + ROI_ALPHA)


def nextClassColor(used: Collection[str]) -> str:
    """The first of :data:`CLASS_COLORS` not in ``used`` (hex colors, any
    case, with or without alpha); after all are used, they repeat."""
    taken = {c.lower()[:7] for c in used}
    free = [c for c in CLASS_COLORS if c not in taken]
    return free[0] if free else CLASS_COLORS[len(taken) % len(CLASS_COLORS)]


def shortName(name: str) -> str:
    """The words of ``name`` (split at spaces and underscores) up to the
    first sample-ID-like word: one with a digit or a period, such as
    ``GDS70.d`` or ``165um``. A composition (``Fo91``, ``An60``) is kept.
    The whole name when its first word is already an ID."""
    words = [w for w in re.split(r"[\s_]+", name.strip()) if w]
    kept = []
    for word in words:
        if re.search(r"[\d.]", word) and not _COMPOSITION.fullmatch(word):
            break
        kept.append(word)
    return " ".join(kept) or name.strip()


def suggestedClass(spectrum: LibrarySpectrum) -> str:
    """The spectrum's mineral, class or category from its metadata, else
    :func:`shortName` of its name."""
    fields = {str(k).lower(): v for k, v in spectrum.provenance.items()}
    for key in CLASS_KEYS:
        value = str(fields.get(key, "")).strip()
        if value:
            return value
    return shortName(spectrum.name)
