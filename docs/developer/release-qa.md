# Release QA: first-time users on macOS, Windows and Linux

After every release, five personas who have never seen Varda try it: the
impatient one, the skeptic scientist, the laptop user, the non-native English
speaker and the breaker. Each drives the real app offscreen, only through what
a user can reach (menus, tool bars, dialogs, clicks, keys and typing), towards
**first success**: open an image, view it, look at a pixel's spectrum, run one
analysis from the Processing menu, and export the result. What confuses,
blocks or crashes them becomes a finding.

The scripts live in `qa/`; the `/release-qa` skill
(`.claude/skills/release-qa/SKILL.md`) runs them, ranks the findings, fixes
them and reruns.

## Run it locally

```bash
uv sync --extra hypyrameter
VARDA_QA_OUT=~/varda-qa uv run pytest -m qa qa/
uv run pytest -m qa qa/test_breaker.py        # one persona
```

Plain `uv run pytest` skips them (`pyproject.toml` deselects the `qa`
marker), so the normal test run takes no longer. Without `VARDA_QA_OUT` the
output goes under pytest's temporary folder; the run ends by printing the
findings and where `findings.json` is.

Each persona test runs in its own process (`qa/conftest.py`), so a crash of
Varda is a finding with the step it happened in, and the other personas
still run. The output folder holds:

| Path | What |
| --- | --- |
| `findings.json` | every finding: persona, step, severity, message, screenshot, OS, how to reproduce, `file:line` when known; counts per severity and persona |
| `<persona>/NN-*.png` | numbered screenshots of every step, message box and finding |
| `<persona>/steps.jsonl` | one line per event: step timings, menus used, file dialogs, waits, warnings and log lines |
| `<persona>/console.txt` | the persona process's output (the stack of a crash) |
| `data/` | the synthetic files the personas open |

Severities, worst first: `crash`, `wrong result`, `blocked`, `confused`,
`annoyed`. To merge reports from several machines into one ranked table with
counts per OS:

```bash
uv run python -m qa.summary release-qa-macos release-qa-windows release-qa-linux
```

## Run it on all three operating systems

`.github/workflows/release-qa.yml` runs the personas on `windows-latest`,
`macos-latest` and `ubuntu-latest` when a release is published, or on demand:

```bash
gh workflow run release-qa.yml --repo varda-pro/Varda -f ref=v0.10.0
gh run list --repo varda-pro/Varda --workflow release-qa.yml --limit 1
gh run download <run-id> --repo varda-pro/Varda --dir release-qa-v0.10.0
```

On Windows the workflow sets `QT_QPA_FONTDIR=C:\Windows\Fonts`: Qt's
offscreen platform finds no fonts there by itself, which made every glyph a
box. Each OS uploads its folder as the artifact `release-qa-<os>` (without
`data/`) and writes a summary on the run's page. The workflow never fails a
release: findings are results, and a broken QA script shows only as a failed
step.

## What the personas do

| Persona | What they try |
| --- | --- |
| `impatient` | first success without reading anything: the empty window's hints, the status bar's next step, a plain click with Pixel Select, double-clicking Run |
| `skeptic` | Help > Documentation (the bundled guide, its online link at this version's tag), Cite Varda, About; the guide's "Network use and privacy"; network connections made during first success, with their callers; band labels and the PCA range with units; provenance in the export; whether the guide gives the method, units, reference and uncertainty |
| `laptop` | everything at 1280×800 and 1024×700 offline (any connection is a finding naming its caller); a layout audit (`common/layout_audit.py`) of the window and each dialog; the same pixel clicked twice; a dozen images, listed in the order chosen; a 215 MB cube |
| `nonnative` | reads every menu item, tool tip and dialog label: unexplained abbreviations, idioms (fine with their plain meaning in parentheses), several names for one thing, error messages that don't say what to do, a Debug menu, developer text in the status bar |
| `breaker` | corrupt, truncated, zero-band, empty, missing, no-CRS and wrong-body (WGS 84 Mars, PDS3 label) files; paths with spaces and unicode; junk in number boxes; PCA from and to one wavelength; export to bare names, names ending in a dot, names Windows forbids or reserves; Esc everywhere; removing an image while PCA runs |

Platform checks, skipped where they don't apply:

- **Windows:** backslashed paths with spaces, a lower-case drive letter, a
  file name in the wrong case, the keyring backend that would hold AI keys,
  and the window and PCA dialog at `QT_SCALE_FACTOR=1.5` (150 % display
  scaling).
- **Linux:** two files whose names differ only in case, a keychain service
  for AI keys, and fonts: every character Varda's text uses must exist in
  some installed font.
- **macOS:** ⌘ shortcuts in the menus, ⌘O, and what a Mac user does with
  "Ctrl+Click" (presses Control).

## How they stand in for a user

- The operating system's file chooser is native on macOS and Windows, so not
  Varda's UI: `QaRun.chooseFiles` queues the files a persona picks, and every
  file dialog Varda asks for is logged. Links Varda opens (Help >
  Documentation) are recorded, never opened.
- Message boxes are screenshot, logged and answered (Esc, or a queued
  `QaRun.answers` button); menus that pop up are answered from
  `QaRun.menuAnswers`.
- A wait over 2 s with no busy indicator is an `annoyed` finding; a step
  stuck over 4 minutes is `blocked` and its dialogs are closed.
- Network connections are recorded through a Python audit hook, each host
  with the frames that made the first connection to it (`Network.callers`);
  `Network.offline` refuses them.
- Every workspace a persona opens must come to the front; one that opens
  behind the current tab is a finding (`journeys.viewImage`).
- App data goes to Qt's test location, so your plugins, libraries and
  sessions are neither used nor changed.
- Real products in `~/Documents/Software/varda-aux/ode` are opened by the
  breaker when present (never in CI); `VARDA_QA_REAL=0` skips them.

## Adding to it

Every release-QA finding that a script could catch becomes a new step or
persona check, so the next release is checked for it too. Use the helpers in
`qa/harness.py` (`useMenu`, `newDialog`, `click`, `typeInto`, `chooseInCombo`,
`waitFor`, `qaShot`, `finding`) and the first-success steps in
`qa/journeys.py`; act through the UI, never by calling Varda's internals to
skip it. Keep checks honest: a finding should be something a real user would
hit, with a screenshot that shows it.
