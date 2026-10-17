"""The fusion prototype's co-registered 36 m stack, gains, noise and
repeatability (ported from data-for-paper/fusion_prototype/code: stage1_grid.py,
fusion_core.py, stage2_fuse.py, stage2b_diag.py, stage3_figs.py)."""

import itertools
import math
import re
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from attrs import frozen
from rasterio.warp import Resampling, reproject
from scipy.ndimage import uniform_filter1d
from scipy.optimize import nnls
from skimage.registration import phase_cross_correlation

from varda.crism.validation import summaryParameter

GRID_M = 36.0
WAVELENGTH_RANGE_NM = (1020.0, 2600.0)
ALBEDO_NM = (1300.0, 1700.0)
MIN_OVERLAP = 3000  # 36 m pixels
GAIN_BLOCK = 4
NOISE_COMPONENTS = 3
PAIR_POINTS = 60000
TILE, TILE_STEP = 48, 24
CHUNK = 24


def _wavelengths(path: Path) -> np.ndarray:
    text = path.with_suffix(".hdr").read_text()
    values = re.search(r"wavelength = \{([^}]*)\}", text)
    assert values is not None
    w = np.array([float(x) for x in values.group(1).split(",")])
    return w * 1000 if w.max() < 20 else w


@frozen
class Grid:
    transform: Affine
    width: int
    height: int


def commonGrid(paths: list[Path], snapTo: Path) -> Grid:
    """stage1_grid.grid: the union of the bounds at 36 m, snapped to ``snapTo``'s
    (the HRL's) lattice."""
    bounds = []
    for path in paths:
        with rasterio.open(path) as d:
            bounds.append(d.bounds)
    with rasterio.open(snapTo) as d:
        hx, hy = d.bounds.left, d.bounds.top
    x0 = min(b.left for b in bounds)
    y0 = min(b.bottom for b in bounds)
    x1 = max(b.right for b in bounds)
    y1 = max(b.top for b in bounds)
    gx0 = hx - math.ceil((hx - x0) / GRID_M) * GRID_M
    gy1 = hy + math.ceil((y1 - hy) / GRID_M) * GRID_M
    return Grid(
        Affine(GRID_M, 0, gx0, 0, -GRID_M, gy1),
        int(math.ceil((x1 - gx0) / GRID_M)),
        int(math.ceil((gy1 - y0) / GRID_M)),
    )


def regrid(
    path: Path, grid: Grid, bands: np.ndarray, correction: Affine | None
) -> np.ndarray:
    """stage1_grid.regrid_bands: bands (0-based) onto the grid, area-averaged
    from finer pixels and bilinear from equal ones; ``correction`` moves the
    image onto the reference (map coordinates)."""
    out = np.full((len(bands), grid.height, grid.width), np.nan, np.float32)
    with rasterio.open(path) as d:
        source = (correction or Affine.identity()) * d.transform
        finer = abs(d.transform.a) < GRID_M - 1e-6
        method = Resampling.average if finer else Resampling.bilinear
        for n, b in enumerate(bands):
            a = d.read(int(b) + 1).astype(np.float32)
            a[(a >= 65534) | (a <= -1)] = np.nan
            reproject(
                a,
                out[n],
                src_transform=source,
                src_crs=d.crs,
                dst_transform=grid.transform,
                dst_crs=d.crs,
                resampling=method,
                src_nodata=np.nan,
                dst_nodata=np.nan,
            )
    return out


def tileShifts(reference: np.ndarray, moving: np.ndarray) -> np.ndarray:
    """stage1_grid.tile_shifts: (row, col, dr, dc) at 48 px tiles every 24 px."""
    found = []
    height, width = reference.shape
    for r in range(0, height - TILE, TILE_STEP):
        for c in range(0, width - TILE, TILE_STEP):
            a = reference[r : r + TILE, c : c + TILE]
            b = moving[r : r + TILE, c : c + TILE]
            if not (np.isfinite(a).all() and np.isfinite(b).all()):
                continue
            if a.std() < 1e-4 or b.std() < 1e-4:
                continue
            s, _, _ = phase_cross_correlation(
                a - a.mean(), b - b.mean(), upsample_factor=20, normalization=None
            )
            if np.abs(s).max() < 4:
                found.append([r + TILE / 2, c + TILE / 2, s[0], s[1]])
    return np.array(found)


