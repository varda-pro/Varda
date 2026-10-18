"""Tests for ROICollection and VardaROI."""

from types import SimpleNamespace

import math

import numpy as np
import pytest
from pyproj import Transformer
from shapely.geometry import Polygon, box

from varda.common.entities import ROIMode, VardaROI, Color
from varda.geo._tests.fixtures import (
    CRISM_MTRDR_ENVI_WKT,
    CRISM_MTRDR_WKT,
    CTX_DEM_PROJ,
    WGS84,
    makeImage,
)
from varda.rois.roi_collection import ROICollection
from varda.utilities.proj_lock import PROJ_LOCK

RED = Color(1.0, 0.0, 0.0, 0.5)
GREEN = Color(0.0, 1.0, 0.0, 0.5)
BLUE = Color(0.0, 0.0, 1.0, 0.5)


def _quantized(color: Color) -> Color:
    """A color as it survives file storage (colors are persisted as 8-bit hex)."""
    return Color.fromHexString(color.toHexString())


@pytest.fixture
def collection() -> ROICollection:
    """An empty ROICollection with no CRS (pixel-space)."""
    return ROICollection()


@pytest.fixture
def sample_polygon() -> Polygon:
    return box(10, 20, 50, 60)


class TestVardaROI:
    def test_immutable(self, sample_polygon: Polygon) -> None:
        roi = VardaROI(
            fid=0,
            name="test",
            color=RED,
            geometry=sample_polygon,
            roiType=ROIMode.RECTANGLE,
        )
        with pytest.raises(AttributeError):
            roi.name = "changed"  # type: ignore[misc]

    def test_default_properties(self, sample_polygon: Polygon) -> None:
        roi = VardaROI(
            fid=0,
            name="test",
            color=RED,
            geometry=sample_polygon,
            roiType=ROIMode.POLYGON,
        )
        assert roi.properties == {}


