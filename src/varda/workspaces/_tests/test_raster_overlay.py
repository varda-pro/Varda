"""Overlaying a co-registered project image (e.g. a classification) on a workspace view."""

import numpy as np
import pytest
from affine import Affine
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from varda.common.di_types import ProjectImages
from varda.common.entities import VardaRaster
from varda.common.grid import compatibleImages
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.workspaces.raster_overlay import (
    CLASSES,
    NO_OVERLAY,
    OverlayBar,
    overlayLayers,
    overlayRaster,
)


def _raster(data, name, **kwargs) -> VardaRaster:
    data = np.asarray(data, dtype=np.float32)
    if data.ndim == 2:
        data = data[:, :, None]
    return VardaRaster(ArrayDataSource(data, **kwargs), name=name)


def _base() -> VardaRaster:
    rng = np.random.default_rng(0)
    return _raster(rng.random((6, 8, 4)), "scene")


def _classification(base: VardaRaster, colors=True) -> VardaRaster:
    classes = np.zeros((6, 8))
    classes[:, 4:] = 1
    classes[0, 0] = np.nan
    probs = np.stack([1 - classes, classes], axis=2)
    data = np.concatenate([classes[:, :, None], probs], axis=2)
    metadata = {"classes": ["dark", "bright"]}
    if colors:
        metadata["classColors"] = ["#ff0000", "#0000ff"]
    return _raster(
        data,
        "scene MLP classes",
        bandNames=["Class", "P(dark)", "P(bright)"],
        extraMetadata=metadata,
    )


def test_only_images_on_the_same_grid_are_offered():
    base = _base()
    same = _classification(base)
    smaller = _raster(np.zeros((3, 8)), "smaller")
    elsewhere = _raster(
        np.zeros((6, 8)),
        "elsewhere",
        transform=Affine.translation(100, 0),
        crs="EPSG:4326",
    )
    assert compatibleImages([base, same, smaller, elsewhere], base) == [same]


def test_a_classification_offers_its_classes_then_each_probability():
    assert overlayLayers(_classification(_base())) == [CLASSES, "P(dark)", "P(bright)"]
    assert overlayLayers(_raster(np.zeros((6, 8, 2)), "x", bandNames=["a", "b"])) == [
        "a",
        "b",
    ]


def test_the_classes_layer_paints_each_class_its_colour_and_leaves_gaps_empty():
    raster, isRgb = overlayRaster(_classification(_base()), CLASSES)
    assert isRgb
    rgb = raster.getData()
    np.testing.assert_allclose(rgb[1, 1], [1, 0, 0])
    np.testing.assert_allclose(rgb[1, 6], [0, 0, 1])
    assert np.isnan(rgb[0, 0]).all()


def test_classes_without_stored_colours_get_distinct_ones():
    raster, _ = overlayRaster(_classification(_base(), colors=False), CLASSES)
    rgb = raster.getData()
    assert not np.allclose(rgb[1, 1], rgb[1, 6])


def test_a_band_layer_is_a_single_band_image():
    raster, isRgb = overlayRaster(_classification(_base()), "P(bright)")
    assert not isRgb and raster.bandCount == 1
    np.testing.assert_allclose(raster.getData()[1, 6, 0], 1.0)


def test_no_data_is_transparent_when_asked():
    renderer = ImageRenderer(image=overlayRaster(_classification(_base()), CLASSES)[0])
    renderer.transparentNoData = True
    rgba = renderer.render()
    assert rgba[0, 0, 3] == 0 and rgba[1, 1, 3] > 0


@pytest.fixture
def bar(qtbot):
    base = _base()
    images = [base, _classification(base)]
    viewports = [ImageViewport(ImageRenderer(image=base)) for _ in range(2)]
    for viewport in viewports:
        qtbot.addWidget(viewport)
    widget = OverlayBar(images, base, viewports)
    qtbot.addWidget(widget)
    return widget, viewports, images


def test_choosing_an_image_overlays_it_on_every_view(bar):
    widget, viewports, images = bar
    assert widget.imageBox.itemText(0) == NO_OVERLAY
    assert widget.imageBox.count() == 2  # the base image itself is not offered
    widget.setOverlay(images[1])
    assert widget.layerBox.currentText() == CLASSES
    assert all(v.hasOverlayImage() for v in viewports)
    # the class key sits on the main view, not in the toolbar
    legend = viewports[0].legendText()
    assert "dark" in legend and "bright" in legend
    assert viewports[1].legendText() == ""
    widget.setOverlay(None)
    assert not any(v.hasOverlayImage() for v in viewports)
    assert viewports[0].legendText() == ""


def test_opacity_and_layer_changes_apply(bar):
    widget, viewports, images = bar
    widget.setOverlay(images[1])
    widget.opacitySlider.setValue(30)
    assert widget.renderer is not None
    assert widget.renderer.settings.opacity.get() == pytest.approx(0.3)
    widget.layerBox.setCurrentText("P(dark)")
    assert viewports[0].legendText() == ""  # no key for a probability layer
    assert widget.renderer.settings.opacity.get() == pytest.approx(0.3)


def test_new_results_appear_in_the_list(qtbot):
    base = _base()
    images = ProjectImages([base])
    widget = OverlayBar(images, base, [])
    qtbot.addWidget(widget)
    assert widget.imageBox.count() == 1
    images.append(_classification(base))
    assert widget.imageBox.count() == 2


