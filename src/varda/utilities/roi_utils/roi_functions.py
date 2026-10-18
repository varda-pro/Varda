from typing import Tuple

import numpy as np
import pyqtgraph as pg

from varda.common.entities import Image
from varda.utilities.roi_utils import RegionCoordinateTransform
from varda.common.entities import ROI


def createROIMask(points: np.ndarray, shape: Tuple[int, int]) -> np.ndarray:
    """
    Create a binary mask from ROI points

    Args:
        points: Nx2 array of (x, y) aka (row, col) coordinates defining the ROI polygon
        shape: (height, width) of the output mask
    """
    from matplotlib.path import Path

    # create a matplotlib path from the points
    path = Path(points)

    # Create a grid of all image coordinates
    y, x = np.mgrid[: shape[0], : shape[1]]
    coords = np.column_stack(
        (x.ravel() + 0.5, y.ravel() + 0.5)
    )  # +0.5 for pixel center

    # Test which points are inside the path
    mask = path.contains_points(coords).reshape(shape)

    return mask


def createROIMaskAlternative(points: np.ndarray, shape: Tuple[int, int]):
    """
    Create a binary mask from ROI points. But uses skimage polygon instead of matplotlib Path. Idk which is better

    Args:
        points: List of points defining the ROI
        shape: Shape of the image (height, width)

    Returns:
        Binary mask array
    """
    from skimage.draw import polygon

    x_coords = [p[0] for p in points]
    y_coords = [p[1] for p in points]

    # Create mask
    mask = np.zeros(shape, dtype=bool)
    rr, cc = polygon(y_coords, x_coords, shape)
    mask[rr, cc] = True

    return mask


def getRectImageRegion(
    roi: ROI, image: Image | np.ndarray, order=1, returnTransform=False
) -> Tuple[np.ndarray, RegionCoordinateTransform] | np.ndarray:
    """
    Uses the axis-aligned bounding box of the ROI to extract an array region.

    Args:
        roi: The ROI entity
        image: The image entity
        order: the type of resampling. 0 = nearest neighbor, 1 = bilinear, 2 = cubic, etc.
        returnTransform: If True, also return a RegionCoordinateTransform object for mapping coordinates
    """
    # get image data
    if isinstance(image, Image):
        data = image.raster
    elif isinstance(image, np.ndarray):
        data = image
    else:
        raise TypeError("image input must be an Image entity or a numpy ndarray.")

    # Bounding box of the polygon
    min_x, min_y, max_x, max_y = roi.getBounds()

    # validate that the bounding box is within the image
    if min_x < 0 or min_y < 0 or max_x > data.shape[1] or max_y > data.shape[0]:
        raise ValueError(
            f"ROI bounding box {min_x, min_y, max_x, max_y} is out of image bounds {data.shape[1], data.shape[0]}"
        )

    width = int(np.ceil(max_x - min_x))
    height = int(np.ceil(max_y - min_y))

    # Extract a rectangular slice of the data.
    shape = (height, width)
    origin = (min_y, min_x)
    vectors = ((1.0, 0.0), (0.0, 1.0))
    arraySlice = pg.affineSlice(
        data, shape, origin, vectors, axes=(0, 1), order=order, default=np.nan
    )
    if returnTransform:
        transform = RegionCoordinateTransform(origin=origin, basisVectors=vectors)
        return arraySlice, transform
    else:
        return arraySlice


def getMaskedArrayRegionSimple(
    roi: ROI, image: Image | np.ndarray, order=1, returnTransform=False
) -> Tuple[np.ndarray, RegionCoordinateTransform] | np.ndarray:
    """
    Uses the axis-aligned bounding box of the ROI to extract an array region, and applies a mask based on the ROI shape.

    Args:
        roi: The ROI entity
        image: The image entity
        order: the type of resampling. 0 = nearest neighbor, 1 = bilinear, 2 = cubic, etc.
        returnTransform: If True, also return a RegionCoordinateTransform object for mapping coordinates
    Returns:
        The masked array region, and optionally the coordinate transform
    """
    if returnTransform:
        arraySlice, transform = getRectImageRegion(roi, image, order, returnTransform)
    else:
        arraySlice = getRectImageRegion(roi, image, order, returnTransform)
        transform = None

    # # get image data
    # if isinstance(image, Image):
    #     data = image.raster
    # elif isinstance(image, np.ndarray):
    #     data = image
    # else:
    #     raise TypeError("image input must be an Image entity or a numpy ndarray.")
    #
    # # Bounding box of the polygon
    # min_x, min_y, max_x, max_y = roi.getBounds()
    #
    # # validate that the bounding box is within the image
    # if min_x < 0 or min_y < 0 or max_x > data.shape[1] or max_y > data.shape[0]:
    #     raise ValueError(
    #         f"ROI bounding box {min_x, min_y, max_x, max_y} is out of image bounds {data.shape[1], data.shape[0]}"
    #     )
    #
    # width = int(np.ceil(max_x - min_x))
    # height = int(np.ceil(max_y - min_y))
    #
    # # Extract a rectangular slice of the data.
    # shape = (height, width)
    # origin = (min_y, min_x)
    # vectors = ((1.0, 0.0), (0.0, 1.0))
    # arraySlice = pg.affineSlice(
    #     data, shape, origin, vectors, axes=(0, 1), order=order, default=np.nan
    # )

    # Mask out only the polygon region
    min_x, min_y, max_x, max_y = roi.getBounds()
    width = int(np.ceil(max_x - min_x))
    height = int(np.ceil(max_y - min_y))
    localPoints = roi.points - np.array([min_x, min_y])
    mask = createROIMask(localPoints, (height, width))
    mask = ~mask
    # reshape mask to match arraySlice dimensions, if necessary (sometimes arrayslice is 2d and sometimes 3d)
    if mask.ndim < arraySlice.ndim:
        mask = np.broadcast_to(~mask[..., np.newaxis], arraySlice.shape)
    maskedArray = np.ma.masked_array(arraySlice, mask=mask)
    # np.newaxis is to explicitly give the mask the same number of dimensions as arraySlice. For some reason need to do that.
    # maskedArray = np.where(mask[..., np.newaxis], arraySlice, np.nan)

    if returnTransform:
        return maskedArray, transform

    return maskedArray