def fitAffine(
    shifts: np.ndarray, grid: Grid, trim: float = 2.5
) -> tuple[Affine, float]:
    """stage1_grid.fit_affine: the map-coordinate affine that moves the image
    by the trimmed linear fit of the tile shifts, and the median residual (px)."""
    r, c, dr, dc = shifts.T
    keep = np.ones(len(r), bool)
    residual = np.zeros(len(r))
    pr = pc = np.zeros(3)
    for _ in range(3):
        x = np.c_[np.ones(keep.sum()), r[keep], c[keep]]
        pr, *_ = np.linalg.lstsq(x, dr[keep], rcond=None)
        pc, *_ = np.linalg.lstsq(x, dc[keep], rcond=None)
        xa = np.c_[np.ones(len(r)), r, c]
        residual = np.hypot(dr - xa @ pr, dc - xa @ pc)
        keep = residual < trim * (1.4826 * np.median(residual[keep]) + 1e-6)
    size, x0, y1 = GRID_M, grid.transform.c, grid.transform.f

    def linear(p: np.ndarray) -> tuple[float, float, float]:
        return p[0] + p[1] * y1 / size - p[2] * x0 / size, p[2] / size, -p[1] / size

    cc, cx, cy = linear(pc)
    rc, rx, ry = linear(pr)
    return (
        Affine(
            1 + size * cx, size * cy, size * cc, -size * rx, 1 - size * ry, -size * rc
        ),
        float(np.median(residual[keep])),
    )


def gainSolve(
    medians: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]],
    count: int,
    reference: int,
) -> np.ndarray:
    """fusion_core.gain_solve: log g_i + log m_i = log g_j + log m_j over the pairs."""
    keys = list(medians)
    bands = len(next(iter(medians.values()))[0])
    unknown = [k for k in range(count) if k != reference]
    a = np.zeros((len(keys), len(unknown)))
    for row, (i, j) in enumerate(keys):
        if i != reference:
            a[row, unknown.index(i)] = 1.0
        if j != reference:
            a[row, unknown.index(j)] = -1.0
    gains = np.ones((count, bands))
    for b in range(bands):
        y = np.array(
            [np.log(medians[p][1][b]) - np.log(medians[p][0][b]) for p in keys]
        )
        solution, *_ = np.linalg.lstsq(a, y, rcond=None)
        for n, k in enumerate(unknown):
            gains[k, b] = np.exp(solution[n])
    return gains


def blockMean(a: np.ndarray, f: int) -> np.ndarray:
    *lead, r, c = a.shape
    r2, c2 = r // f * f, c // f * f
    return a[..., :r2, :c2].reshape(*lead, r2 // f, f, c2 // f, f).mean(axis=(-1, -3))


def robustVariance(x: np.ndarray) -> np.ndarray:
    median = np.median(x, axis=0, keepdims=True)
    return (1.4826 * np.median(np.abs(x - median), axis=0)) ** 2


def projectOut(d: np.ndarray, k: int, smooth: int = 9) -> tuple[np.ndarray, np.ndarray]:
    """fusion_core.project_out with leverage."""
    dc = d - d.mean(0)
    ds = uniform_filter1d(dc, size=smooth, axis=1, mode="nearest")
    cov = ds.T @ ds / max(ds.shape[0] - 1, 1)
    w, v = np.linalg.eigh(cov)
    top = v[:, np.argsort(w)[::-1][:k]]
    return dc - (dc @ top) @ top.T, (top**2).sum(1)


def pairNoise(yi: np.ndarray, yj: np.ndarray, k: int = NOISE_COMPONENTS) -> np.ndarray:
    """fusion_core.pair_noise (the robust variance) of (n, b) aligned spectra."""
    d = yi - yj
    d = d[np.all(np.isfinite(d), axis=1)]
    r, leverage = projectOut(d, k)
    return robustVariance(r) / np.maximum(1.0 - leverage, 0.5)


def threeCorneredHat(
    pairs: dict[tuple[int, int], np.ndarray], count: int
) -> np.ndarray:
    keys = list(pairs)
    bands = len(next(iter(pairs.values())))
    a = np.zeros((len(keys), count))
    for row, (i, j) in enumerate(keys):
        a[row, i] = a[row, j] = 1.0
    out = np.zeros((count, bands))
    for b in range(bands):
        out[:, b], _ = nnls(a, np.array([pairs[p][b] for p in keys]))
    return out


def smoothNoise(s: np.ndarray, size: int = 7) -> np.ndarray:
    from numpy.lib.stride_tricks import sliding_window_view

    half = size // 2
    p = np.where(s > 0, s, np.nan)
    padded = np.pad(p, ((0, 0), (half, half)), mode="edge")
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            smoothed = np.nanmedian(sliding_window_view(padded, size, axis=1), axis=-1)
            fill = np.nanmedian(p, axis=1, keepdims=True)
    return np.where(np.isfinite(smoothed), smoothed, fill)


@frozen(eq=False)
class Stack:
    names: tuple[str, ...]
    wavelengths: np.ndarray  # (b,)
    valid: np.ndarray  # (n, H, W)
    values: np.ndarray  # (n, b, p) at the points, NaN where invalid
    rows: np.ndarray
    cols: np.ndarray
    blockMedians: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]]
    registration: dict[str, tuple[str, float]]


