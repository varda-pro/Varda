import os
import numpy as np
import psutil
import yaml
import re
import bottleneck as bn
from tqdm import tqdm
import dask.array as da
from skimage.morphology import dilation, erosion, footprint_rectangle
from scipy.ndimage import convolve
import rasterio as rio
from scipy.ndimage import binary_opening
from scipy import ndimage
import pandas as pd
from affine import Affine
from shapely.geometry import shape
from rasterio.io import MemoryFile
from exactextract import exact_extract
from rasterio.features import shapes
import tempfile
import shutil
import geopandas as gpd


class Vectroscopy:
    def __init__(self, config):
        self.config = config

    @classmethod
    def from_array(cls, array, thresholds=None, crs=None, transform=None, name=None):
        """
        Create an instance of Vectroscopy from an array.

        Args:
            array: Raster data to process.
            thresholds: Threshold values for the raster data.
            crs: Coordinate Reference System of the raster data.
            transform: Affine transformation for the raster data.
            name: Name for the parameter.

        Returns:
            Vectroscopy: An instance of the Vectroscopy class.
        """
        config = Config(
            process="default"
        )  # could be where you have multiple processing profiles.
        # config.config_array(param=rast, mask=mask, crs=crs, transform=transform)
        if transform is None:
            transform = Affine.translation(0, 0)
        config.add_parameter(
            array=array, thresholds=thresholds, crs=crs, transform=transform, name=name
        )
        return cls(config)

    def add_param(self, array, thresholds=None, crs=None, transform=None, name=None):
        """
        Add another parameter to the existing configuration.

        Args:
            array: Raster data to add
            crs: Coordinate Reference System
            transform: Affine transformation
            name: Name for the parameter
            thresholds: Threshold values for this parameter

        Returns:
            self: Returns self to enable method chaining
        """
        self.config.add_parameter(
            array=array, crs=crs, transform=transform, name=name, thresholds=thresholds
        )
        return self

    def add_mask(
        self, array=None, crs=None, transform=None, name=None, thresholds=None
    ):
        """
        Add a mask to the existing configuration.

        Args:
            array: Raster data to add
            crs: Coordinate Reference System
            transform: Affine transformation
            name: Name for the parameter
            thresholds: Threshold values for this parameter

        Returns:
            self: Returns self to enable method chaining
        """
        self.config.add_mask(
            array=array, crs=crs, transform=transform, name=name, thresholds=thresholds
        )
        return self

    def config_output(
        self,
        stats=None,
        show_base=None,
        base_stats=None,
        driver=None,
        output_path=None,
        output_filename=None,
    ):
        """
        Configure the output settings for the Vectroscopy instance.

        Args:
            stats: The statistics to include in the output.
            show_base: Whether to show the base statistics.
            base_stats: The base statistics to include.
            driver: The GDAL driver to use for output. (e.g. "pandas"(Default), "ESRI Shapefile", "GeoJSON", "GPKG", )
            output_path: The path to the output file. "ESRI Shapefile" is default.

        Returns:
            self: Returns self to enable method chaining.
        """
        self.config.output_stats = stats
        self.config.show_base = show_base
        self.config.base_stats = base_stats
        self.config.driver = driver
        self.config.output_path = output_path
        return self

    @classmethod
    def from_files(
        cls,
        rast=None,
        mask=None,
        stats=None,
        output=None,
        path=None,
        config_yaml: str = None,
    ):
        """
        Create an instance of Vectroscopy from a file.

        Args:
            rast: Single raster data or a list of raster data.
            mask: A mask to apply to the raster data.
            crs: Coordinate Reference System of the raster data.
            transform: Affine transformation for the raster data.
            config_yaml (str): Path to the configuration YAML file.

        Returns:
            Vectroscopy: An instance of the Vectroscopy class.
        """
        config = Config(processing="default")
        config.config_files(rast=rast, mask=mask)
        return cls(config)

    def vectorize(self):
        """
        Vectorizes data.

        Raster data must be the same shape and have the same CRS and transform.

        Args:
            rasts: single raster data or a list of raster data.
            mask: A mask to apply to the raster data.
            raster_list: A list of processed raster data.
            zonal_stats: The zonal statistics for the raster data.

        Returns:
            List: A list of vectorized geometries.
        """
        return ProcessingPipeline(self.config).process_file()


