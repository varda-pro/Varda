import base64
import json
import re

import matplotlib
import numpy as np
import pytest

from varda.shapes._tests.meshes import icosphere
from varda.shapes.plate_data import plateData
from varda.terrain.shape_scene import (
    ShapeSettings,
    plateColors,
    shapeDisplay,
    shapePageHtml,
)


def sceneOf(html: str) -> dict:
    match = re.search(r"window\.VARDA_SCENE = (.*?);</script>", html, re.S)
    assert match is not None
    return json.loads(match.group(1).replace("<\\/", "</"))


def test_page_embeds_quantized_vertices_and_int32_plates():
    model = icosphere(1.0, 2)
    scene = sceneOf(shapePageHtml(model, ShapeSettings(), {}))
    vertices = np.frombuffer(base64.b64decode(scene["vertices"]), "<u2").reshape(-1, 3)
    plates = np.frombuffer(base64.b64decode(scene["plates"]), "<i4").reshape(-1, 3)
    assert len(vertices) == len(model.vertices)
    assert np.array_equal(plates, model.plates)
    low, step = np.array(scene["low"]), np.array(scene["step"])
    assert np.abs(low + vertices * step - model.vertices).max() <= step.max()


def test_a_model_above_the_cap_is_decimated_and_the_page_says_so():
    model = icosphere(1.0, 4)  # 5120 plates
    html = shapePageHtml(model, ShapeSettings(maxPlates=1000), {})
    scene = sceneOf(html)
    shown = len(base64.b64decode(scene["plates"])) // 12
    assert shown <= 1000
    assert f"Shown with {shown:,} of {model.plateCount:,} plates" in scene["note"]
    assert scene["note"] in html


def test_plate_colors_follow_the_color_map():
    model = icosphere(1.0, 1)
    values = np.linspace(0.0, 1.0, model.plateCount)
    data = plateData(model, "slope", "deg", values)
    display = shapeDisplay(model, 10_000)
    colors, legend = plateColors(
        display, ShapeSettings(colorBy="slope", colorMap="viridis"), {"slope": data}
    )
    cmap = matplotlib.colormaps["viridis"]
    expected = np.round(np.array(cmap(values)[:, :3]) * 255).astype(np.uint8)
    assert np.abs(colors.astype(int) - expected).max() <= 1
    assert legend is not None and legend.low == 0.0 and legend.high == 1.0
    assert legend.label == "slope (deg)"


def test_sigma_fade_grays_uncertain_plates():
    model = icosphere(1.0, 1)
    values = np.full(model.plateCount, 10.0)
    sigma = np.where(np.arange(model.plateCount) % 2 == 0, 1.0, 9.0)
    data = plateData(model, "slope", "deg", values, sigma)
    display = shapeDisplay(model, 10_000)
    settings = ShapeSettings(colorBy="slope", sigmaView="fade", fadeAbove=0.5)
    colors, _ = plateColors(display, settings, {"slope": data})
    sure, unsure = colors[0].astype(int), colors[1].astype(int)
    assert not np.array_equal(sure, unsure)
    assert np.ptp(unsure) < np.ptp(sure)  # faded toward gray


def test_sigma_color_shows_sigma():
    model = icosphere(1.0, 1)
    data = plateData(
        model,
        "slope",
        "deg",
        np.zeros(model.plateCount),
        np.linspace(0, 1, model.plateCount),
    )
    display = shapeDisplay(model, 10_000)
    _, legend = plateColors(
        display, ShapeSettings(colorBy="slope", sigmaView="color"), {"slope": data}
    )
    assert legend is not None and legend.label == "slope sigma (deg)"


def test_uncolored_page_has_one_color():
    model = icosphere(1.0, 1)
    colors, legend = plateColors(shapeDisplay(model, 10_000), ShapeSettings(), {})
    assert legend is None and len(np.unique(colors, axis=0)) == 1


def test_rgb_from_three_plate_arrays():
    model = icosphere(1.0, 1)
    ramp = np.linspace(0, 1, model.plateCount)
    data = {n: plateData(model, n, "", ramp) for n in ("r", "g", "b")}
    colors, legend = plateColors(
        shapeDisplay(model, 10_000), ShapeSettings(rgb=("r", "g", "b")), data
    )
    assert legend is None
    assert (
        (colors[:, 0] == colors[:, 1]).all()
        and colors[0, 0] == 0
        and colors[-1, 0] == 255
    )


def test_exported_page_only_logs_to_the_console():
    html = shapePageHtml(icosphere(1.0, 1), ShapeSettings(), {}, panel=True)
    uses = [m.start() for m in re.finditer("varda:", html)]
    logged = [
        m.start() + len('console.log("')
        for m in re.finditer(r'console\.log\("varda:', html)
    ]
    assert uses and sorted(uses) == sorted(logged)


def test_plate_data_for_another_model_is_refused():
    model = icosphere(1.0, 1)
    other = plateData(icosphere(1.0, 2), "slope", "deg", np.zeros(320))
    with pytest.raises(ValueError, match="another shape model"):
        plateColors(
            shapeDisplay(model, 10_000),
            ShapeSettings(colorBy="slope"),
            {"slope": other},
        )
