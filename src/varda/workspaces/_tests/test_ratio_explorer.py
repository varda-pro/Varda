"""Ratio Explorer controller: ratio spectra into the pixel plot; saving boxes."""

import numpy as np
import pytest
from psygnal import Signal
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QPushButton
from shapely.geometry import Polygon

from varda.common.entities import ROIMode
from varda.image_loading.crism_geometry import ColumnGeometry
from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.image_rendering.raster_view.pointer_event import (
    KeyEvent,
    PointerAction,
    PointerEvent,
)
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    RatioExplorerTool,
)
from varda.rois.region_statistics import boxPolygonPixels, computeRegionStatistics
from varda.rois.roi_collection import ROICollection
from varda.utilities.debug import generate_random_image
from varda.workspaces import ratio_explorer
from varda.workspaces.ratio_explorer import RatioExplorerConfig, RatioExplorerController

CTRL = Qt.KeyboardModifier.ControlModifier  # boxes are placed with Ctrl/Cmd held


class FakeDocks:
    def __init__(self):
        self.spectra = []
        self.batches = []

    def addSpectra(self, entries):
        self.batches.append(list(entries))
        self.spectra.extend(entries)


class FakeROIManager:
    def __init__(self, lockColumn=False):
        self.lockColumn = lockColumn
        self.denominatorFid = None

    def setDenominator(self, fid):
        self.denominatorFid = fid


class FakeOverlay:
    def __init__(self, points, color):
        self.points, self.color, self.removed = points, color, False

    def setPoints(self, points):
        self.points = points

    def remove(self):
        self.removed = True


class FakeViewport:
    """Just enough of a viewport to receive mirrored box overlays."""

    sigImageChanged = Signal()  # fires when the shown region (local coords) moves

    def __init__(self):
        self.overlays: list[FakeOverlay] = []
        self.offset = (0.0, 0.0)

    def pixelToLocalCoords(self, pixels):
        return pixels - self.offset

    def addROIOverlay(self, points, color):
        overlay = FakeOverlay(points, color)
        self.overlays.append(overlay)
        return overlay


def _press(button, x, y):
    pos = QPointF(x, y)
    return PointerEvent(PointerAction.PRESS, pos, pos, button, CTRL)


@pytest.fixture
def setup(qtbot):
    image = generate_random_image((40, 40, 10))
    viewport = ImageViewport(ImageRenderer(image=image))
    qtbot.addWidget(viewport)
    tool = RatioExplorerTool(viewport)
    tool.activate()
    collection = ROICollection.fromImage(image)
    docks, roiManager = FakeDocks(), FakeROIManager()
    controller = RatioExplorerController(
        collection, roiManager, docks, RatioExplorerConfig()
    )
    controller.bindTool(tool)
    yield image, tool, collection, docks, roiManager, controller
    tool.deactivate()
    del viewport


def test_ratio_is_plotted_once_both_boxes_exist(setup):
    image, tool, _c, docks, _m, _ctrl = setup
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    assert docks.spectra == []

    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))

    (_w, values, label) = docks.spectra[-1]
    numerator = computeRegionStatistics(boxPolygonPixels(11, 21, 5, 5), image)["mean"]
    denominator = computeRegionStatistics(boxPolygonPixels(31, 5, 5, 5), image)["mean"]
    np.testing.assert_allclose(values, numerator / denominator)
    assert label == "Ratio (11, 21) / (31, 5)"


def test_box_size_follows_the_config(setup):
    _i, tool, _c, docks, _m, ctrl = setup
    ctrl.config.boxWidth.set(3)
    ctrl.config.boxHeight.set(7)
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    np.testing.assert_array_equal(
        tool.selection.numerator, boxPolygonPixels(11, 21, 3, 7)
    )


def test_saving_adds_both_boxes_as_rois_and_sets_the_denominator(setup):
    _i, tool, collection, _d, roiManager, ctrl = setup
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))

    ctrl.saveCurrentBoxes()

    assert len(collection) == 2
    numerator, denominator = (collection.getROI(fid) for fid in collection.fids)
    assert numerator.name == "Ratio 1 numerator"
    assert denominator.name == "Ratio 1 denominator"
    assert numerator.roiType is ROIMode.RECTANGLE
    saved = Polygon(collection.getPixelCoordinates(collection.fids[1]))
    assert saved.equals(Polygon(boxPolygonPixels(31, 5, 5, 5)))
    assert roiManager.denominatorFid == collection.fids[1]


