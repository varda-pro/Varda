"""``Varda --self-test``: check that a build works before it is released.

Runs the real application, then exercises what a frozen bundle most often
breaks: package metadata, bundled resources, PROJ data, reading and writing
files in background threads, numba-compiled rendering, the analyses that
lean on optional libraries, the certificates the update check needs, and
Kindle a Feature's plugin check (Varda running itself in check mode), and
the 3D view's web engine and three.js page. Prints one line per check and returns the exit
code (0 when everything passed).
"""

import importlib
import shutil
import ssl
import tempfile
import time
import traceback
from collections.abc import Callable
from pathlib import Path

import anthropic
import openai
import certifi
import keyring
import numpy as np
from affine import Affine
from pyproj import Transformer
from PyQt6.QtSvg import QSvgRenderer
from varda.help_info import GUIDE_FOLDER, localGuide
from varda.utilities.resources import resource_path
from varda.terrain.scene import TerrainSettings, buildTerrain, pageHtml
from varda.workspaces.terrain.terrain_view import TerrainView
from PyQt6.QtWidgets import QApplication
from rasterio.crs import CRS

from varda.analysis.band_parameters import BandParametersAnalysis
from varda.analysis.transforms import PcaAnalysis
from varda.app import VardaApplication
from varda.branding.title_mark import ICON_DIR
from varda.catalogs.basemaps import LAYERS, bundledImage
from varda.catalogs.gazetteer import loadGazetteer
from varda.common.entities import VardaRaster
from varda.geo.bodies import identifyBody
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.image_loading_service import ImageLoadingService
from varda.image_loading.raster_writer import writeRaster
from varda.image_rendering.image_renderer import ImageRenderer
from varda.kindling.checks import runChecks
from varda.kindling.docs import EXAMPLES, apiReference
from varda.spectral_libraries.formats import loadEnviLibrary, writeEnviLibrary
from varda.spectral_libraries.store import bundledLibraries
from varda.version import vardaVersion

LOAD_TIMEOUT_S = 60
SHAPE = (16, 16, 60)  # rows, columns, bands
CRS_EPSG = 32612


def runSelfTest(app: VardaApplication) -> int:
    image = _sampleImage()
    with tempfile.TemporaryDirectory() as folder:
        checks: list[tuple[str, Callable[[], str]]] = [
            ("main window", lambda: _mainWindow(app)),
            ("version", _version),
            ("brand assets", _brandAssets),
            ("user guide and menu icons", _guideAndMenuIcons),
            (
                "spectral libraries",
                lambda: _spectralLibraries(Path(folder) / "lib.sli"),
            ),
            ("coordinate systems", _coordinateSystems),
            ("Find Data basemaps and names", _basemaps),
            ("ENVI write and read", lambda: _roundTrip(image, Path(folder) / "a.img")),
            (
                "GeoTIFF write and read",
                lambda: _roundTrip(image, Path(folder) / "a.tif"),
            ),
            ("rendering", lambda: _render(image)),
            ("PCA (SPy)", lambda: _pca(image)),
            ("band parameters (HyPyRameter)", lambda: _bandParameters(image)),
            ("plugin API", _pluginApi),
            ("update check certificates", _updateCertificates),
            ("AI libraries", _aiLibraries),
            ("3D view (WebEngine, three.js)", lambda: _terrainView(image)),
            (
                "plugin check (Kindle a Feature)",
                lambda: _pluginCheck(Path(folder), image),
            ),
        ]
        failures = sum(not _report(name, check) for name, check in checks)
    print(f"Self-test: {len(checks) - failures} of {len(checks)} checks passed")
    return 1 if failures else 0


def _report(name: str, check: Callable[[], str]) -> bool:
    try:
        detail = check()
    except Exception:
        print(f"FAIL {name}\n{traceback.format_exc()}", flush=True)
        return False
    print(f"PASS {name}: {detail}", flush=True)
    return True


