# Import data_sources to trigger decorator-based registration
from .data_source import DataSource
from .array_data_source import ArrayDataSource
from .rasterio_data_source import RasterioDataSource
from .envi_data_source import ENVIDataSource
from .pds_data_source import PdsLabelDataSource
from .hdf5_data_source import HDF5DataSource
from .fits_data_source import FitsDataSource
from .in_memory_data_source import InMemoryDataSource

__all__ = [
    "DataSource",
    "ArrayDataSource",
    "RasterioDataSource",
    "ENVIDataSource",
    "PdsLabelDataSource",
    "HDF5DataSource",
    "FitsDataSource",
    "InMemoryDataSource",
]
