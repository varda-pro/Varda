"""Does the per-column factor ln(ours/MTRDR) follow CAT's per-column artifact
patch (VS ADR 11D87, McGuire and Pelkey spectra) band by band?"""

import json
import sys
import warnings
import numpy as np
from pathlib import Path
from scipy.ndimage import median_filter
from varda.crism.calibration import CalibrationLibrary, TransmissionSource
from varda.crism.readers import readTrr3

warnings.simplefilter("ignore")
D = Path("/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper")
S = Path(sys.argv[1])
r = json.load(open(S / "factor-default.json"))
wl = np.array(r["wavelengths"])
q = np.log(np.array(r["q_columns"]))
cols = np.array(r["columns"])
trr3 = readTrr3(D / "FRT00005850/trdr/frt00005850_07_if167l_trr3.lbl")
lib = CalibrationLibrary(
    [D / "ATO0004EC45", D / "FRT00005850"], S / "cache", D / "CAT_v7_4_aux/aux_files"
)
cwl = lib.wavelengths(trr3)
vs = {t.scanId: t for t in lib.transmissions(trr3, TransmissionSource.CAT_VS_ADR)}[
    "11D87"
]
bandIndex = np.nanargmin(np.abs(np.nanmedian(cwl, 0)[None, :] - wl[:, None]), axis=1)
for t in (1930, 1985, 2007, 2067, 2120, 2170, 2210, 2290, 2320, 2400):
    i = np.argmin(abs(wl - t))
    k = bandIndex[i]
    qi = q[:, i] - median_filter(
        q[:, i], 61, mode="nearest"
    )  # column structure < 61 columns
    out = []
    for n, label in enumerate(("McGuire", "Pelkey")):
        a = vs.artifacts[n][cols, k]
        a = a - median_filter(a, 61, mode="nearest")
        out.append(
            f"{label} {np.corrcoef(qi, a)[0, 1]:+.2f} (patch std {a.std() * 100:.2f}%)"
        )
    lt = np.log(vs.values[cols, k])
    lt = lt - median_filter(lt, 61, mode="nearest")
    print(
        f"{t} nm: ln q column std {qi.std() * 100:.2f}% | "
        + " | ".join(out)
        + f" | ln T {np.corrcoef(qi, lt)[0, 1]:+.2f}"
    )
