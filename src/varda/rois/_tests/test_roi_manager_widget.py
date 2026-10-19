"""Tests for ROIManagerWidget plotting + denominator behavior."""

from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest
from psygnal import SignalInstance
from PyQt6.QtWidgets import QMessageBox
from shapely.geometry import box

from varda.common.entities import Color, ROIMode
from varda.common.spectral_axis import BandModel
from varda.geo._tests.fixtures import LRO_WAC_PROJ, makeImage
from varda.plotting.plot import VardaPlotWidget
from varda.rois.roi_calculations import PixelAreaCache, ZonalStatistic
from varda.rois.roi_collection import ROICollection
from varda.rois.roi_drawing_state import ROIDrawingState
from varda.rois.roi_drawing_widgets import ROIClassPicker, ROIShapePicker
from varda.rois.roi_manager_widget import ROIManagerWidget

RED = Color(1.0, 0.0, 0.0, 0.5)
BLUE = Color(0.0, 0.0, 1.0, 0.5)


class _FakePlot:
    """Records plot() and plotWithFill() calls instead of drawing."""

    def xValuesFor(self, image, bandCount):
        return VardaPlotWidget.getPlottableWavelengths(image, bandCount)

    def __init__(self):
        self.calls = []
        self.fillCalls = []

    def plot(self, x, y, color=None, name=None, **kwargs):
        self.calls.append(
            SimpleNamespace(x=np.asarray(x), y=np.asarray(y), color=color, name=name)
        )

    def plotWithFill(
        self,
        x,
        y,
        yLower,
        yUpper,
        fillBrush=None,
        color=None,
        name=None,
        std=None,
        count=None,
        **kwargs,
    ):
        call = SimpleNamespace(
            x=np.asarray(x),
            y=np.asarray(y),
            yLower=np.asarray(yLower),
            yUpper=np.asarray(yUpper),
            color=color,
            name=name,
            std=std,
            count=count,
            origin=None,
        )
        self.fillCalls.append(call)
        return call  # stands in for the curve


def test_an_roi_mean_curve_records_its_image_and_pixels(qtbot, make_split_image):
    c = ROICollection()
    fid = c.addROI(box(2, 2, 8, 8), "dunes", RED, ROIMode.RECTANGLE)
    plot = _FakePlot()
    image = make_split_image(40, 20, 3, 8.0, 4.0)
    w = ROIManagerWidget(c, image, plot)
    w.plotSpectrum(fid)
    origin = plot.fillCalls[0].origin
    assert origin.imageName == image.name
    assert origin.region == "ROI “dunes”"
    assert origin.pixelCount == 36


def test_plot_spectrum_records_filled_curve(qtbot, make_split_image):
    c = ROICollection()
    fid = c.addROI(box(2, 2, 8, 8), "roi", RED, ROIMode.RECTANGLE)
    plot = _FakePlot()
    w = ROIManagerWidget(c, make_split_image(40, 20, 3, 8.0, 4.0), plot)
    w.plotSpectrum(fid)
    assert len(plot.fillCalls) == 1
    np.testing.assert_array_almost_equal(plot.fillCalls[0].y, [8.0, 8.0, 8.0])
    assert plot.fillCalls[0].name == "roi"
    # the spread and per-band n go with the curve, for export
    np.testing.assert_array_almost_equal(plot.fillCalls[0].std, [0.0, 0.0, 0.0])
    assert list(plot.fillCalls[0].count) == [36, 36, 36]


def test_plot_spectrum_fill_band_is_mean_plus_minus_std(qtbot):
    # A 2x2 ROI over pixels with values 1, 2, 3, 4 -> mean 2.5, std ~1.118.
    data = np.array(
        [[[1.0], [2.0]], [[3.0], [4.0]]], dtype=np.float64
    )  # shape (2, 2, 1)

    def get_data(bandIndices=None, window=None):
        if window is None:
            return data
        r, col, h, w = window
        return data[r : r + h, col : col + w, :]

    image = SimpleNamespace(
        name="2x2",
        width=2,
        height=2,
        bandCount=1,
        nodata=None,
        filePath=None,
        crs=None,
        extraMetadata={},
        description="",
        sigMetadataChanged=SignalInstance(),
        wavelengths=np.array([0.0]),
        wavelengthsType=float,
        bandModel=BandModel.empty(1),
        getData=get_data,
    )
    c = ROICollection()
    fid = c.addROI(box(0, 0, 2, 2), "roi", RED, ROIMode.RECTANGLE)
    plot = _FakePlot()
    w = ROIManagerWidget(c, image, plot)
    w.plotSpectrum(fid)

    expected_std = float(np.std([1.0, 2.0, 3.0, 4.0]))  # population std, matches nanstd
    call = plot.fillCalls[0]
    np.testing.assert_array_almost_equal(call.y, [2.5])
    np.testing.assert_array_almost_equal(call.yLower, [2.5 - expected_std])
    np.testing.assert_array_almost_equal(call.yUpper, [2.5 + expected_std])


