"""Minimal MCP client — stdio transport, JSON-RPC 2.0."""
from __future__ import annotations

import json
import subprocess
import threading
import time


class MCPClient:
    def __init__(self, name: str, command: str, cwd: str | None = None):
        self.name = name
        self._proc = subprocess.Popen(
            command, shell=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd or None, text=True, bufsize=1,
        )
        self._lock = threading.Lock()
        self._req_id = 0
        self._stderr_lines: list[str] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()

        time.sleep(0.5)
        if self._proc.poll() is not None:
            tail = "\n".join(self._stderr_lines[-5:])
            raise RuntimeError(f"MCP server '{name}' exited immediately.\n{tail}")

        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "vyrii", "version": "1.0"},
            "capabilities": {},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # ── internals ──────────────────────────────────────────────────────────────

    def _drain_stderr(self):
        for line in self._proc.stderr:
            self._stderr_lines.append(line.rstrip())
            if len(self._stderr_lines) > 20:
                self._stderr_lines.pop(0)

    def _send(self, obj: dict):
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            self._req_id += 1
            req: dict = {"jsonrpc": "2.0", "id": self._req_id, "method": method}
            if params is not None:
                req["params"] = params
            self._send(req)
            for _ in range(300):  # 30 s timeout
                line = self._proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                    if msg.get("id") == self._req_id:
                        if "error" in msg:
                            raise RuntimeError(msg["error"].get("message", "RPC error"))
                        return msg.get("result", {})
                except json.JSONDecodeError:
                    pass
                time.sleep(0.1)
        raise TimeoutError(f"No response from MCP server '{self.name}'")

    # ── public API ─────────────────────────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        return self._rpc("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return "\n".join(
            c.get("text", "")
            for c in result.get("content", [])
            if c.get("type") == "text"
        )

    def close(self):
        try:
            self._proc.terminate()
        except Exception:
            pass