def test_s_and_the_sidebar_button_save_the_boxes(qtbot, setup):
    _i, tool, collection, _d, _m, ctrl = setup
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))
    tool.onKeyEvent(KeyEvent(Qt.Key.Key_S.value, Qt.KeyboardModifier(0)))
    section = ctrl.createSidebarSection()
    qtbot.addWidget(section)
    (button,) = [
        b for b in section.findChildren(QPushButton) if b.text() == "Save Boxes as ROIs"
    ]
    button.click()
    names = [roi.name for roi in collection.getAllROIs()]
    assert names == [
        "Ratio 1 numerator",
        "Ratio 1 denominator",
        "Ratio 2 numerator",
        "Ratio 2 denominator",
    ]


def test_saving_without_both_boxes_adds_nothing(setup):
    _i, tool, collection, _d, _m, ctrl = setup
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    ctrl.saveCurrentBoxes()
    assert len(collection) == 0


def test_column_lock_places_the_denominator_on_the_numerators_sensor_column(
    setup, monkeypatch
):
    image, tool, _c, _d, roiManager, _ctrl = setup
    roiManager.lockColumn = True
    # IR sample == column index, one strip everywhere
    geometry = ColumnGeometry(ir_sample=np.tile(np.arange(40.0), (40, 1)))
    monkeypatch.setattr(ratio_explorer, "columnGeometryFor", lambda *_args: geometry)
    monkeypatch.setattr(type(image), "filePath", property(lambda self: "/fake/x.img"))

    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))

    # same columns as the numerator (9..13), at the clicked row
    np.testing.assert_array_equal(
        tool.selection.denominator, boxPolygonPixels(11, 5, 5, 5)
    )


def _selectBoxes(tool):
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))


def _expectedRatio(image):
    numerator = computeRegionStatistics(boxPolygonPixels(11, 21, 5, 5), image)["mean"]
    denominator = computeRegionStatistics(boxPolygonPixels(31, 5, 5, 5), image)["mean"]
    return numerator / denominator


def test_ratio_can_be_computed_from_an_image_other_than_the_clicked_one(qtbot):
    clicked = generate_random_image((40, 40, 10))
    spectral = generate_random_image((40, 40, 10))
    viewport = ImageViewport(ImageRenderer(image=clicked))
    qtbot.addWidget(viewport)
    tool = RatioExplorerTool(viewport)
    tool.activate()
    docks = FakeDocks()
    controller = RatioExplorerController(
        ROICollection.fromImage(clicked),
        FakeROIManager(),
        docks,
        RatioExplorerConfig(),
        imagesFor=lambda _clicked: [spectral],
        labelWithImageName=True,
    )
    controller.bindTool(tool)

    _selectBoxes(tool)

    (_w, values, label) = docks.spectra[-1]
    np.testing.assert_allclose(values, _expectedRatio(spectral))
    assert label == f"{spectral.name} ratio (11, 21) / (31, 5)"
    tool.deactivate()
    del viewport


def test_several_source_images_are_plotted_as_one_selection(qtbot):
    a = generate_random_image((40, 40, 10))
    b = generate_random_image((40, 40, 10))
    viewport = ImageViewport(ImageRenderer(image=a))
    qtbot.addWidget(viewport)
    tool = RatioExplorerTool(viewport)
    tool.activate()
    docks = FakeDocks()
    controller = RatioExplorerController(
        ROICollection.fromImage(a),
        FakeROIManager(),
        docks,
        RatioExplorerConfig(),
        imagesFor=lambda _clicked: [a, b],
        labelWithImageName=True,
    )
    controller.bindTool(tool)

    _selectBoxes(tool)

    assert len(docks.batches[-1]) == 2  # one batch, so Replace mode keeps both
    labels = [entry[2] for entry in docks.batches[-1]]
    assert labels == [
        f"{a.name} ratio (11, 21) / (31, 5)",
        f"{b.name} ratio (11, 21) / (31, 5)",
    ]
    tool.deactivate()
    del viewport


