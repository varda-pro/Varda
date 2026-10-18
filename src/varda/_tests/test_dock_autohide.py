"""Every workspace's panels can be collapsed to an edge tab: each dock area's
title bar shows the auto-hide (pin) button, and a panel it is pressed for
becomes an auto-hide panel. Guards the global auto-hide config in
``common/ui.py`` and the docs ("Collapsing panels")."""

import numpy as np
import pytest
import PyQt6Ads as ads
from affine import Affine
from PyQt6.QtWidgets import QMainWindow
from pyproj import CRS

from varda.catalogs import registry
from varda.common.di_types import ProjectImages
from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.utilities.debug import generate_random_image
from varda.workspaces.crism_ratio.crism_ratio_workspace import CrismRatioWorkspace
from varda.workspaces.find_data.find_data_workspace import FindDataWorkspace
from varda.workspaces.linked_images.linking import Link
from varda.workspaces.workspace_kinds import buildWorkspace

MARS = CRS.from_user_input("+proj=eqc +R=3396190 +units=m +no_defs")


def _crism() -> CrismRatioWorkspace:
    def raster(name, bands, pixel):
        return VardaRaster(
            ArrayDataSource(
                np.random.default_rng(0)
                .uniform(0.2, 0.4, (20, 20, bands))
                .astype(np.float32),
                wavelengths=np.linspace(1000, 2500, bands),
                wavelengthUnits="nanometers",
                transform=Affine(pixel, 0, 0, 0, -pixel, 0),
                crs=MARS,
            ),
            name=name,
        )

    browse, data = raster("browse", 1, 200.0), raster("data", 6, 100.0)
    return CrismRatioWorkspace(browse, data, [browse, data], Link.PIXEL)


def _findData(monkeypatch) -> FindDataWorkspace:
    monkeypatch.setattr(registry, "catalogRegistry", [])
    return FindDataWorkspace(ProjectImages())


def _build(kind: str, monkeypatch) -> QMainWindow:
    if kind == "crism":
        return _crism()
    if kind == "finddata":
        return _findData(monkeypatch)
    images = ProjectImages([generate_random_image((30, 30, 10)) for _ in range(2)])
    workspace = buildWorkspace(kind, list(images), images)
    assert workspace is not None
    return workspace


@pytest.mark.parametrize(
    "kind",
    ["general", "dual", "linked", "mosaic", "hourglass", "finddata", "crism"],
)
def test_every_panel_can_be_collapsed_to_an_edge_tab(qtbot, monkeypatch, kind):
    workspace = _build(kind, monkeypatch)
    qtbot.addWidget(workspace)
    workspace.showNormal()
    qtbot.waitExposed(workspace)
    manager = workspace.dockManager
    areas = manager.openedDockAreas()
    assert areas
    for area in areas:
        button = area.titleBarButton(ads.TitleBarButton.TitleBarButtonAutoHide)
        assert button.isVisible(), [d.windowTitle() for d in area.dockWidgets()]
    for dock in manager.dockWidgetsMap().values():
        if not dock.isAutoHide():  # the CRISM workspace starts some collapsed
            dock.toggleAutoHide()
        assert dock.isAutoHide(), dock.windowTitle()
