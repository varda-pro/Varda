"""A deterministic synthetic CRISM IR cube with known truth (spec V1), the
shared test helper of every correction step.

The cube is ``(lines, 120, 438)``: the 120 columns and ascending-wavelength
band order of the test crops, with the all-NaN placeholder band (~65535 nm) at
the end. Its per-column wavelengths come from the crop WA and its
transmission from the crop AT. The observed cube is

    R(t, x, λ) = S(t, x, λ) · exp(β(t, x) · ln T_x(λ + δ_x))
                 · exp(β(t, x) · a(x, λ)) · f(x, λ) + n,

then spikes are added, where

- S is the surface I/F *after photometry*: the cube carries no cos i factor.
  Its USGS library spectra are smoothed by a one-band Gaussian in ln, which
  removes their lab band-to-band roughness;
  ``incidenceDeg`` is the incidence a DDR would report; a writer of raw TRR3
  files multiplies the cube (and σ) by cos i;
- ln T_x(λ + δ) is interpolated along column x's own wavelengths; δ_x is a
  smooth function of the column;
- β = exp(−(z − z₀) / H + η), with z the elevation (a smooth field of
  ±1.5 km plus small scatter, z₀ its base), H the scale height and η an
  optional departure of ln β from the elevation law (``betaScatter``, its
  std; 0 by default: β follows z exactly). η is spatially coherent, white
  noise smoothed by a Gaussian of 4 pixels, as aerosol and weather vary
  over many pixels; it is drawn from its own generator, so the rest of the
  cube does not change with it;
- a is the CO2 artifact: the curve-of-growth term (ln T)², high-passed over
  the fit window (orthogonal to a degree-3 Legendre continuum there, zero
  outside 1850–2200 nm), scaled per column to an RMS of
  ``artifactAmplitude`` over the score window 1947–2066 nm. Its amplitude is
  path-scaled (α = β), as CAT scales its patch. It depends only on the AT, so
  every seed carries the same artifact;
- f are multiplicative stripes: high-frequency (independent per column and
  band, RMS ``stripeAmplitude``) times a smooth part (a per-column offset and
  tilt in λ, RMS ``stripeAmplitude / 2`` each);
- n is Gaussian noise with σ² = a + b·R (R the noise-free signal), SNR
  ``snr`` at R = 0.1, a fifth of the variance there being read noise;
- spikes of 5–20 σ, either sign, at ``spikeRate`` per valid sample.
"""

import math
import re
import shutil
from functools import cache
from pathlib import Path

import numpy as np
from attrs import frozen
from numpy.polynomial import legendre
from scipy import ndimage

from varda.analysis.spectral_library import LibrarySpectrum, loadUsgsLibrary, resampleTo
from varda.crism.labels import ArrayLayout, readLabel
from varda.crism.observation import CrismObservation
from varda.crism.readers import DdrBand, readDetectorFrame, readTrr3

_DATA = Path(__file__).parent / "data"
_TRR3 = _DATA / "frs00031442_01_if168l_trr3.lbl"
_DDR = _DATA / "frs00031442_01_de168l_ddr1.lbl"
_WA = _DATA / "cdr490947778566_wa0000000l_3.lbl"
_AT = _DATA / "cdr420862848019_at0000000l_7.lbl"

FIT_WINDOW_NM = (1850.0, 2200.0)
SCORE_WINDOW_NM = (1947.0, 2066.0)
BASE_ELEVATION_M = -2500.0
ELEVATION_RANGE_M = 1500.0
ELEVATION_SCATTER_M = 20.0
INCIDENCE_RANGE_DEG = (50.0, 55.0)
READ_NOISE_FRACTION = 0.2  # of the variance at I/F 0.1
SPIKE_SIGMAS = (5.0, 20.0)
ALBEDO_VARIATION = 0.05
_BETA_SCATTER_PIXELS = 4.0  # σ of the Gaussian that makes η coherent
_SMOOTH_BANDS = 1.0  # σ of the library-spectrum smoothing, in bands

