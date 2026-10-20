import gc

import numpy as np
import pytest
from pyproj import CRS

from varda.common import display_grid
from varda.common.display_grid import (
    DisplayCache,
    displayChoices,
    displayNote,
    needsReprojection,
    reprojectForDisplay,
)
from varda.common.entities import VardaRaster
from varda.geo._tests.fixtures import (
    CTX_DEM_PROJ,
    GDAL_WEST_PROJ,
    HIRISE_POLAR_PROJ,
    MOLA_MEGDR_PROJ,
    WGS84,
    makeImage,
)
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

MARS_EQC = "IAU_2015:49910"
MARS_EQC_OCENTRIC = "IAU_2015:49912"


def _base() -> VardaRaster:
    return makeImage(MARS_EQC, centerLonLat=(10, 5), pixelSize=1000, name="base")


def _withData(image: VardaRaster, data: np.ndarray) -> VardaRaster:
    """``image``'s grid holding ``data`` (rows, cols, bands)."""
    source = ArrayDataSource(data, transform=image.transform, crs=image.crs)
    return VardaRaster(source, name=image.name)


def test_choices_offer_same_body_other_grid():
    base = _base()
    ellipsoid = makeImage(
        MARS_EQC_OCENTRIC, centerLonLat=(10, 5), pixelSize=900, name="ellipsoid"
    )
    earth = makeImage(WGS84, centerLonLat=(10, 5), pixelSize=0.01, name="earth")
    far = makeImage(MARS_EQC, centerLonLat=(120, -40), pixelSize=1000, name="far")

    choices = displayChoices([base, ellipsoid, earth, far], base)

    assert choices.images == [ellipsoid]
    reasons = {e.image.name: e.reason for e in choices.excluded}
    assert "different bodies" in reasons["earth"]
    assert "doesn't overlap" in reasons["far"]
    assert needsReprojection(ellipsoid, base)
    assert not needsReprojection(base, base)


def test_too_large_base_is_excluded_with_its_size(monkeypatch):
    monkeypatch.setattr(display_grid, "DISPLAY_MAX_PIXELS", 100)
    base = _base()
    sameGrid = _withData(base, np.zeros((64, 64, 1), np.float32))
    other = makeImage(MARS_EQC_OCENTRIC, centerLonLat=(10, 5), pixelSize=900)

    choices = displayChoices([base, sameGrid, other], base)

    assert choices.images == [sameGrid]  # on the base's grid, nothing to do
    (excluded,) = choices.excluded
    assert excluded.image is other
    assert "too large to reproject for display (0 Mpx)" in excluded.reason
    assert "Processing → Geometry → Reproject Image" in excluded.reason


def test_class_values_never_blend():
    grid = makeImage(CTX_DEM_PROJ, centerLonLat=(30, 80), pixelSize=500)
    classes = np.zeros((64, 64, 1), np.float32)
    classes[:, 20:40] = 1
    classes[30:, 40:] = 2
    overlay = _withData(grid, classes)
    base = makeImage(HIRISE_POLAR_PROJ, centerLonLat=(30, 80), pixelSize=400)

    shown = reprojectForDisplay(overlay, base, 0)

    assert shown.dtype == np.float32
    assert shown.shape == (base.height, base.width)
    values = set(np.unique(shown[np.isfinite(shown)]).tolist())
    assert values and values <= {0.0, 1.0, 2.0}


def test_0_360_overlay_on_minus180_base():
    overlay = makeImage(MOLA_MEGDR_PROJ, centerLonLat=(257.5, 0), pixelSize=1 / 64)
    base = makeImage(MARS_EQC, centerLonLat=(-102.5, 0), pixelSize=500, width=32)

    assert displayChoices([overlay], base).images == [overlay]
    shown = reprojectForDisplay(overlay, base, 0)
    assert np.isfinite(shown).all()


def test_display_note_names_both_systems():
    base = _base()
    overlay = makeImage(MARS_EQC_OCENTRIC, centerLonLat=(10, 5), pixelSize=900)
    note = displayNote(overlay, base)
    assert note.startswith("Reprojected for display from ")
    assert "nearest neighbor; for display only" in note


def test_cache_hit_returns_same_object():
    cache = DisplayCache()
    overlay, base = _base(), _base()
    array = np.zeros((4, 4), np.float32)
    assert cache.get(overlay, base, 0) is None
    cache.put(overlay, base, 0, array)
    assert cache.get(overlay, base, 0) is array
    assert cache.get(overlay, base, 1) is None


def test_crs_override_on_base_misses():
    cache = DisplayCache()
    overlay, base = _base(), _base()
    cache.put(overlay, base, 0, np.zeros((4, 4), np.float32))
    base.setMetadataOverrides({"crs": CTX_DEM_PROJ})
    assert cache.get(overlay, base, 0) is None


def test_budget_evicts_oldest():
    array = np.zeros((4, 4), np.float32)  # 64 bytes
    cache = DisplayCache(budget=2 * array.nbytes)
    first, second, third, base = _base(), _base(), _base(), _base()
    cache.put(first, base, 0, array)
    cache.put(second, base, 0, array.copy())
    cache.get(first, base, 0)  # now the most recently used
    cache.put(third, base, 0, array.copy())

    assert cache.get(second, base, 0) is None
    assert cache.get(first, base, 0) is array
    assert cache.get(third, base, 0) is not None
    assert cache.nbytes == 2 * array.nbytes


def test_forget_drops_an_overlays_entries():
    cache = DisplayCache()
    overlay, base = _base(), _base()
    cache.put(overlay, base, 0, np.zeros((4, 4), np.float32))
    cache.forget(overlay)
    assert cache.get(overlay, base, 0) is None
    assert len(cache) == 0 and cache.nbytes == 0


def test_dead_overlay_frees_entries():
    cache = DisplayCache()
    overlay, base = _base(), _base()
    cache.put(overlay, base, 0, np.zeros((4, 4), np.float32))
    cache.put(overlay, base, 1, np.zeros((4, 4), np.float32))
    assert len(cache) == 2

    del overlay
    gc.collect()

    assert len(cache) == 0
    assert cache.nbytes == 0


@pytest.mark.parametrize("band", [0, 1])
def test_reproject_reads_only_the_band(band):
    base = _base()
    grid = makeImage(MARS_EQC_OCENTRIC, centerLonLat=(10, 5), pixelSize=900)
    data = np.stack(
        [np.full((64, 64), 7.0, np.float32), np.full((64, 64), 9.0, np.float32)],
        axis=2,
    )
    shown = reprojectForDisplay(_withData(grid, data), base, band)
    assert set(np.unique(shown[np.isfinite(shown)]).tolist()) == {[7.0, 9.0][band]}


def test_grid_key_tells_a_west_base_axis_apart():
    """WKT drops a projected CRS's west-positive base axis; the key keeps it."""
    east = CRS.from_user_input(GDAL_WEST_PROJ)
    definition = east.to_json_dict()
    for axis in definition["base_crs"]["coordinate_system"]["axis"]:
        if axis["direction"] == "east":
            axis["direction"] = "west"
    west = CRS.from_json_dict(definition)
    assert east.to_wkt() == west.to_wkt()  # why WKT can't be the key
    image = makeImage(east, centerLonLat=(100, 10), pixelSize=500)
    keys = {
        display_grid.gridKey(
            VardaRaster(
                ArrayDataSource(
                    np.zeros((4, 4, 1), np.float32), transform=image.transform, crs=crs
                ),
                name="base",
            )
        )
        for crs in (east, west)
    }
    assert len(keys) == 2
