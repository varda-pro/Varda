"""What a workspace provides so that sessions can save and restore it and
Duplicate Workspace can copy it. Each workspace class implements this
Protocol (structurally: Qt widgets can't subclass a Protocol) and is listed
in ``workspaces/registry.py``."""

from __future__ import annotations

from collections.abc import MutableSequence, Sequence
from typing import ClassVar, Protocol, Self

from varda.common.entities import VardaRaster
from varda.image_rendering.image_renderer import ImageRenderer
from varda.rois.roi_collection import ROICollection


class Workspace(Protocol):
    # The workspace's name in session files: never change a released one
    kind: ClassVar[str]

    @classmethod
    def fromSession(
        cls,
        images: Sequence[VardaRaster],
        projectImages: MutableSequence[VardaRaster],
        state: dict,
    ) -> Self | None:
        """A workspace on ``images`` (as ``sessionImages`` gave them), offered
        the (live) project images, with ``state`` (from ``sessionState``)
        applied; None, logged, if the images don't fit this kind."""
        ...

    def sessionImages(self) -> list[VardaRaster]:
        """The images the workspace shows, which a session must keep."""
        ...

    def sessionState(self) -> dict:
        """Its own settings, as JSON-ready values."""
        ...

    def displayRenderers(self) -> list[ImageRenderer]:
        """The renderers whose band and stretch settings make up its display,
        in a fixed order (a duplicate gets copies of their settings)."""
        ...

    @property
    def roiCollection(self) -> ROICollection | None:
        """Its ROIs, saved with it and copied to a duplicate; None if it has
        none."""
        ...
