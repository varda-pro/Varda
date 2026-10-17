"""Does the 2 µm bowl in ours ÷ MTRDR look like CAT's VS ADR artifact spectra?"""

import json
import sys
import warnings
import numpy as np
from pathlib import Path
from scipy.ndimage import uniform_filter1d
from varda.crism.calibration import CalibrationLibrary, TransmissionSource
from varda.crism.readers import readTrr3

warnings.simplefilter("ignore")
D = Path("/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper")
S = Path(sys.argv[1])
shape = json.load(open(S / "shape-lines-default.json"))
wl = np.array(shape["wavelengths"])
ratio = np.array(shape["overall"])
trr3 = readTrr3(D / "FRT00005850/trdr/frt00005850_07_if167l_trr3.lbl")
lib = CalibrationLibrary(
    [D / "ATO0004EC45", D / "FRT00005850"], S / "cache", D / "CAT_v7_4_aux/aux_files"
)
cwl = np.nanmedian(lib.wavelengths(trr3)[270:370], axis=0)
vs = {t.scanId: t for t in lib.transmissions(trr3, TransmissionSource.CAT_VS_ADR)}
at = {t.scanId: t for t in lib.transmissions(trr3, TransmissionSource.PDS_AT)}
window = (wl > 1850) & (wl < 2250)
lr = np.log(ratio[window])
lr = lr - lr.mean()
for scan in ("11D87", "06822"):
    t = vs[scan]
    for k, label in enumerate(("McGuire", "Pelkey")):
        a = np.nanmedian(t.artifacts[k][270:370], axis=0)
        ai = np.interp(wl[window], cwl[np.isfinite(cwl)], a[np.isfinite(cwl)])
        ai = ai - ai.mean()
        print(
            scan,
            label,
            "corr with ln(shape ratio) over 1850–2250 nm:",
            round(float(np.corrcoef(lr, ai)[0, 1]), 3),
            "artifact range",
            round(float(np.nanmin(a)), 4),
            round(float(np.nanmax(a)), 4),
        )
    lt = np.log(np.nanmedian(t.values[270:370], axis=0))
    li = np.interp(wl[window], cwl[np.isfinite(cwl)], lt[np.isfinite(cwl)])
    print(
        scan, "corr with ln T:", round(float(np.corrcoef(lr, li - li.mean())[0, 1]), 3)
    )
t = at["06822"]
lt = np.log(np.nanmedian(t.values[270:370], axis=0))
li = np.interp(wl[window], cwl[np.isfinite(cwl)], lt[np.isfinite(cwl)])
print(
    "06822:AT smoothed ln T (9 bands) corr",
    round(
        float(
            np.corrcoef(lr, uniform_filter1d(li, 9) - uniform_filter1d(li, 9).mean())[
                0, 1
            ]
        ),
        3,
    ),
)