def buildStack(
    paths: dict[str, Path], grid: Grid, points: int = 80000, seed: int = 3
) -> Stack:
    names = tuple(paths)
    reference = names[0]
    wl = _wavelengths(paths[reference])
    wl = wl[(wl >= WAVELENGTH_RANGE_NM[0]) & (wl <= WAVELENGTH_RANGE_NM[1])]
    bands = {}
    for name, path in paths.items():
        own = _wavelengths(path)
        nearest = np.abs(own[None, :] - wl[:, None]).argmin(axis=1)
        assert np.all(np.abs(own[nearest] - wl) < 1.0), name
        bands[name] = nearest
    albedoBands = (wl >= ALBEDO_NM[0]) & (wl <= ALBEDO_NM[1])
    albedo = {
        n: np.nanmean(regrid(p, grid, bands[n][albedoBands], None), 0)
        for n, p in paths.items()
    }
    corrections: dict[str, Affine | None] = {reference: None}
    corrected = {reference: albedo[reference]}
    registration = {reference: (reference, 0.0)}
    for name in names[1:]:
        overlap = {
            k: int((np.isfinite(v) & np.isfinite(albedo[name])).sum())
            for k, v in corrected.items()
        }
        target = max(overlap, key=lambda k: overlap[k])
        shifts = tileShifts(corrected[target], albedo[name])
        assert len(shifts) >= 8, f"{name}: {len(shifts)} tiles against {target}"
        corrections[name], residual = fitAffine(shifts, grid)
        corrected[name] = np.nanmean(
            regrid(paths[name], grid, bands[name][albedoBands], corrections[name]), 0
        )
        registration[name] = (target, residual)
    valid = np.stack(
        [
            np.isfinite(regrid(p, grid, bands[n][::8], corrections[n])).mean(0) > 0.97
            for n, p in paths.items()
        ]
    )
    looks = valid.sum(0)
    rng = np.random.default_rng(seed)
    candidates = np.flatnonzero((looks >= 2).ravel())
    chosen = np.sort(
        rng.choice(candidates, min(points, candidates.size), replace=False)
    )
    rows, cols = np.unravel_index(chosen, looks.shape)
    pairs = [
        (i, j)
        for i, j in itertools.combinations(range(len(names)), 2)
        if (valid[i] & valid[j]).sum() >= MIN_OVERLAP
    ]
    medians = {p: (np.zeros(wl.size), np.zeros(wl.size)) for p in pairs}
    values = np.full((len(names), wl.size, rows.size), np.nan, np.float32)
    for start in range(0, wl.size, CHUNK):
        chunk = slice(start, min(start + CHUNK, wl.size))
        cubes = [
            np.where(
                valid[k][None],
                regrid(paths[n], grid, bands[n][chunk], corrections[n]),
                np.nan,
            )
            for k, n in enumerate(names)
        ]
        for k, cube in enumerate(cubes):
            values[k, chunk] = cube[:, rows, cols]
        for i, j in pairs:
            both = valid[i] & valid[j]
            bi = blockMean(np.where(both, cubes[i], np.nan), GAIN_BLOCK)
            bj = blockMean(np.where(both, cubes[j], np.nan), GAIN_BLOCK)
            for n, b in enumerate(range(chunk.start, chunk.stop)):
                ok = np.isfinite(bi[n]) & np.isfinite(bj[n])
                medians[(i, j)][0][b] = np.median(bi[n][ok])
                medians[(i, j)][1][b] = np.median(bj[n][ok])
    return Stack(names, wl, valid, values, rows, cols, medians, registration)


