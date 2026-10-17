"""The command line: ``python -m varda.crism ...`` and ``Varda --crism ...``.

- ``process``: observations (folders, or TRR3 files) to MTRDR-consistent products.
- ``calibration``: where an observation's calibration files are, and fetch them.
- ``learn-artifacts``: learn the artifact basis of each transmission from
  several observations, and print the leave-one-out check.
- ``validate``: compare our product with an official MTRDR.

Exit codes: 0 everything succeeded; 1 some observations failed (the others
still ran); 2 a usage or configuration error (a bad recipe or path, or
missing calibration files with nothing processed).
"""

import argparse
import json
import logging
import math
import sys
from collections.abc import Sequence
from pathlib import Path

import appdirs
import numpy as np

from varda.crism.atmosphere import (
    ArtifactBasis,
    AtmosphereModel,
    artifactFileName,
    artifactScale,
    learnArtifactBasis,
    saveArtifactBasis,
    shiftedLogTransmission,
)
from varda.crism.calibration import (
    CalibrationFileStatus,
    CalibrationLibrary,
    MissingCalibrationError,
    TransmissionSource,
)
from varda.crism.observation import CrismObservation
from varda.crism.pipeline import ColumnResiduals, columnResiduals, process
from varda.crism.readers import readTrr3
from varda.crism.recipe import Recipe, recipeFromDict
from varda.crism.validation import CO2_WINDOW_NM, co2Residual
from varda.crism.validation_report import validate

EXIT_FAILED = 1
EXIT_USAGE = 2
DEFAULT_OUTPUT = "crism-products"

_log = logging.getLogger(__name__)


def defaultCache() -> Path:
    return Path(appdirs.user_data_dir("Varda")) / "crism"


class _UsageError(Exception):
    """Something the user must fix; the command exits with 2."""


def _error(message: object) -> None:
    print(f"error: {message}", file=sys.stderr)


# --- shared ------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="varda-crism", description="CRISM TRR3 to MTRDR-consistent products."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def calibrationOptions(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--cache", type=Path, help="calibration cache folder")
        sub.add_argument("--cat-aux", type=Path, help="CAT aux_files folder")
        sub.add_argument(
            "--calibration",
            type=Path,
            action="append",
            default=[],
            help="more folders to look for calibration files in (repeatable)",
        )

    run = commands.add_parser("process", help="process observations")
    run.add_argument("paths", nargs="+", type=Path, help="folders or TRR3 files")
    run.add_argument("--recipe", type=Path, help="recipe JSON (default: the defaults)")
    run.add_argument("--out", type=Path, default=Path(DEFAULT_OUTPUT))
    run.add_argument("--sensor-space", action="store_true", help="also write _vter")
    run.add_argument("--cog", action="store_true", help="also write a COG")
    calibrationOptions(run)

    status = commands.add_parser("calibration", help="list calibration files")
    status.add_argument("folder", type=Path)
    status.add_argument("--fetch", action="store_true", help="download what is missing")
    calibrationOptions(status)

    learn = commands.add_parser("learn-artifacts", help="learn artifact bases")
    learn.add_argument("folders", nargs="+", type=Path)
    learn.add_argument("--out", type=Path, required=True)
    learn.add_argument("--scan", default="", help="force this scan (or scan:AT/VS key)")
    learn.add_argument("--recipe", type=Path)
    calibrationOptions(learn)

    check = commands.add_parser("validate", help="compare with an official MTRDR")
    check.add_argument("ours", type=Path, help="our map-projected I/F .img")
    check.add_argument("mtrdr", type=Path, help="the MTRDR's I/F .img")
    check.add_argument("--report", type=Path, default=Path("crism-validation"))
    check.add_argument("--info", type=Path, help="our IN product (default: by name)")
    check.add_argument("--before", type=Path, help="a TRR3 cube, for the spectra plot")
    calibrationOptions(check)
    return parser


def _recipe(path: Path | None) -> Recipe:
    if path is None:
        return Recipe()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise _UsageError(f"cannot read the recipe {path}: {error}") from error
    if not isinstance(data, dict):
        raise _UsageError(f"the recipe {path} must hold a JSON object")
    try:
        return recipeFromDict(data)
    except ValueError as error:
        raise _UsageError(f"{path}: {error}") from error


