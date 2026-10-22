"""Body first, then every catalog's collections of it in one list: grouped by
mission and instrument, each saying which catalog offers it, and a search
keeps those holding every word typed."""

from PyQt6.QtCore import Qt

from varda.catalogs.catalog import Collection
from varda.catalogs.sources import Source, bodies, sourceKey, sourcesFor
from varda.workspaces.find_data.collection_picker import (
    KEY_ROLE,
    BodyBar,
    CollectionFinder,
)

ODE = "PDS Orbital Data Explorer (ODE)"
SOURCES = [
    Source(
        "ode",
        ODE,
        Collection(
            "mars:MRO:CRISM:MTRDR",
            "MRO CRISM PDS4 Map-Projected Targeted Reduced Data Records",
            "Mars",
            "Compact Reconnaissance Imaging Spectrometer for Mars",
            "MRO CRISM",
        ),
    ),
    Source(
        "ode",
        ODE,
        Collection(
            "mars:MRO:CTX:EDR",
            "MRO CTX Experiment Data Record",
            "Mars",
            "Context Camera",
            "MRO CTX",
        ),
    ),
    Source(
        "stac",
        "STAC catalogs",
        Collection("api#crism_mosaic", "CRISM global mosaic (USGS)", "Mars"),
    ),
    Source(
        "ode",
        ODE,
        Collection(
            "moon:LRO:LROC:EDR",
            "LRO LROC Experiment Data Record",
            "Moon",
            "Lunar Reconnaissance Orbiter Camera",
            "LRO LROC",
        ),
    ),
    Source(
        "stac",
        "STAC catalogs",
        Collection("api#kaguya_dtms", "Kaguya Terrain Camera DTMs", "Moon"),
    ),
    Source("stac", "STAC catalogs", Collection("api#io", "Io mosaic", "Io")),
]


def test_bodies_are_ordered_common_first_with_counts():
    assert bodies(SOURCES) == [("Mars", 3), ("Moon", 2), ("Io", 1)]


def test_a_bodys_collections_come_from_every_catalog():
    mars = sourcesFor(SOURCES, "Mars")
    assert {s.catalogId for s in mars} == {"ode", "stac"}
    assert [s.collection.id for s in mars] == [
        "mars:MRO:CRISM:MTRDR",  # by mission and instrument; a catalog that
        "mars:MRO:CTX:EDR",  # gives none groups its own collections
        "api#crism_mosaic",
    ]


def test_the_same_title_is_told_apart_by_catalog_or_id():
    same = "HiRISE DTMs"
    told = sourcesFor(
        [
            Source("stac", "STAC catalogs", Collection("a#hirise_dtms", same, "Mars")),
            Source(
                "stac", "STAC catalogs", Collection("a#hirise_dtms_test", same, "Mars")
            ),
            Source("ode", ODE, Collection("x", "LOLA", "Moon")),
        ],
        "Mars",
    )
    assert [s.collection.title for s in told] == [
        f"{same} [hirise_dtms]",
        f"{same} [hirise_dtms_test]",
    ]
    across = sourcesFor(
        [
            Source("stac", "STAC catalogs", Collection("a#ctx", "CTX", "Mars")),
            Source("ode", ODE, Collection("b", "CTX", "Mars")),
        ],
        "Mars",
    )
    assert sorted(s.collection.title for s in across) == [
        f"CTX [{ODE}]",
        "CTX [STAC catalogs]",
    ]


def _finder(qtbot, body="Mars", select="") -> CollectionFinder:
    finder = CollectionFinder()
    qtbot.addWidget(finder)
    finder.setSources(sourcesFor(SOURCES, body), body, select)
    finder.show()
    return finder


def _shown(finder) -> list[str]:
    return [i.data(KEY_ROLE) for i in finder._leaves()]


def test_the_finder_groups_by_mission_and_tags_the_catalog(qtbot):
    finder = _finder(qtbot)
    model = finder.model
    rows = [model.item(r).text() for r in range(model.rowCount())]
    assert rows == ["MRO CRISM (1)", "MRO CTX (1)", "STAC catalogs (1)"]
    crism = model.item(0).child(0)
    assert crism.text().startswith("PDS4 Map-Projected")  # the group isn't repeated
    assert "From " + ODE in crism.toolTip()
    assert finder.count.text() == "3 collections on Mars"
    assert finder.chosen() is None  # nothing is chosen for the user


def test_every_word_must_match_including_the_catalog(qtbot):
    finder = _finder(qtbot)
    finder.search.setText("crism")
    assert _shown(finder) == [
        sourceKey("ode", "mars:MRO:CRISM:MTRDR"),
        sourceKey("stac", "api#crism_mosaic"),
    ]
    finder.search.setText("crism ode")
    assert _shown(finder) == [sourceKey("ode", "mars:MRO:CRISM:MTRDR")]
    assert finder.count.text() == "1 of 3 collections on Mars"


def test_enter_chooses_the_first_match(qtbot):
    finder = _finder(qtbot)
    with qtbot.waitSignal(finder.sigChosen) as signal:
        finder.search.setText("context")
        qtbot.keyClick(finder.search, Qt.Key.Key_Return)
    assert signal.args == [sourceKey("ode", "mars:MRO:CTX:EDR")]
    assert finder.chosen().collection.id == "mars:MRO:CTX:EDR"


def test_down_moves_through_matches_and_choose_finds_a_hidden_one(qtbot):
    finder = _finder(qtbot)
    finder.search.setText("crism")
    qtbot.keyClick(finder.search, Qt.Key.Key_Down)
    qtbot.keyClick(finder.search, Qt.Key.Key_Down)
    assert finder.tree.currentIndex().data(KEY_ROLE) == sourceKey(
        "stac", "api#crism_mosaic"
    )
    assert finder.choose(sourceKey("ode", "mars:MRO:CTX:EDR"))  # hidden by "crism"
    assert finder.search.text() == ""
    assert finder.chosenKey == sourceKey("ode", "mars:MRO:CTX:EDR")


def test_the_body_bar_shows_bodies_with_counts_and_signals_a_click(qtbot):
    bar = BodyBar()
    qtbot.addWidget(bar)
    bar.setBodies(bodies(SOURCES), "Mars")
    assert [b.text() for b in bar.buttons.values()] == ["Mars  3", "Moon  2", "Io  1"]
    assert bar.current() == "Mars"
    with qtbot.waitSignal(bar.sigBodyChanged) as signal:
        bar.buttons["Moon"].click()
    assert signal.args == ["Moon"] and bar.current() == "Moon"
    bar.setBodies([("Mars", 4), ("Moon", 2)], "Mars")  # Io's catalog went away
    assert list(bar.buttons) == ["Mars", "Moon"] and bar.current() == "Mars"
