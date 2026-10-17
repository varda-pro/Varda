import json

import pytest

from varda.crism.calibration import TransmissionSource
from varda.crism.recipe import (
    RECIPE_VERSION,
    Recipe,
    Resampling,
    SmileMethod,
    recipeFromDict,
    recipeToDict,
)


def test_defaults_follow_the_rulings():
    recipe = Recipe()
    assert recipe.destripe.enabled.get() is False
    assert recipe.smile.esc.get() is True
    assert recipe.atmosphere.artifactModel.get() is True
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
    assert again.smile.method.get() is SmileMethod.LINEAR
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
    assert [member.name for member in SmileMethod] == ["LINEAR"]
    assert "continuumDegree" not in Recipe().atmosphere.params


def test_version_1_recipe_with_the_values_that_ran_loads():
    data = recipeToDict(Recipe())
    data["recipeVersion"] = 1
    data["atmosphere"]["continuumDegree"] = 3  # ty: ignore[invalid-assignment]
    data["smile"]["method"] = "LINEAR"  # ty: ignore[invalid-assignment]
    assert recipeToDict(recipeFromDict(data)) == recipeToDict(Recipe())


@pytest.mark.parametrize(
    ("group", "key", "value"),
    [("atmosphere", "continuumDegree", 5), ("smile", "method", "CUBIC")],
)
def test_version_1_recipe_with_a_removed_value_is_rejected(group, key, value):
    data = recipeToDict(Recipe())
    data["recipeVersion"] = 1
    data[group][key] = value  # ty: ignore[invalid-assignment]
    with pytest.raises(ValueError, match=key):
        recipeFromDict(data)