def test_set_denominator_updates_model_and_emits(qtbot, make_split_image):
    c = ROICollection()
    fid = c.addROI(box(2, 2, 8, 8), "roi", RED, ROIMode.RECTANGLE)
    w = ROIManagerWidget(c, make_split_image(40, 20, 3, 8.0, 4.0), _FakePlot())
    with qtbot.waitSignal(w.sigDenominatorChanged, timeout=500) as sig:
        w.setDenominator(fid)
    assert w.denominatorFid == fid
    assert w.model.denominatorFid == fid
    assert sig.args == [fid]


def test_plot_ratio_without_denominator_does_nothing(
    qtbot, monkeypatch, make_split_image
):
    import varda.rois.roi_manager_widget as mod

    monkeypatch.setattr(mod.QMessageBox, "information", lambda *a, **k: None)
    c = ROICollection()
    fid = c.addROI(box(2, 2, 8, 8), "roi", RED, ROIMode.RECTANGLE)
    plot = _FakePlot()
    w = ROIManagerWidget(c, make_split_image(40, 20, 3, 8.0, 4.0), plot)
    w.plotRatioSpectrum(fid)
    assert plot.calls == []


def test_plot_ratio_with_denominator_records_ratio(qtbot, make_split_image):
    c = ROICollection()
    num = c.addROI(box(2, 2, 8, 8), "num", RED, ROIMode.RECTANGLE)
    den = c.addROI(box(31, 2, 38, 8), "den", BLUE, ROIMode.RECTANGLE)
    plot = _FakePlot()
    w = ROIManagerWidget(c, make_split_image(40, 20, 3, 8.0, 4.0), plot)
    w.setDenominator(den)
    w.plotRatioSpectrum(num)
    assert len(plot.calls) == 1
    np.testing.assert_array_almost_equal(plot.calls[0].y, [2.0, 2.0, 2.0])
    assert plot.calls[0].name == "num / den"


def test_removing_denominator_clears_it(qtbot, make_split_image):
    c = ROICollection()
    den = c.addROI(box(31, 2, 38, 8), "den", BLUE, ROIMode.RECTANGLE)
    w = ROIManagerWidget(c, make_split_image(40, 20, 3, 8.0, 4.0), _FakePlot())
    w.setDenominator(den)
    c.removeROI(den)
    assert w.denominatorFid is None


def test_set_template_updates_model_and_emits(qtbot, make_split_image):
    c = ROICollection()
    fid = c.addROI(box(2, 2, 8, 8), "tmpl", RED, ROIMode.RECTANGLE)
    w = ROIManagerWidget(c, make_split_image(40, 20, 3, 8.0, 4.0), _FakePlot())
    with qtbot.waitSignal(w.sigTemplateChanged, timeout=500) as sig:
        w.setTemplate(fid)
    assert w.templateFid == fid
    assert w.model.templateFid == fid
    assert sig.args == [fid]


def test_place_template_plain_paste(qtbot, make_split_image):
    # Non-georeferenced collection -> geometries stored in pixel space.
    c = ROICollection()
    tmpl = c.addROI(box(2, 2, 6, 6), "tmpl", RED, ROIMode.RECTANGLE)  # centroid (4,4)
    w = ROIManagerWidget(c, make_split_image(40, 20, 3, 8.0, 4.0), _FakePlot())
    w.setTemplate(tmpl)
    before = len(c)
    w.placeTemplate(clickRow=14, clickCol=20)
    assert len(c) == before + 1
    new_fid = c.fids[-1]
    from shapely.geometry import Polygon

    copy = Polygon(c.getPixelCoordinates(new_fid))
    # The copy is the template shifted by a whole number of pixels (so it has
    # the template's exact footprint), centered as close as that allows to the
    # clicked pixel's center (20.5, 14.5).
    assert abs(copy.centroid.x - 20.5) <= 0.5
    assert abs(copy.centroid.y - 14.5) <= 0.5
    shift = (copy.centroid.x - 4.0, copy.centroid.y - 4.0)
    assert all(abs(s - round(s)) < 1e-6 for s in shift)


