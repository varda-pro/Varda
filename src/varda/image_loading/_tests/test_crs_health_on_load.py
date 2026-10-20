"""The coordinate-system health check runs when an image opens: its result is
kept with the image, and the one automatic fix (Earth degrees on a body the
label names) becomes the image's CRS override, recorded as ``crsFix``."""

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.common.provenance import inputRecord
from varda.geo._tests.fixtures import (
    GDAL_WEST_PROJ,
    GDAL_WEST_TRANSFORM,
    HIRISE_LABEL_GROUP,
    HIRISE_LABEL_TRANSFORM,
    HIRISE_POLAR_PROJ,
    WEST_LABEL_GROUP,
    writePds3,
    writeWgs84,
)
from varda.geo.equivalence import sameCrs
from varda.geo.health import LABEL_FIX_HEADLINE, Severity, headline
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.crs_check import (
    applyCrsFix,
    checkImageCrs,
    chooseCrs,
    undoCrsFix,
)
from varda.image_loading.data_sources.rasterio_data_source import RasterioDataSource
from varda.image_loading.image_loading_service import loadRaster
from varda.image_loading.raster_writer import writeRaster

REASON = (
    "Its CRS says Earth (WGS 84) but its label's target is Mars; read as "
    "degrees on the IAU 2015 Mars sphere"
)


def test_earth_degrees_on_mars_are_read_as_mars(tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    assert image.crs is not None and sameCrs(image.crs, "IAU_2015:49900")
    assert image.crsHealth is not None
    assert image.crsHealth.issues[0].code == "earth-crs-on-body"
    body = image.crsHealth.body.body
    assert body is not None and body.name == "Mars"
    assert image.extraMetadata["crsFix"] == {
        "from": "EPSG:4326",
        "to": "IAU_2015:49900",
        "reason": REASON,
        "automatic": True,
    }
    assert sameCrs(image.metadataOverrides["crs"], "IAU_2015:49900")
    assert image.labelHints is not None and image.labelHints.target == "Mars"


def test_the_degrees_are_unchanged(tmp_path):
    path = str(writeWgs84(tmp_path))
    fixed = loadRaster(path)
    (tmp_path / "plain").mkdir()
    plain = loadRaster(str(writeWgs84(tmp_path / "plain", label=None)))
    for col, row in [(0, 0), (7, 3), (15, 15)]:
        assert fixed.pixelToLatLon(col, row) == pytest.approx(
            plain.pixelToLatLon(col, row), abs=1e-12
        )
    lat, lon = fixed.pixelToLatLon(0, 0) or (None, None)
    assert lon == pytest.approx(137.3 + 0.5 / 256)
    assert lat == pytest.approx(-4.5 - 0.5 / 256)


def test_without_a_label_nothing_changes(tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path, label=None)))
    assert image.crs is not None and image.crs.to_epsg() == 4326
    assert image.crsHealth is not None and image.crsHealth.issues == ()
    assert "crsFix" not in image.extraMetadata
    assert image.metadataOverrides == {}


@pytest.mark.parametrize("target", ["SKY", "N/A", "CALIBRATION"])
def test_a_target_that_isnt_a_body_changes_nothing(tmp_path, target):
    label = f'PDS_VERSION_ID = PDS3\nTARGET_NAME = "{target}"\nEND\n'
    image = loadRaster(str(writeWgs84(tmp_path, label=label)))
    assert image.crsHealth is not None and image.crsHealth.issues == ()
    assert image.metadataOverrides == {}


def test_a_projected_earth_crs_is_flagged_not_fixed(tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path, crs="EPSG:4087")))
    assert image.crsHealth is not None
    assert [i.code for i in image.crsHealth.issues][0] == "earth-crs-on-body"
    assert image.crs is not None and image.crs.to_epsg() == 4087
    assert "crsFix" not in image.extraMetadata


