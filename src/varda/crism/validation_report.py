"""``validate``: ours against an official MTRDR, on the grid they share.

Both are MTRDR-lattice map-projected I/F cubes (different extents are fine:
the overlap is cut out through their transforms). The cubes are read in
blocks of rows and each metric is computed by ``varda.crism.validation``, the
same code for both, so a metric's own conventions cannot favor either.
Figures are drawn with matplotlib's Figure and Agg canvas directly (no pyplot,
no Qt backend). ``validate`` writes ``report.json`` (every number) and one
PNG per figure.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path

import numpy as np
from attrs import frozen
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from varda.crism.calibration import CalibrationLibrary
from varda.crism.readers import readTrr3
from varda.crism.validation import (
    CO2_WINDOW_NM,
    SHAPE_WINDOW_NM,
    co2Residual,
    compareMaps,
    geolocationOffset,
    sceneResidualRms,
    shapeRatio,
    summaryParameter,
)
from varda.crism.writers import jsonSafe
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource

PARAMETERS = ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310")
_BLOCK_ROWS = 32
_SAMPLE_COUNT = 20000  # pixels behind the spectra and the shape ratio
_LATTICE_TOLERANCE_PX = 0.25
_SOURCE_SAMPLE_BAND = 0  # the IN product's first band


def _nanMedian(values: np.ndarray, axis: int) -> np.ndarray:
    """np.nanmedian, NaN (not a warning) where every value is NaN."""
    finite = np.isfinite(values)
    ordered = np.sort(np.where(finite, values, np.inf), axis=axis)
    count = finite.sum(axis=axis)
    low = np.maximum((count - 1) // 2, 0)
    high = count // 2

    def pick(index: np.ndarray) -> np.ndarray:
        return np.take_along_axis(ordered, np.expand_dims(index, axis), axis).squeeze(
            axis
        )

    return np.where(count > 0, 0.5 * (pick(low) + pick(high)), np.nan)


def _nanMean(values: np.ndarray) -> np.ndarray:
    """The mean over the last axis of the finite values, NaN where there are none."""
    finite = np.isfinite(values)
    count = finite.sum(axis=-1)
    total = np.where(finite, values, 0.0).sum(axis=-1)
    return np.where(count > 0, total / np.maximum(count, 1), np.nan)


@frozen
class _Window:
    """The overlap in each image's pixels: ``rows`` and ``cols`` are ours,
    the reference's are the same shifted by ``shift`` (rows, cols)."""

    rows: slice
    cols: slice
    shift: tuple[int, int]
    latticeResidualPx: float

    @property
    def shape(self) -> tuple[int, int]:
        return self.rows.stop - self.rows.start, self.cols.stop - self.cols.start


def _overlap(ours: ENVIDataSource, reference: ENVIDataSource) -> _Window:
    a, b = ours.transform, reference.transform
    if not (
        np.isclose(a.a, b.a, rtol=1e-6)
        and np.isclose(a.e, b.e, rtol=1e-6)
        and a.b == a.d == b.b == b.d == 0
    ):
        raise ValueError(
            f"the grids differ in pixel size or rotation ({a.a} × {a.e} against "
            f"{b.a} × {b.e}); both must be MTRDR grids"
        )
    shiftCols = (b.c - a.c) / a.a  # ours column = reference column + shift
    shiftRows = (b.f - a.f) / a.e
    residual = max(abs(shiftCols - round(shiftCols)), abs(shiftRows - round(shiftRows)))
    if residual > _LATTICE_TOLERANCE_PX:
        raise ValueError(
            f"the grids are {residual:.2f} px off each other's lattice; both must "
            "be on the MTRDR lattice"
        )
    dc, dr = round(shiftCols), round(shiftRows)
    cols = slice(max(0, dc), min(ours.width, reference.width + dc))
    rows = slice(max(0, dr), min(ours.height, reference.height + dr))
    if cols.stop <= cols.start or rows.stop <= rows.start:
        raise ValueError("the two images do not overlap")
    return _Window(rows, cols, (dr, dc), residual)


