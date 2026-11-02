# standard library

# third party imports
import numpy as np

# local imports


class Normalize:
    """Test Process"""

    name = "Normalize"

    # not being used yet. will be used for categorization
    path = "Basic Processing/Normalize"

    parameters = {
        "threshold": {
            "type": float,
            "default": 0.0,
            "description": "Threshold value to be added to the minimum and "
            "subtracted from the maximum value of the image.",
        },
    }

    def __init__(self):
        super().__init__()

    def execute(self, image, threshold=0.0):
        min_val = np.min(image) + threshold
        max_val = np.max(image) - threshold

        return np.clip((image - min_val) / (max_val - min_val), 0, 1)
