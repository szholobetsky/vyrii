"""Process — tools started from vyrii on a folder inside ~/.vyrii.

Only the commands of process_tools.json can be started (no free command line):
~/.config/vyrii/process_tools.json when it exists (outside ~/.vyrii, where
Files → Upload cannot reach) — it replaces the packaged one whole,
so another stack of tools needs no change here — else the packaged default (the
pantheon: alkonost, simargl, gamayun, svitovyd, mokosh UIs; syryn without a
web page). Each start is
remembered in ~/.vyrii/process/processes.json — tool, folder, port, pid — so the
Process tab shows what runs where, also after vyrii restarts, and can stop it.

  start(tool, folder, bind) → entry       a free port from the tool's default up
  entries()                 → [entry]     with status: running / starting / exited
  stop(id)                  → entry       kills the process tree vyrii started
  remove(id)                              forgets an exited entry

The same tool on the same folder runs once: start() returns the running entry
with "already": True instead of a second copy. When the tool's default port is
already held — by a copy started elsewhere (terminal, ssh) or by another vyrii
start — start() runs nothing and returns {"need_port": True, "suggest": N}: the
person picks the port, N is free and no listed tool's default (nor reserved_ports).
Processes vyrii did not start are never touched.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

HOME     = Path.home() / ".vyrii"
PROC_DIR = HOME / "process"
REGISTRY = PROC_DIR / "processes.json"

# outside ~/.vyrii on purpose: Files → Upload writes there, and whoever writes this
# file decides what Run starts
USER_TOOLS    = Path.home() / ".config" / "vyrii" / "process_tools.json"
DEFAULT_TOOLS = Path(__file__).with_name("process_tools.json")
PORT_TRIES = 200
STARTUP_GRACE = 90      # seconds a fresh process may take before its port answers


class ProcessError(Exception):
    pass


# ── registry ──────────────────────────────────────────────────────────────────

def _load() -> list[dict]:
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def load_tools() -> dict:
    """{"source": path, "tools": {name: spec}, "reserved": set of ports}."""
    path = USER_TOOLS if USER_TOOLS.is_file() else DEFAULT_TOOLS
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProcessError(f"cannot read {path}: {e}") from e
    specs = {}
    for t in data.get("tools") or []:
        if isinstance(t, dict) and t.get("name") and isinstance(t.get("args", []), list):
            specs[str(t["name"])] = t
    reserved = {int(p) for p in data.get("reserved_ports") or []}
    reserved |= {int(t["port"]) for t in specs.values() if t.get("port")}
    return {"source": str(path), "tools": specs, "reserved": reserved}


def _available(spec: dict) -> str:
    """"" when the command can run here, else why not."""
    oses = spec.get("os")
    if oses and sys.platform not in oses and not any(sys.platform.startswith(o) for o in oses):
        return "only on " + ", ".join(oses)
    if not shutil.which(spec.get("exe") or spec["name"]):
        return "not installed"
    return ""


def tools() -> dict:
    cfg = load_tools()
    return {"source": cfg["source"],
            "tools": [{"name": n, "label": t.get("label") or n, "port": t.get("port"),
                       "unavailable": _available(t)} for n, t in cfg["tools"].items()]}


# ── liveness ──────────────────────────────────────────────────────────────────

def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _image_name(pid: int) -> str | None:
    """Executable name of a live pid ("" if alive but unknown), None if not running."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
                return None
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return Path(buf.value).name.lower()
            return ""
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return ""
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        return cmd.lower()
    except OSError:
        return ""


def _ours(entry: dict) -> bool:
    """The pid is still the program vyrii started (a reused pid is someone else's)."""
    name = _image_name(int(entry.get("pid") or 0))
    if name is None:
        return False
    exe = Path(entry.get("exe") or entry["tool"]).stem.lower()
    return name == "" or exe in name or entry["tool"] in name or "python" in name


def _status(entry: dict) -> str:
    if not _ours(entry):
        return "exited"
    if not entry.get("port"):
        return "running"
    if port_busy(int(entry["port"])):
        return "running"
    return "starting" if time.time() - entry.get("started", 0) < STARTUP_GRACE else "no port"


