"""
entities.py: data structures used throughout Varda
"""

# standard library
from __future__ import annotations
from dataclasses import dataclass, field
import logging
import math

from typing import Any, Dict, Optional, TYPE_CHECKING
from datetime import datetime
import uuid
from enum import Enum
from functools import cached_property
from pathlib import Path

# third party imports
import attrs
import affine
from affine import Affine
import numpy as np
import numpy.typing as npt
import geopandas as gpd
from PyQt6.QtGui import QColor
from pyproj import CRS
from pyproj.exceptions import CRSError

from varda.common.deprecation import warnDeprecated
from psygnal import Signal

from varda.common.spectral_axis import BBL_KEY, FWHM_KEY, BandModel, bandModelFor
from varda.geo.health import CrsHealth
from varda.geo.labels import LabelHints
from varda.geo.measure import toLonLat
from varda.utilities.proj_lock import PROJ_LOCK
from shapely.geometry.base import BaseGeometry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from varda.image_loading.data_sources import DataSource


# Metadata the user can edit in Varda (Image Properties), per band or single
EDITABLE_PER_BAND = ("wavelengths", "bandNames", "fwhm", "bbl")
# "crs": a coordinate system (WKT, or any text PROJ reads) used instead of the
# file's; None is the file's own, kept so a session remembers the choice.
# "transform": the pixel-to-map affine (a, b, c, d, e, f) used instead of the
# file's (e.g. one corrected from the label); None is the file's own
EDITABLE_SINGLE = ("nodata", "wavelengthUnits", "description", "crs", "transform")


TRANSFORM_ERROR = "A transform needs six finite numbers with a nonzero determinant"


