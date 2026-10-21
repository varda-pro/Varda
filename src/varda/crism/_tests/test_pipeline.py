"""The four-pass pipeline end to end: on the real crop, on a crop with invalid
regions, on a synthetic observation with known truth (σ), and its chunking,
toggles and cancellation.

The ENVI products are opened with ``ENVIDataSource`` directly: it is what
``openDataSource`` picks for an ``.img`` with a sibling ``.hdr`` (Task 18), and
``image_loading_service`` imports Qt, which ``varda.crism`` may not.
"""

import gc
import json
import re
import shutil
import warnings
from pathlib import Path

import attrs
import numpy as np
import pytest

from varda.crism import pipeline
from varda.crism._tests.synthetic import makeSyntheticCube, writeSyntheticObservation
from varda.crism._tests.test_calibration import copyCdr
from varda.crism.brightness import segmentIds
from varda.crism.calibration import CalibrationLibrary, TransmissionSource, atTable
from varda.crism.labels import readLabel
from varda.crism.observation import CrismObservation
from varda.crism.pipeline import ProcessCancelled, ProcessResult, process
from varda.crism.projection import mtrdrGrid
from varda.crism.readers import DdrBand, readTrr3, usableLines
from varda.crism.recipe import Recipe, Resampling
from varda.crism.smile import linearWeights, resample, sourceSegments, standardVector
from varda.crism.writers import NODATA, sha256File
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource

_AT = "cdr420862848019_at0000000l_7"
_CROP_FILES = (
    "frs00031442_01_if168l_trr3",
    "frs00031442_01_de168l_ddr1",
    "cdr490947778566_wa0000000l_3",
    _AT,
)
# band groups of the χ² test (nm): short of the 1.65 µm join, the join to the
# CO2 window, the CO2 and hydration window, and the long-wave end
_GROUPS_NM = ((1000.0, 1650.0), (1650.0, 1900.0), (1900.0, 2100.0), (2100.0, 2600.0))


def _offline(url: str, target: Path) -> None:
    raise OSError("offline (test)")


def _library(folder: Path, tmp_path: Path) -> CalibrationLibrary:
    return CalibrationLibrary([folder], tmp_path / "cache", fetch=_offline)


def _header(path: Path) -> str:
    return path.with_suffix(".hdr").read_text()


def _number(header: str, key: str) -> int:
    match = re.search(rf"^{key}\s*=\s*(\d+)", header, re.M)
    assert match is not None, key
    return int(match.group(1))


def _list(header: str, key: str) -> list[float]:
    match = re.search(rf"^{key}\s*=\s*\{{(.*?)\}}", header, re.M | re.S)
    assert match is not None, key
    return [float(v) for v in match.group(1).split(",")]


def _raw(path: Path) -> np.ndarray:
    """An ENVI product as stored, ``(lines, samples, bands)`` float32."""
    header = _header(path)
    shape = tuple(_number(header, key) for key in ("bands", "lines", "samples"))
    return np.fromfile(path, dtype="<f4").reshape(shape).transpose(1, 2, 0)


def _read(path: Path) -> np.ndarray:
    """An ENVI product with no-data as NaN, float64."""
    data = _raw(path).astype(np.float64)
    data[data == NODATA] = np.nan
    return data


def _products(result: ProcessResult) -> list[Path]:
    paths = [
        result.mapProjected,
        result.mapSigma,
        result.geometry,
        result.info,
        result.sensorSpace,
    ]
    found = [p for p in paths if p is not None]
    if result.sensorSpace is not None:
        found.append(_sigmaOf(result.sensorSpace))
    return found


def _sigmaOf(path: Path) -> Path:
    return path.with_name(path.stem + "_sigma.img")


def _copyCrop(cropDir: Path, folder: Path) -> CrismObservation:
    folder.mkdir(parents=True)
    for name in _CROP_FILES:
        for suffix in (".lbl", ".img"):
            shutil.copyfile(cropDir / (name + suffix), folder / (name + suffix))
    return CrismObservation.fromFolder(folder)


def _sensorRecipe() -> Recipe:
    recipe = Recipe()
    recipe.output.sensorSpace.set(True)
    return recipe


