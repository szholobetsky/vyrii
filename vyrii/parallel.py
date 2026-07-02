"""Parallel LLM execution and profile management for the Team tab."""
from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from pathlib import Path

from .engine import BACKEND_OLLAMA, BACKEND_OPENAI, complete, parse_model_spec

# resolved at runtime via init()
_PROFILES_FILE: Path | None = None


def init(vyrii_home: Path) -> None:
    global _PROFILES_FILE
    _PROFILES_FILE = vyrii_home / "parallel_profiles.json"
    (vyrii_home / "exports").mkdir(parents=True, exist_ok=True)


def _profiles_file() -> Path:
    if _PROFILES_FILE is None:
        raise RuntimeError("parallel.init() not called")
    return _PROFILES_FILE


# ── Profile CRUD ──────────────────────────────────────────────────────────────

def load_profiles() -> list[dict]:
    f = _profiles_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_profiles(profiles: list[dict]) -> None:
    _profiles_file().write_text(json.dumps(profiles, indent=2, ensure_ascii=False),
                                encoding="utf-8")


def profile_names() -> list[str]:
    return [p["name"] for p in load_profiles()]


def get_profile(name: str) -> dict | None:
    for p in load_profiles():
        if p["name"] == name:
            return p
    return None


def upsert_profile(profile: dict) -> None:
    profiles = load_profiles()
    for i, p in enumerate(profiles):
        if p["name"] == profile["name"]:
            profiles[i] = profile
            save_profiles(profiles)
            return
    profiles.append(profile)
    save_profiles(profiles)


def delete_profile(name: str) -> None:
    profiles = [p for p in load_profiles() if p["name"] != name]
    save_profiles(profiles)


def export_1bcoder(profiles: list[dict]) -> str:
    """Render 1bcoder-compatible profiles.txt.
    Format: name: host|model|ctx host|model|ctx  # comment
    """
    lines = []
    for p in profiles:
        workers_str = " ".join(
            f"{w['host']}|{w['model']}|ctx" for w in p.get("workers", [])
        )
        comment = f"  # {p['comment']}" if p.get("comment") else ""
        lines.append(f"{p['name']}: {workers_str}{comment}")
    return "\n".join(lines) + "\n"


# ── Parallel execution ────────────────────────────────────────────────────────

def run_parallel(
    workers: list[dict],
    aspects: list[str],
    main_prompt: str,
    base_messages: list[dict],
    num_ctx: int,
    timeout: int,
    progress_cb: callable,
) -> list[dict]:
    """Run all workers concurrently. Returns list of result dicts."""

    def call_one(idx: int, w: dict) -> dict:
        from . import stats as _stats
        aspect = (aspects[idx] if idx < len(aspects) else "").strip()
        prompt = f"{main_prompt}\n\nAspect: {aspect}" if aspect else main_prompt
        msgs = list(base_messages) + [{"role": "user", "content": prompt}]
        backend = BACKEND_OPENAI if w.get("provider") == "openai" else BACKEND_OLLAMA
        host = w["host"]
        url = f"http://{host}" if not host.startswith("http") else host
        host_label = url.replace("http://", "").replace("https://", "")
        for pos in _stats.wait_for_host(host_label):
            progress_cb(f"[queue] **{w['model']}** @ {host} — waiting (position {pos})")
        try:
            reply = complete(msgs, w["model"], url, num_ctx, backend, timeout=timeout)
            progress_cb(f"[done] **{w['model']}** @ {host} — {len(reply)} chars")
            return {**w, "aspect": aspect, "reply": reply, "error": None}
        except Exception as e:
            progress_cb(f"[error] **{w['model']}** @ {host} — {e}")
            return {**w, "aspect": aspect, "reply": None, "error": str(e)}
        finally:
            _stats.release_host_sem(host_label)

    progress_cb(f"Starting **{len(workers)}** worker(s)…")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as pool:
        futures = {pool.submit(call_one, i, w): i for i, w in enumerate(workers)}
        return [f.result() for f in concurrent.futures.as_completed(futures)]


def join_results(main_prompt: str, results: list[dict]) -> str:
    """Flat join with a header per worker."""
    parts = []
    # sort by original worker order if possible
    for r in results:
        label = f"`{r['model']}` @ `{r['host']}`"
        if r.get("aspect"):
            label += f" — *{r['aspect']}*"
        if r.get("error"):
            body = f"*Error: {r['error']}*"
        else:
            body = r.get("reply") or ""
        parts.append(f"### {label}\n\n{body}")

    header = f"## {main_prompt}\n" if main_prompt else ""
    return header + "\n\n---\n\n".join(parts)


def compact_results(
    main_prompt: str,
    results: list[dict],
    model: str,
    url: str,
    backend: str,
    num_ctx: int,
    timeout: int,
) -> str:
    """Ask the current vyrii model to synthesise all worker replies."""
    joined = join_results(main_prompt, results)
    sys_msg = (
        "You are a synthesis assistant. "
        "Combine the following parallel LLM responses into one coherent, "
        "non-redundant answer. Preserve unique insights from each perspective."
    )
    m_name, m_url, m_bk = parse_model_spec(model)
    msgs = [{"role": "user", "content": f"{sys_msg}\n\n{joined}"}]
    return complete(msgs, m_name, m_url or url, num_ctx, m_bk or backend, timeout=timeout)
