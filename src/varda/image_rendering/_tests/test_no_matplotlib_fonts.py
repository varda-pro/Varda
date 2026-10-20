"""Opening a workspace must not build Matplotlib's font cache (release QA #26:
the first image view froze for 24 s on a fresh macOS install, and a bundled
Varda builds the cache again on every launch, in a temporary folder).

pyqtgraph's color-map menu (in the Render Settings' gradient) lists
Matplotlib's maps through ``matplotlib.pyplot``, which loads the fonts.
Checked in a fresh interpreter: other tests may have imported pyplot already.
"""

import subprocess
import sys

import matplotlib
import pyqtgraph as pg

import varda.image_rendering  # noqa: F401  (installs the listing)

SCRIPT = """
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from varda.utilities.debug import generate_random_image
from varda.workspaces.general_image_analysis import (
    GeneralImageAnalysisConfig, GeneralImageAnalysisWorkflow,
)
workspace = GeneralImageAnalysisWorkflow(
    GeneralImageAnalysisConfig([generate_random_image((20, 20, 10))])
)
print("font_manager" if "matplotlib.font_manager" in sys.modules else "no fonts")
"""


def test_opening_a_workspace_loads_no_matplotlib_fonts():
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=120
    )
    assert result.stdout.strip().splitlines()[-1] == "no fonts", result.stderr


def test_the_gradient_menu_still_lists_matplotlibs_maps():
    names = pg.colormap.listMaps(source="matplotlib")
    assert "viridis" in names and "tab10" in names
    assert sorted(names) == sorted(matplotlib.colormaps)