def test_crop_end_to_end(cropDir: Path, tmp_path: Path) -> None:
    observation = CrismObservation.fromFolder(cropDir)
    reports: list[tuple[float, str]] = []
    out = tmp_path / "out"
    result = process(
        observation,
        Recipe(),
        out,
        library=_library(cropDir, tmp_path),
        progress=lambda fraction, message: reports.append((fraction, message)),
    )
    for path in (
        result.mapProjected,
        result.mapSigma,
        result.geometry,
        result.info,
        result.recipe,
        result.provenance,
        result.diagnostics,
    ):
        assert path is not None and path.exists() and path.parent == out
    assert result.sensorSpace is None  # off by default
    assert result.mapProjected is not None
    assert result.mapProjected.name == "frs00031442_01_if168l_vmtr.img"
    assert result.geometry is not None
    assert result.geometry.name == "frs00031442_01_de168l_vmtr.img"
    assert result.info is not None
    assert result.info.name == "frs00031442_01_in168l_vmtr.img"
    source = ENVIDataSource(str(result.mapProjected))
    assert len(source.wavelengths) == 411
    assert _number(_header(result.mapProjected), "bands") == 411
    assert source.crs is not None
    wkt = source.crs.to_wkt("WKT1_ESRI")
    assert "Equidistant_Cylindrical" in wkt
    assert 'Standard_Parallel_1",15' in wkt
    assert source.extraMetadata["sigma"]["kind"] == "instrument-noise"
    assert _number(_header(result.geometry), "bands") == 14
    assert _number(_header(result.info), "bands") == 9

    provenance = json.loads(result.provenance.read_text())
    trr3 = readTrr3(observation.irCube)
    digest = sha256File(trr3.label.image.dataFile)
    assert digest in result.provenance.read_text()
    assert provenance["inputs"][0]["sha256"] == digest
    scanId = next(row.scanId for row in atTable() if row.name == _AT)
    assert provenance["steps"]["atmosphere"]["chosen"]["scanId"] == scanId
    scores = provenance["steps"]["atmosphere"]["scores"]
    assert list(scores) == [f"{scanId}:AT"]  # the crop's one local AT
    assert 0 < scores[f"{scanId}:AT"] < 0.05
    assert provenance["steps"]["atmosphere"]["continuumDegree"] == 3
    assert set(provenance["wallTimes"]) >= {"passA", "passB", "passC", "passD"}
    calibration = {c["name"]: c for c in provenance["calibration"]}
    assert calibration[_AT]["sha256"] and calibration[_AT]["used"]
    assert calibration[_AT]["required"]
    unused = [c for c in calibration.values() if c["kind"] == "AT" and c["name"] != _AT]
    assert unused and not any(c["used"] or c["required"] for c in unused)
    assert calibration["cdr490947778566_wa0000000l_3"]["used"]
    software = provenance["software"]
    assert software["numpy"] == np.__version__ and "rasterio" in software
    systematic = provenance["systematic"]
    for key in (
        "scanChoice",
        "artifactAmplitude",
        "atmosphereBetaExcess",
        "destripeFrame",
    ):
        assert len(systematic[key]) == 411, key
    # 0 here: the crop's empirical σ_β is not above the formal one (ratio 0.99)
    assert min(systematic["atmosphereBetaExcess"]) >= 0
    assert "gitCommit" in software and "gitDirty" in software

    # progress runs from 0 to 1 without going back, every report has a message
    fractions = [fraction for fraction, _ in reports]
    assert fractions[0] == 0.0 and fractions[-1] == 1.0
    assert all(b >= a for a, b in zip(fractions, fractions[1:]))
    assert all(message for _, message in reports)
    # the σ write goes on from where the I/F write ended, it does not repeat
    projecting = [f for f, m in reports if m.startswith("Pass D: projecting")]
    assert len(projecting) == 2 and projecting[1] > projecting[0]
    assert not list(out.glob(".work-*"))
    assert not list(out.glob("*.part"))


