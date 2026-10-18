---
name: release-qa
description: After a Varda release, run five first-time-user personas through the released app on macOS, Windows and Linux, rank what confused or broke them, fix it all in one pass, and rerun them to prove it. Use after every release (CLAUDE.md, "Releasing"), or when asked for launch or release QA.
---

# Release QA: five first-time users, three operating systems

Act as Varda's launch QA lead. A release is not finished until five people
who have never seen Varda have tried it on macOS, Windows and Linux, and
every problem they hit is fixed or deliberately parked.

The personas are scripts in the repo (`qa/`, guide:
`docs/developer/release-qa.md`). They are the baseline; every release adds
to them.

## 0. Set up

- Work from the release tag: `git fetch --tags && git worktree add ../Varda-qa-<version> v<version>`, then `uv sync --extra hypyrameter` there, so you test what users got.
- Fixes go on a new branch `fix/release-qa-<version>` from current `main`, never on the tag.
- Data: the scripts generate synthetic rasters (georeferenced cubes, a WGS 84-tagged Mars GeoTIFF with a PDS3 label, corrupt, truncated and zero-band files, paths with spaces and unicode). Locally they also open the real products in `~/Documents/Software/varda-aux/ode` when present. Never commit data.
- Write everything under the session scratchpad: one folder per OS (`release-qa-macos`, `release-qa-windows`, `release-qa-linux`).

## 1. Run the personas on all three operating systems

Start Windows and Linux first (they take a while), then run macOS locally:

```bash
# Windows and Linux (and macOS again, from a clean runner) in CI
GH_TOKEN=$(gh auth token --user Michael-S-Phillips) \
  gh workflow run release-qa.yml --repo varda-pro/Varda -f ref=v<version>
gh run list --repo varda-pro/Varda --workflow release-qa.yml --limit 1   # the run id
gh run watch <run-id> --repo varda-pro/Varda

# macOS, here
VARDA_QA_OUT=<scratch>/release-qa-macos uv run pytest -m qa qa/

# when the run is done: release-qa-windows/, release-qa-linux/, release-qa-macos/
gh run download <run-id> --repo varda-pro/Varda --dir <scratch>
```

Publishing a release also starts the workflow by itself; use that run if it
exists (`gh run list ... --event release`). The workflow never fails: a red
"Run the personas" step means a QA script broke, so read its log and fix the
script as part of this pass.

Each folder holds `findings.json` (persona, step, severity, message,
screenshot, **OS**, reproduce steps, `file:line`), numbered screenshots and
`steps.jsonl` per persona, and `console.txt` with the stack of any crash.

## 2. Look, don't just count

The scripts catch what they were written to catch. You are still the five
users:

- **Open the screenshots from all three OSes** (Read the PNGs) for every step of every persona, not only the ones with findings. Look for what no check measures: wrong colors or empty plots, clipped or overlapping text, fonts that fell back badly on Linux, dialogs larger than the screen at 150 % scaling on Windows, Ctrl where a Mac shows ⌘, a result that looks implausible.
- Read each persona's `steps.jsonl` for slow steps, warnings and log lines.
- Where a persona's role calls for judgment the script can't make (the skeptic deciding whether a guide page is enough to write a methods section; the non-native speaker deciding whether a message says what to do), judge it yourself from the screenshots and the guide, and add the finding.
- Run the packaged self-test from the release artifacts when available (`Varda --self-test`).

Every finding you add by hand carries the OS it was seen on (or "all").

**First success** (the goal of every persona): open an image, see it, look at a pixel's spectrum, run one analysis from the Processing menu, and export the result together with what is needed to report it: provenance and the user guide's "Reporting your results".

## 3. The five users

