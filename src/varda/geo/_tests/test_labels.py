"""Label hints from PDS3, PDS4 and ISIS labels, written to ``tmp_path``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import numpy as np
import pytest
import rasterio as rio
from affine import Affine

from varda.common.entities import VardaRaster
from varda.common.provenance import inputRecord, makeProvenance
from varda.geo.labels import LabelHints, labelHints, provenanceHints
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.data_sources.data_source import DataSource
from varda.image_loading.data_sources.delegating_data_source import (
    DelegatingDataSource,
)
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
          <cart:a_axis_radius unit="km">1737.4</cart:a_axis_radius>
          <cart:c_axis_radius>1737400</cart:c_axis_radius>
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


def test_pds4_keeps_each_cart_unit() -> None:
    group = labelHints(None, {"xml:PDS4": {"xml:PDS4": PDS4_LABEL}}).projectionGroup
    assert group["A_AXIS_RADIUS"] == "1737.4"
    assert group["A_AXIS_RADIUS_UNIT"] == "km"
    assert group["WEST_BOUNDING_COORDINATE_UNIT"] == "deg"
    assert "C_AXIS_RADIUS_UNIT" not in group  # no unit attribute: none kept


def test_pds3_and_isis_keep_units_beside_values(tmp_path: Path) -> None:
    label = tmp_path / "x.lbl"
    label.write_text(
        "PDS_VERSION_ID = PDS3\nOBJECT = IMAGE_MAP_PROJECTION\n"
        '  A_AXIS_RADIUS = "3376.2" <KM>\n  MAP_SCALE = 0.25 <METERS/PIXEL>\n'
        "  CENTER_LATITUDE = 90.0\n"
        "END_OBJECT = IMAGE_MAP_PROJECTION\nEND\n"
    )
    group = labelHints(str(label)).projectionGroup
    assert group["A_AXIS_RADIUS"] == "3376.2"
    assert group["A_AXIS_RADIUS_UNIT"] == "KM"
    assert group["MAP_SCALE"] == "0.25"
    assert group["MAP_SCALE_UNIT"] == "METERS/PIXEL"
    assert "CENTER_LATITUDE_UNIT" not in group
    isis = {
        "IsisCube": {
            "Mapping": {"PixelResolution": {"value": 18.0, "unit": "meters/pixel"}}
        }
    }
    hints = labelHints(None, {"json:ISIS3": {"json:ISIS3": json.dumps(isis)}})
    assert hints.projectionGroup["PIXELRESOLUTION"] == "18.0"
    assert hints.projectionGroup["PIXELRESOLUTION_UNIT"] == "meters/pixel"


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
def test_broken_metadata_never_raises(metadata: dict[str, dict[str, str]]) -> None:
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


def test_odl_value_on_next_line_and_units_after_quotes(tmp_path: Path) -> None:
    label = tmp_path / "x.lbl"
    label.write_text(
        "PDS_VERSION_ID = PDS3\nTARGET_NAME =\n  MARS\n"
        "OBJECT = MAP_PROJECTION\n"
        '  A_AXIS_RADIUS = "3396.19" <KM>\n'
        "  POSITIVE_LONGITUDE_DIRECTION =\n    WEST\n"
        "END_OBJECT = MAP_PROJECTION\nEND\n"
    )
    hints = labelHints(str(label))
    assert hints.target == "Mars"
    assert hints.longitudeDirection == "west"
    assert hints.projectionGroup["A_AXIS_RADIUS"] == "3396.19"


def test_sfdu_attached_label(tmp_path: Path) -> None:
    image = tmp_path / "old.img"
    image.write_bytes(
        b"NJPL1I00PDS10000000 = SFDU_LABEL\r\nTARGET_NAME = MOON\r\nEND\r\n"
        + b"\xff" * 128
    )
    assert labelHints(str(image)).target == "Moon"


def test_attached_label_read_only_within_64_kib(tmp_path: Path) -> None:
    image = tmp_path / "long.img"
    padding = "".join(f"KEY_{i} = {i}\n" for i in range(8000))  # > 64 KiB
    image.write_text(f"PDS_VERSION_ID = PDS3\n{padding}TARGET_NAME = MARS\nEND\n")
    hints = labelHints(str(image))
    assert hints.target is None
    assert "attached" in hints.source


def test_non_ascii_bytes_in_attached_label(tmp_path: Path) -> None:
    image = tmp_path / "accents.img"
    image.write_bytes(
        b"PDS_VERSION_ID = PDS3\n"
        b'PRODUCER_FULL_NAME = "Jos\xc3\xa9 \x80\xfe"\n'
        b"TARGET_NAME = MARS\nEND\n" + bytes(range(256))
    )
    assert labelHints(str(image)).target == "Mars"


def test_unlistable_folder_still_reads_attached_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "ESP_0001.IMG"
    image.write_bytes(HIRISE_LABEL.encode())

    def refuse(self: Path) -> bool:
        raise PermissionError("no listing")

    monkeypatch.setattr(Path, "is_file", refuse)
    assert labelHints(str(image)).longitudeDomain == "0..360"


class _PluginSource:
    """A plugin's data source: DataSource implemented without subclassing it,
    and without the newer CRS members."""

    filePath = None


def test_delegating_source_defaults_for_plugin_sources() -> None:
    wrapped = DelegatingDataSource(cast(DataSource, _PluginSource()))
    assert wrapped.rawCrsText is None
    assert wrapped.crsError is None
    assert wrapped.gdalMetadata() == {}


# --- Varda provenance: the target of the images a result was made from ---


def test_provenance_download_record_names_the_target() -> None:
    hints = provenanceHints({"source": {"catalog": "PDS ODE", "target": "MARS"}})
    assert hints.target == "Mars"
    assert hints.source == "Varda provenance (downloaded from PDS ODE)"


def test_provenance_inputs_name_the_target() -> None:
    record = {
        "process": "Band Math",
        "inputs": [
            {"name": "a", "labelHints": {"target": "Mars"}},
            {
                "name": "b",
                "provenance": {"inputs": [{"labelHints": {"target": "Mars"}}]},
            },
            {"name": "c"},
        ],
    }
    hints = provenanceHints(record)
    assert hints.target == "Mars"
    assert hints.source == "Varda provenance (Band Math)"
    # only the body: the result's own coordinates may differ from its inputs'
    assert hints.latitudeType is None and hints.longitudeDirection is None


def test_provenance_inputs_on_different_bodies_name_none() -> None:
    record = {
        "inputs": [
            {"labelHints": {"target": "Mars"}},
            {"labelHints": {"target": "Moon"}},
        ]
    }
    assert provenanceHints(record).target is None


def test_a_label_target_wins_over_provenance() -> None:
    label = LabelHints("Moon", None, "east", None, {}, "PDS3 label x.lbl")
    assert provenanceHints({"source": {"target": "Mars"}}, label) == label


def test_provenance_fills_in_only_the_target() -> None:
    label = LabelHints(
        None, "planetocentric", None, None, {"A": "1"}, "PDS3 label x.lbl"
    )
    hints = provenanceHints({"source": {"target": "Mars"}}, label)
    assert hints.target == "Mars"
    assert hints.latitudeType == "planetocentric"
    assert hints.projectionGroup == {"A": "1"}
    assert hints.source == "PDS3 label x.lbl; target from Varda provenance (downloaded)"


@pytest.mark.parametrize("record", [None, {}, {"inputs": "x"}, {"source": 3}, []])
def test_odd_provenance_names_nothing(record: object) -> None:
    assert provenanceHints(record).target is None


def test_a_result_records_its_inputs_label_facts(tmp_path: Path) -> None:
    path = tmp_path / "FRT000107A7_07_IF166J_MTR3.IMG"
    path.write_bytes(b"")
    (tmp_path / "FRT000107A7_07_IF166J_MTR3.LBL").write_text(CRISM_MTRDR_LABEL)
    data = np.zeros((2, 2, 1), dtype=np.float32)
    image = VardaRaster(ArrayDataSource(data, filePath=str(path)), name="crism")
    entry = inputRecord(image)
    assert entry["labelHints"] == {
        "target": "Mars",
        "latitudeType": "planetocentric",
        "longitudeDirection": "east",
        "longitudeDomain": None,
        "source": "PDS3 label FRT000107A7_07_IF166J_MTR3.LBL",
    }
    record = makeProvenance("Band Math", {}, [image])
    assert provenanceHints(record).target == "Mars"


def test_an_input_without_a_label_records_none() -> None:
    data = np.zeros((2, 2, 1), dtype=np.float32)
    image = VardaRaster(ArrayDataSource(data, filePath="/data/scene.img"), name="s")
    assert "labelHints" not in inputRecord(image)