def _sampleImage() -> VardaRaster:
    rows, columns, bands = SHAPE
    rng = np.random.default_rng(0)
    data = rng.uniform(0.1, 0.5, SHAPE).astype(np.float32)
    source = ArrayDataSource(
        data,
        wavelengths=np.linspace(400.0, 2500.0, bands),
        wavelengthUnits="Nanometers",
        crs=CRS.from_epsg(CRS_EPSG),
        transform=Affine(30.0, 0.0, 500000.0, 0.0, -30.0, 3500000.0),
    )
    return VardaRaster(dataSource=source, name="self-test")


def _terrainView(image: VardaRaster) -> str:
    """The web engine starts and loads the viewer page with its data (the
    bundled three.js included); whether WebGL then draws depends on the
    machine's graphics, so it's reported, not required."""
    view = TerrainView()
    outcome: dict[str, object] = {}
    view.loadFinished.connect(lambda ok: outcome.setdefault("loaded", ok))
    view.sigReady.connect(lambda: outcome.setdefault("webgl", "drawing"))
    view.sigFailed.connect(
        lambda reason: outcome.setdefault("webgl", f"none ({reason})")
    )
    terrain = buildTerrain(image, band=0, maxSide=32)
    view.showPage(pageHtml(terrain, None, TerrainSettings(), title="self-test"))
    deadline = time.time() + 30
    while time.time() < deadline and not ("loaded" in outcome and "webgl" in outcome):
        QApplication.processEvents()
        time.sleep(0.02)
    assert outcome.get("loaded") is True, "the web engine didn't load the page"
    found: dict[str, object] = {}
    view.evaluate(
        "typeof window.VARDA_SCENE", lambda kind: found.setdefault("scene", kind)
    )
    deadline = time.time() + 10
    while time.time() < deadline and "scene" not in found:
        QApplication.processEvents()
        time.sleep(0.02)
    assert found.get("scene") == "object", "the page has no terrain data"
    TerrainView.releaseAll()  # WebEngine crashes at exit with a page alive
    return f"page loaded; WebGL {outcome.get('webgl', 'not ready in time')}"


def _mainWindow(app: VardaApplication) -> str:
    assert app.maingui is not None and app.maingui.isVisible()
    return app.maingui.windowTitle() or "shown"


def _version() -> str:
    version = vardaVersion()
    assert version != "unknown", "package metadata is missing"
    return version


def _brandAssets() -> str:
    icons = sorted(ICON_DIR.glob("*.svg"))
    assert icons, f"no icons in {ICON_DIR}"
    broken = [icon.name for icon in icons if not QSvgRenderer(str(icon)).isValid()]
    assert not broken, f"unreadable: {broken}"
    return f"{len(icons)} icons"


def _guideAndMenuIcons() -> str:
    """Help > Documentation reads the bundled guide offline, and the menus'
    icons are files (never fetched)."""
    readme = localGuide()
    assert readme is not None, f"no user guide in {GUIDE_FOLDER}"
    pages = sorted(readme.parent.glob("*.md"))
    icons = sorted(Path(resource_path("resources/icons")).glob("*.svg"))
    broken = [icon.name for icon in icons if not QSvgRenderer(str(icon)).isValid()]
    assert icons and not broken, f"menu icons: {len(icons)}, unreadable: {broken}"
    return f"{len(pages)} guide pages, {len(icons)} menu icons"


def _spectralLibraries(path: Path) -> str:
    """The bundled libraries are packaged, and a library writes and reads back."""
    libraries = bundledLibraries()
    sizes = {library.name: len(library.spectra) for library in libraries}
    assert all(sizes.values()) and len(sizes) == 2, f"bundled: {sizes}"
    spectra = libraries[0].spectra[:2]
    loaded = loadEnviLibrary(writeEnviLibrary(path, spectra))
    assert [s.name for s in loaded] == [s.name for s in spectra], "names differ"
    return ", ".join(f"{name} ({size})" for name, size in sizes.items())


def _basemaps() -> str:
    """Every bundled global image decodes, and every body has its names."""
    for layer in LAYERS:
        assert bundledImage(layer).shape[0] >= 512, f"{layer.layerId} is broken"
    bodies = {layer.body for layer in LAYERS}
    counts = {body: len(loadGazetteer(body) or ()) for body in bodies}
    assert all(counts.values()), f"names: {counts}"
    return f"{len(LAYERS)} images, {sum(counts.values())} names"