class Config:
    """
    Configuration handler for the mineral mapping application.
    Loads and provides access to settings from a YAML file.
    """

    def __init__(self, yaml_file=None, process=None):
        default_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "config.yaml")
        )
        self.yaml = True
        if yaml_file is None:
            self.yaml = False
        self.yaml_file = yaml_file or default_path
        self._config = None
        self.curr_process = None
        self.output_stats = None
        self.show_base = None
        self.base_stats = None
        self.driver = None
        self.output_path = None
        self.process = process or "default"
        self.params = []
        self.load_config()
        self.output_filename = self.create_output_filename()
        # self.config_ram(ram_pct=0.4, verbose=True)

    def config_ram(self, ram_pct, verbose):
        """
        Configure GDAL's cache size based on system memory.

        Parameters:
        - ram_pct (float): Fraction (0-1) of total RAM to allocate to GDAL cache.
        - verbose (bool): Whether to print the configured values.

        Returns:
        - int: Cache size in MB actually set
        """
        if not (0 < ram_pct <= 1):
            raise ValueError("ram_pct must be between 0 and 1")

        # Get system memory
        total_ram_bytes = psutil.virtual_memory().total
        cache_max_bytes = int((total_ram_bytes * ram_pct))

        # Set GDAL cache size
        # gdal.SetCacheMax(cache_max_bytes)
        # gdal.SetConfigOption("GDAL_CACHEMAX", str(cache_max_bytes))  # affects external tools too

        if verbose:
            print(
                f"[GDAL] Cache max set to {cache_max_bytes} MB ({ram_pct * 100:.0f}% of total RAM)"
            )

        return cache_max_bytes

    def get_parameters_list(self):
        """
        Initialize the parameters based on the process configuration.

        Returns:
            List: A list of Parameter objects initialized with the raster data.
        """
        return self.params

    def add_parameter(
        self, array, thresholds=None, crs=None, transform=None, name=None
    ):
        """
        Add a new parameter to the configuration.

        Args:
            array: The raster data as a numpy array.
            crs: Coordinate Reference System of the raster data.
            transform: Affine transformation for the raster data.
            name: Name for the parameter.
            thresholds: Threshold values for this parameter.
        """
        if name is None:
            raise ValueError("Parameter name must be provided.")

        param = Parameter(
            self.name_check(name),
            array=array,
            crs=crs,
            transform=transform,
            thresholds=thresholds,
        )
        self.params.append(param)

    def add_mask(self, array=None, crs=None, transform=None, name=None, threshold=None):
        """
        Add a new mask to the configuration.

        Args:
            array: The raster data as a numpy array.
            crs: Coordinate Reference System of the raster data.
            transform: Affine transformation for the raster data.
            name: Name for the mask.
            threshold: Threshold value for the mask.
        """
        if name is None:
            raise ValueError("Mask name must be provided.")
        if isinstance(threshold, (list, tuple)):
            raise ValueError("Threshold must be a single number, not a list or tuple.")

        mask = Parameter(
            self.name_check(name),
            array=array,
            crs=crs,
            transform=transform,
            thresholds=threshold,
        )
        mask.mask = True
        self.params.append(mask)

    def config_array(self, param, crs, transform, mask=None):
        """
        Initialize the configuration with an array and its metadata.

        Args:
            array: The raster data as a numpy array.
            crs: Coordinate Reference System of the raster data.
            transform: Affine transformation for the raster data.
        """
        for key, value in param.items():
            if isinstance(value, tuple) and len(value) == 2:
                # If the value is a list, assume it's a list of arrays
                param = Parameter(
                    self.name_check(key),
                    array=value[0],
                    crs=crs,
                    transform=transform,
                    thresholds=value[1] if len(value) > 1 else None,
                )
                self.params.append(param)
            else:
                raise ValueError("Provide thresholds")
        if mask is not None:
            for key, value in mask.items():
                if isinstance(value, list):
                    # If the value is a list, assume it's a list of arrays
                    mask_param = Parameter(
                        self.name_check(key),
                        array=value[0],
                        crs=crs,
                        transform=transform,
                        thresholds=value[1] if len(value) > 1 else None,
                    )
                    mask_param.mask = True
                    self.params.append(mask_param)
                else:
                    raise ValueError("Provide thresholds for mask")

    def name_check(self, name):
        """
        Check if the name is valid for a parameter or mask.
        """
        if self.get_driver() == "ESRI Shapefile":
            print("Using ESRI Shapefile driver, truncating name to 6 characters.")
            return name[:6]
        else:
            return name

    def config_files(self, rast, mask=None):
        """
        Initialize the configuration with file paths for parameters and masks.

        Args:
            param: Dictionary of parameter names and their file paths.
            mask: Dictionary of mask names and their file paths.
        """

        # for key, value in rast.items():
        #     if isinstance(value, tuple) and len(value) == 2:
        #         # If the value is a tuple, assume it's a file path and thresholds
        #         param_file_dicts[key] = (value[0], value[1])

        self.init_parameters(rast, mask)

    def config_yaml(self):
        """
        Initialize the configuration from a YAML file.

        Args:
            yaml_file (str): Path to the YAML configuration file.
            process (str): Name of the process to set as current.
        """

        param_file_dicts = self.get_nested(
            "processes", self.process, "thresholds", "parameters", default={}
        )
        mask_file_dicts = self.get_nested(
            "processes", self.process, "thresholds", "masks", default={}
        )

        self.init_parameters(param_file_dicts, mask_file_dicts)

    def init_parameters(self, param_file_dicts, mask_file_dicts):
        """
        Initialize the parameters based on the process configuration.

        Args:
            process: The process configuration dictionary.

        Returns:
            List: A list of Parameter objects initialized with the raster data.
        """
        # param_file_dicts = self.get_file_paths(self.get_param_names())
        # mask_file_dicts = self.get_file_paths(self.get_mask_names())

        param_list = []
        for param_name, parameters in param_file_dicts.items():
            param = Parameter(
                name=param_name,
                raster_path=parameters[0],
                thresholds=parameters[1] if len(parameters) > 1 else None,
            )
            param_list.append(param)

        if mask_file_dicts is not None:
            for mask_name, parameters in mask_file_dicts.items():
                mask_param = Parameter(
                    mask_name,
                    raster_path=parameters[0],
                    thresholds=parameters[1] if len(parameters) > 1 else None,
                )
                mask_param.mask = True
                param_list.append(mask_param)

        self.params = param_list

    def get_file_paths(self, names):
        """
        Returns the file path of the parameter raster or paths for indicators.
        """
        files = os.listdir(self.get_dir_path())
        files_dict = {}

        for param in names:
            file_path = self._find_file(files, param)
            if file_path:
                files_dict[param] = file_path
            else:
                print(f"File for parameter {param} not found in {self.get_dir_path()}")

        return files_dict

    def _find_file(self, files, param):
        """
        Helper function to find the file for a given parameter in the directory.
        """
        pattern = re.compile(rf".*{param}.*\.IMG$")
        for f in files:
            match = pattern.match(f)
            if match:
                return os.path.join(self.get_dir_path(), f)
        return None

    def load_config(self):
        """Load the configuration from the YAML file."""
        if self._config is None:
            with open(self.yaml_file, "r") as file:
                self._config = yaml.safe_load(file)
        # Set top-level keys as attributes for convenience
        if self.process:
            self.set_current_process(self.process)
        #     configuration = self._config.get(self.process, {})
        # else:
        #     configuration = self._config
        # for key, value in configuration.items():
        #     setattr(self, key, value)

    def get(self, key, default=None):
        """Get a top-level config value by key."""
        return self._config.get(key, default)

    def get_nested(self, *keys, default=None):
        """
        Get a nested config value by a sequence of keys.
        Example: config.get_nested('processes', 'my_process', 'thresholds')
        """
        value = self._config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def get_processes(self):
        """Return the processes dictionary from the config."""
        return self._config.get("processes", {})

    def get_current_process(self):
        """Get the current process configuration."""
        if self.curr_process is None:
            raise ValueError("Current process is not set.")
        processes = self.get_processes()
        if self.curr_process not in processes:
            raise ValueError(
                f"Process '{self.curr_process}' not found in configuration."
            )
        return processes[self.curr_process]

    def get_median_config(self):
        """Get the median configuration from the config."""
        if self.curr_process is None:
            raise ValueError("Current process is not set.")

        return self.get_nested(
            "processes", self.curr_process, "thresholds", "median", default={}
        )

    def get_masks(self):
        """Get mask names for the current process."""
        if self.curr_process is None:
            raise ValueError("Current process is not set.")
        return self.get_nested(
            "processes", self.curr_process, "thresholds", "masks", default={}
        )

    def get_pipeline(self):
        """Get the pipeline steps for the current process."""
        if self.curr_process is None:
            raise ValueError("Current process is not set.")
        return self.get_nested("processes", self.curr_process, "pipeline", default=[])

    def get_dir_path(self):
        """Get the directory path for the current process."""
        process = self.get_current_process()
        return process.get("path", "")

    def get_param_names(self):
        """
        Get the parameter names from the current process configuration.
        """
        process = self.get_current_process()
        return list(process["thresholds"]["parameters"].keys())

    def get_mask_names(self):
        """
        Get the mask names from the current process configuration.
        """
        process = self.get_current_process()
        if (
            "masks" not in process["thresholds"]
            or process["thresholds"]["masks"] is None
        ):
            print("No masks found in the process configuration.")
            return []
        return list(process["thresholds"]["masks"].keys())

    def get_task_param(self, task, param_name):
        """
        Get a specific parameter for a task in the current process pipeline.
        """
        if param_name in task:
            return task.get(param_name)
        else:
            return None

    def get_output_path(self):
        """Get the output path for the current process."""
        process = self.get_current_process()
        if self.output_path:
            return self.output_path
        return process["vectorization"].get("output_dict", "")

    def get_driver(self):
        """Get the driver for the current process."""
        if self.driver:
            return self.driver
        process = self.get_current_process()
        return process["vectorization"].get("driver", "pandas")

    def create_output_filename(self):
        """Get the output filename for the current process."""
        driver = self.get_driver()
        if driver == "pandas":
            return None
        extension_map = {"GeoJSON": "geojson", "ESRI Shapefile": "shp", "GPKG": "gpkg"}
        file_extension = extension_map.get(driver)
        if not file_extension:
            raise ValueError(f"Unknown driver: {driver}")

        name = self.get_current_process()["name"] or "output"
        # Simple sanitization: replace spaces with underscores
        safe_name = name.replace(" ", "_")
        return f"{safe_name}_final.{file_extension}"

    def get_output_filename(self):
        """Get the output filename for the current process."""
        if self.output_filename:
            return self.output_filename
        return self.create_output_filename()

    def get_cs(self, crs):
        """Get the coordinate reference system for the current process."""
        from pyproj import CRS

        process = self.get_current_process()
        cs = process["vectorization"].get("cs", None)

        # Create pyproj CRS object from the input CRS
        crs_obj = CRS.from_string(crs) if isinstance(crs, str) else CRS(crs)

        if cs is None or cs == "GCS":
            # Extract the geographic CRS (equivalent to CloneGeogCS)
            if crs_obj.is_projected:
                geogcs = crs_obj.geodetic_crs
                return geogcs.to_wkt()
            else:
                # Already geographic
                return crs_obj.to_wkt()
        elif cs == "PCS":
            # Return the projected CRS (equivalent to CloneProjCS)
            if crs_obj.is_projected:
                return crs_obj.to_wkt()
            else:
                # If it's geographic, we can't extract a projected CRS
                raise ValueError("Cannot extract projected CRS from geographic CRS")
        else:
            return cs

    def get_colormap(self):
        """Get the color map for the current process."""
        process = self.get_current_process()
        return process["vectorization"].get("colormap", None)

    def get_stats(self):
        """Get the statistics configuration for the current process."""
        process = self.get_current_process()
        return process["vectorization"].get("stats", [])

    def get_base_check(self):
        """Check if the current process is set to run in base mode."""
        process = self.get_current_process()
        base_config = process["vectorization"].get("base", None)
        if isinstance(base_config, dict):
            return base_config.get("show", False)

    def get_base_stats(self):
        """Get the base statistics for the current process."""
        process = self.get_current_process()
        if self.get_base_check():
            base_config = process["vectorization"].get("base", None)
            if isinstance(base_config, dict):
                return base_config.get("stats", [])
        return []

    def get_simplification_level(self):
        """Get the simplification level for vectorization."""
        process = self.get_current_process()
        simplify = process["vectorization"].get("simplify", 0)
        return simplify

    # Setters
    def set_current_process(self, process_name):
        """Set the current process name."""
        if process_name in self.get_processes():
            self.curr_process = process_name
        else:
            raise ValueError(f"Process '{process_name}' not found in configuration.")


