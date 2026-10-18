"""Help menu content: the version, system info for bug reports, and citations."""

import platform

from varda._actions import ALL_ACTIONS
from varda._actions._menu_ids import MENUBAR, MenuId
from varda.help_info import citation, systemInfo
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
