"""The skeptic scientist: before trusting anything, reads Help, the citation,
the license and what goes over the network; then checks that every number
they produce can be defended in a methods section (method, units, body
model, how to report uncertainty)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import rasterio
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QPlainTextEdit,
    QTextBrowser,
    QWidget,
)

from qa import journeys
from qa.data import QaData
from qa.harness import Network, QaRun
from varda.analysis.dialog import RunAnalysisDialog
from varda.version import vardaVersion

pytestmark = [pytest.mark.qa, pytest.mark.qt_no_exception_capture]

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "user-guide"
# An enum member's name ("WEIGHTS", "INVERSE_VARIANCE") where its label belongs
INTERNAL_NAME = re.compile(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)+|[A-Z]{4,}")
# What a methods section needs from a process's guide page
GUIDE_NEEDS = {
    "a reference": re.compile(r"(doi\.org|doi:|et al\.|\(\d{4}\))", re.I),
    "its parameters": re.compile(r"parameter|setting", re.I),
    "units": re.compile(r"\bunits?\b|\bnm\b|meters|µm", re.I),
    "how to report uncertainty": re.compile(r"uncertaint|residual|RMSE|error", re.I),
    "what to report": re.compile(r"report", re.I),
}


def test_trust_before_use(qaRun: QaRun, qaData: QaData) -> None:
    run = qaRun
    Network.hosts.clear()

    with run.step("documentation", "Help > Documentation"):
        if run.useMenu("Help", "Documentation"):
            run.settle(0.3)
            run.log("documentation opens", urls=run.openedUrls)
            online = [u for u in run.openedUrls if u.startswith("http")]
            if online:
                run.finding(
                    "confused",
                    f"Help > Documentation opens a web page ({online[-1]}); "
                    "there is no copy of the guide in the app to read offline",
                    reproduce="Disconnect from the network; Help > Documentation",
                )
            guide = next(
                (
                    w
                    for w in QApplication.topLevelWidgets()
                    if w.isVisible() and "User Guide" in w.windowTitle()
                ),
                None,
            )
            if guide is not None:
                run.qaShot(guide, "user-guide", guide.windowTitle())
                text = "\n".join(
                    b.toPlainText() for b in guide.findChildren(QTextBrowser)
                )
                links = " ".join(label.text() for label in guide.findChildren(QLabel))
                version = vardaVersion()
                if f"/v{version}/" not in links:
                    run.finding(
                        "confused",
                        f"The guide's online link isn't this version's (v{version})",
                        widget=guide,
                    )
                if "Network use and privacy" not in text:
                    run.finding(
                        "confused",
                        "The guide's contents don't list 'Network use and privacy'",
                        widget=guide,
                    )
                guide.close()
            elif not online:
                run.finding("confused", "Help > Documentation showed nothing")

    with run.step("cite", "Help > Cite Varda…"):
        if run.useMenu("Help", "Cite Varda"):
            dialog = run.newDialog()
            if dialog is not None:
                run.qaShot(dialog, "cite", dialog.windowTitle())
                text = "\n".join(
                    b.toPlainText() for b in dialog.findChildren(QPlainTextEdit)
                )
                run.log("citation", text=text)
                if "doi" not in text.lower():
                    run.finding(
                        "confused",
                        "The citation has no DOI to cite this exact version",
                        widget=dialog,
                    )
                dialog.reject()

    with run.step("about and license", "Help > About Varda"):
        before = len(run.messages)
        if run.useMenu("Help", "About Varda"):
            run.waitFor(lambda: len(run.messages) > before, "the About box", 10)
            about = run.messagesSince(before)
            text = about[0].text if about else ""
            if "GNU General Public License" not in text and "GPL" not in text:
                run.finding("confused", "About Varda doesn't name the license")

    with run.step("network and keys", "Read the guide on what goes over the network"):
        guide = _guideText()
        for topic, pattern in {
            "the update check (and how to turn it off)": r"Check for updates when Varda starts",
            "where AI keys are kept": r"keychain",
            "basemap tiles and catalogs": r"basemap|tiles",
            "every connection Varda makes": r"Every connection Varda makes",
        }.items():
            if not re.search(pattern, guide, re.I):
                run.finding("confused", f"The user guide doesn't explain {topic}")

    outcome = journeys.firstSuccess(run, qaData.cube, run.folder / "pca.tif")

    with run.step("network during first success"):
        contacted = sorted(set(Network.hosts))
        run.log("network", hosts=contacted)
        for host in contacted:
            caller = " ← ".join(reversed(Network.callers.get(host, [])[-4:]))
            run.finding(
                "confused",
                f"Opening, viewing, analyzing and exporting contacted {host} "
                f"without saying so (from {caller or 'unknown'})",
            )

    if outcome.viewed is not None:
        with run.step("band names and units", "Render Settings > Band"):
            _checkBandLabels(run, outcome.viewed)

    if outcome.opened is not None:
        with run.step("wavelength range", " > ".join(journeys.PCA_MENU)):
            _checkWavelengthRange(run)

    if outcome.exported is not None:
        with run.step("provenance in the export", "Read the exported GeoTIFF's tags"):
            _checkProvenance(run, outcome.exported)

    with run.step("the guide behind PCA", "docs/user-guide/transforms.md"):
        _checkGuide(run, "transforms.md", "PCA")
        if not (GUIDE / "reporting-results.md").exists():
            run.finding("blocked", "There is no 'Reporting results' page in the guide")


def _guideText() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(GUIDE.glob("*.md")))


def _checkGuide(run: QaRun, page: str, process: str) -> None:
    path = GUIDE / page
    if not path.exists():
        run.finding("blocked", f"No user-guide page {page} for {process}")
        return
    text = path.read_text(encoding="utf-8")
    missing = [
        need for need, pattern in GUIDE_NEEDS.items() if not pattern.search(text)
    ]
    run.log("guide check", page=page, missing=missing)
    if missing:
        run.finding(
            "confused",
            f"The {process} guide ({page}) doesn't give {', '.join(missing)}",
        )
    readme = (GUIDE / "README.md").read_text(encoding="utf-8")
    if page not in readme:
        run.finding("confused", f"{page} isn't linked from the guide's contents")


def _checkBandLabels(run: QaRun, workspace: QWidget) -> None:
    """The band drop-down: is a band named so a scientist can tell which it is?"""
    for combo in workspace.findChildren(QComboBox):
        text = combo.currentText()
        if re.fullmatch(r"\d+\.\d{5,}", text):
            run.finding(
                "confused",
                f"A band is labeled {text!r}: a wavelength with "
                f"{len(text.split('.')[1])} decimals and no unit",
                widget=workspace,
                reproduce="Open a cube; look at Render Settings > Band",
            )
            return
        if re.fullmatch(r"-?\d+(\.\d+)?", text):
            run.finding(
                "confused",
                f"A band is labeled {text!r}: a wavelength without its unit",
                widget=workspace,
                reproduce="Open a cube; look at Render Settings > Band",
            )
            return


def _checkWavelengthRange(run: QaRun) -> None:
    """The PCA dialog's wavelength range: in the image's unit, and saying
    which bands go in (release QA #6, #7)."""
    if not run.useMenu(*journeys.PCA_MENU, shot=False):
        return
    dialog = run.newDialog(RunAnalysisDialog)
    if not isinstance(dialog, RunAnalysisDialog):
        return
    boxes = [b for b in dialog.findChildren(QDoubleSpinBox) if b.isVisible()]
    shown = [b.text() for b in boxes]
    run.log("wavelength range", shown=shown, status=dialog.statusLabel.text())
    if boxes and not any(b.suffix().strip() for b in boxes):
        run.finding(
            "confused",
            f"The PCA wavelength range shows {shown[:2]} with no unit",
            widget=dialog,
        )
    if "bands" not in dialog.statusLabel.text():
        run.finding(
            "confused",
            "The PCA dialog doesn't say which bands will go in",
            widget=dialog,
        )
    dialog.reject()


def _checkProvenance(run: QaRun, path: Path) -> None:
    with rasterio.open(path) as dataset:
        tags = dataset.tags(ns="VARDA")
        descriptions = list(dataset.descriptions)
    run.log("export tags", keys=sorted(tags), bands=descriptions[:5])
    record = next((v for v in tags.values() if '"process"' in v), None)
    if record is None:
        run.finding(
            "wrong result",
            "The exported result carries no provenance (how it was made)",
            reproduce="Run PCA, export it as GeoTIFF, read its VARDA tags",
        )
        return
    provenance = json.loads(record)
    if isinstance(provenance, dict) and "provenance" in provenance:
        provenance = provenance["provenance"]
    for need in ("process", "parameters", "vardaVersion", "inputs"):
        if need not in json.dumps(provenance):
            run.finding("confused", f"The export's provenance has no {need}")
    parameters = (
        provenance.get("parameters", {}) if isinstance(provenance, dict) else {}
    )
    for name, value in parameters.items():
        # a methods section quotes settings as the dialog showed them
        if isinstance(value, dict) and set(value) == {"x", "y"}:
            run.finding(
                "confused",
                f"The export's provenance records {name} as x/y with no units",
            )
        elif isinstance(value, str) and INTERNAL_NAME.fullmatch(value):
            run.finding(
                "confused",
                f"The export's provenance records {name} as {value!r}, "
                "not the label the dialog showed",
            )
    if not any(descriptions):
        run.finding("confused", "The exported bands have no names")
    rounded = [d for d in descriptions if d and d.endswith("(0.0%)")]
    if len(rounded) > 1:
        run.finding(
            "confused",
            f"{len(rounded)} exported PC bands are named '(0.0%)', so they can't "
            "be told apart or ranked",
        )
