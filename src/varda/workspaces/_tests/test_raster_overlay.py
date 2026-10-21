"""Overlaying a co-registered project image (e.g. a classification) on a workspace view."""

import numpy as np
import pytest
from affine import Affine
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from pyproj import CRS

from varda.common.di_types import ProjectImages
from varda.common.display_grid import reprojectForDisplay
from varda.common.entities import VardaRaster
from varda.common.grid import compatibleImages
from varda.common.layout_audit import assertNothingClipped
from varda.geo._tests.fixtures import WGS84, makeImage
from varda.geo.transform import transformPoints
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.workspaces import raster_overlay
from varda.workspaces.general_image_analysis.general_image_analysis import (
    GeneralImageAnalysisConfig,
    GeneralImageAnalysisWorkflow,
)
from varda.workspaces.raster_overlay import (
    CLASSES,
    NO_OVERLAY,
    OverlayBar,
    overlayLayers,
    overlayRaster,
    setClassColor,
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


def test_the_bar_says_which_images_cant_be_overlaid(qtbot):
    base = _base()
    smaller = _raster(np.zeros((3, 8)), "smaller")
    widget = OverlayBar(ProjectImages([base, smaller]), base, [])
    qtbot.addWidget(widget)
    assert widget.imageBox.count() == 1
    # Release QA #13: "(1 not on this grid)" was unexplained; 0.12.0: "1
    # image can't be overlaid" said neither why nor what to do
    assert widget.notListed.text() == (
        "1 image can't be overlaid: this image has no map coordinates, so "
        "only images of its size (8 × 6) can be. Resample one to that size "
        "(Processing → Geometry)."
    )
    assert widget.notListed.wordWrap()
    assert "smaller (a different size" in widget.notListed.toolTip()


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


@pytest.fixture
def projectBar(qtbot, monkeypatch):
    """A bar over the project's image list, counting its views' redraws."""
    base = _base()
    images = ProjectImages([base])
    viewports = [ImageViewport(ImageRenderer(image=base)) for _ in range(2)]
    redraws = []
    for viewport in viewports:
        qtbot.addWidget(viewport)
        monkeypatch.setattr(viewport, "refresh", lambda: redraws.append(1))
    widget = OverlayBar(images, base, viewports)
    qtbot.addWidget(widget)
    return widget, viewports, images, redraws


def test_without_an_overlay_new_images_are_offered_without_redrawing(projectBar):
    widget, _viewports, images, redraws = projectBar
    result = _classification(images[0])
    images.append(result)
    images.append(_raster(np.zeros((3, 8)), "smaller"))
    images.refresh()  # e.g. Image Properties OK
    assert widget.imageBox.count() == 2
    assert widget.imageBox.itemText(1) == result.name
    assert redraws == []


def test_a_shown_overlay_stays_without_redrawing_until_it_is_removed(projectBar):
    widget, viewports, images, redraws = projectBar
    result = _classification(images[0])
    images.append(result)
    widget.setOverlay(result)
    widget.layerBox.setCurrentText("P(bright)")
    redraws.clear()

    images.append(_classification(images[0]))
    assert widget.currentOverlay() is result
    assert widget.layerBox.currentText() == "P(bright)"
    assert redraws == []

    images.remove(result)
    assert widget.currentOverlay() is None
    assert not any(v.hasOverlayImage() for v in viewports)
    assert redraws  # the overlay is taken off the views


def test_a_renamed_image_is_listed_under_its_new_name(projectBar):
    widget, _viewports, images, redraws = projectBar
    result = _classification(images[0])
    images.append(result)
    result.setMetadataOverrides({}, name="renamed")
    images.refresh()
    assert widget.imageBox.itemText(1) == "renamed"
    assert redraws == []


def test_editing_the_shown_overlay_shows_it_again(projectBar):
    widget, _viewports, images, redraws = projectBar
    result = _classification(images[0])
    images.append(result)
    widget.setOverlay(result)
    widget.layerBox.setCurrentText("P(dark)")
    before = widget.renderer
    redraws.clear()

    result.setMetadataOverrides({"nodata": 0.0})

    assert widget.renderer is not before
    assert widget.layerBox.currentText() == "P(dark)"  # the layer chosen stays
    assert redraws


def test_a_rename_shows_at_once(projectBar):
    widget, _viewports, images, redraws = projectBar
    result = _classification(images[0])
    images.append(result)
    result.setMetadataOverrides({}, name="renamed")  # no list refresh
    assert widget.imageBox.itemText(1) == "renamed"
    assert redraws == []


# --- overlays across projections ------------------------------------------------

MARS_EQC = "IAU_2015:49910"  # Mars sphere, equirectangular
MARS_EQC_OCENTRIC = "IAU_2015:49912"  # Mars ellipsoid, planetocentric


def _marsBase() -> VardaRaster:
    return makeImage(
        MARS_EQC, centerLonLat=(10, 5), pixelSize=1000, bands=3, name="mars base"
    )


def _marsClassMap(name: str = "mars classes") -> VardaRaster:
    """A two-class map near the base, on the Mars ellipsoid."""
    grid = makeImage(MARS_EQC_OCENTRIC, centerLonLat=(10, 5), pixelSize=900)
    classes = np.zeros((64, 64, 1), np.float32)
    classes[:, 32:] = 1
    source = ArrayDataSource(
        classes,
        bandNames=["Class"],
        transform=grid.transform,
        crs=grid.crs,
        extraMetadata={"classes": ["dark", "bright"]},
    )
    return VardaRaster(source, name=name)


def _marsBand(name: str = "mars index") -> VardaRaster:
    grid = makeImage(MARS_EQC_OCENTRIC, centerLonLat=(10, 5), pixelSize=800)
    source = ArrayDataSource(
        np.linspace(0, 1, 64 * 64, dtype=np.float32).reshape(64, 64, 1),
        bandNames=["Index"],
        transform=grid.transform,
        crs=grid.crs,
    )
    return VardaRaster(source, name=name)


@pytest.fixture
def marsBar(qtbot):
    base = _marsBase()
    images = ProjectImages([base, _marsClassMap()])
    viewport = ImageViewport(ImageRenderer(image=base))
    qtbot.addWidget(viewport)
    widget = OverlayBar(images, base, [viewport])
    qtbot.addWidget(widget)
    return widget, viewport, images


def _waitReprojected(qtbot, widget: OverlayBar) -> None:
    qtbot.waitUntil(lambda: widget.reprojectedLabel.text() == "reprojected for display")


def test_an_overlay_in_another_projection_is_reprojected_for_display(marsBar, qtbot):
    widget, viewport, images = marsBar
    assert widget.imageBox.count() == 2  # the class map is offered
    widget.setOverlay(images[1])
    assert widget.reprojectedLabel.text() == "Reprojecting…"
    assert widget.reprojectedLabel.isVisibleTo(widget)
    _waitReprojected(qtbot, widget)
    assert viewport.hasOverlayImage()
    assert widget.reprojectedLabel.isVisibleTo(widget)
    tip = widget.reprojectedLabel.toolTip()
    assert "nearest neighbor" in tip and "for display only" in tip
    # the class map is shown in its classes' colors, on the base's grid
    assert widget.renderer is not None
    shown = widget.renderer.image
    assert shown.getData().shape[:2] == (images[0].height, images[0].width)
    assert "dark" in viewport.legendText()


def test_an_image_of_another_body_is_not_offered(qtbot):
    base = _marsBase()
    earth = makeImage(WGS84, centerLonLat=(10, 5), pixelSize=0.01, name="earth")
    widget = OverlayBar(ProjectImages([base, earth]), base, [])
    qtbot.addWidget(widget)
    assert widget.imageBox.count() == 1
    assert widget.notListed.text() == (
        "1 image can't be overlaid: of another body. Check its coordinate "
        "system in Image Properties."
    )
    tip = widget.notListed.toolTip()
    assert "Mars" in tip and "Earth" in tip and "earth" in tip


@pytest.mark.parametrize("width", [180, 260, 600])
def test_the_explanation_wraps_in_a_narrow_bar(qtbot, width):
    base = _base()
    smaller = _raster(np.zeros((3, 8)), "smaller")
    widget = OverlayBar(ProjectImages([base, smaller]), base, [])
    qtbot.addWidget(widget)
    widget.resize(width, widget.heightForWidth(width))
    widget.show()
    qtbot.waitExposed(widget)
    assertNothingClipped(widget)


def test_an_image_without_map_coordinates_is_explained(qtbot):
    base = _marsBase()
    plain = _raster(np.zeros((3, 8)), "plain")
    widget = OverlayBar(ProjectImages([base, plain]), base, [])
    qtbot.addWidget(widget)
    assert widget.notListed.text() == (
        "1 image can't be overlaid: no map coordinates. Open a map-projected "
        "version of it."
    )
    assert "plain (not georeferenced" in widget.notListed.toolTip()


def test_images_left_out_for_different_reasons_name_each_reason(qtbot):
    base = _marsBase()
    plain = _raster(np.zeros((3, 8)), "plain")
    earth = makeImage(WGS84, centerLonLat=(10, 5), pixelSize=0.01, name="earth")
    widget = OverlayBar(ProjectImages([base, plain, earth]), base, [])
    qtbot.addWidget(widget)
    assert widget.notListed.text() == (
        "2 images can't be overlaid: no map coordinates (1), of another body "
        "(1). Hover for each image and what to do."
    )
    tip = widget.notListed.toolTip()
    assert "Open a map-projected version" in tip and "Image Properties" in tip
    assert "plain (" in tip and "earth (" in tip


def test_display_marker_keeps_planetocentric_latitude():
    """A marker at 45°N on the Mars sphere lands, on an ellipsoid base,
    within ½ pixel of where ``transformPoints`` puts it."""
    sphere = CRS.from_user_input("+proj=longlat +R=3396190 +no_defs")
    values = np.zeros((100, 100, 1), np.float32)
    values[50, 50] = 1.0  # centered at 77.505°E, 44.995°N
    overlay = _raster(
        values,
        "sphere",
        transform=Affine(0.01, 0, 77.0, 0, -0.01, 45.5),
        crs=sphere,
    )
    ellipsoid = CRS.from_user_input(MARS_EQC_OCENTRIC)
    (x,), (y,) = transformPoints(sphere, ellipsoid, [77.505], [44.995])
    transform = Affine(500.0, 0, x - 50.5 * 500.0, 0, -500.0, y + 50.5 * 500.0)
    base = _raster(np.zeros((100, 100)), "base", transform=transform, crs=ellipsoid)

    shown = reprojectForDisplay(overlay, base, 0)

    rows, cols = np.nonzero(shown == 1.0)
    assert rows.size > 0, "the marker fell off the grid"
    assert abs(rows.mean() - 50) <= 0.5 and abs(cols.mean() - 50) <= 0.5


def test_a_stale_reprojection_is_dropped(qtbot, monkeypatch):
    base = _marsBase()
    first, second = _marsClassMap(), _marsBand()
    finished = []

    def reproject(overlay, onto, band):
        result = reprojectForDisplay(overlay, onto, band)
        finished.append(overlay)
        return result

    monkeypatch.setattr(raster_overlay, "reprojectForDisplay", reproject)
    delivered = []
    onReprojected = OverlayBar._onReprojected

    def deliver(self, *args):
        delivered.append(args[0])
        onReprojected(self, *args)

    monkeypatch.setattr(OverlayBar, "_onReprojected", deliver)
    viewport = ImageViewport(ImageRenderer(image=base))
    qtbot.addWidget(viewport)
    shown = []
    original = viewport.overlayImage
    monkeypatch.setattr(
        viewport, "overlayImage", lambda r: (shown.append(r), original(r))
    )
    widget = OverlayBar(ProjectImages([base, first, second]), base, [viewport])
    qtbot.addWidget(widget)

    widget.setOverlay(first)
    widget.setOverlay(second)  # at once, before the first is done
    qtbot.waitUntil(lambda: len(delivered) == 2)  # both results came back
    assert widget.reprojectedLabel.text() == "reprojected for display"

    assert len(shown) == 1
    assert list(shown[0].image.bandNames) == ["Index 1"]
    assert raster_overlay.displayCache.get(first, base, 0) is None


def test_a_class_color_change_recolors_from_the_cache(marsBar, qtbot, monkeypatch):
    widget, viewport, images = marsBar
    calls = []

    def reproject(overlay, onto, band):
        calls.append(band)
        return reprojectForDisplay(overlay, onto, band)

    monkeypatch.setattr(raster_overlay, "reprojectForDisplay", reproject)
    widget.setOverlay(images[1])
    _waitReprojected(qtbot, widget)
    assert calls == [0]
    before = widget.renderer

    setClassColor(images[1], 1, "#00ff00")

    assert calls == [0]  # not reprojected again
    assert widget.renderer is not before
    assert widget.reprojectedLabel.text() == "reprojected for display"
    rgb = widget.renderer.image.getData()
    assert np.any(np.all(rgb == (0.0, 1.0, 0.0), axis=2))


def test_editing_a_reprojected_overlay_reprojects_it_again(marsBar, qtbot):
    widget, viewport, images = marsBar
    widget.setOverlay(images[1])
    _waitReprojected(qtbot, widget)
    assert raster_overlay.displayCache.get(images[1], images[0], 0) is not None
    images[1].setMetadataOverrides({"nodata": 1.0})
    assert raster_overlay.displayCache.get(images[1], images[0], 0) is None
    _waitReprojected(qtbot, widget)
    assert viewport.hasOverlayImage()


def test_a_failed_reprojection_leaves_no_overlay(marsBar, qtbot, monkeypatch):
    widget, viewport, images = marsBar

    def fail(overlay, onto, band):
        raise ValueError("no overlap")

    monkeypatch.setattr(raster_overlay, "reprojectForDisplay", fail)
    widget.setOverlay(images[1])
    qtbot.waitUntil(
        lambda: widget.reprojectedLabel.text() == "couldn't reproject: no overlap"
    )
    assert not viewport.hasOverlayImage()
    assert widget.renderer is None
    # nothing is drawn, so neither is a legend nor an opacity slider
    assert viewport.legendText() == ""
    assert not widget.opacitySlider.isVisibleTo(widget)
    assert not widget.legendButton.isVisibleTo(widget)


def test_a_long_failure_is_elided_in_full_in_the_tooltip(marsBar, qtbot, monkeypatch):
    widget, _viewport, images = marsBar
    reason = "the warp ran out of memory " * 20

    def fail(overlay, onto, band):
        raise ValueError(reason)

    monkeypatch.setattr(raster_overlay, "reprojectForDisplay", fail)
    widget.setOverlay(images[1])
    qtbot.waitUntil(lambda: widget.reprojectedLabel.text().startswith("couldn't"))
    label = widget.reprojectedLabel
    assert label.text().endswith("…")
    assert label.sizeHint().width() <= raster_overlay.MAX_NOTE_WIDTH + 10
    assert reason in label.toolTip()


def test_no_legend_while_reprojecting(marsBar):
    widget, viewport, images = marsBar
    widget.setOverlay(images[1])
    assert widget.reprojectedLabel.text() == "Reprojecting…"
    assert viewport.legendText() == ""
    assert not widget.opacitySlider.isVisibleTo(widget)


def test_editing_the_base_georeferencing_reprojects_the_overlay(
    marsBar, qtbot, monkeypatch
):
    widget, viewport, images = marsBar
    calls = []

    def reproject(overlay, onto, band):
        calls.append(onto.transform)
        return reprojectForDisplay(overlay, onto, band)

    monkeypatch.setattr(raster_overlay, "reprojectForDisplay", reproject)
    widget.setOverlay(images[1])
    _waitReprojected(qtbot, widget)
    base = images[0]
    moved = base.transform * Affine.translation(5, 0)  # 5 km east

    base.setMetadataOverrides({"transform": list(moved)[:6]})

    assert widget.currentOverlay() is images[1]
    qtbot.waitUntil(lambda: len(calls) == 2)
    _waitReprojected(qtbot, widget)
    assert calls[1] == moved
    assert viewport.hasOverlayImage()


def test_a_base_moved_to_another_body_drops_the_overlay(marsBar, qtbot):
    widget, viewport, images = marsBar
    widget.setOverlay(images[1])
    _waitReprojected(qtbot, widget)

    images[0].setMetadataOverrides({"crs": WGS84})

    assert widget.currentOverlay() is None
    assert not viewport.hasOverlayImage()
    assert viewport.legendText() == ""
    label = widget.reprojectedLabel
    assert label.isVisibleTo(widget) and "removed" in label.text()
    assert "different bodies" in label.toolTip()
    assert "different bodies" in widget.notListed.toolTip()


def test_a_same_grid_overlay_shows_no_reprojection_label(bar):
    widget, viewports, images = bar
    widget.setOverlay(images[1])
    assert all(v.hasOverlayImage() for v in viewports)
    assert not widget.reprojectedLabel.isVisibleTo(widget)


def test_the_reprojection_label_fits_a_laptop(qtbot):
    base = _marsBase()
    classes = _marsClassMap(
        "frt00004185_07_if165j_mtr3.img CNN classes reprojected from CTX"
    )
    images = ProjectImages([base, classes])
    workspace = GeneralImageAnalysisWorkflow(GeneralImageAnalysisConfig(images))
    qtbot.addWidget(workspace)
    workspace.showNormal()
    workspace.resize(1280, 700)  # a 1280 × 800 screen less the menu bar and tabs
    qtbot.waitExposed(workspace)
    bar = workspace.overlayBar
    bar.setOverlay(classes)
    _waitReprojected(qtbot, bar)
    qtbot.wait(50)
    assert bar.reprojectedLabel.isVisible()
    assertNothingClipped(workspace)


def test_several_images_off_the_grid_are_counted_plainly(qtbot):
    base = _base()
    others = [_raster(np.zeros((3, 8)), f"small {i}") for i in range(2)]
    widget = OverlayBar(ProjectImages([base, *others]), base, [])
    qtbot.addWidget(widget)
    assert widget.notListed.text().startswith(
        "2 images can't be overlaid: this image has no map coordinates"
    )