def test_place_template_copy_covers_pixels_centred_on_the_click(
    qtbot, make_split_image
):
    import rasterio.features
    from shapely.geometry import Polygon, mapping

    c = ROICollection()
    # 4x4 pixels (cols 2..5, rows 2..5); stored with a closing vertex, which
    # must not bias the copy's center.
    tmpl = c.addROI(box(2, 2, 6, 6), "tmpl", RED, ROIMode.RECTANGLE)
    w = ROIManagerWidget(c, make_split_image(40, 20, 3, 8.0, 4.0), _FakePlot())
    w.setTemplate(tmpl)
    w.placeTemplate(clickRow=14, clickCol=20)

    copy = Polygon(c.getPixelCoordinates(c.fids[-1]))
    mask = rasterio.features.rasterize(
        [(mapping(copy), 1)], out_shape=(40, 40), fill=0, dtype=np.uint8
    ).astype(bool)
    rows, cols = np.nonzero(mask)
    # An even-sized copy centered on the click extends right/down, the same
    # convention as boxPolygonPixels.
    assert set(cols.tolist()) == {19, 20, 21, 22}
    assert set(rows.tolist()) == {13, 14, 15, 16}


def test_place_template_noop_without_template(qtbot, make_split_image, monkeypatch):
    import varda.rois.roi_manager_widget as mod

    monkeypatch.setattr(mod.QMessageBox, "information", lambda *a, **k: None)
    c = ROICollection()
    w = ROIManagerWidget(c, make_split_image(40, 20, 3, 8.0, 4.0), _FakePlot())
    w.placeTemplate(clickRow=5, clickCol=5)
    assert len(c) == 0


def test_removing_template_clears_it(qtbot, make_split_image):
    c = ROICollection()
    fid = c.addROI(box(2, 2, 6, 6), "tmpl", RED, ROIMode.RECTANGLE)
    w = ROIManagerWidget(c, make_split_image(40, 20, 3, 8.0, 4.0), _FakePlot())
    w.setTemplate(fid)
    c.removeROI(fid)
    assert w.templateFid is None


def test_the_manager_offers_the_shape_and_class_for_drawing(qtbot, make_split_image):
    c = ROICollection()
    state = ROIDrawingState(c)
    w = ROIManagerWidget(
        c, make_split_image(40, 20, 3, 8.0, 4.0), _FakePlot(), drawingState=state
    )
    qtbot.addWidget(w)
    assert w.findChild(ROIShapePicker).state is state
    assert w.findChild(ROIClassPicker).state is state


def test_select_all_and_deselect(qtbot, make_split_image):
    c = ROICollection()
    for i in range(3):
        c.addROI(box(2 + i, 2, 8 + i, 8), f"r{i}", RED, ROIMode.RECTANGLE)
    w = ROIManagerWidget(c, make_split_image(40, 20, 3, 8.0, 4.0), _FakePlot())
    qtbot.addWidget(w)
    w.selectAllButton.click()
    assert w.selectedFids() == c.fids
    w.deselectButton.click()
    assert w.selectedFids() == []


def test_delete_selected_removes_every_selected_roi_after_asking(
    qtbot, make_split_image, monkeypatch
):
    c = ROICollection()
    for i in range(3):
        c.addROI(box(2 + i, 2, 8 + i, 8), f"r{i}", RED, ROIMode.RECTANGLE)
    w = ROIManagerWidget(c, make_split_image(40, 20, 3, 8.0, 4.0), _FakePlot())
    qtbot.addWidget(w)
    w.selectAllButton.click()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    w._deleteBtn.click()
    assert len(c) == 3
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    w._deleteBtn.click()
    assert len(c) == 0


def test_area_columns_fill_in_from_the_background(qtbot):
    """The widget computes pixel areas off the GUI thread; the columns are
    blank until they arrive."""
    image = makeImage(LRO_WAC_PROJ, centerLonLat=(0.0, 0.0), pixelSize=100.0)
    c = ROICollection.fromImage(image)
    (x0, y0), (x1, y1) = image.transform * (27, 27), image.transform * (37, 37)
    fid = c.addROI(box(x0, y1, x1, y0), "r", RED, ROIMode.RECTANGLE)
    w = ROIManagerWidget(c, image, cast(VardaPlotWidget, _FakePlot()))
    qtbot.addWidget(w)
    w.calculatedColumns.areaCache = PixelAreaCache()
    w.calculatedColumns.add("a", ZonalStatistic(image, 0, "area_km2"))
    qtbot.waitUntil(lambda: np.isfinite(c.getProperty(fid, "a")), timeout=10000)
    assert c.getProperty(fid, "a") == pytest.approx(1.0, rel=1e-6)
