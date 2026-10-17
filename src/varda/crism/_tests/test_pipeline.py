"""The four-pass pipeline end to end: on the real crop, on a crop with invalid
regions, on a synthetic observation with known truth (σ), and its chunking,
toggles and cancellation.

The ENVI products are opened with ``ENVIDataSource`` directly: it is what
``openDataSource`` picks for an ``.img`` with a sibling ``.hdr`` (Task 18), and
``image_loading_service`` imports Qt, which ``varda.crism`` may not.
"""

import json
import re
import shutil
import warnings
from pathlib import Path

import numpy as np
import pytest

from varda.crism import pipeline
from varda.crism._tests.synthetic import makeSyntheticCube, writeSyntheticObservation
from varda.crism.brightness import segmentIds
from varda.crism.calibration import CalibrationLibrary, atTable
from varda.crism.labels import readLabel
from varda.crism.observation import CrismObservation
from varda.crism.pipeline import ProcessCancelled, ProcessResult, process
from varda.crism.readers import readTrr3, usableLines
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
    assert provenance["steps"]["atmosphere"]["scores"] == {
        f"{scanId}:AT": pytest.approx(
            provenance["steps"]["atmosphere"]["scores"][f"{scanId}:AT"]
        )
    }
    assert set(provenance["wallTimes"]) >= {"passA", "passB", "passC", "passD"}
    assert any(c["name"] == _AT and c["sha256"] for c in provenance["calibration"])

    # progress runs from 0 to 1 without going back, every report has a message
    fractions = [fraction for fraction, _ in reports]
    assert fractions[0] == 0.0 and fractions[-1] == 1.0
    assert all(b >= a for a, b in zip(fractions, fractions[1:]))
    assert all(message for _, message in reports)
    assert not (out / ".work").exists()
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


def test_sigma_chi2_synthetic(tmp_path: Path) -> None:
    """N2, V1: the sensor-space product against the truth resampled to the
    target vector with the same weights, in units of the product's σ, has a
    reduced χ² of 1 per band group.

    The recipe's steps match the physics the synthetic injects: photometry,
    despike (its spikes) and the atmosphere (its transmission, shift and
    elevation law) are on; the synthetic's CO2 artifact is set to 0, since no
    artifact basis exists for its AT; destripe stays off as by default, so
    the truth keeps its stripes (truth = surface · stripes, resampled); and
    the ESC step is off, because the truth has no residual smile error for it
    to remove (on this scene's units it would add one, Task 16). Samples whose
    interpolation taps a spiked sample are left out.

    σ is the random part only (N2), so it is scored where the atmospheric
    model holds, on the bland basalt unit, and against each band's own mean
    residual: what is left in that mean is the fit's systematic error on these
    library surfaces, not noise. Measured on seeds 0–3: the shift fit is
    +0.17 nm off (ledger A1) and β about 1% low, which offsets single CO2
    bands by up to 0.82σ (bounded below), and the raw χ² of the 1900–2100 nm
    group is 1.04–1.08 on basalt; on the hydrated and smectite units, whose
    β the prior only partly protects (ledger A5), it is 1.38–1.53. With those
    offsets removed, χ² is 0.98–1.01 in every group."""
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
    spiked = np.zeros(cube.shape)
    spiked[tuple(truth.spikes.T)] = 1.0
    touched, _ = resample(spiked, zeros, weights)
    z = (product - expected) / sigma
    usable = np.isfinite(z) & ~(touched > 0) & (truth.units == 0)[..., None]
    for low, high in _GROUPS_NM:
        group = (vector.wavelengths >= low) & (vector.wavelengths < high)
        squares, count, offsets = 0.0, 0, []
        for band in np.nonzero(group & (vector.bbl == 1))[0]:
            values = z[..., band][usable[..., band]]
            offsets.append(float(np.mean(values)))
            squares += float(np.sum((values - offsets[-1]) ** 2))
            count += values.size
        assert count > 100_000
        chi2 = squares / count
        assert chi2 == pytest.approx(1.0, abs=0.05), (low, high, chi2)
        bound = 1.0 if low == 1900.0 else 0.25  # the systematic, in σ
        assert max(np.abs(offsets)) < bound, (low, high, max(np.abs(offsets)))


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
    assert not (out / ".work").exists()
    assert list(out.rglob("*")) == []
