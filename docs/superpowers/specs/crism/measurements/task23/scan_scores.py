"""Every candidate's selection score (PDS ATs + CAT VS ADRs) for the four TRR3s."""

import json
import sys
from pathlib import Path
from varda.crism.calibration import CalibrationLibrary, TransmissionSource
from varda.crism.observation import CrismObservation
from varda.crism.pipeline import columnResiduals
from varda.crism.recipe import Recipe

D = Path("/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper")
S = Path(sys.argv[1])
CAT = {
    "FRT00005850": "11D87",
    "FRS00031442": "1B815",
    "FRS0002FE75": "0A93E",
    "FRS00031260": "1B815",
}
out = {}
for name in CAT:
    obs = CrismObservation.fromFolder(D / name)
    library = CalibrationLibrary(
        [D / "ATO0004EC45", D / name], S / "cache", D / "CAT_v7_4_aux/aux_files"
    )
    recipe = Recipe()
    recipe.atmosphere.source.set(TransmissionSource.BOTH)
    col = columnResiduals(obs, recipe, library)
    sel = col.selection
    ranked = sorted(sel.scores.items(), key=lambda kv: kv[1])
    out[name] = {
        "chosen": sel.chosen.key,
        "relativeMargin": sel.relativeMargin,
        "winFraction": sel.winFraction,
        "scores": dict(ranked),
        "cat": CAT[name],
    }
    print(
        name,
        "chosen",
        sel.chosen.key,
        "relMargin %.4f win %.3f" % (sel.relativeMargin, sel.winFraction),
    )
    for rank, (k, v) in enumerate(ranked, 1):
        mark = " <- CAT" if k.startswith(CAT[name]) else ""
        print(f"  {rank:2d} {k:10s} {v:.5f}{mark}")
(S / "scan_scores.json").write_text(json.dumps(out, indent=1))
