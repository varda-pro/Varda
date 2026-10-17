"""Factor analysis and target transformation (FATT).

A scene's spectra are modeled as linear mixtures of a few components. Factor
analysis finds the space those mixtures span; a library spectrum that lies in
that space (its target transformation reproduces it) is a candidate component
of the scene (Malinowski 1991; Bandfield et al. 2000; Thomas & Bandfield
2017). Results are candidates to confirm, never detections on their own.

Two decompositions are offered:

- Covariance (mean removed), as Bandfield and Thomas: the eigenvectors of the
  covariance matrix plus the mean spectrum.
- Hysime (Bioucas-Dias & Nascimento 2008), as Lin, Tarnas et al. (2021): the
  signal subspace of the non-centered correlation matrix, its size chosen by
  comparing signal and noise power.

The fit is judged by the normalized RMSE of Lin, Tarnas et al. (2021): both
spectra are divided by their sum over the bands before the RMSE, so overall
brightness doesn't matter. ``windowedNrmse`` is their DAFA/TT, which runs the
analysis in small moving windows to locate a component.

Pure numpy; spectra are (pixels, bands) arrays.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import attrs
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from varda.analysis.spectral_library import LibrarySpectrum, resampleTo
from varda.common.parameter import LabeledEnum

# Lin, Tarnas et al. (2021), set from CRISM scenes with confirmed detections
# over 1.7-2.6 µm (about 130 bands); it scales with the band count
NRMSE_THRESHOLD = 1.5e-4
DEFAULT_RANGE_NM = (1700.0, 2600.0)
# DAFA/TT apertures of about 50 pixels; a pixel counts when all agree
DEFAULT_WINDOW_SHAPES = ((7, 7), (5, 10), (10, 5))
DEFAULT_WINDOW_FACTORS = 15
MIN_BANDS = 5
# Memory for the windows being fitted at once, across all threads. The
# windows are cut from the image chunk by chunk, so this bounds the map's
# working memory whatever the image size.
WINDOW_MEMORY_BUDGET = 2**30


class FactorMethod(LabeledEnum):
    COVARIANCE = "Covariance (mean removed)"
    HYSIME = "Hysime (signal subspace)"


@attrs.frozen
class FactorAnalysis:
    method: FactorMethod
    eigenvalues: np.ndarray  # largest first
    vectors: np.ndarray  # (bands, bands), columns match eigenvalues
    mean: np.ndarray  # the mean spectrum (part of the covariance basis)
    indicator: np.ndarray  # Malinowski's IND(n) for n = 1 .. bands - 1
    suggested: int  # the suggested number of factors

    @property
    def maxFactors(self) -> int:
        return self.vectors.shape[1] + (self.method is FactorMethod.COVARIANCE)

    def basis(self, factors: int) -> np.ndarray:
        """(bands, factors) spanning the factor space: the mean and the first
        ``factors - 1`` eigenvectors for the covariance method, the first
        ``factors`` for Hysime."""
        factors = int(np.clip(factors, 1, self.maxFactors))
        if self.method is FactorMethod.COVARIANCE:
            return np.column_stack([self.mean, self.vectors[:, : factors - 1]])
        return self.vectors[:, :factors]


@attrs.frozen
class TargetFit:
    spectrum: LibrarySpectrum
    target: np.ndarray  # the library spectrum at the image's bands
    fit: np.ndarray  # its target transformation
    nrmse: float
    angle: float  # radians


def indicatorFunction(spectra: np.ndarray) -> np.ndarray:
    """Malinowski's indicator function IND(n) = RE(n) / (c - n)², about the
    origin, for n = 1 .. c - 1 (``ind[n - 1]``). Its minimum estimates the
    number of components."""
    x = np.asarray(spectra, dtype=np.float64)
    r, c = x.shape
    if r < c:  # IND is defined for rows >= columns; transpose keeps the eigenvalues
        x = x.T
        r, c = c, r
    eigenvalues = np.clip(np.linalg.eigvalsh(x.T @ x)[::-1], 0.0, None)
    n = np.arange(1, c)
    tail = np.cumsum(eigenvalues[::-1])[::-1]  # tail[n] = sum of eigenvalues[n:]
    realError = np.sqrt(tail[n] / (r * (c - n)))
    return realError / (c - n) ** 2


def hysime(spectra: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """The signal subspace of ``spectra`` (Bioucas-Dias & Nascimento 2008):
    eigenvalues and eigenvectors of the signal correlation matrix, largest
    first, and how many of them carry more signal than noise.

    The noise of each band is what a least-squares fit from all the other
    bands leaves over."""
    y = np.asarray(spectra, dtype=np.float64).T  # (bands, pixels)
    bands, pixels = y.shape
    rr = y @ y.T
    rri = np.linalg.inv(rr + 1e-6 * np.trace(rr) / bands * np.eye(bands))
    noise = np.empty_like(y)
    for i in range(bands):
        xx = rri - np.outer(rri[:, i], rri[i, :]) / rri[i, i]
        others = rr[:, i].copy()
        others[i] = 0.0
        beta = xx @ others
        beta[i] = 0.0
        noise[i] = y[i] - beta @ y
    rn = noise @ noise.T / pixels
    signal = y - noise
    rx = signal @ signal.T / pixels
    ry = y @ y.T / pixels
    eigenvalues, vectors = np.linalg.eigh(rx)
    eigenvalues, vectors = eigenvalues[::-1], vectors[:, ::-1]
    rn = rn + np.trace(rx) / bands / 1e5 * np.eye(bands)
    signalPower = np.einsum("bi,bc,ci->i", vectors, ry, vectors)
    noisePower = np.einsum("bi,bc,ci->i", vectors, rn, vectors)
    count = int(np.sum(-signalPower + 2 * noisePower < 0))
    return eigenvalues, vectors, max(count, 1)


def analyzeFactors(spectra: np.ndarray, method: FactorMethod) -> FactorAnalysis:
    """The factor analysis of ``spectra`` (pixels, bands), all finite."""
    x = np.asarray(spectra, dtype=np.float64)
    if x.ndim != 2 or x.shape[0] < 2 or x.shape[1] < MIN_BANDS:
        raise ValueError(
            f"Factor analysis needs at least 2 spectra of {MIN_BANDS} or more bands."
        )
    if not np.isfinite(x).all():
        raise ValueError("Factor analysis needs finite spectra (leave out no-data).")
    indicator = indicatorFunction(x)
    mean = x.mean(axis=0)
    if method is FactorMethod.HYSIME:
        eigenvalues, vectors, suggested = hysime(x)
    else:
        eigenvalues, vectors = np.linalg.eigh(np.cov(x - mean, rowvar=False))
        eigenvalues, vectors = eigenvalues[::-1], vectors[:, ::-1]
        # IND counts the components about the origin: the mean plus n - 1 vectors
        suggested = int(np.argmin(indicator)) + 1
    return FactorAnalysis(method, eigenvalues, vectors, mean, indicator, suggested)


def targetTransform(basis: np.ndarray, target: np.ndarray) -> np.ndarray:
    """The best reproduction of ``target`` within the factor space: least
    squares over the bands the target covers, evaluated at every band."""
    target = np.asarray(target, dtype=np.float64)
    finite = np.isfinite(target) & np.isfinite(basis).all(axis=1)
    coefficients, *_ = np.linalg.lstsq(basis[finite], target[finite], rcond=None)
    return basis @ coefficients


def normalizedRmse(target: np.ndarray, fit: np.ndarray) -> float:
    """RMSE between the spectra after dividing each by its sum (over the bands
    both cover), as Lin, Tarnas et al. (2021)."""
    both = np.isfinite(target) & np.isfinite(fit)
    t, f = target[both], fit[both]
    return float(np.sqrt(np.mean((t / t.sum() - f / f.sum()) ** 2)))


def spectralAngle(a: np.ndarray, b: np.ndarray) -> float:
    both = np.isfinite(a) & np.isfinite(b)
    a, b = a[both], b[both]
    cosine = a @ b / (np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.arccos(np.clip(cosine, -1.0, 1.0)))


def sampleSpectra(cube: np.ndarray, usable: np.ndarray, stride: int = 1) -> np.ndarray:
    """The spectra of the usable pixels on every ``stride``-th row and column,
    as (pixels, bands)."""
    take = np.zeros_like(usable, dtype=bool)
    take[::stride, ::stride] = True
    return cube[usable & take & np.isfinite(cube).all(axis=2)]


def resampleLibrary(
    library: Sequence[LibrarySpectrum],
    wavelengths: np.ndarray,
    fwhm: np.ndarray | None = None,
) -> tuple[list[LibrarySpectrum], np.ndarray]:
    """The library spectra covering at least five of the bands ``wavelengths``
    (nm) describe, and those spectra at the bands as (spectra, bands)."""
    kept, targets = [], []
    for spectrum in library:
        target = resampleTo(spectrum, wavelengths, fwhm)
        if np.isfinite(target).sum() >= MIN_BANDS:
            kept.append(spectrum)
            targets.append(target)
    return kept, np.array(targets).reshape(len(targets), len(wavelengths))


def rankTargets(
    basis: np.ndarray, spectra: Sequence[LibrarySpectrum], targets: np.ndarray
) -> list[TargetFit]:
    """The target transformation of each target (a row of ``targets``, at the
    basis's bands), best (lowest normalized RMSE) first."""
    fits = np.empty_like(targets)
    complete = np.isfinite(targets).all(axis=1)
    if complete.any():  # one least-squares solve for all the complete targets
        coefficients, *_ = np.linalg.lstsq(basis, targets[complete].T, rcond=None)
        fits[complete] = (basis @ coefficients).T
    for i in np.flatnonzero(~complete):
        fits[i] = targetTransform(basis, targets[i])
    results = [
        TargetFit(
            spectrum,
            target,
            fit,
            normalizedRmse(target, fit),
            spectralAngle(target, fit),
        )
        for spectrum, target, fit in zip(spectra, targets, fits, strict=True)
    ]
    return sorted(results, key=lambda f: f.nrmse)


RANKING_COLUMNS = ("rank", "target", "nrmse", "angle_deg", "ticked", "fits")


def writeRanking(
    path: str | Path,
    fits: Sequence[TargetFit],
    settings: Mapping[str, object],
    *,
    ticked: Collection[str] = (),
    threshold: float | None = None,
) -> None:
    """The ranked targets as CSV, best first, after ``#`` lines giving the
    settings that made the ranking. ``nrmse`` is unitless (the window shows
    it ×10⁻⁴); ``angle_deg`` is the spectral angle between a target and its
    fit; ``fits`` says whether nRMSE is below ``threshold`` (empty without)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write("# Varda FATT ranking (target transformation of library spectra)\n")
        for key, value in settings.items():
            f.write(f"# {key}: {value}\n")
        f.write(
            "# nrmse: normalized RMSE of Lin, Tarnas et al. (2021), unitless; "
            "angle_deg: spectral angle between the target and its fit\n"
        )
        writer = csv.writer(f)
        writer.writerow(RANKING_COLUMNS)
        for rank, fit in enumerate(fits, start=1):
            writer.writerow(
                [
                    rank,
                    fit.spectrum.name,
                    f"{fit.nrmse:.6g}",
                    f"{np.degrees(fit.angle):.4f}",
                    str(fit.spectrum.name in ticked).lower(),
                    "" if threshold is None else str(fit.nrmse < threshold).lower(),
                ]
            )


def fitLibrary(
    basis: np.ndarray,
    wavelengths: np.ndarray,
    library: Sequence[LibrarySpectrum],
    fwhm: np.ndarray | None = None,
) -> list[TargetFit]:
    """The target transformation of every library spectrum, best first.
    ``basis`` is at the bands ``wavelengths`` (nm) describe; spectra covering
    fewer than five of them are skipped."""
    return rankTargets(basis, *resampleLibrary(library, wavelengths, fwhm))


def windowedNrmse(
    cube: np.ndarray,
    targets: np.ndarray,
    factors: int = DEFAULT_WINDOW_FACTORS,
    shapes: Sequence[tuple[int, int]] = DEFAULT_WINDOW_SHAPES,
    progress=None,
    memoryBudget: int = WINDOW_MEMORY_BUDGET,
) -> np.ndarray:
    """DAFA/TT (Lin, Tarnas et al. 2021): target transformation in every
    window of each shape (rows, cols), stepped one pixel at a time.

    The factor space of a window is the first ``factors`` singular vectors of
    its spectra (about the origin). A pixel's value for a target is its best
    (lowest) normalized RMSE among the windows of a shape that contain it,
    and the worst of those across the shapes, so thresholding it keeps the
    pixels all shapes agree on. Windows with a non-finite pixel are left out;
    NaN where none is left. Returns (targets, rows, cols).

    ``targets`` is (count, bands), finite at every band. ``progress(fraction)``
    is called as windows are done. ``memoryBudget`` (bytes) bounds the
    windows in work at once; it changes the speed, never the result.
    """
    cube = np.asarray(cube, dtype=np.float64)
    targets = np.atleast_2d(np.asarray(targets, dtype=np.float64))
    if not np.isfinite(targets).all():
        raise ValueError("Targets must cover every band of the window analysis.")
    rows, cols, bands = cube.shape
    workers = os.cpu_count() or 1
    unit = targets / targets.sum(axis=1, keepdims=True)
    consensus = np.full((len(targets), rows, cols), -np.inf)
    totals = [(rows - h + 1) * (cols - w + 1) for h, w in shapes]
    done = 0
    for (h, w), total in zip(shapes, totals, strict=True):
        if h > rows or w > cols:
            raise ValueError(f"A {h}×{w} window doesn't fit the {rows}×{cols} image.")
        # a view: (window rows, window cols, bands, h, w), no copy
        windows = sliding_window_view(cube, window_shape=(h, w), axis=(0, 1))
        windowRows, windowCols = windows.shape[:2]
        perWindow = np.full((total, len(targets)), np.nan)
        k = min(factors, h * w)
        size = _chunkSize(h * w, bands, memoryBudget, workers)

        def chunk(start: int) -> int:
            index = np.arange(start, min(start + size, total))
            # Only this chunk's windows are copied out: copying them all at
            # once took (windows x bands x h x w) doubles, about 15 GiB per
            # shape for a CRISM scene, more than a laptop has.
            x = windows[index // windowCols, index % windowCols]
            # band-major, as the windows were always laid out: the products
            # sum in that order, so results match earlier versions bit for bit
            x = np.ascontiguousarray(x.reshape(len(index), bands, h * w))
            x = x.transpose(0, 2, 1)
            ok = np.isfinite(x).all(axis=(1, 2))
            if ok.any():
                perWindow[start : start + len(index)][ok] = _windowNrmse(
                    x[ok], targets, unit, k
                )
            return len(index)

        # numpy's batched linear algebra releases the GIL, so threads scale
        with ThreadPoolExecutor(workers) as pool:
            for count in pool.map(chunk, range(0, total, size)):
                done += count
                if progress is not None:
                    progress(done / sum(totals))
        best = _coveringMinimum(perWindow.reshape(windowRows, windowCols, -1), h, w)
        consensus = np.maximum(consensus, best.transpose(2, 0, 1))
    return consensus


def _chunkSize(pixels: int, bands: int, budget: int, workers: int) -> int:
    """Windows per chunk so that every worker's chunk fits the budget. A
    window needs its spectra (copied out, then transposed for the products)
    and its pixels x pixels Gram matrix, eigenvectors and LAPACK workspace."""
    perWindow = 8 * (3 * pixels * bands + 4 * pixels * pixels)
    return max(1, budget // (workers * perWindow))


def _windowNrmse(
    x: np.ndarray, targets: np.ndarray, unit: np.ndarray, k: int
) -> np.ndarray:
    """Normalized RMSE of each target's projection onto each window's first
    ``k`` singular vectors; x is (windows, pixels, bands). The projection is
    found from the small pixels × pixels Gram matrix."""
    gram = x @ x.transpose(0, 2, 1)
    values, vectors = np.linalg.eigh(gram)
    values, vectors = values[:, -k:], vectors[:, :, -k:]
    scale = np.where(values > values[:, -1:] * 1e-12, 1.0 / values, 0.0)
    coefficients = vectors.transpose(0, 2, 1) @ (x @ targets.T) * scale[:, :, None]
    fits = x.transpose(0, 2, 1) @ (vectors @ coefficients)  # (windows, bands, targets)
    fits = fits / fits.sum(axis=1, keepdims=True)
    return np.sqrt(np.mean((fits - unit.T[None]) ** 2, axis=1))


def _coveringMinimum(perWindow: np.ndarray, h: int, w: int) -> np.ndarray:
    """For each pixel, the minimum over the windows (by top-left corner) that
    contain it; NaN where no valid window does."""
    windowRows, windowCols, count = perWindow.shape
    values = np.where(np.isnan(perWindow), np.inf, perWindow)
    best = np.full((windowRows + h - 1, windowCols + w - 1, count), np.inf)
    for i in range(h):
        for j in range(w):
            view = best[i : i + windowRows, j : j + windowCols]
            np.minimum(view, values, out=view)
    return np.where(np.isinf(best), np.nan, best)
