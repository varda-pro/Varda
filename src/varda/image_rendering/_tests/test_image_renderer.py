import warnings

import numpy as np

from varda.common.entities import VardaRaster
from varda.image_rendering.stretch_algorithms import MinMaxStretch
from varda.image_loading.data_sources import ArrayDataSource
from varda.image_rendering.image_renderer import (
    RenderMode,
    RendererSettings,
)


def make_image(bands: int = 5) -> VardaRaster:
    data = (np.random.rand(8, 9, bands) * 100).astype(np.float32)
    wavelengths = np.array([400.0 + i * 100 for i in range(bands)])
    return VardaRaster(dataSource=ArrayDataSource(data, wavelengths=wavelengths))


def test_settings_defaults(qtbot):
    img = make_image(5)
    s = RendererSettings(img)
    assert s.image is img
    assert s.mode.get() == RenderMode.MONO
    assert s.opacity.get() == 1.0
    # bands seeded from image.defaultBands
    red, green, blue = (int(b) for b in img.defaultBands)
    assert s.rgb.red.get() == red
    assert s.rgb.green.get() == green
    assert s.rgb.blue.get() == blue
    assert s.mono.band.get() == red


def test_settings_band_params_are_image_aware(qtbot):
    img = make_image(5)
    s = RendererSettings(img)
    assert s.rgb.red.image is img
    assert s.mono.band.image is img


def test_settings_change_emits_group(qtbot):
    s = RendererSettings(make_image(5))
    received = []
    s.sigParameterChanged.connect(lambda g: received.append(g))
    s.rgb.red.set(3)
    assert received and received[-1] is s


def test_settings_stretch_default_is_auto(qtbot):
    s = RendererSettings(make_image(5))
    assert s.stretch.nameOf(s.stretch.current) == "Min-Max (Auto Full Range)"


def test_settings_defaults_single_band_image(qtbot):
    s = RendererSettings(make_image(1))
    assert s.rgb.red.get() == 0
    assert s.rgb.green.get() == 0
    assert s.rgb.blue.get() == 0
    assert s.mono.band.get() == 0


from varda.common.vec2 import Vec2
from varda.image_rendering.image_renderer import ImageRenderer


def make_renderer(bands: int = 5) -> ImageRenderer:
    return ImageRenderer(image=make_image(bands))


def test_render_returns_rgba(qtbot):
    out = make_renderer().render()
    assert out.ndim == 3 and out.shape[2] == 4


def test_render_is_cached(qtbot):
    r = make_renderer()
    r.render()
    assert r.cachedRender is not None


def test_param_change_invalidates_cache_and_emits_refresh(qtbot):
    r = make_renderer()
    r.render()
    with qtbot.waitSignal(r.sigShouldRefresh, timeout=1000):
        r.settings.opacity.set(0.5)
    assert r.cachedRender is None


def test_mode_switch_changes_render_path(qtbot):
    r = make_renderer()
    r.settings.mode.set(RenderMode.RGB)
    out = r.render()
    assert out.shape[2] == 4


def test_setManualStretch_activates_manual_on_all_channels(qtbot):
    r = make_renderer()
    r.setManualStretch(10.0, 50.0)
    stretch = r.settings.stretch
    assert stretch.nameOf(stretch.current) == "Min-Max (Manual)"
    assert stretch.current.config.redStretch.get() == Vec2(10.0, 50.0)
    assert stretch.current.config.greenStretch.get() == Vec2(10.0, 50.0)
    assert stretch.current.config.blueStretch.get() == Vec2(10.0, 50.0)


def test_setStretchMinMax_seeds_other_channels_and_sets_target(qtbot):
    r = make_renderer()
    r.render()  # auto stretch computes its min/max for seeding
    r.setStretchMinMax(0, 5.0, 7.0)
    manual = r.settings.stretch.option("Min-Max (Manual)")
    assert r.settings.stretch.current is manual
    assert manual.config.redStretch.get() == Vec2(5.0, 7.0)
    # green/blue were seeded from the auto stretch (not left at the Vec2(0, 1) default)
    assert manual.config.greenStretch.get() != Vec2(0.0, 1.0)


def test_setManualStretch_emits_single_refresh(qtbot):
    r = make_renderer()
    r.setManualStretch(10.0, 50.0)  # first call: auto -> manual
    count = 0

    def _inc():
        nonlocal count
        count += 1

    r.sigShouldRefresh.connect(_inc)
    r.setManualStretch(20.0, 60.0)  # manual already active (was the storm case)
    assert count == 1


def test_setStretchMinMax_emits_single_refresh_when_switching(qtbot):
    r = make_renderer()
    r.render()  # auto stretch active; computes min/max for seeding
    count = 0

    def _inc():
        nonlocal count
        count += 1

    r.sigShouldRefresh.connect(_inc)
    # switching into manual + seeding + setting one channel must be ONE refresh
    r.setStretchMinMax(0, 5.0, 7.0)
    assert count == 1
    assert r.settings.stretch.nameOf(r.settings.stretch.current) == "Min-Max (Manual)"


def _renderer_for(data: np.ndarray) -> ImageRenderer:
    return ImageRenderer(image=VardaRaster(dataSource=ArrayDataSource(data)))


def test_no_stretch_renders_and_clips_to_0_1(qtbot):
    # No Stretch hands back the renderer's read-only band array; the renderer
    # must not write into it, and values outside 0-1 must clip, not wrap
    data = np.array([[[-0.5], [0.5], [2.0], [np.nan]]], dtype=np.float32)
    r = _renderer_for(data)
    r.settings.mode.set(RenderMode.RGB)
    r.settings.stretch.selectByName("No Stretch")
    rgba = r.render()
    assert list(rgba[0, :, 0]) == [0, 127, 255, 0]
    assert np.isnan(r.getRawBandData()[0, 3]).all()


def test_render_clips_out_of_range_stretch_output(qtbot):
    data = np.array([[[0.0], [1.0], [5.0]]], dtype=np.float32)
    r = _renderer_for(data)
    r.settings.mode.set(RenderMode.RGB)
    r.setManualStretch(0.0, 1.0)
    rgba = r.render()
    assert list(rgba[0, :, 0]) == [0, 255, 255]


def test_a_constant_image_renders_without_a_divide_warning(qtbot):
    """Release QA 0.12.0 #19: opening a constant GeoTIFF printed numpy's
    "invalid value encountered in divide" (min-max with min == max)."""
    image = VardaRaster(ArrayDataSource(np.full((8, 8, 3), 0.2, np.float32)))
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        stretched = MinMaxStretch().apply(image.getData())
        mono = MinMaxStretch().apply(image.getData()[..., :1])
        ImageRenderer(image).render()
    assert np.isnan(stretched).all() and np.isnan(mono).all()  # as before
