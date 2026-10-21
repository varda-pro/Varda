import math

import numpy as np
import pytest

from varda.shapes._tests.meshes import cube, icosphere, openPatch, reversed_
from varda.shapes.mesh import ShapeModel, decimated, latLonRadius, oriented


def test_sphere_area_and_volume_approach_the_analytic_from_below():
    sphere = icosphere(1.0, 5)
    area, volume = sphere.areas.sum(), sphere.volume
    assert 4 * math.pi * 0.995 < area < 4 * math.pi
    assert 4 / 3 * math.pi * 0.995 < volume < 4 / 3 * math.pi


def test_cube_volume_and_area_are_exact():
    model = cube(2.0)
    assert model.volume == pytest.approx(8.0, rel=1e-12)
    assert model.areas.sum() == pytest.approx(24.0, rel=1e-12)
    assert model.isClosed


def test_normals_point_outward_on_a_sphere():
    sphere = icosphere(1.0, 2)
    outward = np.einsum("ij,ij->i", sphere.normals, sphere.centroids)
    assert (outward > 0).all()
    assert np.allclose(np.linalg.norm(sphere.normals, axis=1), 1.0)


def test_open_patch_is_not_closed():
    patch = openPatch()
    assert patch.isClosed is False
    with pytest.raises(ValueError, match="closed"):
        _ = patch.volume


def test_reversed_winding_is_flipped():
    model, flipped = oriented(reversed_(icosphere(1.0, 2)))
    assert flipped
    assert model.volume > 0


def test_mixed_winding_is_made_consistent():
    sphere = icosphere(1.0, 2)
    plates = sphere.plates.copy()
    plates[::3] = plates[::3, ::-1]
    model, flipped = oriented(ShapeModel(sphere.vertices, plates, "mixed"))
    assert flipped
    assert model.volume == pytest.approx(sphere.volume, rel=1e-12)


def test_well_wound_model_is_kept():
    sphere = icosphere(1.0, 2)
    model, flipped = oriented(sphere)
    assert not flipped and model is sphere


def test_lat_lon_radius_is_planetocentric_east_0_360():
    result = latLonRadius(np.array([[0.0, -1.0, 0.0], [0.0, 0.0, 2.0]]))
    assert result[0] == pytest.approx([0.0, 270.0, 1.0])
    assert result[1][0] == pytest.approx(90.0)
    assert result[1][2] == pytest.approx(2.0)


def test_decimated_respects_the_cap_and_stays_closed():
    sphere = icosphere(1.0, 5)
    small = decimated(sphere, 1000)
    assert small.plates.shape[0] <= 1000
    assert small.isClosed
    assert small.volume == pytest.approx(sphere.volume, rel=0.05)


def test_decimated_under_the_cap_is_the_model_itself():
    sphere = icosphere(1.0, 2)
    assert decimated(sphere, 10_000) is sphere


def test_hash_changes_when_one_vertex_moves():
    sphere = icosphere(1.0, 2)
    moved = sphere.vertices.copy()
    moved[5, 0] += 1e-9
    assert ShapeModel(moved, sphere.plates, "m").contentHash != sphere.contentHash
    assert (
        ShapeModel(sphere.vertices, sphere.plates, "x").contentHash
        == sphere.contentHash
    )


def test_mean_edge_and_adjacency():
    model = cube(2.0)
    # 12 edges of 2 km and 6 face diagonals of 2√2 km
    assert model.meanEdgeKm == pytest.approx((12 * 2 + 6 * 2 * math.sqrt(2)) / 18)
    indptr, indices = model.adjacency
    assert (np.diff(indptr) == 3).all()  # every plate of a closed mesh has 3
    assert len(indices) == 36
