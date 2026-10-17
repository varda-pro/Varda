"""Showing what a library and its spectra are: source, citation, provenance."""

from __future__ import annotations

from html import escape

from PyQt6.QtGui import QTextOption
from PyQt6.QtWidgets import QTextBrowser, QWidget

from varda.spectral_libraries.spectrum import LibrarySpectrum, describeProvenance
from varda.spectral_libraries.store import SpectralLibrary


def provenanceHtml(spectrum: LibrarySpectrum) -> str:
    rows = describeProvenance(spectrum)
    if spectrum.std is not None:
        rows.append(("Spread", "± 1 σ per sample, kept with the spectrum"))
    lines = "".join(
        f"<b>{escape(label)}</b>: {escape(value)}<br>" for label, value in rows
    )
    return f"<p><b>{escape(spectrum.name)}</b><br>{lines}</p>"


def libraryHtml(library: SpectralLibrary) -> str:
    count = len(library.spectra)
    parts = [
        f"<p><b>{escape(library.name)}</b> — {count} "
        f"spectr{'um' if count == 1 else 'a'}, "
        f"{escape(library.kind.value)}</p>"
    ]
    if library.description:
        parts.append(f"<p>{escape(library.description)}</p>")
    if library.path is not None:
        parts.append(f"<p><b>File</b>: {escape(str(library.path))}</p>")
    if library.citation:
        parts.append(f"<p><b>Cite</b>: {escape(library.citation)}</p>")
    elif library.editable:
        parts.append(
            "<p>Your library: cite the images its spectra came from (listed "
            "with each spectrum) and the libraries any were copied from.</p>"
        )
    return "".join(parts)


class InfoBrowser(QTextBrowser):
    """Read-only rich text that wraps long paths anywhere instead of widening
    its window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
