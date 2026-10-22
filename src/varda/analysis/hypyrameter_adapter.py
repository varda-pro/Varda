"""Varda's view of HyPyRameter's spectral-parameter library.

HyPyRameter is an optional dependency (the ``hypyrameter`` extra):
``HYPYRAMETER_AVAILABLE`` says whether it could be imported, and the
functions here raise ``ImportError`` otherwise. Its core API already works on
in-memory cubes with the wavelength axis last, so this module only maps names
and progress reporting onto Varda's conventions.
"""

from __future__ import annotations

import logging
import types
from collections.abc import Callable, Sequence

import numpy as np

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], None]

try:
    import hypyrameter

    _hypyrameter: types.ModuleType | None = hypyrameter
    HYPYRAMETER_AVAILABLE = True
except ImportError as error:  # the optional "hypyrameter" extra is not installed
    logger.info("HyPyRameter unavailable (%s); Band Parameters disabled", error)
    _hypyrameter = None
    HYPYRAMETER_AVAILABLE = False


def parameterNames() -> list[str]:
    """HyPyRameter's spectral parameters, in its definition order."""
    return list(_require().PARAMETERS)


def parameterDescription(name: str) -> str:
    return _require().PARAMETERS[name].description


def parameterBounds(name: str) -> tuple[float, float]:
    """The wavelength range (nm) the parameter reads."""
    low, high = _require().PARAMETERS[name].bounds
    return float(low), float(high)


def validParameterNames(wavelengthsNm) -> list[str]:
    """The parameters an image with these wavelengths (nm) can support."""
    return _require().valid_parameters(np.asarray(wavelengthsNm, dtype=np.float64))


def computeParameterCube(
    cube,
    wavelengthsNm,
    names: Sequence[str],
    reportProgress: ProgressCallback | None = None,
) -> np.ndarray:
    """(rows, cols, len(names)) float32 cube of the named parameters; NaN
    pixels stay NaN. Progress is reported in percent before each parameter."""

    def progress(fraction: float, name: str) -> None:
        if reportProgress is not None:
            reportProgress(int(round(100 * fraction)), name)

    return _require().compute(
        np.asarray(cube, dtype=np.float64),
        np.asarray(wavelengthsNm, dtype=np.float64),
        list(names),
        progress=progress,
    )


def _require() -> types.ModuleType:
    if _hypyrameter is None:
        raise ImportError(
            "HyPyRameter is not installed; install Varda's 'hypyrameter' extra."
        )
    return _hypyrameter
