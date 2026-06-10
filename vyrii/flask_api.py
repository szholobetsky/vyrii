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
import tempfile
import threading
import time
import uuid

from flask import (
    Flask, Response, jsonify, redirect,
    request, send_from_directory, stream_with_context,
)
from flask_cors import CORS

from .engine import (
    BACKEND_OLLAMA, DEFAULT_OLLAMA,
    complete, list_models, stream_chat,
)


def create_app(base_url: str = DEFAULT_OLLAMA, backend: str = BACKEND_OLLAMA,
               auth: bool = False) -> Flask:
    app = Flask(__name__, static_folder=None)
    CORS(app)

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
            if request.method == "OPTIONS" or request.path.startswith("/ui"):
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
            return Response(
                "Unauthorized", 401,
                {"WWW-Authenticate": 'Basic realm="vyrii"'},
            )

    def _default_model() -> str:
        models = list_models(base_url, backend)
        return models[0] if models else ""

    # ── /v1/models ────────────────────────────────────────────────────────────

    @app.route("/v1/models", methods=["GET"])
    def get_models():
        models = list_models(base_url, backend)
        return jsonify({
            "object": "list",
            "data": [
                {"id": m, "object": "model", "created": 0, "owned_by": "local"}
                for m in models
            ],
        })

    # ── /v1/chat/completions ──────────────────────────────────────────────────

    @app.route("/v1/chat/completions", methods=["POST"])
    def chat_completions():
        body = request.get_json(silent=True) or {}
        model = body.get("model") or _default_model()
        messages = body.get("messages", [])
        do_stream = body.get("stream", False)
        cid = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created = int(time.time())

        if do_stream:
            def _gen():
                for chunk in stream_chat(messages, model, base_url, backend=backend):
                    data = {
                        "id": cid, "object": "chat.completion.chunk",
                        "created": created, "model": model,
                        "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(data)}\n\n"
                final = {
                    "id": cid, "object": "chat.completion.chunk",
                    "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(final)}\n\n"
                yield "data: [DONE]\n\n"

            return Response(stream_with_context(_gen()), mimetype="text/event-stream")

        full = complete(messages, model, base_url, backend=backend)
        return jsonify({
            "id": cid, "object": "chat.completion",
            "created": created, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": full}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    # ── /vyrii/translate ──────────────────────────────────────────────────────

    _LANG_CODES = {
        "English": "en", "Ukrainian": "uk", "German": "de", "French": "fr",
        "Spanish": "es", "Polish": "pl", "Italian": "it", "Portuguese": "pt",
        "Chinese": "zh", "Japanese": "ja", "Arabic": "ar", "Auto": "auto",
    }

    @app.route("/vyrii/translate", methods=["POST"])
    def translate():
        body = request.get_json(silent=True) or {}
        text      = body.get("text", "").strip()
        from_lang = body.get("from_lang", "Auto")
        to_lang   = body.get("to_lang", "Ukrainian")
        mode      = body.get("mode", "llm").lower()
        model     = body.get("model", "") or _default_model()
        from_code = _LANG_CODES.get(from_lang, from_lang.lower()[:2])
        to_code   = _LANG_CODES.get(to_lang,   to_lang.lower()[:2])

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
        if "translategemma" in model.lower():
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
                r = _req.post(f"{base_url}/v1/chat/completions", json=payload,
                              headers={"Authorization": "Bearer lm-studio"}, timeout=60)
                r.raise_for_status()
                return jsonify({"result": r.json()["choices"][0]["message"]["content"].strip()})
            except Exception as e:
                return jsonify({"error": str(e)})

        from_clause = f" from {from_lang}" if from_lang != "Auto" else ""
        prompt = (
            f"Translate the following text{from_clause} to {to_lang}. "
            f"Output ONLY the translation — no introduction, no explanation.\n\n{text}"
        )
        return jsonify({"result": complete([{"role": "user", "content": prompt}],
                                           model, base_url, backend=backend)})

    # ── /vyrii/webask ─────────────────────────────────────────────────────────

    @app.route("/vyrii/webask", methods=["POST"])
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
            return jsonify({"result": complete([{"role": "user", "content": prompt}],
                                               model, base_url, backend=backend)})
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend)
        _wf.run(adapter, f"{question} -d {top_n}")
        return jsonify({"result": adapter.last_reply or "No results."})

    # ── /vyrii/webcrawl ───────────────────────────────────────────────────────

    @app.route("/vyrii/webcrawl", methods=["POST"])
    def webcrawl():
        import tempfile as _tmp
        from .adapter import ChatAdapter
        from .flows import webcrawl as _wcf
        body      = request.get_json(silent=True) or {}
        model     = body.get("model", "") or _default_model()
        url       = body.get("url", "")
        mode      = body.get("mode", "combine")
        filt      = body.get("filter", "none")
        depth     = body.get("depth", 2)
        max_pages = body.get("max_pages", 20)
        task      = body.get("task", "").strip()
        format_out = body.get("format_out", "log")
        ask       = body.get("ask", False)
        columns   = body.get("columns", "").strip()
        adapter   = ChatAdapter(model=model, base_url=base_url, backend=backend)

        args = f'{url} --mode {mode} --depth {depth} -N {max_pages}'
        if filt != "none":
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

        return jsonify({"result": adapter.last_reply or "No results."})

    # ── /vyrii/deepagent ──────────────────────────────────────────────────────

    @app.route("/vyrii/deepagent", methods=["POST"])
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
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend)
        args = f'"{task}" --maxdepth {sections}'
        if ref_url:
            args += f' --ref {ref_url}'
        if use_web:
            args += f' --web {web_n}'
        if rag_proj:
            _vyrii_home = pathlib.Path.home() / ".vyrii"
            args += f' --rag {rag_proj} --rag-store "{_vyrii_home}"'
        _df.run(adapter, args)
        return jsonify({"result": adapter.last_reply or "No output."})

    # ── /vyrii/webanalys ──────────────────────────────────────────────────────

    @app.route("/vyrii/webanalys", methods=["POST"])
    def webanalys():
        from .adapter import ChatAdapter
        from .flows import webanalys as _waf
        body  = request.get_json(silent=True) or {}
        model = body.get("model", "") or _default_model()
        query = body.get("query", "").strip()
        n     = body.get("n", 5)
        if not query:
            return jsonify({"error": "query is required"})
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend)
        _waf.run(adapter, f'"{query}" -n {n}')
        return jsonify({"result": adapter.last_reply or "No results."})

    # ── /vyrii/scan ───────────────────────────────────────────────────────────

    @app.route("/vyrii/scan", methods=["POST"])
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
        if not path:
            return jsonify({"error": "path is required"})
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend)
        args = f'"{path}"'
        if query:
            args += f' --query "{query}"'
        args += f' --chunk {chunk} --summary {summary} --target {target} --rounds {rounds}'
        if ext:
            args += f' --ext {ext}'
        _sc.run(adapter, args)
        return jsonify({"result": adapter.last_reply or "Done."})

    # ── /vyrii/webindex ───────────────────────────────────────────────────────

    @app.route("/vyrii/webindex", methods=["POST"])
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
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend)
        args = f'"{url}"'
        if project:
            args += f' --project {project}'
        if path:
            args += f' --path "{path}"'
        args += f' --depth {depth} --pages {pages}'
        _wi.run(adapter, args)
        return jsonify({"result": adapter.last_reply or "Done."})

    # ── /vyrii/obfuscate / deobfuscate ────────────────────────────────────────

    def _glossary_tmp(content: str) -> str:
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
        body = request.get_json(silent=True) or {}
        text     = body.get("text", "")
        glossary = body.get("glossary", "").strip()
        force    = body.get("force", False)
        model    = body.get("model", "") or _default_model()
        if not glossary:
            return jsonify({"error": "glossary is required"})
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend)
        adapter._last_output = text
        gpath = _glossary_tmp(glossary)
        args  = f'--glossary {gpath}'
        if force:
            args += ' --force'
        try:
            _of.run(adapter, args)
        finally:
            try: os.unlink(gpath)
            except Exception: pass
        return jsonify({"result": adapter.last_reply or ""})

    @app.route("/vyrii/deobfuscate", methods=["POST"])
    def deobfuscate():
        from .adapter import ChatAdapter
        from .flows import deobfuscate as _dof
        body = request.get_json(silent=True) or {}
        text     = body.get("text", "")
        glossary = body.get("glossary", "").strip()
        force    = body.get("force", False)
        model    = body.get("model", "") or _default_model()
        if not glossary:
            return jsonify({"error": "glossary is required"})
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend)
        adapter._last_output = text
        gpath = _glossary_tmp(glossary)
        args  = f'--glossary {gpath}'
        if force:
            args += ' --force'
        try:
            _dof.run(adapter, args)
        finally:
            try: os.unlink(gpath)
            except Exception: pass
        return jsonify({"result": adapter.last_reply or ""})

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
        return jsonify(_read_cfg())

    @app.route("/vyrii/settings", methods=["POST"])
    def settings_save():
        body = request.get_json(silent=True) or {}
        allowed = {"saved_url", "saved_model", "saved_backend", "lang",
                   "theme", "timeout", "worker_timeout"}
        updates = {k: v for k, v in body.items() if k in allowed and v is not None}
        try:
            _write_cfg(updates)
            return jsonify({"ok": True, "config": _read_cfg()})
        except Exception as e:
            return jsonify({"error": str(e)})

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
        _h.add_message(chat_id, body.get("role", ""), body.get("content", ""))
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
                        "messages": _h.get_messages(chat_id)})

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

        results, sources, ctx_parts = [], [], []
        for i, f in enumerate(files, 1):
            fpath   = pathlib.Path(f.get("path", ""))
            fname   = fpath.name
            score   = f.get("score", 0)
            text    = ""
            for cand in [
                _VYRII_HOME / ".simargl_web" / project / fname,
                _VYRII_HOME / fpath,
                pathlib.Path(fpath),
                _VYRII_HOME / fname,
            ]:
                try:
                    if cand.is_file():
                        text = cand.read_text(encoding="utf-8", errors="replace")[:3000]
                        break
                except Exception:
                    continue
            snippet = text[:300].replace("\n", " ")
            results.append({"rank": i, "file": fname, "score": round(score, 3),
                            "snippet": snippet, "text": text[:1500]})
            sources.append(fname)
            ctx_parts.append(f"[Source {i}: {fname}  score:{score:.2f}]\n{text}")

        return jsonify({"results": results, "context": "\n\n".join(ctx_parts), "sources": sources})

    @app.route("/vyrii/rag/ask", methods=["POST"])
    def rag_ask():
        body  = request.get_json(silent=True) or {}
        model = body.get("model", "") or _default_model()
        query = body.get("query", "")
        ctx   = body.get("context", "").strip()
        if not ctx:
            return jsonify({"error": "context is required"})
        prompt = (
            f"Use the following sources to answer the question. "
            f"Be concise and cite sources by filename when relevant.\n\n"
            f"{ctx}\n\n---\n\nQuestion: {query}\n\nAnswer:"
        )
        try:
            answer = complete([{"role": "user", "content": prompt}],
                              model, base_url, backend=backend,
                              num_ctx=8192, timeout=300)
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

    def _flist(root: pathlib.Path, depth: int = 0, max_depth: int = 6) -> dict:
        if not root.is_dir() or depth > max_depth:
            return {}
        result = {}
        for item in sorted(root.iterdir(), key=lambda x: (x.is_file(), x.name)):
            key = item.name + ("/" if item.is_dir() else "")
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
            _fsafe(body.get("path", "")).mkdir(parents=True, exist_ok=True)
            return jsonify({"ok": True, "path": body.get("path", "")})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)})

    @app.route("/vyrii/files/upload", methods=["POST"])
    def files_upload():
        dest   = request.args.get("dest", "") or request.form.get("dest", "")
        files  = request.files.getlist("files")
        saved: list[str] = []
        errors: list[dict] = []
        for f in files:
            try:
                fname = f.filename or "upload"
                rel   = (dest.rstrip("/\\") + "/" + fname) if dest.strip() else fname
                target = _fsafe(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(f.read())
                saved.append(str(target.relative_to(_FHOME)))
            except Exception as e:
                errors.append({"file": getattr(f, "filename", "?"), "error": str(e)})
        if errors and not saved:
            return jsonify({"error": errors})
        return jsonify({"ok": True, "saved": saved, **({"errors": errors} if errors else {})})

    @app.route("/vyrii/files", methods=["DELETE"])
    def files_delete():
        body = request.get_json(silent=True) or {}
        try:
            p = _fsafe(body.get("path", ""))
            if p.is_dir():
                shutil.rmtree(p)
            elif p.is_file():
                p.unlink()
            else:
                return jsonify({"error": f"Not found: {body.get('path', '')}"})
            return jsonify({"ok": True})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)})

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

    # ── /vyrii/system/* ───────────────────────────────────────────────────────

    _ROOT = pathlib.Path(__file__).parent.parent

    @app.route("/vyrii/system/restart", methods=["POST"])
    def system_restart():
        def _do():
            time.sleep(1.5)
            devnull = open(os.devnull, "w")
            if platform.system() == "Windows":
                import subprocess
                script = str(_ROOT / "vyrii_auto_api.bat")
                DETACHED  = 0x00000008
                NEW_GROUP = 0x00000200
                subprocess.Popen(script, creationflags=DETACHED | NEW_GROUP,
                                 close_fds=True, stdin=subprocess.DEVNULL,
                                 stdout=devnull, stderr=devnull, shell=True)
            else:
                import subprocess
                script = str(_ROOT / "vyrii_auto_api.sh")
                subprocess.Popen(["bash", script], start_new_session=True,
                                 stdin=subprocess.DEVNULL, stdout=devnull, stderr=devnull)
            os._exit(0)
        threading.Thread(target=_do, daemon=False).start()
        return jsonify({"ok": True, "message": "Restarting vyrii… reload the page in a few seconds."})

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

        @app.route("/ui/")
        def _ui_index():
            return send_from_directory(_ui_dir, "index.html")

        @app.route("/ui/<path:filename>")
        def _ui_static(filename):
            return send_from_directory(_ui_dir, filename)

    return app


def main(port: int = 5000, host: str = "0.0.0.0",
         base_url: str = DEFAULT_OLLAMA, backend: str = BACKEND_OLLAMA,
         auth: bool = False) -> None:
    app = create_app(base_url=base_url, backend=backend, auth=auth)
    print(f"vyrii Flask -> http://localhost:{port}  (UI: /ui/)")
    print(f"  backend : {base_url}  ({backend})")
    print(f"  auth    : {'on' if auth else 'off'}")
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    import sys
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    main(port=_port)
