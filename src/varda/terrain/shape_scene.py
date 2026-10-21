"""A shape model as a self-contained three.js page, like the DEM page
(``terrain.scene.pageHtml``): the same template and import map, its own
viewer (``resources/terrain/shape_viewer.js``). No Qt.

The page holds the model's vertices quantized to 16 bits per axis over its
bounding box (Bennu's 0.5 km: 8 mm steps) and its plates as int32, and one
color per plate. Models above ``ShapeSettings.maxPlates`` are shown
decimated (``mesh.decimated``) and the page says so; each shown plate takes
the color of the full model's plate nearest its centroid. A click reports
the plate and the view ray (``varda:pick``), so Varda reads the full model
where the ray meets it, never the decimated one.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Literal

import matplotlib
import numpy as np
from attrs import asdict, frozen

from varda.shapes.mesh import ShapeModel, decimated
from varda.shapes.plate_data import PlateData, attach
from varda.shapes.raycast import RayCaster
from varda.terrain.scene import THREE_FILES, Legend, _dataUrl
from varda.utilities.resources import resource_path

DEFAULT_MAX_PLATES = 512_000
# Exported web pages: browsers on ordinary laptops stay responsive
WEB_EXPORT_PLATES = 200_000
SURFACE_GRAY = (200, 196, 188)
NO_DATA_GRAY = (110, 110, 110)
LEGEND_STOPS = 32
_PERCENTILES = (2.0, 98.0)  # the color range when none is set


@frozen
class ShapeSettings:
    """How the shape page shows the model. ``colorBy`` names a plate
    dataset (``rgb`` three, for red, green and blue); ``sigmaView`` shows
    the dataset's sigma as the color, or fades plates whose sigma/|value|
    exceeds ``fadeAbove``; ``light`` follows the camera or the sun
    (``sunBodyFrame``, a direction in body-fixed coordinates)."""

    colorBy: str | None = None
    colorMap: str = "viridis"
    rgb: tuple[str, str, str] | None = None
    sigmaView: Literal["off", "color", "fade"] = "off"
    fadeAbove: float = 0.5
    light: Literal["headlight", "sun"] = "headlight"
    sunBodyFrame: tuple[float, float, float] = (1.0, 0.0, 0.0)
    maxPlates: int = DEFAULT_MAX_PLATES
    valueRange: tuple[float, float] | None = None  # the color map's ends

    def viewSettings(self) -> dict:
        """The settings the page applies itself (``window.viewer.set``)."""
        return {"light": self.light, "sun": list(self.sunBodyFrame)}


@frozen(eq=False)
class ShapeDisplay:
    """The model as shown: itself, or decimated to the cap, and for each
    shown plate the model's plate it takes its color from."""

    model: ShapeModel  # the full model
    shown: ShapeModel
    sourcePlate: np.ndarray  # (shown plates,) int

    @property
    def isDecimated(self) -> bool:
        return self.shown is not self.model

    @property
    def note(self) -> str:
        if not self.isDecimated:
            return f"{self.model.plateCount:,} plates"
        return (
            f"Shown with {self.shown.plateCount:,} of {self.model.plateCount:,} "
            "plates (decimated for display; readouts use every plate)"
        )


def shapeDisplay(model: ShapeModel, maxPlates: int) -> ShapeDisplay:
    """The model ready to show at most ``maxPlates`` plates (slow for large
    models: run it in the background)."""
    shown = decimated(model, maxPlates)
    if shown is model:
        return ShapeDisplay(model, model, np.arange(model.plateCount))
    source = RayCaster(model).closestPoint(shown.centroids).plate
    return ShapeDisplay(model, shown, source.astype(np.int64))


