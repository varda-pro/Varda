"""Named features: longitude conventions, the bundled tables, which labels a
view shows, Go to search and its view."""

import math

import numpy as np
import pytest

from varda.catalogs.basemaps import LAYERS
from varda.catalogs.gazetteer import (
    Gazetteer,
    eastLongitude,
    featureView,
    loadGazetteer,
    search,
    selectLabels,
)


def test_east_longitude_conversion():
    assert eastLongitude(137.8) == pytest.approx(137.8)
    assert eastLongitude(226.2) == pytest.approx(-133.8)  # 0-360 east
    assert eastLongitude(348.8) == pytest.approx(-11.2)
    assert eastLongitude(11.2, positiveWest=True) == pytest.approx(-11.2)
    assert eastLongitude(308.8, positiveWest=True) == pytest.approx(51.2)
    assert eastLongitude(360.0) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "body, name, latitude, longitude",
    [
        ("Mars", "Gale", -5.4, 137.8),
        ("Mars", "Isidis Planitia", 13.9, 87.0),
        ("Mars", "Olympus Mons", 18.65, -133.8),
        ("Moon", "Tycho", -43.3, -11.2),
        ("Io", "Loki Patera", 13.0, 51.2),  # 308.8°W
        ("Titan", "Kraken Mare", 68.0, 50.0),  # 310°W
    ],
)
def test_known_features_are_where_they_should_be(body, name, latitude, longitude):
    names = loadGazetteer(body)
    assert names is not None
    i = names.index(name)
    assert names.latitude[i] == pytest.approx(latitude, abs=0.5)
    assert names.longitude[i] == pytest.approx(longitude, abs=1.5)


def test_every_imagery_body_has_names():
    for body in {layer.body for layer in LAYERS}:
        names = loadGazetteer(body)
        assert names is not None and len(names) > 10, body
        assert set(names.approval) == {"Adopted by IAU"}
        assert (np.abs(names.longitude) <= 180).all()
    assert loadGazetteer("Earth") is None


def fake(names, lats, lons, diameters) -> Gazetteer:
    return Gazetteer(
        "Mars",
        tuple(names),
        np.array(lats, float),
        np.array(lons, float),
        np.array(diameters, float),
        tuple("Crater, craters" for _ in names),
        tuple("Adopted by IAU" for _ in names),
    )


def labels(g: Gazetteer, view, widthPx: float = 1000.0):
    x0, _, x1, _ = view
    return [
        g.names[i]
        for i in selectLabels(
            g, g.longitude, g.latitude, view, widthPx / (x1 - x0), g.kmPerDegree
        )
    ]


def test_big_features_zoomed_out_small_ones_zoomed_in():
    g = fake(["Big", "Small"], [0, 10], [0, 20], [2000, 20])
    assert labels(g, (-180, -90, 180, 90)) == ["Big"]
    # 40° across 1000 px: 20 km ≈ 0.34° ≈ 8 px, still too small
    assert labels(g, (0, -10, 40, 30)) == ["Big"]
    # 4° across 1000 px: 20 km ≈ 84 px
    assert labels(g, (18, 8, 22, 12)) == ["Small"]


def test_labels_only_in_the_view():
    g = fake(["In", "Out"], [0, 0], [10, 100], [500, 500])
    assert labels(g, (0, -20, 40, 20)) == ["In"]


def test_overlapping_labels_keep_the_larger():
    # two names a hair apart; both large enough
    g = fake(
        ["Larger Crater", "Smaller Crater", "Far Away"],
        [0, 0.1, 0],
        [0, 0.1, 30],
        [800, 600, 600],
    )
    shown = labels(g, (-40, -20, 40, 20))
    assert shown == ["Larger Crater", "Far Away"]


def test_label_count_is_capped():
    count = 500
    g = fake(
        [f"F{i}" for i in range(count)],
        np.linspace(-80, 80, count),
        np.tile(np.linspace(-170, 170, 25), 20),
        [1000] * count,
    )
    assert len(labels(g, (-180, -90, 180, 90), widthPx=20000)) <= 60


def test_features_without_a_size_show_when_zoomed_in():
    g = fake(["Vent"], [0], [0], [0])
    assert labels(g, (-180, -90, 180, 90)) == []
    assert labels(g, (-2, -2, 2, 2)) == ["Vent"]


def test_selection_uses_projected_coordinates():
    # a polar view: positions in meters, NaN for one off the map
    g = fake(["Pole", "Hidden"], [89, -89], [0, 0], [300, 300])
    x = np.array([0.0, np.nan])
    y = np.array([-59_000.0, np.nan])
    chosen = selectLabels(g, x, y, (-500_000, -500_000, 500_000, 500_000), 1e-3, 1e-3)
    assert [g.names[i] for i in chosen] == ["Pole"]


def test_search_is_case_insensitive_exact_then_prefix_then_contains():
    names = loadGazetteer("Mars")
    assert names is not None
    hits = [names.names[i] for i in search(names, "gale")]
    assert hits[0] == "Gale"
    assert all("gale" in h.lower() for h in hits)
    starts = [names.names[i] for i in search(names, "olymp")]
    assert "Olympus Mons" in starts
    prefixed = [h.lower().startswith("olymp") for h in starts]
    assert prefixed == sorted(prefixed, reverse=True)  # prefixes, then contains
    contains = [names.names[i] for i in search(names, "marineris")]
    assert contains == ["Valles Marineris"]
    assert search(names, "  ") == []


def test_search_ignores_accents():
    g = fake(["Ångström", "Abbe"], [0, 0], [0, 1], [10, 10])
    assert [g.names[i] for i in search(g, "angs")] == ["Ångström"]


def test_feature_view_is_about_twice_the_diameter():
    names = loadGazetteer("Mars")
    assert names is not None
    i = names.index("Gale")
    west, south, east, north = featureView(names, i)
    heightKm = (north - south) * names.kmPerDegree
    assert heightKm == pytest.approx(2 * names.diameter[i], rel=0.01)
    assert (west + east) / 2 == pytest.approx(names.longitude[i])
    assert (south + north) / 2 == pytest.approx(names.latitude[i])
    # wider in longitude by 1/cos(latitude)
    ratio = (east - west) / (north - south)
    assert ratio == pytest.approx(1 / math.cos(math.radians(names.latitude[i])))


def test_feature_view_stays_on_the_globe():
    names = loadGazetteer("Mars")
    assert names is not None
    west, south, east, north = featureView(names, names.index("Vastitas Borealis"))
    assert -180 <= west < east <= 180 and -90 <= south < north <= 90
