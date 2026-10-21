"""Parameter maps, albedo and CO2 of ours vs the MTRDR on their overlap, plus
the source sample/line backplanes of both, saved to an npz for analysis."""

import sys
from pathlib import Path
import numpy as np
from varda.crism import validation_report as vr
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource

ours_path, mtrdr_path, ours_in, mtrdr_in, out = map(Path, sys.argv[1:6])
ours, ref = ENVIDataSource(str(ours_path)), ENVIDataSource(str(mtrdr_path))
w = vr._overlap(ours, ref)
c = vr._compare(ours, ref, w)
dr, dc = w.shift
refRows = slice(w.rows.start - dr, w.rows.stop - dr)
refCols = slice(w.cols.start - dc, w.cols.stop - dc)
a = ENVIDataSource(str(ours_in))
b = ENVIDataSource(str(mtrdr_in))
oi = vr._read(a, w.rows, w.cols, np.array([0, 1]))
mi = vr._read(b, refRows, refCols, np.array([5, 6]))
mi[mi >= 65534] = np.nan
save = {
    "wavelengths": c.wavelengths,
    "good": c.good,
    "oursSample": oi[..., 0],
    "oursLine": oi[..., 1],
    "mtrdrSample": mi[..., 0],
    "mtrdrLine": mi[..., 1],
    "co2Ours": c.co2[0],
    "co2Mtrdr": c.co2[1],
    "albedoOurs": c.albedo[0],
    "albedoMtrdr": c.albedo[1],
    "rows": np.array([w.rows.start, w.rows.stop]),
    "cols": np.array([w.cols.start, w.cols.stop]),
    "refRows": np.array([refRows.start, refRows.stop]),
    "refCols": np.array([refCols.start, refCols.stop]),
}
for name, (p, q) in c.parameters.items():
    save[f"{name}_ours"] = p
    save[f"{name}_mtrdr"] = q
np.savez_compressed(out, **save)
print("saved", out)
