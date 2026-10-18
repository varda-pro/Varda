"""ROIs from masks (a threshold, a mask image): one ROI per connected
region, sharing a class name, specks below a size dropped; and the class
masks back out (ROI to Mask)."""

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS

from varda.analysis.analysis import AnalysisContext
from varda.analysis.image_math import RoiToMaskAnalysis
from varda.analysis.registry import ANALYSES
from varda.common.entities import Color, VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.rois.mask_rois import addMaskAsRois, maskRegions, thresholdMask
from varda.rois.roi_collection import ROICollection

RED = Color(1.0, 0.0, 0.0, 0.5)


def _mask():
    mask = np.zeros((20, 30), bool)
    mask[2:8, 3:10] = True  # 42 px
    mask[12:18, 15:28] = True  # 78 px
    mask[0, 29] = True  # a speck
    return mask


def _image(georeferenced=True):
    data = np.zeros((20, 30, 2), np.float32)
    data[..., 1] = np.arange(30)[None, :]  # band 2: the column number
    if not georeferenced:
        return VardaRaster(ArrayDataSource(data), name="plain")
    return VardaRaster(
        ArrayDataSource(
            data,
            transform=Affine(18, 0, 1000, 0, -18, 5000),
            crs=CRS.from_user_input("+proj=eqc +R=3396190 +units=m +no_defs"),
        ),
        name="scene",
    )


def test_connected_regions_become_polygons_largest_first():
    regions = maskRegions(_mask())
    assert [round(r.area) for r in regions] == [78, 42, 1]
    assert [round(r.area) for r in maskRegions(_mask(), minPixels=4)] == [78, 42]
    assert len(maskRegions(_mask(), maxRegions=1)) == 1


@pytest.mark.parametrize("georeferenced", [True, False])
def test_a_mask_becomes_a_class_whose_mask_is_the_mask(georeferenced):
    image = _image(georeferenced)
    rois = ROICollection.fromImage(image)
    added, dropped = addMaskAsRois(rois, _mask(), image, "bright", RED, minPixels=4)
    assert (added, dropped) == (2, 1)
    assert list(rois.classes()) == ["bright"]
    expected = _mask()
    expected[0, 29] = False  # the speck was dropped
    np.testing.assert_array_equal(rois.classMask("bright", image), expected)


def test_a_threshold_selects_inside_or_outside_a_range():
    image = _image()
    inside = thresholdMask(image, 1, 10, 19)
    assert inside.sum() == 20 * 10 and inside[:, 10:20].all()
    outside = thresholdMask(image, 1, 10, 19, inside=False)
    assert (outside == ~inside).all()


def test_roi_classes_become_a_mask():
    assert RoiToMaskAnalysis in ANALYSES
    image = _image()
    rois = ROICollection.fromImage(image)
    addMaskAsRois(rois, _mask(), image, "bright", RED)
    analysis = RoiToMaskAnalysis()
    analysis.setContext(AnalysisContext(rois=rois))
    analysis.prepareFor(image)
    assert analysis.classes.value == ["bright"]
    mask = analysis.run(image, lambda *_: None).getData()[..., 0]
    np.testing.assert_array_equal(mask, _mask().astype(float))
    assert analysis.unavailableReason(image) == ""
    analysis.setContext(AnalysisContext(rois=ROICollection.fromImage(image)))
    assert "ROI" in analysis.unavailableReason(image)


def test_the_dialog_makes_a_class_from_a_threshold_or_a_mask(qtbot):
    from varda.rois.mask_roi_dialog import NewClassFromMaskDialog

    image = _image()
    maskImage = VardaRaster(
        ArrayDataSource(
            _mask()[..., None].astype(np.float32),
            transform=image.transform,
            crs=image.crs,
        ),
        name="bright mask",
    )
    rois = ROICollection.fromImage(image)
    dialog = NewClassFromMaskDialog(rois, image, [image, maskImage])
    qtbot.addWidget(dialog)
    dialog.band.setValue(2)  # the column number, 0-29
    assert (dialog.low.value(), dialog.high.value()) == (0, 29)
    dialog.low.setValue(10)
    dialog.high.setValue(19)
    assert "200 of 600" in dialog.count.text()
    dialog.name.setText("middle")
    dialog.accept()
    np.testing.assert_array_equal(rois.classMask("middle", image)[:, 10:20], True)

    dialog = NewClassFromMaskDialog(rois, image, [image, maskImage])
    qtbot.addWidget(dialog)
    dialog.fromMask.setChecked(True)
    dialog.name.setText("bright")
    dialog.minPixels.setValue(1)
    dialog.accept()
    np.testing.assert_array_equal(rois.classMask("bright", image), _mask())


def test_the_dialog_takes_a_two_band_mask_or_one_on_another_grid(qtbot):
    from varda.rois.mask_roi_dialog import NewClassFromMaskDialog

    image = _image()
    # a Shadow Mask's output: the mask, then a diagnostic band (never 0)
    twoBands = VardaRaster(
        ArrayDataSource(
            np.stack([_mask(), np.full((20, 30), 7.0)], axis=2).astype(np.float32),
            transform=image.transform,
            crs=image.crs,
        ),
        name="shadow mask",
    )
    # the same mask on a 2× finer grid over the same ground
    t = image.transform
    fine = VardaRaster(
        ArrayDataSource(
            np.repeat(np.repeat(_mask(), 2, 0), 2, 1)[..., None].astype(np.float32),
            transform=Affine(t.a / 2, 0, t.c, 0, t.e / 2, t.f),
            crs=image.crs,
        ),
        name="fine mask",
    )
    plain = _image(georeferenced=False)
    rois = ROICollection.fromImage(image)
    dialog = NewClassFromMaskDialog(rois, image, [image, twoBands, fine, plain])
    qtbot.addWidget(dialog)
    assert dialog.masks == [twoBands, fine]
    assert "plain (not georeferenced" in dialog.notListed.text()
    dialog.fromMask.setChecked(True)
    np.testing.assert_array_equal(dialog.chosenPixels(), _mask())  # band 1
    dialog.maskBox.setCurrentIndex(1)
    np.testing.assert_array_equal(dialog.chosenPixels(), _mask())
    assert "fine mask resampled from 9.0 m to 18.0 m pixels (nearest)" in (
        dialog.resampled.text()
    )
