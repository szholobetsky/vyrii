"""Start-up warning (no password + Wi-Fi), the Scheduler only with a password, Settings → Require password."""
import base64
import os
import pathlib
import subprocess
import sys
import time

import pytest

from vyrii import netinfo


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".vyrii").mkdir()

    def _make(auth=False, bind="0.0.0.0", rows=()):
        monkeypatch.setattr(netinfo, "addresses", lambda: list(rows))
        from vyrii.flask_api import create_app
        app = create_app(auth=auth)
        app.config["VYRII_BIND"] = bind
        return app.test_client()
    return _make


WIFI = {"iface": "Wi-Fi", "kind": "wifi", "ip": "192.168.0.132"}
CABLE = {"iface": "Ethernet", "kind": "ethernet", "ip": "192.168.137.50"}
ADMIN = {"Authorization": "Basic " + base64.b64encode(b"admin:admin").decode()}


def _exp(c):
    return c.get("/vyrii/system/exposure", base_url="http://x:5000").get_json()


def test_any_address_without_password_warns(make):
    r = _exp(make(rows=[CABLE, WIFI]))
    assert r["warn"] and r["port"] == "5000" and r["boot"]
    assert {a["kind"] for a in r["addresses"]} == {"ethernet", "wifi"}
    assert not any(a["trusted"] for a in r["addresses"])


def test_private_network_stops_the_warning_until_the_address_changes(make, monkeypatch):
    c = make(rows=[CABLE])
    assert _exp(c)["warn"]
    r = c.post("/vyrii/system/trust", json={"ips": ["192.168.137.50", "8.8.8.8"]}).get_json()
    assert r["trusted_ips"] == ["192.168.137.50"]          # only this machine's own addresses
    assert not _exp(c)["warn"]
    # plugged into a router: another address, a fresh start of vyrii
    c2 = make(rows=[{"iface": "Ethernet", "kind": "ethernet", "ip": "192.168.1.20"}])
    assert _exp(c2)["warn"]


def test_trusting_one_address_keeps_warning_for_another(make):
    c = make(rows=[CABLE, WIFI])
    c.post("/vyrii/system/trust", json={"ips": ["192.168.137.50"]})
    r = _exp(c)
    assert r["warn"] and [a["ip"] for a in r["addresses"] if not a["trusted"]] == ["192.168.0.132"]


def test_remove_one_trusted_address_keeps_the_others(make):
    c = make(rows=[CABLE, WIFI])
    c.post("/vyrii/system/trust", json={"ips": ["192.168.137.50", "192.168.0.132"]})
    r = c.post("/vyrii/system/trust", json={"remove": "192.168.0.132"}).get_json()
    assert r["trusted_ips"] == ["192.168.137.50"]
    assert [a["ip"] for a in _exp(c)["addresses"] if not a["trusted"]] == ["192.168.0.132"]


def test_forget_trusted(make):
    c = make(rows=[CABLE])
    c.post("/vyrii/system/trust", json={"ips": ["192.168.137.50"]})
    assert c.post("/vyrii/system/trust", json={"clear": True}).get_json()["trusted_ips"] == []
    assert _exp(c)["warn"]


def test_localhost_bind_does_not_warn(make):
    r = _exp(make(bind="127.0.0.1", rows=[WIFI]))
    assert not r["warn"] and r["addresses"] == []


def test_password_on_does_not_warn(make):
    r = make(auth=True, rows=[WIFI]).get("/vyrii/system/exposure", headers=ADMIN).get_json()
    assert r["auth"] and not r["warn"]


def test_scheduler_needs_a_password(make):
    r = make().get("/vyrii/scheduler/tasks")
    assert r.status_code == 403 and r.get_json()["need_auth"]
    assert make(auth=True).get("/vyrii/scheduler/tasks", headers=ADMIN).status_code == 200


def test_auth_setting_and_restart_drops_cli_auth(make, monkeypatch):
    c = make(auth=True)
    assert c.post("/vyrii/settings", json={"auth": 0}, headers=ADMIN).get_json()["config"]["auth"] is False
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(os, "_exit", lambda code: calls.append("exit"))
    monkeypatch.setattr(sys, "argv", ["vyrii", "--auth", "--bind", "0.0.0.0"])
    c.post("/vyrii/system/restart-cmd", json={}, headers=ADMIN)
    for _ in range(40):
        if calls:
            break
        time.sleep(0.1)
    assert calls[0] == ["vyrii", "--bind", "0.0.0.0"]


def test_kind_names():
    assert netinfo._kind("wlan0") == "wifi"
    assert netinfo._kind("Беспроводная сеть", "Native 802.11") == "wifi"
    assert netinfo._kind("eth0") == "ethernet" and netinfo._kind("enp3s0") == "ethernet"
    assert netinfo._kind("Ethernet 2", "802.3") == "ethernet"
    assert netinfo._kind("end0") == "ethernet" and netinfo._kind("enX0") == "ethernet"
    assert netinfo._kind("ppp0") == "other"