# The units: (library spectrum, fraction mixed with basalt, I/F at 1300–1700 nm)
_BASALT = "BasaltFresh_BR93-46B_BECKb_AREF"
_UNITS = (
    (_BASALT, 1.0, 0.10),  # 0: the bland background
    ("Opal_WS732_BECKa_AREF", 0.5, 0.16),  # 1: a 1.9 µm hydrated phase
    ("Nontronite_NG-1.a_BECKb_AREF", 0.5, 0.14),  # 2: an Fe/Mg smectite
    ("Magnesite_Hydromag_HS47.3B_BECKa_AREF", 0.5, 0.18),  # 3: carbonate
)
HYDRATED_UNIT = 1  # the opal mixture: a strong 1.9 µm band
_COLUMN_UNIT = 3  # spans every line, a few columns wide
_COLUMN_UNIT_WIDTH = 6
_BLOBS_PER_UNIT = 2


@frozen(eq=False)
class SyntheticTruth:
    """Everything that made the cube; ``(l, s, b)`` per sample, ``(s, b)`` per
    column and band, ``(l, s)`` per pixel."""

    surface: np.ndarray  # (l, s, b) I/F after photometry, before atmosphere
    beta: np.ndarray  # (l, s)
    shiftNm: np.ndarray  # (s,) δ_x
    stripes: np.ndarray  # (s, b) multiplicative
    spikes: np.ndarray  # (n, 3) int: (line, sample, band)
    spikeSigmas: np.ndarray  # (n,) signed amplitude in units of noiseSigma
    noiseSigma: np.ndarray  # (l, s, b)
    incidenceDeg: np.ndarray  # (l, s)
    elevationM: np.ndarray  # (l, s)
    columnWavelengths: np.ndarray  # (s, b) nm, NaN for the placeholder band
    transmission: np.ndarray  # (s, b) T_x(λ) at the column's own wavelengths
    artifact: np.ndarray  # (s, b) a(x, λ), high-pass, in ln R
    columnUnitMask: np.ndarray  # (l, s) the column-aligned unit
    units: (
        np.ndarray
    )  # (l, s) int: 0 basalt, 1 hydrated (1.9 µm), 2 smectite, 3 carbonate


@cache
def _detector() -> tuple[np.ndarray, np.ndarray]:
    """The crop's per-column wavelengths and ln T, ascending wavelength; ln T's
    invalid samples are interpolated along the column."""
    rows = readTrr3(_TRR3).detectorRows
    wavelengths = readDetectorFrame(_WA).alignedTo(rows)
    transmission = readDetectorFrame(_AT).alignedTo(rows)
    logT = np.full(wavelengths.shape, np.nan)
    for x in range(wavelengths.shape[0]):
        bands = np.isfinite(wavelengths[x])
        known = bands & np.isfinite(transmission[x])
        logT[x, bands] = np.interp(
            wavelengths[x, bands],
            wavelengths[x, known],
            np.log(transmission[x, known]),
        )
    return wavelengths, logT


@cache
def _library() -> dict[str, LibrarySpectrum]:
    return {spectrum.name: spectrum for spectrum in loadUsgsLibrary()}


def _libraryAt(spectrum: LibrarySpectrum, wavelengths: np.ndarray) -> np.ndarray:
    """The spectrum at ``wavelengths``, held at its end values beyond its range."""
    values = resampleTo(spectrum, wavelengths.ravel()).reshape(wavelengths.shape)
    keep = np.isfinite(spectrum.wavelengths) & np.isfinite(spectrum.reflectance)
    x, y = spectrum.wavelengths[keep], spectrum.reflectance[keep]
    order = np.argsort(x)
    x, y = x[order], y[order]
    values = np.where(wavelengths < x[0], y[0], values)
    values = np.where(wavelengths > x[-1], y[-1], values)
    return _smoothBands(values)


