"""Help menu content: the version, system info for bug reports, and citations."""

import platform

from PyQt6.QtWidgets import QLabel

from varda._actions import ALL_ACTIONS
from varda._actions._help_actions import CiteDialog
from varda._actions._menu_ids import MENUBAR, MenuId
from varda.help_info import citation, citationNote, systemInfo
from varda.version import vardaVersion


def test_the_version_comes_from_the_package_metadata():
    version = vardaVersion()
    assert version and version != "unknown"
    assert version[0].isdigit()


def test_system_info_has_what_a_bug_report_needs():
    info = systemInfo()
    assert f"Varda {vardaVersion()}" in info
    assert platform.python_version() in info
    for name in (
        "PyQt6",
        "numpy",
        "rasterio",
        "GDAL",
        "Spectral Python (SPy)",
        "PROJ",
        "HyPyRameter",
        "PyTorch",
    ):
        assert name in info


def test_citations_come_from_citation_cff():
    apa, bibtex = citation()
    assert apa.startswith("Phillips, M., Oved, J., Elliott, E., & Ayaz, H.")
    assert "Varda: visualization and analysis of raster data" in apa
    assert vardaVersion() in apa or "0." in apa
    assert bibtex.startswith("@software{varda,")
    assert (
        "author = {Phillips, Michael and Oved, Jesse and Elliott, Emma and Ayaz, Hamad}"
        in bibtex
    )
    assert "license = {GPL-3.0-or-later}" in bibtex
    assert apa.endswith("https://doi.org/10.5281/zenodo.23447208")
    assert "doi = {10.5281/zenodo.23447208}" in bibtex


def test_the_help_menu_offers_about_cite_system_info_and_docs():
    assert (MenuId.HELP, "Help") in MENUBAR
    titles = {a.title for a in ALL_ACTIONS if a.menus and a.menus[0].id == MenuId.HELP}
    assert {
        "About Varda",
        "Cite Varda…",
        "Copy System Info",
        "Documentation",
        "Report an Issue…",
    } <= titles


def test_the_citation_says_its_doi_cites_every_version(qtbot):
    """Release QA 0.12.0 #8: "(Version 0.12.0)" beside the concept DOI, which
    resolves to the latest version, didn't pin the version."""
    note = citationNote()
    assert "every version" in note
    assert "latest" in note
    assert "own DOI" in note
    assert vardaVersion() in note
    assert 'href="https://doi.org/10.5281/zenodo.23447208"' in note

    dialog = CiteDialog()
    qtbot.addWidget(dialog)
    (label,) = [
        label for label in dialog.findChildren(QLabel) if "own DOI" in label.text()
    ]
    assert label.openExternalLinks()
    assert label.wordWrap()
