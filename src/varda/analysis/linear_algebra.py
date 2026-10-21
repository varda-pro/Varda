"""Pure-numpy linear transforms of image cubes: PCA, MNF and FastICA.

Cubes are (rows, cols, bands). Work happens on the (samples, bands) matrix of
valid pixels; ``flattenValid``/``unflatten`` convert to and from it, leaving
nodata pixels as NaN in results. Covariances are accumulated in row chunks so
a full CRISM cube (hundreds of thousands of pixels x hundreds of bands) never
needs a second full-size copy.

Every transform also returns what its inverse needs (``mean`` and ``inverse``),
so the number of components to keep can be decided afterwards, ENVI-style:
transform with everything, look at the eigenvalues and the component images,
then rebuild the image from the first N.
"""

from __future__ import annotations

from collections.abc import Sequence

import attrs
from enum import Enum

import numpy as np

_CHUNK_ROWS = 65_536

# Fraction of the variance the suggested PCA/ICA component count captures.
_SUGGESTED_VARIANCE = 0.99
# MNF eigenvalues are SNR + 1; components below this are taken to be noise.
_MNF_NOISE_EIGENVALUE = 2.0
# FastICA stops once no unmixing vector turns by more than this (1 - |cos|)
FASTICA_TOLERANCE = 1e-5


@attrs.frozen
class TransformResult:
    """``scores``: (samples, k) transformed data. ``loadings``: (bands, k)
    component vectors in band space. ``explainedVariance``: (k,) each
    component's share (of variance for PCA/ICA, of noise-whitened variance for
    MNF), in decreasing order. ``eigenvalues``: (k,) the unnormalized values
    behind those shares. ``mean``: (bands,) and ``inverse``: (k, bands) such that
    ``data ~ scores @ inverse + mean``. For an iterative transform (FastICA),
    ``iterations`` run and whether it ``converged``; None otherwise. For MNF,
    the ``noiseCovariance`` (bands, bands) it whitened by."""

    scores: np.ndarray
    loadings: np.ndarray
    explainedVariance: np.ndarray
    eigenvalues: np.ndarray
    mean: np.ndarray
    inverse: np.ndarray
    iterations: int | None = None
    converged: bool | None = None
    noiseCovariance: np.ndarray | None = None


def varianceShareText(share: float) -> str:
    """A share of the variance (0-1) as a percentage that tells components
    apart: one decimal from 1 % ("85.2%"), two significant figures below
    ("0.34%", "0.0012%", "3.4e-05%"), never a rounded "0.0%"."""
    percent = 100 * float(share)
    return f"{percent:.1f}%" if abs(percent) >= 1 else f"{percent:.2g}%"


def flattenValid(cube, nodata: float | None) -> tuple[np.ndarray, np.ndarray]:
    """(samples, bands) matrix of pixels valid in every band, and the (rows,
    cols) mask of which pixels those are. Nodata and NaN mark invalid."""
    data = np.asarray(cube, dtype=np.float64)
    if nodata is not None:
        data = np.where(data == nodata, np.nan, data)
    valid = np.all(np.isfinite(data), axis=2)
    return data[valid], valid


def unflatten(
    values: np.ndarray, valid: np.ndarray, fill: float = np.nan
) -> np.ndarray:
    """Put (samples, k) values back on the (rows, cols) grid; invalid pixels get ``fill``."""
    out = np.full(valid.shape + (values.shape[1],), fill, dtype=np.float64)
    out[valid] = values
    return out


def inverseTransform(
    scores: np.ndarray,
    inverse: np.ndarray,
    mean: np.ndarray,
    components: int | Sequence[int],
) -> np.ndarray:
    """Rebuild (samples, bands) data from the first ``components`` scores, or
    from the given 0-based component indices."""
    if isinstance(components, int):
        k = min(components, scores.shape[1], inverse.shape[0])
        return scores[:, :k] @ inverse[:k] + mean
    indices = list(components)
    return scores[:, indices] @ inverse[indices] + mean


