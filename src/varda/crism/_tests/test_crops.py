from pathlib import Path

import pytest
import rasterio

CROPS = [
    ("frs00031442_01_if168l_trr3.lbl", (438, 16, 120)),
    ("frs00031442_01_de168l_ddr1.lbl", (14, 16, 120)),
    ("cdr490947778566_wa0000000l_3.lbl", (438, 1, 120)),
    ("cdr420862848019_at0000000l_7.lbl", (438, 1, 120)),
]


@pytest.mark.parametrize(("name", "shape"), CROPS)
def test_crop_shape(cropDir: Path, name: str, shape: tuple[int, int, int]) -> None:
    with rasterio.open(cropDir / name) as dataset:
        assert (dataset.count, dataset.height, dataset.width) == shape