def _read(
    source: ENVIDataSource,
    rows: slice,
    cols: slice,
    bands: np.ndarray | None = None,
) -> np.ndarray:
    window = (rows.start, cols.start, rows.stop - rows.start, cols.stop - cols.start)
    return source.getData(bandIndices=bands, window=window).astype(np.float64)


def _nanometers(source: ENVIDataSource) -> np.ndarray:
    wavelengths = np.asarray(source.wavelengths, dtype=np.float64)
    return wavelengths * 1000.0 if np.nanmax(wavelengths) < 20 else wavelengths


@frozen(eq=False)
class _Comparison:
    """The overlap's maps (``(h, w)``) and spectra samples (``(n, b)``)."""

    wavelengths: np.ndarray
    parameters: dict[str, tuple[np.ndarray, np.ndarray]]  # name: (ours, reference)
    co2: tuple[np.ndarray, np.ndarray]
    albedo: tuple[np.ndarray, np.ndarray]
    samples: tuple[np.ndarray, np.ndarray]


def _blocks(rows: slice) -> Iterator[slice]:
    for start in range(rows.start, rows.stop, _BLOCK_ROWS):
        yield slice(start, min(start + _BLOCK_ROWS, rows.stop))


def _compare(
    ours: ENVIDataSource, reference: ENVIDataSource, window: _Window
) -> _Comparison:
    wavelengths = _nanometers(ours)
    refWavelengths = _nanometers(reference)
    if wavelengths.size != refWavelengths.size:
        raise ValueError(
            f"the images have {wavelengths.size} and {refWavelengths.size} bands; "
            "both must be on the MTRDR band vector"
        )
    height, width = window.shape
    maps = {
        name: (np.empty((height, width)), np.empty((height, width)))
        for name in PARAMETERS
    }
    co2 = (np.empty((height, width)), np.empty((height, width)))
    albedo = (np.empty((height, width)), np.empty((height, width)))
    inCo2 = (wavelengths > CO2_WINDOW_NM[0]) & (wavelengths < CO2_WINDOW_NM[1])
    inShape = (wavelengths >= SHAPE_WINDOW_NM[0]) & (wavelengths <= SHAPE_WINDOW_NM[1])
    step = max(1, int(np.sqrt(height * width / _SAMPLE_COUNT)))
    taken: tuple[list[np.ndarray], list[np.ndarray]] = ([], [])
    dr, dc = window.shift
    refCols = slice(window.cols.start - dc, window.cols.stop - dc)
    for rows in _blocks(window.rows):
        out = slice(rows.start - window.rows.start, rows.stop - window.rows.start)
        blocks = (
            (_read(ours, rows, window.cols), wavelengths),
            (
                _read(reference, slice(rows.start - dr, rows.stop - dr), refCols),
                refWavelengths,
            ),
        )
        for side, (cube, wl) in enumerate(blocks):
            for name in PARAMETERS:
                maps[name][side][out] = summaryParameter(name, wl, cube)
            co2[side][out] = co2Residual(wl[inCo2], cube[..., inCo2])
            albedo[side][out] = _nanMean(cube[..., inShape])
            keep = ((np.arange(rows.start, rows.stop) - window.rows.start) % step) == 0
            taken[side].append(cube[keep][:, ::step].reshape(-1, cube.shape[-1]))
    return _Comparison(
        wavelengths,
        maps,
        co2,
        albedo,
        (np.concatenate(taken[0]), np.concatenate(taken[1])),
    )


def _infoPath(oursPath: Path) -> Path:
    """The IN product beside a map-projected I/F: ``_if168l_vmtr`` to ``_in168l_vmtr``."""
    return oursPath.with_name(
        re.sub(r"_if(?=\d{3}[a-z]_vmtr)", "_in", oursPath.stem) + oursPath.suffix
    )


