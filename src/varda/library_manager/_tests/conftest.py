import os

# Run Qt headless for these widget tests. Must be set before any QApplication is
# created (pytest-qt creates it lazily when qtbot/qapp is first used).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
