"""ctxtimer — empirically measure the maximum context size a model/host/timeout
combination can handle before failing.

Ported from 1bcoder's `/flow ctxtimer` (C:\\Project\\1bcoder\\_bcoder_data\\flows\\ctxtimer.py),
but rewritten around vyrii's own engine rather than routed through the
`ChatAdapter`/`flows/*.py` shim used for other ported flows, because ctxtimer
has two requirements those flows don't:

1. It needs a RELIABLE success/failure signal. 1bcoder's `chat._stream_chat()`
   swallows exceptions internally and returns "" / None as sentinels; vyrii's
   `engine.py` instead embeds a caught error as literal text
   ("**[Error: ...]**") inside the normal returned string. Neither convention
   is safe to string-sniff for a tool whose entire job is telling "the model
   answered" apart from "the request timed out" — so `test_context()` below
   calls `engine.stream_chat(..., raise_errors=True)` and iterates it
   directly, relying on a real exception rather than parsing text.
2. Its progress *is* the point (not an incidental print()), so it's driven by
   an explicit `progress_cb` callback (mirroring `vyrii/parallel.py`'s
   `run_parallel`), not stdout capture.
"""
from __future__ import annotations

import csv
import pathlib
from datetime import datetime

from .engine import stream_chat

_VYRII_HOME = pathlib.Path.home() / ".vyrii"
_DATA_DIR = pathlib.Path(__file__).parent / "ctxtimer_data"


# ── tokenization (1 token ≈ 4 chars, same estimate 1bcoder/vyrii use elsewhere) ──

def chars_to_tokens(num_chars: int) -> int:
    return num_chars // 4


def tokens_to_chars(num_tokens: int) -> int:
    return num_tokens * 4


def safe_num_ctx(max_tokens_to_test: int, floor: int = 4096) -> int:
    """Round up to a power of two >= max_tokens_to_test, at least `floor`.

    Unlike 1bcoder (where num_ctx is already configured elsewhere in the CLI),
    vyrii's ChatAdapter passes num_ctx explicitly per request — if it's
    smaller than the prompt being tested, Ollama truncates/mismeasures, which
    would silently invalidate the whole measurement.
    """
    n = max(int(max_tokens_to_test), floor)
    p = 1
    while p < n:
        p *= 2
    return p


# ── base prompt ──────────────────────────────────────────────────────────────

def load_base_prompt() -> str:
    """User override first (~/.vyrii/ctxtimer/base_prompt.txt), else bundled copy."""
    override = _VYRII_HOME / "ctxtimer" / "base_prompt.txt"
    if override.is_file():
        try:
            return override.read_text(encoding="utf-8")
        except OSError:
            pass
    bundled = _DATA_DIR / "base_prompt.txt"
    if bundled.is_file():
        try:
            return bundled.read_text(encoding="utf-8")
        except OSError:
            pass
    return ""


# ── report.csv ────────────────────────────────────────────────────────────────

def get_report_path() -> pathlib.Path:
    d = _VYRII_HOME / "ctxtimer"
    d.mkdir(parents=True, exist_ok=True)
    return d / "report.csv"


_REPORT_HEADER = ["timestamp", "model", "provider", "timeout_s", "max_context_tokens",
                  "search_mode", "start_tokens", "end_tokens"]


def save_result(model: str, provider: str, timeout: int, max_tokens: int, mode: str,
                 start: int, end: int | None) -> None:
    path = get_report_path()
    file_exists = path.is_file()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(_REPORT_HEADER)
        writer.writerow([datetime.now().isoformat(), model, provider, timeout,
                          max_tokens, mode.upper(), start, end if end is not None else "-"])


def list_report(model_filter: str | None = None) -> list[dict]:
    path = get_report_path()
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if model_filter:
        rows = [r for r in rows if model_filter.lower() in r.get("model", "").lower()]
    return rows


def clear_report() -> bool:
    path = get_report_path()
    if not path.is_file():
        return False
    path.unlink()
    return True


# ── single test ───────────────────────────────────────────────────────────────

