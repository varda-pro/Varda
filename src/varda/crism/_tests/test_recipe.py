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
    recipe.smile.method.set(SmileMethod.CUBIC)
    recipe.projection.resampling.set(Resampling.AREA)
    recipe.atmosphere.source.set(TransmissionSource.BOTH)
    data = json.loads(json.dumps(recipeToDict(recipe)))
    assert data["recipeVersion"] == RECIPE_VERSION
    again = recipeFromDict(data)
    assert recipeToDict(again) == recipeToDict(recipe)
    assert again.despike.threshold.get() == 7.5
    assert again.smile.method.get() is SmileMethod.CUBIC
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
