import itertools
import math

import numpy as np
import pytest

from varda.shapes._tests.meshes import cube, icosphere, openPatch
from varda.shapes.gravity import G, polyhedronGravity


def test_outside_a_sphere_the_field_is_a_point_mass():
    sphere = icosphere(1.0, 4)
    density = 2000.0
    mass = density * sphere.volume * 1e9
    rng = np.random.default_rng(3)
    directions = rng.normal(size=(20, 3))
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)
    points = directions * rng.uniform(1.5, 3.0, size=(20, 1))  # km
    potential, acceleration = polyhedronGravity(sphere, density, points)
    r = np.linalg.norm(points, axis=1) * 1000
    assert potential == pytest.approx(-G * mass / r, rel=2e-3)
    assert np.linalg.norm(acceleration, axis=1) == pytest.approx(
        G * mass / r**2, rel=2e-3
    )
    # toward the center
    inward = -np.einsum("ij,ij->i", acceleration, directions)
    assert (inward > 0).all()


def test_inside_the_laplacian_is_4_pi_g_rho():
    sphere = icosphere(1.0, 3)
    density = 1500.0
    point = np.array([0.1, 0.2, -0.1])
    h = 0.01  # km
    offsets = [np.zeros(3)] + [s * h * e for e in np.eye(3) for s in (1, -1)]
    potential, _ = polyhedronGravity(sphere, density, point + np.array(offsets))
    laplacian = (potential[1:].sum() - 6 * potential[0]) / (h * 1000) ** 2
    assert laplacian == pytest.approx(4 * math.pi * G * density, rel=1e-4)


def prismPotential(low, high, point, density):
    """The potential (negative, J/kg) of a uniform rectangular prism, from
    the closed form for ∫∫∫ dV / r (Nagy, 1966; Nagy et al., 2000):
    a sum over the corners of xy ln(z + r) + yz ln(x + r) + zx ln(y + r)
    − x²/2 atan(yz / xr) − y²/2 atan(zx / yr) − z²/2 atan(xy / zr)."""
    total = 0.0
    for corner in itertools.product((0, 1), repeat=3):
        x, y, z = np.where(corner, high, low) - point
        r = math.sqrt(x * x + y * y + z * z)
        sign = (-1) ** (3 - sum(corner))
        total += sign * (
            x * y * math.log(z + r) + y * z * math.log(x + r) + z * x * math.log(y + r)
            - x * x / 2 * math.atan(y * z / (x * r))
            - y * y / 2 * math.atan(z * x / (y * r))
            - z * z / 2 * math.atan(x * y / (z * r))
        )  # fmt: skip
    return -G * density * total


@pytest.mark.parametrize("point", [(0.8, 0.8, 0.8), (0.9, 0.7, 0.6), (1.5, -1.2, 0.9)])
def test_werners_cube_matches_the_closed_form(point):
    """Werner (1994) checks the polyhedron formula on a cube: here a unit
    cube (1 km, centered) against the prism's closed form, off its diagonal
    too."""
    density = 1000.0
    potential, _ = polyhedronGravity(cube(1.0), density, np.array([point]))
    expected = prismPotential(
        np.full(3, -500.0), np.full(3, 500.0), np.array(point) * 1000, density
    )
    assert potential[0] == pytest.approx(expected, rel=1e-10)


def test_acceleration_is_minus_the_potential_gradient():
    sphere = icosphere(1.0, 2)
    point = np.array([1.3, -0.4, 0.5])
    h = 1e-4
    offsets = np.array([point + s * h * e for e in np.eye(3) for s in (1, -1)])
    potential, _ = polyhedronGravity(sphere, 1000.0, offsets)
    gradient = (potential[0::2] - potential[1::2]) / (2 * h * 1000)
    _, acceleration = polyhedronGravity(sphere, 1000.0, point[None])
    assert acceleration[0] == pytest.approx(-gradient, rel=1e-6)


def test_an_open_model_is_refused():
    with pytest.raises(ValueError, match="closed shape model"):
        polyhedronGravity(openPatch(), 1000.0, np.zeros((1, 3)))


def test_degenerate_plates_and_edges_contribute_nothing():
    """A zero-area plate (on a repeated vertex, so with a zero-length edge)
    is skipped: the field stays finite and unchanged."""
    model = cube(1.0)
    vertices = np.vstack(
        [model.vertices, model.vertices[[0]]]
    )  # a duplicate of vertex 0
    sliver = np.array([[0, 1, 8]])  # 8 == 0: a zero-length edge, zero area
    degenerate = type(model)(vertices, np.vstack([model.plates, sliver]), "sliver")
    point = np.array([[0.9, 0.7, 0.6]])
    clean, _ = polyhedronGravity(model, 1000.0, point)
    dirty, acceleration = polyhedronGravity(degenerate, 1000.0, point)
    assert np.isfinite(dirty).all() and np.isfinite(acceleration).all()
    assert dirty[0] == pytest.approx(clean[0], rel=1e-12)


def test_a_point_on_an_edge_stays_finite():
    potential, acceleration = polyhedronGravity(
        cube(1.0), 1000.0, np.array([[0.5, 0.5, 0.0]])
    )
    assert np.isfinite(potential).all() and np.isfinite(acceleration).all()
