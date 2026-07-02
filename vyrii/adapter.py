"""ChatAdapter — bridges 1bcoder flow interface to vyrii engine.

Flows expect a `chat` object with:
  chat._stream_chat(messages)   → str
  chat._web_ddg_search(q, n)    → [(title, url, snippet), ...]
  chat._web_strip_html(bytes)   → str
  chat._sep(label)              → None  (display separator, no-op here)
  chat._role                    → str   (system prompt)
  chat.num_ctx                  → int
  chat.params                   → dict  (misc params, e.g. host/model for parallel)
  chat.last_reply               → str   (written by flow after completion)
  chat._last_output             → str   (same)
  chat.messages                 → list  (flow may append to it)

Usage:
  from vyrii.adapter import ChatAdapter
  from vyrii.flows import webask

  adapter = ChatAdapter(model="qwen3:1.7b", base_url="http://localhost:11434",
                        backend="ollama", num_ctx=4096)
  webask.run(adapter, "what is quantum entanglement")
  result = adapter.last_reply
"""
from __future__ import annotations

import re
import requests as _requests

from .engine import complete, BACKEND_OLLAMA, DEFAULT_OLLAMA, parse_model_spec


class ChatAdapter:
    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_OLLAMA,
        backend: str = BACKEND_OLLAMA,
        num_ctx: int = 4096,
        role: str = "You are a helpful assistant.",
        timeout: int = 180,
    ):
        m_name, m_url, m_bk = parse_model_spec(model)
        self._model    = m_name
        self._base_url = m_url or base_url
        self._backend  = m_bk or backend
        self.num_ctx   = num_ctx
        self._role     = role
        self._timeout  = timeout
        self.params: dict = {}        # used by deepagent_md parallel workers
        self.messages: list[dict] = []
        self.last_reply   = ""
        self._last_output = ""

    # ── LLM ──────────────────────────────────────────────────────────────────

    def _stream_chat(self, messages: list[dict]) -> str:
        return complete(messages, self._model, self._base_url,
                        num_ctx=self.num_ctx, backend=self._backend,
                        timeout=self._timeout)

    def _sep(self, label: str = "") -> None:
        pass  # no-op: vyrii renders output differently

    # ── Web utilities ─────────────────────────────────────────────────────────

    @staticmethod
    def _web_strip_html(html_bytes: bytes) -> str:
        from html.parser import HTMLParser

        class _S(HTMLParser):
            SKIP = {"script", "style", "nav", "header", "footer", "aside", "noscript",
                    "form", "select", "option", "iframe"}
            def __init__(self):
                super().__init__(); self._d = 0; self.out = []
            def handle_starttag(self, t, a):
                if t in self.SKIP: self._d += 1
            def handle_endtag(self, t):
                if t in self.SKIP and self._d: self._d -= 1
            def handle_data(self, d):
                if not self._d:
                    s = d.strip()
                    if s: self.out.append(s)

        p = _S()
        p.feed(html_bytes.decode("utf-8", "replace"))
        return "\n".join(p.out)

    @staticmethod
    def _web_ddg_search(term: str, n: int = 8) -> list[tuple[str, str, str]]:
        from html.parser import HTMLParser
        from urllib.parse import parse_qs, urlparse, unquote

        class _DDG(HTMLParser):
            def __init__(self):
                super().__init__(); self.results = []; self._cur = {}; self._in = None
            def handle_starttag(self, tag, attrs):
                d = dict(attrs); cls = d.get("class", "")
                if tag == "a" and "result__a" in cls:
                    href = d.get("href", "")
                    try:
                        params = parse_qs(urlparse(href).query)
                        url = params.get("uddg", [""])[0] or unquote(href)
                    except Exception:
                        url = href
                    self._cur["url"] = url; self._in = "title"
                elif tag == "a" and "result__snippet" in cls:
                    self._in = "snippet"
            def handle_endtag(self, tag):
                if self._in and tag == "a":
                    if "url" in self._cur and "title" in self._cur:
                        self.results.append((
                            self._cur.get("title", ""),
                            self._cur.get("url", ""),
                            self._cur.get("snippet", ""),
                        ))
                        self._cur = {}
                    self._in = None
            def handle_data(self, data):
                if self._in == "title":
                    self._cur["title"] = self._cur.get("title", "") + data
                elif self._in == "snippet":
                    self._cur["snippet"] = self._cur.get("snippet", "") + data

        resp = _requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": term},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        p = _DDG(); p.feed(resp.text)
        return p.results[:n]