class TestROICollectionCRUD:
    def test_add_roi(self, collection: ROICollection, sample_polygon: Polygon) -> None:
        fid = collection.addROI(
            geometry=sample_polygon,
            name="ROI 1",
            color=RED,
            roiType=ROIMode.RECTANGLE,
        )
        assert fid == 0
        assert len(collection) == 1

    def test_add_multiple(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        fid1 = collection.addROI(sample_polygon, "A", RED, ROIMode.RECTANGLE)
        fid2 = collection.addROI(sample_polygon, "B", GREEN, ROIMode.POLYGON)
        assert fid1 != fid2
        assert len(collection) == 2

    def test_get_roi(self, collection: ROICollection, sample_polygon: Polygon) -> None:
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        roi = collection.getROI(fid)
        assert isinstance(roi, VardaROI)
        assert roi.name == "ROI 1"
        assert roi.color == RED
        assert roi.roiType == ROIMode.RECTANGLE
        assert roi.geometry.equals(sample_polygon)

    def test_get_all_rois(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        collection.addROI(sample_polygon, "A", RED, ROIMode.RECTANGLE)
        collection.addROI(sample_polygon, "B", GREEN, ROIMode.POLYGON)
        rois = collection.getAllROIs()
        assert len(rois) == 2
        assert rois[0].name == "A"
        assert rois[1].name == "B"

    def test_remove_roi(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        collection.removeROI(fid)
        assert len(collection) == 0
        with pytest.raises(KeyError):
            collection.getROI(fid)

    def test_remove_nonexistent(self, collection: ROICollection) -> None:
        with pytest.raises(KeyError):
            collection.removeROI(999)

    def test_update_roi(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        collection.updateROI(fid, name="Renamed")
        roi = collection.getROI(fid)
        assert roi.name == "Renamed"

    def test_update_rejects_user_columns(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        with pytest.raises(ValueError, match="not a core"):
            collection.updateROI(fid, custom_field="value")


class TestROICollectionUserColumns:
    def test_add_column(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        collection.addColumn("material", default="unknown")
        assert collection.getProperty(fid, "material") == "unknown"

    def test_set_property(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        collection.addColumn("material")
        collection.setProperty(fid, "material", "iron")
        assert collection.getProperty(fid, "material") == "iron"

    def test_set_property_rejects_core_columns(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        with pytest.raises(ValueError, match="core column"):
            collection.setProperty(fid, "name", "bad")

    def test_user_properties_in_snapshot(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        collection.addColumn("material", default="unknown")
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        collection.setProperty(fid, "material", "iron")
        roi = collection.getROI(fid)
        assert roi.properties["material"] == "iron"

    def test_duplicate_column_raises(self, collection: ROICollection) -> None:
        collection.addColumn("material")
        with pytest.raises(ValueError, match="already exists"):
            collection.addColumn("material")

    def test_user_columns_property(self, collection: ROICollection) -> None:
        assert collection.userColumns == []
        collection.addColumn("material")
        collection.addColumn("notes")
        assert collection.userColumns == ["material", "notes"]

    def test_add_column_strips_and_rejects_empty(
        self, collection: ROICollection
    ) -> None:
        collection.addColumn("  notes  ")
        assert collection.userColumns == ["notes"]
        with pytest.raises(ValueError, match="empty"):
            collection.addColumn("   ")

    def test_add_column_rejects_reserved(self, collection: ROICollection) -> None:
        with pytest.raises(ValueError, match="reserved"):
            collection.addColumn("geometry")

    def test_add_column_emits_columns_changed(self, collection: ROICollection) -> None:
        received: list[int] = []
        collection.sigColumnsChanged.connect(lambda: received.append(1))
        collection.addColumn("notes")
        assert received == [1]

    def test_new_roi_backfills_existing_column(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        collection.addColumn("notes")
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        # New ROI gets an empty value rather than NaN for the existing column.
        assert collection.getProperty(fid, "notes") == ""

    def test_remove_column(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        collection.addColumn("notes", default="hi")
        collection.removeColumn("notes")
        assert collection.userColumns == []
        assert "notes" not in collection.getROI(fid).properties

    def test_remove_column_rejects_core(self, collection: ROICollection) -> None:
        with pytest.raises(ValueError, match="core column"):
            collection.removeColumn("name")

    def test_remove_nonexistent_column(self, collection: ROICollection) -> None:
        with pytest.raises(KeyError):
            collection.removeColumn("missing")

    def test_remove_column_emits_columns_changed(
        self, collection: ROICollection
    ) -> None:
        collection.addColumn("notes")
        received: list[int] = []
        collection.sigColumnsChanged.connect(lambda: received.append(1))
        collection.removeColumn("notes")
        assert received == [1]

    def test_rename_column_preserves_values(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        collection.addColumn("notes")
        collection.setProperty(fid, "notes", "important")
        collection.renameColumn("notes", "comments")
        assert collection.userColumns == ["comments"]
        assert collection.getProperty(fid, "comments") == "important"

    def test_rename_column_rejects_core(self, collection: ROICollection) -> None:
        collection.addColumn("notes")
        with pytest.raises(ValueError, match="core column"):
            collection.renameColumn("name", "notes")

    def test_rename_column_rejects_duplicate(self, collection: ROICollection) -> None:
        collection.addColumn("notes")
        collection.addColumn("comments")
        with pytest.raises(ValueError, match="already exists"):
            collection.renameColumn("notes", "comments")

    def test_rename_nonexistent_column(self, collection: ROICollection) -> None:
        with pytest.raises(KeyError):
            collection.renameColumn("missing", "other")


class TestROICollectionSignals:
    def test_add_signal(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        received: list[int] = []
        collection.sigROIAdded.connect(received.append)
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        assert received == [fid]

    def test_remove_signal(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        received: list[int] = []
        collection.sigROIRemoved.connect(received.append)
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        collection.removeROI(fid)
        assert received == [fid]

    def test_update_signal(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        received: list[int] = []
        collection.sigROIUpdated.connect(received.append)
        fid = collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        collection.updateROI(fid, name="Renamed")
        assert received == [fid]

    def test_collection_changed_signal(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        count = [0]
        collection.sigCollectionChanged.connect(
            lambda: count.__setitem__(0, count[0] + 1)
        )
        collection.addROI(sample_polygon, "ROI 1", RED, ROIMode.RECTANGLE)
        collection.addROI(sample_polygon, "ROI 2", GREEN, ROIMode.POLYGON)
        assert count[0] == 2


class TestROICollectionFids:
    def test_fids_property(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        f1 = collection.addROI(sample_polygon, "A", RED, ROIMode.RECTANGLE)
        f2 = collection.addROI(sample_polygon, "B", GREEN, ROIMode.POLYGON)
        assert collection.fids == [f1, f2]

    def test_fids_after_remove(
        self, collection: ROICollection, sample_polygon: Polygon
    ) -> None:
        f1 = collection.addROI(sample_polygon, "A", RED, ROIMode.RECTANGLE)
        f2 = collection.addROI(sample_polygon, "B", GREEN, ROIMode.POLYGON)
        collection.removeROI(f1)
        assert collection.fids == [f2]


def _make_fake_image(width: int = 100, height: int = 100):
    """Create a minimal fake image object for testing pixel-space operations."""
    return SimpleNamespace(width=width, height=height)


class TestPixelCoordinates:
    def test_pixel_space_returns_coords(self, collection: ROICollection) -> None:
        poly = Polygon([(5, 10), (15, 10), (15, 20), (5, 20), (5, 10)])
        fid = collection.addROI(poly, "test", RED, ROIMode.POLYGON)
        coords = collection.getPixelCoordinates(fid)
        # Polygon exterior includes closing vertex
        assert coords.shape == (5, 2)
        np.testing.assert_array_almost_equal(coords[0], [5, 10])

    def test_nonexistent_fid(self, collection: ROICollection) -> None:
        with pytest.raises(KeyError):
            collection.getPixelCoordinates(999)


class TestMask:
    def test_mask_shape_and_content(self, collection: ROICollection) -> None:
        # A 10x10 box in pixel space inside a 100x100 image
        poly = box(10, 20, 20, 30)
        fid = collection.addROI(poly, "box", RED, ROIMode.RECTANGLE)
        image = _make_fake_image(100, 100)
        mask = collection.getMask(fid, image)

        assert mask.shape == (100, 100)
        assert mask.dtype == bool
        # Center of the box should be True
        assert mask[25, 15] is np.True_
        # Outside the box should be False
        assert mask[0, 0] is np.False_

    def test_mask_pixel_count(self, collection: ROICollection) -> None:
        # A box from (0,0) to (10,10) -- should cover ~100 pixels
        poly = box(0, 0, 10, 10)
        fid = collection.addROI(poly, "box", RED, ROIMode.RECTANGLE)
        image = _make_fake_image(50, 50)
        mask = collection.getMask(fid, image)
        # rasterize may differ slightly at edges, but should be ~100
        assert 80 <= mask.sum() <= 121


def _make_fake_image_with_data(
    width: int, height: int, bands: int, fill_value: float = 5.0
):
    """Fake image that returns constant data for all bands."""
    data = np.full((height, width, bands), fill_value, dtype=np.float64)
    return SimpleNamespace(
        width=width,
        height=height,
        bandCount=bands,
        nodata=None,
        wavelengths=np.arange(bands, dtype=np.float64),
        getData=lambda bandIndices=None, window=None: (
            data[
                window[0] : window[0] + window[2], window[1] : window[1] + window[3], :
            ]
            if window is not None
            else data
        ),
    )


class TestSpectralStatistics:
    def test_mean_spectrum(self, collection: ROICollection) -> None:
        poly = box(2, 2, 8, 8)
        fid = collection.addROI(poly, "roi", RED, ROIMode.RECTANGLE)
        image = _make_fake_image_with_data(20, 20, 3, fill_value=7.0)
        spectrum = collection.getMeanSpectrum(fid, image)
        np.testing.assert_array_almost_equal(spectrum.values, [7.0, 7.0, 7.0])

    def test_std_deviation_constant(self, collection: ROICollection) -> None:
        poly = box(2, 2, 8, 8)
        fid = collection.addROI(poly, "roi", RED, ROIMode.RECTANGLE)
        image = _make_fake_image_with_data(20, 20, 3, fill_value=7.0)
        std = collection.getStdDeviation(fid, image)
        np.testing.assert_array_almost_equal(std, [0.0, 0.0, 0.0])

    def test_roi_statistics_keys(self, collection: ROICollection) -> None:
        poly = box(2, 2, 8, 8)
        fid = collection.addROI(poly, "roi", RED, ROIMode.RECTANGLE)
        image = _make_fake_image_with_data(20, 20, 3, fill_value=5.0)
        stats = collection.getROIStatistics(fid, image)
        assert set(stats.keys()) == {
            "mean",
            "std",
            "min",
            "max",
            "pixel_count",
            "valid_count",
        }
        assert stats["pixel_count"] > 0
        np.testing.assert_array_almost_equal(stats["min"], [5.0, 5.0, 5.0])
        np.testing.assert_array_almost_equal(stats["max"], [5.0, 5.0, 5.0])


class TestFileIO:
    def test_geojson_round_trip(self, collection: ROICollection, tmp_path) -> None:
        poly1 = box(0, 0, 10, 10)
        poly2 = box(20, 20, 30, 30)
        POLY1_COLOR = RED
        collection.addROI(poly1, "ROI A", POLY1_COLOR, ROIMode.RECTANGLE)
        POLY2_COLOR = Color(0, 1, 0, 0.8)
        collection.addROI(poly2, "ROI B", POLY2_COLOR, ROIMode.POLYGON)

        path = str(tmp_path / "rois.geojson")
        collection.toFile(path)

        loaded = ROICollection.fromFile(path)
        assert len(loaded) == 2
        rois = loaded.getAllROIs()
        assert rois[0].name == "ROI A"
        assert rois[0].color == _quantized(POLY1_COLOR)
        assert rois[0].roiType == ROIMode.RECTANGLE
        assert rois[0].geometry.equals(poly1)
        assert rois[1].name == "ROI B"
        assert rois[1].color == _quantized(POLY2_COLOR)

    def test_gpkg_round_trip(self, collection: ROICollection, tmp_path) -> None:
        poly = box(5, 5, 15, 15)
        POLY_COLOR = Color(0.4, 0.4, 0.4, 1.0)
        collection.addROI(poly, "Test", POLY_COLOR, ROIMode.FREEHAND)

        path = str(tmp_path / "rois.gpkg")
        collection.toFile(path)

        loaded = ROICollection.fromFile(path)
        assert len(loaded) == 1
        roi = loaded.getROI(0)
        assert roi.name == "Test"
        assert roi.roiType == ROIMode.FREEHAND
        assert roi.color == _quantized(POLY_COLOR)

    def test_export_empty_collection(self, collection: ROICollection, tmp_path) -> None:
        path = str(tmp_path / "empty.geojson")
        collection.toFile(path)
        # Should not create file (just warns)
        import os

        assert not os.path.exists(path)


class TestApplyToImage:
    def test_apply_same_crs(self) -> None:
        from pyproj import CRS
        from affine import Affine

        crs = CRS.from_epsg(4326)
        source = ROICollection(crs=crs, transform=Affine.identity())
        poly = box(10, 20, 30, 40)
        source.addROI(poly, "A", RED, ROIMode.RECTANGLE)

        target_image = SimpleNamespace(
            crs=crs,
            transform=Affine.identity(),
            width=100,
            height=100,
        )
        result = source.applyToImage(target_image)
        assert len(result) == 1
        roi = result.getROI(0)
        assert roi.name == "A"
        assert roi.geometry.equals(poly)

    def test_apply_no_crs_raises(self, collection: ROICollection) -> None:
        poly = box(0, 0, 10, 10)
        collection.addROI(poly, "A", RED, ROIMode.RECTANGLE)
        target = SimpleNamespace(crs=None, transform=None)
        with pytest.raises(ValueError, match="pixel-space"):
            collection.applyToImage(target)

    def test_apply_rois_to_other_body_refused(self) -> None:
        mars = makeImage(CRISM_MTRDR_WKT, centerLonLat=(10.0, 15.0), pixelSize=18.0)
        earth = makeImage(WGS84, centerLonLat=(10.0, 15.0), pixelSize=0.01)
        source = ROICollection.fromImage(mars)
        source.addROI(box(0, 0, 10, 10), "A", RED, ROIMode.RECTANGLE)
        with pytest.raises(ValueError, match="Mars and Earth are different bodies"):
            source.applyToImage(earth)

    def test_apply_rois_to_an_equivalent_crs_copies_them(self, monkeypatch) -> None:
        pds3 = makeImage(CRISM_MTRDR_WKT, centerLonLat=(10.0, 15.0), pixelSize=18.0)
        envi = makeImage(
            CRISM_MTRDR_ENVI_WKT, centerLonLat=(10.0, 15.0), pixelSize=18.0
        )
        source = ROICollection.fromImage(pds3)
        poly = box(0, 0, 10, 10)
        source.addROI(poly, "A", RED, ROIMode.RECTANGLE)

        def refuse(*args, **kwargs):
            raise AssertionError("an equivalent CRS needs no reprojection")

        monkeypatch.setattr(Transformer, "from_crs", refuse)
        assert source.applyToImage(envi).getROI(0).geometry.equals(poly)

    def test_apply_rois_from_a_sphere_to_an_ellipsoid_keeps_planetocentric_places(
        self,
    ) -> None:
        """Each vertex keeps its planetocentric latitude (Snyder, 1987, eq.
        3-28) on the planetographic, west-positive Mars ellipsoid."""
        ctx = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 45.0), pixelSize=20.0)
        ographic = makeImage(
            "IAU_2015:49901", centerLonLat=(-77.5, 45.3), pixelSize=0.01
        )
        source = ROICollection.fromImage(ctx)
        x, y = ctx.pixelToGeo(32, 32)
        source.addROI(box(x, y, x + 100, y + 100), "A", RED, ROIMode.RECTANGLE)
        moved = source.applyToImage(ographic).getROI(0).geometry
        lonW, latG = moved.exterior.coords[0]
        a, c = 3396190.0, 3376200.0
        latC = math.degrees(math.atan((c / a) ** 2 * math.tan(math.radians(latG))))
        with PROJ_LOCK:
            lonC, expected = Transformer.from_crs(
                CTX_DEM_PROJ, "+proj=longlat +R=3396190 +no_defs", always_xy=True
            ).transform(*box(x, y, x + 100, y + 100).exterior.coords[0])
        assert latC == pytest.approx(expected, abs=1e-9)
        assert (-lonW) % 360 == pytest.approx(lonC % 360, abs=1e-9)


class TestRatioSpectrum:
    def test_ratio_of_two_rois(
        self, collection: ROICollection, make_split_image
    ) -> None:
        # numerator ROI in the left half (value 8), denominator in right half (4)
        num_fid = collection.addROI(box(2, 2, 8, 8), "num", RED, ROIMode.RECTANGLE)
        den_fid = collection.addROI(box(31, 2, 38, 8), "den", BLUE, ROIMode.RECTANGLE)
        image = make_split_image(40, 20, 3, left_fill=8.0, right_fill=4.0)
        ratio = collection.getRatioSpectrum(num_fid, den_fid, image)
        np.testing.assert_array_almost_equal(ratio.values, [2.0, 2.0, 2.0])

    def test_ratio_against_self_is_one(
        self, collection: ROICollection, make_split_image
    ) -> None:
        fid = collection.addROI(box(2, 2, 8, 8), "roi", RED, ROIMode.RECTANGLE)
        image = make_split_image(40, 20, 2, left_fill=5.0, right_fill=9.0)
        ratio = collection.getRatioSpectrum(fid, fid, image)
        np.testing.assert_array_almost_equal(ratio.values, [1.0, 1.0])


def _make_partial_nodata_image(width, height, bands, nodata=65535.0):
    """Fake image where band 1 is nodata in the top half but valid elsewhere.

    Simulates an (unmasked) source where one wavelength is mostly nodata
    (e.g. CRISM's ~2800 nm band), while pixels remain valid in other bands.
    """
    data = np.full((height, width, bands), 0.1, dtype=np.float64)
    data[:, :, 1] = 0.2
    data[: height // 2, :, 1] = nodata  # top half of band 1 is nodata
    return SimpleNamespace(
        width=width,
        height=height,
        bandCount=bands,
        nodata=nodata,
        wavelengths=np.arange(bands, dtype=np.float64),
        getData=lambda bandIndices=None, window=None: (
            data[
                window[0] : window[0] + window[2],
                window[1] : window[1] + window[3],
                :,
            ]
            if window is not None
            else data
        ),
    )


class TestNodataHandling:
    def test_per_band_nodata_excluded_from_stats(
        self, collection: ROICollection
    ) -> None:
        # Band 1 is nodata (65535) in half the ROI's pixels but valid elsewhere.
        # The per-band nodata must not poison band 1's mean/std (the bug behind
        # the CRISM 2800 nm "outlier" spike).
        image = _make_partial_nodata_image(6, 6, 3)
        fid = collection.addROI(box(0, 0, 6, 6), "roi", RED, ROIMode.RECTANGLE)
        stats = collection.getROIStatistics(fid, image)
        mean = np.asarray(stats["mean"])
        std = np.asarray(stats["std"])
        np.testing.assert_array_almost_equal(mean, [0.1, 0.2, 0.1])
        assert float(np.nanmax(mean)) < 1.0  # no nodata-driven outlier
        assert float(np.nanmax(std)) < 1.0  # std not inflated by nodata


class TestClasses:
    """ROIs that share a name are one class (e.g. three polygons of "basalt")."""

    def test_classes_group_rois_by_name_in_first_drawn_order(self) -> None:
        rois = ROICollection()
        a = rois.addROI(box(0, 0, 2, 2), "basalt", RED, ROIMode.RECTANGLE)
        b = rois.addROI(box(5, 5, 7, 7), "dust", GREEN, ROIMode.RECTANGLE)
        c = rois.addROI(box(8, 0, 9, 1), "basalt", RED, ROIMode.RECTANGLE)
        assert rois.classes() == {"basalt": [a, c], "dust": [b]}

    def test_a_class_mask_is_the_union_of_its_rois(self) -> None:
        rois = ROICollection()
        rois.addROI(box(0, 0, 2, 2), "basalt", RED, ROIMode.RECTANGLE)
        rois.addROI(box(5, 5, 7, 7), "basalt", RED, ROIMode.RECTANGLE)
        rois.addROI(box(10, 10, 12, 12), "dust", GREEN, ROIMode.RECTANGLE)
        image = _make_fake_image(20, 20)
        mask = rois.classMask("basalt", image)
        assert mask[1, 1] and mask[6, 6] and not mask[11, 11]

    def test_a_class_colour_is_its_first_rois(self) -> None:
        rois = ROICollection()
        rois.addROI(box(0, 0, 2, 2), "basalt", RED, ROIMode.RECTANGLE)
        assert rois.classColor("basalt") == RED
        assert rois.classColor("missing") is None
