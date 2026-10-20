"""Write a VardaRaster to disk as ENVI (.img + .hdr) or GeoTIFF.

The metadata written is what Varda's own data sources read back: ENVI header
items (wavelength, wavelength units, band names, default bands, description)
for ENVI, and the equivalent GDAL tags plus band descriptions for GeoTIFF.
The provenance record and the scientific metadata worth keeping go, as JSON,
in the ``VARDA`` metadata domain: inside a GeoTIFF, in ``<file>.aux.xml``
beside an ENVI file (``common.provenance.fileTags``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import numpy as np
import rasterio

from varda.common.provenance import VARDA_NAMESPACE, fileTags, provenanceText
from varda.common.spectral_axis import BAND_METADATA_KEYS
from varda.common.entities import VardaRaster
from varda.utilities.proj_lock import PROJ_LOCK

ENVI_SUFFIXES = frozenset({".img", ".hdr"})
GEOTIFF_SUFFIXES = frozenset({".tif", ".tiff"})
SAVE_FILE_FILTER = "ENVI image (*.img *.hdr);;GeoTIFF (*.tif *.tiff)"


def suggestedOutputPath(image: VardaRaster, stem: str | None = None) -> Path:
    """An ENVI path next to the image's file (or in the home directory)."""
    folder = Path(image.filePath).parent if image.filePath else Path.home()
    return folder / f"{stem or image.name}.img"


# Not allowed in file names on Windows; refused everywhere, so an exported
# file can be shared with any computer
FORBIDDEN_CHARACTERS = '<>:"|?*'
RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def resolveOutputPath(text: str, folder: Path) -> Path:
    """Where the typed ``text`` points: a bare or relative name goes into
    ``folder`` (never the folder Varda happened to start in). Trailing dots
    and spaces, which Windows drops, are removed. Raises ValueError, in
    words for the user, for a name no system can take or a folder that
    doesn't exist."""
    typed = Path(text.strip())
    name = typed.name.rstrip(". ")
    if not name:
        raise ValueError("Type a file name to export to.")
    forbidden = sorted({c for c in name if c in FORBIDDEN_CHARACTERS or ord(c) < 32})
    if forbidden:
        shown = " ".join(c if ord(c) >= 32 else f"\\x{ord(c):02x}" for c in forbidden)
        raise ValueError(
            f"{shown} can't be used in file names (Windows forbids them). "
            "Choose another name."
        )
    if name.split(".")[0].upper() in RESERVED_NAMES:
        raise ValueError(
            f"{name.split('.')[0]} is reserved by Windows for devices. "
            "Choose another name."
        )
    target = typed.with_name(name)
    suffix = target.suffix.lower()
    if suffix and suffix not in ENVI_SUFFIXES | GEOTIFF_SUFFIXES:
        raise ValueError(
            f"{suffix} isn't a format Varda writes: end the name in .img (ENVI) "
            "or .tif (GeoTIFF)."
        )
    if not target.is_absolute():
        target = folder / target
    if not target.parent.is_dir():
        raise ValueError(
            f"The folder {target.parent} doesn't exist. Choose another folder "
            "with Browse…, or create it first."
        )
    return target


# Files written beside a raster whose metadata holds a record under the key:
# the function gets the record and the data file's path, writes its files and
# returns them with what each holds (named in the raster's description, so a
# reader knows what must travel with it). Analyses register their own (see
# ratio_cube_analysis).
SidecarWriter = Callable[[dict, Path], list[tuple[Path, str]]]
SIDECAR_WRITERS: dict[str, SidecarWriter] = {}


