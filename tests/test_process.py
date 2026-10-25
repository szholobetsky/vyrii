"""Process tab: commands from process_tools.json, busy ports, registry. No tool is started."""
import json
import socket
import sys

import pytest

from vyrii import process as P


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "HOME", tmp_path)
    monkeypatch.setattr(P, "PROC_DIR", tmp_path / "process")
    monkeypatch.setattr(P, "REGISTRY", tmp_path / "process" / "processes.json")
    monkeypatch.setattr(P, "USER_TOOLS", tmp_path / "process_tools.json")
    (tmp_path / "proj").mkdir()
    return tmp_path


def _listen():
    """A socket held on a port with free ports above it (an OS-chosen one may sit at 65535)."""
    for port in range(23000, 23500):
        srv = socket.socket()
        try:
            srv.bind(("127.0.0.1", port))
        except OSError:
            srv.close()
            continue
        srv.listen()
        return srv
    pytest.skip("no free port in 23000-23500")


def _write_tools(home, tools, reserved=()):
    (home / "process_tools.json").write_text(
        json.dumps({"reserved_ports": list(reserved), "tools": tools}), encoding="utf-8")


def test_packaged_default_lists_the_pantheon(home):
    cfg = P.load_tools()
    assert cfg["source"].endswith("process_tools.json") and str(home) not in cfg["source"]
    assert {"alkonost", "simargl", "gamayun", "svitovyd", "mokosh", "syryn"} <= set(cfg["tools"])
    assert {8090, 8093, 8095, 8097, 8099, 5000} <= cfg["reserved"]


def test_user_file_replaces_the_default(home):
    _write_tools(home, [{"name": "crm", "exe": "crm", "args": ["serve"], "port": 9100}])
    cfg = P.load_tools()
    assert list(cfg["tools"]) == ["crm"] and cfg["source"] == str(home / "process_tools.json")
    assert P.tools()["tools"][0]["unavailable"] == "not installed"


def test_unknown_tool_is_refused(home):
    with pytest.raises(P.ProcessError, match="unknown tool"):
        P.start("cmd", home / "proj", "proj")


def test_os_limited_tool(home):
    other = "linux" if sys.platform == "win32" else "win32"
    _write_tools(home, [{"name": "python", "exe": "python", "args": [], "port": None, "os": [other]}])
    assert P.tools()["tools"][0]["unavailable"] == f"only on {other}"


def test_default_port_held_elsewhere_asks_for_a_port(home):
    with _listen() as srv:
        busy = srv.getsockname()[1]
        _write_tools(home, [{"name": "python", "exe": "python", "args": ["-c", "pass"], "port": busy}],
                     reserved=[busy + 1])
        r = P.start("python", home / "proj", "proj")
    assert r["need_port"] and r["busy"] == busy and r["holder"] is None
    assert r["suggest"] not in (busy, busy + 1)       # reserved ports are never offered
    assert not (home / "process" / "processes.json").exists()   # nothing started


def test_chosen_port_that_is_busy_is_refused(home):
    with _listen() as srv:
        busy = srv.getsockname()[1]
        _write_tools(home, [{"name": "python", "exe": "python", "args": [], "port": 1}])
        with pytest.raises(P.ProcessError, match="busy"):
            P.start("python", home / "proj", "proj", port=busy)


def test_exited_entry_status_and_remove(home):
    P._save([{"id": "x1", "tool": "gamayun", "exe": "gamayun.exe", "folder": str(home / "proj"),
              "rel": "proj", "port": 1, "host": "127.0.0.1", "pid": 2 ** 22 + 7, "started": 0,
              "log": str(home / "none.log")}])
    [e] = P.entries()
    assert e["status"] == "exited"
    P.remove("x1")
    assert P.entries() == []
