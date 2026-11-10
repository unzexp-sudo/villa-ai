"""Lazy singletons for the store, event bus and run limits.

Sibling modules are being written concurrently, so every dependency is
imported INSIDE the function body and wrapped with a clear ``RuntimeError`` if
it is unavailable yet. Importing this module itself must never fail.
"""

import threading

_lock = threading.RLock()
_state: dict = {"store": None, "bus": None, "limits": None}


def get_store():
    """Return the process-wide JsonFileStore."""
    with _lock:
        if _state["store"] is None:
            try:
                from app.core.config import settings
                from app.core.store import JsonFileStore
            except ImportError as exc:
                raise RuntimeError(
                    "get_store(): store/config dependency is unavailable "
                    f"while the tree is being built ({exc})"
                ) from exc
            _state["store"] = JsonFileStore(settings.DATA_DIR)
        return _state["store"]


def get_bus():
    """Return the process-wide EventBus (shares the store)."""
    with _lock:
        if _state["bus"] is None:
            try:
                from app.core.emitter import EventBus
            except ImportError as exc:
                raise RuntimeError(
                    "get_bus(): emitter dependency is unavailable while the "
                    f"tree is being built ({exc})"
                ) from exc
            _state["bus"] = EventBus(get_store())
        return _state["bus"]


def get_limits():
    """Return the process-wide RunLimits value object."""
    with _lock:
        if _state["limits"] is None:
            try:
                from app.core.config import get_limits as _get_limits
            except ImportError as exc:
                raise RuntimeError(
                    "get_limits(): config dependency is unavailable while the "
                    f"tree is being built ({exc})"
                ) from exc
            _state["limits"] = _get_limits()
        return _state["limits"]


def reset_state() -> None:
    """Drop all singletons (for tests)."""
    with _lock:
        _state["store"] = None
        _state["bus"] = None
        _state["limits"] = None


__all__ = ["get_store", "get_bus", "get_limits", "reset_state"]
