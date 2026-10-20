import numpy as np

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources import ArrayDataSource
from varda.image_rendering.render_parameters import BandParameter
from varda.common.parameter_widgets import parameterWidget


def make_image(bands: int = 5) -> VardaRaster:
    data = (np.random.rand(8, 9, bands) * 100).astype(np.float32)
    wavelengths = np.array([400.0 + i * 100 for i in range(bands)])
    return VardaRaster(dataSource=ArrayDataSource(data, wavelengths=wavelengths))


def test_the_band_list_shows_rounded_wavelengths_with_their_unit(qtbot):
    """Release QA #7: 'Band 2353.8461538461543', no unit."""
    data = np.zeros((4, 4, 3), dtype=np.float32)
    wavelengths = np.array([2340.7692307692305, 2347.3076923076924, 2353.8461538461543])
    image = VardaRaster(
        dataSource=ArrayDataSource(data, wavelengths=wavelengths, wavelengthUnits="nm")
    )
    p = BandParameter("Band", 0)
    p.setImage(image)
    widget = parameterWidget(p)
    qtbot.addWidget(widget)
    combo = widget.comboBox
    assert [combo.itemText(i) for i in range(3)] == [
        "2340.8 nm",
        "2347.3 nm",
        "2353.8 nm",
    ]


def test_band_parameter_set_get(qtbot):
    p = BandParameter("Band", 0)
    p.setImage(make_image(5))
    p.set(3)
    assert p.get() == 3


def test_band_parameter_clamps_stale_value_on_set_image(qtbot):
    p = BandParameter("Band", 0)
    p.value = 99  # stale index from before an image was attached
    p.setImage(make_image(5))
    assert p.get() == 0


def test_band_parameter_clone_preserves_value_and_image(qtbot):
    img = make_image(5)
    p = BandParameter("Band", 0)
    p.setImage(img)
    p.set(2)
    c = p.clone()
    assert c.get() == 2
    assert c.image is img


def test_band_parameter_widget_reflects_value(qtbot):
    img = make_image(4)
    p = BandParameter("Band", 0)
    p.setImage(img)
    w = parameterWidget(p)
    qtbot.addWidget(w)
    p.set(2)
    assert w.comboBox.currentIndex() == 2


def test_band_parameter_widget_combo_drives_param(qtbot):
    img = make_image(4)
    p = BandParameter("Band", 0)
    p.setImage(img)
    w = parameterWidget(p)
    qtbot.addWidget(w)
    w.comboBox.setCurrentIndex(3)
    assert p.get() == 3


import pyqtgraph as pg

from varda.image_rendering.render_parameters import ColorMapParameter


def test_colormap_default_is_a_colormap(qtbot):
    p = ColorMapParameter("Color Map")
    assert isinstance(p.get(), pg.ColorMap)


def test_colormap_set_get(qtbot):
    p = ColorMapParameter("Color Map")
    new = pg.ColorMap(None, color=[1.0, 0.0])
    p.set(new)
    assert p.get() is new


def test_colormap_clone(qtbot):
    p = ColorMapParameter("Color Map")
    c = p.clone()
    assert isinstance(c, ColorMapParameter)
    assert isinstance(c.get(), pg.ColorMap)


def test_colormap_widget_builds(qtbot):
    p = ColorMapParameter("Color Map")
    w = parameterWidget(p)
    qtbot.addWidget(w)
    assert w.gradient is not None


from varda.common.vec2 import Vec2
from varda.image_rendering.render_parameters import StretchParameter


def test_stretch_default_is_auto(qtbot):
    p = StretchParameter("Stretch")
    assert p.nameOf(p.current) == "Min-Max (Auto Full Range)"


def test_stretch_select_by_name(qtbot):
    p = StretchParameter("Stretch")
    p.selectByName("Min-Max (Manual)")
    assert p.nameOf(p.current) == "Min-Max (Manual)"
    assert p.current is p.option("Min-Max (Manual)")


def test_stretch_subparam_change_propagates(qtbot):
    p = StretchParameter("Stretch")
    p.selectByName("Min-Max (Manual)")
    received = []
    p.sigParameterChanged.connect(lambda v: received.append(v))
    p.option("Min-Max (Manual)").config.redStretch.set(Vec2(0.1, 0.9))
    assert len(received) >= 1


def test_stretch_inactive_subparam_change_does_not_propagate(qtbot):
    p = StretchParameter("Stretch")  # default active = Auto
    received = []
    p.sigParameterChanged.connect(lambda v: received.append(v))
    # Manual is NOT the active algorithm; changing it must not emit
    p.option("Min-Max (Manual)").config.redStretch.set(Vec2(0.3, 0.7))
    assert received == []


def test_stretch_clone_preserves_selection_with_independent_instances(qtbot):
    p = StretchParameter("Stretch")
    p.selectByName("Linear Percentile")
    c = p.clone()
    assert c.nameOf(c.current) == "Linear Percentile"
    assert c.current is not p.current


def test_stretch_widget_combo_drives_selection(qtbot):
    p = StretchParameter("Stretch")
    w = parameterWidget(p)
    qtbot.addWidget(w)
    manual_index = p.optionNames.index("Min-Max (Manual)")
    w.comboBox.setCurrentIndex(manual_index)
    assert p.nameOf(p.current) == "Min-Max (Manual)"
    assert w.stack.currentIndex() == manual_index
