"""Where vyrii can be reached from: the IPv4 addresses of the machine's network
interfaces, each marked ethernet / wifi / other.

Used by the start-up warning in the browser ("THIS COMPUTER IS VISIBLE ON THE
NETWORK at Ethernet a.a.a.a:5000, Wi-Fi b.b.b.b:5000"). vyrii does not guess
which network is safe: the person marks an address as a private network, and a
new address (the mini-PC plugged into a router) warns again. No extra
dependency: Windows asks PowerShell (Get-NetAdapter -Physical: no WSL/Hyper-V/VPN
adapters, PhysicalMediaType tells Wi-Fi apart even on a localized Windows);
Linux and Android read `ip -o -4 addr` and tell the kind from the interface
name; psutil is used when it is installed and the others give nothing.
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys

_SKIP = re.compile(r"^(lo|docker|br-|veth|virbr|vmnet|vboxnet|tun|tap|wg|zt|tailscale|dummy|rmnet|ccmni)", re.I)
_WIFI = re.compile(r"(wlan|wlp|wlo|wifi|wi-fi|wireless|802\.11|бездрот|беспровод)", re.I)
_ETH  = re.compile(r"^(eth|en|em\d|ethernet)|ethernet|802\.3", re.I)


def _kind(name: str, media: str = "") -> str:
    text = f"{name} {media}"
    if _WIFI.search(text):
        return "wifi"
    if _ETH.search(name) or _ETH.search(media):
        return "ethernet"
    return "other"


def _windows() -> list[dict]:
    ps = ("Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | ForEach-Object { "
          "$a = $_; Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 "
          "-ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{ "
          "n = $a.InterfaceAlias; m = [string]$a.PhysicalMediaType; ip = $_.IPAddress } } } "
          "| ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                         capture_output=True, text=True, timeout=15,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    if not out:
        return []
    rows = json.loads(out)
    rows = rows if isinstance(rows, list) else [rows]
    return [{"iface": r["n"], "kind": _kind(r["n"], r.get("m") or ""), "ip": r["ip"]} for r in rows]


def _ip_cmd() -> list[dict]:
    out = subprocess.run(["ip", "-o", "-4", "addr", "show", "up"],
                         capture_output=True, text=True, timeout=5).stdout
    res = []
    for line in out.splitlines():
        m = re.match(r"\d+:\s+(\S+)\s+inet\s+([\d.]+)/", line)
        if m:
            res.append({"iface": m.group(1), "kind": _kind(m.group(1)), "ip": m.group(2)})
    return res


def _psutil() -> list[dict]:
    import psutil
    stats = psutil.net_if_stats()
    res = []
    for name, addrs in psutil.net_if_addrs().items():
        if name in stats and not stats[name].isup:
            continue
        for a in addrs:
            if a.family == socket.AF_INET:
                res.append({"iface": name, "kind": _kind(name), "ip": a.address})
    return res


def _primary() -> list[dict]:
    """The address of the default route — no packet is sent by a UDP connect."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
    return [{"iface": "", "kind": "other", "ip": ip}]


def addresses() -> list[dict]:
    """[{"iface", "kind": ethernet|wifi|other, "ip"}] without loopback and virtual adapters."""
    finders = [_windows] if sys.platform == "win32" else []
    finders += [_ip_cmd, _psutil, _primary]
    for find in finders:
        try:
            rows = find()
        except Exception:
            continue
        rows = [r for r in rows if r["ip"] and not r["ip"].startswith("127.")
                and not _SKIP.match(r["iface"] or "")]
        if rows:
            return rows
    return []


def reachable(bind: str) -> list[dict]:
    """Addresses vyrii listens on for a --bind value; [] when only this machine can reach it."""
    if not bind or bind in ("127.0.0.1", "localhost", "::1"):
        return []
    rows = addresses()
    if bind in ("0.0.0.0", "::", ""):
        return rows
    return [r for r in rows if r["ip"] == bind] or [{"iface": "", "kind": "other", "ip": bind}]