def _co2ByColumn(
    info: ENVIDataSource, window: _Window, co2: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """The median CO2 residual of ours by detector column (the IN product's
    source sample): ``(columns, medians)``."""
    sample = _read(
        info,
        window.rows,
        window.cols,
        np.array([_SOURCE_SAMPLE_BAND]),
    )[..., 0]
    valid = np.isfinite(sample) & np.isfinite(co2)
    columns = np.round(sample[valid]).astype(int)
    values = co2[valid]
    order = np.argsort(columns, kind="stable")
    unique, first = np.unique(columns[order], return_index=True)
    groups = np.split(values[order], first[1:])
    return unique, np.array([np.median(group) for group in groups])


def _beforeSpectrum(
    trr3Path: Path, library: CalibrationLibrary
) -> tuple[np.ndarray, np.ndarray]:
    """The median spectrum of a TRR3 cube, with its median-over-columns wavelengths."""
    trr3 = readTrr3(trr3Path)
    wavelengths = _nanMedian(library.wavelengths(trr3), 0)
    lines, samples, bands = trr3.shape
    step = max(1, int(np.sqrt(lines * samples / _SAMPLE_COUNT)))
    cube = trr3.lines(slice(0, lines, step))[:, ::step].reshape(-1, bands)
    return wavelengths, _nanMedian(cube.astype(np.float64), 0)


def _save(figure: Figure, path: Path) -> None:
    FigureCanvasAgg(figure)
    figure.savefig(path, dpi=110)


def _mapFigure(name: str, ours: np.ndarray, reference: np.ndarray, path: Path) -> None:
    figure = Figure(figsize=(14, 4.5), layout="constrained")
    finite = reference[np.isfinite(reference)]
    low, high = np.percentile(finite, [2, 98]) if finite.size else (0.0, 1.0)
    difference = ours - reference
    spread = (
        np.nanpercentile(np.abs(difference), 98)
        if np.isfinite(difference).any()
        else 1.0
    )
    spread = spread if spread > 0 else 1.0
    panels = (
        ("Ours", ours, "viridis", low, high),
        ("MTRDR", reference, "viridis", low, high),
        ("Ours − MTRDR", difference, "RdBu_r", -spread, spread),
    )
    for axes, (title, image, colors, vmin, vmax) in zip(figure.subplots(1, 3), panels):
        shown = axes.imshow(
            image, cmap=colors, vmin=vmin, vmax=vmax, interpolation="nearest"
        )
        axes.set_title(f"{name}: {title}")
        axes.set_xticks([])
        axes.set_yticks([])
        figure.colorbar(shown, ax=axes, shrink=0.8)
    _save(figure, path)


def _spectraFigure(
    comparison: _Comparison,
    ratio: np.ndarray,
    before: tuple[np.ndarray, np.ndarray] | None,
    path: Path,
) -> None:
    figure = Figure(figsize=(10, 7), layout="constrained")
    top, bottom = figure.subplots(2, 1, sharex=True)
    wl = comparison.wavelengths
    if before is not None:
        top.plot(*before, color="0.6", label="Before (TRR3)")
    top.plot(wl, _nanMedian(comparison.samples[0], 0), label="Ours (after)")
    top.plot(wl, _nanMedian(comparison.samples[1], 0), label="MTRDR", linestyle="--")
    top.set_ylabel("Median I/F")
    top.legend()
    bottom.plot(wl, ratio)
    bottom.axhline(1.0, color="0.5", linewidth=0.8)
    bottom.set_ylabel("Shape ratio, ours ÷ MTRDR")
    bottom.set_xlabel("Wavelength (nm)")
    _save(figure, path)


def _columnFigure(
    columns: np.ndarray, medians: np.ndarray, reference: float, path: Path
) -> None:
    figure = Figure(figsize=(10, 4), layout="constrained")
    axes = figure.subplots()
    axes.plot(columns, medians, linewidth=0.8, label="Ours, by detector column")
    axes.axhline(reference, color="C1", linestyle="--", label="MTRDR median")
    axes.set_xlabel("Detector column (source sample)")
    axes.set_ylabel("Median CO2 residual (1940–2100 nm)")
    axes.legend()
    _save(figure, path)


def _median(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(np.median(finite)) if finite.size else float("nan")


def validate(
    oursPath: Path,
    mtrdrPath: Path,
    folder: Path,
    *,
    infoPath: Path | None = None,
    before: Path | None = None,
    library: CalibrationLibrary | None = None,
) -> Path:
    """Compare ``oursPath`` with ``mtrdrPath`` and write ``report.json`` and
    the figures into ``folder``; returns the report's path. ``infoPath``
    is the IN product (found beside ours by name when not given; the CO2-by-
    column figure is skipped, and said so in the report, when there is none).
    ``before`` is a TRR3 cube whose median spectrum joins the spectra figure
    (it needs ``library`` for the wavelengths)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    ours, reference = ENVIDataSource(str(oursPath)), ENVIDataSource(str(mtrdrPath))
    try:
        window = _overlap(ours, reference)
        comparison = _compare(ours, reference, window)
        wavelengths = comparison.wavelengths
        report: dict[str, object] = {
            "ours": str(oursPath),
            "mtrdr": str(mtrdrPath),
            "overlap": {
                "pixels": window.shape[0] * window.shape[1],
                "rows": window.shape[0],
                "columns": window.shape[1],
                "latticeResidualPx": window.latticeResidualPx,
            },
            "parameters": {},
        }
        for name, (a, b) in comparison.parameters.items():
            fit = compareMaps(a, b)
            report["parameters"][name] = {
                "pearson": fit.pearson,
                "medianBias": fit.medianBias,
                "n": fit.n,
            }
            _mapFigure(name, a, b, folder / f"{name}.png")
        try:
            dx, dy = geolocationOffset(*comparison.albedo)
            report["geolocationOffset"] = {"dxPx": dx, "dyPx": dy}
        except ValueError as error:
            report["geolocationOffset"] = {"error": str(error)}
        ratio = shapeRatio(*comparison.samples, wavelengths)
        finite = np.isfinite(ratio)
        report["shapeRatio"] = {
            "median": _median(ratio),
            "min": float(np.nanmin(ratio)) if finite.any() else None,
            "max": float(np.nanmax(ratio)) if finite.any() else None,
            "rmsFromOne": float(np.sqrt(np.nanmean((ratio - 1) ** 2)))
            if finite.any()
            else None,
            "wavelengthsNm": wavelengths,
            "byBand": ratio,
        }
        inCo2 = (wavelengths > CO2_WINDOW_NM[0]) & (wavelengths < CO2_WINDOW_NM[1])
        report["co2Residual"] = {
            side: {
                "median": _median(co2),
                "sceneRms": sceneResidualRms(wavelengths[inCo2], samples[:, inCo2]),
            }
            for side, co2, samples in zip(
                ("ours", "mtrdr"), comparison.co2, comparison.samples
            )
        }
        info = infoPath if infoPath is not None else _infoPath(Path(oursPath))
        if info.is_file():
            source = ENVIDataSource(str(info))
            try:
                columns, medians = _co2ByColumn(source, window, comparison.co2[0])
            finally:
                source.close()
            report["co2ByColumn"] = {"column": columns, "median": medians}
            _columnFigure(
                columns,
                medians,
                _median(comparison.co2[1]),
                folder / "co2_by_column.png",
            )
        else:
            report["co2ByColumn"] = {"skipped": f"no IN product at {info}"}
        beforeSpectrum = None
        if before is not None:
            if library is None:
                raise ValueError(
                    "a calibration library is needed for the TRR3 wavelengths"
                )
            beforeSpectrum = _beforeSpectrum(before, library)
        _spectraFigure(comparison, ratio, beforeSpectrum, folder / "spectra.png")
    finally:
        ours.close()
        reference.close()
    path = folder / "report.json"
    path.write_text(json.dumps(jsonSafe(report), indent=2))
    return path
