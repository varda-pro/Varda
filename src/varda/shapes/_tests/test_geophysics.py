import attrs
import numpy as np
import pytest

from varda.shapes._tests.meshes import icosphere, openPatch, uvSphere
from varda.shapes.geophysics import GeophysicsSettings, geophysics
from varda.shapes.gravity import G
from varda.shapes.mesh import ShapeModel, latLonRadius

NAMES = [
    "area",
    "gravitational acceleration",
    "centrifugal acceleration",
    "total acceleration",
    "effective potential",
    "slope",
    "geopotential elevation",
    "tilt",
    "slope (ρ−σ)",
    "slope (ρ+σ)",
    "geopotential elevation (ρ−σ)",
    "geopotential elevation (ρ+σ)",
]


BENNU_LIKE = GeophysicsSettings(
    density=1190.0, densitySigma=13.0, rotationPeriodH=4.296, uRef="area-weighted mean"
)


def settings(**changes) -> GeophysicsSettings:
    return attrs.evolve(BENNU_LIKE, **changes)


def run(model, **changes):
    results = geophysics(model, settings(**changes), lambda *_: None)
    return {data.name: data for data in results}


def ellipsoid(a, b, c, subdivisions=3) -> ShapeModel:
    sphere = icosphere(1.0, subdivisions)
    return ShapeModel(sphere.vertices * [a, b, c], sphere.plates, "ellipsoid")


def test_all_outputs_with_units_and_provenance():
    model = icosphere(0.25, 2)
    results = geophysics(model, settings(), lambda *_: None)
    assert [d.name for d in results] == NAMES
    units = {d.name: d.unit for d in results}
    assert units["area"] == "m²" and units["slope"] == "°"
    assert (
        units["effective potential"] == "J/kg" and units["total acceleration"] == "m/s²"
    )
    assert units["geopotential elevation"] == "m" and units["tilt"] == "°"
    provenance = results[5].provenance
    assert provenance["evaluatedOn"] == model.plateCount
    assert provenance["meanEdgeKm"] == pytest.approx(model.meanEdgeKm)
    assert provenance["settings"]["density"] == 1190.0
    assert all(d.shapeHash == model.contentHash for d in results)


def test_spin_tilts_a_spheres_slope_as_theory_says():
    """On a sphere, spin adds atan(ω²r sinφ cosφ / (g − ω²r cos²φ)) to the
    slope at latitude φ: nothing at the poles and the equator."""
    model = icosphere(0.25, 4)
    out = run(model)
    still = run(model, rotationPeriodH=1e9)["slope"].values
    assert still.max() < 3.0  # the plates' own facets (mesh resolution)
    lat = np.radians(latLonRadius(model.centroids)[:, 0])
    r = np.linalg.norm(model.centroids, axis=1) * 1000
    g = G * 1190.0 * model.volume * 1e9 / r**2
    spin = (2 * np.pi / (4.296 * 3600)) ** 2 * r
    theory = np.degrees(
        np.arctan(spin * np.sin(lat) * np.cos(lat) / (g - spin * np.cos(lat) ** 2))
    )
    added = out["slope"].values - still
    assert np.abs(added - np.abs(theory)).mean() < 0.3
    assert np.abs(theory).max() > 10  # the spin matters here
    nearPoleOrEquator = np.abs(np.sin(2 * lat)) < 0.05
    assert nearPoleOrEquator.any()
    assert np.abs(added[nearPoleOrEquator]).max() < np.abs(theory).max() / 5


def test_lower_density_means_steeper_slopes_where_spin_matters():
    model = ellipsoid(0.30, 0.25, 0.20)
    out = run(model, densitySigma=200.0, rotationPeriodH=3.5)
    lat = latLonRadius(model.centroids)[:, 0]
    mid = (np.abs(lat) > 20) & (np.abs(lat) < 70)
    low, nominal, high = (
        out["slope (ρ−σ)"].values[mid],
        out["slope"].values[mid],
        out["slope (ρ+σ)"].values[mid],
    )
    assert (low >= nominal - 1e-9).all() and (nominal >= high - 1e-9).all()
    assert (low - high).mean() > 0.1


def test_elevation_is_zero_on_average_and_slope_in_range():
    model = ellipsoid(0.30, 0.25, 0.20)
    out = run(model)
    elevation = out["geopotential elevation"].values
    potentialRef = np.average(out["effective potential"].values, weights=model.areas)
    assert np.isfinite(elevation).all() and potentialRef < 0
    assert ((out["slope"].values >= 0) & (out["slope"].values <= 90)).all()
    minimum = run(model, uRef="minimum")["geopotential elevation"].values
    assert minimum.min() == pytest.approx(0.0, abs=1e-9)


def test_tilt_is_zero_on_a_sphere_and_grows_on_an_ellipsoid():
    sphere = run(icosphere(0.25, 3))["tilt"].values
    assert sphere.max() < 2.0
    wide = run(ellipsoid(0.30, 0.25, 0.20), tiltRadiusM=40.0)["tilt"].values
    assert wide.max() > 10.0


def test_an_open_patch_is_refused():
    with pytest.raises(ValueError, match="Gravity needs a closed shape model"):
        geophysics(openPatch(), settings(), lambda *_: None)


def test_a_large_model_is_evaluated_on_a_coarser_one():
    model = uvSphere(0.25, 388, 388)
    assert model.plateCount > 300_000
    results = geophysics(model, settings(maxExactPlates=3000), lambda *_: None)
    provenance = results[0].provenance
    assert provenance["evaluatedOn"] < 300_000
    assert all(len(d.values) == model.plateCount for d in results)
    assert np.isfinite(results[5].values).all()


def test_coarse_evaluation_matches_the_exact_slopes():
    """Above the limit, gravity comes from a coarser polyhedron evaluated at
    every plate's own centroid: slopes stay within a small fraction of a
    degree of the exact run."""
    model = ellipsoid(0.30, 0.25, 0.20, subdivisions=5)  # 20,480 plates
    exact = run(model)["slope"].values
    coarse = run(model, maxExactPlates=10_000)
    assert coarse["slope"].provenance["evaluatedOn"] <= 5000
    error = np.abs(coarse["slope"].values - exact)
    assert error.mean() < 0.1 and error.max() < 0.5


def test_a_rough_model_still_gets_a_closed_coarse_model():
    model = ellipsoid(0.30, 0.25, 0.20, subdivisions=5)
    rng = np.random.default_rng(4)
    noisy = model.vertices * (1 + 0.002 * rng.standard_normal((len(model.vertices), 1)))
    rough = ShapeModel(noisy, model.plates, "rough")
    out = run(rough, maxExactPlates=3000)
    assert np.isfinite(out["slope"].values).all()
    assert out["slope"].provenance["gravitySource"]["plates"] < 3000


def test_tilt_in_chunks_matches_one_pass():
    from varda.shapes.geophysics import _tilt

    model = ellipsoid(0.30, 0.25, 0.20)
    assert np.allclose(_tilt(model, 40.0, chunk=97), _tilt(model, 40.0, chunk=10**9))


def test_a_tilt_radius_needing_too_many_plate_pairs_is_refused(monkeypatch):
    from varda.shapes import geophysics as module

    monkeypatch.setattr(module, "MAX_TILT_PAIRS", 10_000)
    with pytest.raises(ValueError, match="tilt radius"):
        run(icosphere(0.25, 3), tiltRadiusM=200.0)