def _observations(paths: Sequence[Path]) -> list[CrismObservation]:
    found: list[CrismObservation] = []
    for path in paths:
        try:
            if path.is_dir():
                try:
                    found.append(CrismObservation.fromFolder(path))
                except ValueError:  # several observations
                    found += CrismObservation.discover(path)
            else:
                found.append(CrismObservation.fromFiles(path))
        except (OSError, ValueError) as error:
            raise _UsageError(error) from error
    return found


def _library(
    args: argparse.Namespace, paths: Sequence[Path] = ()
) -> CalibrationLibrary:
    """The observations' own folders are searched for calibration files too."""
    folders = [*args.calibration, *(p if p.is_dir() else p.parent for p in paths)]
    return CalibrationLibrary(folders, args.cache or defaultCache(), args.cat_aux)


# --- process -----------------------------------------------------------------------


def _run(args: argparse.Namespace) -> int:
    recipe = _recipe(args.recipe)
    if args.sensor_space:
        recipe.output.sensorSpace.set(True)
    if args.cog:
        recipe.output.cog.set(True)
    observations = _observations(args.paths)
    if not observations:
        raise _UsageError("no observation found")
    library = _library(args, args.paths)
    done = failed = missing = 0
    for observation in observations:
        last = ""

        def progress(fraction: float, message: str) -> None:
            nonlocal last
            if message != last:
                last = message
                print(
                    f"[{fraction:4.0%}] {observation.name}: {message}", file=sys.stderr
                )

        try:
            result = process(
                observation, recipe, args.out, library=library, progress=progress
            )
        except MissingCalibrationError as error:
            failed += 1
            missing += 1
            _error(f"{observation.name}: {error}")
        except Exception as error:  # one bad observation must not stop the rest
            failed += 1
            _log.exception("Processing %s failed", observation.name)
            _error(f"{observation.name}: {type(error).__name__}: {error}")
        else:
            done += 1
            for product in (
                result.mapProjected,
                result.mapSigma,
                result.geometry,
                result.info,
                result.sensorSpace,
            ):
                if product is not None:
                    print(product)
    if not failed:
        return 0
    return EXIT_USAGE if not done and missing == failed else EXIT_FAILED


# --- calibration -------------------------------------------------------------------


def _row(entry: CalibrationFileStatus) -> str:
    if entry.localPath is not None:
        where = f"found    {entry.localPath}"
    else:
        optional = "" if entry.required else " (optional)"
        where = f"missing{optional}  {entry.url or 'no known source'}"
    note = f"  [{entry.note}]" if entry.note else ""
    return f"{where}\n         {entry.kind} {entry.name}{note}"


def _calibration(args: argparse.Namespace) -> int:
    observations = _observations([args.folder])
    if not observations:
        raise _UsageError(f"no observation found under {args.folder}")
    library = _library(args, [args.folder])
    blocked = False
    for observation in observations:
        trr3 = readTrr3(observation.irCube)
        if args.fetch:
            try:
                library.ensure(trr3)
            except MissingCalibrationError:
                blocked = True
        entries = library.status(trr3)
        try:
            local = library.transmissions(trr3, TransmissionSource.PDS_AT)
        except ValueError as error:  # e.g. a CDR with the wrong column count
            raise _UsageError(f"{observation.name}: {error}") from error
        if entries[0].localPath is None or not local:
            blocked = True
        print(f"{observation.name} ({observation.irCube.name})")
        for entry in entries:
            print(_row(entry))
        keys = ", ".join(t.key for t in local) or "none"
        print(f"         transmissions available locally: {keys}")
    return EXIT_USAGE if blocked else 0


# --- learn-artifacts ---------------------------------------------------------------