1. **The impatient one** (`qa/test_impatient.py`). Skims, clicks the first plausible button, never reads tooltips or docs, and quits at the first error or unexplained wait. Report the first point where a real user would have given up.
2. **The skeptic scientist** (`test_skeptic.py`). Before trusting anything, reads Help → Documentation, Cite Varda, the license, and what goes over the network (update check, Find Data catalogs and basemap tiles, AI providers and where keys are kept). For every number they produce, they check that the guide gives the method, units, surface or body model, and how to report uncertainty, per CLAUDE.md's scientific-documentation rule. Flag every number that can't be defended in a methods section.
3. **The laptop user** (`test_laptop.py`). Does everything at 1280×800, and again at 1024×700, offline. Opens a dozen images and a large cube. Flags anything clipped, needing horizontal scrolling, frozen without progress, or running out of memory; `common/layout_audit` runs on every dialog. Windows: 150 % display scaling (`QT_SCALE_FACTOR=1.5`). Linux: missing fonts and their fallback.
4. **The non-native English speaker** (`test_nonnative.py`). Follows only what the UI literally says, with no outside knowledge of jargon and no guessing from context. Flags ambiguous labels, unexplained abbreviations (I/F, MTRDR, CRS…), idioms, messages that don't say what to do next, and inconsistent terms for the same thing.
5. **The breaker** (`test_breaker.py`). Pastes junk into every field (text in number boxes, huge numbers, empty, unicode, paths with spaces). Cancels and closes windows mid-load and mid-analysis, double-clicks everything, presses Esc at every step, and opens corrupt, truncated, zero-band, no-CRS and wrong-body (WGS 84-tagged Mars) files. Flags every crash, traceback, hang, silent wrong result or lost work. Platform traps: Windows backslash, space and drive-letter paths, case-insensitive names and the keyring backend; Linux case-sensitive names; macOS ⌘ versus Ctrl.

Each finding has: one line, the screenshot paths, the exact steps to reproduce, the severity (crash or wrong result > blocked > confused > annoyed), **the OS it happened on**, and the file and line if they can point to it.

## 4. Merge and rank

```bash
uv run python -m qa.summary <scratch>/release-qa-macos <scratch>/release-qa-windows <scratch>/release-qa-linux
```

prints one table: findings that read the same are merged, ranked by **how many personas hit them**, then by severity, with a **count per OS** (a Windows-only crash and an everywhere-confusion are different fixes). Add your hand-made findings from step 2, dedupe those that share a root cause, and publish the merged list as a short page or doc for Michael, with screenshots from each OS, before fixing.

## 5. Fix everything in one pass

- One fix branch. Every fix is test-first: reproduce it in a pytest-qt test, then fix it. Respect CLAUDE.md: layouts, connections via bound methods or `connectWhileAlive`, docs updated in the same change, CHANGELOG under Unreleased.
- A finding only one OS showed gets a test that runs there (or is marked to skip elsewhere), and the fix is checked on that OS by the rerun.
- **Grow the suite:** every finding a script could have caught but didn't becomes a new step in `qa/` (a new check in the persona that found it, or a new `journeys` step), in the same branch, so the next release is checked for it too. Keep the scripts acting only through the UI.
- If something should not be fixed, park it with a one-line reason and say so in the report. Never drop a finding silently.
- Run the full suite, `lint-imports`, ruff and ty.

## 6. Prove it

Rerun all five personas **on all three OSes** against the fix branch, with the same scripts plus the new ones: locally for macOS, and in CI with

```bash
GH_TOKEN=$(gh auth token --user Michael-S-Phillips) \
  gh workflow run release-qa.yml --repo varda-pro/Varda -f ref=fix/release-qa-<version>
```

(push the branch first), then download and merge as in steps 1 and 4. Each finding gets **FIXED** (with an after screenshot from the OS it was found on), **STILL THERE**, or **PARKED**. Repeat the fix pass for anything still there. Then open the PR (`GH_TOKEN=... gh pr create --repo varda-pro/Varda --head fix/release-qa-<version>`), merge when CI is green, and say whether the fixes warrant a patch release. Don't publish a release without Michael's go-ahead: every release mints a Zenodo DOI.
