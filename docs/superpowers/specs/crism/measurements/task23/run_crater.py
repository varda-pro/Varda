import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import crater as C

D = Path("/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper")
S = Path(sys.argv[1])
frs = Path(sys.argv[2])
out = Path(sys.argv[3])
frsName = sys.argv[4] if len(sys.argv) > 4 else "FRS00031442"
paths = {
    "FRT00005C5E": D / "FRT00005C5E/mtrdr/frt00005c5e_07_if166j_mtr3.img",
    "FRT000047A3": D / "FRT000047A3/mtrdr/frt000047a3_07_if166j_mtr3.img",
    "FRT000066A4": D / "FRT000066A4/mtrdr/frt000066a4_07_if166j_mtr3.img",
    "FRT0001FB74": D / "FRT0001FB74/mtrdr/frt0001fb74_07_if166j_mtr3.img",
    "HRL000040FF": D / "HRL000040FF/mtrdr/hrl000040ff_07_if183j_mtr3.img",
    frsName: frs,
}
t = time.time()
grid = C.commonGrid(
    [p for n, p in paths.items() if n.startswith(("FRT", "HRL"))], paths["HRL000040FF"]
)
stack = C.buildStack(paths, grid)
print(
    "stack",
    round(time.time() - t),
    "s",
    grid.width,
    grid.height,
    stack.values.shape,
    stack.registration,
    flush=True,
)
pickle.dump(stack, open(out, "wb"))