def test_invalid_regions_become_nodata(cropDir: Path, tmp_path: Path) -> None:
    observation = _copyCrop(cropDir, tmp_path / "obs")
    trr3 = readTrr3(observation.irCube)
    layout = readLabel(observation.irCube).image
    stored = np.memmap(
        layout.dataFile,
        dtype=layout.dtype,
        mode="r+",
        offset=layout.offset,
        shape=(layout.lines, layout.bands, layout.samples),
    )
    columns, lines = [40, 41, 42], [5, 6]
    wavelengths = _library(tmp_path / "obs", tmp_path).wavelengths(trr3)
    finite = np.isfinite(wavelengths).all(axis=0)  # not the placeholder band
    middle = np.median(wavelengths[:, finite], axis=0)
    band = int(np.flatnonzero(finite)[np.argmin(np.abs(middle - 2100.0))])
    fileBand = layout.bands - 1 - band if trr3.flipped else band
    stored[:, :, columns] = NODATA
    stored[lines] = NODATA
    stored[:, fileBand] = NODATA
    stored.flush()
    del stored

    result = process(
        observation,
        _sensorRecipe(),
        tmp_path / "out",
        library=_library(tmp_path / "obs", tmp_path),
    )
    for path in _products(result):
        assert np.isfinite(_raw(path)).all(), path.name  # no NaN or inf on disk

    assert result.sensorSpace is not None
    sensor = _raw(result.sensorSpace)
    sensorSigma = _raw(_sigmaOf(result.sensorSpace))
    for values in (sensor, sensorSigma):
        assert (values[:, columns] == NODATA).all()
        assert (values[lines] == NODATA).all()
    # the invalid band empties the output bands that interpolate from it
    target = standardVector().wavelengths
    nearest = int(np.argmin(np.abs(target - wavelengths[60, band])))
    assert (sensor[..., nearest] == NODATA).all()
    bbl = _list(_header(result.sensorSpace), "bbl")
    empty = (sensor == NODATA).all(axis=(0, 1))
    assert all(bbl[i] == 0 for i in np.nonzero(empty)[0])

    assert result.mapProjected is not None and result.mapSigma is not None
    assert result.info is not None
    info = _read(result.info)
    sourceSample, sourceLine = info[..., 0], info[..., 1]
    hit = np.isin(sourceSample, columns) | np.isin(sourceLine, lines)
    assert hit.any()
    for path in (result.mapProjected, result.mapSigma):
        mapped = _raw(path)
        assert (mapped[hit] == NODATA).all()
        assert (mapped[..., nearest] == NODATA).all()
    # IN: the bands derived from the data too; the source position is kept
    assert (_raw(result.info)[hit][:, 2:] == NODATA).all()


def test_bad_pixel_elements_are_kept(cropDir: Path, tmp_path: Path) -> None:
    """Elements flagged in the BP CDRs hold I/F the calibration already
    interpolated along the detector row (DPSIS App. L §2.6), which the MTRDRs use:
    they are counted, not masked (Task 23: masking them left 98% of
    FRS00031442's pixels with a gap in the CO2 window)."""
    observation = _copyCrop(cropDir, tmp_path / "obs")
    trr3 = readTrr3(observation.irCube)
    library = _library(tmp_path / "obs", tmp_path)
    names = [entry.name for entry in library.status(trr3) if entry.kind == "BP"]
    assert names
    for name in names:  # a crop AT as the BP: no zero, so every element flagged
        copyCdr(cropDir, _AT, tmp_path / "obs", name)
    flagged = library.badPixels(trr3)
    assert flagged is not None and flagged.all()

    result = process(observation, _sensorRecipe(), tmp_path / "out", library=library)
    assert result.sensorSpace is not None
    assert np.isfinite(_read(result.sensorSpace)).mean() > 0.5
    mask = json.loads(result.provenance.read_text())["steps"]["mask"]
    assert mask["badPixelInterpolated"] > 0 and "badPixel" not in mask