def test_a_crs_chosen_already_is_kept_and_checked(tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    image.setMetadataOverrides({"crs": "IAU_2015:30100"})  # the Moon, say
    health = checkImageCrs(image)
    assert image.metadataOverrides == {"crs": "IAU_2015:30100"}
    assert health.body.body is not None and health.body.body.name == "Moon"
    assert "crsFix" not in image.extraMetadata  # not the automatic fix


def test_the_files_own_crs_chosen_explicitly_stays(tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    image.setMetadataOverrides({"crs": None})
    health = checkImageCrs(image)
    assert image.crs is not None and image.crs.to_epsg() == 4326
    assert health.issues[0].code == "earth-crs-on-body"
    assert "crsFix" not in image.extraMetadata
    # the health is of the CRS in use: Earth, with the fix offered again
    assert health.body.body is not None and health.body.body.name == "Earth"
    assert health.description.authority == "EPSG:4326"
    assert "you chose to keep the file's CRS" in health.issues[0].message
    fix = health.issues[0].fix
    assert fix is not None and not fix.automatic


def test_a_named_body_varda_does_not_know_is_flagged_not_fixed(tmp_path):
    label = 'PDS_VERSION_ID = PDS3\nTARGET_NAME = "BENNU"\nEND\n'
    image = loadRaster(str(writeWgs84(tmp_path, label=label)))
    assert image.crsHealth is not None
    (issue,) = image.crsHealth.issues
    assert issue.code == "earth-crs-on-body" and issue.fix is None
    assert issue.message == "Its CRS says Earth, but its label says Bennu."
    assert image.metadataOverrides == {}
    assert image.crs is not None and image.crs.to_epsg() == 4326


def test_choosing_a_crs_on_a_reopened_export_drops_its_old_record(tmp_path):
    fixed = loadRaster(str(writeWgs84(tmp_path)))
    reopened = loadRaster(str(writeRaster(fixed, tmp_path / "copy.tif")))
    assert "crsFix" in reopened.extraMetadata
    reopened.setMetadataOverrides({"crs": "IAU_2015:30100"})
    checkImageCrs(reopened)
    assert "crsFix" not in reopened.extraMetadata


def test_an_input_record_uses_the_label_facts_read_at_load(tmp_path, monkeypatch):
    image = loadRaster(str(writeWgs84(tmp_path)))
    calls = []
    monkeypatch.setattr(
        "varda.common.provenance.labelHints", lambda *a: calls.append(a)
    )
    entry = inputRecord(image)
    assert calls == []  # no label read on the GUI thread
    assert entry["labelHints"]["target"] == "Mars"
    assert entry["crsFix"]["to"] == "IAU_2015:49900"


def test_an_image_made_in_varda_is_checked_too():
    image = VardaRaster(ArrayDataSource(np.zeros((4, 4, 1), dtype=np.float32)))
    health = checkImageCrs(image)
    assert image.crsHealth is health and health.issues == ()


def test_a_crs_chosen_by_hand_is_recorded_not_automatic(tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    health = chooseCrs(image, "IAU_2015:49910", "Its producer's projection")
    assert sameCrs(image.crs, "IAU_2015:49910") and image.crsHealth is health
    assert image.extraMetadata["crsFix"] == {
        "from": "EPSG:4326",
        "to": "IAU_2015:49910",
        "reason": "Its producer's projection",
        "automatic": False,
    }


def test_undoing_the_fix_keeps_the_files_crs_and_says_so(tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    health = undoCrsFix(image)
    assert image.metadataOverrides["crs"] is None
    assert sameCrs(image.crs, "EPSG:4326")
    issue = health.issues[0]
    assert issue.code == "earth-crs-on-body" and issue.fix is not None
    assert not issue.fix.automatic  # offered again
    assert image.extraMetadata["crsFix"] == {
        "from": "EPSG:4326",
        "to": "IAU_2015:49900",
        "reason": REASON,
        "automatic": True,
        "undone": True,
    }


def test_applying_the_declined_fix_records_it_as_chosen(tmp_path):
    image = loadRaster(str(writeWgs84(tmp_path)))
    fix = undoCrsFix(image).issues[0].fix
    assert fix is not None
    chooseCrs(image, fix.wkt, fix.reason)
    assert sameCrs(image.crs, "IAU_2015:49900")
    assert image.extraMetadata["crsFix"] == {
        "from": "EPSG:4326",
        "to": "IAU_2015:49900",
        "reason": REASON,
        "automatic": False,
    }


def test_a_crs_chosen_for_an_image_without_one():
    image = VardaRaster(ArrayDataSource(np.zeros((4, 4, 1), np.float32)), name="x")
    chooseCrs(image, "IAU_2015:49900", "Same as other.tif")
    assert sameCrs(image.crs, "IAU_2015:49900")
    assert image.extraMetadata["crsFix"]["from"] == "none"


# --- CRSs built from the label (spec §4.8) ---


@pytest.fixture
def unreadByGdal(monkeypatch):
    """Files open with no CRS and the identity transform: GDAL 3.9.3 builds
    the HiRISE label's CRS itself, so this stands in for a product it can't."""
    for name in ("crs", "rawCrsText"):
        monkeypatch.setattr(RasterioDataSource, name, property(lambda self: None))
    monkeypatch.setattr(
        RasterioDataSource, "transform", property(lambda self: Affine.identity())
    )


def _westAxis(crs: CRS | str) -> bool:
    geodetic = CRS.from_user_input(crs).geodetic_crs
    return geodetic is not None and any(
        axis.direction == "west" for axis in geodetic.axis_info
    )


def test_an_image_without_a_crs_gets_its_labels(tmp_path, unreadByGdal):
    image = loadRaster(str(writePds3(tmp_path, HIRISE_LABEL_GROUP)))
    assert image.hasGeospatialData
    assert sameCrs(image.crs, HIRISE_POLAR_PROJ)
    assert tuple(image.transform)[:6] == pytest.approx(HIRISE_LABEL_TRANSFORM)
    overrides = image.metadataOverrides
    assert sameCrs(overrides["crs"], HIRISE_POLAR_PROJ)
    assert overrides["transform"] == pytest.approx(list(HIRISE_LABEL_TRANSFORM))
    record = image.extraMetadata["crsFix"]
    assert record["from"] == "none" and record["source"] == "label"
    assert record["automatic"] is True
    assert record["transform"] == pytest.approx(list(HIRISE_LABEL_TRANSFORM))
    assert headline(image.crsHealth) == LABEL_FIX_HEADLINE
    # the pixel grid is the label's: pixel (0, 0) is 120 km from the pole
    lat, lon = image.pixelToLatLon(0, 0) or (None, None)
    assert lat is not None and 87 < lat < 88


def test_a_label_fix_is_checked_again_unchanged(tmp_path, unreadByGdal):
    image = loadRaster(str(writePds3(tmp_path, HIRISE_LABEL_GROUP)))
    record = dict(image.extraMetadata["crsFix"])
    health = checkImageCrs(image)  # e.g. after a session restored it
    assert image.extraMetadata["crsFix"] == record
    assert health.issues[0].code == "no-crs" and health.issues[0].fix is not None
    assert health.issues[0].fix.automatic


def test_undoing_a_label_fix_removes_both_overrides(tmp_path, unreadByGdal):
    image = loadRaster(str(writePds3(tmp_path, HIRISE_LABEL_GROUP)))
    record = dict(image.extraMetadata["crsFix"])
    health = undoCrsFix(image)
    assert image.metadataOverrides == {"crs": None}
    assert not image.hasGeospatialData and image.transform.is_identity
    assert image.extraMetadata["crsFix"] == {**record, "undone": True}
    (issue,) = health.issues
    assert issue.code == "no-crs" and issue.fix is not None
    assert not issue.fix.automatic  # offered again, with the label's grid
    assert issue.fix.transform == pytest.approx(HIRISE_LABEL_TRANSFORM)
    checkImageCrs(image)  # a session restored as undone stays undone
    assert image.extraMetadata["crsFix"]["undone"] is True
    # and applied again by hand: both overrides, recorded as the user's
    applyCrsFix(image, issue.fix)
    assert sameCrs(image.crs, HIRISE_POLAR_PROJ)
    assert tuple(image.transform)[:6] == pytest.approx(HIRISE_LABEL_TRANSFORM)
    chosen = image.extraMetadata["crsFix"]
    assert chosen["automatic"] is False and chosen["from"] == "none"
    assert chosen["source"] == "label"
    assert chosen["transform"] == pytest.approx(list(HIRISE_LABEL_TRANSFORM))
    assert image.crsHealth is not None
    assert image.crsHealth.issues[0].fix is not None
    assert image.crsHealth.issues[0].fix.automatic  # in use: Undo offered


def test_a_label_gdal_misread_can_be_applied_and_undone(tmp_path):
    image = loadRaster(str(writePds3(tmp_path, WEST_LABEL_GROUP)))
    assert image.crs is not None and sameCrs(image.crs, GDAL_WEST_PROJ)
    health = image.crsHealth
    assert health is not None
    issue = next(i for i in health.issues if i.code == "label-crs-mismatch")
    assert issue.fix is not None and not issue.fix.automatic
    assert "crsFix" not in image.extraMetadata
    applied = applyCrsFix(image, issue.fix)
    assert _westAxis(image.crs)  # the label's, longitude positive west
    assert image.transform == GDAL_WEST_TRANSFORM  # GDAL's grid is kept
    assert "transform" not in image.metadataOverrides
    record = image.extraMetadata["crsFix"]
    assert record["automatic"] is False and record["source"] == "label"
    assert record["reason"] == issue.fix.reason and _westAxis(str(record["to"]))
    used = next(i for i in applied.issues if i.code == "label-crs-mismatch")
    assert used.fix is not None and used.fix.automatic  # in use: Undo offered
    assert used.severity is Severity.INFO and used.message == issue.fix.reason
    again = undoCrsFix(image)
    assert sameCrs(image.crs, GDAL_WEST_PROJ) and not _westAxis(image.crs)
    assert image.extraMetadata["crsFix"]["undone"] is True
    offered = next(i for i in again.issues if i.code == "label-crs-mismatch")
    assert offered.fix is not None and not offered.fix.automatic


def test_a_west_label_crs_is_kept_as_projjson(tmp_path):
    image = loadRaster(str(writePds3(tmp_path, WEST_LABEL_GROUP)))
    health = image.crsHealth
    assert health is not None
    fix = next(i.fix for i in health.issues if i.code == "label-crs-mismatch")
    assert fix is not None
    applyCrsFix(image, fix)
    saved = image.metadataOverrides["crs"]
    assert saved.lstrip().startswith("{") and _westAxis(saved)
    assert str(image.extraMetadata["crsFix"]["to"]).lstrip().startswith("{")


def test_a_crs_set_by_hand_against_the_label_is_not_an_error(tmp_path):
    image = loadRaster(str(writePds3(tmp_path, WEST_LABEL_GROUP)))
    health = chooseCrs(image, "IAU_2015:49910", "Its producer's projection")
    issue = next(i for i in health.issues if i.code == "label-crs-mismatch")
    assert issue.severity is Severity.INFO
    assert issue.message.startswith("You chose Mars, ")
    assert "; its label describes Mars, " in issue.message
    assert issue.fix is not None and not issue.fix.automatic  # still offered
