"""Lambertian photometry (spec S2): I/F ÷ cos(i).

The TER default takes i from a least-squares polynomial fit to the DDR areoid
incidence, which removes the stair-steps of its coarse grid. ``lambert`` takes
any incidence array (for example DDR band 6, the MOLA surface incidence).
"""

import numpy as np
from attrs import frozen

_MAX_INCIDENCE_DEG = 90.0
_COEFFICIENTS = 5


@frozen
class IncidenceFit:
    """θ = c0 + c1·x + c2·x² + c3·t + c4·t², x the sample and t the line index.

    ``coefficients`` are in the *scaled* basis: x and t are centered on
    ``center`` (t0, x0) and divided by ``scale`` (line, sample) before the
    powers are taken, which keeps the fit well conditioned. Indices are
    absolute in the scene, so chunks of lines evaluate consistently.
    """

    coefficients: np.ndarray  # (5,) for (1, x, x², t, t²), scaled basis
    center: tuple[float, float]  # (line, sample)
    scale: tuple[float, float]  # (line, sample)
    rmsDeg: float

    def evaluate(self, lineIndex: np.ndarray, sampleIndex: np.ndarray) -> np.ndarray:
        """θ in degrees; ``lineIndex`` and ``sampleIndex`` broadcast."""
        t = (np.asarray(lineIndex, dtype=float) - self.center[0]) / self.scale[0]
        x = (np.asarray(sampleIndex, dtype=float) - self.center[1]) / self.scale[1]
        return _design(x, t, self.coefficients)


def _design(x: np.ndarray, t: np.ndarray, c: np.ndarray) -> np.ndarray:
    return c[0] + c[1] * x + c[2] * x**2 + c[3] * t + c[4] * t**2


def fitIncidence(incidenceDeg: np.ndarray) -> IncidenceFit:
    """Least-squares fit to the finite pixels of ``incidenceDeg`` ``(l, s)``.
    Raises ValueError with fewer valid pixels than the 5 coefficients."""
    lines, samples = incidenceDeg.shape
    center = ((lines - 1) / 2, (samples - 1) / 2)
    scale = (max(lines - 1, 1) / 2, max(samples - 1, 1) / 2)
    t, x = np.meshgrid(
        (np.arange(lines) - center[0]) / scale[0],
        (np.arange(samples) - center[1]) / scale[1],
        indexing="ij",
    )
    valid = np.isfinite(incidenceDeg)
    if valid.sum() < _COEFFICIENTS:
        raise ValueError(
            f"{int(valid.sum())} valid incidence angles; the polynomial needs at "
            f"least {_COEFFICIENTS}"
        )
    xv, tv, y = x[valid], t[valid], incidenceDeg[valid].astype(float)
    design = np.stack([np.ones_like(xv), xv, xv**2, tv, tv**2], axis=1)
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    rms = float(np.sqrt(np.mean((design @ coefficients - y) ** 2)))
    return IncidenceFit(coefficients, center, scale, rms)


def lambert(
    cube: np.ndarray, sigma: np.ndarray, incidenceDeg: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Divide ``cube`` and ``sigma`` ``(l, s, b)`` by cos i, ``incidenceDeg`` ``(l, s)``.

    A NaN incidence, or one at or beyond 90°, gives NaN.
    """
    incidence = np.asarray(incidenceDeg, dtype=float)
    with np.errstate(invalid="ignore"):
        usable = incidence < _MAX_INCIDENCE_DEG
    cosine = np.where(usable, np.cos(np.radians(incidence)), np.nan)[..., None]
    return cube / cosine, sigma / cosine