def _smoothBands(values: np.ndarray) -> np.ndarray:
    """A Gaussian of one band (σ) along the band axis, in ln and NaN-aware.

    The lab spectra carry band-to-band roughness (a second-difference RMS of
    0.013 in ln for the basalt) that no Mars surface at CRISM resolution
    shows and that a wavelength-shift fit would read as a shift of about
    0.2 nm."""
    finite = np.isfinite(values) & (values > 0)
    logV = np.log(np.where(finite, values, 1.0))
    weight = ndimage.gaussian_filter1d(
        finite.astype(float), _SMOOTH_BANDS, axis=-1, mode="constant"
    )
    smooth = ndimage.gaussian_filter1d(
        np.where(finite, logV, 0.0), _SMOOTH_BANDS, axis=-1, mode="constant"
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(finite, np.exp(smooth / weight), values)


def _unitSpectra(wavelengths: np.ndarray) -> np.ndarray:
    """``(unit, s, b)``: each unit's I/F at every column's wavelengths."""
    library = _library()
    basalt = _libraryAt(library[_BASALT], wavelengths)
    window = (wavelengths >= 1300) & (wavelengths <= 1700)
    spectra = []
    for name, fraction, brightness in _UNITS:
        mixed = (
            fraction * _libraryAt(library[name], wavelengths) + (1 - fraction) * basalt
        )
        level = np.nanmean(np.where(window, mixed, np.nan), axis=1, keepdims=True)
        spectra.append(brightness * mixed / level)
    return np.array(spectra)


def _artifact(wavelengths: np.ndarray, logT: np.ndarray, rms: float) -> np.ndarray:
    """``(s, b)``: (ln T)² high-passed over the fit window, RMS ``rms`` over
    the score window, zero elsewhere."""
    artifact = np.zeros(wavelengths.shape)
    for x in range(wavelengths.shape[0]):
        inside = (wavelengths[x] >= FIT_WINDOW_NM[0]) & (
            wavelengths[x] <= FIT_WINDOW_NM[1]
        )
        u = np.interp(wavelengths[x, inside], FIT_WINDOW_NM, (-1.0, 1.0))
        q = logT[x, inside] ** 2
        high = q - legendre.legval(u, legendre.legfit(u, q, 3))
        score = (wavelengths[x, inside] >= SCORE_WINDOW_NM[0]) & (
            wavelengths[x, inside] <= SCORE_WINDOW_NM[1]
        )
        artifact[x, inside] = rms * high / np.sqrt(np.mean(high[score] ** 2))
    return artifact


def _smoothField(rng: np.random.Generator, lines: int, samples: int) -> np.ndarray:
    """``(l, s)``: a few long waves with random phases, scaled to [−1, 1]."""
    t = np.arange(lines)[:, None] / max(lines - 1, 1)
    x = np.arange(samples)[None, :] / (samples - 1)
    phases = rng.uniform(0, 2 * np.pi, 3)
    field = (
        0.5 * np.sin(2 * np.pi * 0.8 * t + phases[0])
        + 0.3 * np.sin(2 * np.pi * 0.6 * x + phases[1])
        + 0.2 * np.sin(2 * np.pi * (0.5 * t + 0.4 * x) + phases[2])
    )
    return field / np.abs(field).max()


def _units(rng: np.random.Generator, lines: int, samples: int) -> np.ndarray:
    """``(l, s)`` unit indices: basalt, elliptical blobs of units 1 and 2, and
    the column-aligned unit last, so it spans every line."""
    t, x = np.mgrid[0:lines, 0:samples]
    units = np.zeros((lines, samples), dtype=np.int64)
    for unit in (1, 2):
        for _ in range(_BLOBS_PER_UNIT):
            ct, cx = rng.uniform(0, lines), rng.uniform(0, samples)
            rt, rx = rng.uniform(5, 14), rng.uniform(5, 14)
            units[((t - ct) / rt) ** 2 + ((x - cx) / rx) ** 2 <= 1] = unit
    first = int(rng.integers(15, samples - 15 - _COLUMN_UNIT_WIDTH))
    units[:, first : first + _COLUMN_UNIT_WIDTH] = _COLUMN_UNIT
    return units


def makeSyntheticCube(
    lines: int = 64,
    seed: int = 0,
    *,
    snr: float = 100.0,
    shiftAmplitudeNm: float = 0.8,
    scaleHeightKm: float = 11.0,
    stripeAmplitude: float = 0.005,
    spikeRate: float = 1e-4,
    artifactAmplitude: float = 0.006,
    betaScatter: float = 0.0,
    textureAmplitude: float = 0.0,
) -> tuple[np.ndarray, SyntheticTruth]:
    """The observed cube ``(lines, 120, 438)`` (float64, NaN only in the
    placeholder band) and its truth; the module docstring gives the model.
    ``textureAmplitude`` (opt-in) multiplies the signal by a spatially white,
    achromatic factor 1 + N(0, amplitude) per pixel, the albedo texture that a
    noise estimate must not mistake for noise.
    The same arguments always give the same arrays."""
    rng = np.random.default_rng(seed)
    wavelengths, logT = _detector()
    samples, bands = wavelengths.shape
    valid = np.isfinite(wavelengths)

    # surface
    units = _units(rng, lines, samples)
    albedo = 1.0 + ALBEDO_VARIATION * _smoothField(rng, lines, samples)
    spectra = _unitSpectra(wavelengths)  # (unit, s, b)
    surface = spectra[units, np.arange(samples)[None, :]] * albedo[..., None]

    # atmosphere: shifted transmission, β from elevation, the artifact
    u = np.arange(samples) / (samples - 1)
    shift = shiftAmplitudeNm * np.sin(np.pi * u + rng.uniform(0, 2 * np.pi))
    shiftedLogT = np.full(wavelengths.shape, np.nan)
    for x in range(samples):
        w = wavelengths[x, valid[x]]
        shiftedLogT[x, valid[x]] = np.interp(w + shift[x], w, logT[x, valid[x]])
    elevation = (
        BASE_ELEVATION_M
        + ELEVATION_RANGE_M * _smoothField(rng, lines, samples)
        + rng.normal(0.0, ELEVATION_SCATTER_M, (lines, samples))
    )
    beta = np.exp(-(elevation - BASE_ELEVATION_M) / (scaleHeightKm * 1000.0))
    if betaScatter > 0:
        white = np.random.default_rng((seed, 1)).normal(size=beta.shape)
        departure = ndimage.gaussian_filter(white, _BETA_SCATTER_PIXELS)
        beta = beta * np.exp(betaScatter * departure / departure.std())
    artifact = _artifact(wavelengths, logT, artifactAmplitude)

    # stripes
    tilt = np.interp(
        wavelengths, (wavelengths[valid].min(), wavelengths[valid].max()), (-1, 1)
    )
    smooth = (
        rng.normal(0.0, stripeAmplitude / 2, (samples, 1))
        + rng.normal(0.0, stripeAmplitude / 2, (samples, 1)) * tilt
    )
    stripes = np.exp(rng.normal(0.0, stripeAmplitude, (samples, bands)) + smooth)
    stripes[~valid] = 1.0

    # signal, noise and spikes
    signal = surface * np.exp(beta[..., None] * (shiftedLogT + artifact)) * stripes
    if textureAmplitude > 0:  # its own stream: the default cubes stay as they were
        texture = 1 + np.random.default_rng([seed, 1]).normal(
            0.0, textureAmplitude, (lines, samples)
        )
        signal = signal * texture[..., None]
    sigma0 = 0.1 / snr
    readVariance = READ_NOISE_FRACTION * sigma0**2
    shotVariance = (1 - READ_NOISE_FRACTION) * sigma0**2 / 0.1
    noiseSigma = np.sqrt(readVariance + shotVariance * np.maximum(signal, 0.0))
    cube = signal + rng.standard_normal(signal.shape) * noiseSigma

    validBands = np.nonzero(valid.all(axis=0))[0]
    population = lines * samples * validBands.size
    count = min(int(rng.poisson(spikeRate * population)), population)
    flat = np.sort(rng.choice(population, count, replace=False))
    t, x, b = np.unravel_index(flat, (lines, samples, validBands.size))
    spikes = np.column_stack([t, x, validBands[b]]).astype(np.int64)
    spikeSigmas = rng.uniform(*SPIKE_SIGMAS, count) * rng.choice((-1.0, 1.0), count)
    cube[t, x, validBands[b]] += spikeSigmas * noiseSigma[t, x, validBands[b]]

    # incidence: a smooth polynomial over 50–55°
    tt = np.arange(lines)[:, None] / max(lines - 1, 1)
    low, high = INCIDENCE_RANGE_DEG
    incidence = low + (high - low) * (0.6 * tt + 0.4 * u[None, :] ** 2)

    return cube, SyntheticTruth(
        surface=surface,
        beta=beta,
        shiftNm=shift,
        stripes=stripes,
        spikes=spikes,
        spikeSigmas=spikeSigmas,
        noiseSigma=noiseSigma,
        incidenceDeg=incidence,
        elevationM=elevation,
        columnWavelengths=wavelengths.copy(),
        transmission=np.exp(logT),
        artifact=artifact,
        columnUnitMask=units == _COLUMN_UNIT,
        units=units,
    )


# --- the observation as files (Task 20) ---------------------------------------------

# A Jezero-like scene: pixel centers about 18 m apart, the swath turned a little
SCENE_CENTER_DEG = (18.3, 77.4)  # (latitude N, longitude E)
SCENE_PIXEL_M = 18.0
SCENE_ROTATION_DEG = 8.0
_MARS_RADIUS_M = 3396190.0
_SLEW_STEP = 10.0  # line spacings between slew lines (usableLines flags > 4)
_NODATA = 65535.0
_LABEL_NOTE = "/* Varda synthetic observation, written by writeSyntheticObservation */"
# DDR backplanes the synthetic does not model: constants
_DDR_CONSTANTS = {
    DdrBand.EMISSION_AREOID: 5.0,
    DdrBand.PHASE: 55.0,
    DdrBand.EMISSION_MOLA: 5.0,
    DdrBand.SLOPE: 0.0,
    DdrBand.SLOPE_AZIMUTH: 0.0,
    DdrBand.THERMAL_INERTIA: 350.0,
    DdrBand.ALBEDO: 0.2,
    DdrBand.LOCAL_SOLAR_TIME: 15.0,
    DdrBand.SPARE: _NODATA,
}


def _sub(pattern: str, replacement: str, text: str) -> str:
    """Replace the first match of ``pattern``; a miss is a bug in the template."""
    result, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    assert count == 1, f"label anchor not found: {pattern}"
    return result


def _relabel(label: Path, lines: int, records: int, rownumRecord: int | None) -> str:
    """The crop's label with its line count, file records and row-number table
    pointer (1-based record) rewritten; the first FILE object is the image's."""
    text = label.read_text()
    text = _sub(r"^/\* Varda test crop:.*\*/$", _LABEL_NOTE, text)
    text = _sub(r"^(\s*LINES\s*=\s*)\d+", rf"\g<1>{lines}", text)
    text = _sub(r"^(\s*FILE_RECORDS\s*=\s*)\d+", rf"\g<1>{records}", text)
    if rownumRecord is not None:
        text = _sub(
            r'^(\s*\^ROWNUM_TABLE\s*=\s*\(\s*"[^"]+"\s*,\s*)\d+',
            rf"\g<1>{rownumRecord}",
            text,
        )
    return text


def _recordBytes(layout: ArrayLayout) -> int:
    return layout.samples * layout.dtype.itemsize


def _writeProduct(
    label: Path, target: Path, image: np.ndarray, layout: ArrayLayout, table: bytes
) -> None:
    """``image`` in stored axis order, then ``table``, padded to whole records,
    as ``target`` (.img) with the crop's label rewritten beside it."""
    record = _recordBytes(layout)
    data = np.where(np.isfinite(image), image, _NODATA).astype(layout.dtype).tobytes()
    imageRecords = len(data) // record
    payload = data + table
    payload += bytes(-len(payload) % record)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.with_suffix(".img").write_bytes(payload)
    target.with_suffix(".lbl").write_text(
        _relabel(
            label,
            image.shape[0] if layout.interleave == "BIL" else image.shape[1],
            len(payload) // record,
            imageRecords + 1 if table else None,
        )
    )


def _geometry(
    lines: int, samples: int, slewLines: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """Latitude and longitude of the pixel centers ``(l, s)``: lines run
    south, samples east, both turned by ``SCENE_ROTATION_DEG``. The first
    ``slewLines`` lines are a slew: ``_SLEW_STEP`` line spacings apart, they
    reach far north of the scene."""
    rows, cols = np.mgrid[0:lines, 0:samples].astype(np.float64)
    rows[:slewLines] = slewLines + _SLEW_STEP * (rows[:slewLines] - slewLines)
    rows -= (lines - 1) / 2
    cols -= (samples - 1) / 2
    angle = math.radians(SCENE_ROTATION_DEG)
    east = SCENE_PIXEL_M * (cols * math.cos(angle) + rows * math.sin(angle))
    north = SCENE_PIXEL_M * (cols * math.sin(angle) - rows * math.cos(angle))
    lat0, lon0 = SCENE_CENTER_DEG
    lat = lat0 + np.degrees(north / _MARS_RADIUS_M)
    lon = lon0 + np.degrees(east / (_MARS_RADIUS_M * math.cos(math.radians(lat0))))
    return lat, lon


def writeSyntheticObservation(
    folder: Path, cube: np.ndarray, truth: SyntheticTruth, *, slewLines: int = 0
) -> CrismObservation:
    """Write ``cube`` and its truth as a raw observation the readers accept:
    the TRR3 (``trdr/``, I/F times cos i, since the synthetic cube carries no
    cos i factor), its DDR (``ddr/``: the truth's incidence, as the areoid and
    the MOLA one, and elevation; latitude and longitude of a Jezero-like swath
    of ~18 m pixels turned by 8°; the other backplanes constant), and the
    calibration ``CalibrationLibrary`` finds offline in ``CRISM_CDR/``: the
    crop's WA and an AT holding the truth's transmission. The labels are the
    crops', with their line counts, records and pointers rewritten. The
    first ``slewLines`` lines are given the geometry of a slew (``_geometry``)."""
    folder = Path(folder)
    lines, samples, bands = cube.shape
    trr3 = readTrr3(_TRR3)
    layout = trr3.label.image
    if (samples, bands) != (layout.samples, layout.bands):
        raise ValueError(
            f"the cube is {samples} x {bands}; the crop template is "
            f"{layout.samples} x {layout.bands}"
        )
    assert layout.rownumOffset is not None
    table = layout.dataFile.read_bytes()[
        layout.rownumOffset : layout.rownumOffset + 2 * bands
    ]
    cosine = np.cos(np.radians(truth.incidenceDeg))[..., None]
    raw = cube * cosine
    stored = (raw[:, :, ::-1] if trr3.flipped else raw).transpose(0, 2, 1)  # BIL
    irCube = folder / "trdr" / _TRR3.with_suffix(".img").name
    _writeProduct(_TRR3, irCube, stored, layout, table)

    ddrLayout = readLabel(_DDR).image
    lat, lon = _geometry(lines, samples, slewLines)
    planes = np.full((len(DdrBand), lines, samples), _NODATA)
    planes[DdrBand.INCIDENCE_AREOID] = truth.incidenceDeg
    planes[DdrBand.INCIDENCE_MOLA] = truth.incidenceDeg
    planes[DdrBand.LATITUDE] = lat
    planes[DdrBand.LONGITUDE] = lon
    planes[DdrBand.ELEVATION] = truth.elevationM
    for band, value in _DDR_CONSTANTS.items():
        planes[band] = value
    irDdr = folder / "ddr" / _DDR.with_suffix(".img").name
    _writeProduct(_DDR, irDdr, planes, ddrLayout, b"")

    calibration = folder / "CRISM_CDR"
    for source, kind in ((_WA, "wa"), (_AT, "at")):
        (calibration / kind).mkdir(parents=True, exist_ok=True)
        for suffix in (".lbl", ".img"):
            shutil.copyfile(
                source.with_suffix(suffix),
                calibration / kind / source.with_suffix(suffix).name,
            )
    at = readLabel(calibration / "at" / _AT.name).image
    rows = readDetectorFrame(_AT).detectorRows
    position = {int(row): i for i, row in enumerate(trr3.detectorRows)}
    stored = np.memmap(
        at.dataFile,
        dtype=at.dtype,
        mode="r+",
        offset=at.offset,
        shape=(1, at.bands, at.samples),
    )
    for j, row in enumerate(rows):
        values = truth.transmission[:, position[int(row)]]
        stored[0, j] = np.where(np.isfinite(values), values, _NODATA)
    stored.flush()
    del stored
    return CrismObservation(
        trr3.id.observationName,
        trr3.id.counter,
        irCube.with_suffix(".lbl"),
        irDdr.with_suffix(".lbl"),
    )
