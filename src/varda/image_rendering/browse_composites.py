"""The browse products images are showing, computed once per image and
product however many views show them (a workspace's main and zoom views,
linked views), in the background, and kept while the image is open."""

import weakref

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from varda.analysis.browse_products import BrowseProduct, computeProduct
from varda.common.entities import VardaRaster
from varda.utilities.background import onMainThread, runInBackground

KEEP_PER_IMAGE = 6  # the latest composites of each image (a few MB each)


class BrowseComposites(QObject):
    # image, product text, and: nothing / percent and message / the error
    sigReady = pyqtSignal(object, str)
    sigProgress = pyqtSignal(object, str, int, str)
    sigFailed = pyqtSignal(object, str, str)

    def __init__(self) -> None:
        super().__init__()
        self._done: weakref.WeakKeyDictionary[VardaRaster, dict[str, np.ndarray]] = (
            weakref.WeakKeyDictionary()
        )
        self._running: weakref.WeakKeyDictionary[VardaRaster, set[str]] = (
            weakref.WeakKeyDictionary()
        )
        self._failed: weakref.WeakKeyDictionary[VardaRaster, dict[str, str]] = (
            weakref.WeakKeyDictionary()
        )
        # Bumped by forget: a result computed before then is dropped
        self._generation: weakref.WeakKeyDictionary[VardaRaster, int] = (
            weakref.WeakKeyDictionary()
        )

    def get(self, image: VardaRaster, product: BrowseProduct) -> np.ndarray | None:
        """The composite, or None until it's ready (asking for it with
        ``request``)."""
        done = self._done.get(image, {})
        text = product.asText()
        if text in done:
            done[text] = done.pop(text)  # most recently used last
            return done[text]
        return None

    def isRunning(self, image: VardaRaster, product: BrowseProduct) -> bool:
        return product.asText() in self._running.get(image, set())

    def error(self, image: VardaRaster, product: BrowseProduct) -> str:
        """Why the last attempt failed ("" if it didn't)."""
        return self._failed.get(image, {}).get(product.asText(), "")

    def request(self, image: VardaRaster, product: BrowseProduct) -> None:
        """Compute the composite unless it's done, running or has failed;
        ``sigReady`` or ``sigFailed`` says when it's over."""
        text = product.asText()
        running = self._running.setdefault(image, set())
        if (
            text in running
            or text in self._done.get(image, {})
            or self.error(image, product)
        ):
            return
        running.add(text)
        generation = self._generation.get(image, 0)
        progress = onMainThread(lambda p, m: self.sigProgress.emit(image, text, p, m))
        runInBackground(
            lambda: computeProduct(image, product, progress),
            lambda composite: self._finished(image, text, generation, composite),
            lambda error: self._finished(image, text, generation, None, str(error)),
            description=f"Computing the {product.name} browse product",
            owner=self,
        )

    def forget(self, image: VardaRaster) -> None:
        """The image's data or wavelengths changed: compute anew."""
        self._done.pop(image, None)
        self._failed.pop(image, None)
        self._running.pop(image, None)
        self._generation[image] = self._generation.get(image, 0) + 1

    def _finished(
        self,
        image: VardaRaster,
        text: str,
        generation: int,
        composite: np.ndarray | None,
        error: str = "",
    ) -> None:
        if generation != self._generation.get(image, 0):
            return  # the image changed while it was computed
        self._running.get(image, set()).discard(text)
        if composite is None:
            self._failed.setdefault(image, {})[text] = error
            self.sigFailed.emit(image, text, error)
            return
        composite.setflags(write=False)  # shared by every view
        done = self._done.setdefault(image, {})
        done[text] = composite
        while len(done) > KEEP_PER_IMAGE:
            done.pop(next(iter(done)))
        self.sigReady.emit(image, text)


COMPOSITES = BrowseComposites()
