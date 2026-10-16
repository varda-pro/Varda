import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def cropDir() -> Path:
    """The folder of real-data crops made by ``data/make_crops.py``."""
    return Path(__file__).parent / "data"


@pytest.fixture
def demoData() -> Path:
    """The demo data folder (``VARDA_DEMO_DATA``); skips the test when it is absent."""
    folder = Path(
        os.environ.get("VARDA_DEMO_DATA", REPO_ROOT.parent / "varda-demo-data")
    )
    if not (folder / "data-for-paper").is_dir():
        pytest.skip(f"demo data not found at {folder}")
    return folder
