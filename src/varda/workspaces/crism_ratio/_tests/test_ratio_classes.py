"""Short class names from library spectra, and colors for new classes."""

import numpy as np
import pytest

from varda.spectral_libraries.spectrum import LibrarySpectrum
from varda.workspaces.crism_ratio.ratio_classes import (
    CLASS_COLORS,
    DENOMINATOR_CLASS_COLOR,
    nextClassColor,
    shortName,
    suggestedClass,
)


def spectrum(name, **provenance):
    return LibrarySpectrum(
        name, np.array([1000.0, 2000.0]), np.array([0.2, 0.3]), provenance=provenance
    )


@pytest.mark.parametrize(
    ("name", "short"),
    [
        ("Olivine Fo91 GDS70.d 165-250 µm USGS splib07a AREF", "Olivine Fo91"),
        ("Olivine_GDS70.a_Fo89_165um_BECKb_AREF", "Olivine"),
        ("Kaolinite CM9", "Kaolinite"),
        ("Plagioclase An60 HS349.3B", "Plagioclase An60"),
        ("Fe/Mg smectite", "Fe/Mg smectite"),
        ("Nontronite", "Nontronite"),
        ("GDS70 olivine", "GDS70 olivine"),  # starts with an ID: the whole name
    ],
)
def test_the_short_name_stops_at_the_first_sample_id(name, short):
    assert shortName(name) == short


def test_a_mineral_or_class_in_the_metadata_wins():
    assert suggestedClass(spectrum("Olivine Fo91 GDS70.d", mineral="Forsterite")) == (
        "Forsterite"
    )
    assert suggestedClass(spectrum("x GDS1", Class="Phyllosilicate")) == (
        "Phyllosilicate"
    )
    assert suggestedClass(spectrum("Olivine Fo91 GDS70.d", library="USGS")) == (
        "Olivine Fo91"
    )


def test_new_classes_get_distinct_colors_that_are_not_the_boxes():
    used: list[str] = []
    for _ in CLASS_COLORS:
        used.append(nextClassColor(used))
    assert sorted(used) == sorted(CLASS_COLORS)
    for color in (*CLASS_COLORS, DENOMINATOR_CLASS_COLOR):
        red, green, blue = (int(color[i : i + 2], 16) for i in (1, 3, 5))
        assert not (green > 150 and red < 120 and blue < 120)  # no box green
        assert not (red > 200 and blue > 200 and green < 120)  # no box magenta
    assert DENOMINATOR_CLASS_COLOR not in CLASS_COLORS
    # an alpha suffix or upper case still counts as used
    assert nextClassColor([CLASS_COLORS[0].upper() + "80"]) == CLASS_COLORS[1]
