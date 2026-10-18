from varda.geo._tests.fixtures import CRISM_MTRDR_WKT, WGS84, makeImage
from varda.image_rendering.image_renderer import ImageRenderer
from varda.image_rendering.raster_view import image_viewport
from varda.image_rendering.raster_view.image_viewport import ImageViewport


def _viewport(qtbot, crs: str, lonLat: tuple[float, float], size: float):
    image = makeImage(crs, centerLonLat=lonLat, pixelSize=size)
    viewport = ImageViewport(ImageRenderer(image=image))
    qtbot.addWidget(viewport)
    return viewport


def test_the_readout_names_the_body_and_conventions(qtbot):
    viewport = _viewport(qtbot, CRISM_MTRDR_WKT, (77.5, 18.4), 18.0)
    text = viewport._buildReadoutText(10, 10)
    assert "Mars, planetocentric" in text
    assert "xy " in text and " m" in text


def test_a_geographic_image_gets_no_xy_line(qtbot):
    viewport = _viewport(qtbot, WGS84, (10.0, 20.0), 0.001)
    text = viewport._buildReadoutText(10, 10)
    assert "Earth" in text
    assert "xy " not in text


def test_the_description_is_built_once_and_rebuilt_on_metadata_changes(
    qtbot, monkeypatch
):
    calls = []
    real = image_viewport.describeCrs

    def counting(*args):
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(image_viewport, "describeCrs", counting)
    viewport = _viewport(qtbot, CRISM_MTRDR_WKT, (77.5, 18.4), 18.0)
    for _ in range(3):
        viewport._buildReadoutText(10, 10)
    assert len(calls) == 1
    viewport._imageRenderer.image.sigMetadataChanged.emit()
    assert len(calls) == 2
