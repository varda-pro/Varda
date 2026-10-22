"""The Preview panel's gallery: turning (wrapping round), keeping the
current item and pictures across new selections, its signals, its states
(empty, one item, loading, unavailable) and laptop-sized layouts."""

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QPixmap, QWheelEvent
from PyQt6.QtWidgets import QApplication

from varda.common.layout_audit import assertNothingClipped
from varda.workspaces.find_data.preview_gallery import (
    EMPTY,
    LOADING,
    MOTION_MS,
    GalleryItem,
    PreviewGallery,
)

ITEMS = [
    GalleryItem(f"p{i}", f"Product {i}", f"2024-01-0{i} · HiRISE RED")
    for i in range(1, 8)
]


def pixmap(width: int = 40, height: int = 30, color: str = "tomato") -> QPixmap:
    picture = QPixmap(width, height)
    picture.fill(QColor(color))
    return picture


@pytest.fixture
def gallery(qtbot) -> PreviewGallery:
    widget = PreviewGallery()
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def shown(gallery, qtbot) -> PreviewGallery:
    gallery.resize(380, 520)
    gallery.show()
    qtbot.waitExposed(gallery)
    gallery.setItems(ITEMS)
    return gallery


def wheel(widget, dy: int = 0, dx: int = 0) -> None:
    event = QWheelEvent(
        QPointF(widget.width() / 2, widget.height() / 2),
        QPointF(widget.mapToGlobal(QPoint(widget.width() // 2, 10))),
        QPoint(0, 0),
        QPoint(dx, dy),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


def test_next_and_previous_wrap_round(gallery):
    gallery.setItems(ITEMS[:3])
    assert gallery.currentId() == "p1"
    gallery.next()
    gallery.next()
    assert gallery.currentId() == "p3"
    gallery.next()
    assert gallery.currentId() == "p1"
    gallery.previous()
    assert gallery.currentId() == "p3"
    gallery.setCurrent("p2")
    assert gallery.currentId() == "p2"
    gallery.setCurrent("missing")
    assert gallery.currentId() == "p2"


def test_set_items_starts_on_the_given_item(gallery):
    gallery.setItems(ITEMS, current="p4")
    assert gallery.currentId() == "p4"
    assert gallery.position.text() == "4 of 7"


def test_set_items_keeps_the_current_item_and_pictures(gallery, qtbot):
    gallery.setItems(ITEMS[:4], current="p3")
    gallery.setPicture("p3", pixmap())
    gallery.setPicture("p1", pixmap())
    gallery.setUnavailable("p2", "No preview.")
    with qtbot.assertNotEmitted(gallery.sigCurrentChanged):
        gallery.setItems([ITEMS[2], ITEMS[1], ITEMS[5]])
    assert gallery.currentId() == "p3"
    assert gallery.picture("p3") is not None
    assert gallery.message("p2") == "No preview."
    assert gallery.picture("p1") is None  # gone with its item
    assert gallery.message("p6") == LOADING
    # without the current item, the first is shown (and announced)
    with qtbot.waitSignal(gallery.sigCurrentChanged) as blocker:
        gallery.setItems(ITEMS[4:6])
    assert blocker.args == ["p5"]


def test_pictures_of_items_not_shown_are_ignored(gallery):
    gallery.setItems(ITEMS[:2])
    gallery.setPicture("elsewhere", pixmap())
    gallery.setUnavailable("elsewhere", "No preview.")
    assert gallery.picture("elsewhere") is None
    assert not gallery.isUnavailable("elsewhere")


def test_placeholders_and_unavailable_messages(gallery):
    gallery.setItems(ITEMS[:2])
    assert gallery.message("p1") == LOADING
    gallery.setUnavailable("p1", "No preview.")
    assert gallery.message("p1") == "No preview."
    gallery.setPicture("p1", pixmap())
    assert gallery.message("p1") == ""
    gallery.grab()  # draws every state without error


def test_keys_turn_the_gallery(shown, qtbot):
    shown.setFocus()
    with qtbot.waitSignal(shown.sigCurrentChanged) as blocker:
        qtbot.keyClick(shown, Qt.Key.Key_Right)
    assert blocker.args == ["p2"]
    qtbot.keyClick(shown, Qt.Key.Key_End)
    assert shown.currentId() == "p7"
    qtbot.keyClick(shown, Qt.Key.Key_Right)
    assert shown.currentId() == "p1"
    qtbot.keyClick(shown, Qt.Key.Key_Left)
    assert shown.currentId() == "p7"
    qtbot.keyClick(shown, Qt.Key.Key_Home)
    assert shown.currentId() == "p1"
    with qtbot.waitSignal(shown.sigActivated) as blocker:
        qtbot.keyClick(shown, Qt.Key.Key_Return)
    assert blocker.args == ["p1"]


def test_the_wheel_turns_the_gallery(shown, qtbot):
    with qtbot.waitSignal(shown.sigCurrentChanged) as blocker:
        wheel(shown.stage, dy=-120)
    assert blocker.args == ["p2"]
    wheel(shown.stage, dx=120)
    assert shown.currentId() == "p1"
    wheel(shown.stage, dy=-40)  # less than a notch: not yet
    assert shown.currentId() == "p1"
    wheel(shown.stage, dy=-80)
    assert shown.currentId() == "p2"


def test_the_wheel_scrolls_the_filmstrip(shown):
    before = shown.strip.offset()
    wheel(shown.strip, dy=-240)
    assert shown.strip.offset() > before
    assert shown.currentId() == "p1"


def test_clicking_a_thumbnail_focuses_it(shown, qtbot):
    center = shown.strip.thumbRect(2).center().toPoint()
    with qtbot.waitSignal(shown.sigCurrentChanged) as blocker:
        qtbot.mouseClick(shown.strip, Qt.MouseButton.LeftButton, pos=center)
    assert blocker.args == ["p3"]


def test_the_arrows_turn_the_gallery(shown, qtbot):
    qtbot.mouseClick(shown.stage.previousButton, Qt.MouseButton.LeftButton)
    assert shown.currentId() == "p7"
    qtbot.mouseClick(shown.stage.nextButton, Qt.MouseButton.LeftButton)
    assert shown.currentId() == "p1"


def test_double_clicking_the_picture_activates_it(shown, qtbot):
    shown.setCurrent("p5")
    with qtbot.waitSignal(shown.sigActivated) as blocker:
        qtbot.mouseDClick(
            shown.stage, Qt.MouseButton.LeftButton, pos=shown.stage.rect().center()
        )
    assert blocker.args == ["p5"]


def test_the_filmstrip_keeps_the_current_thumbnail_in_view(shown, qtbot):
    shown.setCurrent("p7")
    qtbot.waitUntil(lambda: shown.motionProgress() == 1.0, timeout=2000)
    rect = shown.strip.thumbRect(6)
    assert rect.left() >= 0 and rect.right() <= shown.strip.width()


def test_one_item_shows_no_strip_or_arrows(shown):
    shown.setItems(ITEMS[:1])
    assert shown.stage.isVisible()
    assert not shown.strip.isVisible()
    assert not shown.stage.nextButton.isVisible()
    assert not shown.position.isVisible()
    assert shown.title.fullText() == "Product 1"
    shown.next()
    assert shown.currentId() == "p1"


def test_no_items_shows_a_note(shown, qtbot):
    with qtbot.assertNotEmitted(shown.sigCurrentChanged):
        shown.setItems([])
    assert shown.currentId() is None
    assert shown.emptyNote.isVisible()
    assert shown.emptyNote.text() == EMPTY
    assert not shown.stage.isVisible()
    shown.next()  # nothing to turn
    qtbot.keyClick(shown, Qt.Key.Key_Right)


def test_a_long_title_is_elided_with_the_full_text_in_its_tooltip(shown):
    long = "ESP_012345_1985_RED_a_very_long_product_name_that_cannot_fit_here"
    shown.setItems([GalleryItem("long", long, "2024")])
    shown.resize(260, 300)
    assertNothingClipped(shown)
    assert shown.title.text() != long
    assert shown.title.toolTip() == long


def test_the_slide_does_not_hold_up_the_state(shown, qtbot):
    """The current item changes at once; the slide only follows it, and
    ends within its duration."""
    shown.next()
    assert shown.currentId() == "p2"
    assert shown.title.fullText() == "Product 2"
    qtbot.waitUntil(lambda: shown.motionProgress() == 1.0, timeout=MOTION_MS * 10)
    assert shown.stage.previous is None


def test_hidden_galleries_do_not_animate(gallery):
    gallery.setItems(ITEMS)
    gallery.next()
    assert gallery.motionProgress() == 1.0


@pytest.mark.parametrize("size", [(260, 300), (420, 520)])
def test_nothing_is_cut_off(gallery, qtbot, size):
    gallery.setItems(
        [
            GalleryItem(
                f"x{i}", f"FRT0000A4FC_07_IF166L_TRR3_{i}", "2008-03-02 · CRISM FRT"
            )
            for i in range(7)
        ]
    )
    gallery.setPicture("x0", pixmap(30, 120))
    gallery.setUnavailable("x1", "The catalog has no browse image of it.")
    gallery.resize(*size)
    gallery.show()
    qtbot.waitExposed(gallery)
    for itemId in ("x0", "x1", "x6"):
        gallery.setCurrent(itemId)
        assertNothingClipped(gallery)
        gallery.grab()
