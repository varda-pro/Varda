---
name: release-qa
description: After a Varda release, run five first-time-user personas through the released app, rank what confused or broke them, fix it all in one pass, and rerun them to prove it. Use after every release (CLAUDE.md, "Releasing"), or when asked for launch or release QA.
---

# Release QA: five first-time users

Act as Varda's launch QA lead. A release is not finished until five people
who have never seen Varda have tried it and every problem they hit is fixed
or deliberately parked.

## 0. Set up

- Work from the release tag: `git fetch --tags && git worktree add ../Varda-qa-<version> v<version>`, then `uv sync --extra hypyrameter` there, so you test what users got.
- Fixes go on a new branch `fix/release-qa-<version>` from current `main`, never on the tag.
- Data: the real products in `~/Documents/Software/varda-aux/ode` (a CRISM MTRDR with labels, its IOVF output, a CTX DEM, HiRISE) plus synthetic rasters the personas make. Never commit data.
- Each persona works in its own scratch folder under the session scratchpad and writes its report there.

## 1. How a persona "uses" Varda

Varda is a desktop Qt app, so there is no browser. Personas drive the real
app offscreen:

- Write throwaway pytest-qt or plain scripts (in scratch, never in the repo) that build the app the way `varda.startup.createVarda` does, with `QT_QPA_PLATFORM=offscreen`. Then act only through what a user could do: menus (`MENUBAR`, the `_actions` package), buttons, dialogs, keyboard shortcuts, `QTest` clicks and typing.
- Calling internal functions to skip the UI defeats the test.
- After every step, `grab()` the main window or dialog to a numbered PNG and **look at it**. Screenshot every moment of confusion, being stuck, annoyance, an error, clipped text, or a wait over 2 s with no feedback.
- Time each step. Record stderr, warnings and log lines (`varda.log`).
- Also run the packaged self-test from the release artifacts when available (`Varda --self-test`).

**First success** (the goal of every persona): open an image, see it, look at a pixel's spectrum, run one analysis from the Processing menu, and export the result together with what is needed to report it: provenance and the user guide's "Reporting your results".

## 2. The five users (dispatch in parallel, one subagent each)

1. **The impatient one.** Skims, clicks the first plausible button, never reads tooltips or docs, and quits at the first error or unexplained wait. Report the first point where a real user would have given up.
2. **The skeptic scientist.** Before trusting anything, reads Help → Documentation, Cite Varda, the license, and what goes over the network (update check, Find Data catalogs and basemap tiles, AI providers and where keys are kept). For every number they produce, they check that the guide gives the method, units, surface or body model, and how to report uncertainty, per CLAUDE.md's scientific-documentation rule. Flag every number that can't be defended in a methods section.
3. **The laptop user.** Does everything at 1280×800, and again at 1024×700. Opens a dozen images and a full-size cube, and works offline: no network, slow disk. Flags anything clipped, needing horizontal scrolling, frozen without progress, or running out of memory. Run `common/layout_audit.assertNothingClipped` on every dialog they open.
4. **The non-native English speaker.** Follows only what the UI literally says, with no outside knowledge of jargon and no guessing from context. Flags ambiguous labels, unexplained abbreviations (I/F, MTRDR, CRS…), idioms, messages that don't say what to do next, and inconsistent terms for the same thing.
5. **The breaker.** Pastes junk into every field (text in number boxes, huge numbers, empty, unicode, paths with spaces). Cancels and closes windows mid-load and mid-analysis, double-clicks everything, presses Esc and Back at every step, and opens corrupt, truncated, zero-band, no-CRS and wrong-body (WGS 84-tagged Mars) files. Flags every crash, traceback, hang, silent wrong result or lost work.

Each persona's report is a list of findings. Each finding has: one line, the screenshot paths, the exact steps to reproduce, the severity (crash or wrong result > blocked > confused > annoyed), and the file and line if they can point to it.

## 3. Merge and rank

Merge the five reports into one list. Dedupe findings that share a root cause, and rank by **how many personas hit it**, then by severity. Publish the merged list as a short page or doc for Michael, with screenshots, before fixing.

## 4. Fix everything in one pass

- One fix branch. Every fix is test-first: reproduce it in a pytest-qt test, then fix it. Respect CLAUDE.md: layouts, connections via bound methods or `connectWhileAlive`, docs updated in the same change, CHANGELOG under Unreleased.
- If something should not be fixed, park it with a one-line reason and say so in the report. Never drop a finding silently.
- Run the full suite, `lint-imports`, ruff and ty.

## 5. Prove it

Rerun all five personas against the fix branch, with the same scripts and data. Each finding gets **FIXED** (with an after screenshot), **STILL THERE**, or **PARKED**. Repeat the fix pass for anything still there. Then open the PR (`--repo varda-pro/Varda`, using the Michael-S-Phillips gh token), merge when CI is green, and say whether the fixes warrant a patch release. Don't publish a release without Michael's go-ahead: every release mints a Zenodo DOI.
