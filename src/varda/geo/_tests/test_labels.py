"""Label hints from PDS3, PDS4 and ISIS labels, written to ``tmp_path``."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio as rio
from affine import Affine

from varda.geo.labels import LabelHints, labelHints
from varda.image_loading.data_sources.rasterio_data_source import RasterioDataSource

CRISM_MTRDR_LABEL = """PDS_VERSION_ID                = PDS3
/* A CRISM MTRDR-style label */
TARGET_NAME                   = MARS
PRODUCT_ID                    = "FRT000107A7_07_IF166J_MTR3"
OBJECT                         = IMAGE_MAP_PROJECTION
  ^DATA_SET_MAP_PROJECTION     = "MTR_MAP.CAT"
  MAP_PROJECTION_TYPE          = "EQUIRECTANGULAR"
  A_AXIS_RADIUS                =  3396.19 <KM>
  POSITIVE_LONGITUDE_DIRECTION = EAST
  CENTER_LATITUDE              =  15.0000000 <DEGREE>
  WESTERNMOST_LONGITUDE        =  71.7585420 <DEGREE>
  EASTERNMOST_LONGITUDE        =  72.0371960 <DEGREE>
  COORDINATE_SYSTEM_NAME       = "PLANETOCENTRIC"
  MAP_PROJECTION_DESC          = "A description that runs
                                  over two lines"
END_OBJECT                     = IMAGE_MAP_PROJECTION
OBJECT              = IMAGE
  LINES             = 2
END_OBJECT = IMAGE
END
"""

HIRISE_LABEL = """PDS_VERSION_ID = PDS3
TARGET_NAME = "MARS"
OBJECT = IMAGE_MAP_PROJECTION
  MAP_PROJECTION_TYPE = "EQUIRECTANGULAR"
  COORDINATE_SYSTEM_NAME = PLANETOCENTRIC
  POSITIVE_LONGITUDE_DIRECTION = EAST
  WESTERNMOST_LONGITUDE = 352.1 <DEG>
  EASTERNMOST_LONGITUDE = 352.3 <DEG>
END_OBJECT = IMAGE_MAP_PROJECTION
END
"""

PDS4_LABEL = """<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1"
    xmlns:cart="http://pds.nasa.gov/pds4/cart/v1">
  <Observation_Area>
    <Target_Identification>
      <name>Moon</name>
      <type>Satellite</type>
    </Target_Identification>
    <Discipline_Area>
      <cart:Cartography>
        <cart:Spatial_Domain>
          <cart:Bounding_Coordinates>
            <cart:west_bounding_coordinate unit="deg">-10.5</cart:west_bounding_coordinate>
            <cart:east_bounding_coordinate unit="deg">-9.5</cart:east_bounding_coordinate>
          </cart:Bounding_Coordinates>
        </cart:Spatial_Domain>
        <cart:Geodetic_Model>
          <cart:latitude_type>Planetographic</cart:latitude_type>
          <cart:longitude_direction>Positive West</cart:longitude_direction>
        </cart:Geodetic_Model>
      </cart:Cartography>
    </Discipline_Area>
  </Observation_Area>
