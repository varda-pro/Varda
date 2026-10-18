"""Ratio Explorer: Ctrl+left-click numerator box, Ctrl+right-click denominator box."""

import numpy as np
import pytest
from PyQt6.QtCore import QPointF, Qt

from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.image_rendering.raster_view.triple_raster_view import TripleRasterView
from varda.image_rendering.raster_view.pointer_event import (
    KeyEvent,
    PointerAction,
    PointerEvent,
)
from varda.image_rendering.raster_view.viewport_tools.ratio_explorer_tool import (
    RatioExplorerTool,
    RatioSelection,
)
from varda.rois.region_statistics import boxPolygonPixels
from varda.utilities.debug import generate_random_image

NO_MOD = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier  # Cmd on macOS


def _press(button: Qt.MouseButton, x: float, y: float, modifiers=CTRL) -> PointerEvent:
    pos = QPointF(x, y)
    return PointerEvent(PointerAction.PRESS, pos, pos, button, modifiers)


def _left(x: float, y: float) -> PointerEvent:
    return _press(Qt.MouseButton.LeftButton, x, y)


def _right(x: float, y: float) -> PointerEvent:
    return _press(Qt.MouseButton.RightButton, x, y)


@pytest.fixture
def tool(qtbot):
    image = generate_random_image((40, 40, 10))
    viewport = ImageViewport(ImageRenderer(image=image))
    qtbot.addWidget(viewport)
    tool = RatioExplorerTool(viewport)
    tool.activate()
    yield tool
    tool.deactivate()
    del viewport


def test_ctrl_left_click_places_a_numerator_box_centred_on_the_pixel(qtbot, tool):
    with qtbot.waitSignal(tool.sigSelectionChanged) as blocker:
        assert tool.onPointerEvent(_left(10.4, 20.7)) is True

    (selection,) = blocker.args
    np.testing.assert_array_equal(selection.numerator, boxPolygonPixels(10, 20, 5, 5))
    assert selection.denominator is None


def test_plain_clicks_pass_through_so_the_view_can_be_navigated(qtbot, tool):
    with qtbot.assertNotEmitted(tool.sigSelectionChanged):
        assert (
            tool.onPointerEvent(_press(Qt.MouseButton.LeftButton, 10.0, 20.0, NO_MOD))
            is False
        )
        assert (
            tool.onPointerEvent(_press(Qt.MouseButton.RightButton, 10.0, 20.0, NO_MOD))
            is False
        )
    assert tool.selection.numerator is None


def test_box_size_comes_from_the_provider(qtbot, tool):
    tool.setBoxSizeProvider(lambda: (5, 9))
    with qtbot.waitSignal(tool.sigSelectionChanged) as blocker:
        tool.onPointerEvent(_left(10.0, 20.0))
    np.testing.assert_array_equal(
        blocker.args[0].numerator, boxPolygonPixels(10, 20, 5, 9)
    )


def test_right_click_before_a_numerator_does_nothing(qtbot, tool):
    with qtbot.assertNotEmitted(tool.sigSelectionChanged):
        assert tool.onPointerEvent(_right(10.0, 20.0)) is True


def test_right_click_places_a_same_size_denominator_box_at_the_click(qtbot, tool):
    tool.onPointerEvent(_left(10.0, 20.0))
    with qtbot.waitSignal(tool.sigSelectionChanged) as blocker:
        tool.onPointerEvent(_right(30.2, 5.9))

    (selection,) = blocker.args
    np.testing.assert_array_equal(selection.numerator, boxPolygonPixels(10, 20, 5, 5))
    np.testing.assert_array_equal(selection.denominator, boxPolygonPixels(30, 5, 5, 5))


def test_denominator_placement_can_be_delegated(qtbot, tool):
    calls = []

    def placer(numerator, row, col):
        calls.append((row, col))
        return numerator + np.array([0.0, 7.0])

    tool.setDenominatorPlacer(placer)
    tool.onPointerEvent(_left(10.0, 20.0))
    with qtbot.waitSignal(tool.sigSelectionChanged) as blocker:
        tool.onPointerEvent(_right(30.0, 5.0))

    assert calls == [(5, 30)]
    np.testing.assert_array_equal(
        blocker.args[0].denominator,
        boxPolygonPixels(10, 20, 5, 5) + np.array([0.0, 7.0]),
    )


def test_new_left_click_replaces_the_numerator_and_keeps_the_denominator(qtbot, tool):
    tool.onPointerEvent(_left(10.0, 20.0))
    tool.onPointerEvent(_right(30.0, 5.0))
    with qtbot.waitSignal(tool.sigSelectionChanged) as blocker:
        tool.onPointerEvent(_left(12.0, 22.0))

    (selection,) = blocker.args
    np.testing.assert_array_equal(selection.numerator, boxPolygonPixels(12, 22, 5, 5))
    np.testing.assert_array_equal(selection.denominator, boxPolygonPixels(30, 5, 5, 5))


def test_s_key_requests_a_save_and_escape_clears_both_boxes(qtbot, tool):
    tool.onPointerEvent(_left(10.0, 20.0))
    tool.onPointerEvent(_right(30.0, 5.0))

    with qtbot.waitSignal(tool.sigSaveRequested):
        assert tool.onKeyEvent(KeyEvent(Qt.Key.Key_S, NO_MOD, "s")) is True

    with qtbot.waitSignal(tool.sigSelectionChanged) as blocker:
        assert tool.onKeyEvent(KeyEvent(Qt.Key.Key_Escape, NO_MOD)) is True
    assert blocker.args[0].numerator is None
    assert blocker.args[0].denominator is None


