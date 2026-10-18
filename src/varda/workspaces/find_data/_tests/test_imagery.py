"""The imagery streamer follows the map's view only while it exists."""

from PyQt6 import sip

from varda.workspaces.find_data.imagery import ImageryStreamer
from varda.workspaces.find_data.map_view import FootprintMap


def test_a_deleted_streamer_ignores_the_map_moving(qtbot):
    """The map is the streamer's sibling, not its child: panning it after the
    streamer is gone must not start the deleted streamer's timer."""
    footprintMap = FootprintMap()
    qtbot.addWidget(footprintMap)
    streamer = ImageryStreamer(footprintMap)
    sip.delete(streamer)
    footprintMap.viewBox.setRange(xRange=(10, 20), yRange=(10, 20), padding=0)