def test_mirrored_boxes_follow_a_viewport_whose_shown_region_moves(setup):
    _i, tool, _c, _d, _m, ctrl = setup
    other = FakeViewport()
    ctrl.setMirrorViewports([tool.viewport, other])
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))

    other.offset = (2.0, 1.0)  # the region view panned by (2, 1)
    other.sigImageChanged.emit()

    numeratorOverlay, _ = other.overlays
    assert [(p.x(), p.y()) for p in numeratorOverlay.points] == [
        (pt[0] - 2.0, pt[1] - 1.0) for pt in boxPolygonPixels(11, 21, 5, 5)
    ]


def test_boxes_are_mirrored_onto_the_other_viewports(setup):
    _i, tool, _c, _d, _m, ctrl = setup
    other = FakeViewport()
    ctrl.setMirrorViewports([tool.viewport, other])

    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    assert len(other.overlays) == 1  # numerator only so far
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))

    assert len(other.overlays) == 2
    numeratorOverlay, denominatorOverlay = other.overlays
    assert [(p.x(), p.y()) for p in numeratorOverlay.points] == [
        tuple(pt) for pt in boxPolygonPixels(11, 21, 5, 5)
    ]
    assert numeratorOverlay.color != denominatorOverlay.color


def test_mirrored_boxes_move_rather_than_multiply(setup):
    _i, tool, _c, _d, _m, ctrl = setup
    other = FakeViewport()
    ctrl.setMirrorViewports([other])
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 13.0, 23.0))

    assert len(other.overlays) == 1
    assert [(p.x(), p.y()) for p in other.overlays[0].points] == [
        tuple(pt) for pt in boxPolygonPixels(13, 23, 5, 5)
    ]


def test_mirrored_boxes_are_removed_when_the_tool_deactivates(setup):
    _i, tool, _c, _d, _m, ctrl = setup
    other = FakeViewport()
    ctrl.setMirrorViewports([other])
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))

    tool.deactivate()

    assert all(overlay.removed for overlay in other.overlays)
    tool.activate()  # so the fixture's deactivate is balanced


def _lockedColumns(setup, monkeypatch, lock=True):
    image, tool, _c, _d, roiManager, ctrl = setup
    roiManager.lockColumn = lock
    # IR sample == column index // 10: four detector strips of ten columns
    geometry = ColumnGeometry(ir_sample=np.tile(np.arange(40.0) // 10, (40, 1)))
    monkeypatch.setattr(ratio_explorer, "columnGeometryFor", lambda *_args: geometry)
    monkeypatch.setattr(type(image), "filePath", property(lambda self: "/fake/x.img"))
    return tool, ctrl


def test_a_new_numerator_pulls_the_denominator_into_its_column(setup, monkeypatch):
    tool, _ctrl = _lockedColumns(setup, monkeypatch)
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))

    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 3.0, 21.0))

    # the denominator keeps its row (5) and size, now in the numerator's strip
    center = _center(tool.selection.denominator)
    assert center[1] == 5
    strip = np.arange(40) // 10
    assert strip[center[0]] == strip[3]
    assert ratio_explorer.boxSize(tool.selection.denominator) == (5, 5)


def test_a_new_numerator_leaves_the_denominator_when_the_lock_is_off(
    setup, monkeypatch
):
    tool, _ctrl = _lockedColumns(setup, monkeypatch, lock=False)
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 3.0, 21.0))
    np.testing.assert_array_equal(
        tool.selection.denominator, boxPolygonPixels(31, 5, 5, 5)
    )


def test_an_unreachable_row_keeps_the_denominator_and_says_so(setup, monkeypatch):
    tool, _ctrl = _lockedColumns(setup, monkeypatch)
    # the numerator's observation segment (0) covers rows 0-19 only
    segment = np.repeat(np.array([0.0, 1.0]), 20)[:, None] * np.ones((1, 40))
    geometry = ColumnGeometry(
        ir_sample=np.tile(np.arange(40.0) // 10, (40, 1)), segment_id=segment
    )
    monkeypatch.setattr(ratio_explorer, "columnGeometryFor", lambda *_args: geometry)
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 31.0, 5.0))
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 30.0))
    before = tool.selection.denominator.copy()
    shown = []
    monkeypatch.setattr(tool, "showText", lambda text, timeout=None: shown.append(text))

    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 3.0, 5.0))

    np.testing.assert_array_equal(tool.selection.denominator, before)
    assert len(shown) == 1 and "left in place" in shown[0]