def writeRaster(image: VardaRaster, path: str | Path) -> Path:
    """Write ``image`` to ``path``; the format follows the suffix (ENVI when
    there is none). Returns the path of the data file written (for ENVI the
    ``.img``; its ``.hdr`` sits beside it)."""
    target = Path(path)
    # "name." would become "name..img"; Windows drops trailing dots anyway
    target = target.with_name(target.name.rstrip(". ") or target.name)
    suffix = target.suffix.lower()
    if suffix == "":
        target, suffix = target.with_suffix(".img"), ".img"
    if suffix in ENVI_SUFFIXES:
        target, driver = target.with_suffix(".img"), "ENVI"
    elif suffix in GEOTIFF_SUFFIXES:
        driver = "GTiff"
    else:
        raise ValueError(
            f"Cannot write '{suffix}' files; use ENVI (.img/.hdr) or GeoTIFF (.tif)."
        )

    data = np.asarray(image.getData())
    if data.ndim == 2:
        data = data[:, :, np.newaxis]
    names = [str(name) for name in image.bandNames]
    wavelengths = [str(w) for w in image.wavelengths]
    # Wavelengths only for spectra (and parameter images, whose reader needs
    # the list); other products (slopes, classes, components) are named by
    # their band names alone
    writeAxis = image.bandModel.isSpectral or image.dataSource.isParameterImage
    displayBands = [str(int(b) + 1) for b in image.defaultBands]  # ENVI is 1-based

    with PROJ_LOCK:  # PROJ is not thread-safe; writing usually runs in a worker
        crs = rasterio.CRS.from_wkt(image.crs.to_wkt()) if image.crs else None
    profile = {
        "driver": driver,
        "height": data.shape[0],
        "width": data.shape[1],
        "count": data.shape[2],
        "dtype": data.dtype.name,
        "crs": crs,
        "transform": image.transform,
        "nodata": image.nodata,
    }
    if driver == "GTiff":
        profile["BIGTIFF"] = "IF_SAFER"

    sidecars = [
        sidecar
        for key, writer in SIDECAR_WRITERS.items()
        if isinstance(image.extraMetadata.get(key), dict)
        for sidecar in writer(image.extraMetadata[key], target)
    ]
    varda = fileTags(image.extraMetadata)
    if driver == "ENVI" and varda:
        aux = (Path(f"{target}.aux.xml"), "Varda's metadata record, read through GDAL")
        sidecars.insert(0, aux)
    if driver == "ENVI" and sidecars:  # the header too, when anything else
        sidecars.insert(0, (target.with_suffix(".hdr"), "the ENVI header"))

    description = _describe(image, sidecars)

    with rasterio.open(target, "w", **profile) as dst:
        dst.write(np.moveaxis(data, 2, 0))
        for i, name in enumerate(names, start=1):
            dst.set_band_description(i, name)
        if driver == "ENVI":
            tags = {
                "band_names": _enviList(names),
                "default_bands": _enviList(displayBands),
                # kept by GDAL in the .aux.xml (what Varda reads back); the
                # header's own description is set after closing
                "description": description,
            }
            if writeAxis:
                tags["wavelength"] = _enviList(wavelengths)
                tags["wavelength_units"] = image.wavelengthUnits
            # FWHM, bad bands and the scale factor, as read (in the file's units)
            for key in BAND_METADATA_KEYS:
                value = image.extraMetadata.get(key)
                if value is not None:
                    tags[key] = (
                        value
                        if isinstance(value, str)
                        else _enviList([str(v) for v in value])
                    )
            dst.update_tags(ns="ENVI", **tags)
        else:
            tags = {"description": description}
            if writeAxis:
                tags["wavelength"] = ", ".join(wavelengths)
                tags["wavelength_units"] = image.wavelengthUnits
            dst.update_tags(**tags)
            for i, name in enumerate(names, start=1):
                dst.update_tags(i, name=name)
        if varda:
            dst.update_tags(ns=VARDA_NAMESPACE, **varda)
    if driver == "ENVI":
        _setEnviDescription(target.with_suffix(".hdr"), description)
    return target


_ENVI_DESCRIPTION = re.compile(r"^description = \{.*?\}", re.MULTILINE | re.DOTALL)


def _setEnviDescription(header: Path, description: str) -> None:
    """Put ``description`` in the header: GDAL's ENVI driver writes the
    file's path there, whatever the ENVI metadata says."""
    text = header.read_text()
    entry = f"description = {{\n{description}}}"
    if _ENVI_DESCRIPTION.search(text):
        text = _ENVI_DESCRIPTION.sub(lambda _match: entry, text, count=1)
    else:
        text = text.replace("ENVI\n", f"ENVI\n{entry}\n", 1)
    header.write_text(text)


def _enviList(items: list[str]) -> str:
    return "{" + ", ".join(items) + "}"


def _describe(image: VardaRaster, sidecars: list[tuple[Path, str]]) -> str:
    """The image's description, with how it was made (if Varda made it) and
    the files that travel with it."""
    travel = ", ".join(f"{path.name} ({what})" for path, what in sidecars)
    parts = [
        image.dataSource.description,
        provenanceText(image),
        f"Keep these files with it: {travel}" if travel else "",
    ]
    # ENVI lists use braces, so keep them out of the text
    text = ". ".join(p.rstrip(".") for p in parts if p)
    return text.replace("{", "(").replace("}", ")")