def test_context(chat, prompt_text: str, context_tokens: int, full_mode: bool = False) -> tuple[bool, str]:
    """
    Test if the model can produce output at the given context size.

    probe mode (default): options={"num_predict": 1} — the model must stop
      after exactly one output token. Isolates time-to-first-token from
      decode-phase slowdown / long <think> preambles. If content streamed
      before a failure occurred anyway, that still counts as success (prefill
      succeeded — matches 1bcoder's leniency rule).
    full mode: no num_predict limit — success requires the ENTIRE response to
      complete with no error anywhere in the stream.

    Returns (success, error_kind) where error_kind is "" on success, else
    "timeout" or "error".
    """
    context = prompt_text[:tokens_to_chars(context_tokens)]
    if not context:
        return False, "empty context"

    messages = [{"role": "user", "content":
                 f"{context}\n\n---\n\nSummarize the above text in 1-2 sentences:"}]
    options = None if full_mode else {"num_predict": 1}

    got_any = False
    try:
        for chunk in stream_chat(
            messages, chat.model, chat._base_url, num_ctx=chat.num_ctx,
            backend=chat.provider, timeout=chat.timeout,
            options=options, raise_errors=True,
        ):
            got_any = True
            if not full_mode:
                break  # probe mode: one observed chunk is enough
        return True, ""
    except Exception as e:
        if not full_mode and got_any:
            # Prefill succeeded (we saw output) before whatever happened next.
            return True, ""
        msg = str(e).lower()
        if "timeout" in msg or "timed out" in msg:
            return False, "timeout"
        return False, "error"


# ── search loop ───────────────────────────────────────────────────────────────

def run_search(
    chat,
    *,
    mode: str = "seq",
    start: int = 1000,
    end: int | None = None,
    step: int = 1000,
    full_mode: bool = False,
    progress_cb=None,
    should_cancel=None,
) -> dict:
    """Port of 1bcoder's _run_impl search loop. print() -> progress_cb({...}).

    progress_cb, if given, is called after each individual test with:
      {"tokens": N, "status": "ok"|"fail", "error": ""|"timeout"|"error"}

    should_cancel, if given, is checked at the top of each loop iteration;
    when it returns True the search stops early (for a UI Cancel button).

    Returns:
      {"results": [{"tokens":N,"status":...,"error":...}, ...],
       "max_success_tokens": int|None,
       "base_prompt_tokens": int, "mode": mode, "full_mode": full_mode,
       "cancelled": bool}
    """
    def _emit(tokens, ok, err=""):
        item = {"tokens": tokens, "status": "ok" if ok else "fail", "error": err}
        results.append(item)
        if progress_cb:
            progress_cb(item)

    base_prompt = load_base_prompt()
    base_prompt_tokens = chars_to_tokens(len(base_prompt)) if base_prompt else 0

    if end is None and mode == "bin":
        end = min(base_prompt_tokens, start * 10) if base_prompt_tokens else start * 10
    if base_prompt_tokens and start > base_prompt_tokens:
        start = base_prompt_tokens
    if mode == "bin" and end and base_prompt_tokens and end > base_prompt_tokens:
        end = base_prompt_tokens

    results: list[dict] = []
    max_success_tokens = None
    cancelled = False

    if not base_prompt:
        return {"results": [], "max_success_tokens": None,
                "base_prompt_tokens": 0, "mode": mode, "full_mode": full_mode,
                "cancelled": False, "error": "base_prompt.txt not found"}

    if mode == "seq":
        current = start
        while current <= base_prompt_tokens:
            if should_cancel and should_cancel():
                cancelled = True
                break
            ok, err = test_context(chat, base_prompt, current, full_mode)
            if ok:
                max_success_tokens = current
                _emit(current, True)
            else:
                _emit(current, False, err)
                break  # sequential mode: stop at first failure
            current += step

    else:  # binary search
        low = start
        high = end or base_prompt_tokens
        tested: set[int] = set()

        while high - low > step:
            if should_cancel and should_cancel():
                cancelled = True
                break
            mid = (low + high) // 2
            mid = (mid // step) * step
            if mid <= low:
                mid = low + step
            if mid in tested or mid > base_prompt_tokens:
                break

            ok, err = test_context(chat, base_prompt, mid, full_mode)
            tested.add(mid)

            if ok:
                max_success_tokens = mid
                _emit(mid, True)
                low = mid
            else:
                _emit(mid, False, err)
                high = mid

        if not cancelled:
            for test_size in [low, low + step, high - step, high]:
                if should_cancel and should_cancel():
                    cancelled = True
                    break
                if test_size <= 0 or test_size > base_prompt_tokens or test_size in tested:
                    continue
                ok, err = test_context(chat, base_prompt, test_size, full_mode)
                tested.add(test_size)
                if ok:
                    max_success_tokens = max(max_success_tokens or 0, test_size)
                    _emit(test_size, True)
                else:
                    _emit(test_size, False, err)

    if max_success_tokens is not None:
        save_result(chat.model, chat.provider, chat.timeout, max_success_tokens,
                     mode, start, end)

    return {
        "results": results,
        "max_success_tokens": max_success_tokens,
        "base_prompt_tokens": base_prompt_tokens,
        "mode": mode,
        "full_mode": full_mode,
        "cancelled": cancelled,
    }
