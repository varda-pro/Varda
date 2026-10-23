"""Browse products in the display settings: choosing one shows its channels
in RGB once computed, stretched to their ranges; going back to the bands
brings back the stretch; custom products are saved and offered again."""

import json

import numpy as np
import pytest
from PyQt6.QtWidgets import QDialog

from varda.analysis import browse_products
from varda.analysis.browse_products import CRISM_STANDARD, parseProduct, productRanges
from varda.common.entities import VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_rendering import browse_controls
from varda.image_rendering.browse_composites import COMPOSITES
from varda.image_rendering.browse_controls import (
    BANDS_LABEL,
    CustomProductDialog,
    customProducts,
)
from varda.image_rendering.image_renderer import (
    ImageRenderer,
    RendererSettings,
    RenderMode,
)
from varda.image_rendering.render_parameters import StretchParameter
from varda.image_rendering.render_state import restoreSettings, settingsState

CRISM_NM = np.arange(436.0, 3920.0, 6.55)
FAL = next(p for p in CRISM_STANDARD if p.name == "FAL")
MAF = next(p for p in CRISM_STANDARD if p.name == "MAF")


def _image() -> VardaRaster:
    rng = np.random.default_rng(5)
    cube = 0.2 + 0.05 * rng.random((12, 9, CRISM_NM.size))
    return VardaRaster(
        ArrayDataSource(cube, wavelengths=CRISM_NM, wavelengthUnits="nm"),
        name="frt",
    )


def _ready(qtbot, renderer: ImageRenderer) -> None:
    def shown():
        renderer.render()
        assert renderer.showsBrowseProduct()

    qtbot.waitUntil(shown, timeout=10_000)


def _stretchName(settings: RendererSettings) -> str:
    return settings.stretch.nameOf(settings.stretch.current)


def test_choosing_a_product_shows_its_channels_stretched_to_their_ranges(qtbot):
    image = _image()
    renderer = ImageRenderer(image)
    settings = renderer.settings
    settings.stretch.selectByName("Linear Percentile")
    settings.showBrowseProduct(FAL)
    assert settings.mode.get() is RenderMode.RGB
    _ready(qtbot, renderer)

    composite = COMPOSITES.get(image, FAL)
    assert composite is not None and composite.shape == (12, 9, 3)
    np.testing.assert_array_equal(renderer.getRawBandData(), composite)
    assert _stretchName(settings) == StretchParameter.MANUAL_NAME
    manual = settings.stretch.option(StretchParameter.MANUAL_NAME).config
    shown = [
        (v.x, v.y)
        for v in (
            manual.redStretch.get(),
            manual.greenStretch.get(),
            manual.blueStretch.get(),
        )
    ]
    assert shown == pytest.approx(productRanges(FAL, composite))

    settings.showBrowseProduct(None)
    assert _stretchName(settings) == "Linear Percentile"
    renderer.render()
    assert not renderer.showsBrowseProduct()


def test_one_band_mode_leaves_the_product(qtbot):
    settings = RendererSettings(_image())
    settings.showBrowseProduct(FAL)
    settings.mode.set(RenderMode.MONO)
    assert settings.browse.get() == "" and settings.browseProduct() is None


def test_views_of_one_image_compute_its_product_once(qtbot, monkeypatch):
    calls = []
    compute = browse_products.computeProduct

    def counted(image, product, progress=None):
        calls.append(product.name)
        return compute(image, product, progress)

    monkeypatch.setattr(
        "varda.image_rendering.browse_composites.computeProduct", counted
    )
    image = _image()
    main, zoom = ImageRenderer(image), ImageRenderer(image)
    zoom.settings = main.settings  # not how views share, but both ask
    main.settings.showBrowseProduct(FAL)
    other = ImageRenderer(image)
    other.settings.showBrowseProduct(FAL)
    _ready(qtbot, main)
    _ready(qtbot, other)
    assert calls == ["FAL"]


def test_a_result_computed_before_an_edit_is_dropped(qtbot):
    image = _image()
    COMPOSITES.request(image, FAL)
    COMPOSITES.forget(image)  # the wavelengths were edited, say
    qtbot.wait(500)
    assert COMPOSITES.get(image, FAL) is None
    COMPOSITES.request(image, FAL)  # asked again: computed anew
    qtbot.waitUntil(lambda: COMPOSITES.get(image, FAL) is not None, timeout=10_000)


def test_a_product_the_image_cant_show_says_why_and_the_bands_stay(qtbot):
    image = _image()
    renderer = ImageRenderer(image)
    renderer.settings.browse.set("X = R9000, R9000, R9000")
    panel = renderer.getSettingsPanel()
    qtbot.addWidget(panel)
    renderer.render()
    channels = panel.bandStack.currentWidget()
    qtbot.waitUntil(lambda: "no band near 9000 nm" in channels.status.text())
    renderer.render()
    assert not renderer.showsBrowseProduct()