def test_sigma_chi2_synthetic(tmp_path: Path) -> None:
    """N2, V1: the sensor-space product against the truth resampled to the
    target vector with the same weights, in units of the product's σ.

    The recipe's steps match the physics the synthetic injects: photometry,
    despike (its spikes) and the atmosphere (its transmission, shift and
    elevation law) are on; the synthetic's CO2 artifact is set to 0, since no
    artifact basis exists for its AT; destripe stays off as by default, so
    the truth keeps its stripes (truth = surface · stripes, resampled); and
    the ESC step is off, because the truth has no residual smile error for it
    to remove (on this scene's units it would add one, Task 16).

    σ is the random part only (N2). The atmosphere fit has a systematic error
    on these library surfaces: the shift is +0.17 nm off (ledger A1) and β
    about 1% low, offsetting single CO2 bands by up to 0.82σ on basalt, and
    the 1.9 µm bands of the hydrated and smectite units bias their β further
    (A5). So, measured on seeds 0–3:

    1. about each band's mean residual, on the bland basalt unit: χ²
       0.98–1.01 in every group (1.00 ± 0.05), the offsets bounded;
    2. raw on basalt: 1.04–1.08 in 1900–2100 nm (≤ 1.10), 0.99–1.01 elsewhere
       (1.00 ± 0.05);
    3. raw on every unit outside 1880–2100 nm: 0.995–1.013 (1.00 ± 0.05);
    4. raw on the hydrated and smectite units in 1900–2100 nm: 1.50 on this
       seed, 1.66–1.72 on seeds 1–3; < 1.6 is a regression bound on the known
       A5 systematic for seed 0;
    5. the despike-replaced samples (σ = σ_p; about 300, every one an
       injected spike): 0.92–0.96, held to 1.00 ± 0.25, looser because the χ²
       of 300 unit normals alone scatters by 0.08.

    1–4 leave out the samples whose interpolation taps an injected spike or
    a replaced sample."""
    cube, truth = makeSyntheticCube(lines=64, seed=0, artifactAmplitude=0.0)
    observation = writeSyntheticObservation(tmp_path / "obs", cube, truth)
    recipe = _sensorRecipe()
    recipe.smile.esc.set(False)
    recipe.projection.enabled.set(False)
    result = process(
        observation,
        recipe,
        tmp_path / "out",
        library=_library(tmp_path / "obs", tmp_path),
    )
    assert result.sensorSpace is not None
    product = _read(result.sensorSpace)
    sigma = _read(_sigmaOf(result.sensorSpace))
    with np.load(result.diagnostics) as diagnostics:
        replacedAt = diagnostics["spikePositions"]

    vector = standardVector()
    wavelengths = truth.columnWavelengths
    weights = linearWeights(
        wavelengths,
        vector.wavelengths,
        sourceSegments(wavelengths),
        segmentIds(vector.wavelengths),
    )
    zeros = np.zeros(cube.shape)
    expected, _ = resample(truth.surface * truth.stripes, zeros, weights)

    def tapping(positions: np.ndarray) -> np.ndarray:
        marked = np.zeros(cube.shape)
        marked[tuple(np.asarray(positions).T)] = 1.0
        return resample(marked, zeros, weights)[0] > 0

    replaced = tapping(replacedAt)
    z = (product - expected) / sigma
    good = np.isfinite(z) & (vector.bbl == 1)
    clean = good & ~tapping(truth.spikes) & ~replaced
    basalt = (truth.units == 0)[..., None]
    co2 = (vector.wavelengths >= 1900.0) & (vector.wavelengths < 2100.0)

    def chi2(mask: np.ndarray) -> float:
        assert mask.sum() > 10_000
        return float(np.mean(z[mask] ** 2))

    for low, high in _GROUPS_NM:
        group = (vector.wavelengths >= low) & (vector.wavelengths < high)
        squares, count, offsets = 0.0, 0, []
        for band in np.nonzero(group & (vector.bbl == 1))[0]:
            values = z[..., band][(clean & basalt)[..., band]]
            offsets.append(float(np.mean(values)))
            squares += float(np.sum((values - offsets[-1]) ** 2))
            count += values.size
        assert count > 100_000
        about = squares / count
        assert about == pytest.approx(1.0, abs=0.05), (low, high, about)  # 1
        bound = 1.0 if low == 1900.0 else 0.25  # the systematic, in σ
        assert max(np.abs(offsets)) < bound, (low, high, max(np.abs(offsets)))
        raw = chi2(clean & basalt & group)  # 2
        if low == 1900.0:
            assert raw <= 1.10, raw
        else:
            assert raw == pytest.approx(1.0, abs=0.05), (low, high, raw)
        outside = group & ~(
            (vector.wavelengths >= 1880.0) & (vector.wavelengths < 2100.0)
        )
        if outside.any():
            every = chi2(clean & outside)  # 3
            assert every == pytest.approx(1.0, abs=0.05), (low, high, every)
    hydrated = np.isin(truth.units, (1, 2))[..., None]
    assert chi2(clean & hydrated & co2) < 1.6  # 4
    assert replaced.sum() > 100
    spikes = float(np.mean(z[good & replaced] ** 2))  # 5
    assert spikes == pytest.approx(1.0, abs=0.25), spikes