def columnCo2Median(column: ColumnResiduals, basis: ArtifactBasis | None) -> float:
    """The median over columns of the CO2 residual (``co2Residual``) of the
    column spectra corrected for the transmission, and for ``basis`` when given
    (R / exp(β (ln T + c a)), c from ``artifactScale``): the leave-one-out
    table's metric, also used by ``_tests/test_regression.py``. Columns
    without wavelengths in the CO2 window are left out; NaN when none has."""
    fit = column.selection.fit
    path = shiftedLogTransmission(
        column.wavelengths, column.selection.chosen.values, fit.shiftNm
    )
    if basis is not None:
        probe = AtmosphereModel(column.selection.chosen, fit.shiftNm, basis.spectra)
        scale, _ = artifactScale(
            column.spectra, column.spread, column.wavelengths, probe
        )
        if math.isfinite(scale):
            path = path + scale * basis.spectra
    with np.errstate(invalid="ignore", over="ignore"):
        corrected = column.spectra * np.exp(-fit.beta[:, None] * path)
        window = (column.wavelengths > CO2_WINDOW_NM[0]) & (
            column.wavelengths < CO2_WINDOW_NM[1]
        )
    covered = window.sum(axis=1) >= 4  # not the columns without wavelengths
    if not covered.any():
        return math.nan
    residual = co2Residual(column.wavelengths[covered], corrected[covered])
    finite = residual[np.isfinite(residual)]
    return float(np.median(finite)) if finite.size else math.nan


def _learn(args: argparse.Namespace) -> int:
    recipe = _recipe(args.recipe)
    recipe.atmosphere.scan.set(args.scan)
    observations = _observations(args.folders)
    if not observations:
        raise _UsageError("no observation found")
    library = _library(args, args.folders)
    groups: dict[str, list[ColumnResiduals]] = {}
    failed = 0
    for observation in observations:
        try:
            column = columnResiduals(observation, recipe, library)
        except Exception as error:
            failed += 1
            _error(f"{observation.name}: {type(error).__name__}: {error}")
        else:
            groups.setdefault(column.transmissionKey, []).append(column)
    if groups:
        args.out.mkdir(parents=True, exist_ok=True)
    for key, columns in sorted(groups.items()):
        try:
            _learnKey(key, columns, args.out)
        except ValueError as error:
            failed += 1
            _error(f"{key}: {error}")
    if failed:
        return EXIT_FAILED if groups else EXIT_USAGE
    return 0


def _learnKey(key: str, columns: list[ColumnResiduals], out: Path) -> None:
    def learn(chosen: list[ColumnResiduals]) -> ArtifactBasis:
        return learnArtifactBasis(
            [c.residuals for c in chosen],
            key,
            [c.name for c in chosen],
            wavelengths=columns[0].wavelengths,
            sclks=[c.sclk for c in chosen],
        )

    full = learn(columns)
    saveArtifactBasis(full, out / artifactFileName(key))
    print(
        f"\n{key}: {len(columns)} observation(s), written to {out / artifactFileName(key)}"
    )
    print("Column-spectrum CO2 residual (median over columns), leave-one-out check")
    print(f"{'observation':<22}{'no basis':>12}{'leave-one-out':>15}{'in-sample':>12}")
    for held, column in enumerate(columns):
        others = columns[:held] + columns[held + 1 :]
        leftOut = columnCo2Median(column, learn(others)) if others else math.nan
        print(
            f"{column.name:<22}{columnCo2Median(column, None):>12.5f}"
            f"{leftOut:>15.5f}{columnCo2Median(column, full):>12.5f}"
        )


# --- validate ----------------------------------------------------------------------


def _validate(args: argparse.Namespace) -> int:
    library = _library(args, [args.before] if args.before else [])
    try:
        report = validate(
            args.ours,
            args.mtrdr,
            args.report,
            infoPath=args.info,
            before=args.before,
            library=library,
        )
    except (OSError, ValueError) as error:
        raise _UsageError(error) from error
    print(report)
    return 0


_COMMANDS = {
    "process": _run,
    "calibration": _calibration,
    "learn-artifacts": _learn,
    "validate": _validate,
}


def crismMain(argv: Sequence[str]) -> int:
    """Run the command line on ``argv`` (without the program name) and return
    the exit code (see the module docstring)."""
    args = _parser().parse_args(list(argv))
    try:
        return _COMMANDS[args.command](args)
    except _UsageError as error:
        _error(error)
        return EXIT_USAGE