def _coordinateSystems() -> str:
    toLonLat = Transformer.from_crs(CRS_EPSG, 4326, always_xy=True)
    lon, lat = toLonLat.transform(500000.0, 3500000.0)
    assert np.isfinite([lon, lat]).all()
    # PROJ's IAU_2015 authority is bundled: bodies are found by their codes
    vesta = identifyBody("IAU_2015:200000400")
    assert vesta.body is not None and vesta.body.name == "Vesta", vesta.evidence
    # rasterio's GDAL (not only pyproj) must see IAU_2015 too: an old PROJ_DATA
    # from another installation fails here
    assert CRS.from_user_input("IAU_2015:49900").to_wkt()
    return f"EPSG:{CRS_EPSG} -> ({lon:.3f}, {lat:.3f}), {vesta.evidence} is Vesta"


def _roundTrip(image: VardaRaster, path: Path) -> str:
    written = writeRaster(image, path)
    loaded = _load(written)
    try:
        assert loaded.shape == image.shape, f"{loaded.shape} != {image.shape}"
        np.testing.assert_allclose(loaded.wavelengths, image.wavelengths, rtol=1e-4)
        assert loaded.crs is not None and loaded.crs.to_epsg() == CRS_EPSG
        return f"{written.name} {loaded.shape}"
    finally:
        loaded.close()  # Windows can't delete a file that is still open


def _load(path: Path) -> VardaRaster:
    """Load through the app's background loader, as File > Import does."""
    results: list[VardaRaster | str] = []
    ImageLoadingService.load_image_data(str(path), results.append, results.append)
    deadline = time.monotonic() + LOAD_TIMEOUT_S
    while not results and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert results, f"timed out loading {path.name}"
    result = results[0]
    assert isinstance(result, VardaRaster), result
    return result


def _render(image: VardaRaster) -> str:
    rgba = ImageRenderer(image).render()
    assert rgba.shape == (*SHAPE[:2], 4), rgba.shape
    return f"{rgba.shape}"


def _pca(image: VardaRaster) -> str:
    result = PcaAnalysis().run(image, lambda _percent, _message: None)
    assert result.bandCount > 0
    return f"{result.bandCount} components"


def _pluginApi() -> str:
    """Plugins import varda.api, which nothing in Varda imports itself, so a
    bundle can silently leave it out."""
    api = importlib.import_module("varda.api")
    missing = [name for name in api.__all__ if not hasattr(api, name)]
    assert not missing, f"varda.api lacks {missing}"
    return f"API {api.API_VERSION}, {len(api.__all__)} names"


def _updateCertificates() -> str:
    """The HTTPS certificates the update check uses are bundled. Offline: the
    self-test never contacts GitHub."""
    context = ssl.create_default_context(cafile=certifi.where())
    count = len(context.get_ca_certs())
    assert count, f"no certificates in {certifi.where()}"
    return f"{count} certificates"


def _aiLibraries() -> str:
    """The Anthropic and OpenAI SDKs import and keyring has a backend (on
    Linux without a keychain service that's keyring's fail backend, which is
    reported)."""
    return (
        f"anthropic {anthropic.__version__}, openai {openai.__version__}, "
        f"keyring {type(keyring.get_keyring()).__module__}"
    )


def _pluginCheck(folder: Path, image: VardaRaster) -> str:
    """Check the bundled example plugin the way Kindle a Feature checks a new
    one: in a separate Varda process, on a crop of an image."""
    assert "class Analysis" in apiReference()
    plugin = folder / "band-depth"
    shutil.copytree(EXAMPLES / "band-depth", plugin)
    report = runChecks(plugin, image)
    assert report.passed, report.describe()
    runs = report.analyses[0].runs
    return f"{report.analyses[0].name}: {', '.join(run.image for run in runs)}"


def _bandParameters(image: VardaRaster) -> str:
    analysis = BandParametersAnalysis()
    assert analysis.isAvailable(), "HyPyRameter is not installed"
    analysis.prepareFor(image)
    choices = analysis.parameters.choices
    assert choices, "no parameters for 400-2500 nm"
    analysis.parameters.set(choices[:2])
    result = analysis.run(image, lambda _percent, _message: None)
    return f"{', '.join(result.bandNames)}"