class Parameter:
    def __init__(
        self,
        name: str,
        raster_path=None,
        array=None,
        crs=None,
        transform=None,
        thresholds=None,
    ):
        self.name = name
        self.raster_path = raster_path
        self.mask = False
        self.dataset = None
        self.crs = None
        self.transform = None
        self.raster = self.init_raster(raster_path, array, crs, transform)
        self.thresholds = self.config_thresholds(thresholds)

    def init_raster(self, raster_path=None, array=None, crs=None, transform=None):
        """Initialize the raster data from a file or an array."""
        if raster_path:
            # dataset = gdal.Open(raster_path)
            # band = dataset.GetRasterBand(1)
            # band_array = band.ReadAsArray()
            # self.crs = dataset.GetProjection()
            # self.transform = dataset.GetGeoTransform()
            # if band.GetNoDataValue() is not None:
            #     nodata = band.GetNoDataValue()
            #     band_array[band_array == nodata] = np.nan
            # self.dataset = dataset
            with rio.open(raster_path) as src:
                band_array = src.read(1, masked=True).filled(np.nan)
                self.crs = src.crs
                self.transform = src.transform
                self.dataset = src
            return band_array

        elif array is not None:
            if crs is None or transform is None:
                raise ValueError(
                    "Both crs and transform must be provided when using an array."
                )
            if hasattr(transform, "to_gdal"):
                transform = transform.to_gdal()
            if hasattr(crs, "to_wkt"):
                crs = crs.to_wkt()
            self.crs = crs  # if crs is not None else cfg.Config().get('default_crs')
            self.transform = transform  # if transform is not None else cfg.Config().get('default_transform')

            # path = FileHandler().create_temp_file(prefix=self.name, suffix='tif')
            # self.raster_path = save_raster_gdal(array, crs, transform, path)
            return array

        else:
            raise ValueError(
                "Either raster_path or array with crs and transfrom must be provided."
            )

    def median_filter(self, size=3, iterations=1):
        """Apply a median filter to the raster data."""
        return dask_nanmedian_filter(
            self.raster, window_size=size, iterations=iterations
        )

    def threshold(self, raster=None, thresholds=None):
        """Apply thresholds to the raster data and return a list."""
        if raster is None:
            raster = self.raster
        if thresholds is None:
            thresholds = self.thresholds
        return full_threshold(raster, thresholds)

    def coverage_mask(self):
        """Calculate the coverage mask for the parameter (True where raster is not NaN)."""
        return ~np.isnan(self.raster)

    def get_transform(self):
        """Return the affine transform of the raster dataset."""
        return self.transform

    def get_crs(self):
        """Return the coordinate reference system of the raster dataset."""
        return self.crs

    def get_thresholds(self):
        """Return the thresholds for the parameter."""
        return self.thresholds

    def get_raster(self):
        """Return the raster data."""
        if self.raster is None:
            raise ValueError("Raster data is not initialized.")
        return self.raster

    def set_thresholds(self, thresholds):
        """Set the thresholds for the parameter."""
        if isinstance(thresholds, list):
            self.thresholds = thresholds
        else:
            raise ValueError("Thresholds must be a list.")

    def config_thresholds(self, thresholds):
        """Configure the thresholds for the parameter."""
        return get_raster_thresholds(self.raster, thresholds)

    def release(self):
        """Release the raster dataset."""
        self.raster = None
        self.dataset = None
        self.mask = None