def describeComponentSelection(indices: Sequence[int]) -> str:
    """'3 components' for the first three, else the 1-based numbers as ranges,
    e.g. 'components 2-5' or 'components 1, 3-4, 6'."""
    ordered = sorted(set(int(i) for i in indices))
    if ordered == list(range(len(ordered))):
        return f"{len(ordered)} components"
    runs: list[list[int]] = []
    for index in ordered:
        if runs and index == runs[-1][-1] + 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    parts = [
        f"{run[0] + 1}" if len(run) == 1 else f"{run[0] + 1}-{run[-1] + 1}"
        for run in runs
    ]
    noun = "component" if len(ordered) == 1 else "components"
    return f"{noun} {', '.join(parts)}"


def suggestedComponents(eigenvalues: np.ndarray, kind: str) -> int:
    """A starting point for how many components to keep: for MNF, those with
    an eigenvalue clearly above the noise floor, but no more than the elbow of
    the eigenvalue curve (with unfiltered stripes or spikes the noise is not
    white and the floor sits well above 1); otherwise enough to capture 99% of
    the variance. At least one."""
    values = np.asarray(eigenvalues, dtype=np.float64)
    if kind == "MNF":
        aboveNoise = int(np.sum(values > _MNF_NOISE_EIGENVALUE))
        return max(1, min(aboveNoise, elbowIndex(values)))
    total = values.sum()
    if total <= 0:
        return 1
    cumulative = np.cumsum(values) / total
    return int(np.searchsorted(cumulative, _SUGGESTED_VARIANCE) + 1)


def suggestionRule(kind: str) -> str:
    """How ``suggestedComponents`` chooses its count for ``kind``, in words."""
    if kind == "MNF":
        return (
            f"MNF eigenvalue above {_MNF_NOISE_EIGENVALUE:g} (SNR + 1), but no "
            "more than the elbow of the log eigenvalue curve (the point farthest "
            "from the line joining its ends); at least 1"
        )
    return f"enough components for {100 * _SUGGESTED_VARIANCE:g}% of the variance"


def elbowIndex(eigenvalues: np.ndarray) -> int:
    """How many leading values come before the elbow of a decreasing curve:
    the point of the log curve farthest from the line joining its ends."""
    values = np.asarray(eigenvalues, dtype=np.float64)
    positive = values[values > 0]
    if positive.size < 3:
        return int(positive.size) or 1
    y = np.log(positive)
    x = np.arange(y.size, dtype=np.float64)
    dx, dy = x[-1] - x[0], y[-1] - y[0]
    distance = np.abs(dy * x - dx * y + x[-1] * y[0] - y[-1] * x[0]) / np.hypot(dx, dy)
    return int(np.argmax(distance)) + 1


def pca(X: np.ndarray, components: int) -> TransformResult:
    """Principal components of the rows of ``X``, most variance first."""
    n, bands = X.shape
    k = min(components, bands)
    if n < 2 or n < k:
        raise ValueError(f"PCA needs at least {max(k, 2)} samples, got {n}")
    mean = X.mean(axis=0)
    covariance = _covariance(X, mean)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)  # ascending
    order = np.argsort(eigenvalues)[::-1][:k]
    eigenvalues, eigenvectors = eigenvalues[order], eigenvectors[:, order]
    total = float(np.trace(covariance))
    share = eigenvalues / total if total > 0 else np.zeros(k)
    return TransformResult(
        scores=_project(X, mean, eigenvectors),
        loadings=eigenvectors,
        explainedVariance=share,
        eigenvalues=eigenvalues,
        mean=mean,
        inverse=eigenvectors.T,  # orthonormal
    )