def subsetGains(stack: Stack, members: list[int]) -> np.ndarray:
    """The joint gains of ``members`` (the first held at 1), (len(members), b)."""
    index = {k: n for n, k in enumerate(members)}
    medians = {
        (index[i], index[j]): m
        for (i, j), m in stack.blockMedians.items()
        if i in index and j in index
    }
    return gainSolve(medians, len(members), 0)


def noiseVariance(
    stack: Stack, members: list[int], gains: np.ndarray, seed: int = 7
) -> np.ndarray:
    """stage2_fuse: the three-cornered hat on gain-aligned pair differences, smoothed."""
    rng = np.random.default_rng(seed)
    pairs = {}
    index = {k: n for n, k in enumerate(members)}
    for i, j in itertools.combinations(members, 2):
        both = np.isfinite(stack.values[i, 0]) & np.isfinite(stack.values[j, 0])
        both &= np.isfinite(stack.values[i]).all(0) & np.isfinite(stack.values[j]).all(
            0
        )
        idx = np.flatnonzero(both)
        if idx.size < MIN_OVERLAP // 2:
            continue
        if idx.size > PAIR_POINTS:
            idx = np.sort(rng.choice(idx, PAIR_POINTS, replace=False))
        yi = (gains[index[i]][:, None] * stack.values[i][:, idx]).T
        yj = (gains[index[j]][:, None] * stack.values[j][:, idx]).T
        pairs[(index[i], index[j])] = pairNoise(yi, yj)
    return smoothNoise(threeCorneredHat(pairs, len(members)))


def chiSquareGood(wl: np.ndarray) -> np.ndarray:
    return ~(((wl > 1950) & (wl < 2080)) | (wl > 2560))


def shapeChiSquare(y: np.ndarray, v: np.ndarray, wl: np.ndarray) -> float:
    """stage2b_diag.chi2_stats on shape-normalized spectra (each ÷ its
    1.3–1.7 µm mean): y (n_obs, b, pts), v (n_obs, b)."""
    norm = (wl >= ALBEDO_NM[0]) & (wl <= ALBEDO_NM[1])
    good = chiSquareGood(wl)
    m = np.nanmean(y[:, norm, :], 1, keepdims=True)
    ysh = y / m
    vsh = v / np.nanmedian(m, axis=2) ** 2
    w = np.where(np.isfinite(ysh), 1 / vsh[:, :, None], 0)
    f = np.nansum(np.where(np.isfinite(ysh), ysh, 0) * w, 0) / w.sum(0)
    looks = np.isfinite(ysh[:, 0, :]).sum(0)
    r2 = np.nansum(np.where(np.isfinite(ysh), (ysh - f) ** 2 * w, 0), 0)
    return float(np.nanmedian(r2[good].mean(0) / np.maximum(looks - 1, 1)))


def parameterStack(
    stack: Stack, members: list[int], gains: np.ndarray, name: str
) -> np.ndarray:
    """(len(members), pts) of a summary parameter on the gain-aligned spectra."""
    return np.stack(
        [
            summaryParameter(
                name, stack.wavelengths, (gains[n][:, None] * stack.values[k]).T
            )
            for n, k in enumerate(members)
        ]
    )


def repeatability(per: np.ndarray) -> float:
    """stage3_figs: the median, over points with >= 3 looks, of the std across looks."""
    finite = np.isfinite(per)
    three = finite.sum(0) >= 3
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        sd = np.nanstd(per[:, three], 0, ddof=1)
    return float(np.nanmedian(sd))
