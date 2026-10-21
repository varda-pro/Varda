"""Vector files dropped on the window go to the current workspace: an
overlay in Find Data, else the importer of the manager their geometry
suggests; images in the same drop still import as images."""

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from affine import Affine
from app_model import Application
from PyQt6.QtWidgets import QMessageBox, QWidget
from shapely.geometry import LineString, Point, box

from varda import vector_drop
from varda.common.di_types import ProjectImages
from varda.maingui import MainGUI
from varda.utilities.debug import generate_random_image
from varda.vector_import import Target, isDroppedVectorFile, suggestedTarget
from varda.vector_import_dialog import VectorImporter
from varda.workspaces.general_image_analysis import (
    GeneralImageAnalysisConfig,
    GeneralImageAnalysisWorkflow,
)


APP_NAME = "varda-test-vector-drop"


def _layer(path, geometries) -> str:
    gpd.GeoDataFrame(
        {"name": [f"f{i}" for i in range(len(geometries))]},
        geometry=geometries,
        crs="EPSG:4326",
    ).to_file(path)
    return str(path)


@pytest.fixture
def polygons(tmp_path) -> str:
    return _layer(tmp_path / "units.gpkg", [box(0, 0, 1, 1), box(2, 2, 3, 3)])


@pytest.fixture
def points(tmp_path) -> str:
    return _layer(tmp_path / "sites.gpkg", [Point(0, 0), Point(1, 1)])


@pytest.fixture
def lines(tmp_path) -> str:
    return _layer(tmp_path / "traverse.geojson", [LineString([(0, 0), (1, 1)])])


def test_vector_files_are_told_from_images(tmp_path, polygons, lines):
    assert isDroppedVectorFile(polygons) and isDroppedVectorFile(lines)
    # a GeoPackage of raster tiles alone is an image
    tiles = tmp_path / "tiles.gpkg"
    with rasterio.open(
        tiles,
        "w",
        driver="GPKG",
        width=16,
        height=16,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=Affine(0.1, 0, 0, 0, -0.1, 0),
    ) as dst:
        dst.write(np.zeros((1, 16, 16), np.uint8))
    assert not isDroppedVectorFile(tiles)
    assert not isDroppedVectorFile(tmp_path / "scene.img")
    assert not isDroppedVectorFile(tmp_path / "table.csv")  # use Import…


def test_the_geometry_suggests_the_target(polygons, points, lines):
    assert suggestedTarget(polygons) is Target.ROIS
    assert suggestedTarget(points) is Target.POINTS
    assert suggestedTarget(lines) is Target.LINES


@pytest.fixture
def workspace(qtbot):
    workflow = GeneralImageAnalysisWorkflow(
        GeneralImageAnalysisConfig([generate_random_image((20, 20, 10))])
    )
    qtbot.addWidget(workflow)
    return workflow


@pytest.fixture
def opened(monkeypatch) -> list[tuple[Target, str]]:
    """The importers asked to open a file, instead of reading it."""
    calls: list[tuple[Target, str]] = []
    monkeypatch.setattr(
        VectorImporter, "openFile", lambda self, path: calls.append((self.target, path))
    )
    return calls


def test_a_drop_goes_to_the_importer_its_geometry_suggests(
    workspace, opened, polygons, points, lines
):
    for path in (polygons, points, lines):
        vector_drop.openVectorFile(path, workspace, workspace)
    assert opened == [
        (Target.ROIS, polygons),
        (Target.POINTS, points),
        (Target.LINES, lines),
    ]


@pytest.fixture
def gui(qtbot):
    app = Application(APP_NAME)
    app.images = ProjectImages()
    gui = MainGUI(app)
    qtbot.addWidget(gui)
    yield gui
    Application.destroy(APP_NAME)


def test_images_and_vectors_in_one_drop_each_go_their_way(
    monkeypatch, gui, workspace, opened, polygons, tmp_path
):
    imported: list[list[str]] = []
    monkeypatch.setattr(
        vector_drop, "importImagePaths", lambda paths, images: imported.append(paths)
    )
    gui.addTab(workspace)
    assert gui.currentWorkspace() is workspace
    image = str(tmp_path / "scene.img")
    vector_drop.importDroppedPaths([image, polygons], ProjectImages(), gui)
    assert imported == [[image]]
    assert opened == [(Target.ROIS, polygons)]


def test_without_a_workspace_for_an_image_it_says_where_to_drop(
    monkeypatch, qtbot, opened, polygons
):
    told: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda parent, title, text: told.append(text)
    )
    parent = QWidget()
    qtbot.addWidget(parent)
    vector_drop.openVectorFile(polygons, None, parent)
    vector_drop.openVectorFile(polygons, QWidget(), parent)  # no managers
    assert told == [vector_drop.NO_WORKSPACE] * 2
    assert opened == []


def test_in_find_data_a_vector_file_is_an_overlay(monkeypatch, qtbot, polygons):
    overlays: list[str] = []

    class FindData(vector_drop.FindDataWorkspace):
        def __init__(self):  # no catalogs: only the type matters here
            QWidget.__init__(self)

        def addOverlay(self, path):
            overlays.append(path)

    vector_drop.openVectorFile(polygons, FindData(), QWidget())
    assert overlays == [polygons]
