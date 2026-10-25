"""Flask backend — same endpoints as api.py, no Rust deps.

OpenAI-compatible:
  GET  /v1/models
  POST /v1/chat/completions   (streaming SSE + non-streaming)

vyrii extensions:
  POST /vyrii/translate
  POST /vyrii/webask
  ... (full parity with api.py)

Run standalone:
  python -m vyrii.flask_api [port]
"""
from __future__ import annotations

import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from flask import (
    Flask, Response, copy_current_request_context, jsonify, redirect,
    request, send_from_directory, stream_with_context,
)
from flask_cors import CORS

from .engine import (
    BACKEND_OLLAMA, BACKEND_OPENAI, DEFAULT_OLLAMA,
    complete, list_models, model_capabilities, parse_model_spec, smart_ctx, stream_chat,
)
from . import attachments as _att
from . import stats as _stats


def _parse_interview_questions(text: str):
    import re, json
    text = text.strip()
    text = re.sub(r'^```[a-z]*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    text = text.strip()
    text = re.sub(r'(?<!:)//[^\n]*', '', text)
    text = re.sub(r'(?<!")#[^\n"]*', '', text)
    text = re.sub(r',(\s*[}\]])', r'\1', text)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [{"q": str(i.get("q", "")).strip(),
                     "options": [str(o).strip() for o in i.get("options", [])]}
                    for i in data if isinstance(i, dict) and i.get("q")]
    except Exception:
        pass
    m = re.search(r'\[.*\]', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return [{"q": str(i.get("q", "")).strip(),
                         "options": [str(o).strip() for o in i.get("options", [])]}
                        for i in data if isinstance(i, dict) and i.get("q")]
        except Exception:
            pass
    # regex fallback
    results = []
    for qm in re.finditer(r'"q"\s*:\s*"([^"]+)"', text):
        q = qm.group(1)
        block = text[qm.end():]
        opts = [o for o in re.findall(r'"([^"]{5,})"', block[:300])
                if o not in ('options', 'q')]
        results.append({"q": q, "options": opts[:4]})
        if not block.strip().startswith(',') and results:
            break
    return results or None


def create_app(base_url: str = DEFAULT_OLLAMA, backend: str = BACKEND_OLLAMA,
               auth: bool = False) -> Flask:
    try:
        _cfg_boot = json.loads((pathlib.Path.home() / ".vyrii" / "config.json").read_text(encoding="utf-8"))
    except Exception:
        _cfg_boot = {}
    base_url = _cfg_boot.get("saved_url") or base_url
    timeout  = int(_cfg_boot.get("timeout", 180))
    _sb = (_cfg_boot.get("saved_backend") or "").lower()
    if _sb in ("openai", "openai-compatible"):
        backend = BACKEND_OPENAI
    elif _sb in ("ollama",):
        backend = BACKEND_OLLAMA

    app = Flask(__name__, static_folder=None)
    CORS(app)

    # ── network exposure: the start-up warning in the browser ─────────────────
    # interfaces are looked up once per start, on the first call (PowerShell takes
    # seconds; VYRII_BIND is set only after create_app returns)

    _boot_id = f"{int(time.time())}"
    _exposure: dict = {}
    _exposure_lock = threading.Lock()

    def _exposure_rows() -> list[dict]:
        with _exposure_lock:
            if "rows" not in _exposure:
                from . import netinfo
                try:
                    _exposure["rows"] = netinfo.reachable(app.config.get("VYRII_BIND", "127.0.0.1"))
                except Exception:
                    _exposure["rows"] = []
        return _exposure["rows"]

    @app.route("/vyrii/system/exposure", methods=["GET"])
    def system_exposure():
        trusted = set(_read_cfg().get("trusted_ips") or [])
        rows = [{**r, "trusted": r["ip"] in trusted} for r in _exposure_rows()]
        port = request.host.rsplit(":", 1)[1] if ":" in request.host else ""
        return jsonify({"auth": auth, "bind": app.config.get("VYRII_BIND", ""), "port": port,
                        "boot": _boot_id, "addresses": rows, "trusted_ips": sorted(trusted),
                        # vyrii does not guess: an address the person has not marked as a
                        # private network warns; a new one (plugged into a router) warns again
                        "warn": not auth and any(not r["trusted"] for r in rows)})

    @app.route("/vyrii/system/trust", methods=["POST"])
    def system_trust():
        """{"ips": [...]} marks this machine's current addresses as a private network;
        {"remove": ip} forgets one; {"clear": true} forgets them all."""
        body = request.get_json(silent=True) or {}
        if body.get("clear"):
            _write_cfg({"trusted_ips": []})
            return jsonify({"ok": True, "trusted_ips": []})
        if body.get("remove"):
            trusted = [ip for ip in _read_cfg().get("trusted_ips") or [] if ip != body["remove"]]
            _write_cfg({"trusted_ips": trusted})
            return jsonify({"ok": True, "trusted_ips": trusted})
        current = {r["ip"] for r in _exposure_rows()}
        ips = [ip for ip in body.get("ips") or [] if ip in current]
        trusted = sorted(set(_read_cfg().get("trusted_ips") or []) | set(ips))
        _write_cfg({"trusted_ips": trusted})
        return jsonify({"ok": True, "trusted_ips": trusted})

    # the Scheduler runs any shell command: only when vyrii asks for a password
    @app.before_request
    def _scheduler_needs_auth():
        if not auth and request.path.startswith("/vyrii/scheduler"):
            return jsonify({"error": "Scheduler needs a password: Settings → Authentication → "
                                     "Require password, then restart vyrii.",
                            "need_auth": True}), 403
        return None

    # ── basic auth middleware ─────────────────────────────────────────────────

    if auth:
        import base64 as _b64
        import json as _json_auth

        def _read_auth_cfg() -> tuple[str, str]:
            try:
                p = pathlib.Path.home() / ".vyrii" / "config.json"
                cfg = _json_auth.loads(p.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
            return cfg.get("auth_user", "admin"), cfg.get("auth_pass", "admin")

        @app.before_request
        def _basic_auth():
            # Logout endpoint: always 401 + WWW-Authenticate so browser clears its credential cache
            if request.path == "/vyrii/auth/logout":
                return Response("", 401, {"WWW-Authenticate": 'Basic realm="vyrii"'})
            if request.method == "OPTIONS" or request.path.startswith("/ui") or request.path == "/":
                return None
            exp_user, exp_pass = _read_auth_cfg()
            hdr = request.headers.get("Authorization", "")
            if hdr.startswith("Basic "):
                try:
                    user, _, pwd = _b64.b64decode(hdr[6:]).decode("utf-8", errors="replace").partition(":")
                    if user == exp_user and pwd == exp_pass:
                        return None
                except Exception:
                    pass
            return Response("Unauthorized", 401)

    def _default_model() -> str:
        models = list_models(base_url, backend)
        return models[0] if models else ""

    # ── /v1/models ────────────────────────────────────────────────────────────

    def _active_profile_workers() -> list[dict]:
        cfg = _read_cfg()
        name = cfg.get("active_profile", "")
        if not name:
            return []
        from . import parallel as _par_models
        profile = _par_models.get_profile(name)
        return profile.get("workers", []) if profile else []

    @app.route("/v1/models", methods=["GET"])
    def get_models():
        local = list_models(base_url, backend)
        host_label = base_url.replace("http://", "").replace("https://", "")
        data = [
            {"id": m, "object": "model", "created": 0,
             "owned_by": "local", "group": f"{host_label} ({backend})"}
            for m in local
        ]
        for w in _active_profile_workers():
            h = w.get("host", "")
            url = f"http://{h}" if not h.startswith("http") else h
            h_label = h.replace("http://", "").replace("https://", "")
            bk = "openai" if w.get("provider") == "openai" else "ollama"
            mid = f"{w['model']}@{bk}://{h_label}"
            data.append({"id": mid, "object": "model", "created": 0,
                         "owned_by": "remote", "group": f"{h_label} ({bk})"})
        return jsonify({"object": "list", "data": data})

    # ── start scheduler ────────────────────────────────────────────────────────
    from . import scheduler as _sch_startup
    _sch_startup.start_scheduler()

    # ── /v1/chat/completions ──────────────────────────────────────────────────

    @app.route("/v1/chat/completions", methods=["POST"])
    def chat_completions():
        body = request.get_json(silent=True) or {}
        raw_model = body.get("model") or _default_model()
        messages = body.get("messages", [])
        # files attached in the chat (paths under ~/.vyrii, uploaded to input/):
        # documents become text in the message, images go to "images"
        messages = _att.expand_messages(messages, pathlib.Path.home() / ".vyrii")
        do_stream = body.get("stream", False)
        cid = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created = int(time.time())

        cfg = _read_cfg()
        if cfg.get("autocut_enabled"):
            from . import ctxwindow
            messages = ctxwindow.apply_autocut(
                messages, enabled=True,
                first=int(cfg.get("autocut_first") or 0),
                last=int(cfg.get("autocut_last") or 2000),
                algo=cfg.get("autocut_algo") or "bm25",
                limit=int(cfg.get("autocut_limit") or 500),
            )

        m_name, m_url, m_backend = parse_model_spec(raw_model)
        use_url = m_url or base_url
        use_backend = m_backend or backend

        num_ctx = body.get("num_ctx") or smart_ctx(messages)

        host_label = use_url.replace("http://", "").replace("https://", "")

        if do_stream:
            def _gen():
                for pos in _stats.wait_for_host(host_label):
                    yield f'data: {json.dumps({"waiting": True, "position": pos})}\n\n'
                rid = _stats.record_start(host_label, m_name)
                try:
                    for chunk in stream_chat(messages, m_name, use_url,
                                             num_ctx=num_ctx, backend=use_backend, timeout=timeout):
                        data = {
                            "id": cid, "object": "chat.completion.chunk",
                            "created": created, "model": raw_model,
                            "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                        }
                        yield f"data: {json.dumps(data)}\n\n"
                    final = {
                        "id": cid, "object": "chat.completion.chunk",
                        "created": created, "model": raw_model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
                    yield f"data: {json.dumps(final)}\n\n"
                    yield "data: [DONE]\n\n"
                finally:
                    _stats.record_end(rid)
                    _stats.auto_release_host(host_label)
                    _stats.release_host_sem(host_label)

            return Response(stream_with_context(_gen()), mimetype="text/event-stream")

        for _ in _stats.wait_for_host(host_label):
            pass
        rid = _stats.record_start(host_label, m_name)
        try:
            full = complete(messages, m_name, use_url, num_ctx=num_ctx, backend=use_backend, timeout=timeout)
        finally:
            _stats.record_end(rid)
            _stats.auto_release_host(host_label)
            _stats.release_host_sem(host_label)
        return jsonify({
            "id": cid, "object": "chat.completion",
            "created": created, "model": raw_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": full}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    # ── /vyrii/translate ──────────────────────────────────────────────────────

    _LANG_CODES = {
        "English": "en", "Ukrainian": "uk", "German": "de", "French": "fr",
        "Spanish": "es", "Polish": "pl", "Italian": "it", "Portuguese": "pt",
        "Chinese": "zh", "Japanese": "ja", "Arabic": "ar", "Auto": "auto",
    }

    # ── live output for tool tabs ─────────────────────────────────────────────
    # A route marked @_streamable answers as before; with {"stream": true} in the
    # body it runs in a worker thread and sends SSE: {"type":"start"} when a model
    # call begins, {"type":"token","text"} as the model writes, {"type":"log","text"}
    # for the flow's progress lines, and {"type":"done","data": <the usual JSON>}.

    import functools as _functools
    import queue as _queue

    def _streamable(view):
        @_functools.wraps(view)
        def wrapper(*args, **kwargs):
            body = request.get_json(silent=True) or {}
            if not body.get("stream"):
                return view(*args, **kwargs)
            from . import live
            q: _queue.Queue = _queue.Queue()

            @copy_current_request_context
            def work():
                data: dict
                with live.capture(on_token=lambda t: q.put({"type": "token", "text": t}),
                                  on_start=lambda: q.put({"type": "start"}),
                                  on_log=lambda t: q.put({"type": "log", "text": t})):
                    try:
                        resp = app.make_response(view(*args, **kwargs))
                        data = resp.get_json(silent=True)
                        if data is None:
                            data = {"result": resp.get_data(as_text=True)}
                    except Exception as e:
                        data = {"error": str(e)}
                q.put({"type": "done", "data": data})

            threading.Thread(target=work, daemon=True).start()

            def gen():
                while True:
                    ev = q.get()
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    if ev["type"] == "done":
                        break
            return Response(stream_with_context(gen()), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        return wrapper

    @app.route("/vyrii/translate", methods=["POST"])
    @_streamable
    def translate():
        body = request.get_json(silent=True) or {}
        text      = body.get("text", "").strip()
        from_lang = body.get("from_lang", "Auto")
        to_lang   = body.get("to_lang", "Ukrainian")
        mode      = body.get("mode", "llm").lower()
        model     = body.get("model", "") or _default_model()
        from_code = _LANG_CODES.get(from_lang, from_lang.lower()[:2])
        to_code   = _LANG_CODES.get(to_lang,   to_lang.lower()[:2])

        m_name, m_url, m_backend = parse_model_spec(model)
        model   = m_name
        use_url = m_url or base_url
        use_bk  = m_backend or backend

        if mode == "argos":
            try:
                import re as _re
                import argostranslate.translate as _at
                _blocks: list = []
                def _stash(m):
                    _blocks.append(m.group(0)); return f"[CODEBLK_{len(_blocks)-1}]"
                text = _re.sub(r"```[\s\S]*?```", _stash, text)
                def _restore(s):
                    for i, b in enumerate(_blocks): s = s.replace(f"[CODEBLK_{i}]", b)
                    return s
                _ph = _re.compile(r"(\[CODEBLK_\d+\])")
                parts = _ph.split(text)
                out = []
                for p in parts:
                    if _ph.fullmatch(p): out.append(p)
                    elif p.strip():      out.append(_at.translate(p, from_code, to_code))
                    else:                out.append(p)
                return jsonify({"result": _restore("".join(out))})
            except Exception as e:
                return jsonify({"error": str(e)})

        if mode == "nllb":
            try:
                import re as _re
                import ctranslate2, sentencepiece as _spm
                _NLLB_DIR = os.path.join(os.path.expanduser("~"), ".1bcoder", "nllb-200")
                _SP_PATH  = os.path.join(_NLLB_DIR, "sentencepiece.bpe.model")
                if not os.path.isdir(_NLLB_DIR):
                    return jsonify({"error": f"NLLB model not found at {_NLLB_DIR}"})
                _FLORES = {
                    "en": "eng_Latn", "uk": "ukr_Cyrl", "de": "deu_Latn",
                    "fr": "fra_Latn", "es": "spa_Latn", "pl": "pol_Latn",
                    "it": "ita_Latn", "pt": "por_Latn", "ru": "rus_Cyrl",
                    "zh": "zho_Hans", "ja": "jpn_Jpan", "ko": "kor_Hang",
                    "ar": "arb_Arab",
                }
                src_f = _FLORES.get(from_code, f"{from_code}_Latn")
                tgt_f = _FLORES.get(to_code,   f"{to_code}_Latn")
                sp = _spm.SentencePieceProcessor(); sp.Load(_SP_PATH)
                tr = ctranslate2.Translator(_NLLB_DIR, device="cpu")
                def _chunk(line):
                    toks = sp.encode(line, out_type=str)
                    res  = tr.translate_batch(
                        [[src_f] + toks + ["</s>"]], target_prefix=[[tgt_f]],
                        max_decoding_length=512, repetition_penalty=1.3, beam_size=4,
                    )
                    return sp.decode(res[0].hypotheses[0][1:])
                _blocks2: list = []
                def _stash2(m):
                    _blocks2.append(m.group(0)); return f"[CODEBLK_{len(_blocks2)-1}]"
                text = _re.sub(r"```[\s\S]*?```", _stash2, text)
                def _restore2(s):
                    for i, b in enumerate(_blocks2): s = s.replace(f"[CODEBLK_{i}]", b)
                    return s
                _ph2 = _re.compile(r"^\[CODEBLK_\d+\]$")
                out_lines = []
                for line in text.split("\n"):
                    s = line.strip()
                    out_lines.append(line if not s or _ph2.match(s) else _chunk(s))
                return jsonify({"result": _restore2("\n".join(out_lines))})
            except Exception as e:
                return jsonify({"error": str(e)})

        # llm mode
        if "translategemma" in model.lower() and use_bk == BACKEND_OPENAI:
            # LM Studio-specific structured content format
            import requests as _req
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "source_lang_code": from_code,
                     "target_lang_code": to_code, "text": text}
                ]}],
                "stream": False, "temperature": 0.1,
            }
            try:
                r = _req.post(f"{use_url}/v1/chat/completions", json=payload,
                              headers={"Authorization": "Bearer lm-studio"}, timeout=timeout)
                r.raise_for_status()
                return jsonify({"result": r.json()["choices"][0]["message"]["content"].strip()})
            except Exception as e:
                return jsonify({"error": str(e)})

        if "translategemma" in model.lower():
            # Ollama: system+user prompt format per translategemma model card
            src_label = from_lang if from_lang != "Auto" else "auto-detected"
            sys_msg = (
                f"You are a professional {src_label} ({from_code}) to {to_lang} ({to_code}) translator. "
                f"Your goal is to accurately convey the meaning and nuances of the original text "
                f"while adhering to {to_lang} grammar, vocabulary, and cultural sensitivities. "
                f"Produce only the {to_lang} translation, without any additional explanations or commentary."
            )
            msgs = [{"role": "system", "content": sys_msg}, {"role": "user", "content": text}]
            return jsonify({"result": complete(msgs, model, use_url, backend=use_bk, timeout=timeout)})

        from_clause = f" from {from_lang}" if from_lang != "Auto" else ""
        prompt = (
            f"Translate the following text{from_clause} to {to_lang}. "
            f"Output ONLY the translation — no introduction, no explanation.\n\n{text}"
        )
        return jsonify({"result": complete([{"role": "user", "content": prompt}],
                                           model, use_url, backend=use_bk, timeout=timeout)})

    def _run_flow_log(run, adapter, args) -> str:
        """Run a flow and keep what it printed: when it ends without a reply
        (search blocked, no pages fetched) the page shows why, not "No results"."""
        from . import live
        buf: list[str] = []
        with live.capture(on_log=buf.append):
            run(adapter, args)
        return "".join(buf).strip()

    # ── /vyrii/search — the web search engine, chosen on the WebAsk page ─────
    # stored in config.json "search"; every page and flow with a web search uses it

    @app.route("/vyrii/search/engines", methods=["GET"])
    def search_engines():
        from . import websearch as _ws
        from .adapter import load_search_settings
        st = load_search_settings()
        engines = []
        for name, (label, need) in _ws.ENGINES.items():
            opts = _ws.engine_options(st, name)
            engines.append({"name": name, "label": label, "needs": need,
                            "missing": _ws.missing(st, name),
                            "url": opts.get("url", "") if need == "url" else "",
                            "has_key": bool(str(opts.get("key") or "").strip())})
        return jsonify({"default": _ws.default_engine(st), "engines": engines})

    @app.route("/vyrii/search/engine", methods=["POST"])
    def search_engine_save():
        from . import websearch as _ws
        from .adapter import load_search_settings
        body = request.get_json(silent=True) or {}
        name = _ws.resolve_engine(str(body.get("engine", "")))
        if not name:
            return jsonify({"error": f"unknown engine — known: {', '.join(_ws.ENGINES)}"}), 400
        st = load_search_settings()
        for k in ("key", "url"):
            v = str(body.get(k) or "").strip()
            if v:
                st.setdefault("engines", {}).setdefault(name, {})[k] = v
        gap = _ws.missing(st, name)
        if not gap:
            st["default"] = name
        _write_cfg({"search": st})
        return jsonify({"ok": not gap, "default": _ws.default_engine(st), "missing": gap})

    # ── /vyrii/webask ─────────────────────────────────────────────────────────

    @app.route("/vyrii/webask", methods=["POST"])
    @_streamable
    def webask():
        from .adapter import ChatAdapter
        from .flows import webask as _wf
        body     = request.get_json(silent=True) or {}
        model    = body.get("model", "") or _default_model()
        question = body.get("question", "").strip()
        url      = body.get("url", "").strip()
        top_n    = body.get("top_n", 3)
        if not question:
            return jsonify({"error": "question is required"})
        if url:
            from .tools import fetch_text
            try:
                page = fetch_text(url)
            except Exception as e:
                return jsonify({"error": str(e)})
            prompt = (
                f"Source: {url}\n\n{page}\n\n---\n\n"
                f"Question: {question}\n\nAnswer based ONLY on the content above."
            )
            answer = complete([{"role": "user", "content": prompt}],
                              model, base_url, backend=backend, timeout=timeout)
            return jsonify({"result": answer, "context": f"{prompt}\n\n---\n\nAnswer:\n{answer}"})
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        log = _run_flow_log(_wf.run, adapter, f"{question} -d {top_n}")
        return jsonify({"result": adapter.last_reply or log or "No results.", "context": adapter.full_context()})

    # ── /vyrii/webcrawl ───────────────────────────────────────────────────────

    @app.route("/vyrii/webcrawl", methods=["POST"])
    @_streamable
    def webcrawl():
        import tempfile as _tmp
        from .adapter import ChatAdapter
        from .flows import webcrawl as _wcf
        body      = request.get_json(silent=True) or {}
        model     = body.get("model", "") or _default_model()
        url       = body.get("url", "")
        mode      = body.get("mode", "combine")
        filt       = body.get("filter", "none")
        url_prefix = body.get("url_prefix", "").strip()
        out_path_arg = body.get("path", "").strip()
        depth      = body.get("depth", 2)
        max_pages  = body.get("max_pages", 20)
        task       = body.get("task", "").strip()
        format_out = body.get("format_out", "log")
        ask        = body.get("ask", False)
        columns    = body.get("columns", "").strip()
        adapter    = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)

        import time as _wct, pathlib as _wcp
        _vyrii_home = _wcp.Path.home() / ".vyrii"
        if out_path_arg:
            _out_dir = _wcp.Path(out_path_arg)
        else:
            _out_dir = _vyrii_home / "crawl"
        _out_dir.mkdir(parents=True, exist_ok=True)
        _ts = _wct.strftime("%Y%m%d_%H%M%S")
        if mode in ("pages", "mirror"):
            out_path = str(_out_dir) if out_path_arg else str(_out_dir / f"crawl_{_ts}")
        elif mode == "extract" or (mode == "llm" and format_out == "structured"):
            out_path = str(_out_dir / f"crawl_{_ts}.csv")
        else:
            out_path = str(_out_dir / f"crawl_{_ts}.txt")

        args = f'{url} --mode {mode} --depth {depth} -N {max_pages} --out {out_path}'
        if filt != "none":
            if filt == "url-prefix":
                actual_prefix = (url_prefix or url).rstrip("/")
                args += f' --filter {actual_prefix}'
            else:
                args += f' --filter {filt}'
        if task:
            args += f' --task "{task}"'
        if mode == "llm":
            args += f' --format {format_out}'
        if ask:
            args += ' --ask'

        _cols_tmp = None
        if columns and mode in ("extract", "llm"):
            _cols_tmp = _tmp.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, encoding="utf-8"
            )
            _cols_tmp.write(columns)
            _cols_tmp.close()
            args += f' --columns {_cols_tmp.name}'

        _wcf.run(adapter, args)

        if _cols_tmp:
            try:
                os.unlink(_cols_tmp.name)
            except Exception:
                pass

        return jsonify({"result": adapter.last_reply or "No results.", "context": adapter.full_context()})

    # ── /vyrii/deepagent ──────────────────────────────────────────────────────

    @app.route("/vyrii/deepagent", methods=["POST"])
    @_streamable
    def deepagent():
        from .adapter import ChatAdapter
        from .flows import deepagent_md as _df
        body      = request.get_json(silent=True) or {}
        model     = body.get("model", "") or _default_model()
        task      = body.get("task", "").strip()
        ref_url   = body.get("ref_url", "").strip()
        sections  = body.get("sections", 3)
        use_web   = body.get("use_web", False)
        web_n     = body.get("web_n", 3)
        rag_proj  = body.get("rag_project", "").strip()
        if not task:
            return jsonify({"error": "task is required"})
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        args = f'"{task}" --maxdepth {sections}'
        if ref_url:
            args += f' --ref {ref_url}'
        if use_web:
            args += f' --web {web_n}'
        if rag_proj:
            _vyrii_home = pathlib.Path.home() / ".vyrii"
            args += f' --rag {rag_proj} --rag-store "{_vyrii_home}"'
        _df.run(adapter, args)
        return jsonify({"result": adapter.last_reply or "No output.", "context": adapter.full_context()})

    # ── /vyrii/webanalys ──────────────────────────────────────────────────────

    @app.route("/vyrii/webanalys", methods=["POST"])
    @_streamable
    def webanalys():
        from .adapter import ChatAdapter
        from .flows import webanalys as _waf
        body  = request.get_json(silent=True) or {}
        model = body.get("model", "") or _default_model()
        query = body.get("query", "").strip()
        n     = body.get("n", 5)
        if not query:
            return jsonify({"error": "query is required"})
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        log = _run_flow_log(_waf.run, adapter, f'"{query}" -n {n}')
        return jsonify({"result": adapter.last_reply or log or "No results.", "context": adapter.full_context()})

    # ── /vyrii/scan ───────────────────────────────────────────────────────────

    @app.route("/vyrii/scan", methods=["POST"])
    @_streamable
    def scan_compact():
        from .adapter import ChatAdapter
        from .flows import scan as _sc
        body  = request.get_json(silent=True) or {}
        model = body.get("model", "") or _default_model()
        path  = body.get("path", "").strip()
        query = body.get("query", "").strip()
        chunk   = body.get("chunk", 4000)
        summary = body.get("summary", 400)
        target  = body.get("target", 8000)
        rounds  = body.get("rounds", 1)
        ext     = body.get("ext", "").strip()
        mode    = body.get("mode", "").strip()
        if not path:
            return jsonify({"error": "path is required"})
        if mode not in ("", "summary", "focus", "filter"):
            return jsonify({"error": f"unknown mode: {mode}"})
        if mode in ("focus", "filter") and not query:
            return jsonify({"error": f"mode {mode} needs a query"})
        # a relative path is one from Files (relative to ~/.vyrii), not to the folder vyrii started in
        if not os.path.isabs(path):
            try:
                if _fsafe(path).exists():
                    path = str(_fsafe(path))
            except ValueError:
                pass
        if not os.path.exists(path):
            return jsonify({"error": f"Not found: {path}"})
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        args = f'"{path}"'
        if query:
            args += f' --query "{query}"'
        if mode:
            args += f' --mode {mode}'
        args += f' --chunk {chunk} --summary {summary} --target {target} --rounds {rounds}'
        if ext:
            args += f' --ext {ext}'
        _sc.run(adapter, args)
        if not adapter.last_reply:
            # nothing summarised — say why, not "Done."
            if query and mode in ("", "filter"):
                return jsonify({"error": f"Nothing in {path} is relevant to the query: {query}"})
            return jsonify({"error": f"Nothing scanned in {path} — no files of the listed extensions, "
                                     f"or they are empty"})
        return jsonify({"result": adapter.last_reply, "context": adapter.full_context()})

    # ── /vyrii/webindex ───────────────────────────────────────────────────────

    @app.route("/vyrii/webindex", methods=["POST"])
    @_streamable
    def webindex():
        from .adapter import ChatAdapter
        from .flows import webindex as _wi
        body    = request.get_json(silent=True) or {}
        model   = body.get("model", "") or _default_model()
        url     = body.get("url", "").strip()
        project = body.get("project", "").strip()
        path    = body.get("path", "").strip()
        depth   = body.get("depth", 2)
        pages   = body.get("pages", 20)
        if not url or not url.startswith("http"):
            return jsonify({"error": "valid url is required"})
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        args = f'"{url}"'
        if project:
            args += f' --project {project}'
        if path:
            args += f' --path "{path}"'
        args += f' --depth {depth} --pages {pages}'
        _wi.run(adapter, args)
        return jsonify({"result": adapter.last_reply or "Done."})

    # ── /vyrii/obfuscate / deobfuscate ────────────────────────────────────────

    def _dict_tmp(content: str) -> str:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        return tmp.name

    @app.route("/vyrii/obfuscate", methods=["POST"])
    def obfuscate():
        from .adapter import ChatAdapter
        from .flows import obfuscate as _of
        body       = request.get_json(silent=True) or {}
        text       = body.get("text", "")
        dictionary = body.get("dictionary", "").strip()
        force      = body.get("force", False)
        model      = body.get("model", "") or _default_model()
        if not dictionary:
            return jsonify({"error": "dictionary is required"})
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        adapter._last_output = text
        dpath = _dict_tmp(dictionary)
        args  = f'--dict {dpath}'
        if force:
            args += ' --force'
        try:
            _of.run(adapter, args)
        finally:
            try: os.unlink(dpath)
            except Exception: pass
        return jsonify({"result": adapter.last_reply or ""})

    @app.route("/vyrii/deobfuscate", methods=["POST"])
    def deobfuscate():
        from .adapter import ChatAdapter
        from .flows import deobfuscate as _dof
        body       = request.get_json(silent=True) or {}
        text       = body.get("text", "")
        dictionary = body.get("dictionary", "").strip()
        force      = body.get("force", False)
        model      = body.get("model", "") or _default_model()
        if not dictionary:
            return jsonify({"error": "dictionary is required"})
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        adapter._last_output = text
        dpath = _dict_tmp(dictionary)
        args  = f'--dict {dpath}'
        if force:
            args += ' --force'
        try:
            _dof.run(adapter, args)
        finally:
            try: os.unlink(dpath)
            except Exception: pass
        return jsonify({"result": adapter.last_reply or ""})

    @app.route("/vyrii/interview", methods=["POST"])
    def interview():
        from .adapter import ChatAdapter
        body         = request.get_json(silent=True) or {}
        task         = body.get("task", "").strip()
        n            = max(1, min(20, int(body.get("n", 5))))
        file_content = body.get("file_content", "").strip()
        model        = body.get("model", "") or _default_model()
        if not task:
            return jsonify({"error": "task is required"}), 400
        ctx    = f"\n\nAdditional context:\n{file_content[:3000]}" if file_content else ""
        system = (
            f"You are a requirements analyst. Generate exactly {n} clarifying questions "
            "that must be answered before implementation begins. For each question provide "
            "2-3 concrete answer options. Output ONLY a valid JSON array — no markdown, "
            "no explanation. Format: "
            '[{"q": "Question?", "options": ["Option A", "Option B"]}, ...]'
        )
        user    = f"Task: {task}{ctx}"
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        raw     = adapter._stream_chat([{"role": "system", "content": system},
                                        {"role": "user",   "content": user}]) or ""
        questions = _parse_interview_questions(raw)
        if not questions:
            return jsonify({"error": "Failed to parse questions", "raw": raw[:500]}), 500
        return jsonify({"questions": questions})

    # ── /vyrii/themes ─────────────────────────────────────────────────────────

    @app.route("/vyrii/themes", methods=["GET"])
    def list_themes():
        themes_dir = pathlib.Path(__file__).parent / "ui" / "themes"
        if not themes_dir.exists():
            return jsonify({"themes": ["ocean"]})
        themes = sorted(f.stem for f in themes_dir.glob("*.css") if f.is_file())
        return jsonify({"themes": themes or ["ocean"]})

    # ── /vyrii/settings ───────────────────────────────────────────────────────

    _CFG = pathlib.Path.home() / ".vyrii" / "config.json"

    def _read_cfg() -> dict:
        try:
            return json.loads(_CFG.read_text(encoding="utf-8")) if _CFG.exists() else {}
        except Exception:
            return {}

    def _write_cfg(updates: dict):
        cfg = _read_cfg()
        cfg.update({k: v for k, v in updates.items() if v is not None})
        _CFG.parent.mkdir(parents=True, exist_ok=True)
        _CFG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    @app.route("/vyrii/settings", methods=["GET"])
    def settings_get():
        cfg = _read_cfg()
        if "restart_cmd" not in cfg:
            cfg["restart_cmd"] = " ".join(sys.argv)
        return jsonify(cfg)

    @app.route("/vyrii/settings", methods=["POST"])
    def settings_save():
        body = request.get_json(silent=True) or {}
        allowed = {"saved_url", "saved_model", "saved_backend", "lang",
                   "theme", "timeout", "worker_timeout", "active_profile",
                   "reserve_mode", "reserve_timeout", "restart_cmd", "port", "auth",
                   "ollama_kv_cache", "ollama_flash_attention",
                   "ollama_keep_alive", "ollama_max_loaded_models", "ollama_host",
                   "ollama_vulkan", "ollama_igpu_enable",
                   "autocut_enabled", "autocut_first", "autocut_last",
                   "autocut_limit", "autocut_algo"}
        updates = {k: v for k, v in body.items() if k in allowed and v is not None}
        if "auth" in updates:
            updates["auth"] = bool(updates["auth"])
        if "port" in updates:
            try:
                port = int(updates["port"] or 0)
            except (TypeError, ValueError):
                return jsonify({"error": "port must be a number"}), 400
            if port and not 1024 <= port <= 65535:
                return jsonify({"error": "port must be 1024–65535 (empty = 5000)"}), 400
            updates["port"] = port or ""
        try:
            _write_cfg(updates)
            return jsonify({"ok": True, "config": _read_cfg()})
        except Exception as e:
            return jsonify({"error": str(e)})

    # ── /vyrii/tools — external services bar, shared config.json with Gradio ──
    # ("external_tools" key — the same one app.py's _load_tools/_save_tools
    # read/write, so a tool added here shows up in the Gradio UI too and
    # vice versa, no separate storage).

    _DEFAULT_TOOLS = [
        {"name": "SIMARGL", "port": 8099},
        {"name": "SVITOVYD", "port": 8097},
        {"name": "GAMAYUN", "port": 8095},
        {"name": "MOKOSH", "port": 8093},
        {"name": "ALKONOST", "port": 8090},
    ]

    def _valid_tool(name, port):
        try:
            port = int(port)
        except (TypeError, ValueError):
            return None
        name = str(name or "").strip().upper()
        if not name or not (1 <= port <= 65535):
            return None
        return {"name": name, "port": port}

    @app.route("/vyrii/tools", methods=["GET"])
    def tools_get():
        cfg = _read_cfg()
        return jsonify({"tools": cfg.get("external_tools", list(_DEFAULT_TOOLS))})

    @app.route("/vyrii/tools", methods=["POST"])
    def tools_save():
        body = request.get_json(silent=True) or {}
        tools, seen_ports = [], set()
        for t in body.get("tools", []):
            v = _valid_tool(t.get("name"), t.get("port"))
            if v and v["port"] not in seen_ports:
                tools.append(v)
                seen_ports.add(v["port"])
        if not tools:
            tools = list(_DEFAULT_TOOLS)
        _write_cfg({"external_tools": tools})
        return jsonify({"ok": True, "tools": tools})

    @app.route("/vyrii/tools/add", methods=["POST"])
    def tools_add():
        body = request.get_json(silent=True) or {}
        v = _valid_tool(body.get("name"), body.get("port"))
        if not v:
            return jsonify({"error": "name and a valid port (1-65535) are required"}), 400
        cfg = _read_cfg()
        tools = cfg.get("external_tools", list(_DEFAULT_TOOLS))
        if not any(t["port"] == v["port"] for t in tools):
            tools.append(v)
            _write_cfg({"external_tools": tools})
        return jsonify({"ok": True, "tools": tools})

    @app.route("/vyrii/tools/reset", methods=["POST"])
    def tools_reset():
        _write_cfg({"external_tools": list(_DEFAULT_TOOLS)})
        return jsonify({"ok": True, "tools": list(_DEFAULT_TOOLS)})

    @app.route("/vyrii/stats", methods=["GET"])
    def vyrii_stats():
        return jsonify({"stats": _stats.get_stats(), "locks": _stats.get_all_locks()})

    @app.route("/vyrii/lock", methods=["GET"])
    def vyrii_lock_info():
        return jsonify({"locks": _stats.get_all_locks()})

    @app.route("/vyrii/lock", methods=["POST"])
    def vyrii_lock():
        body = request.get_json(silent=True) or {}
        host = body.get("host", "")
        action = body.get("action", "lock")
        ip = request.remote_addr or "127.0.0.1"
        if not host:
            return jsonify({"error": "host required"}), 400
        if action == "release":
            _stats.release_host(host, ip)
            return jsonify({"ok": True})
        cfg = _read_cfg()
        mode = cfg.get("reserve_mode", "response")
        timeout = int(cfg.get("reserve_timeout", 600))
        return jsonify(_stats.lock_host(host, ip, mode, timeout))

    @app.route("/vyrii/auth/password", methods=["POST"])
    def auth_change_password():
        body = request.get_json(silent=True) or {}
        username = body.get("username", "").strip()
        password = body.get("password", "")
        if not username or not password:
            return jsonify({"error": "username and password are required"})
        _write_cfg({"auth_user": username, "auth_pass": password})
        return jsonify({"ok": True})

    # ── /vyrii/history/* ──────────────────────────────────────────────────────

    @app.route("/vyrii/history/chats", methods=["GET"])
    def hist_list_chats():
        from . import history as _h
        return jsonify([{"id": id_, "title": title, "created_at": ts}
                        for id_, title, ts in _h.list_chats()])

    @app.route("/vyrii/history/chats", methods=["POST"])
    def hist_create_chat():
        from . import history as _h
        body = request.get_json(silent=True) or {}
        cid = _h.create_chat(body.get("title", "").strip() or "New chat")
        return jsonify({"id": cid})

    @app.route("/vyrii/history/chats/<int:chat_id>/messages", methods=["POST"])
    def hist_add_message(chat_id):
        from . import history as _h
        body = request.get_json(silent=True) or {}
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else None
        msg_id = _h.add_message(chat_id, body.get("role", ""), body.get("content", ""), meta)
        return jsonify({"ok": True, "id": msg_id})

    @app.route("/vyrii/history/chats/<int:chat_id>/messages/<int:msg_id>", methods=["DELETE"])
    def hist_delete_message(chat_id, msg_id):
        from . import history as _h
        if not _h.delete_message(chat_id, msg_id):
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})

    @app.route("/vyrii/history/chats/<int:chat_id>", methods=["GET"])
    def hist_get_chat(chat_id):
        from . import history as _h
        import sqlite3 as _sq
        with _h._conn() as c:
            row = c.execute(
                "SELECT title, created_at FROM chats WHERE id=?", (chat_id,)
            ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify({"id": chat_id, "title": row[0], "created_at": row[1],
                        "messages": _h.get_messages_full(chat_id)})

    @app.route("/vyrii/compact", methods=["POST"])
    def compact_chat():
        body = request.get_json(silent=True) or {}
        messages = body.get("messages", [])
        model = body.get("model") or _default_model()
        if not messages:
            return jsonify({"summary": "", "error": "no messages"})
        m_name, m_url, m_bk = parse_model_spec(model)
        history_text = "\n\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m.get('content', '')}"
            for m in messages if m.get("content")
        )
        summary = complete(
            [{"role": "user", "content":
              "Summarize this conversation concisely, preserving all key "
              "information, decisions, and context:\n\n" + history_text}],
            m_name, m_url or base_url, backend=m_bk or backend, timeout=timeout,
        )
        return jsonify({"summary": summary})

    @app.route("/vyrii/history/search", methods=["GET"])
    def hist_search():
        from . import history as _h
        q = request.args.get("q", "")
        return jsonify([{"id": i, "title": t, "created_at": ts}
                        for i, t, ts in _h.search_chats(q)])

    @app.route("/vyrii/history/chats/<int:chat_id>", methods=["DELETE"])
    def hist_delete_chat(chat_id):
        from . import history as _h
        _h.delete_chat(chat_id)
        return jsonify({"ok": True})

    # ── /vyrii/rag/* ──────────────────────────────────────────────────────────

    _VYRII_HOME = pathlib.Path.home() / ".vyrii"

    @app.route("/vyrii/rag/projects", methods=["GET"])
    def rag_projects():
        projects: set[str] = set()
        for store in [_VYRII_HOME / ".simargl", _VYRII_HOME / ".simargl_web"]:
            if store.exists():
                for d in store.iterdir():
                    if d.is_dir():
                        projects.add(d.name)
        return jsonify({"projects": sorted(projects)})

    @app.route("/vyrii/rag/search", methods=["POST"])
    def rag_search():
        body    = request.get_json(silent=True) or {}
        query   = body.get("query", "").strip()
        project = body.get("project", "").strip()
        top_k   = body.get("top_k", 3)
        mode    = body.get("mode", "file")
        sort    = body.get("sort", "rank")
        proj_path = body.get("project_path", "").strip()
        if not query or not project:
            return jsonify({"error": "project and query are required"})
        mode = mode if mode in ("file", "task", "aggr", "refine") else "file"
        sort = sort if sort in ("rank", "freq") else "rank"
        store_dir = (str(pathlib.Path(proj_path) / ".simargl") if proj_path
                     else str(_VYRII_HOME / ".simargl"))
        try:
            from simargl.searcher import search as _sim_search
            result = _sim_search(
                query, mode=mode, sort=sort, top_n=top_k,
                project_id=project, store_dir=store_dir,
            )
        except ImportError:
            return jsonify({"error": "simargl not installed — run: pip install simargl"})
        except Exception as e:
            return jsonify({"error": str(e)})

        if mode == "task":
            units = result.get("units", [])[:top_k]
            return jsonify({
                "mode": "task",
                "results": [
                    {"unit_id": u.get("unit_id", ""), "text_preview": u.get("text_preview", ""),
                     "score": u.get("similarity", 0), "files": u.get("files", [])}
                    for u in units
                ],
                "context": "", "sources": [],
            })

        if mode == "aggr":
            modules = result.get("modules", [])[:top_k]
            return jsonify({
                "mode": "aggr",
                "results": [{"path": m.get("module", ""), "score": m.get("score", 0)} for m in modules],
                "context": "", "sources": [],
            })

        files = result.get("files", [])[:top_k]
        if not files:
            return jsonify({"results": [], "context": "", "sources": [], "mode": mode})

        from simargl.searcher import get_chunk_text as _sim_get_chunk

        results, sources, ctx_parts = [], [], []
        for i, f in enumerate(files, 1):
            fpath    = pathlib.Path(f.get("path", ""))
            fname    = fpath.name
            score    = f.get("score", 0)
            chunk_n  = f.get("chunk_n", 0)
            text     = ""
            for cand in [
                _VYRII_HOME / ".simargl_web" / project / fname,
                _VYRII_HOME / project / fpath,   # /vyrii/files/index convention: ~/.vyrii/<project>/<relpath>
                _VYRII_HOME / fpath,
                pathlib.Path(fpath),
                _VYRII_HOME / fname,
            ]:
                try:
                    if cand.is_file():
                        # actual matched chunk, not the file's first N characters
                        text = _sim_get_chunk(str(cand), chunk_n, project_id=project,
                                              store_dir=store_dir)
                        break
                except Exception:
                    continue
            snippet = text[:300].replace("\n", " ")
            results.append({"rank": i, "file": fname, "score": round(score, 3),
                            "chunk": chunk_n, "snippet": snippet, "text": text[:1500]})
            sources.append(fname)
            ctx_parts.append(f"[Source {i}: {fname}  score:{score:.2f}  chunk:{chunk_n}]\n{text}")

        return jsonify({"results": results, "context": "\n\n".join(ctx_parts), "sources": sources})

    @app.route("/vyrii/rag/ask", methods=["POST"])
    @_streamable
    def rag_ask():
        body  = request.get_json(silent=True) or {}
        model = body.get("model", "") or _default_model()
        query = body.get("query", "")
        ctx   = body.get("context", "").strip()
        if not ctx:
            return jsonify({"error": "context is required"})
        m_name, m_url, m_bk = parse_model_spec(model)
        prompt = (
            f"Use the following sources to answer the question. "
            f"Be concise and cite sources by filename when relevant.\n\n"
            f"{ctx}\n\n---\n\nQuestion: {query}\n\nAnswer:"
        )
        try:
            answer = complete([{"role": "user", "content": prompt}],
                              m_name, m_url or base_url, backend=m_bk or backend,
                              num_ctx=8192, timeout=timeout * 2)
            return jsonify({"result": answer})
        except Exception as e:
            return jsonify({"error": str(e)})

    # ── /vyrii/files/* ────────────────────────────────────────────────────────

    _FHOME = pathlib.Path.home() / ".vyrii"

    def _fsafe(rel: str) -> pathlib.Path:
        p = (_FHOME / rel.lstrip("/\\")).resolve()
        if not str(p).startswith(str(_FHOME.resolve())):
            raise ValueError("Path outside ~/.vyrii/")
        return p

    # vyrii's own state in the root of ~/.vyrii: Files may show it, never write or delete it
    # (an uploaded config.json would change the login, a scheduler.json the commands it runs,
    # a processes.json the pid that Stop kills)
    _FPROTECTED_FILES = {"config.json", "scheduler.json", "process_tools.json"}
    _FPROTECTED_DIRS  = {"process", "scheduler_logs"}

    def _fwritable(rel: str) -> pathlib.Path:
        p = _fsafe(rel)
        parts = p.relative_to(_FHOME.resolve()).parts
        if not parts:
            raise ValueError("~/.vyrii itself cannot be changed here")
        if parts[0] in _FPROTECTED_DIRS or (len(parts) == 1 and (
                parts[0] in _FPROTECTED_FILES or parts[0].startswith("history.db"))):
            raise ValueError(f"{parts[0]} belongs to vyrii itself — not writable from Files")
        # .git is written by git alone: hooks and config there run programs on the next pull
        if any(x.lower() == ".git" for x in parts):
            raise ValueError(".git is written by git only — not from Files")
        return p

    def _flist(root: pathlib.Path, depth: int = 0, max_depth: int = 6) -> dict:
        if not root.is_dir() or depth > max_depth:
            return {}
        result = {}
        for item in sorted(root.iterdir(), key=lambda x: (x.is_file(), x.name)):
            key = item.name + ("/" if item.is_dir() else "")
            if item.name == ".git" and item.is_dir():
                result[key] = {}            # a repository: shown, its objects not listed
                continue
            result[key] = _flist(item, depth + 1, max_depth) if item.is_dir() else None
        return result

    @app.route("/vyrii/files/read", methods=["GET"])
    def files_read():
        path = request.args.get("path", "").strip()
        if not path:
            return jsonify({"error": "path is required"})
        try:
            p = _fsafe(path)
            if not p.is_file():
                return jsonify({"error": f"Not a file: {path}"})
            size = p.stat().st_size
            MAX  = 65536
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return jsonify({"error": f"Cannot read: {e}"})
            return jsonify({
                "name": p.name, "path": path, "size": size,
                "content": content[:MAX], "truncated": len(content) > MAX,
            })
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)})

    @app.route("/vyrii/files/list", methods=["GET"])
    def files_list():
        path = request.args.get("path", "").strip()
        try:
            root = _fsafe(path) if path else _FHOME
            if not root.is_dir():
                return jsonify({"error": f"Not a directory: {path}"})
            return jsonify({"path": str(root), "tree": _flist(root)})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/vyrii/files/mkdir", methods=["POST"])
    def files_mkdir():
        body = request.get_json(silent=True) or {}
        try:
            _fwritable(body.get("path", "")).mkdir(parents=True, exist_ok=True)
            return jsonify({"ok": True, "path": body.get("path", "")})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)})

    def _unique(target: pathlib.Path) -> pathlib.Path:
        """name.ext -> name (1).ext, name (2).ext … — a free name next to it."""
        n, cand = 1, target
        while cand.exists():
            cand = target.with_name(f"{target.stem} ({n}){target.suffix}")
            n += 1
        return cand

    @app.route("/vyrii/files/upload", methods=["POST"])
    def files_upload():
        dest   = request.args.get("dest", "") or request.form.get("dest", "")
        files  = request.files.getlist("files")
        # unique=1: never overwrite (chat attachments — an older message of a
        # chat may point to a file of the same name)
        unique = (request.args.get("unique", "") or request.form.get("unique", "")) in ("1", "true")
        saved: list[str] = []
        errors: list[dict] = []
        for f in files:
            try:
                fname = f.filename or "upload"
                rel   = (dest.rstrip("/\\") + "/" + fname) if dest.strip() else fname
                target = _fwritable(rel)
                if unique:
                    target = _unique(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(f.read())
                saved.append(str(target.relative_to(_FHOME)))
            except Exception as e:
                errors.append({"file": getattr(f, "filename", "?"), "error": str(e)})
        if errors and not saved:
            return jsonify({"error": errors})
        return jsonify({"ok": True, "saved": saved, **({"errors": errors} if errors else {})})

    @app.route("/vyrii/files/unzip", methods=["POST"])
    def files_unzip():
        from vyrii import fileops
        body = request.get_json(silent=True) or {}
        try:
            r = fileops.unzip(_fsafe(body.get("path", "")), writable=lambda p: _fwritable(
                str(p.relative_to(_FHOME.resolve()))))
            dest = str(r["dest"].relative_to(_FHOME.resolve())).replace("\\", "/") + "/"
            return jsonify({"ok": True, "dest": dest, "files": r["files"], "skipped": r["skipped"]})
        except (ValueError, fileops.FileOpError) as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)})

    @app.route("/vyrii/files/git", methods=["POST"])
    def files_git():
        """{op: clone, path: folder, url, name?} | {op: pull, path} | {op: checkout, path, ref}"""
        from vyrii import fileops
        body = request.get_json(silent=True) or {}
        op = body.get("op", "")
        writable = lambda p: _fwritable(str(p.relative_to(_FHOME.resolve())))
        try:
            folder = _fsafe(body.get("path", ""))
            if op == "clone":
                r = fileops.clone(body.get("url", ""), folder, body.get("name", ""), writable=writable)
                r["path"] = str(r["path"].relative_to(_FHOME.resolve())).replace("\\", "/") + "/"
            elif op == "pull":
                writable(folder)
                r = fileops.pull(folder)
            elif op == "checkout":
                writable(folder)
                r = fileops.checkout(folder, body.get("ref", ""))
            elif op == "head":
                r = {"head": fileops.head(folder) if fileops.is_repo(folder) else ""}
            else:
                return jsonify({"error": f"unknown op: {op}"}), 400
            return jsonify({"ok": True, **r})
        except (ValueError, fileops.FileOpError) as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)})

    @app.route("/vyrii/files/raw", methods=["GET"])
    def files_raw():
        """The file itself (chat thumbnails of attached images)."""
        try:
            p = _fsafe(request.args.get("path", "").strip())
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        if not p.is_file():
            return jsonify({"error": "Not a file"}), 404
        return send_from_directory(str(p.parent), p.name)

    @app.route("/vyrii/model/caps", methods=["GET"])
    def model_caps():
        """{"vision": true|false|null} — null = unknown (not Ollama, or no answer)."""
        m_name, m_url, m_backend = parse_model_spec(request.args.get("model", ""))
        caps = model_capabilities(m_name, m_url or base_url, m_backend or backend)
        return jsonify({"capabilities": caps,
                        "vision": None if caps is None else "vision" in caps})

    @app.route("/vyrii/attach/list", methods=["GET"])
    def attach_list():
        """Files uploaded earlier to ~/.vyrii/input/ (the chat's "uploaded earlier")."""
        return jsonify({"files": _att.list_input(_FHOME)})

    @app.route("/vyrii/attach/check", methods=["GET"])
    def attach_check():
        """Can the model get this attachment? {"ok": true, "kind"} or {"ok": false, "error"}."""
        return jsonify(_att.check(_FHOME, request.args.get("path", "")))

    @app.route("/vyrii/files", methods=["DELETE"])
    def files_delete():
        body = request.get_json(silent=True) or {}
        try:
            p = _fwritable(body.get("path", ""))
            if p.is_dir():
                # git's pack files are read-only on Windows: clear the flag and retry
                def _ro(fn, path, _exc):
                    os.chmod(path, 0o666)
                    fn(path)
                shutil.rmtree(p, onerror=_ro)
            elif p.is_file():
                p.unlink()
            else:
                return jsonify({"error": f"Not found: {body.get('path', '')}"})
            return jsonify({"ok": True})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)})

    # ── /vyrii/process/* — pantheon UIs started on a folder inside ~/.vyrii ──
    # only the fixed commands of process.TOOLS; the Process tab lists them

    from . import process as _proc

    def _proc_err(e):
        return jsonify({"error": str(e)}), 400

    @app.route("/vyrii/process/tools", methods=["GET"])
    def process_tools():
        try:
            return jsonify(_proc.tools())
        except _proc.ProcessError as e:
            return _proc_err(e)

    @app.route("/vyrii/process/list", methods=["GET"])
    def process_list():
        return jsonify({"processes": _proc.entries()})

    @app.route("/vyrii/process/run", methods=["POST"])
    def process_run():
        body = request.get_json(silent=True) or {}
        rel = str(body.get("path", "")).strip().strip("/\\")
        try:
            folder = _fsafe(rel)
            port = body.get("port")
            return jsonify(_proc.start(str(body.get("tool", "")), folder, rel or ".",
                                       bind=app.config.get("VYRII_BIND", "127.0.0.1"),
                                       port=int(port) if port not in (None, "") else None))
        except (ValueError, _proc.ProcessError) as e:
            return _proc_err(e)

    @app.route("/vyrii/process/stop", methods=["POST"])
    def process_stop():
        try:
            return jsonify(_proc.stop(str((request.get_json(silent=True) or {}).get("id", ""))))
        except _proc.ProcessError as e:
            return _proc_err(e)

    @app.route("/vyrii/process/remove", methods=["POST"])
    def process_remove():
        try:
            _proc.remove(str((request.get_json(silent=True) or {}).get("id", "")))
            return jsonify({"ok": True})
        except _proc.ProcessError as e:
            return _proc_err(e)

    @app.route("/vyrii/process/log", methods=["GET"])
    def process_log():
        try:
            return jsonify({"log": _proc.log_tail(request.args.get("id", ""))})
        except _proc.ProcessError as e:
            return _proc_err(e)

    @app.route("/vyrii/files/index", methods=["POST"])
    def files_index():
        import subprocess
        body = request.get_json(silent=True) or {}
        try:
            target = _fsafe(body.get("path", ""))
            if not target.is_dir():
                return jsonify({"error": f"Not a directory: {body.get('path', '')}"})
            project = target.name
            rel     = str(target.relative_to(_FHOME))
            result  = subprocess.run(
                ["simargl", "index", "files", rel,
                 "--project", project, "--store", ".simargl"],
                capture_output=True, text=True, timeout=300,
                cwd=str(_FHOME),
            )
            if result.returncode == 0:
                return jsonify({"ok": True, "project": project})
            return jsonify({"error": result.stderr[:500]})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)})

    # ── /vyrii/glossary/* ─────────────────────────────────────────────────────
    # /flow glossary ported into vyrii/flows/glossary.py (self-contained copy,
    # same convention as ctxwindow.py) — see concepts/GLOSSARY.md in simrgl for
    # the full design rationale.

    def _glossary_index_args(body: dict) -> str:
        project = body.get("project", "default").strip() or "default"
        args = f'--project {project}'
        args += f' --chunk {int(body.get("chunk", 1000))}'
        args += f' --overlap {int(body.get("overlap", 50))}'
        for flag in ("redefine", "refact", "crosslink", "unique", "tabular"):
            if body.get(flag):
                args += f' --{flag}'
        return args

    # run_id -> {"stop": bool} — same pattern as _ctxtimer_cancel_flags: a
    # background indexing job can't receive Ctrl+C (that's 1bcoder-CLI-only,
    # SIGINT isn't delivered to worker threads), so the Stop button flips
    # this flag instead; glossary.py's _llm() checks it before/after every
    # LLM call via chat._glossary_should_cancel.
    _glossary_cancel_flags: dict = {}

    @app.route("/vyrii/glossary/index", methods=["POST"])
    def glossary_index():
        from .adapter import ChatAdapter, stream_flow_lines
        from .flows import glossary as _gl
        body = request.get_json(silent=True) or {}
        model = body.get("model", "") or _default_model()
        try:
            target = _fsafe(body.get("path", ""))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        if not target.is_dir():
            return jsonify({"error": f"Not a directory: {body.get('path', '')}"}), 400

        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        args = f'index "{target}" ' + _glossary_index_args(body)

        cancel_flag = {"stop": False}
        run_id = str(uuid.uuid4())
        _glossary_cancel_flags[run_id] = cancel_flag
        adapter._glossary_should_cancel = lambda: cancel_flag["stop"]

        def _gen():
            def _run():
                _gl.set_base_dir(str(target))
                _gl.run(adapter, args)
            try:
                yield f'data: {json.dumps({"type": "started", "run_id": run_id})}\n\n'
                for line in stream_flow_lines(_run):
                    yield f'data: {json.dumps({"type": "line", "line": line})}\n\n'
            finally:
                _glossary_cancel_flags.pop(run_id, None)
                yield 'data: [DONE]\n\n'

        return Response(stream_with_context(_gen()), mimetype="text/event-stream")

    @app.route("/vyrii/glossary/index/cancel", methods=["POST"])
    def glossary_index_cancel():
        body = request.get_json(silent=True) or {}
        flag = _glossary_cancel_flags.get(body.get("run_id", ""))
        if flag is not None:
            flag["stop"] = True
        return jsonify({"ok": True})

    @app.route("/vyrii/glossary/projects", methods=["GET"])
    def glossary_projects():
        from .flows import glossary as _gl
        results = _gl.list_glossaries(str(_FHOME))
        for r in results:
            try:
                r["folder"] = str(pathlib.Path(r["folder"]).relative_to(_FHOME))
            except ValueError:
                pass
        return jsonify({"projects": results})

    @app.route("/vyrii/glossary/terms", methods=["GET"])
    def glossary_terms():
        from .flows import glossary as _gl
        project = request.args.get("project", "default").strip() or "default"
        query   = request.args.get("q", "").strip().lower()
        terms = _gl._load_terms(project)
        if query:
            terms = [t for t in terms if query in t]
        return jsonify({"terms": terms})

    @app.route("/vyrii/glossary/term", methods=["GET"])
    def glossary_term():
        from .flows import glossary as _gl
        project = request.args.get("project", "default").strip() or "default"
        term    = request.args.get("term", "").strip()
        if not term:
            return jsonify({"error": "term is required"}), 400
        if not os.path.isfile(_gl._term_path(project, term)):
            return jsonify({"error": f"no such term: {_gl._kebab(term)}"}), 404
        return jsonify(_gl._read_term_file(project, term))

    # ── /vyrii/ontology/* ─────────────────────────────────────────────────────
    # The logic lives in vyrii/onto_service.py, shared with api.py and app.py;
    # these routes only translate HTTP. See concepts/ONTOLOGY.md in simrgl.

    from . import onto_service as _ons
    _ons.setup(_FHOME)

    def _onto_err(e):
        return jsonify({"error": str(e)}), e.status

    @app.route("/vyrii/ontology/index", methods=["POST"])
    def ontology_index():
        from .adapter import ChatAdapter
        body = request.get_json(silent=True) or {}
        model = body.get("model", "") or _default_model()
        try:
            target = _fsafe(body.get("path", ""))
            if not target.exists():
                raise _ons.OntoError(f"Not found: {body.get('path', '')}")
            args = _ons.index_args(target, body.get("project"), body.get("chunk", 1200),
                                   body.get("strict"), body.get("jev", True))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except _ons.OntoError as e:
            return _onto_err(e)
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        return Response(stream_with_context(_ons.index_sse(adapter, args)),
                        mimetype="text/event-stream")

    @app.route("/vyrii/ontology/index/cancel", methods=["POST"])
    def ontology_index_cancel():
        _ons.cancel_index((request.get_json(silent=True) or {}).get("run_id"))
        return jsonify({"ok": True})

    @app.route("/vyrii/ontology/projects", methods=["GET"])
    def ontology_projects():
        return jsonify({"projects": _ons.projects()})

    @app.route("/vyrii/ontology/triples", methods=["GET"])
    def ontology_triples():
        try:
            return jsonify({"triples": _ons.triples(request.args.get("project"),
                                                    request.args.get("q", ""))})
        except _ons.OntoError as e:
            return _onto_err(e)

    @app.route("/vyrii/ontology/journal", methods=["GET"])
    def ontology_journal():
        try:
            return jsonify({"rows": _ons.journal(request.args.get("project"))})
        except _ons.OntoError as e:
            return _onto_err(e)

    @app.route("/vyrii/ontology/resolve", methods=["POST"])
    def ontology_resolve():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(_ons.resolve(body.get("project"), body.get("id"),
                                        body.get("choice", ""), body.get("text")))
        except _ons.OntoError as e:
            return _onto_err(e)

    @app.route("/vyrii/ontology/cmd", methods=["POST"])
    def ontology_cmd():
        """add / remove / check / show / ask / sparql / export -> printed text."""
        body = request.get_json(silent=True) or {}
        try:
            return jsonify({"result": _ons.command(body.get("cmd", ""), body.get("project"),
                                                   body.get("arg"), body.get("strict"))})
        except _ons.OntoError as e:
            return _onto_err(e)

    # ── /vyrii/roles/* ────────────────────────────────────────────────────────
    # Separate entity from /vyrii/prompts — a Role is chosen once at the start
    # of a new chat and becomes an invisible system message for the rest of
    # it (see vyrii/roles.py); Prompts are manually inserted snippets.

    from . import roles as _roles
    _roles.init(_VYRII_HOME)

    @app.route("/vyrii/roles", methods=["GET"])
    def roles_list():
        return jsonify({"roles": _roles.list_roles()})

    @app.route("/vyrii/roles/<name>", methods=["GET"])
    def roles_get(name):
        r = _roles.get_role(name)
        if r is None:
            return jsonify({"error": f"Role '{name}' not found"}), 404
        return jsonify(r)

    @app.route("/vyrii/roles", methods=["POST"])
    def roles_save():
        body = request.get_json(silent=True) or {}
        name = body.get("name", "")
        if not name.strip():
            return jsonify({"error": "name is required"}), 400
        _roles.save_role(name, body.get("prompt", ""), body.get("size", 0))
        return jsonify({"ok": True})

    @app.route("/vyrii/roles/<name>", methods=["DELETE"])
    def roles_delete(name):
        _roles.delete_role(name)
        return jsonify({"ok": True})

    # ── /vyrii/team/* ─────────────────────────────────────────────────────────

    from . import parallel as _par
    _par.init(_VYRII_HOME)

    @app.route("/vyrii/team/profiles", methods=["GET"])
    def team_profiles():
        return jsonify({"profiles": _par.load_profiles()})

    @app.route("/vyrii/team/profile/<name>", methods=["GET"])
    def team_profile_get(name):
        p = _par.get_profile(name)
        if p is None:
            return jsonify({"error": f"Profile '{name}' not found"}), 404
        return jsonify(p)

    @app.route("/vyrii/team/profile", methods=["POST"])
    def team_profile_save():
        body = request.get_json(silent=True) or {}
        _par.upsert_profile(body)
        return jsonify({"ok": True})

    @app.route("/vyrii/team/profile/<name>", methods=["DELETE"])
    def team_profile_delete(name):
        _par.delete_profile(name)
        return jsonify({"ok": True})

    @app.route("/vyrii/team/run", methods=["POST"])
    def team_run():
        import queue as _q
        body         = request.get_json(silent=True) or {}
        profile_name = body.get("profile_name", "")
        query        = body.get("query", "")
        aspects      = body.get("aspects", [])
        ctx_mode     = body.get("ctx_mode", "none")
        combine      = body.get("combine", "join")
        messages     = body.get("messages", [])
        model        = body.get("model", "") or _default_model()
        num_ctx      = body.get("num_ctx", 4096)
        timeout      = body.get("timeout", 180)

        profile = _par.get_profile(profile_name)
        if profile is None:
            def _e():
                yield f'data: {json.dumps({"type": "error", "text": f"Profile not found: {profile_name}"})}\n\n'
            return Response(stream_with_context(_e()), mimetype="text/event-stream")

        workers = profile.get("workers", [])
        if not workers:
            def _e2():
                yield f'data: {json.dumps({"type": "error", "text": "Profile has no workers"})}\n\n'
            return Response(stream_with_context(_e2()), mimetype="text/event-stream")

        q: _q.Queue = _q.Queue()

        def _run():
            try:
                results = _par.run_parallel(
                    workers, aspects, query, messages, num_ctx, timeout,
                    lambda msg: q.put({"type": "progress", "text": msg}),
                )
                if combine == "compact":
                    final = _par.compact_results(
                        query, results, model,
                        base_url, backend, num_ctx, timeout,
                    )
                else:
                    final = _par.join_results(query, results)
                q.put({"type": "done", "text": final})
            except Exception as e:
                q.put({"type": "error", "text": str(e)})

        threading.Thread(target=_run, daemon=True).start()

        def _gen():
            while True:
                item = q.get()
                yield f"data: {json.dumps(item)}\n\n"
                if item["type"] in ("done", "error"):
                    break

        return Response(stream_with_context(_gen()), mimetype="text/event-stream")

    # ── /vyrii/ctxtimer/* ─────────────────────────────────────────────────────

    _ctxtimer_cancel_flags: dict = {}

    @app.route("/vyrii/ctxtimer/run", methods=["POST"])
    def ctxtimer_run():
        import queue as _q
        from . import ctxtimer as _ct
        from .adapter import ChatAdapter

        body    = request.get_json(silent=True) or {}
        model   = body.get("model", "") or _default_model()
        mode    = body.get("mode", "seq")
        start   = int(body.get("start", 1000))
        end     = body.get("end")
        end     = int(end) if end is not None else None
        step    = int(body.get("step", 1000))
        full    = bool(body.get("full", False))
        req_timeout = int(body.get("timeout") or timeout)

        base_prompt = _ct.load_base_prompt()
        if not base_prompt:
            def _e():
                yield f'data: {json.dumps({"type": "error", "text": "base_prompt.txt not found"})}\n\n'
            return Response(stream_with_context(_e()), mimetype="text/event-stream")

        max_test = end or _ct.chars_to_tokens(len(base_prompt))
        num_ctx  = _ct.safe_num_ctx(max(max_test, start))
        adapter  = ChatAdapter(model=model, base_url=base_url, backend=backend,
                                num_ctx=num_ctx, timeout=req_timeout)

        q: _q.Queue = _q.Queue()
        cancel_flag = {"stop": False}
        run_id = str(uuid.uuid4())
        _ctxtimer_cancel_flags[run_id] = cancel_flag

        def _run():
            try:
                result = _ct.run_search(
                    adapter, mode=mode, start=start, end=end, step=step, full_mode=full,
                    progress_cb=lambda ev: q.put({"type": "progress", **ev}),
                    should_cancel=lambda: cancel_flag["stop"],
                )
                q.put({"type": "done", **result})
            except Exception as e:
                q.put({"type": "error", "text": str(e)})
            finally:
                _ctxtimer_cancel_flags.pop(run_id, None)

        threading.Thread(target=_run, daemon=True).start()

        def _gen():
            yield f'data: {json.dumps({"type": "started", "run_id": run_id})}\n\n'
            while True:
                item = q.get()
                yield f"data: {json.dumps(item)}\n\n"
                if item["type"] in ("done", "error"):
                    break

        return Response(stream_with_context(_gen()), mimetype="text/event-stream")

    @app.route("/vyrii/ctxtimer/cancel", methods=["POST"])
    def ctxtimer_cancel():
        body = request.get_json(silent=True) or {}
        flag = _ctxtimer_cancel_flags.get(body.get("run_id", ""))
        if flag is not None:
            flag["stop"] = True
        return jsonify({"ok": True})

    @app.route("/vyrii/ctxtimer/report", methods=["GET"])
    def ctxtimer_report():
        from . import ctxtimer as _ct
        model_filter = request.args.get("model") or None
        return jsonify({"rows": _ct.list_report(model_filter)})

    @app.route("/vyrii/ctxtimer/report", methods=["DELETE"])
    def ctxtimer_report_clear():
        from . import ctxtimer as _ct
        return jsonify({"ok": _ct.clear_report()})

    # ── /vyrii/system/* ───────────────────────────────────────────────────────

    _ROOT = pathlib.Path(__file__).parent.parent

    @app.route("/vyrii/system/restart", methods=["POST"])
    def system_restart():
        # used to run vyrii_auto_api.bat/.sh from the source checkout (FastAPI on :8000,
        # absent from an installed package); now the same restart as restart-cmd
        cmd, port = _restart_argv()
        _restart_with(cmd)
        return jsonify({"ok": True, "port": port,
                        "message": "Restarting vyrii… reload the page in a few seconds."})

    def _restart_argv() -> tuple[list[str], int | None]:
        """The command vyrii was started with. With a port saved in Settings
        (config.json "port") the port options are dropped, so the saved port wins
        (-p > config.json > 5000 in __main__); without one the command stays as it was.
        Never a command from the browser."""
        argv = sys.argv[:]
        cfg = _read_cfg()
        if cfg.get("auth") is False:           # switched off in Settings: drop --auth of the start
            argv = [a for a in argv if a != "--auth"]
        saved = cfg.get("port")
        try:
            saved = int(saved) if saved else None
        except (TypeError, ValueError):
            saved = None
        if not saved:
            return argv, None
        out, i = [argv[0]], 1
        while i < len(argv):
            a = argv[i]
            if a in ("-p", "--port"):
                i += 2
                continue
            if a.startswith("--port=") or (a.startswith("-p") and a[2:].isdigit()):
                i += 1
                continue
            if a == "--flask" and i + 1 < len(argv) and argv[i + 1].isdigit():
                out.append(a)          # bare --flask: the port comes from -p / config.json
                i += 2
                continue
            if a.startswith("--flask="):
                out.append("--flask")
                i += 1
                continue
            out.append(a)
            i += 1
        return out, saved

    def _restart_with(cmd: list[str]):
        def _do():
            import subprocess as _sp
            time.sleep(1.5)
            _sp.Popen(cmd, cwd=os.getcwd(), start_new_session=True)
            os._exit(0)
        threading.Thread(target=_do, daemon=False).start()

    @app.route("/vyrii/system/restart-cmd", methods=["POST"])
    def system_restart_cmd():
        # the body is ignored on purpose: it used to carry a command to run, i.e.
        # any program on this machine for anyone who could reach vyrii
        cmd, port = _restart_argv()
        _restart_with(cmd)
        return jsonify({"ok": True, "port": port})

    def _ollama_env() -> dict:
        cfg = _read_cfg()
        env = os.environ.copy()
        kv = cfg.get("ollama_kv_cache", "").strip()
        if kv: env["OLLAMA_KV_CACHE_TYPE"] = kv
        if cfg.get("ollama_flash_attention"): env["OLLAMA_FLASH_ATTENTION"] = "1"
        ka = cfg.get("ollama_keep_alive", "").strip()
        if ka: env["OLLAMA_KEEP_ALIVE"] = ka
        ml = str(cfg.get("ollama_max_loaded_models", "")).strip()
        if ml: env["OLLAMA_MAX_LOADED_MODELS"] = ml
        oh = cfg.get("ollama_host", "").strip()
        if oh: env["OLLAMA_HOST"] = oh
        env["OLLAMA_VULKAN"] = "1" if cfg.get("ollama_vulkan") else "0"
        env["OLLAMA_IGPU_ENABLE"] = "1" if cfg.get("ollama_igpu_enable") else "0"
        return env

    def _ollama_persist_windows(env: dict) -> None:
        """Write Ollama env vars to HKCU\\Environment so any auto-restart picks them up."""
        import winreg
        _OLLAMA_VARS = ["OLLAMA_KV_CACHE_TYPE", "OLLAMA_FLASH_ATTENTION",
                        "OLLAMA_KEEP_ALIVE", "OLLAMA_MAX_LOADED_MODELS", "OLLAMA_HOST",
                        "OLLAMA_VULKAN", "OLLAMA_IGPU_ENABLE"]
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE)
        for name in _OLLAMA_VARS:
            val = env.get(name, "")
            if val:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, val)
            else:
                try: winreg.DeleteValue(key, name)
                except FileNotFoundError: pass
        winreg.CloseKey(key)

    @app.route("/vyrii/system/ollama-restart", methods=["POST"])
    def ollama_restart():
        import socket as _sock
        env = _ollama_env()
        if platform.system() == "Windows":
            try: _ollama_persist_windows(env)
            except Exception: pass
            # Kill existing Ollama
            subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
            # Wait until port 11434 is free (catches any auto-restart that grabs the port)
            for _ in range(20):
                time.sleep(0.5)
                try:
                    s = _sock.socket()
                    s.bind(("127.0.0.1", 11434))
                    s.close()
                    break  # port is free
                except OSError:
                    # still occupied — kill whatever grabbed it and try again
                    subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
            log_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Ollama")
            os.makedirs(log_dir, exist_ok=True)
            log_fh = open(os.path.join(log_dir, "server.log"), "a", encoding="utf-8")
            subprocess.Popen(["ollama", "serve"], env=env,
                             start_new_session=True,
                             stdout=log_fh, stderr=log_fh)
        else:
            subprocess.run(["pkill", "-x", "ollama"], capture_output=True)
            time.sleep(1)
            subprocess.Popen(["ollama", "serve"], env=env,
                             start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({"ok": True})

    @app.route("/vyrii/system/reboot", methods=["POST"])
    def system_reboot():
        import subprocess
        body = request.get_json(silent=True) or {}
        if not body.get("confirmed", False):
            return jsonify({"error": "Set confirmed=true to proceed."})
        if platform.system() == "Windows":
            subprocess.Popen(["shutdown", "/r", "/t", "10"])
            return jsonify({"ok": True, "message": "Windows reboot in 10 s."})
        subprocess.Popen(["systemctl", "reboot"])
        return jsonify({"ok": True, "message": "System reboot initiated."})

    @app.route("/vyrii/system/shutdown", methods=["POST"])
    def system_shutdown():
        import subprocess
        body = request.get_json(silent=True) or {}
        if not body.get("confirmed", False):
            return jsonify({"error": "Set confirmed=true to proceed."})
        if platform.system() == "Windows":
            subprocess.Popen(["shutdown", "/s", "/t", "10"])
            return jsonify({"ok": True, "message": "Windows shutdown in 10 s."})
        subprocess.Popen(["systemctl", "poweroff"])
        return jsonify({"ok": True, "message": "System shutdown initiated."})

    # ── /vyrii/projects ───────────────────────────────────────────────────────

    _PROJECTS_FILE = pathlib.Path.home() / ".vyrii" / "projects.json"

    def _load_projects() -> list:
        try:
            if _PROJECTS_FILE.exists():
                return json.loads(_PROJECTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return []

    def _save_projects(projects: list):
        _PROJECTS_FILE.parent.mkdir(exist_ok=True)
        _PROJECTS_FILE.write_text(
            json.dumps(projects, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _resolve_project_cwd(project_name: str) -> str | None:
        for p in _load_projects():
            if p.get("name") == project_name:
                return p.get("path", "")
        return None

    @app.route("/vyrii/projects", methods=["GET"])
    def projects_list():
        return jsonify({"projects": _load_projects()})

    @app.route("/vyrii/projects", methods=["POST"])
    def projects_add():
        body = request.get_json(silent=True) or {}
        name = body.get("name", "").strip()
        if not name:
            return jsonify({"error": "name is required"})
        projects = _load_projects()
        projects = [p for p in projects if p.get("name") != name]
        projects.append({"name": name, "path": body.get("path", "").strip(),
                          "description": body.get("description", "")})
        _save_projects(projects)
        return jsonify({"ok": True, "projects": projects})

    @app.route("/vyrii/projects/<name>", methods=["DELETE"])
    def projects_delete(name):
        projects = [p for p in _load_projects() if p.get("name") != name]
        _save_projects(projects)
        return jsonify({"ok": True, "projects": projects})

    # ── /vyrii/run ────────────────────────────────────────────────────────────

    @app.route("/vyrii/run", methods=["POST"])
    def run_command():
        import subprocess
        body    = request.get_json(silent=True) or {}
        command = body.get("command", "")
        cwd     = body.get("cwd", "").strip() or None
        project = body.get("project", "").strip()

        if project:
            resolved = _resolve_project_cwd(project)
            if resolved is None:
                return jsonify({"error": f"Project not found: {project}"})
            cwd = resolved

        if cwd and not pathlib.Path(cwd).is_dir():
            return jsonify({"error": f"Directory not found: {cwd}"})

        t0 = time.time()
        try:
            result = subprocess.run(
                command, shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=600,
                encoding="utf-8", errors="replace",
            )
            return jsonify({
                "stdout": result.stdout, "stderr": result.stderr,
                "returncode": result.returncode,
                "duration_s": round(time.time() - t0, 2),
                "cwd": cwd or "",
            })
        except subprocess.TimeoutExpired:
            return jsonify({"error": "Command timed out (600s)",
                            "stdout": "", "stderr": "", "returncode": -1})
        except Exception as e:
            return jsonify({"error": str(e), "stdout": "", "stderr": "", "returncode": -1})

    # ── /vyrii/scheduler/* ────────────────────────────────────────────────────

    from . import scheduler as _sch

    @app.route("/vyrii/scheduler/tasks", methods=["GET"])
    def sch_tasks_list():
        return jsonify({"tasks": _sch.load_tasks()})

    @app.route("/vyrii/scheduler/tasks", methods=["POST"])
    def sch_tasks_create():
        body = request.get_json(silent=True) or {}
        name    = body.get("name", "").strip()
        command = body.get("command", "").strip()
        if not name or not command:
            return jsonify({"error": "name and command are required"})
        task = _sch.add_task(
            name=name, command=command,
            schedule_type=body.get("schedule_type", "daily"),
            hour=body.get("hour", 9),
            minute=body.get("minute", 0),
            day_of_week=body.get("day_of_week", "mon"),
            interval_value=body.get("interval_value", 60),
        )
        return jsonify({"ok": True, "task": task})

    @app.route("/vyrii/scheduler/tasks/<task_id>", methods=["DELETE"])
    def sch_tasks_delete(task_id):
        _sch.remove_task(task_id)
        return jsonify({"ok": True})

    @app.route("/vyrii/scheduler/tasks/<task_id>/toggle", methods=["POST"])
    def sch_tasks_toggle(task_id):
        enabled = _sch.toggle_task(task_id)
        return jsonify({"ok": True, "enabled": enabled})

    @app.route("/vyrii/scheduler/tasks/<task_id>/run", methods=["POST"])
    def sch_tasks_run_now(task_id):
        _sch.run_now(task_id)
        return jsonify({"ok": True})

    @app.route("/vyrii/scheduler/tasks/<task_id>/logs", methods=["GET"])
    def sch_task_logs(task_id):
        logs = _sch.get_task_logs(task_id)
        return jsonify({"logs": [{"filename": f.name, "size": f.stat().st_size,
                                   "mtime": f.stat().st_mtime} for f in logs]})

    @app.route("/vyrii/scheduler/log", methods=["GET"])
    def sch_log_read():
        filename = request.args.get("filename", "").strip()
        if not filename:
            return jsonify({"error": "filename is required"})
        log_dir = pathlib.Path.home() / ".vyrii" / "scheduler_logs"
        p = (log_dir / filename).resolve()
        if not str(p).startswith(str(log_dir)):
            return jsonify({"error": "path outside log dir"})
        if not p.is_file():
            return jsonify({"error": "log not found"})
        return jsonify({"content": p.read_text(encoding="utf-8", errors="replace"),
                        "filename": filename})

    # ── /vyrii/prompts ────────────────────────────────────────────────────────

    def _prm_path() -> pathlib.Path:
        return pathlib.Path.home() / ".vyrii" / "prompts.json"

    def _load_prompts() -> list[dict]:
        p = _prm_path()
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_prompts(items: list[dict]) -> None:
        _prm_path().parent.mkdir(parents=True, exist_ok=True)
        _prm_path().write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @app.route("/vyrii/prompts", methods=["GET"])
    def prompts_list():
        return jsonify({"prompts": _load_prompts()})

    @app.route("/vyrii/prompts", methods=["POST"])
    def prompts_save():
        body = request.get_json(silent=True) or {}
        name   = body.get("name", "").strip()
        prompt = body.get("prompt", "")
        if not name or not prompt:
            return jsonify({"error": "name and prompt are required"})
        pid   = body.get("id", "").strip() or uuid.uuid4().hex[:12]
        items = [p for p in _load_prompts() if p.get("id") != pid]
        items.append({
            "id": pid, "name": name, "prompt": prompt,
            "description": body.get("description", ""),
            "model": body.get("model", "").strip(),
            "area": body.get("area", "").strip(),
        })
        _save_prompts(items)
        return jsonify({"ok": True, "id": pid})

    @app.route("/vyrii/prompts/<prompt_id>", methods=["DELETE"])
    def prompts_delete(prompt_id):
        items = [p for p in _load_prompts() if p.get("id") != prompt_id]
        _save_prompts(items)
        return jsonify({"ok": True})

    # ── static UI ─────────────────────────────────────────────────────────────

    _ui_dir = os.path.join(os.path.dirname(__file__), "ui")

    if os.path.isdir(_ui_dir):
        @app.route("/")
        def _ui_root():
            return redirect("/ui/")

        # no-cache: the browser asks every time whether a page file changed (a 304 when not),
        # so a new vyrii shows its new app.js without Ctrl+F5
        @app.route("/ui/")
        def _ui_index():
            r = send_from_directory(_ui_dir, "index.html", max_age=0)
            r.headers["Cache-Control"] = "no-cache"
            return r

        @app.route("/ui/<path:filename>")
        def _ui_static(filename):
            r = send_from_directory(_ui_dir, filename, max_age=0)
            r.headers["Cache-Control"] = "no-cache"
            return r

    return app


def main(port: int = 5000, host: str = "0.0.0.0",
         base_url: str = DEFAULT_OLLAMA, backend: str = BACKEND_OLLAMA,
         auth: bool = False) -> None:
    app = create_app(base_url=base_url, backend=backend, auth=auth)
    app.config["VYRII_BIND"] = host
    print(f"vyrii -> http://localhost:{port}  (UI: /ui/)")
    print(f"  backend : {base_url}  ({backend})")
    print(f"  auth    : {'on' if auth else 'off'}")
    from waitress import serve
    serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    import sys
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    main(port=_port)
