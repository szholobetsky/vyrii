"""In-memory request statistics, per-host semaphore queue, and lock/reserve."""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_log: list[dict] = []
_active: dict[int, dict] = {}
_next_id = 0

_WINDOW = 15 * 60

# ── Per-host semaphore (Stage 1) ─────────────────────────────────────────────
_host_sems: dict[str, threading.Semaphore] = {}


def get_host_sem(host: str) -> threading.Semaphore:
    with _lock:
        if host not in _host_sems:
            _host_sems[host] = threading.Semaphore(1)
        return _host_sems[host]


_host_queue: dict[str, list[int]] = {}
_host_queue_seq: dict[str, int] = {}


def queue_position(host: str, ticket: int) -> int:
    with _lock:
        q = _host_queue.get(host, [])
        if ticket in q:
            return q.index(ticket) + 1
        return 0


def wait_for_host(host: str):
    """Generator — yields personal queue position every ~2s while waiting.
    When exhausted, semaphore is acquired. Caller MUST call release_host_sem()."""
    sem = get_host_sem(host)
    with _lock:
        _host_queue_seq[host] = _host_queue_seq.get(host, 0) + 1
        ticket = _host_queue_seq[host]
        _host_queue.setdefault(host, []).append(ticket)
    try:
        while not sem.acquire(timeout=2):
            yield queue_position(host, ticket)
    finally:
        with _lock:
            q = _host_queue.get(host, [])
            if ticket in q:
                q.remove(ticket)


def release_host_sem(host: str) -> None:
    sem = get_host_sem(host)
    try:
        sem.release()
    except ValueError:
        pass


# ── Lock/Reserve (Stage 3) ───────────────────────────────────────────────────
_host_locks: dict[str, dict] = {}
_MAX_LOCK = 30 * 60


def lock_host(host: str, ip: str, mode: str = "response",
              timeout: int = 600) -> dict:
    with _lock:
        info = _check_lock_expired(host)
        if info and info["locked_by"] != ip:
            return {"ok": False, "error": f"locked by {info['locked_by']}",
                    "locked_by": info["locked_by"],
                    "remaining": _remaining(info)}
        _host_locks[host] = {"locked_by": ip, "locked_at": time.time(),
                             "mode": mode, "timeout": timeout}
        return {"ok": True}


def release_host(host: str, ip: str | None = None) -> None:
    with _lock:
        info = _host_locks.get(host)
        if info and (ip is None or info["locked_by"] == ip):
            _host_locks.pop(host, None)


def auto_release_host(host: str) -> None:
    with _lock:
        info = _host_locks.get(host)
        if info and info["mode"] == "response":
            _host_locks.pop(host, None)


def check_lock(host: str, ip: str) -> bool:
    with _lock:
        info = _check_lock_expired(host)
        if not info:
            return True
        return info["locked_by"] == ip


def get_lock_info(host: str) -> dict | None:
    with _lock:
        return _check_lock_expired(host)


def get_all_locks() -> dict:
    with _lock:
        now = time.time()
        result = {}
        for h in list(_host_locks):
            info = _check_lock_expired(h)
            if info:
                result[h] = {**info, "remaining": _remaining(info)}
        return result


def _check_lock_expired(host: str) -> dict | None:
    info = _host_locks.get(host)
    if not info:
        return None
    elapsed = time.time() - info["locked_at"]
    max_t = min(info["timeout"], _MAX_LOCK) if info["mode"] == "timer" else _MAX_LOCK
    if elapsed > max_t:
        _host_locks.pop(host, None)
        return None
    return info


def _remaining(info: dict) -> int:
    max_t = min(info["timeout"], _MAX_LOCK) if info["mode"] == "timer" else _MAX_LOCK
    return max(0, int(max_t - (time.time() - info["locked_at"])))


def record_start(host: str, model: str) -> int:
    global _next_id
    with _lock:
        _prune()
        _next_id += 1
        entry = {"id": _next_id, "host": host, "model": model,
                 "start": time.time(), "end": None}
        _active[_next_id] = entry
        _log.append(entry)
        return _next_id


def record_end(request_id: int) -> None:
    with _lock:
        entry = _active.pop(request_id, None)
        if entry:
            entry["end"] = time.time()


def get_stats() -> list[dict]:
    with _lock:
        _prune()
        now = time.time()
        hosts: dict[str, dict] = {}
        for e in _log:
            h = e["host"]
            if h not in hosts:
                hosts[h] = {"host": h, "active": 0,
                            "req_1m": 0, "req_5m": 0, "req_15m": 0}
            s = hosts[h]
            age = now - e["start"]
            if age <= 60:
                s["req_1m"] += 1
            if age <= 300:
                s["req_5m"] += 1
            s["req_15m"] += 1
            if e["end"] is None:
                s["active"] += 1
        return sorted(hosts.values(), key=lambda x: x["host"])


def _prune() -> None:
    cutoff = time.time() - _WINDOW
    while _log and _log[0]["start"] < cutoff and _log[0]["end"] is not None:
        old = _log.pop(0)
        _active.pop(old["id"], None)
