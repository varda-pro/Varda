"""Help > Documentation opens the guide that ships with this version, offline
(release QA #8: it opened GitHub's main branch, whatever the version)."""

from PyQt6.QtCore import QUrl

from varda._actions._help_actions import GuideWindow
from varda.help_info import documentationUrl, localGuide
from varda.version import vardaVersion


def test_the_online_guide_is_the_installed_versions():
    url = documentationUrl()
    assert f"/blob/v{vardaVersion()}/docs/user-guide/README.md" in url
    assert "/main/" not in url


def test_an_unknown_version_falls_back_to_main():
    assert "/blob/main/" in documentationUrl("unknown")


def test_the_guide_ships_with_varda():
    guide = localGuide()
    assert guide is not None and guide.name == "README.md" and guide.is_file()


def test_the_guide_window_reads_the_guide_and_follows_its_links(qtbot):
    readme = localGuide()
    assert readme is not None
    window = GuideWindow(readme)
    qtbot.addWidget(window)
    assert "Varda user guide" in window.browser.toPlainText()

    window.browser.setSource(QUrl("network-and-privacy.md"))
    assert "Network use and privacy" in window.browser.toPlainText()
    assert window.browser.openExternalLinks()
    assert documentationUrl() in window.onlineLink.text()
