"""Settings → Port and restart (no command from the browser); Files never writes vyrii's own state."""
import io
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".vyrii").mkdir()
    from vyrii.flask_api import create_app
    return create_app().test_client(), tmp_path / ".vyrii"


@pytest.fixture
def launched(monkeypatch):
    """Catch the restart instead of running it (it would end the test process)."""
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(os, "_exit", lambda code: calls.append(("exit", code)))
    return calls


def _wait(calls, n=2):
    for _ in range(40):
        if len(calls) >= n:
            return
        time.sleep(0.1)


@pytest.mark.parametrize("name", ["config.json", "scheduler.json", "process_tools.json", "history.db"])
def test_upload_cannot_replace_vyrii_state(client, name):
    c, home = client
    (home / "config.json").write_text('{"auth_pass": "secret"}', encoding="utf-8")
    r = c.post("/vyrii/files/upload", data={"files": (io.BytesIO(b"{}"), name)},
               content_type="multipart/form-data").get_json()
    assert "belongs to vyrii" in json.dumps(r)
    assert json.loads((home / "config.json").read_text(encoding="utf-8"))["auth_pass"] == "secret"


def test_upload_into_a_folder_with_the_same_name_is_fine(client):
    c, home = client
    r = c.post("/vyrii/files/upload?dest=proj", data={"files": (io.BytesIO(b"{}"), "config.json")},
               content_type="multipart/form-data").get_json()
    assert r.get("ok") and (home / "proj" / "config.json").exists()


def test_delete_and_mkdir_refuse_state_and_root(client):
    c, home = client
    (home / "process").mkdir()
    assert "belongs to vyrii" in c.delete("/vyrii/files", json={"path": "process"}).get_json()["error"]
    assert "cannot be changed" in c.delete("/vyrii/files", json={"path": ""}).get_json()["error"]
    assert "belongs to vyrii" in c.post("/vyrii/files/mkdir", json={"path": "process/x"}).get_json()["error"]
    assert (home / "process").is_dir()


def test_port_setting_is_validated(client):
    c, home = client
    assert c.post("/vyrii/settings", json={"port": "80"}).status_code == 400
    assert c.post("/vyrii/settings", json={"port": "abc"}).status_code == 400
    assert c.post("/vyrii/settings", json={"port": "5001"}).get_json()["config"]["port"] == 5001


def test_restart_ignores_a_command_from_the_browser(client, launched, monkeypatch):
    c, _ = client
    monkeypatch.setattr(sys, "argv", ["vyrii", "--auth", "-p", "5003"])
    r = c.post("/vyrii/system/restart-cmd", json={"cmd": "calc.exe"}).get_json()
    _wait(launched)
    assert r["port"] is None
    assert launched[0] == ["vyrii", "--auth", "-p", "5003"]      # no saved port: as it was started


def test_restart_moves_to_the_saved_port(client, launched, monkeypatch):
    c, _ = client
    c.post("/vyrii/settings", json={"port": 5001})
    monkeypatch.setattr(sys, "argv", ["vyrii", "--auth", "-p", "5003", "--bind", "127.0.0.1"])
    r = c.post("/vyrii/system/restart", json={}).get_json()
    _wait(launched)
    assert r["port"] == 5001
    assert launched[0] == ["vyrii", "--auth", "--bind", "127.0.0.1"]


def test_main_port_order(tmp_path):
    from vyrii.__main__ import _saved_port
    assert _saved_port({"port": 5001}) == 5001
    assert _saved_port({"port": ""}) is None
    assert _saved_port({"port": "x"}) is None
    assert _saved_port({}) is None
