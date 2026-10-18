"""Merge release-QA reports from several machines into one ranked list.

    uv run python -m qa.summary release-qa-macos release-qa-windows release-qa-linux

Each argument is an output folder (``VARDA_QA_OUT``, or a downloaded
``release-qa-<os>`` artifact) holding a ``findings.json``. Findings that
read the same but for their numbers are merged; the list is ranked by how
many personas hit them, then severity, with a count per OS and a screenshot.
Prints Markdown.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from qa.harness import SEVERITIES, Finding, readFindings


@dataclass
class Merged:
    message: str
    severity: str
    personas: set[str] = field(default_factory=set)
    perOs: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    screenshots: list[str] = field(default_factory=list)


def key(found: Finding) -> str:
    """Findings that differ only in numbers (sizes, seconds, counts) merge."""
    return re.sub(r"\d+(\.\d+)?", "#", found.message)


def merge(folders: list[Path]) -> list[Merged]:
    merged: dict[str, Merged] = {}
    for folder in folders:
        for found in readFindings(folder):
            entry = merged.setdefault(key(found), Merged(found.message, found.severity))
            entry.personas.add(found.persona)
            entry.perOs[found.os] += 1
            if SEVERITIES.index(found.severity) < SEVERITIES.index(entry.severity):
                entry.severity = found.severity
            if found.screenshot and len(entry.screenshots) < 3:
                entry.screenshots.append(f"{folder.name}/{found.screenshot}")
    return sorted(
        merged.values(),
        key=lambda m: (-len(m.personas), SEVERITIES.index(m.severity)),
    )


def markdown(folders: list[Path], limit: int = 40) -> str:
    rows = merge(folders)
    systems = sorted({os for row in rows for os in row.perOs})
    lines = [
        f"{len(rows)} distinct findings from {len(folders)} report(s)",
        "",
        "| # | severity | personas | " + " | ".join(systems) + " | finding |",
        "|---|---|---|" + "---|" * len(systems) + "---|",
    ]
    for number, row in enumerate(rows[:limit], 1):
        counts = " | ".join(str(row.perOs.get(os, 0)) for os in systems)
        shot = f" ({row.screenshots[0]})" if row.screenshots else ""
        lines.append(
            f"| {number} | {row.severity} | {len(row.personas)} "
            f"({', '.join(sorted(row.personas))}) | {counts} | {row.message}{shot} |"
        )
    return "\n".join(lines)


def main(arguments: list[str]) -> int:
    folders = [Path(a) for a in arguments] or [Path("release-qa")]
    print(markdown([f for f in folders if (f / "findings.json").exists()]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
