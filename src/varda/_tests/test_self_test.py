"""Self-test checks that need no bundle run in the test suite too."""

import pytest

from varda.self_test import _dskRead, _planetocentricWarp, _shapeModel


def test_planetocentric_reprojection_check_passes():
    assert _planetocentricWarp().startswith("marker within")


def test_shape_model_check_passes(tmp_path):
    assert _shapeModel(tmp_path / "octahedron.obj").startswith("octahedron: 8 plates")


def test_dsk_check_passes_where_spiceypy_is_installed(tmp_path):
    pytest.importorskip("spiceypy")
    assert _dskRead(tmp_path / "octahedron.bds").startswith("DSK type 2: 8 plates")
