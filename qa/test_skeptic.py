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
from PyQt6.QtWidgets import QComboBox, QPlainTextEdit, QWidget

from qa import journeys
from qa.data import QaData
from qa.harness import Network, QaRun

pytestmark = [pytest.mark.qa, pytest.mark.qt_no_exception_capture]

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "user-guide"
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
        }.items():
            if not re.search(pattern, guide, re.I):
                run.finding("confused", f"The user guide doesn't explain {topic}")

    outcome = journeys.firstSuccess(run, qaData.cube, run.folder / "pca.tif")

    with run.step("network during first success"):
        contacted = sorted(set(Network.hosts))
        run.log("network", hosts=contacted)
        if contacted:
            run.finding(
                "confused",
                f"Opening, viewing, analyzing and exporting contacted {contacted} "
                "without saying so",
            )

    if outcome.viewed is not None:
        with run.step("band names and units", "Render Settings > Band"):
            _checkBandLabels(run, outcome.viewed)

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
    if not any(descriptions):
        run.finding("confused", "The exported bands have no names")