def test_the_bar_says_which_images_arent_on_this_grid(qtbot):
    base = _base()
    smaller = _raster(np.zeros((3, 8)), "smaller")
    widget = OverlayBar(ProjectImages([base, smaller]), base, [])
    qtbot.addWidget(widget)
    assert widget.imageBox.count() == 1
    assert widget.notListed.text() == "(1 not on this grid)"
    assert "smaller (different pixel grid" in widget.notListed.toolTip()


def test_the_overlay_sits_between_the_image_and_what_is_drawn_on_it(bar):
    widget, viewports, images = bar
    widget.setOverlay(images[1])
    viewport = viewports[0]
    outline = viewport.addPolygonOverlay()
    assert viewport._imageItem.zValue() < viewport._overlayImageItem.zValue()
    assert viewport._overlayImageItem.zValue() < outline._item.zValue()


def test_the_legend_can_be_hidden(bar):
    widget, viewports, images = bar
    widget.setOverlay(images[1])
    assert widget.legendButton.isVisibleTo(widget)
    widget.legendButton.setChecked(False)
    assert viewports[0].legendText() == ""
    widget.legendButton.setChecked(True)
    assert "dark" in viewports[0].legendText()


def test_the_layer_picker_shows_only_when_there_is_a_choice(qtbot, bar):
    widget, viewports, images = bar
    widget.setOverlay(images[1])  # classes plus probabilities
    assert widget.layerBox.isVisibleTo(widget)
    base = images[0]
    single = _raster(np.zeros(base.shape[:2] + (1,), np.float32), "one band")
    images.append(single)
    widget._refreshImages()
    widget.setOverlay(single)
    assert not widget.layerBox.isVisibleTo(widget)


def test_a_long_overlay_name_does_not_widen_the_toolbar(qtbot):
    base = _base()
    classes = _classification(base)
    widths = []
    for name in ("short", "frt00004185_07_if165j_mtr3.img CNN classes " * 3):
        overlay = VardaRaster(classes.dataSource, name=name)
        widget = OverlayBar(
            [base, overlay], base, [ImageViewport(ImageRenderer(image=base))]
        )
        qtbot.addWidget(widget)
        widget.setOverlay(overlay)
        widths.append(widget.sizeHint().width())
        index = widget.imageBox.currentIndex()
        assert widget.imageBox.itemData(index, Qt.ItemDataRole.ToolTipRole) == name
    assert widths[1] == widths[0]  # the name is elided, not given more room


def test_a_band_overlay_takes_the_chosen_color_map_reversed(bar):
    widget, viewports, images = bar
    widget.setOverlay(images[1])
    assert not widget.colorsButton.isVisibleTo(widget)  # classes keep their colors
    widget.layerBox.setCurrentText("P(dark)")
    assert widget.colorsButton.isVisibleTo(widget)
    widget.setColors(colorMap="Gray")
    lut = widget.renderer.settings.mono.colorMap.get().getLookupTable(0, 1, 2)
    assert tuple(lut[0][:3]) < tuple(lut[-1][:3])  # dark to light
    widget.reverseAction.trigger()
    lut = widget.renderer.settings.mono.colorMap.get().getLookupTable(0, 1, 2)
    assert tuple(lut[0][:3]) > tuple(lut[-1][:3])


def test_a_percentile_stretch_leaves_outliers_out(qtbot):
    base = _base()
    values = np.linspace(0, 1, 48).reshape(6, 8)
    values[0, 0] = 100.0  # one outlier
    result = _raster(values, "index", bandNames=["Index"])
    viewport = ImageViewport(ImageRenderer(image=base))
    qtbot.addWidget(viewport)
    widget = OverlayBar([base, result], base, [viewport])
    qtbot.addWidget(widget)
    widget.setOverlay(result)
    assert np.ravel(widget.renderer.getMinMaxValues()[1])[0] == pytest.approx(100)
    widget.setColors(stretch="5-95 %")  # 1 of 48 pixels is over 2 %
    assert np.ravel(widget.renderer.getMinMaxValues()[1])[0] < 2


def test_clicking_a_swatch_recolors_the_class_in_every_view(bar, qtbot, monkeypatch):
    widget, viewports, images = bar
    other = OverlayBar(
        images, images[0], [ImageViewport(ImageRenderer(image=images[0]))]
    )
    qtbot.addWidget(other)
    widget.setOverlay(images[1])
    other.setOverlay(images[1])
    assert 'href="class:1"' in viewports[0].legendText()
    monkeypatch.setattr(
        "varda.workspaces.raster_overlay.QColorDialog.getColor",
        lambda *a: QColor("#00ff00"),
    )
    viewports[0].sigLegendLink.emit("class:1")
    assert images[1].extraMetadata["classColors"] == ["#ff0000", "#00ff00"]
    assert "#00ff00" in viewports[0].legendText()
    rgb = other.renderer.image.getData()
    assert tuple(rgb[0, 7]) == (0.0, 1.0, 0.0)  # the other workspace follows


def test_a_deleted_bar_ignores_new_project_images(qtbot):
    """The project's image list outlives a closed workspace's bar: an image
    added afterwards must not call into the deleted bar."""
    base = _base()
    images = ProjectImages([base])
    widget = OverlayBar(images, base, [])
    sip.delete(widget)
    images.append(_classification(base))  # raised RuntimeError (deleted object)