class FileHandler:
    """
    A singleton class to handle file operations for mineral mapping.
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(FileHandler, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.temp_dir = tempfile.mkdtemp()
        self._initialized = True

    def create_temp_file(self, prefix, suffix):
        """
        Create a temporary file in the temporary directory.

        Args:
            prefix (str): Prefix for the temporary file name.
            suffix (str): Suffix for the temporary file name.

        Returns:
            str: Path to the created temporary file.
        """
        file_path = os.path.join(self.temp_dir, f"{prefix}_temp.{suffix}")
        return file_path

    def get_directory(self):
        """
        Get the path to the temporary directory.

        Returns:
            str: Path to the temporary directory.
        """
        return self.temp_dir

    def cleanup(self):
        """
        Clean up the temporary directory and its contents.
        """
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        self.temp_dir = None
        self._initialized = False


class ProcessingPipeline:
    """
    A class to handle the complete processing pipeline dictated by a YAML file.

    Attributes:
    -----------
        yaml_file (str): The path to the YAML file containing the processing configuration.
    """

    def __init__(self, config):
        self.config = config
        self.crs = None
        self.transform = None
        self.mask = None
        self.indication = False

    def process_file(self):
        """
        Process the parameter or indicator based on the name.
        """
        # for process_name, process in tqdm(self.config.processes.items(), desc="Processing Processes"):
        try:
            FileHandler()
            process = self.config.get_current_process()
            for _ in tqdm(range(1), desc=f"Processing: {process['name']}"):
                param_list = self.config.get_parameters_list()
                processed_rasters = self.process_parameters(param_list)
                return self.vectorize(processed_rasters, param_list)
        finally:
            FileHandler().cleanup()
            print("files cleaned up.")

    def vectorize(self, raster_list, param_list):
        """
        Vectorize the raster data based on the zonal statistics.

        Args:
            process: The process configuration dictionary.
            raster_list: A list of processed raster data.
            zonal_stats: The zonal statistics for the raster data.

        Returns:
            List: A list of vectorized geometries.
        """
        simplification_level = self.config.get_simplification_level()

        driver = self.config.get_driver()
        thresholds = self.assign_thresholds(raster_list, param_list)
        stats_list = self.config.get_stats()

        # # Old vectorization
        # start_old = time.time()
        gdf = list_vectorize(
            raster_list, thresholds, self.crs, self.transform, simplification_level
        )
        # gdf = list_zonal_stats(polygons, param_list, self.crs, self.transform, stats_list)
        # end_old = time.time()
        # print(f"Old vectorization took {end_old - start_old:.2f} seconds")

        # New files based vector/stats
        # gdf = list_raster_to_shape_gdal(raster_list, thresholds, self.crs, self.transform, param_list, stats_list, simplification_level)
        # gdf = list_raster_stats(param_list, raster_list, stats_list, thresholds)

        # colormap = self.config.get_colormap()
        # if colormap:
        #     gdf = self.assign_color(gdf, colormap=colormap)

        # mars_gcs = {
        #     "proj": "longlat",
        #     "a": 3396190,
        #     "rf": 169.894447223612,
        #     "no_defs": True
        # }
        # gdf.set_crs(self.crs, inplace=True)
        # cs = self.config.get_cs(self.crs)
        # projected_gdf = gdf.to_crs(cs)

        if driver == "pandas":
            return gdf

        output_dict = self.config.get_output_path()
        filename = self.config.get_output_filename()
        save_shapefile(gdf, output_dict, filename, driver=driver)
        return None

    def process_parameters(self, param_list):
        """
        Process the raster data based on the configuration.

        Returns:
            List: A list of processed raster data
        """
        raster_list = self.threshold(param_list)

        target_param = param_list[0]
        self.crs = target_param.get_crs()
        self.transform = target_param.get_transform()

        show_rasters = False
        # if show_rasters:
        #     show_raster(raster_list[0], title="threshold- Processed Raster lowest")
        # utils.save_raster(raster_list[0], r"\\lasp-store\home\taja6898\Documents\Mars_Data\T1250_demo_parameters", "MC13_thresholded_0.tif", param_list[0].dataset.profile)
        # boolean filters
        for task in self.config.get_pipeline() if self.config.get_pipeline() else []:
            task_name = task.get("task", "")
            if "majority" in task_name:
                iterations = self.config.get_task_param(task, "iterations")
                size = self.config.get_task_param(task, "size")

                iterations = 1 if iterations is None else iterations
                size = 3 if size is None else size
                raster_list = list_majority_filter(
                    raster_list, iterations=iterations, size=size
                )
                # if show_rasters:
                #     show_raster(raster_list[0], title=f"{task_name} - Processed Raster lowest")

            elif "boundary" in task_name:
                iterations = self.config.get_task_param(task, "iterations")
                size = self.config.get_task_param(task, "size")

                iterations = 1 if iterations is None else iterations
                size = 3 if size is None else size
                raster_list = list_boundary_clean(
                    raster_list, iterations=iterations, radius=size
                )
                # if show_rasters:
                #     show_raster(raster_list[0], title=f"{task_name} - Processed Raster lowest")

            elif "sieve" in task_name:
                threshold = self.config.get_task_param(task, "threshold")
                iterations = self.config.get_task_param(task, "iterations")
                connectedness = self.config.get_task_param(task, "connectedness")

                threshold = 9 if threshold is None else threshold
                iterations = 1 if iterations is None else iterations
                connectedness = 4 if connectedness is None else connectedness

                raster_list = list_sieve_filter(
                    raster_list,
                    iterations=iterations,
                    threshold=threshold,
                    crs=self.crs,
                    transform=self.transform,
                    connectedness=connectedness,
                )
                # if show_rasters:
                #     show_raster(raster_list[0], title=f"{task_name} - Processed Raster lowest")
            elif "open" in task_name:
                iterations = self.config.get_task_param(task, "iterations")
                size = self.config.get_task_param(task, "size")

                iterations = 1 if iterations is None else iterations
                size = 3 if size is None else size
                raster_list = list_binary_opening(
                    raster_list, iterations=iterations, size=size
                )
                # if show_rasters:
                #     show_raster(raster_list[0], title=f"{task_name} - Processed Raster lowest")

        mask = target_param.coverage_mask()
        for i in range(len(raster_list)):
            raster_list[i] = raster_list[i] * mask
        raster_list = list(raster_list)

        base_check = self.config.get_base_check()
        if base_check:
            raster_list.insert(0, mask.astype(np.uint8))
        return raster_list

    def threshold(self, param_list):
        """
        Applies median filter, thresholds, and then masks the data.

        Args:
            process: The process configuration dictionary.
            param_list: A list of Parameter objects initialized with the raster data.

        Returns:
            List: A list of processed raster data at the number of desired intervals.
        """
        param_thresholded_list = []
        masks_thresholded_list = []
        for param in param_list:
            if not isinstance(param, Parameter):
                raise TypeError(f"Expected Parameter object, got {type(param)}")

            # Apply median filter
            median_iterations = self.config.get_median_config().get("iterations", 0)
            median_size = self.config.get_median_config().get("size", 3)

            # preprocessing = param.median_filter(iterations=median_iterations, size=median_size)
            # utils.show_raster(preprocessing, title="median_filter")

            preproccessing = param.median_filter(
                iterations=median_iterations, size=median_size
            )
            # utils.show_raster(test_median, title="new_median_filter")
            # utils.save_raster(median_filter, r"\\lasp-store\home\taja6898\Documents\Code\mineral-mapping\outputs", f"T1250_median_filter_D2300.tif", param.dataset.profile)

            if param.mask:
                masks_thresholded_list.append(
                    param.threshold(preproccessing, param.get_thresholds())
                )
            else:
                param_thresholded_list.append(
                    param.threshold(preproccessing, param.get_thresholds())
                )

        # Combine the thresholded rasters
        if len(masks_thresholded_list) > 0 or len(param_thresholded_list) > 1:
            self.indication = True
            param_levels = list(zip(*param_thresholded_list))

            combined_mask = np.logical_not(
                np.logical_or.reduce(masks_thresholded_list)
            ).astype(np.uint32)

            if (
                combined_mask.ndim == 3 and combined_mask.shape[0] == 1
            ):  # Check if alwasy true
                combined_mask = np.squeeze(combined_mask, axis=0)
            # show_raster(combined_mask, title="mask")
            raster_list = [
                np.prod(level_rasters, axis=0) for level_rasters in param_levels
            ]
            # show_raster(raster_list[0], title="threshold - Processed Raster lowest")
            for i in range(len(raster_list)):
                if (
                    raster_list[i].ndim == 3 and raster_list[i].shape[0] == 1
                ):  # Check if alwasy false
                    raster_list[i] = np.squeeze(raster_list[i], axis=0)
                raster_list[i] = raster_list[i] * combined_mask
            # show_raster(raster_list[0], title="threshold - Processed Raster lowest")
            return raster_list

        return param_thresholded_list[0]

    def assign_thresholds(self, raster_list, param_list):
        """
        Assign thresholds to the raster data based on the parameters.

        Args:
            raster_list: A list of processed raster data.
            param_list: A list of Parameter objects initialized with the raster data.

        Returns:
            List: A list of thresholds for each parameter.
        """
        if self.indication:
            size = len(raster_list)
            thresholds = [i + 1 for i in range(size)]
        else:
            thresholds = param_list[0].get_thresholds()

        base_check = self.config.get_base_check()
        if base_check:
            thresholds.insert(0, 0)

        return thresholds

    def assign_color(self, gdf, colormap="viridis"):
        """
        Assign colors to the geometries in the GeoDataFrame based on the thresholds.

        Args:
            gdf: The GeoDataFrame containing the geometries.
            colormap (str): The name of the matplotlib colormap to use for coloring the geometries.

        Returns:
            GeoDataFrame: The input GeoDataFrame with an added 'color' column.
        """

        # thresholds = gdf['Threshold'].unique()
        # cmap = plt.get_cmap(colormap, len(thresholds))
        # color_map = {val: mcolors.to_hex(cmap(i)) for i, val in enumerate(sorted(thresholds))}

        # gdf['hex_color'] = gdf['Threshold'].map(color_map)
        return gdf

    def assign_spatial_info(self, dataset):
        """
        Assigns the spatial information from the dataset to the class attributes.

        Args:
            dataset: The raster dataset to extract spatial information from.
        """
        self.crs = dataset.crs
        self.transform = dataset.transform
        print(f"Assigned CRS: {self.crs}, Transform: {self.transform}")


"""Median Filter"""


def dask_nanmedian_filter(arr, window_size=3, iterations=1):
    dask_arr = da.from_array(arr, chunks=(1024, 1024))  # Adjust chunk size as needed

    for _ in tqdm(range(iterations), desc="Applying Dask nanmedian filter"):
        dask_arr = dask_arr.map_overlap(
            nanmedian_2d,
            window_size=window_size,
            depth=window_size // 2,
            boundary=np.nan,
            dtype=arr.dtype,
        )

    return dask_arr.compute()


def nanmedian_2d(x, window_size):
    """Apply 2D nanmedian filter to a NumPy array with given window size."""
    pad = window_size // 2
    padded = np.pad(x, pad, mode="constant", constant_values=np.nan)

    # Create sliding windows
    windows = np.lib.stride_tricks.sliding_window_view(
        padded, (window_size, window_size)
    )
    windows = windows.reshape(windows.shape[0], windows.shape[1], -1)

    return bn.nanmedian(windows, axis=2)


"""Thresholds"""


def full_threshold(raster, thresholds):
    """Apply multiple thresholds to a raster and return a list of binary arrays."""
    results = []
    for t in tqdm(thresholds, desc="Applying thresholds"):
        result = threshold(raster, t)
        results.append(result)

    return results


def threshold(raster, threshold):
    raster = np.asarray(raster)
    return (raster > threshold).astype(raster.dtype)


"""Majority Filter"""


def list_majority_filter(raster_list, iterations=1, size=3):
    return [
        majority_filter_fast(raster, size=size, iterations=iterations)
        # for raster in raster_list
        for raster in tqdm(raster_list, desc="Applying majority filter")
    ]


def majority_filter_fast(binary_array, size=3, iterations=1):
    kernel = np.ones((size, size), dtype=np.uint8)
    array = np.nan_to_num(binary_array, nan=0).astype(np.uint8)
    threshold = (size * size) // 2

    for _ in range(iterations):
        count = convolve(array, kernel, mode="mirror")
        array = (count > threshold).astype(np.uint8)

    return array


def dask_majority_filter(arr, size=3, iterations=1):
    def majority_func(block):
        return majority_filter_fast(block, size=size, iterations=iterations)

    dask_arr = da.from_array(arr, chunks=(1024, 1024))
    depth = size // 2

    return dask_arr.map_overlap(majority_func, depth=depth, boundary=0).compute()


"""Boundary Clean Filter"""


def list_boundary_clean(raster_list, iterations=1, radius=1):
    return [
        boundary_clean(raster, iterations=iterations, radius=radius)
        # for raster in raster_list
        for raster in tqdm(raster_list, desc="Boundary cleaning")
    ]


def boundary_clean(raster_array, iterations=2, radius=3):
    """
    Smooth binary raster boundaries similar to ArcGIS Boundary Clean tool.

    Parameters:
    - raster_array (np.ndarray): Binary array (1 = feature, 0 = background)
    - iterations (int): How many expand-shrink cycles to perform
    - radius (int): Structuring element size (larger = more aggressive smoothing)

    Returns:
    - np.ndarray: Smoothed binary raster
    """
    result = np.copy(raster_array).astype(np.uint8)
    selem = footprint_rectangle((radius, radius))

    for _ in range(iterations):
        expanded = dilation(result, selem)
        result = erosion(expanded, selem)

    return result


"""Sieve Filter"""


def list_sieve_filter(
    array, crs, transform, iterations=1, threshold=9, connectedness=4
):
    array = np.asarray(array)
    bands, height, width = array.shape
    filtered_array = np.empty_like(array, dtype="uint8")

    # for b in tqdm(range(bands), desc="Applying Sieve Filter"):
    #     array_uint8 = np.nan_to_num(array[b], nan=0).astype("uint8")

    #     src_ds = gdal.GetDriverByName("MEM").Create("", width, height, 1, gdal.GDT_Byte)
    #     src_ds.SetGeoTransform(transform)
    #     src_ds.SetProjection(crs)
    #     src_ds.GetRasterBand(1).WriteArray(array_uint8)

    #     for _ in range(iterations):
    #         dst_ds = gdal.GetDriverByName("MEM").Create("", width, height, 1, gdal.GDT_Byte)
    #         dst_ds.SetGeoTransform(transform)
    #         dst_ds.SetProjection(crs)

    #         gdal.SieveFilter(
    #             srcBand=src_ds.GetRasterBand(1),
    #             maskBand=None,
    #             dstBand=dst_ds.GetRasterBand(1),
    #             threshold=threshold,
    #             connectedness=connectedness
    #         )
    #         src_ds = dst_ds

    #     filtered_array[b] = dst_ds.GetRasterBand(1).ReadAsArray()

    return filtered_array


"""Binary Opening"""


def list_binary_opening(raster_list, iterations, size):
    """
    Apply binary opening to a list of rasters.

    Args:
        raster_list (list of np.ndarray): List of binary rasters.

    Returns:
        list: List of rasters after applying binary opening.
    """
    return [
        _binary_opening(raster, iterations=iterations, size=size)
        for raster in tqdm(raster_list, desc="Applying binary opening")
    ]


def _binary_opening(raster, iterations, size):
    """
    Apply binary opening to a single raster.

    Args:
        raster (np.ndarray): Input binary raster.
        structure (np.ndarray): Structuring element for the opening operation.

    Returns:
        np.ndarray: Raster after applying binary opening.
    """
    if not isinstance(raster, np.ndarray):
        raise ValueError("Input raster must be a NumPy array.")

    structure = footprint_rectangle((size, size))

    return binary_opening(raster, structure=structure, iterations=iterations)


# ===========================================#
# Other
# ===========================================#


def label_clusters(binary_raster, connectivity=1):
    """
    Labels connected regions of 1s in a binary raster.

    Parameters:
    - binary_raster (np.ndarray): 2D array of 0s and 1s.
    - connectivity (int): 1 for 4-connected, 2 for 8-connected (diagonals included).

    Returns:
    - labeled (np.ndarray): Same shape as input, with unique labels for each cluster.
    """
    structure = ndimage.generate_binary_structure(2, connectivity)
    labeled = ndimage.label(binary_raster, structure=structure)[0]
    return labeled


def get_raster_thresholds(raster, thresholds=["75p", "85p", "95p"]):
    """
    Calculate thresholds for a raster based on specified percentiles.

    Args:
        raster (numpy.ndarray): Input raster data.
        percentile_list (list): List of percentiles to calculate thresholds for.

    Returns:
        dict: Dictionary with percentile values as keys and corresponding threshold values as values.
    """
    temp_thresholds = []
    for t in thresholds:
        if isinstance(t, str) and t.endswith("p"):
            p = float(t[:-1])
            temp_thresholds.append(np.nanpercentile(raster, p).round(4))
        elif isinstance(t, (int, float)):
            temp_thresholds.append(t)
        else:
            raise ValueError(
                f"Invalid threshold format: {t}. Must be a number or a string ending with 'p'."
            )

    return temp_thresholds


# def show_raster(raster, cmap='gray', title=None):
#     import matplotlib.pyplot as plt

#     plt.figure(figsize=(8, 6))
#     img = plt.imshow(raster, cmap=cmap)
#     plt.colorbar(img, label='Value')
#     if title:
#         plt.title(title)
#     plt.axis('off')
#     plt.show()


def open_raster(raster_path):
    return rio.open(raster_path)


def open_raster_band(raster, band_number):
    return raster.read(band_number, masked=True).filled(np.nan)


def save_raster(raster, output_path, file_name, profile):
    if not os.path.exists(output_path):
        os.makedirs(output_path)
    full_path = os.path.join(output_path, file_name)
    raster = np.asarray(raster)
    with rio.open(full_path, "w", **profile) as dst:
        # If raster is 2D, add a band axis
        if raster.ndim == 2:
            dst.write(raster, 1)
        else:
            dst.write(raster)


def save_raster_gdal(array, crs, transform, output_path):
    """
    Save a raster array to a file using GDAL.

    Args:
        array (np.ndarray): The raster data to save.
        crs (str): The coordinate reference system in WKT format.
        transform (tuple): The affine transformation parameters.
        output_path (str): The path where the raster will be saved.

    Returns:
        str: The path to the saved raster file.
    """
    # driver = gdal.GetDriverByName('GTiff')
    # height, width = array.shape
    # dataset = driver.Create(output_path, width, height, 1, gdal.GDT_Float32)

    # if dataset is None:
    #     raise IOError(f"Could not create raster file at {output_path}")

    # dataset.SetGeoTransform(transform) # add error handling
    # dataset.SetProjection(crs)

    # dataset.GetRasterBand(1).WriteArray(array)
    # dataset.FlushCache()

    # return output_path


########################
#  Vector Ops
########################


def list_raster_to_shape_gdal(
    raster_list,
    thresholds,
    crs,
    transform,
    param_list,
    stats_list,
    simplification_level=0,
):
    file_paths = []
    for raster, threshold in tqdm(
        zip(raster_list, thresholds), desc="Converting rasters to shapes"
    ):
        vector_file = FileHandler().create_temp_file(
            prefix=f"{threshold}_shapes", suffix="shp"
        )
        raster_to_shape_gdal(
            raster.astype(np.uint8), transform, crs, vector_file, threshold=threshold
        )
        file_paths.append(vector_file)

    gdf = list_file_zonal_stats(
        file_paths, param_list, crs, transform, stats_list, simplification_level
    )

    return gdf


def raster_to_shape_gdal(binary_array, transform, crs_wkt, vector_file, threshold=0):
    """
    Convert a binary numpy array to polygons and save as a shapefile.

    Parameters:
    - binary_array: 2D numpy array (binary mask)
    - transform: affine.Affine transform for the raster
    - crs_wkt: CRS in WKT format
    - vector_file: output shapefile path
    """

    # # Create an in-memory raster from the array
    # driver = gdal.GetDriverByName('MEM')
    # rows, cols = binary_array.shape
    # mem_raster = driver.Create('', cols, rows, 1, gdal.GDT_Byte)
    # mem_raster.SetGeoTransform(transform)
    # mem_raster.SetProjection(crs_wkt)
    # mem_raster.GetRasterBand(1).WriteArray(binary_array.astype(np.uint8))

    # # Prepare shapefile
    # shp_driver = ogr.GetDriverByName('ESRI Shapefile')
    # shp_ds = shp_driver.CreateDataSource(vector_file)
    # srs = osr.SpatialReference()
    # srs.ImportFromWkt(crs_wkt)
    # layer = shp_ds.CreateLayer('layername', srs=srs)
    # field = ogr.FieldDefn('ID', ogr.OFTInteger)
    # layer.CreateField(field)

    # threshold_field = ogr.FieldDefn('Threshold', ogr.OFTReal)
    # layer.CreateField(threshold_field)

    # # Polygonize
    # # Only polygonize where the raster is 1 (not 0)
    # mask_band = mem_raster.GetRasterBand(1)
    # gdal.Polygonize(mask_band, mask_band, layer, 0, [], callback=None)

    # layer.ResetReading()
    # for feature in layer:
    #     feature.SetField('Threshold', float(threshold))
    #     layer.SetFeature(feature)

    # layer = None
    # shp_ds.Destroy()
    # shp_ds = None
    # mem_raster = None
    # mask_band = None


def list_file_zonal_stats(
    path_list, param_list, crs, transform, stats_list, simplification_level=0
):
    """
    Compute zonal statistics for a list of raster files and a list of parameters.

    Args:
        path_list (list): List of file paths to the raster files.
        param_list (list): List of parameters to compute statistics for.
        crs: Coordinate Reference System (e.g., from rasterio).
        transform: Affine transform (e.g., from rasterio).
        stats_list (list): List to store the computed statistics.

    Returns:
        GeoDataFrame with zonal statistics.
    """
    results = gpd.GeoDataFrame()
    x_res = transform[1]
    y_res = abs(transform[5])  # y res is negative for north-up images
    pixel_area = x_res * y_res
    for param in param_list:
        stats_config = config_stats(stats_list, param.name)
        temp = file_zonal_stats(
            path_list,
            param,
            crs,
            transform,
            stats_config,
            pixel_area,
            simplification_level,
        )
        if results.empty:
            results = temp
        else:
            results = results.join(
                temp.set_index(results.index), rsuffix=f"_{param.name}"
            )
            if f"geometry_{param.name}" in results.columns:
                results = results.drop(columns=[f"geometry_{param.name}"])
                results = results.drop(columns=[f"Threshold_{param.name}"])

    return results


def file_zonal_stats(
    path_list, param, crs, transform, stats_config, pixel_area, simplification_level
):
    """
    Compute zonal statistics for a list of raster files and a list of parameters.

    Parameters:
    - path_list: List of file paths to the raster files.
    - param_list: List of parameters to compute statistics for.
    - crs: Coordinate Reference System (e.g., from rasterio).
    - transform: Affine transform (e.g., from rasterio).
    - stats_list: List to store the computed statistics.

    Returns:
    - GeoDataFrame with zonal statistics.
    """
    gdf = gpd.GeoDataFrame()
    # param_name = param.name
    # if len(stats_config) != 0:
    #     if param.raster_path is not None:
    #             base_raster = param.raster_path
    #     else:
    #         base_raster = array_to_gdal(param.raster, transform, crs)
    #     # param.release()

    # with gdal.Open(base_raster) as rast:
    #     # for path in tqdm(path_list, desc=f"Calculating zonal stats for {param.name}"):
    #     #     temp = gpd.read_file(path)
    #     #     gdf = pd.concat([gdf, temp], ignore_index=True)
    #     # if len(stats_config) != 0:
    #     #     # with ogr.Open(path) as vect:
    #     #     # pre_gdf = gpd.read_file(path)
    #     #     # pre_gdf = pre_gdf.dissolve(by='Threshold', as_index=False)

    #     #     temp = exact_extract(
    #     #         rast,
    #     #         gdf,
    #     #         stats_config,
    #     #         include_geom=True,
    #     #         include_cols="Threshold",
    #     #         # strategy="raster-sequential",
    #     #         output='pandas',
    #     #         progress=True,
    #     #         max_cells_in_memory=1000000000
    #     #     )
    #     #     gdf = gpd.GeoDataFrame()
    #     #     gdf = pd.concat([gdf, temp], ignore_index=True)

    #     #     if f"{param_name}_SQK" in gdf.columns:
    #     #         gdf[f"{param_name}_SQK"] = gdf[f"{param_name}_SQK"] * pixel_area * 0.000001  # Convert to square kilometers

    #     #     float_cols = gdf.select_dtypes(include=['float']).columns
    #     #     gdf[float_cols] = gdf[float_cols].round(4)
    #     # else:
    #     #     temp = gpd.read_file(path)
    #     #     gdf = pd.concat([gdf, temp], ignore_index=True)
    #     for path in tqdm(path_list, desc=f"Calculating zonal stats for {param.name}"):
    #         if len(stats_config) != 0:
    #             with ogr.Open(path) as vect:
    #             # pre_gdf = gpd.read_file(path)
    #             # pre_gdf = pre_gdf.dissolve(by='Threshold', as_index=False)
    #                 temp = exact_extract(
    #                     rast,
    #                     vect,
    #                     stats_config,
    #                     include_geom=True,
    #                     include_cols="Threshold",
    #                     # strategy="raster-sequential",
    #                     output='pandas',
    #                     progress=True,
    #                     max_cells_in_memory=10000000000
    #                 )

    #             gdf = pd.concat([gdf, temp], ignore_index=True)

    #             if f"{param_name}_SQK" in gdf.columns:
    #                 gdf[f"{param_name}_SQK"] = gdf[f"{param_name}_SQK"] * pixel_area * 0.000001  # Convert to square kilometers

    #             float_cols = gdf.select_dtypes(include=['float']).columns
    #             gdf[float_cols] = gdf[float_cols].round(4)
    #         else:
    #             temp = gpd.read_file(path)
    #             gdf = pd.concat([gdf, temp], ignore_index=True)

    return gdf


def list_vectorize(raster_list, thresholds, crs, transform, simplify_tol):
    """
    Vectorizes a list of rasters using corresponding threshold values.

    Parameters:
    - raster_list (list of np.ndarray): List of binary rasters.
    - thresholds (list of float or int): Threshold values associated with each raster.
    - crs: Coordinate Reference System (e.g., from rasterio).
    - transform: Affine transform (e.g., from rasterio).
    - simplify_tol: Simplification tolerance in map units.

    Returns:
    - List of GeoDataFrames
    """
    results = [
        # vectorize(raster, threshold, transform, crs, simplify_tol=simplify_tol)
        dask_vectorize(
            raster, transform, crs, threshold=threshold, simplify_tol=simplify_tol
        )
        for raster, threshold in tqdm(
            zip(raster_list, thresholds), desc="Vectorizing", total=len(raster_list)
        )
    ]
    results = combine_polygons(results)
    return results


def vectorize_chunk(chunk, transform, value=1, simplify_tol=0, threshold=None):
    """
    Vectorize a chunk (NumPy array).
    Return a list of GeoJSON-like dicts.
    """
    result = []
    transform = Affine(*transform)  # Ensure it's an Affine

    for geom, val in shapes(chunk.astype("int32"), transform=transform):
        if val == value:
            poly = shape(geom)
            if simplify_tol:
                poly = poly.simplify(simplify_tol, preserve_topology=True)
            feature = {"geometry": poly}
            if threshold is not None:
                feature["Threshold"] = threshold
            result.append(feature)
    return result


def dask_vectorize(
    array,
    transform,
    crs,
    chunk_size=(512, 512),
    value=1,
    simplify_tol=0,
    threshold=None,
):
    """
    Vectorize a large raster using Dask with blockwise vectorization. (256, 256) (512, 512)

    Parameters:
    - array: 2D NumPy array or Dask array
    - transform: Affine transform (rasterio-style)
    - crs: CRS (string, dict, or pyproj.CRS)
    - chunk_size: size of chunks to break the array into
    - value: pixel value to vectorize
    - simplify_tol: simplification tolerance

    Returns:
    - GeoDataFrame with vectorized polygons
    """
    if not isinstance(array, da.Array):
        array = da.from_array(array, chunks=chunk_size)

    results = []
    affine_transform = Affine(
        transform[1],
        transform[2],
        transform[0],
        transform[4],
        transform[5],
        transform[3],
    )
    for i in range(0, array.shape[0], chunk_size[0]):
        for j in range(0, array.shape[1], chunk_size[1]):
            block = array[i : i + chunk_size[0], j : j + chunk_size[1]].compute()
            if np.any(block == value):
                block_transform = affine_transform * Affine.translation(j, i)
                geoms = vectorize_chunk(
                    block, block_transform, value, simplify_tol, threshold
                )
                results.extend(geoms)
    if results:
        return gpd.GeoDataFrame(results)
        # return gpd.GeoDataFrame(results, crs=crs)
    else:
        return gpd.GeoDataFrame()


def save_shapefile(gdf, output_path, file_name, driver="ESRI Shapefile"):
    if not os.path.exists(output_path):
        os.makedirs(output_path)
    file_path = os.path.join(output_path, file_name)
    if os.path.exists(file_path):
        os.remove(file_path)
    gdf.to_file(file_path, driver=driver)


def simplify_raster_geometry(gdf, tolerance):
    """
    Simplify polygons using the GeoSeries.simplify method.

    Parameters:
    - gdf (GeoDataFrame): Vectorized raster polygons
    - tolerance (float): Tolerance for simplification (in CRS units)

    Returns:
    - GeoDataFrame: Simplified polygons
    """
    gdf = gdf.copy()
    gdf["geometry"] = gdf["geometry"].simplify(tolerance, preserve_topology=True)
    return gdf


# =====================================================#
# Attribute Table Operations
# =====================================================#


def combine_polygons(gdf):
    """
    Combine polygons from a list of GeoDataFrames by merging geometries that touch or overlap.
    This helps to reconstruct polygons that were split during tiling.

    Parameters:
    - gdf_list: List of GeoDataFrames

    Returns:
    - GeoDataFrame with merged polygons
    """
    # merged = pd.concat(gdf_list[1:], ignore_index=True)
    if isinstance(gdf, list):
        gdf = pd.concat(gdf, ignore_index=True)
    dissolved = gdf.dissolve(by="Threshold", as_index=False)
    separated = dissolved.explode(index_parts=True)
    # separated = pd.concat([gdf_list[0], separated], ignore_index=True) # Add the first GeoDataFrame (mask) back to the merged result
    cleaned = separated.reset_index(drop=True)
    return cleaned


def list_zonal_stats(polygons, param_list, crs, transform, stats_list):
    """
    Calculate zonal statistics for a list of polygons and parameters.

    Parameters:
    - polygons (list): List of polygon geometries.
    - param_list (list): List of parameters for each polygon.
    - crs: Coordinate reference system.
    - transform: Affine transform for the raster.

    Returns:
    - list: Zonal statistics for each polygon.
    """
    results = []

    x_res = transform[1]
    y_res = abs(transform[5])  # y res is negative for north-up images
    pixel_area = x_res * y_res
    gdf = combine_polygons(polygons[1:])
    # gdf = polygons[0:2]
    results = gpd.GeoDataFrame()
    for param in param_list:
        stats_config = config_stats(
            stats_list, param.name
        )  # Get the configured stats for the parameter

        # raster_path = get_tiled_raster_path(param)
        temp = zonal_stats(
            gdf,
            param.raster,
            param.dataset,
            pixel_area,
            crs,
            transform,
            param.name,
            stats_config,
            param,
        )
        # temp = tiled_zonal_stats(gdf, raster_path, stats_config, tile_size=2048, overlap=100, temp_dir=None, cleanup=True, strategy="raster-sequential")
        if results.empty:
            results = temp
        else:
            results = results.join(
                temp.set_index(results.index), rsuffix=f"_{param.name}"
            )
            if f"geometry_{param.name}" in results.columns:
                results = results.drop(columns=[f"geometry_{param.name}"])
                # results = results.drop(columns=[f"value_{param.name}"])
    return results


def zonal_stats(
    gdf,
    data_raster,
    dataset,
    pixel_area,
    crs,
    transform,
    param_name,
    stats_config,
    param,
):
    """Calculate zonal statistics for a raster and vector layers."""
    if len(stats_config) != 0:
        empty_gdf = gpd.GeoDataFrame()
        # if dataset is not None:
        #     base_raster = dataset
        # else:
        #     base_raster = array_to_gdal(data_raster, transform, crs)
        base_raster = array_to_gdal(data_raster, transform, crs)
        raster_path = None  # get_tiled_raster_path(param)
        param.release()  # Release the raster dataset to avoid memory issues
        # temp = rioxarray_zonal_stats(gdf, raster_path, stat="median")
        temp = exact_extract(
            raster_path,
            gdf,
            stats_config,
            include_geom=True,
            include_cols="Threshold",
            # strategy="raster-sequential",
            output="pandas",
            progress=True,
            max_cells_in_memory=1000000000,  # Adjust as needed for large datasets
        )
        # temp_dir = os.path.dirname(raster_path)
        # shutil.rmtree(temp_dir)
        gdf = pd.concat([empty_gdf, temp], ignore_index=True)

        gdf[f"{param_name}_SQK"] = (
            gdf[f"{param_name}_SQK"] * pixel_area * 0.000001
        )  # Convert to square kilometers

        float_cols = gdf.select_dtypes(include=["float"]).columns
        gdf[float_cols] = gdf[float_cols].round(4)
    return gdf


def config_stats(stats_list, param_name):
    """configure statistics for a list of stats."""
    stat_config = []
    stats_map = {
        "mean": f"{param_name}_MEN=mean",
        "median": f"{param_name}_MDN=median",
        "area": f"{param_name}_SQK=count",
        "count": f"{param_name}_CNT=count",
        "min": f"{param_name}_MIN=min",
        "max": f"{param_name}_MAX=max",
        "std": f"{param_name}_STD=stdev",
    }
    # stats_map = {
    #         'mean': "MEN=mean",
    #         'median': "MDN=median",
    #         'area': "SQK=count",
    #         'count': "CNT=count",
    #         'min': "MIN=min",
    #         'max': "MAX=max",
    #         'std': "STD=stdev",
    #     }
    for stat in stats_list:
        if isinstance(stat, str) and stat.endswith("p"):
            if len(stat) < 2 or not stat[:-1].isdigit():
                raise ValueError(
                    f"Invalid percentile format: {stat}. Must be a number followed by 'p'."
                )
            p = float(stat[:-1])
            stat_config.append(f"Q=quantile(q={p / 100})")
        elif stat in stats_map:
            stat_config.append(stats_map[stat])
        else:
            raise ValueError(
                f"Statistic '{stat}' is not supported. Supported statistics are: {list(stats_map.keys())}"
            )
    return stat_config


def array_to_rasterio(array, transform, crs):
    height, width = array.shape
    memfile = MemoryFile()
    with memfile.open(
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=array.dtype,
        transform=transform,
        crs=crs,
    ) as dataset:
        dataset.write(array, 1)
    return memfile.open()


def array_to_gdal(array, transform, crs):
    """Convert a NumPy array to an in-memory GDAL raster dataset."""
    # height, width = array.shape
    # mem_driver = gdal.GetDriverByName('MEM')
    # dataset = mem_driver.Create('', width, height, 1, gdal.GDT_Float32)
    # dataset.SetGeoTransform(transform)

    # srs = osr.SpatialReference()
    # srs.ImportFromWkt(crs)
    # dataset.SetProjection(srs.ExportToWkt())

    # band = dataset.GetRasterBand(1)
    # band.WriteArray(array)
    # band.FlushCache()
    # return dataset
    return


def list_raster_stats(param_list, raster_list, stats, thresholds):
    for param in param_list:
        base_raster = param.get_raster()
        for raster, threshold in zip(raster_list, thresholds):
            # print(f"Calculating zonal stats for {param.name} with threshold {threshold}")
            labeled_raster = label_clusters(raster)
            results = scipy_zonal_stats(base_raster, labeled_raster, stats)


def scipy_zonal_stats(base_raster, labeled_raster, stats):
    """
    Calculate zonal statistics using SciPy for a GeoDataFrame and a raster file.

    Parameters:
    - gdf (GeoDataFrame): GeoDataFrame with polygon geometries.
    - raster_path (str): Path to the raster file.
    - stat (str): Statistic to calculate ('mean', 'median', 'min', 'max', 'std').
    """
    # Get the unique labels from the labeled raster
    unique_labels = np.arange(0, labeled_raster.max() + 1)
    unique_labels = unique_labels[unique_labels != 0]

    # Initialize a dictionary to hold the results
    results = {label: {} for label in unique_labels}
    for stat in tqdm(stats, desc="Calculating statistics"):
        if stat == "mean":
            values = ndimage.mean(
                base_raster, labels=labeled_raster, index=unique_labels
            )
        elif stat == "count":
            values = region_count(labels=labeled_raster, index=unique_labels)
        elif stat == "min":
            values = ndimage.labeled_comprehension(
                base_raster, labeled_raster, unique_labels, np.nanmin, float, np.nan
            )
        elif stat == "max":
            values = ndimage.labeled_comprehension(
                base_raster, labeled_raster, unique_labels, np.nanmax, float, np.nan
            )
        elif stat == "std":
            values = ndimage.standard_deviation(
                base_raster, labels=labeled_raster, index=unique_labels
            )
        elif stat == "median":
            values = ndimage.labeled_comprehension(
                base_raster, labeled_raster, unique_labels, bn.nanmedian, float, np.nan
            )
            # values = ndimage.labeled_comprehension(
            #     base_raster, labeled_raster, unique_labels, lambda x: np.nanpercentile(x, 50), float, 0
            # )
        elif stat.endswith("p") and stat[:-1].isdigit():
            q = float(stat[:-1])
            values = ndimage.labeled_comprehension(
                base_raster,
                labeled_raster,
                unique_labels,
                lambda x: np.nanpercentile(x, q),
                float,
                0,
            )
        else:
            print(f"Statistic '{stat}' is not supported. Skipping.")

        for label, value in zip(unique_labels, values):
            results[label][stat] = value

    return results


def region_count(labels, index):
    counts = np.bincount(labels.ravel())
    # Handle case where some indices might be larger than max label
    result = np.zeros(len(index), dtype=int)
    valid_mask = index < len(counts)
    result[valid_mask] = counts[index[valid_mask]]
    return result
