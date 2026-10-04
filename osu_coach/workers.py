"""The worker processes the parallel jobs share: judging plays, reading map types, the ratings.

Starting a pool of processes takes half a second or more (longer in the packaged app), and a search reads map types
ten maps at a time: one pool, started the first time it is needed and stopped after IDLE_S without work, so its
memory goes back while nothing runs. A job hands it a few tasks at a time (imap's window), so two jobs running at
once share the workers instead of one waiting for the other's whole queue.
"""

import itertools
import os
import threading
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

WORKERS = os.cpu_count() or 2
IDLE_S = 120.0
PARALLEL_MIN = 8   # fewer tasks than this aren't worth starting the workers for (when they aren't running already)

_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None
_users = 0
_timer: threading.Timer | None = None


def _acquire() -> ProcessPoolExecutor:
    global _pool, _users, _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=WORKERS)
        _users += 1
        return _pool


def _release(pool: ProcessPoolExecutor, broken: bool):
    global _pool, _users, _timer
    with _lock:
        _users -= 1
        if broken and _pool is pool:   # a worker died: the next job starts a new pool
            _pool = None
        if _users == 0 and _pool is not None and _timer is None:
            _timer = threading.Timer(IDLE_S, _stop_idle)
            _timer.daemon = True
            _timer.start()
    if broken:
        pool.shutdown(wait=False, cancel_futures=True)


def _stop_idle():
    global _pool, _timer
    with _lock:
        _timer = None
        if _users or _pool is None:
            return
        pool, _pool = _pool, None
    pool.shutdown(wait=False, cancel_futures=True)


def imap(fn, items, window: int | None = None, parallel_min: int = PARALLEL_MIN):
    """fn over items, the results in order: in the shared worker processes, at most `window` tasks queued at a time
    (default: two per worker); in this process when there are fewer than `parallel_min` items and the workers aren't
    running. Closing the generator (contextlib.closing) cancels the tasks that haven't started."""
    items = list(items)
    if len(items) < parallel_min and _pool is None:
        for a in items:
            yield fn(a)
        return
    pool = _acquire()
    broken = False
    pending = deque()
    rest = iter(items)
    try:
        for a in itertools.islice(rest, window or 2 * WORKERS):
            pending.append(pool.submit(fn, a))
        while pending:
            result = pending.popleft().result()
            for a in itertools.islice(rest, 1):
                pending.append(pool.submit(fn, a))
            yield result
    except BrokenProcessPool:
        broken = True
        raise
    finally:
        for f in pending:
            f.cancel()
        _release(pool, broken)
