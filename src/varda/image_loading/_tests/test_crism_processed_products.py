"""Products of Varda's CRISM processing work with the CRISM tools built on
``crism_geometry`` (the CRISM Ratio workspace, Ratio Cube): the map-projected
I/F finds its IN (detector columns) and DE (incidence) backplanes by name, the
sensor-space I/F finds the TRR3's DDR through its provenance, and every
product shows how it was made."""

from pathlib import Path

import numpy as np
import pytest

from varda.common.entities import VardaRaster
from varda.common.provenance import PROVENANCE_KEY, provenanceText
from varda.crism.calibration import CalibrationLibrary
from varda.crism.observation import CrismObservation
from varda.crism.pipeline import PROCESS_ID, ProcessResult, process
from varda.crism.recipe import Recipe
from varda.image_loading import crism_geometry
from varda.image_loading.crism_geometry import (
    ROUTE_BESIDE,
    columnGeometryFor,
    sceneIncidenceFor,
)
from varda.image_loading.image_loading_service import openDataSource

CROPS = Path(__file__).parents[2] / "crism" / "_tests" / "data"
TRR3 = "frs00031442_01_if168l_trr3"
DDR = "frs00031442_01_de168l_ddr1"


def _offline(url: str, target: Path) -> None:
    raise OSError("offline (test)")


@pytest.fixture(scope="module")
def processed(tmp_path_factory: pytest.TempPathFactory) -> ProcessResult:
    folder = tmp_path_factory.mktemp("crism")
    recipe = Recipe()
    recipe.output.sensorSpace.set(True)
    library = CalibrationLibrary([CROPS], folder / "cache", fetch=_offline)
    return process(
        CrismObservation.fromFolder(CROPS), recipe, folder / "out", library=library
    )


@pytest.fixture(autouse=True)
def _freshCache():
    crism_geometry._geometry_cache.clear()
    yield
    crism_geometry._geometry_cache.clear()


def _open(path: Path | None) -> VardaRaster:
    assert path is not None
    return VardaRaster(openDataSource(str(path)), name=path.name)


def test_map_product_finds_its_backplanes(processed: ProcessResult) -> None:
    image = _open(processed.mapProjected)
    assert processed.info is not None and processed.geometry is not None

    columns = columnGeometryFor(image)
    assert columns is not None
    assert columns.route == ROUTE_BESIDE
    assert Path(columns.source).name == processed.info.name
    # the IN file's source sample is the TRR3 column under each map pixel
    info = _open(processed.info).getBands([0])[..., 0]
    np.testing.assert_array_equal(columns.ir_sample, info)
    assert np.isfinite(columns.ir_sample).any()

    incidence = sceneIncidenceFor(image)
    assert incidence is not None
    assert incidence.route == ROUTE_BESIDE
    assert Path(incidence.path).name == processed.geometry.name
    assert 0 < incidence.low <= incidence.mean <= incidence.high < 90


def test_sensor_product_finds_the_ddr_through_its_provenance(
    processed: ProcessResult,
) -> None:
    image = _open(processed.sensorSpace)
    incidence = sceneIncidenceFor(image)
    assert incidence is not None
    assert incidence.route.startswith("from source")
    assert Path(incidence.path).name.lower() == f"{DDR}.img"


def test_products_show_how_they_were_made(processed: ProcessResult) -> None:
    assert processed.mapProjected is not None
    mapRecord = _open(processed.mapProjected).extraMetadata[PROVENANCE_KEY]
    assert len(mapRecord["results"]["systematic"]["scanChoice"]) == 411
    details = f"{processed.mapProjected.stem}.provenance.json"
    assert mapRecord["results"]["details"] == details
    for path in (
        processed.mapProjected,
        processed.mapSigma,
        processed.geometry,
        processed.info,
        processed.sensorSpace,
    ):
        image = _open(path)
        record = image.extraMetadata[PROVENANCE_KEY]
        assert record["processId"] == PROCESS_ID, path
        assert [Path(entry["path"]).stem for entry in record["inputs"]] == [TRR3, DDR]
        assert record["parameters"] == mapRecord["parameters"]
        assert "CRISM processing" in provenanceText(image)


def test_trr3_finds_the_ddr_beside_it() -> None:
    """A TRR3's DDR has a detached PDS3 label and no ENVI header: GDAL opens
    it through the label, and its band names come from the label."""
    incidence = sceneIncidenceFor(_open(CROPS / f"{TRR3}.lbl"))
    assert incidence is not None
    assert incidence.route == ROUTE_BESIDE
    assert Path(incidence.path).name == f"{DDR}.img"
    assert 0 < incidence.low <= incidence.mean <= incidence.high < 90
