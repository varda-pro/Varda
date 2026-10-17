"""A library spectrum and the record of where it came from.

Every spectrum keeps its own provenance, a flat JSON-friendly dict, so a
library can always say what each spectrum is. The keys Varda writes:

- spectra you add: ``sourceImage``, ``sourcePath``, ``region`` (e.g. "pixel
  (12, 40)", "5×5 px box centered on (12, 40)", "ROI “dunes”"),
  ``pixelCount``, ``statistic`` ("single pixel" or "mean ± 1 σ"), ``note``
  (e.g. the processing behind it), ``vardaVersion`` and ``added`` (ISO date);
- spectra read from a file: ``library`` (the library's name) and ``file``;
- spectra copied from another library keep the original's keys and add
  ``copiedFrom`` (that library's name).

Wavelengths are in nm (readers convert declared units).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

import attrs
import numpy as np

from varda.version import vardaVersion

Provenance = Mapping[str, str | int | float]


@attrs.frozen(eq=False)
class LibrarySpectrum:
    name: str
    wavelengths: np.ndarray  # nm (the loaders convert declared units)
    reflectance: np.ndarray  # the values, in ``valueUnits``
    source: str = ""  # the library it came from
    valueUnits: str = ""  # e.g. "reflectance", "I/F"; "" when not stated
    std: np.ndarray | None = None  # per-sample σ of a region's mean, else None
    provenance: Provenance = attrs.field(factory=dict)


@attrs.frozen
class SpectrumOrigin:
    """Where a plotted spectrum was read from, for recording it in a library."""

    imageName: str
    imagePath: str = ""
    region: str = ""  # "pixel (12, 40)", "ROI “dunes”", ...
    pixelCount: int = 1
    note: str = ""  # e.g. processing applied before averaging


def spectrumFromImage(
    name: str,
    wavelengthsNm: np.ndarray,
    values: np.ndarray,
    origin: SpectrumOrigin,
    *,
    std: np.ndarray | None = None,
    valueUnits: str = "",
) -> LibrarySpectrum:
    """A new library spectrum read from an image, with its provenance."""
    provenance: dict[str, str | int | float] = {
        "sourceImage": origin.imageName,
        "sourcePath": origin.imagePath,
        "region": origin.region,
        "pixelCount": int(origin.pixelCount),
        "statistic": "single pixel" if std is None else "mean ± 1 σ",
        "vardaVersion": vardaVersion(),
        "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if origin.note:
        provenance["note"] = origin.note
    return LibrarySpectrum(
        name,
        np.asarray(wavelengthsNm, dtype=np.float64),
        np.asarray(values, dtype=np.float64),
        source=origin.imageName,
        valueUnits=valueUnits,
        std=None if std is None else np.asarray(std, dtype=np.float64),
        provenance={k: v for k, v in provenance.items() if v != ""},
    )


def copiedSpectrum(
    spectrum: LibrarySpectrum, fromLibrary: str, name: str | None = None
) -> LibrarySpectrum:
    """``spectrum`` copied into another library: its provenance kept, and
    the library it was copied from recorded."""
    provenance = dict(spectrum.provenance)
    provenance.setdefault("copiedFrom", fromLibrary)
    return attrs.evolve(
        spectrum,
        name=spectrum.name if name is None else name,
        source=spectrum.source or fromLibrary,
        provenance=provenance,
    )


def describeProvenance(spectrum: LibrarySpectrum) -> list[tuple[str, str]]:
    """(label, value) rows saying where a spectrum came from, for display."""
    labels = {
        "sourceImage": "Image",
        "sourcePath": "Image file",
        "region": "Region",
        "pixelCount": "Pixels",
        "statistic": "Statistic",
        "note": "Note",
        "library": "Library",
        "file": "File",
        "copiedFrom": "Copied from",
        "vardaVersion": "Varda version",
        "added": "Added",
    }
    rows = [("Value units", spectrum.valueUnits or "not stated")]
    known = [k for k in labels if k in spectrum.provenance]
    other = [k for k in spectrum.provenance if k not in labels]
    rows += [(labels.get(k, k), str(spectrum.provenance[k])) for k in known + other]
    finite = np.isfinite(spectrum.wavelengths) & np.isfinite(spectrum.reflectance)
    if finite.any():
        wavelengths = spectrum.wavelengths[finite]
        rows.append(
            (
                "Samples",
                f"{int(finite.sum())}, {wavelengths.min():.1f}–"
                f"{wavelengths.max():.1f} nm",
            )
        )
    return rows
