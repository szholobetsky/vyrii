"""Chat engine — Ollama and OpenAI-compatible backends (llama.cpp, llamafile, LM Studio…)."""
from __future__ import annotations

import json
import requests

BACKEND_OLLAMA = "ollama"
BACKEND_OPENAI = "openai"

DEFAULT_OLLAMA = "http://localhost:11434"
DEFAULT_OPENAI = "http://localhost:8080"


import re as _re

def parse_model_spec(spec: str) -> tuple[str, str | None, str | None]:
    """Parse 'model@backend://host:port' → (model, base_url, backend_type).
    If no '@', returns (spec, None, None) — caller should use defaults."""
    if not spec or "@" not in spec:
        return spec or "", None, None
    model, rest = spec.split("@", 1)
    m = _re.match(r"(ollama|openai)://(.+)", rest)
    if not m:
        return spec, None, None
    backend_type = m.group(1)
    host = m.group(2)
    base_url = f"http://{host}" if not host.startswith("http") else host
    return model, base_url, backend_type

CTX_START = 2048
CTX_STEP  = 2048
_CTX_FILL  = 0.70

# Ollama families that are embedding-only (no chat capability)
_EMBED_FAMILIES = {"bert", "nomic-bert"}

# Fallback name-based filter for OpenAI-compatible endpoints (no type metadata)
_EMBED_NAME_FRAGMENTS = ("embed", "minilm", "e5-small", "e5-base", "e5-large")
_EMBED_NAME_PREFIXES  = ("bge-",)


def _is_embedding_name(name: str) -> bool:
    low = name.lower().split(":")[0]
    return (
        any(p in low for p in _EMBED_NAME_FRAGMENTS)
        or any(low.startswith(p) for p in _EMBED_NAME_PREFIXES)
    )


def list_models(base_url: str = DEFAULT_OLLAMA, backend: str = BACKEND_OLLAMA) -> list[str]:
    try:
        if backend == BACKEND_OPENAI:
            r = requests.get(f"{base_url}/v1/models", timeout=5)
            r.raise_for_status()
            data = r.json()
            if "data" in data:
                names = [m["id"] for m in data["data"] if "id" in m]
            elif "models" in data:
                names = [m.get("id") or m.get("name", "") for m in data["models"]]
            else:
                names = []
            return [n for n in names if n and not _is_embedding_name(n)]
        else:
            r = requests.get(f"{base_url}/api/tags", timeout=5)
            r.raise_for_status()
            result = []
            for m in r.json().get("models", []):
                family = m.get("details", {}).get("family", "")
                if family not in _EMBED_FAMILIES:
                    result.append(m["name"])
            return result
    except Exception:
        return []


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)


def smart_ctx(messages: list[dict], current: int = CTX_START) -> int:
    total = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
    while total >= int(current * _CTX_FILL):
        current += CTX_STEP
    return current


def _clean_messages(messages: list[dict]) -> list[dict]:
    result = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        # Gradio 6 may return content as a list of blocks: [{"type":"text","text":"..."}]
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(item.get("text") or item.get("content") or "")
                else:
                    parts.append(str(item))
            content = "\n".join(p for p in parts if p)
        elif not isinstance(content, str):
            content = str(content)
        if role in ("user", "assistant", "system") and content:
            result.append({"role": role, "content": content})
    return result


def stream_chat(
    messages: list[dict],
    model: str,
    base_url: str = DEFAULT_OLLAMA,
    num_ctx: int = CTX_START,
    backend: str = BACKEND_OLLAMA,
    thinking: bool = False,
    timeout: int = 180,
    options: dict | None = None,
    raise_errors: bool = False,
):
    """Yield text chunks from streaming chat API.

    options: extra backend-specific generation options merged on top of the
      defaults (e.g. {"num_predict": 1} for Ollama). Ignored keys for the
      OpenAI backend are best-effort translated (see _stream_openai).
    raise_errors: if True, a request failure (timeout, connection error, ...)
      is re-raised instead of being swallowed and embedded as
      "**[Error: ...]**" text in the yielded output. Callers that need a
      reliable success/failure signal (rather than human-facing chat text)
      should set this to True.
    """
    clean = _clean_messages(messages)
    if not clean or not model:
        yield "**[Error: no messages or model not set]**"
        return

    if backend == BACKEND_OPENAI:
        yield from _stream_openai(clean, model, base_url, timeout, options, raise_errors)
    else:
        yield from _stream_ollama(clean, model, base_url, num_ctx, thinking, timeout, options, raise_errors)


def _stream_ollama(clean, model, base_url, num_ctx, thinking=False, timeout=180,
                    options=None, raise_errors=False):
    url = f"{base_url}/api/chat"
    payload = {
        "model": model,
        "messages": clean,
        "stream": True,
        "options": {"num_ctx": num_ctx, **(options or {})},
    }
    if thinking:
        payload["think"] = True
    try:
        with requests.post(url, json=payload, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            in_think = False
            for raw in resp.iter_lines():
                if not raw:
                    continue
                data = json.loads(raw)
                if data.get("done"):
                    break
                msg = data.get("message", {})
                think_chunk = msg.get("thinking", "")
                if think_chunk:
                    if not in_think:
                        yield "<think>"
                        in_think = True
                    yield think_chunk
                content = msg.get("content", "")
                if content:
                    if in_think:
                        yield "</think>"
                        in_think = False
                    yield content
            if in_think:
                yield "</think>"
    except Exception as e:
        if raise_errors:
            raise
        yield f"\n\n**[Error: {e}]**"


def _stream_openai(clean, model, base_url, timeout=180, options=None, raise_errors=False):
    url = f"{base_url}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": clean,
        "stream": True,
    }
    if options:
        o = dict(options)
        if "num_predict" in o:
            payload["max_tokens"] = o.pop("num_predict")
        payload.update(o)
    try:
        with requests.post(url, json=payload, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if line.startswith("data: "):
                    line = line[6:]
                if line.strip() == "[DONE]":
                    break
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                delta = data.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content
    except Exception as e:
        if raise_errors:
            raise
        yield f"\n\n**[Error: {e}]**"


def complete(
    messages: list[dict],
    model: str,
    base_url: str = DEFAULT_OLLAMA,
    num_ctx: int = CTX_START,
    backend: str = BACKEND_OLLAMA,
    timeout: int = 180,
    options: dict | None = None,
    raise_errors: bool = False,
) -> str:
    """Non-streaming single response."""
    return "".join(stream_chat(messages, model, base_url, num_ctx, backend, timeout=timeout,
                                options=options, raise_errors=raise_errors))