def test_p_key_requests_saving_the_numerator_as_a_point(qtbot, tool):
    with qtbot.waitSignal(tool.sigSavePointRequested):
        assert tool.onKeyEvent(KeyEvent(Qt.Key.Key_P, NO_MOD, "p")) is True


def test_a_selection_shown_from_another_view_is_continued_without_a_report(qtbot, tool):
    selection = RatioSelection(boxPolygonPixels(10, 20, 5, 5), None)
    with qtbot.assertNotEmitted(tool.sigSelectionChanged):
        tool.showSelection(selection)

    tool.onPointerEvent(_right(30.0, 5.0))  # no "numerator first" refusal
    np.testing.assert_array_equal(
        tool.selection.denominator, boxPolygonPixels(30, 5, 5, 5)
    )


def test_moves_are_not_consumed_so_hover_readouts_keep_working(tool):
    pos = QPointF(1.0, 1.0)
    move = PointerEvent(PointerAction.MOVE, pos, pos, Qt.MouseButton.NoButton, NO_MOD)
    assert tool.onPointerEvent(move) is False


def test_the_boxes_stay_on_their_pixels_when_the_view_pans_and_zooms(qtbot):
    image = generate_random_image((80, 120, 10))
    view = TripleRasterView(ImageRenderer(image=image))
    qtbot.addWidget(view)
    view.resize(1200, 700)
    view.show()
    main = view.viewport2  # shows the region boxed in the overview
    tool = RatioExplorerTool(main)
    tool.activate()
    tool.onPointerEvent(_left(50.0, 30.0))
    handle = tool._numeratorOverlay
    placed = []
    original = handle.setPoints
    handle.setPoints = lambda points: (placed.append(points), original(points))
    view.roi1.setPos((20, 10))  # pan
    view.roi1.setSize((60, 40))  # and zoom out
    expected = main.pixelToLocalCoords(tool.selection.numerator)
    assert placed, "the box was not moved with the view"
    got = np.array([[p.x(), p.y()] for p in placed[-1]])
    np.testing.assert_allclose(got, expected)
    tool.deactivate()
    assert tool._numeratorOverlay is None and tool.selection.numerator is not None


def test_a_new_numerator_moves_the_denominator_through_the_follower(tool):
    tool.onPointerEvent(_left(10.0, 20.0))
    tool.onPointerEvent(_right(30.0, 5.0))
    seen = []

    def follower(numerator, denominator):
        seen.append((numerator, denominator))
        return denominator + np.array([-20, 0])

    tool.setDenominatorFollower(follower)
    tool.onPointerEvent(_left(12.0, 22.0))

    np.testing.assert_array_equal(
        tool.selection.numerator, boxPolygonPixels(12, 22, 5, 5)
    )
    np.testing.assert_array_equal(
        tool.selection.denominator, boxPolygonPixels(10, 5, 5, 5)
    )
    assert len(seen) == 1


def test_the_denominator_stays_without_a_follower_or_when_it_returns_none(tool):
    tool.onPointerEvent(_left(10.0, 20.0))
    tool.onPointerEvent(_right(30.0, 5.0))
    tool.onPointerEvent(_left(12.0, 22.0))
    np.testing.assert_array_equal(
        tool.selection.denominator, boxPolygonPixels(30, 5, 5, 5)
    )

    tool.setDenominatorFollower(lambda _n, _d: None)
    tool.onPointerEvent(_left(14.0, 24.0))
    np.testing.assert_array_equal(
        tool.selection.denominator, boxPolygonPixels(30, 5, 5, 5)
    )


def test_the_follower_is_not_asked_before_a_denominator_exists(tool):
    calls = []
    tool.setDenominatorFollower(lambda n, d: calls.append((n, d)))
    tool.onPointerEvent(_left(10.0, 20.0))
    assert tool.selection.denominator is None
    assert calls == []


def test_click_mapper_converts_clicks_to_data_pixels(qtbot, tool):
    tool.setClickMapper(lambda x, y: (x * 2, y * 2))
    tool.onPointerEvent(_left(5.0, 10.0))
    np.testing.assert_array_equal(
        tool.selection.numerator, boxPolygonPixels(10, 20, 5, 5)
    )
    tool.onPointerEvent(_right(15.0, 2.0))
    np.testing.assert_array_equal(
        tool.selection.denominator, boxPolygonPixels(30, 4, 5, 5)
    )


def test_click_mapper_none_shows_text_and_keeps_the_selection(qtbot, tool):
    tool.onPointerEvent(_left(10.0, 20.0))
    before = tool.selection
    shown = []
    tool.showText = lambda text, timeout=0: shown.append(text)
    tool.setClickMapper(lambda x, y: None)
    with qtbot.assertNotEmitted(tool.sigSelectionChanged):
        assert tool.onPointerEvent(_left(1.0, 1.0)) is True
        assert tool.onPointerEvent(_right(1.0, 1.0)) is True
    assert shown == ["Outside the I/F cube"] * 2
    assert tool.selection is before


def test_box_projector_draws_the_boxes_through_it(qtbot, tool):
    drawn = []
    tool.viewport.pixelToLocalCoords = lambda p: drawn.append(p) or p
    tool.setBoxProjector(lambda polygon: polygon + 100.0)
    tool.onPointerEvent(_left(10.0, 20.0))
    np.testing.assert_array_equal(drawn[-1], boxPolygonPixels(10, 20, 5, 5) + 100.0)
    # the selection itself stays in data pixels
    np.testing.assert_array_equal(
        tool.selection.numerator, boxPolygonPixels(10, 20, 5, 5)
    )
