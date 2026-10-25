"""Live output of a tool run: model text as it is generated, flow progress lines.

engine.complete() reads every answer in chunks anyway; it hands each chunk to
the listeners of the current thread (token), and says when a new model call
begins (start). A flow's print() lines go to the same listeners (log) through a
stdout router that sends a thread's writes to its listeners and everything else
to the real stdout — so two runs in two server threads never mix, and nothing
global is swapped while a run is going on.

    with live.capture(on_token=..., on_start=..., on_log=...):
        result = run_the_tool()

Captures nest: each listener of the stack gets the events.
"""
from __future__ import annotations

import io
import sys
import threading
from contextlib import contextmanager

_local = threading.local()
_install_lock = threading.Lock()


def _stack() -> list:
    st = getattr(_local, "stack", None)
    if st is None:
        st = _local.stack = []
    return st


def start() -> None:
    """A new model call begins."""
    for h in _stack():
        if h.get("start"):
            h["start"]()


def token(text: str) -> None:
    if not text:
        return
    for h in _stack():
        if h.get("token"):
            h["token"](text)


class _Router(io.TextIOBase):
    """sys.stdout that sends a capturing thread's writes to its listeners."""

    def __init__(self, real):
        self._real = real

    def write(self, s):
        handlers = [h["log"] for h in _stack() if h.get("log")]
        if not handlers:
            return self._real.write(s)
        for h in handlers:
            h(s)
        return len(s)

    def flush(self):
        try:
            self._real.flush()
        except Exception:
            pass

    @property
    def encoding(self):
        return getattr(self._real, "encoding", "utf-8")

    def isatty(self):
        return False


def _install_router() -> None:
    with _install_lock:
        if not isinstance(sys.stdout, _Router):
            sys.stdout = _Router(sys.stdout)


@contextmanager
def capture(on_token=None, on_start=None, on_log=None):
    if on_log:
        _install_router()
    handler = {"token": on_token, "start": on_start, "log": on_log}
    _stack().append(handler)
    try:
        yield
    finally:
        _stack().remove(handler)