def _parsedTransform(value: object) -> Affine | None:
    """The ``transform`` override ``value`` (six numbers ``a, b, c, d, e, f``,
    a list or a tuple) as an Affine; ``None`` for none. Raises ValueError for
    anything else, a non-finite number or a zero determinant (``a·e − b·d``)."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        raise ValueError(TRANSFORM_ERROR)
    numbers: list[float] = []
    for n in value:
        if isinstance(n, bool) or not isinstance(
            n, (int, float, np.integer, np.floating)
        ):
            raise ValueError(TRANSFORM_ERROR)
        numbers.append(float(n))
    if not all(math.isfinite(n) for n in numbers):
        raise ValueError(TRANSFORM_ERROR)
    a, b, c, d, e, f = numbers
    if a * e - b * d == 0:
        raise ValueError(TRANSFORM_ERROR)
    return Affine(a, b, c, d, e, f)


class VardaRaster:
    """Primary raster image entity in Varda.

    Wraps a DataSource for data access and holds application-level metadata.
    Metadata edited in Varda (``setMetadataOverrides``) takes the place of
    the file's; reads then give the edited no-data value as NaN.
    """

    sigMetadataChanged = Signal()  # an edit of the name or metadata

    def __init__(self, dataSource: DataSource, name: str | None = None):
        self._dataSource = dataSource
        self._name = name
        self._overrides: dict = {}
        self._overrideCrs: CRS | None = None  # the "crs" override, parsed once
        self._overrideTransform: Affine | None = None  # the "transform" override
        # Set by loading (image_loading.crs_check): the coordinate-system
        # health check, and the facts the image's own label states
        self.crsHealth: CrsHealth | None = None
        self.labelHints: LabelHints | None = None

    # --- Edited metadata ---

    @property
    def metadataOverrides(self) -> dict:
        """The metadata edited in Varda (keys of ``EDITABLE_PER_BAND`` and
        ``EDITABLE_SINGLE``), a copy."""
        return dict(self._overrides)

    def setMetadataOverrides(self, overrides: dict, name: str | None = None) -> None:
        """Replace the edited metadata (``{}`` goes back to the file's) and,
        with ``name``, the name; everything showing the image is told."""
        for key, value in overrides.items():
            if key not in EDITABLE_PER_BAND + EDITABLE_SINGLE:
                raise ValueError(f"{key!r} isn't metadata Varda can edit.")
            if key in EDITABLE_PER_BAND and len(value) != self.bandCount:
                raise ValueError(
                    f"{key} needs a value for each of the {self.bandCount} bands, "
                    f"not {len(value)}."
                )
        overrideCrs = self._parsedCrs(overrides.get("crs"))
        overrideTransform = _parsedTransform(overrides.get("transform"))
        self._overrides = dict(overrides)
        self._overrideTransform = overrideTransform
        self._overrideCrs = overrideCrs
        if name:
            self._name = name
        for cached in ("bandModel", "raster"):
            self.__dict__.pop(cached, None)
        self.sigMetadataChanged.emit()

    def _parsedCrs(self, text: object) -> CRS | None:
        """The ``crs`` override ``text`` as a CRS (the one already built when
        it hasn't changed); ``None`` for none. Raises ValueError when PROJ
        can't read it."""
        if text is None:
            return None
        if not isinstance(text, str):
            raise ValueError(f"A coordinate system is text (WKT), not {text!r}.")
        if self._overrideCrs is not None and self._overrides.get("crs") == text:
            return self._overrideCrs
        with PROJ_LOCK:
            try:
                return CRS.from_user_input(text)
            except CRSError as error:
                raise ValueError(f"That isn't a coordinate system: {error}") from error

    def _readEdited(
        self, bandIndices: npt.ArrayLike | None, window: tuple | None
    ) -> np.ndarray:
        """The raw values with the edited no-data value (not the file's) as
        NaN, as file reads give no-data; whole numbers become float where
        some are no-data."""
        return self._withEditedNoData(
            self._dataSource.getData(bandIndices, window, masked=False)
        )

    def _withEditedNoData(self, raw: np.ndarray) -> np.ndarray:
        """``_readEdited`` of values read as they are in the file."""
        values = np.asarray(np.ma.getdata(raw))
        nodata = self._overrides["nodata"]
        if nodata is None or np.isnan(nodata):
            return values
        hit = values == nodata
        if not hit.any():
            return values
        if values.dtype.kind != "f":
            values = values.astype(
                np.float32 if values.dtype.itemsize <= 2 else np.float64
            )
        else:
            values = values.copy()
        values[hit] = np.nan
        return values

    # alternate constructor
    @classmethod
    def fromDataSource(cls, dataSource: DataSource) -> VardaRaster:
        """Create a VardaRaster directly from a DataSource, deriving metadata from it."""
        return cls(dataSource)

    # --- High-level data access ---

    def getSpectrum(self, x: int, y: int) -> Spectrum:
        """Get the spectrum at a specific pixel location.

        Args:
            x: Column index.
            y: Row index.

        Returns:
            Spectrum with values and wavelengths.
        """
        values = self._dataSource.getPixelSpectrum(x, y)
        return Spectrum(
            values=values, wavelengths=self.wavelengths, pixel_coordinates=(x, y)
        )

    def getBands(self, bandIndices: npt.ArrayLike) -> np.ndarray:
        """Get raster data for specific bands.

        Returns:
            Array with shape (height, width, n_bands).
        """
        if "nodata" in self._overrides:
            return self._readEdited(bandIndices, None)
        return self._dataSource.getBands(bandIndices)

    def getData(
        self,
        bandIndices: list[int] | None = None,
        window: tuple[int, int, int, int] | None = None,
        masked=True,
    ) -> np.ndarray:
        """Get raster data with optional band and spatial subsetting.

        Returns:
            Array with shape (height, width, bands).
        """
        if masked and "nodata" in self._overrides:
            return self._readEdited(bandIndices, window)
        return self._dataSource.getData(bandIndices, window, masked=masked)

    def getDataReduced(
        self,
        bandIndices: list[int] | None,
        window: tuple[int, int, int, int] | None,
        step: int,
    ) -> np.ndarray:
        """Every ``step``-th row and column of ``getData(bandIndices,
        window)``, read from the file's overviews where it has them: how a
        texture or preview of a large image is read."""
        if "nodata" in self._overrides:
            return self._withEditedNoData(
                self._dataSource.getDataReduced(bandIndices, window, step, masked=False)
            )
        return self._dataSource.getDataReduced(bandIndices, window, step)

    def __getitem__(self, key) -> np.ndarray:
        """Numpy-like indexing: ``raster[y, x, :]``."""
        return self._dataSource[key]

    # --- Coordinate transformation ---

    def pixelToGeo(self, col: int, row: int) -> tuple[float, float]:
        """CRS coordinates of the center of pixel (``col``, ``row``)."""
        x, y = self.transform * (col + 0.5, row + 0.5)
        return float(x), float(y)

    def geoToPixel(self, x: float, y: float) -> tuple[int, int]:
        """The (col, row) of the pixel containing CRS point (``x``, ``y``)."""
        col, row = ~self.transform * (x, y)
        return math.floor(col), math.floor(row)

    def imageToGeo(self, points: npt.ArrayLike) -> np.ndarray:
        """Continuous image coordinates (x = column, y = row; whole numbers on
        pixel edges, as for shape corners) to CRS coordinates, shape (n, 2)."""
        xy = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        x, y = self.transform * (xy[:, 0], xy[:, 1])
        return np.column_stack([x, y])

    def geoToImage(self, points: npt.ArrayLike) -> np.ndarray:
        """CRS coordinates to continuous image coordinates; see ``imageToGeo``."""
        xy = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        col, row = ~self.transform * (xy[:, 0], xy[:, 1])
        return np.column_stack([col, row])

    def pixelToLatLon(self, col: int, row: int) -> tuple[float, float] | None:
        """Pixel center as (lat, lon) in degrees on the CRS's own geodetic CRS:
        its body, latitude type (planetocentric or planetographic) and
        longitude direction, as the readout labels them (``geo.measure.toLonLat``,
        which reads the axis order rather than trusting ``always_xy``).

        Returns ``None`` if the raster has no CRS or the CRS has no longitude
        and latitude (a local CRS)."""
        crs = self.crs
        if crs is None:
            return None
        with PROJ_LOCK:
            if crs.geodetic_crs is None:
                return None
        ((lon, lat),) = toLonLat(crs, [self.pixelToGeo(col, row)])
        return float(lat), float(lon)

    # --- Delegated properties from DataSource ---

    @property
    def name(self) -> str:
        if self._name is None:
            if self._dataSource.filePath is not None:
                self._name = Path(self._dataSource.filePath).name
            else:
                self._name = "Untitled"

        return self._name

    @property
    def width(self) -> int:
        return self._dataSource.width

    @property
    def height(self) -> int:
        return self._dataSource.height

    @property
    def bandCount(self) -> int:
        return self._dataSource.bandCount

    @property
    def shape(self) -> tuple[int, int, int]:
        return (self.height, self.width, self.bandCount)

    @property
    def dtype(self) -> np.dtype:
        return self._dataSource.dtype

    @property
    def nodata(self) -> float | None:
        return self._overrides.get("nodata", self._dataSource.nodata)

    @property
    def wavelengths(self) -> np.ndarray:
        if "wavelengths" in self._overrides:
            return np.asarray(self._overrides["wavelengths"], dtype=np.float64)
        return self._dataSource.wavelengths

    @property
    def wavelengthsType(self) -> type:
        if "wavelengths" in self._overrides:
            return float
        return self._dataSource.wavelengthsType

    @property
    def wavelengthUnits(self) -> str:
        return self._overrides.get("wavelengthUnits", self._dataSource.wavelengthUnits)

    @cached_property
    def bandModel(self) -> BandModel:
        """The spectral axis: centers in nm (None if the bands aren't one),
        FWHM, bad bands, scale factor and no-data. Use it for any calculation
        with wavelengths instead of ``wavelengths`` and ``wavelengthUnits``."""
        model = bandModelFor(
            self.wavelengths,
            self.wavelengthUnits,
            self.nodata,
            self.extraMetadata,
            self.bandCount,
        )
        if model.unitNote:
            logger.warning("%s: %s", self.name, model.unitNote)
        return model

    @property
    def bandNames(self) -> list[str]:
        return list(self._overrides.get("bandNames", self._dataSource.bandNames))

    @property
    def defaultBands(self) -> np.ndarray:
        return self._dataSource.defaultBands

    @property
    def transform(self) -> Affine:
        """The pixel-to-map affine: the ``transform`` override when set, else
        the file's (``dataSource.transform``)."""
        if self._overrideTransform is not None:
            return self._overrideTransform
        return self._dataSource.transform

    @property
    def crs(self) -> CRS | None:
        """The coordinate system: the ``crs`` override when set, else the
        file's (``dataSource.crs``)."""
        if self._overrideCrs is not None:
            return self._overrideCrs
        return self._dataSource.crs

    @property
    def filePath(self) -> str | None:
        return self._dataSource.filePath

    @property
    def driver(self) -> str:
        return self._dataSource.driver

    @property
    def hasGeospatialData(self) -> bool:
        return self.crs is not None and self.transform != affine.identity

    @property
    def extraMetadata(self) -> dict:
        metadata = self._dataSource.extraMetadata
        if "fwhm" not in self._overrides and "bbl" not in self._overrides:
            return metadata
        metadata = dict(metadata)
        if "fwhm" in self._overrides:
            metadata[FWHM_KEY] = list(self._overrides["fwhm"])
        if "bbl" in self._overrides:
            metadata[BBL_KEY] = list(self._overrides["bbl"])
        return metadata

    @property
    def description(self) -> str:
        return self._overrides.get("description", self._dataSource.description)

    @property
    def dataSource(self) -> DataSource:
        """Access the underlying DataSource directly."""
        return self._dataSource

    # --- Memory management ---

    def close(self) -> None:
        """Close the underlying DataSource."""
        self._dataSource.close()

    # --- Compatibility layer (temporary, for migration) ---

    @cached_property
    def raster(self) -> np.ndarray:
        """Full raster data as (h, w, bands) array.

        .. deprecated::
            Use getBands(), getData(), or getSpectrum() instead.
            This loads the entire image into memory.
        """
        warnDeprecated(
            "VardaRaster.raster",
            since="0.0.2",
            instead="getBands(), getData() or getSpectrum()",
        )
        return self._dataSource.readAllBands()

    @property
    def metadata(self) -> CompatMetadata:
        """Backward-compatible metadata adapter.

        .. deprecated::
            Access properties directly on VardaRaster instead of through .metadata.
        """
        return CompatMetadata(self)

    def __repr__(self) -> str:
        return (
            f"VardaRaster({self.name!r}, {self.width}x{self.height}x{self.bandCount})"
        )


class CompatMetadata:
    """Backward-compatible adapter that makes ``image.metadata.X`` work during migration.

    Delegates to VardaRaster properties so old code keeps working.
    """

    def __init__(self, raster: VardaRaster):
        self._raster = raster

    @property
    def filePath(self) -> str:
        return self._raster.filePath or ""

    @property
    def driver(self) -> str:
        return self._raster.driver

    @property
    def width(self) -> int:
        return self._raster.width

    @property
    def height(self) -> int:
        return self._raster.height

    @property
    def bandCount(self) -> int:
        return self._raster.bandCount

    @property
    def dtype(self) -> str:
        return str(self._raster.dtype)

    @property
    def dataIgnore(self) -> float:
        nd = self._raster.nodata
        return nd if nd is not None else 0

    @property
    def defaultBands(self) -> np.ndarray:
        return self._raster.defaultBands

    @property
    def wavelengths(self) -> np.ndarray:
        return self._raster.wavelengths

    @property
    def wavelengths_type(self) -> type:
        return self._raster.wavelengthsType

    @property
    def name(self) -> str:
        return self._raster.name

    @property
    def transform(self) -> Affine:
        return self._raster.transform

    @property
    def crs(self) -> CRS | None:
        return self._raster.crs

    @property
    def extraMetadata(self) -> dict:
        return self._raster.extraMetadata

    @property
    def hasGeospatialData(self) -> bool:
        return self._raster.hasGeospatialData

    def toFlatDict(self) -> dict:
        core = {
            "filePath": self.filePath,
            "driver": self.driver,
            "width": self.width,
            "height": self.height,
            "bandCount": self.bandCount,
            "dtype": self.dtype,
            "dataIgnore": self.dataIgnore,
            "defaultBands": self.defaultBands,
            "wavelengths": self.wavelengths,
            "name": self.name,
        }
        core.update(self._raster.extraMetadata)
        return core

    def __iter__(self):
        return iter(self.toFlatDict().items())

    def __getitem__(self, item):
        return self.toFlatDict().get(item)

    def __repr__(self):
        items = self.toFlatDict()
        out = "CompatMetadata:\n"
        for key, value in items.items():
            out += f"    {key}: {value}\n"
        return out


@attrs.frozen(slots=True)
class Spectrum:
    """data container representing a Spectrum object in Varda

    Attributes:
        values (np.ndarray): a 1d array storing the spectral values.
        wavelengths (np.ndarray): a 1d array storing the wavelengths corresponding to the spectral values.
        index: A unique identifier for the spectrum. Mainly to be used for comparisons.
    """

    values: np.ndarray = attrs.field(converter=np.asarray)
    wavelengths: np.ndarray | list[str] = attrs.field(converter=np.asarray)
    pixel_coordinates: tuple[int, int] | None = None

    @values.validator
    def _check_values(self, attribute, value):
        if value.ndim != 1:
            raise ValueError(f"values must be a 1d array, got {value.ndim}d array")


class ROIMode(Enum):
    """Enum to define different ROI drawing modes."""

    FREEHAND = 0
    RECTANGLE = 1
    ELLIPSE = 2
    POLYGON = 3


@attrs.frozen(slots=True)
class VardaROI:
    """Immutable snapshot of a single ROI feature.

    Attributes:
        fid: Feature ID, unique within an ROICollection.
        name: Display name.
        color: RGBA color.
        geometry: Shapely geometry in CRS or pixel coordinates.
        roiType: Drawing mode used to create this ROI.
        properties: Arbitrary user-defined metadata.
    """

    fid: int
    name: str
    color: Color
    geometry: BaseGeometry
    roiType: ROIMode
    properties: dict[str, Any] = attrs.Factory(dict)


@dataclass
class Metadata:
    """Data container representing the metadata of an image.

    This ensures that every image contains a standard base set of metadata,
    but allows for adding extra metadata items via _extraMetadata.

    Note that this data container is mutable, But do not directly modify its contents.
    use ProjectContext to edit it so that the program knows when it's changed.
    """

    filePath: str = ""
    driver: str = ""
    width: int = 0
    height: int = 0
    dtype: str = ""
    dataIgnore: float = 0
    bandCount: int = 0
    defaultBand: np.ndarray[tuple[int], np.dtype[np.uint]] = field(
        default_factory=lambda: np.zeros(3, dtype=np.uint)
    )
    wavelengths: np.ndarray = field(default_factory=lambda: np.zeros(0))
    wavelengths_type: Type = float
    name: str = ""
    transform: Affine = affine.identity
    crs: CRS | None = None

    extraMetadata: Dict[str, str | int | float] = field(default_factory=dict)

    def __post_init__(self):
        """Validate that all attributes are of the correct type and handle unexpected kwargs."""
        if not isinstance(self.filePath, str):
            raise self.BadMetadataError("filePath", "str", self.filePath)
        if not isinstance(self.driver, str):
            raise self.BadMetadataError("driver", "str", self.driver)
        if not isinstance(self.width, int):
            raise self.BadMetadataError("width", "int", self.width)
        if not isinstance(self.dtype, str):
            raise self.BadMetadataError("dtype", "str", self.dtype)
        if not isinstance(self.dataIgnore, (int, float)):
            raise self.BadMetadataError("dataIgnore", "int or float", self.dataIgnore)
        if not isinstance(self.bandCount, int):
            raise self.BadMetadataError("bandCount", "int", self.bandCount)
        if not isinstance(self.defaultBand, list | np.ndarray):
            raise self.BadMetadataError("defaultBand", "Band", self.defaultBand)
        if not isinstance(self.wavelengths, np.ndarray):
            raise self.BadMetadataError("wavelengths", "np.ndarray", self.wavelengths)
        if not isinstance(self.extraMetadata, dict):
            raise self.BadMetadataError("extraMetadata", "dict", self.extraMetadata)
        for key, value in self.extraMetadata.items():
            if not self._checkExtraMetadata(value):
                raise self.BadMetadataError(
                    f"Extra Metadata: {key}", "str, int, or float", value
                )

        # fix inputs
        if self.wavelengths.size == 0:
            self.wavelengths = np.arange(self.bandCount)
        if len(self.name) == 0:
            self.name = self.filePath.split("/")[-1]

    def __init__(self, **kwargs):
        """
        Initialize metadata with flexible handling of unexpected keyword arguments.
        Any unexpected kwargs are placed in extraMetadata automatically.
        """
        # Get the list of expected field names from the dataclass
        expected_fields = [f.name for f in self.__dataclass_fields__.values()]

        # Handle expected fields
        for f in expected_fields:
            if f in kwargs:
                setattr(self, f, kwargs.pop(f))
            elif f == "extraMetadata":
                # Initialize extraMetadata if not provided
                self.extraMetadata = kwargs.pop("extraMetadata", {})

        # Move any remaining unexpected kwargs to extraMetadata
        for key, value in kwargs.items():
            logger.warning(
                f"Unexpected keyword argument '{key}' moved to extraMetadata"
            )
            self.extraMetadata[key] = value

        # I think this doesnt get called automatically if we override __init__
        self.__post_init__()

    def _checkExtraMetadata(self, item):
        """Check if a value is serializable by JSON."""
        if isinstance(item, (str, int, float)):
            return True
        if isinstance(item, (list, tuple)):
            return True and all(self._checkExtraMetadata(i) for i in item)
        if isinstance(item, dict):
            return True and all(
                self._checkExtraMetadata(key) and self._checkExtraMetadata(value)
                for key, value in item.items()
            )
        return False

    def serialize(self):
        """Generates a dictionary representation of the metadata."""
        return {
            "filePath": self.filePath,
            "driver": self.driver,
            "width": self.width,
            "height": self.height,
            "dtype": self.dtype,
            "dataIgnore": self.dataIgnore,
            "bandCount": self.bandCount,
            "defaultBand": self.defaultBand.serialize(),
            "wavelengths": self.wavelengths.tolist(),
            "extraMetadata": self.extraMetadata,
            "name": self.name,
        }

    @classmethod
    def deserialize(cls, data: Any):
        """Deserialize a dictionary representation of the metadata."""
        return cls(
            filePath=data["filePath"],
            driver=data["driver"],
            width=data["width"],
            height=data["height"],
            dtype=data["dtype"],
            dataIgnore=data["dataIgnore"],
            bandCount=data["bandCount"],
            defaultBand=data["defaultBand"],
            wavelengths=np.array(data["wavelengths"]),
            extraMetadata=data["extraMetadata"],
            name=data.get("name", ""),
        )

    # other methods
    def toFlatDict(self):
        """generate a flat dict containing all the metadata and extra metadata"""
        coreMetadata = {
            key: value for key, value in self.__dict__.items() if key != "extraMetadata"
        }
        return {**coreMetadata, **self.extraMetadata}

    @property
    def hasGeospatialData(self):
        """Check if the metadata contains geospatial data."""
        return self.crs is not None and self.transform is not None

    # magic methods to add the ability to iterate through the items
    def __iter__(self):
        items = self.toFlatDict().items()
        for attr, value in items:
            yield attr, value

    def __repr__(self):
        allItems = self.toFlatDict()
        out = "Metadata:\n"
        for key, value in allItems.items():
            out += "    " + f"{key}: {value}" + "\n"
        return out

    def __getitem__(self, item):
        return self.toFlatDict().get(item)

    class BadMetadataError(Exception):
        """Raised when the input given to metadata is of an incompatible format."""

        def __init__(self, itemName, correctType, actualValue):
            self.message = (
                f"{itemName} must be a {correctType}, got {type(actualValue).__name__}"
            )
            super().__init__(self.message)


@dataclass
class ROICustomData:
    """Stores custom data for an ROI"""

    values: Dict[str, Any] = field(default_factory=dict)

    def serialize(self):
        """Serialize custom data for storage"""
        # Convert any non-serializable values to strings
        serialized = {}
        for key, value in self.values.items():
            if isinstance(value, np.ndarray):
                serialized[key] = value.tolist()
            else:
                serialized[key] = value
        return serialized

    @classmethod
    def deserialize(cls, data):
        """Deserialize custom data from storage"""
        custom_data = cls()
        custom_data.values = data
        return custom_data


@dataclass
class ROI:
    """Enhanced data container for a region of interest in an image.

    Attributes:
        id: Unique identifier for the ROI
        name: User-friendly name for the ROI
        mode: Drawing mode for the ROI (e.g., freehand, rectangle)
        sourceImage: The source image this ROI was created from
        points: Points defining the ROI in pixel coordinates [x, y]
        geoPoints: Points in geographic coordinates (if available) [lon, lat]
        color: The ROI color in RGBA 0-255 format, in a QColor object
        visible: Whether the ROI is currently visible
        creationTime: When the ROI was created
        description: User description of the ROI
        arraySlice: Extracted image data within the ROI (optional)
        meanSpectrum: Mean spectral values within the ROI (optional)
        customData: Custom user-defined data associated with the ROI
    """

    gdf: Optional[gpd.GeoDataFrame] = field(default_factory=lambda: gpd.GeoDataFrame())
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Unnamed ROI"
    mode: ROIMode = ROIMode.FREEHAND
    sourceImage: Image = None
    points: np.ndarray = field(default_factory=lambda: np.empty((0, 2)))
    geoPoints: Optional[np.ndarray] = None
    color: QColor = field(default_factory=lambda: QColor(255, 0, 0, 128))
    visible: bool = True
    creationTime: datetime = field(default_factory=datetime.now)
    description: str = "No description"
    arraySlice: Optional[np.ndarray] = None
    meanSpectrum: Optional[np.ndarray] = None
    customData: ROICustomData = field(default_factory=ROICustomData)

    def __post_init__(self):
        """Validate and initialize the ROI after creation"""
        # Ensure points is a numpy array
        if not isinstance(self.points, np.ndarray):
            if isinstance(self.points, list):
                self.points = np.array(self.points)
            else:
                logger.warning(
                    f"Converting invalid points type {type(self.points)} to empty array"
                )
                self.points = np.array([])

        # Same for geo_points
        if self.geoPoints is not None and not isinstance(self.geoPoints, np.ndarray):
            if isinstance(self.geoPoints, list):
                self.geoPoints = np.array(self.geoPoints)
            else:
                logger.warning(
                    f"Converting invalid geo_points type {type(self.geoPoints)} to None"
                )
                self.geoPoints = None

        # Ensure color is a valid RGBA tuple
        if not isinstance(self.color, QColor):
            logger.warning(f"Invalid color {self.color}, using default")
            self.color = QColor(255, 0, 0, 128)  # Default red with 50% opacity

    def updateProperties(self, **kwargs):
        """Update multiple ROI properties at once"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                logger.warning(f"Unknown ROI property: {key}")

    def getCustomValue(self, column_name, default=None):
        """Get a custom data value by column name"""
        return self.customData.values.get(column_name, default)

    def setCustomValue(self, column_name, value):
        """Set a custom data value"""
        self.customData.values[column_name] = value

    def getBounds(self):
        """Calculate the bounding box of the ROI points"""
        if len(self.points) == 0:
            return 0, 0, 0, 0
        min_x, min_y = self.points.min(axis=0)
        max_x, max_y = self.points.max(axis=0)
        return min_x, min_y, max_x, max_y

    def serialize(self):
        """Convert the ROI to a serializable dictionary"""
        # Convert numpy arrays to lists for serialization
        points_list = (
            self.points.tolist() if isinstance(self.points, np.ndarray) else []
        )
        geo_points_list = (
            self.geoPoints.tolist() if isinstance(self.geoPoints, np.ndarray) else None
        )

        # Convert array_slice and mean_spectrum to lists if they exist
        array_slice_list = array_slice_list = (
            self.arraySlice.tolist() if self.arraySlice is not None else None
        )

        mean_spectrum_list = (
            self.meanSpectrum.tolist() if self.meanSpectrum is not None else None
        )

        # convert color to tuple
        colorTuple = (
            self.color.red(),
            self.color.green(),
            self.color.blue(),
            self.color.alpha(),
        )
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode.name,
            "sourceImage": self.sourceImage,
            "points": points_list,
            "geoPoints": geo_points_list,
            "color": colorTuple,
            "visible": self.visible,
            "creationTime": self.creationTime.isoformat(),
            "description": self.description,
            "arraySlice": array_slice_list,
            "meanSpectrum": mean_spectrum_list,
            "customData": self.customData.serialize(),
        }

    @classmethod
    def deserialize(cls, data):
        """Create an ROI from a serialized dictionary"""
        inputKwargs = {}

        if data.get("id"):
            inputKwargs["id"] = data["id"]
        if data.get("name"):
            inputKwargs["name"] = data["name"]
        if data.get("mode"):
            inputKwargs["mode"] = ROIMode[data["mode"]]
        if data.get("sourceImage") is not None:
            inputKwargs["sourceImage"] = data["sourceImage"]

        points = data.get("points")
        if points is not None:
            inputKwargs["points"] = np.array(points)

        geo_points = data.get("geoPoints")
        if geo_points is not None:
            inputKwargs["geoPoints"] = np.array(geo_points)

        creationTime = data.get("creationTime")
        if creationTime:
            inputKwargs["creationTime"] = datetime.fromisoformat(creationTime)

        array_slice = data.get("arraySlice")
        if array_slice is not None:
            inputKwargs["arraySlice"] = np.array(array_slice)

        mean_spectrum = data.get("meanSpectrum")
        if mean_spectrum is not None:
            inputKwargs["meanSpectrum"] = np.array(mean_spectrum)

        color = data.get("color")
        if color is not None:
            inputKwargs["color"] = QColor(*color)

        visible = data.get("visible")
        if visible is not None:
            inputKwargs["visible"] = visible

        description = data.get("description")
        if description is not None:
            inputKwargs["description"] = description

        custom_data = data.get("customData")
        if custom_data is not None:
            inputKwargs["customData"] = ROICustomData.deserialize(custom_data)

        return cls(**inputKwargs)

    def clone(self):
        """Create a deep copy of the ROI"""
        return ROI.deserialize(self.serialize())

    def __str__(self):
        return f"ROI '{self.name}' ({self.id}) with {len(self.points)} points"


@dataclass
class Plot:
    """
    Attributes:
        plot_type (str): The type of plot (e.g., "ROI", "Histogram").
        timestamp (str): The time when the plot was saved.
        data (Any): Data needed to reconstruct the plot.
    """

    plot_type: str
    timestamp: str
    data: Any

    @staticmethod
    def create(roi: ROI):
        """Factory method to create a new plot with a timestamp."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return Plot("ROI", timestamp, roi.meanSpectrum)


@attrs.define
class Color:
    r: float
    g: float
    b: float
    a: float

    @classmethod
    def black(cls):
        return cls(0.0, 0.0, 0.0, 1.0)

    @classmethod
    def white(cls):
        return cls(1.0, 1.0, 1.0, 1.0)

    @classmethod
    def fromHexString(cls, hex):
        """
        an rgba hex string in the format of "#RRGGBBAA", or opaque "#RRGGBB"
        """
        digits = hex[1:]
        return cls.fromBytes(
            bytes.fromhex(digits if len(digits) == 8 else digits + "ff")
        )

    @classmethod
    def fromBytes(cls, b: bytes):
        return cls(
            float(b[0]) / 255.0,
            float(b[1]) / 255.0,
            float(b[2]) / 255.0,
            float(b[3]) / 255.0,
        )

    @classmethod
    def fromQColor(cls, color: QColor):
        return cls(color.redF(), color.greenF(), color.blueF(), color.alphaF())

    def toQColor(self) -> QColor:
        c = self._toBytes()
        return QColor(c[0], c[1], c[2], c[3])

    def toHexString(self) -> str:
        """
        returns a hex string in the format of "#RRGGBBAA"
        """
        return "#{:02x}{:02x}{:02x}{:02x}".format(*self._toBytes())

    def _toBytes(self):
        return bytes(
            (
                round(self.r * 255),
                round(self.g * 255),
                round(self.b * 255),
                round(self.a * 255),
            )
        )


# --- Lazy import for Image alias. This basically just makes it so ---
def __getattr__(name):
    if name in ("Image", "VardaRaster"):
        globals()["Image"] = VardaRaster
        globals()["VardaRaster"] = VardaRaster
        return VardaRaster
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