def _withWeights(X: np.ndarray, W: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """X with missing values as 0, and W with those (and negative or missing
    weights) as weight 0."""
    finite = np.isfinite(X)
    W = np.where(finite & np.isfinite(W), np.clip(W, 0, None), 0.0)
    return np.where(finite, X, 0.0), W


def weightedCovariance(X: np.ndarray, W: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The weighted mean and covariance of the rows of ``X`` (a weight per
    value): (W∘X)ᵀ(W∘X) ./ WᵀW about the weighted mean (Delchambre 2015,
    MNRAS 446, 3545), so the scale of the weights doesn't matter."""
    X, W = _withWeights(X, W)
    bands = X.shape[1]
    totals = W.sum(axis=0)
    mean = (W * X).sum(axis=0) / np.where(totals > 0, totals, 1)
    products, pairs = np.zeros((bands, bands)), np.zeros((bands, bands))
    for start in range(0, X.shape[0], _CHUNK_ROWS):
        w = W[start : start + _CHUNK_ROWS]
        weighted = w * (X[start : start + _CHUNK_ROWS] - mean)
        products += weighted.T @ weighted
        pairs += w.T @ w
    return mean, products / np.where(pairs > 0, pairs, 1)


def weightedScores(
    X: np.ndarray, W: np.ndarray, mean: np.ndarray, inverse: np.ndarray
) -> np.ndarray:
    """Each row's scores on a basis (``inverse``, (k, bands): data ≈ scores
    @ inverse + mean), fitted to its weighted values by least squares, so
    values without weight don't move them."""
    X, W = _withWeights(X, W)
    n, bands = X.shape
    k = inverse.shape[0]
    basis = inverse.T  # (bands, k)
    scores = np.empty((n, k))
    ridge = 1e-12 * np.eye(k)  # rows weighted in fewer than k bands
    rows = max(1, 2**24 // (bands * k))  # (rows, bands, k) at a time
    for start in range(0, n, rows):
        w = W[start : start + rows]
        normal = (w[:, :, None] * basis).transpose(0, 2, 1) @ basis + ridge
        right = (w * (X[start : start + rows] - mean)) @ basis
        scores[start : start + rows] = np.linalg.solve(normal, right[..., None])[..., 0]
    return scores


def weightedPca(X: np.ndarray, W: np.ndarray, components: int) -> TransformResult:
    """Principal components of the rows of ``X`` with a weight per value
    (``W``, same shape; e.g. frames used or inverse variances): the weighted
    covariance's eigenvectors, and each row's scores fitted to its weighted
    values. NaN values and negative weights count as weight 0."""
    n, bands = X.shape
    k = min(components, bands)
    if n < 2 or n < k:
        raise ValueError(f"Weighted PCA needs at least {max(k, 2)} samples, got {n}")
    mean, covariance = weightedCovariance(X, W)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)  # ascending
    order = np.argsort(eigenvalues)[::-1][:k]
    eigenvalues, vectors = eigenvalues[order], eigenvectors[:, order]
    total = float(np.trace(covariance))
    return TransformResult(
        scores=weightedScores(X, W, mean, vectors.T),
        loadings=vectors,
        explainedVariance=eigenvalues / total if total > 0 else np.zeros(k),
        eigenvalues=eigenvalues,
        mean=mean,
        inverse=vectors.T,
    )


class NoiseEstimate(Enum):
    """How MNF estimates the noise covariance."""

    HORIZONTAL = "horizontal"  # differences between left-right neighbors (default)
    VERTICAL = "vertical"  # up-down neighbors
    DIAGONAL = "diagonal"  # each pixel and the one below-right
    AVERAGED = "averaged"  # horizontal and vertical together
    HOMOGENEOUS_REGION = "region"  # the spread within a uniform area


_NEIGHBOUR_OFFSETS = {
    NoiseEstimate.HORIZONTAL: (0, 1),
    NoiseEstimate.VERTICAL: (1, 0),
    NoiseEstimate.DIAGONAL: (1, 1),
}


def _neighbourDifferences(
    data: np.ndarray, valid: np.ndarray, dr: int, dc: int
) -> np.ndarray:
    rows, cols = valid.shape
    a = (slice(0, rows - dr), slice(0, cols - dc))
    b = (slice(dr, rows), slice(dc, cols))
    pairs = valid[a] & valid[b]
    return (data[b] - data[a])[pairs] / np.sqrt(2.0)


def _neighbourDifferenceWeights(
    weights: np.ndarray, valid: np.ndarray, dr: int, dc: int
) -> np.ndarray:
    """The weight of each neighbor difference: w₁w₂ / (w₁ + w₂), the inverse
    variance of a difference of values weighted by inverse variance (0 if
    either has none)."""
    rows, cols = valid.shape
    a = (slice(0, rows - dr), slice(0, cols - dc))
    b = (slice(dr, rows), slice(dc, cols))
    pairs = valid[a] & valid[b]
    wa, wb = weights[a][pairs], weights[b][pairs]
    total = wa + wb
    return np.where(total > 0, wa * wb / np.where(total > 0, total, 1), 0.0)


def noiseCovariance(
    cube,
    valid: np.ndarray,
    method: NoiseEstimate = NoiseEstimate.HORIZONTAL,
    region: np.ndarray | None = None,
    weights: np.ndarray | None = None,
) -> np.ndarray:
    """The noise covariance for MNF.

    Neighbor methods (Green et al. 1988) difference adjacent valid pixels in
    one direction (or both, averaged): the scene is assumed smooth at that
    scale, so differences are mostly noise. Pick the direction along which the
    sensor's noise varies (e.g. vertical for along-track striping).
    ``HOMOGENEOUS_REGION`` uses the covariance within ``region`` (a mask of a
    uniform area, such as a dark or flat ROI), where all variation is noise.
    With ``weights`` (a weight per value, like the cube) the covariance is
    the weighted one, each difference weighted as both its values are.
    """
    data = np.asarray(cube, dtype=np.float64)
    if method is NoiseEstimate.HOMOGENEOUS_REGION:
        if region is None:
            raise ValueError(
                "A homogeneous region (mask) is needed for this noise estimate"
            )
        samples = data[region & valid]
        sampleWeights = None if weights is None else weights[region & valid]
    else:
        directions = (
            [NoiseEstimate.HORIZONTAL, NoiseEstimate.VERTICAL]
            if method is NoiseEstimate.AVERAGED
            else [method]
        )
        offsets = [_NEIGHBOUR_OFFSETS[d] for d in directions]
        samples = np.concatenate(
            [_neighbourDifferences(data, valid, *o) for o in offsets]
        )
        sampleWeights = (
            None
            if weights is None
            else np.concatenate(
                [_neighbourDifferenceWeights(weights, valid, *o) for o in offsets]
            )
        )
    if samples.shape[0] < 2:
        raise ValueError("Too few valid pixels to estimate noise")
    if sampleWeights is not None:
        return shrunkToPositive(weightedCovariance(samples, sampleWeights)[1])
    return _covariance(samples, samples.mean(axis=0))


def shrunkToPositive(covariance: np.ndarray, floor: float = 1e-3) -> np.ndarray:
    """A covariance blended with its diagonal just enough that its smallest
    eigenvalue is at least ``floor`` times the mean variance. A weighted
    covariance normalizes each pair of bands over the values both have, so
    it can come out with negative eigenvalues; whitening by it (MNF) would
    blow those directions up. The diagonal (the variances) is always
    positive, so some blend works."""
    diagonal = np.diag(np.diag(covariance))
    target = floor * float(np.mean(np.diag(covariance)))

    def smallest(share: float) -> float:
        return float(np.linalg.eigvalsh((1 - share) * covariance + share * diagonal)[0])

    if smallest(0.0) >= target:
        return covariance
    low, high = 0.0, 1.0
    for _ in range(30):  # bisection on the share of the diagonal
        middle = (low + high) / 2
        low, high = (low, middle) if smallest(middle) >= target else (middle, high)
    return (1 - high) * covariance + high * diagonal


def mnf(
    X: np.ndarray,
    noiseCov: np.ndarray,
    components: int,
    weights: np.ndarray | None = None,
) -> TransformResult:
    """Minimum Noise Fraction: PCA of the noise-whitened data, so components
    are ordered by signal-to-noise rather than by variance. With ``weights``
    (a weight per value) the signal covariance is the weighted one and each
    row's scores are fitted to its weighted values."""
    noiseValues, noiseVectors = np.linalg.eigh(noiseCov)
    floor = max(noiseValues.max(), 1e-300) * 1e-10  # guard near-singular noise
    noiseValues = np.maximum(noiseValues, floor)
    whitening = noiseVectors @ np.diag(noiseValues**-0.5) @ noiseVectors.T
    unwhitening = noiseVectors @ np.diag(noiseValues**0.5) @ noiseVectors.T
    if weights is not None:
        mean, covariance = weightedCovariance(X, weights)
        signal = whitening @ covariance @ whitening
        values, vectors = np.linalg.eigh(signal)
        order = np.argsort(values)[::-1][: min(components, X.shape[1])]
        values, vectors = values[order], vectors[:, order]
        inverse = vectors.T @ unwhitening
        total = float(np.trace(signal))
        return TransformResult(
            scores=weightedScores(X, weights, mean, inverse),
            loadings=whitening @ vectors,
            explainedVariance=values / total if total > 0 else np.zeros(len(values)),
            eigenvalues=values,
            mean=mean,
            inverse=inverse,
            noiseCovariance=noiseCov,
        )
    mean = X.mean(axis=0)
    whitened = pca((X - mean) @ whitening, components)
    return TransformResult(
        scores=whitened.scores,
        loadings=whitening @ whitened.loadings,
        explainedVariance=whitened.explainedVariance,
        eigenvalues=whitened.eigenvalues,
        mean=mean,
        inverse=whitened.loadings.T @ unwhitening,
        noiseCovariance=noiseCov,
    )


def fastIca(
    X: np.ndarray,
    components: int,
    maxIterations: int = 200,
    tolerance: float = FASTICA_TOLERANCE,
    seed: int = 0,
    weights: np.ndarray | None = None,
) -> TransformResult:
    """FastICA (parallel, logcosh contrast) on PCA-whitened data (weighted
    PCA with ``weights``, a weight per value).

    ``scores`` are unit-variance independent sources ordered by how much of the
    data's variance each explains; ``loadings`` is the mixing matrix in band
    space (bands, k), so ``X - mean ~ scores @ loadings.T``. ``converged``
    is False when ``maxIterations`` ran out first.
    """
    n = X.shape[0]
    whitened = (
        pca(X, components) if weights is None else weightedPca(X, weights, components)
    )
    mean = whitened.mean
    k = whitened.scores.shape[1]
    spread = np.sqrt(whitened.scores.var(axis=0, ddof=1))
    Z = whitened.scores / spread

    rng = np.random.default_rng(seed)
    W = _symmetricDecorrelation(rng.normal(size=(k, k)))
    iterations, converged = 0, False
    for iterations in range(1, maxIterations + 1):
        WZ = Z @ W.T
        g = np.tanh(WZ)
        gDerivativeMean = (1.0 - g**2).mean(axis=0)
        Wnew = _symmetricDecorrelation((g.T @ Z) / n - gDerivativeMean[:, None] * W)
        converged = np.max(np.abs(np.abs(np.diag(Wnew @ W.T)) - 1.0)) < tolerance
        W = Wnew
        if converged:
            break

    sources = Z @ W.T
    if weights is None:
        mixing, *_ = np.linalg.lstsq(sources, X - mean, rcond=None)  # (k, bands)
    else:  # Z = sources @ W (W is orthogonal), and X - mean ≈ Z·spread @ inverse
        mixing = W @ np.diag(spread) @ whitened.inverse
    # Unit-variance sources: each one's share of the variance is its mixing
    # vector's squared norm. Order the components by it, largest first.
    variance = np.sum(mixing**2, axis=1)
    order = np.argsort(variance)[::-1]
    sources, mixing, variance = sources[:, order], mixing[order], variance[order]
    total = variance.sum()
    return TransformResult(
        scores=sources,
        loadings=mixing.T,
        explainedVariance=variance / total if total > 0 else np.zeros(k),
        eigenvalues=variance,
        mean=mean,
        inverse=mixing,
        iterations=iterations,
        converged=bool(converged),
    )


def _symmetricDecorrelation(W: np.ndarray) -> np.ndarray:
    values, vectors = np.linalg.eigh(W @ W.T)
    return (vectors @ np.diag(values**-0.5) @ vectors.T) @ W


def _covariance(X: np.ndarray, mean: np.ndarray) -> np.ndarray:
    bands = X.shape[1]
    total = np.zeros((bands, bands))
    for start in range(0, X.shape[0], _CHUNK_ROWS):
        chunk = X[start : start + _CHUNK_ROWS] - mean
        total += chunk.T @ chunk
    return total / (X.shape[0] - 1)


def _project(X: np.ndarray, mean: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    scores = np.empty((X.shape[0], vectors.shape[1]))
    for start in range(0, X.shape[0], _CHUNK_ROWS):
        scores[start : start + _CHUNK_ROWS] = (
            X[start : start + _CHUNK_ROWS] - mean
        ) @ vectors
    return scores
