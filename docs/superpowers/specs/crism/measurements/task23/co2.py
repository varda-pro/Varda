"""V3's CO2 residual of a map-projected product: per-pixel median and scene
residual RMS (the M7 metric, validation.co2Residual / sceneResidualRms), the
NaN fraction, and the per-detector-column median (from the IN product)."""

import sys
from pathlib import Path
import numpy as np
from varda.crism import validation_report as vr
from varda.crism.validation import CO2_WINDOW_NM, co2Residual, sceneResidualRms
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource


def score(path: Path) -> dict:
    src = ENVIDataSource(str(path))
    wl = vr._nanometers(src)
    good = vr._goodBands(path, src.bandCount)
    inside = (wl > CO2_WINDOW_NM[0]) & (wl < CO2_WINDOW_NM[1]) & good
    idx = np.flatnonzero(inside)
    spectra = []
    per = []
    footprint = 0
    for r0 in range(0, src.height, 64):
        rows = slice(r0, min(r0 + 64, src.height))
        cube = vr._read(src, rows, slice(0, src.width), idx)
        any_ = np.isfinite(cube).any(-1)
        footprint += int(any_.sum())
        c = co2Residual(wl[idx], cube)
        per.append(c[any_])
        ok = np.isfinite(c)
        spectra.append(cube[ok])
    src.close()
    per = np.concatenate(per)
    spectra = np.concatenate(spectra)
    finite = per[np.isfinite(per)]
    return {
        "median": float(np.median(finite)),
        "sceneRms": sceneResidualRms(wl[idx], spectra),
        "footprintPixels": footprint,
        "scoredPixels": int(finite.size),
        "nanFraction": 1 - finite.size / footprint,
    }


if __name__ == "__main__":
    out = {}
    for p in sys.argv[1:]:
        out[Path(p).name] = score(Path(p))
        print(
            Path(p).name,
            {
                k: round(v, 5) if isinstance(v, float) else v
                for k, v in out[Path(p).name].items()
            },
            flush=True,
        )
