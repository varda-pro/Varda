"""The one way Varda runs work off the GUI thread.

``runInBackground`` runs a function on Qt's global thread pool, then calls
``onDone`` with its result, or ``onFailed`` with the exception, on the GUI
thread. A task that reports progress is given a callback wrapped with
``onMainThread``, which is safe to call from the worker. With an ``owner``
(the widget the result is for), a result that arrives after Qt deleted the
owner (its workspace was closed, say) is dropped.

Workers may read images while the display does: data sources keep reads
thread-safe (see ``RasterioDataSource`` and ``CachedDataSource``).
"""

import logging
from collections.abc import Callable

from PyQt6 import sip
from PyQt6.QtCore import QObject
from superqt.utils import FunctionWorker, create_worker, ensure_main_thread

from varda.utilities.proj_lock import keepProjContext

logger = logging.getLogger(__name__)


def runInBackground[R](
    task: Callable[[], R],
    onDone: Callable[[R], None],
    onFailed: Callable[[Exception], None],
    *,
    description: str = "Background task",
    owner: QObject | None = None,
) -> FunctionWorker[R]:
    """Run ``task`` on a worker thread; its outcome comes back on the GUI thread.
    A failure is logged as "<description> failed" before ``onFailed`` runs.
    Neither runs once ``owner`` (if given) has been deleted."""

    def gone() -> bool:
        return owner is not None and sip.isdeleted(owner)

    def done(result: R) -> None:
        if not gone():
            onDone(result)

    def failed(error: Exception) -> None:
        logger.error("%s failed", description, exc_info=error)
        if not gone():
            onFailed(error)

    def work() -> R:
        try:
            return task()
        finally:
            keepProjContext()  # pyproj would free it between pool tasks

    worker = create_worker(
        work, _start_thread=False, _connect={"returned": done, "errored": failed}
    )
    worker.start()
    return worker


def onMainThread(callback: Callable[..., object]) -> Callable[..., None]:
    """``callback``, safe to call from a worker: each call runs on the GUI
    thread, in order, without the worker waiting for it."""
    run = ensure_main_thread(lambda *args: callback(*args))

    def call(*args) -> None:
        run(*args)

    return call
