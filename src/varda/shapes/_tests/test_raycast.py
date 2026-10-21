import time

import numpy as np
import pytest

from varda.shapes._tests.meshes import icosphere, twoLobes, uvSphere
from varda.shapes.raycast import RayCaster


def sphereHits(origins, directions, radius=1.0):
    """The analytic distance along unit ``directions`` to a sphere at 0."""
    b = np.einsum("ij,ij->i", origins, directions)
    c = np.einsum("ij,ij->i", origins, origins) - radius**2
    return -b - np.sqrt(b * b - c)


def test_rays_match_the_analytic_sphere_within_the_polygonal_error():
    sphere = icosphere(1.0, 4)
    rng = np.random.default_rng(1)
    origins = rng.normal(size=(1000, 3))
    origins = 3 * origins / np.linalg.norm(origins, axis=1, keepdims=True)
    # aim within 0.5 km of the center: incidence at most 30°
    targets = rng.uniform(-0.5, 0.5, size=(1000, 3)) / np.sqrt(3)
    directions = targets - origins
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)
    hits = RayCaster(sphere).intersect(origins, directions)
    assert (hits.plate >= 0).all()
    # a plate inscribed in the sphere lies at most 1 − √(1 − e²/3) below it
    edges = (
        sphere.vertices[sphere.plates] - sphere.vertices[np.roll(sphere.plates, 1, 1)]
    )
    longest = np.linalg.norm(edges, axis=2).max()
    sagitta = 1 - np.sqrt(1 - longest**2 / 3)
    error = hits.distance - sphereHits(origins, directions)
    assert (error >= -1e-12).all()
    assert (error <= sagitta / np.cos(np.radians(30))).all()
    assert np.allclose(np.linalg.norm(hits.points - origins, axis=1), hits.distance)


def test_misses_are_minus_one():
    hits = RayCaster(icosphere(1.0, 2)).intersect(
        np.array([[0, 0, 5.0], [0, 0, 5.0]]), np.array([[1.0, 0, 0], [0, 0, 1.0]])
    )
    assert hits.plate.tolist() == [-1, -1]
    assert np.isnan(hits.distance).all()


def test_two_lobes_give_the_nearest_hit_along_the_ray():
    caster = RayCaster(twoLobes())
    # a radial ray from the center along +x crosses the near lobe at 0.5 km
    # and its far side at 2.5 km; viewed from outside, the first is 2.5 km
    outward = caster.intersect(np.array([[0.0, 0, 0]]), np.array([[1.0, 0, 0]]))
    inward = caster.intersect(np.array([[10.0, 0, 0]]), np.array([[-1.0, 0, 0]]))
    assert outward.points[0, 0] == pytest.approx(0.5, abs=0.01)
    assert inward.points[0, 0] == pytest.approx(2.5, abs=0.01)


def test_closest_point():
    sphere = icosphere(1.0, 4)
    caster = RayCaster(sphere)
    points = np.array([[0.0, 0, 2.0], [0.0, 0.3, 0.0], [5.0, 5.0, 5.0]])
    hits = caster.closestPoint(points)
    radii = np.linalg.norm(hits.points, axis=1)
    assert ((radii > 0.99) & (radii <= 1.0 + 1e-12)).all()
    brute = [
        min(np.linalg.norm(c - p) for c in sphere.vertices) for p in points
    ]  # vertices are on the surface: no farther than the nearest vertex
    assert (hits.distance <= np.array(brute) + 1e-12).all()


@pytest.mark.slow
def test_a_million_plates_build_and_cast_quickly():
    RayCaster(icosphere(1.0, 1)).intersect(np.zeros((1, 3)), np.ones((1, 3)))  # compile
    big = uvSphere(1.0, 708, 708)
    assert big.plateCount > 1_000_000
    rng = np.random.default_rng(0)
    origins = rng.normal(size=(10_000, 3))
    origins = 3 * origins / np.linalg.norm(origins, axis=1, keepdims=True)
    start = time.perf_counter()
    hits = RayCaster(big).intersect(origins, -origins)
    assert time.perf_counter() - start < 5.0
    assert (hits.plate >= 0).all()
