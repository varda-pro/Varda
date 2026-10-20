"""Self-test checks that need no bundle run in the test suite too."""

from varda.self_test import _planetocentricWarp


def test_planetocentric_reprojection_check_passes():
    assert _planetocentricWarp().startswith("marker within")
