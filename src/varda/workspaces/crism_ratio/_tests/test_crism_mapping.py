"""Clicks on a browse view mapped to I/F pixels, and boxes mapped back."""

import numpy as np
from affine import Affine
from pyproj import CRS, Transformer

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.workspaces.crism_ratio.mapping import dataToView, viewToData
from varda.workspaces.linked_images.linking import Link

EQC = CRS.from_user_input("+proj=eqc +R=3396190 +units=m +no_defs")
SINU = CRS.from_user_input("+proj=sinu +R=3396190 +units=m +no_defs")


def _image(transform, crs, shape=(40, 50, 2), name="a"):
    data = np.zeros(shape, dtype=np.float32)
    return VardaRaster(ArrayDataSource(data, transform=transform, crs=crs), name=name)


def _to_sinu(x: float, y: float) -> tuple[float, float]:
    return Transformer.from_crs(EQC, SINU, always_xy=True).transform(x, y)


def _views():
    """The I/F cube on an equirectangular grid and a browse image of the same
    ground on a sinusoidal grid, built so a known ground point lands on a
    known pixel in each."""
    data = _image(Affine(20, 0, 1000, 0, -20, 5000), EQC, name="iof")
    # ground point (1410, 4610) is data pixel (20.5, 19.5); put it on browse
    # pixel (7.5, 9.5) of a 10 m grid
    sx, sy = _to_sinu(1410, 4610)
    view = _image(Affine(10, 0, sx - 75, 0, -10, sy + 95), SINU, name="browse")
    return view, data


def test_map_link_converts_a_click_across_projections():
    view, data = _views()
    out = viewToData(view, data, Link.MAP, [[7.5, 9.5]])
    assert out is not None
    np.testing.assert_allclose(out, [[20.5, 19.5]], atol=0.01)


def test_a_click_outside_the_data_footprint_is_none():
    view, data = _views()
    assert viewToData(view, data, Link.MAP, [[7.5, 9.5], [-500, 9.5]]) is None


def test_pixel_link_is_the_identity():
    view, data = _views()
    out = viewToData(view, data, Link.PIXEL, [[3.2, 4.8]])
    assert out is not None
    np.testing.assert_allclose(out, [[3.2, 4.8]])
    assert viewToData(view, data, Link.PIXEL, [[60, 4]]) is None  # off 50 columns


def test_data_to_view_round_trips_a_box():
    view, data = _views()
    box = np.array([[18, 17], [23, 17], [23, 22], [18, 22]], dtype=float)
    onView = dataToView(view, data, Link.MAP, box)
    np.testing.assert_allclose(
        onView[0] + (onView[2] - onView[0]) / 2, [7.5, 9.5], atol=0.5
    )
    back = viewToData(view, data, Link.MAP, onView)
    assert back is not None
    np.testing.assert_allclose(back, box, atol=0.01)