def test_chunking_invariance(
    cropDir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observation = CrismObservation.fromFolder(cropDir)
    library = _library(cropDir, tmp_path)
    whole = process(observation, _sensorRecipe(), tmp_path / "64", library=library)
    monkeypatch.setattr(pipeline, "CHUNK_LINES", 7)
    chunked = process(observation, _sensorRecipe(), tmp_path / "7", library=library)
    for first, second in zip(_products(whole), _products(chunked), strict=True):
        assert first.name == second.name
        a, b = _raw(first), _raw(second)
        assert a.shape == b.shape
        np.testing.assert_array_equal(a == NODATA, b == NODATA, err_msg=first.name)
        np.testing.assert_allclose(a, b, rtol=1e-6, atol=0, err_msg=first.name)


def test_recipe_toggles(cropDir: Path, tmp_path: Path) -> None:
    observation = CrismObservation.fromFolder(cropDir)
    library = _library(cropDir, tmp_path)
    recipe = _sensorRecipe()
    for step in ("photometry", "despike", "atmosphere", "destripe", "smile"):
        getattr(recipe, step).enabled.set(False)
    recipe.projection.enabled.set(False)
    result = process(observation, recipe, tmp_path / "none", library=library)
    assert result.sensorSpace is not None
    assert result.mapProjected is None and result.info is None

    # the TRR3 resampled only: masked as the pipeline masks, from the bands'
    # nominal (median) wavelengths to the target vector, the same in every column
    trr3, ddr = observation.readIr()
    wavelengths = library.wavelengths(trr3)
    cube = trr3.lines(slice(0, trr3.shape[0]))
    cube[:, ~np.isfinite(wavelengths)] = np.nan
    cube[~usableLines(ddr)] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # the placeholder band
        median = np.nanmedian(wavelengths, axis=0)
    nominal = np.broadcast_to(median, wavelengths.shape)
    vector = standardVector()
    weights = linearWeights(
        nominal,
        vector.wavelengths,
        sourceSegments(wavelengths),
        segmentIds(vector.wavelengths),
    )
    expected, _ = resample(cube, np.zeros(cube.shape), weights)
    product = _read(result.sensorSpace)
    np.testing.assert_array_equal(np.isnan(product), np.isnan(expected))
    np.testing.assert_allclose(product, expected, rtol=1e-6)

    recipe = Recipe()
    recipe.projection.enabled.set(False)
    result = process(observation, recipe, tmp_path / "noMap", library=library)
    assert result.mapProjected is None and result.mapSigma is None
    assert result.geometry is None and result.info is None
    assert result.sensorSpace is not None  # the only image product left
    assert result.provenance.exists()

    # the optional paths: destripe on, area-weighted projection
    recipe = Recipe()
    recipe.destripe.enabled.set(True)
    recipe.projection.resampling.set(Resampling.AREA)
    result = process(observation, recipe, tmp_path / "area", library=library)
    assert result.mapProjected is not None and result.info is not None
    for path in _products(result):
        assert np.isfinite(_raw(path)).all(), path.name
    looks = _read(result.info)[..., 8]
    assert np.nanmin(looks) > 0 and np.nanmax(looks) > 1
    assert np.isfinite(_read(result.mapProjected)).any()
    provenance = json.loads(result.provenance.read_text())
    assert provenance["steps"]["destripe"]["enabled"] is True
    assert "RSC(frame)" in provenance["history"] and "area" in provenance["history"]
    assert "RSC" in provenance["sigma"]["steps"]  # σ/f, as PHT and ESC scale it
    frame = provenance["systematic"]["destripeFrame"]  # the crop has too few
    assert len(frame) == 411 and min(frame) >= 0  # bland lines for a frame
    assert "Mean spike count" in _header(result.info)
    with np.load(result.diagnostics) as diagnostics:
        assert diagnostics["stripeFactors"].shape == (120, 438)


def test_cancel(cropDir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "CHUNK_LINES", 7)
    calls = []

    def cancelled() -> bool:
        calls.append(1)
        return len(calls) > 1  # after the first chunk

    out = tmp_path / "out"
    with pytest.raises(ProcessCancelled):
        process(
            CrismObservation.fromFolder(cropDir),
            _sensorRecipe(),
            out,
            library=_library(cropDir, tmp_path),
            cancelled=cancelled,
        )
    assert len(calls) == 2
    assert list(out.rglob("*")) == []


def test_rerun_replaces_products(cropDir: Path, tmp_path: Path) -> None:
    # Review Focus 4 at the run level: a second run into the folder replaces
    # the products, removes those of the first run it does not write (only
    # Varda's own names) and leaves other files alone
    observation = CrismObservation.fromFolder(cropDir)
    library = _library(cropDir, tmp_path)
    out = tmp_path / "out"
    first = process(observation, _sensorRecipe(), out, library=library)
    assert first.sensorSpace is not None and first.sensorSpace.exists()
    (out / "notes.txt").write_text("mine")
    second = process(observation, Recipe(), out, library=library)
    assert second.sensorSpace is None
    assert not first.sensorSpace.exists()
    assert not _sigmaOf(first.sensorSpace).exists()
    assert not first.sensorSpace.with_suffix(".hdr").exists()
    assert second.mapProjected is not None and second.mapProjected.exists()
    assert (out / "notes.txt").read_text() == "mine"
    assert not list(out.glob(".work-*")) and not list(out.glob("*.old-*"))


def test_work_folder_left_behind_is_reported(
    cropDir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # a work folder that cannot be removed is a warning: the run itself
    # succeeded (or keeps its own error)
    def stuck(path: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError(f"in use: {path}")

    out = tmp_path / "out"
    monkeypatch.setattr(pipeline.shutil, "rmtree", stuck)
    result = process(
        CrismObservation.fromFolder(cropDir),
        Recipe(),
        out,
        library=_library(cropDir, tmp_path),
    )
    assert result.mapProjected is not None and result.mapProjected.exists()
    (work,) = out.glob(".work-*")
    assert str(work) in caplog.text and "WARNING" in caplog.text
    caplog.clear()
    with pytest.raises(ProcessCancelled):  # a failed run keeps its own error
        process(
            CrismObservation.fromFolder(cropDir),
            Recipe(),
            tmp_path / "other",
            library=_library(cropDir, tmp_path),
            cancelled=lambda: True,
        )
    (work,) = (tmp_path / "other").glob(".work-*")
    assert str(work) in caplog.text


def _fileHashes(folder: Path) -> dict[str, str]:
    return {p.name: sha256File(p) for p in folder.iterdir() if p.is_file()}


def test_two_runs_into_one_folder(cropDir: Path, tmp_path: Path) -> None:
    """Final review, Important 2: each run has its own work folder, so a run
    that starts while another runs (here, inside its progress callback) does
    not remove or overwrite the other's work; another run's work folder,
    such as one left by a crash, stays as it was."""
    observation = CrismObservation.fromFolder(cropDir)
    library = _library(cropDir, tmp_path)
    out = tmp_path / "out"
    stale = out / ".work-crashed"
    stale.mkdir(parents=True)
    (stale / "atmosphere.f32").write_bytes(b"left by a crash")
    inner: list[ProcessResult] = []

    def progress(fraction: float, message: str) -> None:
        if message.startswith("Pass C") and not inner:
            inner.append(process(observation, _sensorRecipe(), out, library=library))

    outer = process(observation, Recipe(), out, library=library, progress=progress)
    assert len(inner) == 1 and inner[0].sensorSpace is not None
    assert outer.mapProjected is not None and outer.sensorSpace is None
    assert np.isfinite(_read(outer.mapProjected)).any()
    assert not inner[0].sensorSpace.exists()  # the later publish replaced it
    assert (stale / "atmosphere.f32").read_bytes() == b"left by a crash"
    assert [p.name for p in out.glob(".work-*")] == [stale.name]


@pytest.mark.parametrize("failing", ["backup", "move"])
def test_publish_failure_keeps_previous_product(
    cropDir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    """Final review, Important 3: on Windows a product open in another
    program cannot be replaced. Publishing is a transaction: the earlier
    products are renamed aside first, and any failure restores every one of
    them (each image with its own header) and names the file in use."""
    observation = CrismObservation.fromFolder(cropDir)
    library = _library(cropDir, tmp_path)
    out = tmp_path / "products"
    first = process(observation, _sensorRecipe(), out, library=library)
    assert first.mapProjected is not None
    before = _fileHashes(out)
    replace = pipeline.os.replace
    busy = first.mapProjected.with_suffix(".hdr").name

    def inUse(source: object, target: object) -> None:
        source, target = Path(str(source)), Path(str(target))
        backingUp = source.parent == out and ".old-" in target.name
        movingIn = target.parent == out and source.parent != out
        if target.name == busy and movingIn and failing == "move":
            raise PermissionError(f"in use: {target}")
        if source.name == busy and backingUp and failing == "backup":
            raise PermissionError(f"in use: {source}")
        replace(source, target)

    monkeypatch.setattr(pipeline.os, "replace", inUse)
    with pytest.raises(pipeline.ProductInUseError, match=f"{busy}.*in use; close it"):
        process(observation, Recipe(), out, library=library)
    monkeypatch.setattr(pipeline.os, "replace", replace)
    assert _fileHashes(out) == before
    assert not list(out.glob(".work-*"))


def _mappedWorkFiles(work: Path) -> int:
    """Live arrays (memmaps, and views of them) that keep a file under
    ``work`` mapped: Windows cannot delete those files."""
    count = 0
    for obj in gc.get_objects():
        if not isinstance(obj, np.ndarray):
            continue
        array: object = obj
        while isinstance(array, np.ndarray):
            if isinstance(array, np.memmap) and str(array.filename or "").startswith(
                str(work)
            ):
                count += 1
                break
            array = array.base
    return count


@pytest.mark.parametrize("stage", ["Pass B", "Pass C", "Pass D", "error"])
def test_no_work_file_is_mapped_at_cleanup(
    cropDir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    # finding 4: the frames of a cancel or failure must not keep the work
    # memmaps (or views of them) alive when the work folder is removed
    monkeypatch.setattr(pipeline, "CHUNK_LINES", 7)
    seen: list[int] = []
    removeWork = pipeline._removeWork

    def spy(work: Path, succeeded: bool) -> None:
        seen.append(_mappedWorkFiles(work))
        removeWork(work, succeeded)

    monkeypatch.setattr(pipeline, "_removeWork", spy)
    last = [""]

    def cancelled() -> bool:
        return last[0].startswith(stage)

    if stage == "error":

        def broken(*args: object) -> None:
            raise RuntimeError("forced")

        monkeypatch.setattr(pipeline, "applyEsc", broken)
    expected = RuntimeError if stage == "error" else ProcessCancelled
    with pytest.raises(expected):
        process(
            CrismObservation.fromFolder(cropDir),
            _sensorRecipe(),
            tmp_path / "out",
            library=_library(cropDir, tmp_path),
            progress=lambda fraction, message: last.__setitem__(0, message),
            cancelled=cancelled,
        )
    assert last[0].startswith("Pass") and seen == [0]
    assert not list((tmp_path / "out").glob(".work-*"))


def test_git_commit_only_for_vardas_checkout(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "other"\n')
    assert not pipeline._isVardaCheckout(str(tmp_path))
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "varda"\n')
    assert pipeline._isVardaCheckout(str(tmp_path))
    assert not pipeline._isVardaCheckout(str(tmp_path / "missing"))


def test_slew_lines_stay_off_the_map(tmp_path: Path) -> None:
    """Final review, Important 1: the slew lines ``usableLines`` drops carry
    no I/F, so they must not shape the grid (FRS00031442's 13 made it 756 ×
    903 instead of 702 × 295) or leave a strip of DE and IN without I/F."""
    slew = 6
    cube, truth = makeSyntheticCube(lines=40, seed=2, artifactAmplitude=0.0)
    observation = writeSyntheticObservation(
        tmp_path / "obs", cube, truth, slewLines=slew
    )
    _, ddr = observation.readIr()
    usable = usableLines(ddr)  # a step is to the previous line: one more is out
    assert not usable[: slew + 1].any() and usable[slew + 1 :].all()
    recipe = Recipe()
    for step in ("despike", "atmosphere"):
        getattr(recipe, step).enabled.set(False)
    result = process(
        observation,
        recipe,
        tmp_path / "out",
        library=_library(tmp_path / "obs", tmp_path),
    )
    assert result.mapProjected is not None and result.geometry is not None
    assert result.info is not None
    lat = ddr.band(DdrBand.LATITUDE)[usable]
    lon = ddr.band(DdrBand.LONGITUDE)[usable]
    expected = mtrdrGrid(lat, lon, pipeline.PIXEL_SIZE_M)
    header = _header(result.mapProjected)
    assert (_number(header, "samples"), _number(header, "lines")) == (
        expected.width,
        expected.height,
    )
    hasData = np.isfinite(_read(result.mapProjected)).any(axis=2)
    for product in (result.geometry, result.info):
        np.testing.assert_array_equal(
            np.isfinite(_read(product)).any(axis=2), hasData, err_msg=product.name
        )


def test_mismatched_ddr_and_missing_incidence_are_clear(tmp_path: Path) -> None:
    """A DDR of another size, and a DDR without incidence, stop the run with
    an error that says so (not an index error halfway through)."""
    cube, truth = makeSyntheticCube(lines=24, seed=3)
    first = writeSyntheticObservation(tmp_path / "a", cube, truth)
    shorter, shortTruth = makeSyntheticCube(lines=20, seed=3)
    second = writeSyntheticObservation(tmp_path / "b", shorter, shortTruth)
    mixed = attrs.evolve(first, irDdr=second.irDdr)
    with pytest.raises(ValueError, match=r"24 × 120.*20 × 120"):
        process(
            mixed, Recipe(), tmp_path / "o1", library=_library(tmp_path / "a", tmp_path)
        )

    blind = attrs.evolve(truth, incidenceDeg=np.full(truth.incidenceDeg.shape, np.nan))
    observation = writeSyntheticObservation(tmp_path / "c", cube, blind)
    with pytest.raises(ValueError, match="no valid incidence in the DDR"):
        process(
            observation,
            Recipe(),
            tmp_path / "o2",
            library=_library(tmp_path / "c", tmp_path),
        )


def test_chosen_vs_adr_is_recorded(
    cropDir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final review, Important 5: the provenance names the VS ADR the
    atmosphere used, with its path and SHA-256 (it said "not used")."""
    library = _library(cropDir, tmp_path)
    trr3 = readTrr3(cropDir / (_CROP_FILES[0] + ".lbl"))
    (at,) = library.transmissions(trr3, TransmissionSource.PDS_AT)
    adr = tmp_path / "ADR42086284801_08608_VS00L_8.IMG"
    adr.write_bytes(b"a stand-in for the ADR's image")
    vs = attrs.evolve(
        at,
        sourceName=adr.stem.upper(),
        artifacts=np.zeros((2, *at.values.shape)),
        path=adr,
    )
    monkeypatch.setattr(library, "transmissions", lambda trr3, source: [vs])
    recipe = Recipe()
    recipe.atmosphere.source.set(TransmissionSource.CAT_VS_ADR)
    result = process(
        CrismObservation.fromFolder(cropDir), recipe, tmp_path / "out", library=library
    )
    provenance = json.loads(result.provenance.read_text())
    assert provenance["steps"]["atmosphere"]["chosen"]["key"] == vs.key
    (record,) = [r for r in provenance["calibration"] if r["kind"] == "VS"]
    assert record["name"] == adr.stem.lower()
    assert record["path"] == str(adr) and record["sha256"] == sha256File(adr)
    assert record["used"] is True and record["required"] is True


def test_vs_source_without_cat_aux_folder_says_so(
    cropDir: Path, tmp_path: Path
) -> None:
    recipe = Recipe()
    recipe.atmosphere.source.set(TransmissionSource.CAT_VS_ADR)
    with pytest.raises(ValueError, match="CAT aux folder"):
        process(
            CrismObservation.fromFolder(cropDir),
            recipe,
            tmp_path / "out",
            library=_library(cropDir, tmp_path),
        )
