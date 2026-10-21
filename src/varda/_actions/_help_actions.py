"""The Help menu: about Varda, how to cite it, system info for bug reports,
the documentation, the issue tracker and checking for updates."""

from __future__ import annotations

from pathlib import Path

from app_model.types import Action, MenuRule
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication, QTextDocument
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from varda._actions._menu_ids import MenuGroup, MenuId
from varda.help_info import (
    ISSUES_URL,
    REPOSITORY_URL,
    citation,
    citationNote,
    documentationUrl,
    localGuide,
    systemInfo,
)
from varda.maingui import MainGUI
from varda.update_check import checkForUpdatesNow
from varda.version import vardaVersion


def _copy(text: str) -> None:
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)


def showAbout(mainGui: MainGUI) -> None:
    QMessageBox.about(
        mainGui,
        "About Varda",
        f"<h3>Varda {vardaVersion()}</h3>"
        "<p>Visualization and analysis of raster data: hyperspectral and "
        "multispectral imagery.</p>"
        "<p>Developed at the Space Imagery Center, Lunar and Planetary "
        "Laboratory, University of Arizona, by Michael Phillips, Jesse Oved, "
        "Emma Elliott and Hamad Ayaz.</p>"
        "<p>Free software under the GNU General Public License v3 or later.</p>"
        f'<p><a href="{REPOSITORY_URL}">{REPOSITORY_URL}</a></p>',
    )


class CiteDialog(QDialog):
    """The citation as APA and BibTeX, each with a copy button, and what
    its DOI pins."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cite Varda")
        apa, bibtex = citation()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("If Varda helps your research, please cite it:"))
        for title, text in (("APA", apa), ("BibTeX", bibtex)):
            layout.addWidget(QLabel(f"<b>{title}</b>"))
            box = QPlainTextEdit(text)
            box.setReadOnly(True)
            box.setMinimumHeight(70 if title == "APA" else 150)
            if title == "APA":  # three lines: leave the height to BibTeX
                box.setMaximumHeight(110)
            layout.addWidget(box)
            button = QPushButton(f"Copy {title}")
            button.clicked.connect(lambda _, t=text: _copy(t))
            layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignRight)
        note = QLabel(citationNote())
        note.setWordWrap(True)
        note.setOpenExternalLinks(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setMinimumWidth(520)


def showCitation(mainGui: MainGUI) -> None:
    dialog = CiteDialog(mainGui)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.open()


def copySystemInfo(mainGui: MainGUI) -> None:
    _copy(systemInfo())
    statusBar = mainGui.statusBar()
    if statusBar is not None:
        statusBar.showMessage("System info copied to the clipboard", 4000)


class GuideWindow(QDialog):
    """The user guide shipped with this Varda, readable offline: Markdown
    pages whose links lead from page to page; web links open in the browser."""

    def __init__(self, readme: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Varda {vardaVersion()} User Guide")
        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(True)
        self.browser.setSearchPaths([str(readme.parent)])
        self.browser.setSource(
            QUrl.fromLocalFile(str(readme)),
            QTextDocument.ResourceType.MarkdownResource,
        )
        back = QPushButton("Back")
        back.clicked.connect(self.browser.backward)
        self.browser.backwardAvailable.connect(back.setEnabled)
        back.setEnabled(False)
        url = documentationUrl()
        self.onlineLink = QLabel(f'Online, for this version: <a href="{url}">{url}</a>')
        self.onlineLink.setOpenExternalLinks(True)
        self.onlineLink.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.browser, 1)
        layout.addWidget(self.onlineLink)
        self.setMinimumSize(360, 300)
        self.resize(820, 700)


def openDocumentation(mainGui: MainGUI) -> None:
    """The guide shipped with this version; online (this version's tag) when
    it isn't shipped."""
    readme = localGuide()
    if readme is None:
        QDesktopServices.openUrl(QUrl(documentationUrl()))
        return
    window = GuideWindow(readme, mainGui)
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    window.show()


def reportIssue() -> None:
    QDesktopServices.openUrl(QUrl(ISSUES_URL))


def _inHelp(group: str, order: int) -> list[MenuRule]:
    return [MenuRule(id=MenuId.HELP, group=group, order=order)]


HELP_ACTIONS: list[Action] = [
    Action(
        id="varda.help.documentation",
        title="Documentation",
        callback=openDocumentation,
        menus=_inHelp(MenuGroup.HELP_LINKS, 1),
    ),
    Action(
        id="varda.help.report_issue",
        title="Report an Issue…",
        callback=reportIssue,
        menus=_inHelp(MenuGroup.HELP_LINKS, 2),
    ),
    Action(
        id="varda.help.copy_system_info",
        title="Copy System Info",
        tooltip="Copy the version and platform details a bug report needs",
        callback=copySystemInfo,
        menus=_inHelp(MenuGroup.HELP_INFO, 1),
    ),
    Action(
        id="varda.help.cite",
        title="Cite Varda…",
        callback=showCitation,
        menus=_inHelp(MenuGroup.HELP_INFO, 2),
    ),
    Action(
        id="varda.help.check_for_updates",
        title="Check for Updates…",
        tooltip="See whether a newer Varda has been released",
        callback=lambda mainGui: checkForUpdatesNow(mainGui),
        menus=_inHelp(MenuGroup.HELP_ABOUT, 0),
    ),
    Action(
        id="varda.help.about",
        title="About Varda",
        callback=showAbout,
        menus=_inHelp(MenuGroup.HELP_ABOUT, 1),
    ),
]
