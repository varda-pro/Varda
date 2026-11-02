"""Every workspace fits a laptop: on a 1280 × 800 screen (less the menu bar
and tabs) none of its text is cut off, and it can get that small at all."""

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS

from varda.common.di_types import ProjectImages
from varda.common.entities import VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.utilities.debug import generate_random_image
from varda.workspaces.registry import buildWorkspace

LAPTOP = (1280, 700)


def _dem() -> VardaRaster:
    yy, xx = np.mgrid[0:40, 0:60]
    heights = -1500 + 200 * np.exp(-((xx - 30) ** 2 + (yy - 20) ** 2) / 100.0)
    return VardaRaster(
        ArrayDataSource(
            heights[..., None].astype(np.float32),
            transform=Affine(20, 0, 0, 0, -20, 0),
            crs=CRS.from_user_input("+proj=eqc +R=3389500 +units=m +no_defs"),
        ),
        name="dem",
    )


@pytest.mark.parametrize(
    "kind", ["general", "dual", "linked", "mosaic", "hourglass", "terrain"]
)
def test_a_workspace_fits_a_laptop_screen(qtbot, kind):
    if kind == "terrain":
        images = ProjectImages([_dem()])
    else:
        images = ProjectImages([generate_random_image((30, 30, 10)) for _ in range(2)])
    workspace = buildWorkspace(kind, list(images), images)
    assert workspace is not None
    qtbot.addWidget(workspace)
    assert workspace.minimumSizeHint().width() <= LAPTOP[0]
    workspace.showNormal()  # some open maximized: on the test screen, 800 × 800
    workspace.resize(*LAPTOP)
    qtbot.waitExposed(workspace)
    qtbot.wait(50)
    assert (workspace.width(), workspace.height()) == LAPTOP
    assertNothingClipped(workspace)
