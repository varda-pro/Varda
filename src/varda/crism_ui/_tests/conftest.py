import os

# Run Qt headless for tests in this directory. Must be set before any QApplication
# is created (pytest-qt creates it lazily when qtbot/qapp is first used).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def demoData() -> Path:
    """The demo data folder (``VARDA_DEMO_DATA``); skips the test when it is absent."""
    folder = Path(
        os.environ.get("VARDA_DEMO_DATA", REPO_ROOT.parent / "varda-demo-data")
    )
    if not (folder / "data-for-paper").is_dir():
        raise pytest.skip.Exception(f"demo data not found at {folder}")
    return folder
