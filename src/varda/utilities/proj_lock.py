"""PROJ (pyproj) across threads: one lock, and contexts that outlive a task.

PROJ is not thread-safe: hold PROJ_LOCK around CRS construction, transformer
creation, transforms and WKT export.

pyproj keeps one PROJ context per OS thread, but frees it with the thread's
*Python* state. A Qt pool thread is not a Python thread: its Python state is
made for each task and dropped after it, so pyproj frees the thread's context
while still handing out its pointer, and the next task's first PROJ call on
that thread uses freed memory (a SIGSEGV in proj_create, in
``proj_log_debug``). ``keepProjContext`` keeps each pool thread's context
alive; ``runInBackground`` calls it after every task.
"""

import threading

from pyproj._context import get_context_manager  # ty: ignore[unresolved-import]

PROJ_LOCK = threading.RLock()

_threadContexts: dict[int, object] = {}  # native thread id: pyproj's context owner


def keepProjContext() -> None:
    """Keep the calling thread's PROJ context, if it has one, alive for the
    rest of the process, so pyproj can't free it when a Qt pool thread's
    Python state is dropped between tasks. One context per pool thread."""
    manager = get_context_manager()
    if manager is not None:
        with PROJ_LOCK:
            _threadContexts[threading.get_native_id()] = manager
