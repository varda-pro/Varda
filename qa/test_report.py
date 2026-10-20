"""The QA report files (not a persona: runs with the ordinary tests)."""

from __future__ import annotations

import json
from pathlib import Path

from qa.harness import FINDINGS_FILE, Finding, addFinding, ensureFindings, readFindings


def test_a_clean_run_still_writes_an_empty_report(tmp_path: Path):
    ensureFindings(tmp_path)
    report = json.loads((tmp_path / FINDINGS_FILE).read_text("utf-8"))
    assert report["findings"] == []
    assert readFindings(tmp_path) == []


def test_ensuring_the_report_keeps_the_findings(tmp_path: Path):
    found = Finding(
        persona="breaker",
        step="s",
        severity="confused",
        message="m",
        screenshot=None,
        os="Linux",
    )
    addFinding(tmp_path, found)
    ensureFindings(tmp_path)
    assert readFindings(tmp_path) == [found]
