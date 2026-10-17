"""The median of each parameter in two products of the same observation (with
and without the artifact basis), on the pixels valid in both."""

import sys
import warnings
import numpy as np
from pathlib import Path
from varda.crism import validation_report as vr
from varda.crism.validation import summaryParameter
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource

warnings.simplefilter("ignore")


def medians(a_p: Path, b_p: Path) -> dict:
    a, b = ENVIDataSource(str(a_p)), ENVIDataSource(str(b_p))
    wl = vr._nanometers(a)
    out = {}
    maps = {p: ([], []) for p in ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310")}
    for r0 in range(0, a.height, 64):
        rows = slice(r0, min(r0 + 64, a.height))
        ca, cb = (
            vr._read(a, rows, slice(0, a.width)),
            vr._read(b, rows, slice(0, b.width)),
        )
        for p, (x, y) in maps.items():
            pa, pb = summaryParameter(p, wl, ca), summaryParameter(p, wl, cb)
            ok = np.isfinite(pa) & np.isfinite(pb)
            x.append(pa[ok])
            y.append(pb[ok])
    for p, (x, y) in maps.items():
        x, y = np.concatenate(x), np.concatenate(y)
        out[p] = {
            "with": float(np.median(x)),
            "without": float(np.median(y)),
            "shift": float(np.median(x) - np.median(y)),
            "medianOfDifference": float(np.median(x - y)),
            "n": int(x.size),
        }
    return out


if __name__ == "__main__":
    for name, a, b in zip(sys.argv[1::3], sys.argv[2::3], sys.argv[3::3]):
        m = medians(Path(a), Path(b))
        print(
            name,
            {
                p: f"{v['shift']:+.5f} ({v['medianOfDifference']:+.5f})"
                for p, v in m.items()
            },
            flush=True,
        )
