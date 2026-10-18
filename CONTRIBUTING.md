# Contributing to Varda

Thanks for helping. Bug reports, ideas and pull requests are all welcome.
Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md).

## Reporting a bug or asking for a feature

Open an [issue](https://github.com/varda-pro/Varda/issues) using
the bug or feature template. For a bug, **Help → Copy System Info** in Varda
copies the version and platform details the template asks for.

## Working on the code

Varda uses [uv](https://docs.astral.sh/uv/). Python 3.13 is required.

```bash
uv sync --extra hypyrameter    # dependencies (add --extra ml for PyTorch)
uv run varda                   # run the app
uv run pytest                  # tests (colocated in _tests/ folders)
uv run ruff format             # format
uv run ruff check              # lint
uv run ty check                # type check
uv run varda --self-test       # what release builds check before publishing
uv run pytest -m qa qa/        # release QA: five first-time users drive the app
```

`uv run pytest` leaves out the release-QA personas in `qa/`; see
[docs/developer/release-qa.md](docs/developer/release-qa.md) for running them
locally and on all three operating systems in CI.

Before opening a pull request:

- Add or update tests for what you changed; GUI code is easiest to test
  when the logic sits in a Qt-free module.
- Run the commands above; new code should be formatted, lint-clean and typed.
- Note anything users will notice in [CHANGELOG.md](CHANGELOG.md), especially
  changes that can alter analysis results.
- Follow the conventions in [CLAUDE.md](CLAUDE.md): type hints (never
  `Any`), camelCase in Qt subclasses, no lazy imports.

## Retiring an API

Plugins build on Varda's classes, so don't remove a public function or
property outright. Deprecate it first:

```python
from varda.common.deprecation import warnDeprecated

warnDeprecated("oldName()", since="0.4.0", instead="newName()")
```

`since` is the version the deprecation first ships in. List it under
*Deprecated* in the changelog, and remove it no sooner than the next minor
release. Varda's own tests fail if Varda calls anything deprecated.

## License

Varda is licensed under the [GNU GPL v3 or later](LICENSE). By contributing
you agree that your contributions are licensed the same way.