</Product_Observational>
"""


def test_pds3_crism_mtrdr_detached_label(tmp_path: Path) -> None:
    image = tmp_path / "FRT000107A7_07_IF166J_MTR3.IMG"
    image.write_bytes(b"\0" * 16)
    (tmp_path / "FRT000107A7_07_IF166J_MTR3.LBL").write_text(CRISM_MTRDR_LABEL)
    hints = labelHints(str(image))
    assert hints.target == "Mars"
    assert hints.latitudeType == "planetocentric"
    assert hints.longitudeDirection == "east"
    # 71.8..72.0 fits both domains
    assert hints.longitudeDomain is None
    assert hints.projectionGroup["MAP_PROJECTION_TYPE"] == "EQUIRECTANGULAR"
    assert hints.projectionGroup["A_AXIS_RADIUS"] == "3396.19"
    assert hints.projectionGroup["WESTERNMOST_LONGITUDE"] == "71.7585420"
    assert hints.projectionGroup["MAP_PROJECTION_DESC"] == (
        "A description that runs over two lines"
    )
    assert "PDS3 label" in hints.source
    assert "FRT000107A7_07_IF166J_MTR3.LBL" in hints.source


def test_pds3_label_is_the_path(tmp_path: Path) -> None:
    label = tmp_path / "x.lbl"
    label.write_text(CRISM_MTRDR_LABEL)
    assert labelHints(str(label)).target == "Mars"


def test_pds3_hirise_longitudes_past_180_are_0_to_360(tmp_path: Path) -> None:
    image = tmp_path / "ESP_0001.IMG"
    image.write_bytes(HIRISE_LABEL.encode() + b"\0\1\2\3" * 64)  # attached
    hints = labelHints(str(image))
    assert hints.target == "Mars"
    assert hints.longitudeDirection == "east"
    assert hints.longitudeDomain == "0..360"


def test_isis_json_metadata() -> None:
    isis = {
        "IsisCube": {
            "Mapping": {
                "ProjectionName": "SimpleCylindrical",
                "TargetName": "Moon",
                "LatitudeType": "Planetocentric",
                "LongitudeDirection": "PositiveWest",
                "LongitudeDomain": 180,
                "EquatorialRadius": {"value": 1737400.0, "unit": "meters"},
            }
        }
    }
    hints = labelHints(None, {"json:ISIS3": {"json:ISIS3": json.dumps(isis)}})
    assert hints.target == "Moon"
    assert hints.longitudeDirection == "west"
    assert hints.latitudeType == "planetocentric"
    assert hints.longitudeDomain == "-180..180"
    assert hints.projectionGroup["PROJECTIONNAME"] == "SimpleCylindrical"
    assert hints.projectionGroup["EQUATORIALRADIUS"] == "1737400.0"
    assert hints.source == "ISIS cube label"


def test_pds4_metadata() -> None:
    hints = labelHints(None, {"xml:PDS4": {"xml:PDS4": PDS4_LABEL}})
    assert hints.target == "Moon"
    assert hints.longitudeDirection == "west"
    assert hints.latitudeType == "planetographic"
    assert hints.longitudeDomain == "-180..180"
    assert hints.projectionGroup["LONGITUDE_DIRECTION"] == "Positive West"
    assert hints.source == "PDS4 label"


def test_unknown_target_is_kept_as_written(tmp_path: Path) -> None:
    label = tmp_path / "x.lbl"
    label.write_text("PDS_VERSION_ID = PDS3\nTARGET_NAME = BENNU\nEND\n")
    assert labelHints(str(label)).target == "BENNU"


def test_garbage_is_empty(tmp_path: Path) -> None:
    junk = tmp_path / "junk.img"
    junk.write_bytes(np.random.default_rng(0).bytes(4096))
    hints = labelHints(str(junk))
    assert hints == LabelHints.empty(hints.source)
    assert hints.target is None and hints.projectionGroup == {}
    assert hints.source


@pytest.mark.parametrize(
    "metadata",
    [
        {"xml:PDS4": {"xml:PDS4": "<not xml"}},
        {"json:ISIS3": {"json:ISIS3": "{not json"}},
        {"json:ISIS3": {"json:ISIS3": "[1, 2]"}},
    ],
)
def test_broken_metadata_never_raises(metadata) -> None:
    hints = labelHints(None, metadata)
    assert hints.target is None
    assert hints.source


def test_missing_file_is_empty(tmp_path: Path) -> None:
    hints = labelHints(str(tmp_path / "nowhere.img"))
    assert hints.target is None and hints.source


def test_no_path_no_metadata_is_empty() -> None:
    assert labelHints(None) == LabelHints.empty(labelHints(None).source)


class _BrokenCrs:
    def to_wkt(self) -> str:
        return 'GEOGCS["broken",DATUM[nonsense'


class _ReaderWithBrokenCrs:
    """A dataset reader whose CRS PROJ can't parse."""

    def __init__(self, reader: rio.DatasetReader) -> None:
        self._reader = reader

    @property
    def crs(self) -> _BrokenCrs:
        return _BrokenCrs()

    def __getattr__(self, name: str):
        return getattr(self._reader, name)


def _geotiff(path: Path) -> str:
    with rio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=Affine(1, 0, 0, 0, -1, 4),
    ) as dst:
        dst.write(np.zeros((1, 4, 4), dtype=np.float32))
    return str(path)


def test_rasterio_source_keeps_raw_crs_text_and_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = RasterioDataSource(_geotiff(tmp_path / "broken.tif"))
    monkeypatch.setattr(source, "_src", _ReaderWithBrokenCrs(source._src))
    assert source.crs is None
    assert source.rawCrsText == 'GEOGCS["broken",DATUM[nonsense'
    assert source.crsError
    source.close()


def test_rasterio_source_good_crs(tmp_path: Path) -> None:
    source = RasterioDataSource(_geotiff(tmp_path / "good.tif"))
    assert source.crs is not None
    assert source.crsError is None
    assert source.rawCrsText and "WGS 84" in source.rawCrsText
    assert source.gdalMetadata() == {}
    source.close()