def test_without_a_geometry_file_the_denominator_stays_and_it_says_so(
    setup, monkeypatch
):
    tool, _ctrl = _lockedColumns(setup, monkeypatch)
    monkeypatch.setattr(ratio_explorer, "columnGeometryFor", lambda *_args: None)
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))
    before = tool.selection.denominator.copy()
    shown = []
    monkeypatch.setattr(tool, "showText", lambda text, timeout=None: shown.append(text))

    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 3.0, 21.0))

    np.testing.assert_array_equal(tool.selection.denominator, before)
    assert len(shown) == 1


def test_following_with_an_even_box_does_not_drift(setup, monkeypatch):
    tool, ctrl = _lockedColumns(setup, monkeypatch)
    ctrl.config.boxWidth.set(4)
    ctrl.config.boxHeight.set(4)
    tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
    tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 31.0, 5.0))
    first = None
    for _ in range(4):
        tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 11.0, 21.0))
        first = tool.selection.denominator if first is None else first
        np.testing.assert_array_equal(tool.selection.denominator, first)


def _center(polygon):
    return ratio_explorer.centerPixel(polygon)


def test_components_carry_the_numerator_denominator_and_ratio(setup):
    image, tool, _c, _d, _m, ctrl = setup
    received = []
    ctrl.sigComponents.connect(received.append)

    _selectBoxes(tool)

    (components,) = received
    numerator = np.asarray(
        computeRegionStatistics(boxPolygonPixels(11, 21, 5, 5), image)["mean"]
    )
    denominator = np.asarray(
        computeRegionStatistics(boxPolygonPixels(31, 5, 5, 5), image)["mean"]
    )
    assert components.image is image
    np.testing.assert_allclose(components.numerator, numerator)
    np.testing.assert_allclose(components.denominator, denominator)
    np.testing.assert_allclose(components.ratio, numerator / denominator)
    assert len(components.wavelengths) == len(components.ratio)
    assert components.selection is tool.selection


def test_both_spectra_are_plotted_next_to_the_ratio_when_asked(qtbot):
    image = generate_random_image((40, 40, 10))
    viewport = ImageViewport(ImageRenderer(image=image))
    qtbot.addWidget(viewport)
    tool = RatioExplorerTool(viewport)
    tool.activate()
    docks = FakeDocks()
    controller = RatioExplorerController(
        ROICollection.fromImage(image),
        FakeROIManager(),
        docks,
        RatioExplorerConfig(),
        showComponents=True,
    )
    controller.bindTool(tool)

    _selectBoxes(tool)

    assert [entry[2] for entry in docks.batches[-1]] == [
        "Ratio (11, 21) / (31, 5)",
        "Numerator (11, 21)",
        "Denominator (31, 5)",
    ]
    tool.deactivate()
    del viewport


def test_saved_rois_carry_the_name_suffix_and_boxes_are_in_the_box_image(qtbot):
    shown = generate_random_image((40, 40, 10))
    boxes = generate_random_image((40, 40, 10))
    viewport = ImageViewport(ImageRenderer(image=shown))
    qtbot.addWidget(viewport)
    tool = RatioExplorerTool(viewport)
    tool.activate()
    collection = ROICollection.fromImage(boxes)
    controller = RatioExplorerController(
        collection,
        FakeROIManager(),
        FakeDocks(),
        RatioExplorerConfig(),
        nameSuffix=lambda: " (verdict)",
    )
    controller.bindTool(tool, boxes)
    _selectBoxes(tool)

    controller.saveCurrentBoxes()

    assert [collection.getROI(f).name for f in collection.fids] == [
        "Ratio 1 numerator (verdict)",
        "Ratio 1 denominator (verdict)",
    ]
    tool.deactivate()
    del viewport
