"""Standard browse products: labeling and ordering the browse images a
catalog offers for a product (``Catalog.browseImages``), so every catalog
names them alike ("Infrared false color", "Fe mineralogy") and lists the
most useful first."""

from __future__ import annotations

import re
from collections.abc import Iterable

from varda.catalogs.catalog import BrowseImage

# Words written as they are spelled, whatever case the catalog uses: acronyms,
# instrument names, chemical formulas and element symbols standing as words
# ("PHYLLOSILICATE WITH FE AND MG")
SPELLINGS: dict[str, str] = {
    word.upper(): word
    for word in (
        "IR", "NIR", "VNIR", "SWIR", "TIR", "UV", "VIS", "RGB", "IRB",
        "MTRDR", "TRDR", "RDR", "EDR", "CDR", "DDR", "DTM", "DEM", "PDS",
        "HiRISE", "CTX", "CRISM", "THEMIS", "LROC", "NAC", "WAC", "HRSC",
        "Fe", "Mg", "Al", "Ca", "Ti", "Si", "OH", "H2O", "CO2", "TiO2",
        "grayscale",
    )
}  # fmt: skip
SPELLINGS["GREYSCALE"] = "grayscale"  # American English
WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def sentenceCase(text: str) -> str:
    """``text`` in sentence case, keeping acronyms, element symbols and
    formulas as they are spelled. Text in capitals (as PDS descriptions are)
    is lowered word by word; in mixed case, words with capitals after the
    first letter (acronyms, "HiRISE") are kept."""
    shouting = text.upper() == text
    first = True

    def word(match: re.Match[str]) -> str:
        nonlocal first
        token = match.group()
        if token.upper() in SPELLINGS:
            out = SPELLINGS[token.upper()]
        elif not shouting and any(c.isupper() for c in token[1:]):
            out = token
        else:
            out = token.lower()
        if first:
            out = out[:1].upper() + out[1:]
            first = False
        return out

    return WORD.sub(word, " ".join(text.split()))


def _rank(image: BrowseImage) -> tuple[int, str]:
    """Color composites first (false color, true color, then other color
    and RGB composites), then the rest alphabetically; images not projected
    onto the map last."""
    kind = image.kind.lower()
    if "unprojected" in kind:
        group = 4
    elif "false color" in kind:
        group = 0
    elif "true color" in kind:
        group = 1
    elif any(w in kind.split() for w in ("color", "rgb", "irb", "composite")):
        group = 2
    else:
        group = 3
    return group, kind


def ordered(images: Iterable[BrowseImage]) -> list[BrowseImage]:
    """Browse images most useful first (``_rank``), one of each kind (the
    first listed) and of each URL."""
    kinds: set[str] = set()
    urls: set[str] = set()
    unique = []
    for image in images:
        if image.kind.lower() in kinds or image.url in urls:
            continue
        kinds.add(image.kind.lower())
        urls.add(image.url)
        unique.append(image)
    return sorted(unique, key=_rank)