def test_the_picker_lists_crism_s_products_and_greys_out_what_cant_be_shown(qtbot):
    pytest.importorskip("hypyrameter")
    renderer = ImageRenderer(_image())
    panel = renderer.getSettingsPanel()
    qtbot.addWidget(panel)
    picker = panel.browsePicker
    assert picker is not None
    rows = [picker.model.item(r) for r in range(picker.model.rowCount())]
    assert rows[0].text() == BANDS_LABEL and picker.combo.currentIndex() == 0
    byName = {r.text().split()[0]: r for r in rows[2:]}
    assert all(row.isEnabled() for row in byName.values())  # all 18, from 436 nm

    picker.combo.setCurrentIndex(rows.index(byName["MAF"]))
    picker.combo.activated.emit(picker.combo.currentIndex())
    assert renderer.settings.browseProduct() is MAF
    assert panel.bandStack.currentIndex() == panel._browseIndex
    channels = panel.bandStack.currentWidget()
    assert [r.text() for r in channels.rows] == list(MAF.channels)
    assert "olivine" in channels.rows[0].toolTip().lower()
    _ready(qtbot, renderer)
    qtbot.waitUntil(lambda: "stretched" in channels.status.text())


def test_products_beyond_the_image_s_wavelengths_are_greyed_out(qtbot):
    pytest.importorskip("hypyrameter")
    wavelengths = np.arange(436.0, 2600.0, 6.55)  # no 3 µm
    image = VardaRaster(
        ArrayDataSource(
            np.full((6, 5, wavelengths.size), 0.2),
            wavelengths=wavelengths,
            wavelengthUnits="nm",
        ),
        name="short",
    )
    panel = ImageRenderer(image).getSettingsPanel()
    qtbot.addWidget(panel)
    model = panel.browsePicker.model
    rows = [model.item(r) for r in range(model.rowCount())]
    byName = {r.text().split()[0]: r for r in rows[2:]}
    greyed = {name for name, row in byName.items() if not row.isEnabled()}
    assert greyed == {"IC2", "CHL", "CR2"}  # R3920, BD3000, CINDEX2
    assert "CINDEX2 needs 3450-3875 nm" in byName["CR2"].toolTip()


def test_an_image_without_wavelengths_has_no_picker(qtbot):
    image = VardaRaster(ArrayDataSource(np.zeros((10, 10, 3))), name="rgb")
    assert image.bandModel.centers is None
    panel = ImageRenderer(image).getSettingsPanel()
    qtbot.addWidget(panel)
    assert panel.browsePicker is None


def test_a_custom_product_is_checked_saved_and_shown(qtbot, monkeypatch):
    image = _image()
    dialog = CustomProductDialog(image)
    qtbot.addWidget(dialog)
    save = dialog.buttons.button(dialog.buttons.StandardButton.Save)
    assert not save.isEnabled() and "name" in dialog.problem.text()
    dialog.name.setText("MAF")
    for box, channel in zip(dialog.channels, ("R2529", "R1330", "R9000")):
        box.setCurrentText(channel)
    assert "CRISM standard" in dialog.problem.text()
    dialog.name.setText("Albedo trio")
    assert dialog.problem.text() == "no band near 9000 nm"
    dialog.channels[2].setCurrentText("R770")
    assert save.isEnabled()
    dialog.show()
    qtbot.waitExposed(dialog)
    assertNothingClipped(dialog)

    renderer = ImageRenderer(image)
    panel = renderer.getSettingsPanel()
    qtbot.addWidget(panel)
    picker = panel.browsePicker
    assert picker is not None
    monkeypatch.setattr(
        browse_controls.CustomProductDialog,
        "exec",
        lambda self: (
            self.name.setText("Albedo trio"),
            [
                b.setCurrentText(c)
                for b, c in zip(self.channels, ("R2529", "R1330", "R770"))
            ],
            QDialog.DialogCode.Accepted,
        )[-1],
    )
    picker.newProduct()
    mine = parseProduct("Albedo trio = R2529, R1330, R770")
    assert customProducts() == [mine]
    assert renderer.settings.browseProduct() == mine
    assert picker.combo.currentText() == "Albedo trio"
    _ready(qtbot, renderer)

    picker.deleteProduct()
    assert customProducts() == []
    assert renderer.settings.browseProduct() is None
    assert picker.combo.currentText() == BANDS_LABEL


def test_the_product_and_its_stretch_survive_a_session(qtbot):
    image = _image()
    renderer = ImageRenderer(image)
    renderer.settings.showBrowseProduct(FAL)
    _ready(qtbot, renderer)
    renderer.setStretchMinMax(0, 0.1, 0.2)  # the user's own red range
    state = json.loads(json.dumps(settingsState(renderer.settings)))

    restored = ImageRenderer(image)
    restoreSettings(restored.settings, state)
    assert restored.settings.browseProduct() is FAL
    _ready(qtbot, restored)
    red = restored.settings.stretch.option(
        StretchParameter.MANUAL_NAME
    ).config.redStretch
    assert (red.get().x, red.get().y) == pytest.approx((0.1, 0.2))  # not reset
