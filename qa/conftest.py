"""Fixtures for the release-QA personas (``uv run pytest -m qa qa/``).

Output goes to ``$VARDA_QA_OUT`` (default: a folder under pytest's temporary
directory): per persona a folder of numbered screenshots, ``steps.jsonl``
(every step's timing, warnings and log lines) and ``console.txt``; and
``findings.json`` for them all.

Each persona test runs in its own pytest process, so a crash of Varda
(a segfault, an abort) is a finding for that persona instead of the end of
the run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Before the QApplication exists (the root conftest imports Qt WebEngine)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qa.data import QaData, makeData  # noqa: E402
from qa.harness import (  # noqa: E402
    CONSOLE_FILE,
    FINDINGS_FILE,
    STEPS_FILE,
    SEVERITIES,
    Finding,
    QaRun,
    addFinding,
    osName,
    readFindings,
)

OUT_ENV = "VARDA_QA_OUT"
CHILD_ENV = "VARDA_QA_CHILD"  # set in the process that runs one persona
PERSONA_TIMEOUT_S = 30 * 60
_ran: list[Path] = []  # output folders of the personas run (in the parent)


def isChild() -> bool:
    return bool(os.environ.get(CHILD_ENV))


def personaOf(item: pytest.Item) -> str:
    return Path(str(item.path)).stem.removeprefix("test_")


@pytest.fixture(scope="session")
def qaOut(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = os.environ.get(OUT_ENV)
    out = Path(folder) if folder else tmp_path_factory.mktemp("varda-qa")
    out.mkdir(parents=True, exist_ok=True)
    if not isChild():  # a fresh report: clear what an earlier run wrote
        (out / FINDINGS_FILE).unlink(missing_ok=True)
        for folder in out.iterdir():
            if folder.is_dir():
                for old in [*folder.glob("*.png"), folder / STEPS_FILE]:
                    old.unlink(missing_ok=True)
                (folder / CONSOLE_FILE).unlink(missing_ok=True)
    return out.resolve()


@pytest.fixture(scope="session")
def qaData(qaOut: Path) -> QaData | None:
    """The synthetic files, written once per persona process, beside the
    screenshots (None in the parent process, which runs no persona itself)."""
    return makeData(qaOut / "data") if isChild() else None


@pytest.fixture
def qaRun(request: pytest.FixtureRequest, qaOut: Path) -> Iterator[QaRun | None]:
    """A started Varda for the persona named by the test module
    (``test_<persona>.py``); closed afterwards."""
    if not isChild():
        yield None
        return
    qtbot = request.getfixturevalue("qtbot")  # the QApplication
    assert qtbot is not None
    run = QaRun(personaOf(request.node), qaOut)
    try:
        run.start()
        yield run
    finally:
        run.close()


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """In the parent, run the persona test in a child pytest process."""
    if isChild() or pyfuncitem.get_closest_marker("qa") is None:
        return None
    out = Path(str(pyfuncitem.funcargs["qaOut"]))
    persona = personaOf(pyfuncitem)
    root = Path(str(pyfuncitem.config.rootpath))
    nodeid = pyfuncitem.nodeid
    env = {**os.environ, CHILD_ENV: "1", OUT_ENV: str(out)}
    for marker in pyfuncitem.iter_markers("qa_env"):  # e.g. QT_SCALE_FACTOR
        env.update({key: str(value) for key, value in marker.kwargs.items()})
    command = [sys.executable, "-m", "pytest", nodeid, "-m", "qa", "-q"]
    command += ["-p", "no:cacheprovider"]
    try:
        done = subprocess.run(
            command,
            cwd=root,
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=PERSONA_TIMEOUT_S,
        )
        code, output = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as expired:
        code = None
        output = f"{expired.stdout or ''}{expired.stderr or ''}"
    folder = out / persona
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / CONSOLE_FILE).open("a", encoding="utf-8") as console:
        console.write(f"$ {' '.join(command)}\n{output}\n")
    _ran.append(out)
    if code not in (0, 1):  # killed, crashed, hung or never started
        addFinding(out, _crashFinding(persona, folder, code, str(output)))
    elif code == 1:  # the harness itself failed: a bug in the QA scripts
        raise AssertionError(
            f"{nodeid} failed in its own process:\n{str(output)[-3000:]}"
        )
    return True


def _crashFinding(persona: str, folder: Path, code: int | None, output: str) -> Finding:
    """Varda's process died: say in which step, with its last screenshot."""
    step = "start"
    log = folder / STEPS_FILE
    if log.exists():
        lines = [
            json.loads(line) for line in log.read_text("utf-8").splitlines() if line
        ]
        step = lines[-1]["step"] if lines else step
    shots = sorted(folder.glob("*.png"))
    fatal = next(
        (line for line in output.splitlines() if "Fatal Python error" in line), ""
    )
    what = (
        f"hung for over {PERSONA_TIMEOUT_S // 60} minutes"
        if code is None
        else f"crashed (exit code {code}{'; ' + fatal if fatal else ''})"
    )
    return Finding(
        persona=persona,
        step=step,
        severity="crash",
        message=f"Varda {what}; see {persona}/console.txt for the stack",
        screenshot=shots[-1].relative_to(folder.parent).as_posix() if shots else None,
        os=osName(),
    )


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if isChild() or not _ran:  # an ordinary test run: no personas ran
        return
    out = _ran[-1]
    findings = readFindings(out)
    counts = ", ".join(
        f"{sum(f.severity == s for f in findings)} {s}" for s in SEVERITIES
    )
    terminalreporter.write_sep("-", f"release QA on {osName()}")
    terminalreporter.write_line(f"{len(findings)} findings ({counts})")
    terminalreporter.write_line(f"report: {out / FINDINGS_FILE}")
    for found in findings[:40]:
        terminalreporter.write_line(
            f"  [{found.severity}] {found.persona}/{found.step}: {found.message}"
        )
