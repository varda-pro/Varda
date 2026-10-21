import json

import pytest

import math

from varda.common.parameter import IntParameter, ParameterGroup
from varda.crism import recipe as recipeModule
from varda.crism.calibration import TransmissionSource
from varda.crism.recipe import (
    RECIPE_VERSION,
    Recipe,
    Resampling,
    recipeFromDict,
    recipeToDict,
)


def test_defaults_follow_the_rulings():
    recipe = Recipe()
    assert recipe.destripe.enabled.get() is False
    assert recipe.smile.esc.get() is True
    assert recipe.atmosphere.artifactModel.get() is False  # D12
    assert recipe.atmosphere.source.get() is TransmissionSource.PDS_AT
    assert recipe.despike.threshold.get() == 5.0
    assert recipe.projection.maxDistanceFactor.get() == 2.0


def test_recipe_round_trip():
    recipe = Recipe()
    recipe.despike.threshold.set(7.5)
    recipe.projection.resampling.set(Resampling.AREA)
    recipe.atmosphere.source.set(TransmissionSource.BOTH)
    data = json.loads(json.dumps(recipeToDict(recipe)))
    assert data["recipeVersion"] == RECIPE_VERSION
    again = recipeFromDict(data)
    assert recipeToDict(again) == recipeToDict(recipe)
    assert again.despike.threshold.get() == 7.5
    assert again.atmosphere.source.get() is TransmissionSource.BOTH


def test_recipe_rejects_newer_version():
    data = recipeToDict(Recipe())
    data["recipeVersion"] = RECIPE_VERSION + 1
    with pytest.raises(ValueError, match="newer"):
        recipeFromDict(data)


def test_recipe_rejects_unknown_keys():
    data = recipeToDict(Recipe())
    data["bogus"] = {}
    with pytest.raises(ValueError, match="bogus"):
        recipeFromDict(data)
    data = recipeToDict(Recipe())
    data["despike"]["bogus"] = 1  # ty: ignore[invalid-assignment]
    with pytest.raises(ValueError, match="bogus"):
        recipeFromDict(data)


def test_removed_options_are_gone():
    # writing nothing deleted the previous products (final review, Important 4)
    assert "enabled" not in Recipe().output.params
    # one interpolation only: nothing to choose (final review)
    assert "method" not in Recipe().smile.params
    assert "continuumDegree" not in Recipe().atmosphere.params
    assert not hasattr(recipeModule, "SmileMethod")
    data = recipeToDict(Recipe())
    data["smile"]["method"] = "LINEAR"  # ty: ignore[invalid-assignment]
    with pytest.raises(ValueError, match="method"):
        recipeFromDict(data)


@pytest.mark.parametrize(
    ("group", "key", "value"),
    [
        ("despike", "threshold", 10.5),  # range 3–10
        ("despike", "threshold", 2.0),
        ("despike", "threshold", math.nan),
        ("projection", "pixelSize", math.inf),
        ("projection", "maxDistanceFactor", -1.0),
    ],
)
def test_recipe_rejects_values_outside_the_range(group, key, value):
    """Final review: a recipe file sets only values the dialog could."""
    data = recipeToDict(Recipe())
    data[group][key] = value  # ty: ignore[invalid-assignment]
    with pytest.raises(ValueError, match=key):
        recipeFromDict(data)
    data[group][key] = 5  # ty: ignore[invalid-assignment]
    if key == "threshold":  # an integer is a fine float
        assert recipeFromDict(data).despike.threshold.get() == 5.0


def test_integer_parameters_take_integers_in_range():
    class Group(ParameterGroup):
        count = IntParameter("Count", 3, (1, 9))

    for bad in (2.5, 12, math.nan, "3"):
        with pytest.raises(ValueError, match="count"):
            recipeModule._setParameter("count", Group().count, bad)
    group = Group()
    recipeModule._setParameter("count", group.count, 4.0)
    assert group.count.get() == 4 and isinstance(group.count.get(), int)
