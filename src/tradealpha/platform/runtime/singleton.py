"""
Process singleton (finding SC-01). Only the process holding ``<instance>/historify.lock`` may run the scheduler,
background workers and own the DuckDB bar store. A second launch keeps serving nothing in the background and logs
a clear error instead of duplicating every scheduled download.
"""
from __future__ import annotations

import logging
import os

from filelock import FileLock, Timeout

log = logging.getLogger(__name__)
_held: dict[str, FileLock] = {}


def acquire_instance_lock(instance_path: str, name: str = "historify") -> bool:
    path = os.path.join(instance_path, f"{name}.lock")
    if path in _held:
        return True
    os.makedirs(instance_path, exist_ok=True)
    lock = FileLock(path, is_singleton=False, thread_local=False)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        log.error("Another TradeAlpha process holds %s; background services will NOT start in this process.", path)
        return False
    _held[path] = lock
    return True


def release_instance_lock(instance_path: str, name: str = "historify") -> None:
    path = os.path.join(instance_path, f"{name}.lock")
    lock = _held.pop(path, None)
    if lock:
        lock.release()