def entries() -> list[dict]:
    out = []
    for e in _load():
        e = dict(e)
        e["status"] = _status(e)
        out.append(e)
    return out


# ── start / stop ──────────────────────────────────────────────────────────────

def suggest_port(start: int, taken: set[int], reserved: set[int] = frozenset()) -> int:
    """First free port after `start` that no listed tool takes by default."""
    end = min(start + PORT_TRIES, 65536)
    for port in range(start + 1, end):
        if port not in taken and port not in reserved and not port_busy(port):
            return port
    raise ProcessError(f"no free port in {start + 1}–{end - 1}")


def start(tool: str, folder: Path, rel: str, bind: str = "127.0.0.1",
          port: int | None = None) -> dict:
    cfg = load_tools()
    spec = cfg["tools"].get(tool)
    if not spec:
        raise ProcessError(f"unknown tool '{tool}' — {', '.join(cfg['tools'])} ({cfg['source']})")
    if not folder.is_dir():
        raise ProcessError(f"not a folder: {rel}")
    why = _available(spec)
    if why:
        raise ProcessError(f"{tool}: {why}")
    exe = shutil.which(spec.get("exe") or tool)

    items = _load()
    live = []
    for e in items:
        st = _status(e)
        if st != "exited":
            live.append(e)
            if e["tool"] == tool and Path(e["folder"]) == folder:
                return {**e, "status": st, "already": True}

    default_port = spec.get("port")
    taken = {int(e["port"]) for e in live if e.get("port")}
    reserved = cfg["reserved"]
    if not default_port:
        port = None
    elif port is None:
        if port_busy(default_port) or default_port in taken:
            holder = next((e for e in live if int(e["port"]) == default_port), None)
            return {"need_port": True, "tool": tool, "busy": default_port,
                    "holder": ({"tool": holder["tool"], "rel": holder.get("rel", "")}
                               if holder else None),
                    "suggest": suggest_port(default_port, taken, reserved)}
        port = default_port
    else:
        port = int(port)
        if not 1024 <= port <= 65535:
            raise ProcessError("port must be 1024–65535")
        if port_busy(port) or port in taken:
            raise ProcessError(f"port {port} is busy — try {suggest_port(port, taken, reserved)}")
    host = bind or "127.0.0.1"
    argv = [exe] + [str(a).format(port=port or "", host=host, root=str(folder))
                    for a in spec.get("args") or []]

    pid_id = uuid.uuid4().hex[:8]
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    log_path = PROC_DIR / f"{tool}-{pid_id}.log"
    log = open(log_path, "w", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    kw: dict = {"cwd": str(folder), "stdin": subprocess.DEVNULL, "stdout": log,
                "stderr": subprocess.STDOUT, "env": env, "close_fds": True}
    if sys.platform == "win32":
        # its own process group and no console: it outlives vyrii, Ctrl+C in vyrii's console spares it
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, **kw)
    finally:
        log.close()

    entry = {"id": pid_id, "tool": tool, "exe": Path(exe).name, "folder": str(folder), "rel": rel, "port": port,
             "host": host, "pid": proc.pid, "started": time.time(),
             "cmd": " ".join([tool] + argv[1:]), "log": str(log_path)}
    items.append(entry)
    _save(items)
    return {**entry, "status": "starting", "already": False}


def _find(items: list[dict], pid_id: str) -> dict:
    for e in items:
        if e.get("id") == pid_id:
            return e
    raise ProcessError(f"no process '{pid_id}'")


def stop(pid_id: str) -> dict:
    items = _load()
    e = _find(items, pid_id)
    if _ours(e):
        pid = int(e["pid"])
        if sys.platform == "win32":
            # /T: the console-script launcher runs python as a child — stop the whole tree
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=20)
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    return {**e, "status": _status(e)}


def remove(pid_id: str) -> None:
    items = _load()
    e = _find(items, pid_id)
    if _status(e) != "exited":
        raise ProcessError("still running — stop it first")
    _save([x for x in items if x.get("id") != pid_id])


def log_tail(pid_id: str, max_chars: int = 6000) -> str:
    e = _find(_load(), pid_id)
    try:
        text = Path(e["log"]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max_chars:]
