"""ChatAdapter — bridges 1bcoder flow interface to vyrii engine.

Flows expect a `chat` object with:
  chat._stream_chat(messages)   → str
  chat._web_ddg_search(q, n)    → [(title, url, snippet), ...]  (the WebAsk engine)
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


def stream_flow_lines(run_fn):
    """Run run_fn() in a background thread, capture its stdout/stderr, and
    yield each printed line as it arrives (stripped, blank lines skipped).

    Shared by all three vyrii UIs for any flow that reports progress via
    plain print() (the 1bcoder-flow convention) instead of a progress_cb:
    Gradio's app.py::_stream_flow wraps this into its markdown-log-block
    accumulation; Flask/FastAPI routes wrap it into SSE `data: ...\\n\\n` frames.
    """
    import threading as _th
    import queue as _q
    import sys, io

    q = _q.Queue()

    class _W(io.TextIOBase):
        def write(self, s):
            if s:
                q.put(s)
            return len(s) if s else 0
        def flush(self):
            pass

    def _worker():
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = _W()
        sys.stderr = _W()
        try:
            run_fn()
        except Exception as e:
            import traceback as _tb
            q.put(f"\n[ERROR] {e}\n{_tb.format_exc()}\n")
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            q.put(None)

    _th.Thread(target=_worker, daemon=True).start()
    while True:
        item = q.get()
        if item is None:
            break
        text = item.rstrip('\n')
        if text.strip():
            yield text


def load_search_settings() -> dict:
    """The "search" block of ~/.vyrii/config.json: {"default": ..., "engines": {...}}."""
    import json, pathlib
    try:
        cfg = json.loads((pathlib.Path.home() / ".vyrii" / "config.json").read_text(encoding="utf-8"))
        s = cfg.get("search")
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


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
        self.llm_calls: list[tuple[list[dict], str]] = []   # (messages, reply) per call

    # ── LLM ──────────────────────────────────────────────────────────────────

    def _stream_chat(self, messages: list[dict], options: dict | None = None,
                      raise_errors: bool = False) -> str:
        reply = complete(messages, self._model, self._base_url,
                         num_ctx=self.num_ctx, backend=self._backend,
                         timeout=self._timeout, options=options, raise_errors=raise_errors)
        self.llm_calls.append((messages, reply or ""))
        return reply

    def full_context(self) -> str:
        """Everything the model read during the flow (user turns of every LLM
        call — fetched pages, chunks, ...) plus what it answered. This is what
        "Add full to chat" sends, so the main chat can ask about the raw
        material, not only about the summary."""
        parts = []
        for messages, reply in self.llm_calls:
            for m in messages:
                if m.get("role") == "user" and m.get("content"):
                    parts.append(m["content"])
            if reply:
                parts.append("Answer:\n" + reply)
        return "\n\n---\n\n".join(parts)

    # ── read-only aliases matching 1bcoder's chat object attribute names ─────
    # (chat.model / chat.provider / chat.timeout, no leading underscore) so
    # ported code (e.g. ctxtimer's report logging) can use the same names.

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return self._backend

    @property
    def timeout(self) -> int:
        return self._timeout

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
    def _web_search(term: str, n: int = 8, engine: str | None = None) -> list[tuple[str, str, str]]:
        """(title, url, snippet) from the engine chosen on the WebAsk page
        (~/.vyrii/config.json → "search"; DuckDuckGo unless changed).
        Raises websearch.SearchError — a blocked engine says so, not "no results"."""
        from . import websearch
        return websearch.search(term, n, load_search_settings(), engine=engine)

    # flows call this name (webask, webanalys, deepagent_md) — it follows the WebAsk choice too
    _web_ddg_search = _web_search