def plateColors(
    display: ShapeDisplay, settings: ShapeSettings, plateData: dict[str, PlateData]
) -> tuple[np.ndarray, Legend | None]:
    """(shown plates, 3) uint8 colors and the color bar, if any. Raises
    ValueError for data made for another model."""
    count = display.shown.plateCount
    if settings.rgb is not None:
        channels = []
        for name in settings.rgb:
            data = plateData[name]
            attach(display.model, data)
            channels.append(_scaled(data.values[display.sourcePlate], None))
        rgb = np.column_stack(channels)
        colors = np.where(np.isfinite(rgb), np.round(np.nan_to_num(rgb) * 255), np.nan)
        out = np.where(
            np.isnan(colors).any(axis=1, keepdims=True), NO_DATA_GRAY, colors
        )
        return out.astype(np.uint8), None
    if settings.colorBy is None:
        return np.tile(np.array(SURFACE_GRAY, np.uint8), (count, 1)), None
    data = plateData[settings.colorBy]
    attach(display.model, data)
    values = data.values[display.sourcePlate]
    sigma = None if data.sigma is None else data.sigma[display.sourcePlate]
    label = f"{data.name} ({data.unit})" if data.unit else data.name
    if settings.sigmaView == "color" and sigma is not None:
        values = sigma
        label = (
            f"{data.name} sigma ({data.unit})" if data.unit else f"{data.name} sigma"
        )
    low, high = settings.valueRange or _range(values)
    cmap = matplotlib.colormaps[settings.colorMap]
    scaled = _scaled(values, (low, high))
    rgb = np.asarray(cmap(np.nan_to_num(scaled))[:, :3]) * 255
    if settings.sigmaView == "fade" and sigma is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            unsure = (
                sigma / np.abs(data.values[display.sourcePlate]) > settings.fadeAbove
            )
        gray = rgb.mean(axis=1, keepdims=True)
        rgb = np.where(unsure[:, None], 0.25 * rgb + 0.75 * gray, rgb)
    rgb = np.where(np.isfinite(scaled)[:, None], rgb, NO_DATA_GRAY)
    stops = np.asarray(cmap(np.linspace(0, 1, LEGEND_STOPS))[:, :3]) * 255
    legend = Legend(
        tuple("#%02x%02x%02x" % tuple(int(round(c)) for c in stop) for stop in stops),
        float(low),
        float(high),
        label,
    )
    return np.round(rgb).astype(np.uint8), legend


def _range(values: np.ndarray) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0, 1.0
    if finite.size <= 100:
        return float(finite.min()), float(finite.max())
    low, high = np.percentile(finite, _PERCENTILES)
    return float(low), float(high)


def _scaled(values: np.ndarray, bounds: tuple[float, float] | None) -> np.ndarray:
    """``values`` mapped to 0–1 between ``bounds`` (their own range when
    None), clipped; NaN stays NaN."""
    low, high = bounds or _range(values)
    span = high - low if high > low else 1.0
    with np.errstate(invalid="ignore"):
        return np.clip((values - low) / span, 0.0, 1.0)


def colorPayload(colors: np.ndarray, legend: Legend | None) -> dict:
    """What ``window.viewer.setColors`` takes."""
    return {
        "colors": base64.b64encode(
            np.ascontiguousarray(colors, np.uint8).tobytes()
        ).decode(),
        "legend": None if legend is None else asdict(legend),
    }


def shapePageHtml(
    model: ShapeModel,
    settings: ShapeSettings,
    plateData: dict[str, PlateData],
    camera: dict | None = None,
    panel: bool = False,
    display: ShapeDisplay | None = None,
    title: str | None = None,
) -> str:
    """The shape page: self-contained, so the same file works in Varda and
    in any browser; ``panel`` adds a light switch and a home button for
    readers of an exported page. ``display`` reuses a ``shapeDisplay``."""
    folder = Path(resource_path("resources/terrain"))
    if display is None or display.shown.plateCount > settings.maxPlates:
        display = shapeDisplay(model, settings.maxPlates)
    shown = display.shown
    low = shown.vertices.min(axis=0)
    step = np.maximum(shown.vertices.max(axis=0) - low, 1e-12) / 65535.0
    quantized = np.round((shown.vertices - low) / step).astype("<u2")
    colors, legend = plateColors(display, settings, plateData)
    title = title or (model.name or "Shape model")
    data = {
        "name": model.name,
        "note": display.note,
        "low": low.tolist(),
        "step": step.tolist(),
        "vertices": base64.b64encode(quantized.tobytes()).decode(),
        "plates": base64.b64encode(shown.plates.astype("<i4").tobytes()).decode(),
        "settings": settings.viewSettings(),
        "camera": camera,
        "radiusKm": float(np.linalg.norm(shown.vertices, axis=1).max()),
    } | colorPayload(colors, legend)
    importmap = {"imports": {k: _dataUrl(folder / f) for k, f in THREE_FILES.items()}}
    safe = _escaped(title)
    controls = (
        SHAPE_PANEL.format(title=safe, note=_escaped(display.note)) if panel else ""
    )
    template = (folder / "viewer.html").read_text()
    return (
        template.replace("{{TITLE}}", f"{safe} · {_escaped(display.note)}")
        .replace("{{IMPORTMAP}}", json.dumps(importmap))
        .replace("{{PANEL}}", controls)
        .replace("{{SCENE}}", json.dumps(data).replace("</", "<\\/"))
        .replace("{{VIEWER}}", (folder / "shape_viewer.js").read_text())
    )


def _escaped(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


SHAPE_PANEL = """<div id="panel">
<h1>{title}</h1>
<label>Light from the sun</label><input type="checkbox" data-setting="sunLight">
<p class="note">{note}</p>
<button id="home">Reset view</button>
</div>
<div id="credit">Made with <a href="https://github.com/varda-pro/Varda">Varda</a></div>"""
