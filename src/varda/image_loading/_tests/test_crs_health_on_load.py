"""The coordinate-system health check runs when an image opens: its result is
kept with the image, and the one automatic fix (Earth degrees on a body the
label names) becomes the image's CRS override, recorded as ``crsFix``."""

import numpy as np
import pytest

from varda.common.entities import VardaRaster
from varda.common.provenance import inputRecord
from varda.geo._tests.fixtures import writeWgs84
from varda.geo.equivalence import sameCrs
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.crs_check import checkImageCrs
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
