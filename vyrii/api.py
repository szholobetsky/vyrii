"""FastAPI backend — OpenAI-compatible /v1/* + vyrii-specific /vyrii/* endpoints.

OpenAI-compatible (works with Open WebUI, LibreChat, SillyTavern, etc.):
  GET  /v1/models
  POST /v1/chat/completions   (streaming SSE + non-streaming)

vyrii extensions:
  POST /vyrii/translate
  POST /vyrii/webask
  POST /vyrii/webcrawl
  POST /vyrii/deepagent
"""
from __future__ import annotations

import json
import re
import time
import uuid

from fastapi import FastAPI, File as _File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .engine import (
    BACKEND_OLLAMA, BACKEND_OPENAI, DEFAULT_OLLAMA,
    list_models, parse_model_spec, stream_chat, complete,
)
from . import stats as _stats


# ── request models (module-level — required by Pydantic v2) ──────────────────

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: str = ""
    messages: list[ChatMessage]
    stream: bool = False

class TranslateRequest(BaseModel):
    text: str
    from_lang: str = "Auto"
    to_lang: str = "Ukrainian"
    mode: str = "llm"   # llm | argos | nllb
    model: str = ""

class WebAskRequest(BaseModel):
    question: str
    url: str = ""
    top_n: int = 3
    model: str = ""

class WebCrawlRequest(BaseModel):
    url:        str
    mode:       str  = "combine"   # combine | pages | extract | mirror | llm
    filter:     str  = "none"      # none | url-prefix | llm
    url_prefix: str  = ""          # custom prefix for url-prefix filter; defaults to start URL
    path:       str  = ""          # output directory; default: ~/.vyrii/crawl/
    depth:      int  = 2
    max_pages:  int  = 20
    task:       str  = ""
    format_out: str  = "log"       # log | structured  (llm mode)
    ask:        bool = False
    columns:    str  = ""          # YAML for extract/llm mode
    model:      str  = ""

class DeepAgentRequest(BaseModel):
    task:        str
    ref_url:     str  = ""
    sections:    int  = 3
    model:       str  = ""
    use_web:     bool = False
    web_n:       int  = 3
    rag_project: str  = ""

class WebAnalysRequest(BaseModel):
    query: str
    n: int = 5
    model: str = ""

class ScanRequest(BaseModel):
    path: str
    query: str = ""
    chunk: int = 4000
    summary: int = 400
    target: int = 8000
    rounds: int = 1
    ext: str = ""
    model: str = ""

class WebIndexRequest(BaseModel):
    url: str
    project: str = ""
    path: str = ""
    depth: int = 2
    pages: int = 20
    model: str = ""

class InterviewRequest(BaseModel):
    task:         str
    n:            int = 5
    file_content: str = ""
    model:        str = ""

class TeamProfileRequest(BaseModel):
    name:    str
    comment: str        = ""
    workers: list[dict] = []   # [{host, model, provider}]

class TeamRunRequest(BaseModel):
    profile_name: str
    query:        str
    aspects:      list[str] = []
    ctx_mode:     str       = "none"    # none | last | full
    combine:      str       = "join"    # join | compact
    messages:     list[dict] = []       # chat history for last/full mode
    model:        str       = ""
    num_ctx:      int       = 4096
    timeout:      int       = 180

class ObfuscateRequest(BaseModel):
    text: str
    glossary: str
    force: bool = False
    model: str = ""

class DeobfuscateRequest(BaseModel):
    text: str
    glossary: str
    force: bool = False
    model: str = ""

class SystemConfirmRequest(BaseModel):
    confirmed: bool = False

class FileMkdirRequest(BaseModel):
    path: str

class FileDeleteRequest(BaseModel):
    path: str

class FileIndexRequest(BaseModel):
    path: str

class SettingsRequest(BaseModel):
    saved_url:                str | None = None
    saved_model:              str | None = None
    saved_backend:            str | None = None
    lang:                     str | None = None
    theme:                    str | None = None
    timeout:                  int | None = None
    worker_timeout:           int | None = None
    active_profile:           str | None = None
    reserve_mode:             str | None = None
    reserve_timeout:          int | None = None
    restart_cmd:              str | None = None
    ollama_kv_cache:          str | None = None
    ollama_flash_attention:   int | None = None
    ollama_keep_alive:        str | None = None
    ollama_max_loaded_models: str | None = None
    ollama_host:              str | None = None

class LockRequest(BaseModel):
    host:   str
    action: str = "lock"

class AuthChangeRequest(BaseModel):
    username: str
    password: str

class HistCreateRequest(BaseModel):
    title: str

class HistAddMessageRequest(BaseModel):
    role:    str
    content: str

class RagSearchRequest(BaseModel):
    project:      str
    query:        str
    top_k:        int = 3
    mode:         str = "file"   # file | task | aggr | refine
    sort:         str = "rank"   # rank | freq  (task mode only)
    project_path: str = ""       # if set, use <project_path>/.simargl as store_dir

class RagAskRequest(BaseModel):
    query:   str
    context: str
    model:   str = ""

class ProjectRequest(BaseModel):
    name:        str
    path:        str
    description: str = ""

class RunRequest(BaseModel):
    command: str
    cwd:     str = ""
    project: str = ""   # resolves cwd from projects.json if set

class SchTaskRequest(BaseModel):
    name:           str
    command:        str
    schedule_type:  str = "daily"
    hour:           int = 9
    minute:         int = 0
    day_of_week:    str = "mon"
    interval_value: int = 60

class PromptItem(BaseModel):
    id:          str = ""
    name:        str
    prompt:      str
    description: str = ""
    model:       str = ""
    area:        str = ""


def create_app(base_url: str = DEFAULT_OLLAMA, backend: str = BACKEND_OLLAMA,
               auth: bool = False) -> FastAPI:
    import pathlib as _pl_boot
    try:
        _cfg_boot = json.loads((_pl_boot.Path.home() / ".vyrii" / "config.json").read_text(encoding="utf-8"))
    except Exception:
        _cfg_boot = {}
    base_url = _cfg_boot.get("saved_url") or base_url
    timeout  = int(_cfg_boot.get("timeout", 180))
    _sb = (_cfg_boot.get("saved_backend") or "").lower()
    if _sb in ("openai", "openai-compatible"):
        backend = BACKEND_OPENAI
    elif _sb in ("ollama",):
        backend = BACKEND_OLLAMA

    app = FastAPI(title="vyrii API", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if auth:
        import base64 as _b64, pathlib as _pl_auth, json as _json_auth

        def _read_auth_cfg() -> tuple[str, str]:
            try:
                cfg = _json_auth.loads((_pl_auth.Path.home() / ".vyrii" / "config.json").read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
            return cfg.get("auth_user", "admin"), cfg.get("auth_pass", "admin")

        from fastapi import Request as _Request
        from fastapi.responses import Response as _AuthResponse

        @app.middleware("http")
        async def _basic_auth(request: _Request, call_next):
            # Logout endpoint: always 401 + WWW-Authenticate so browser clears its credential cache
            if request.url.path == "/vyrii/auth/logout":
                return _AuthResponse(status_code=401,
                                     headers={"WWW-Authenticate": 'Basic realm="vyrii"'})
            # Static UI files are public — credentials are handled in-app via JS
            if request.method == "OPTIONS" or request.url.path.startswith("/ui") or request.url.path == "/":
                return await call_next(request)
            exp_user, exp_pass = _read_auth_cfg()
            auth_hdr = request.headers.get("Authorization", "")
            if auth_hdr.startswith("Basic "):
                try:
                    user, _, pwd = _b64.b64decode(auth_hdr[6:]).decode("utf-8", errors="replace").partition(":")
                    if user == exp_user and pwd == exp_pass:
                        return await call_next(request)
                except Exception:
                    pass
            return _AuthResponse(
                status_code=401,
                headers={},
            )

    # ── start scheduler ────────────────────────────────────────────────────────
    from . import scheduler as _sch_startup
    _sch_startup.start_scheduler()

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

    @app.get("/v1/models")
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
            h_label = h.replace("http://", "").replace("https://", "")
            bk = "openai" if w.get("provider") == "openai" else "ollama"
            mid = f"{w['model']}@{bk}://{h_label}"
            data.append({"id": mid, "object": "model", "created": 0,
                         "owned_by": "remote", "group": f"{h_label} ({bk})"})
        return {"object": "list", "data": data}

    # ── /v1/chat/completions ──────────────────────────────────────────────────

    @app.post("/v1/chat/completions")
    def chat_completions(req: ChatRequest):
        raw_model = req.model or _default_model()
        messages = [m.model_dump() for m in req.messages]
        cid = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created = int(time.time())

        m_name, m_url, m_backend = parse_model_spec(raw_model)
        use_url = m_url or base_url
        use_backend = m_backend or backend

        host_label = use_url.replace("http://", "").replace("https://", "")

        if req.stream:
            def _generate():
                for pos in _stats.wait_for_host(host_label):
                    yield f'data: {json.dumps({"waiting": True, "position": pos})}\n\n'
                rid = _stats.record_start(host_label, m_name)
                try:
                    for chunk in stream_chat(messages, m_name, use_url,
                                             backend=use_backend, timeout=timeout):
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

            return StreamingResponse(_generate(), media_type="text/event-stream")

        for _ in _stats.wait_for_host(host_label):
            pass
        rid = _stats.record_start(host_label, m_name)
        try:
            full = complete(messages, m_name, use_url, backend=use_backend, timeout=timeout)
        finally:
            _stats.record_end(rid)
            _stats.auto_release_host(host_label)
            _stats.release_host_sem(host_label)
        return {
            "id": cid, "object": "chat.completion",
            "created": created, "model": raw_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": full}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    # ── /vyrii/translate ──────────────────────────────────────────────────────

    _LANG_CODES = {
        "English": "en", "Ukrainian": "uk", "German": "de", "French": "fr",
        "Spanish": "es", "Polish": "pl", "Italian": "it", "Portuguese": "pt",
        "Chinese": "zh", "Japanese": "ja", "Arabic": "ar", "Auto": "auto",
    }

    @app.post("/vyrii/translate")
    def translate(req: TranslateRequest):
        from_code = _LANG_CODES.get(req.from_lang, req.from_lang.lower()[:2])
        to_code   = _LANG_CODES.get(req.to_lang,   req.to_lang.lower()[:2])
        text      = req.text.strip()
        mode      = req.mode.lower()

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
                return {"result": _restore("".join(out))}
            except Exception as e:
                return {"error": str(e)}

        if mode == "nllb":
            try:
                import re as _re, os as _os
                import ctranslate2, sentencepiece as _spm
                _NLLB_DIR = _os.path.join(_os.path.expanduser("~"), ".1bcoder", "nllb-200")
                _SP_PATH  = _os.path.join(_NLLB_DIR, "sentencepiece.bpe.model")
                if not _os.path.isdir(_NLLB_DIR):
                    return {"error": f"NLLB model not found at {_NLLB_DIR}"}
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
                _blocks: list = []
                def _stash(m):
                    _blocks.append(m.group(0)); return f"[CODEBLK_{len(_blocks)-1}]"
                text = _re.sub(r"```[\s\S]*?```", _stash, text)
                def _restore(s):
                    for i, b in enumerate(_blocks): s = s.replace(f"[CODEBLK_{i}]", b)
                    return s
                _ph = _re.compile(r"^\[CODEBLK_\d+\]$")
                out_lines = []
                for line in text.split("\n"):
                    s = line.strip()
                    out_lines.append(line if not s or _ph.match(s) else _chunk(s))
                return {"result": _restore("\n".join(out_lines))}
            except Exception as e:
                return {"error": str(e)}

        # llm mode
        model = req.model or _default_model()
        m_name, m_url, m_backend = parse_model_spec(model)
        model   = m_name
        use_url = m_url or base_url
        use_bk  = m_backend or backend

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
                return {"result": r.json()["choices"][0]["message"]["content"].strip()}
            except Exception as e:
                return {"error": str(e)}

        if "translategemma" in model.lower():
            # Ollama: system+user prompt format per translategemma model card
            src_label = req.from_lang if req.from_lang != "Auto" else "auto-detected"
            sys_msg = (
                f"You are a professional {src_label} ({from_code}) to {req.to_lang} ({to_code}) translator. "
                f"Your goal is to accurately convey the meaning and nuances of the original text "
                f"while adhering to {req.to_lang} grammar, vocabulary, and cultural sensitivities. "
                f"Produce only the {req.to_lang} translation, without any additional explanations or commentary."
            )
            msgs = [{"role": "system", "content": sys_msg}, {"role": "user", "content": text}]
            return {"result": complete(msgs, model, use_url, backend=use_bk, timeout=timeout)}

        from_clause = f" from {req.from_lang}" if req.from_lang != "Auto" else ""
        prompt = (
            f"Translate the following text{from_clause} to {req.to_lang}. "
            f"Output ONLY the translation — no introduction, no explanation.\n\n{text}"
        )
        return {"result": complete([{"role": "user", "content": prompt}], model, use_url, backend=use_bk, timeout=timeout)}

    # ── /vyrii/webask ─────────────────────────────────────────────────────────

    @app.post("/vyrii/webask")
    def webask(req: WebAskRequest):
        from .adapter import ChatAdapter
        from .flows import webask as _wf
        model = req.model or _default_model()
        question = req.question.strip()
        if not question:
            return {"error": "question is required"}
        m_name, m_url, m_bk = parse_model_spec(model)
        if req.url.strip():
            from .tools import fetch_text
            try:
                page = fetch_text(req.url.strip())
            except Exception as e:
                return {"error": str(e)}
            prompt = (
                f"Source: {req.url}\n\n{page}\n\n---\n\n"
                f"Question: {question}\n\nAnswer based ONLY on the content above."
            )
            return {"result": complete([{"role": "user", "content": prompt}],
                                        m_name, m_url or base_url, backend=m_bk or backend, timeout=timeout)}
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        _wf.run(adapter, f"{question} -d {req.top_n}")
        return {"result": adapter.last_reply or "No results."}

    # ── /vyrii/webcrawl ───────────────────────────────────────────────────────

    @app.post("/vyrii/webcrawl")
    def webcrawl(req: WebCrawlRequest):
        import tempfile as _tmp, os as _os2, time as _wct, pathlib as _wcp
        from .adapter import ChatAdapter
        from .flows import webcrawl as _wcf
        model   = req.model or _default_model()
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)

        _vyrii_home = _wcp.Path.home() / ".vyrii"
        _out_dir = _wcp.Path(req.path.strip()) if req.path.strip() else _vyrii_home / "crawl"
        _out_dir.mkdir(parents=True, exist_ok=True)
        _ts = _wct.strftime("%Y%m%d_%H%M%S")
        if req.mode in ("pages", "mirror"):
            out_path = str(_out_dir) if req.path.strip() else str(_out_dir / f"crawl_{_ts}")
        elif req.mode == "extract" or (req.mode == "llm" and req.format_out == "structured"):
            out_path = str(_out_dir / f"crawl_{_ts}.csv")
        else:
            out_path = str(_out_dir / f"crawl_{_ts}.txt")

        args = f'{req.url} --mode {req.mode} --depth {req.depth} -N {req.max_pages} --out {out_path}'

        if req.filter != "none":
            if req.filter == "url-prefix":
                actual_prefix = (req.url_prefix.strip() or req.url).rstrip("/")
                args += f' --filter {actual_prefix}'
            else:
                args += f' --filter {req.filter}'

        if req.task.strip():
            args += f' --task "{req.task.strip()}"'

        if req.mode == "llm":
            args += f' --format {req.format_out}'

        if req.ask:
            args += ' --ask'

        # write columns YAML to a temp file if provided
        _cols_tmp = None
        if req.columns.strip() and req.mode in ("extract", "llm"):
            _cols_tmp = _tmp.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, encoding="utf-8"
            )
            _cols_tmp.write(req.columns.strip())
            _cols_tmp.close()
            args += f' --columns {_cols_tmp.name}'

        _wcf.run(adapter, args)

        if _cols_tmp:
            try:
                _os2.unlink(_cols_tmp.name)
            except Exception:
                pass

        return {"result": adapter.last_reply or "No results."}

    # ── /vyrii/deepagent ──────────────────────────────────────────────────────

    @app.post("/vyrii/deepagent")
    def deepagent(req: DeepAgentRequest):
        from .adapter import ChatAdapter
        from .flows import deepagent_md as _df
        model = req.model or _default_model()
        task = req.task.strip()
        if not task:
            return {"error": "task is required"}
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        import pathlib as _plda
        args = f'"{task}" --maxdepth {req.sections}'
        if req.ref_url.strip():
            args += f' --ref {req.ref_url.strip()}'
        if req.use_web:
            args += f' --web {req.web_n}'
        if req.rag_project.strip():
            _vyrii_home = _plda.Path.home() / ".vyrii"
            args += f' --rag {req.rag_project.strip()} --rag-store "{_vyrii_home}"'
        _df.run(adapter, args)
        return {"result": adapter.last_reply or "No output."}

    # ── /vyrii/webanalys ──────────────────────────────────────────────────────

    @app.post("/vyrii/webanalys")
    def webanalys(req: WebAnalysRequest):
        from .adapter import ChatAdapter
        from .flows import webanalys as _waf
        model = req.model or _default_model()
        query = req.query.strip()
        if not query:
            return {"error": "query is required"}
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        _waf.run(adapter, f'"{query}" -n {req.n}')
        return {"result": adapter.last_reply or "No results."}

    # ── /vyrii/scan ───────────────────────────────────────────────────────────

    @app.post("/vyrii/scan")
    def scan_compact(req: ScanRequest):
        from .adapter import ChatAdapter
        from .flows import scan as _sc
        model = req.model or _default_model()
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        path = req.path.strip()
        if not path:
            return {"error": "path is required"}
        args = f'"{path}"'
        if req.query.strip():
            args += f' --query "{req.query.strip()}"'
        args += f' --chunk {req.chunk} --summary {req.summary} --target {req.target} --rounds {req.rounds}'
        if req.ext.strip():
            args += f' --ext {req.ext.strip()}'
        _sc.run(adapter, args)
        return {"result": adapter.last_reply or "Done."}

    # ── /vyrii/webindex ───────────────────────────────────────────────────────

    @app.post("/vyrii/webindex")
    def webindex(req: WebIndexRequest):
        from .adapter import ChatAdapter
        from .flows import webindex as _wi
        model = req.model or _default_model()
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        url = req.url.strip()
        if not url or not url.startswith("http"):
            return {"error": "valid url is required"}
        args = f'"{url}"'
        if req.project.strip():
            args += f' --project {req.project.strip()}'
        if req.path.strip():
            args += f' --path "{req.path.strip()}"'
        args += f' --depth {req.depth} --pages {req.pages}'
        _wi.run(adapter, args)
        return {"result": adapter.last_reply or "Done."}

    # ── /vyrii/interview ──────────────────────────────────────────────────────

    @app.post("/vyrii/interview")
    def interview(req: InterviewRequest):
        import re as _re, json as _json
        from .adapter import ChatAdapter
        task = (req.task or "").strip()
        if not task:
            return {"error": "task is required"}
        n = max(1, min(20, req.n))
        model = req.model or _default_model()
        ctx = f"\n\nAdditional context:\n{req.file_content[:3000]}" if req.file_content.strip() else ""
        system = (
            f"You are a requirements analyst. Generate exactly {n} clarifying "
            "questions that must be answered before implementation begins. "
            "For each question provide 2-3 concrete answer options. "
            "Output ONLY a valid JSON array — no markdown, no explanation. Format: "
            '[{"q": "Question?", "options": ["Option A", "Option B"]}, ...]'
        )
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        raw = adapter._stream_chat([
            {"role": "system", "content": system},
            {"role": "user",   "content": f"Task: {task}{ctx}"},
        ]) or ""
        raw = raw.strip()
        raw = _re.sub(r'^```[a-z]*\n?', '', raw)
        raw = _re.sub(r'\n?```\s*$', '', raw).strip()
        raw = _re.sub(r'(?<!:)//[^\n]*', '', raw)
        raw = _re.sub(r'(?<!")#[^\n"]*', '', raw)
        raw = _re.sub(r',(\s*[}\]])', r'\1', raw)
        try:
            questions = _json.loads(raw)
            if isinstance(questions, list):
                questions = [{"q": str(i.get("q", "")).strip(),
                              "options": [str(o).strip() for o in i.get("options", [])]}
                             for i in questions if isinstance(i, dict) and i.get("q")]
                return {"questions": questions}
        except Exception:
            pass
        m = _re.search(r'\[.*\]', raw, _re.DOTALL)
        if m:
            try:
                questions = _json.loads(m.group(0))
                if isinstance(questions, list):
                    return {"questions": [
                        {"q": str(i.get("q", "")).strip(),
                         "options": [str(o).strip() for o in i.get("options", [])]}
                        for i in questions if isinstance(i, dict) and i.get("q")
                    ]}
            except Exception:
                pass
        return {"error": "Failed to parse questions", "raw": raw[:500]}

    # ── /vyrii/obfuscate ──────────────────────────────────────────────────────

    import tempfile as _tmpf, os as _os3

    def _glossary_tempfile(content: str) -> str:
        """Write glossary YAML content to a temp file; return its path."""
        tmp = _tmpf.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        return tmp.name

    @app.post("/vyrii/obfuscate")
    def obfuscate(req: ObfuscateRequest):
        from .adapter import ChatAdapter
        from .flows import obfuscate as _of
        if not req.glossary.strip():
            return {"error": "glossary is required"}
        model   = req.model or _default_model()
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        adapter._last_output = req.text
        gpath   = _glossary_tempfile(req.glossary)
        args    = f'--glossary {gpath}'
        if req.force:
            args += ' --force'
        try:
            _of.run(adapter, args)
        finally:
            try: _os3.unlink(gpath)
            except Exception: pass
        return {"result": adapter.last_reply or ""}

    # ── /vyrii/deobfuscate ────────────────────────────────────────────────────

    @app.post("/vyrii/deobfuscate")
    def deobfuscate(req: DeobfuscateRequest):
        from .adapter import ChatAdapter
        from .flows import deobfuscate as _dof
        if not req.glossary.strip():
            return {"error": "glossary is required"}
        model   = req.model or _default_model()
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend, timeout=timeout)
        adapter._last_output = req.text
        gpath   = _glossary_tempfile(req.glossary)
        args    = f'--glossary {gpath}'
        if req.force:
            args += ' --force'
        try:
            _dof.run(adapter, args)
        finally:
            try: _os3.unlink(gpath)
            except Exception: pass
        return {"result": adapter.last_reply or ""}

    # ── /vyrii/themes ────────────────────────────────────────────────────────

    @app.get("/vyrii/themes")
    def list_themes():
        import pathlib as _pth
        themes_dir = _pth.Path(__file__).parent / "ui" / "themes"
        if not themes_dir.exists():
            return {"themes": ["ocean"]}
        themes = sorted(f.stem for f in themes_dir.glob("*.css") if f.is_file())
        return {"themes": themes or ["ocean"]}

    # ── /vyrii/settings ──────────────────────────────────────────────────────

    import pathlib as _pl, json as _json
    _CFG = _pl.Path.home() / ".vyrii" / "config.json"

    def _read_cfg() -> dict:
        try:
            return _json.loads(_CFG.read_text(encoding="utf-8")) if _CFG.exists() else {}
        except Exception:
            return {}

    def _write_cfg(updates: dict):
        cfg = _read_cfg()
        cfg.update({k: v for k, v in updates.items() if v is not None})
        _CFG.parent.mkdir(parents=True, exist_ok=True)
        _CFG.write_text(_json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    @app.get("/vyrii/settings")
    def settings_get():
        return _read_cfg()

    @app.post("/vyrii/settings")
    def settings_save(req: SettingsRequest):
        try:
            _write_cfg(req.model_dump(exclude_none=True))
            return {"ok": True, "config": _read_cfg()}
        except Exception as e:
            return {"error": str(e)}

    @app.get("/vyrii/stats")
    def vyrii_stats():
        return {"stats": _stats.get_stats(), "locks": _stats.get_all_locks()}

    @app.get("/vyrii/lock")
    def vyrii_lock_info():
        return {"locks": _stats.get_all_locks()}

    @app.post("/vyrii/lock")
    def vyrii_lock(req: LockRequest, raw_request: Request = None):
        ip = "127.0.0.1"
        if raw_request and raw_request.client:
            ip = raw_request.client.host
        if req.action == "release":
            _stats.release_host(req.host, ip)
            return {"ok": True}
        cfg = _read_cfg()
        mode = cfg.get("reserve_mode", "response")
        timeout = int(cfg.get("reserve_timeout", 600))
        return _stats.lock_host(req.host, ip, mode, timeout)

    @app.post("/vyrii/auth/password")
    def auth_change_password(req: AuthChangeRequest):
        if not req.username.strip() or not req.password:
            return {"error": "username and password are required"}
        _write_cfg({"auth_user": req.username.strip(), "auth_pass": req.password})
        return {"ok": True}

    # ── /vyrii/history/* ─────────────────────────────────────────────────────

    @app.get("/vyrii/history/chats")
    def hist_list_chats():
        from . import history as _h
        return [{"id": id_, "title": title, "created_at": ts}
                for id_, title, ts in _h.list_chats()]

    @app.post("/vyrii/history/chats")
    def hist_create_chat(req: HistCreateRequest):
        from . import history as _h
        cid = _h.create_chat(req.title.strip() or "New chat")
        return {"id": cid}

    @app.post("/vyrii/history/chats/{chat_id}/messages")
    def hist_add_message(chat_id: int, req: HistAddMessageRequest):
        from . import history as _h
        _h.add_message(chat_id, req.role, req.content)
        return {"ok": True}

    @app.get("/vyrii/history/chats/{chat_id}")
    def hist_get_chat(chat_id: int):
        from . import history as _h
        import sqlite3 as _sq
        with _h._conn() as c:
            row = c.execute(
                "SELECT title, created_at FROM chats WHERE id=?", (chat_id,)
            ).fetchone()
        if not row:
            return {"error": "not found"}
        return {"id": chat_id, "title": row[0], "created_at": row[1],
                "messages": _h.get_messages(chat_id)}

    @app.get("/vyrii/history/search")
    def hist_search(q: str = ""):
        from . import history as _h
        return [{"id": i, "title": t, "created_at": ts}
                for i, t, ts in _h.search_chats(q)]

    @app.delete("/vyrii/history/chats/{chat_id}")
    def hist_delete_chat(chat_id: int):
        from . import history as _h
        _h.delete_chat(chat_id)
        return {"ok": True}

    # ── /vyrii/rag/* ─────────────────────────────────────────────────────────

    _VYRII_HOME = _pl.Path.home() / ".vyrii"

    @app.get("/vyrii/rag/projects")
    def rag_projects():
        projects: set[str] = set()
        for store in [_VYRII_HOME / ".simargl", _VYRII_HOME / ".simargl_web"]:
            if store.exists():
                for d in store.iterdir():
                    if d.is_dir():
                        projects.add(d.name)
        return {"projects": sorted(projects)}

    @app.post("/vyrii/rag/search")
    def rag_search(req: RagSearchRequest):
        query   = req.query.strip()
        project = req.project.strip()
        if not query or not project:
            return {"error": "project and query are required"}
        mode = req.mode if req.mode in ("file", "task", "aggr", "refine") else "file"
        sort = req.sort if req.sort in ("rank", "freq") else "rank"
        if req.project_path.strip():
            store_dir = str(_pl.Path(req.project_path.strip()) / ".simargl")
        else:
            store_dir = str(_VYRII_HOME / ".simargl")
        try:
            from simargl.searcher import search as _sim_search
            result = _sim_search(
                query, mode=mode, sort=sort, top_n=req.top_k,
                project_id=project,
                store_dir=store_dir,
            )
        except ImportError:
            return {"error": "simargl not installed — run: pip install simargl"}
        except Exception as e:
            return {"error": str(e)}

        # Task mode — return units (historical tasks)
        if mode == "task":
            units = result.get("units", [])[:req.top_k]
            return {
                "mode": "task",
                "results": [
                    {
                        "unit_id":      u.get("unit_id", ""),
                        "text_preview": u.get("text_preview", ""),
                        "score":        u.get("similarity", 0),
                        "files":        u.get("files", []),
                    }
                    for u in units
                ],
                "context": "", "sources": [],
            }

        # Aggr mode — module-level results
        if mode == "aggr":
            modules = result.get("modules", [])[:req.top_k]
            return {
                "mode": "aggr",
                "results": [{"path": m.get("module", ""), "score": m.get("score", 0)} for m in modules],
                "context": "", "sources": [],
            }

        # File / refine mode
        files = result.get("files", [])[:req.top_k]
        if not files:
            return {"results": [], "context": "", "sources": [], "mode": mode}

        results, sources, ctx_parts = [], [], []
        for i, f in enumerate(files, 1):
            path    = _pl.Path(f.get("path", ""))
            fname   = path.name
            score   = f.get("score", 0)
            text    = ""
            for cand in [
                _VYRII_HOME / ".simargl_web" / project / fname,  # webindex source
                _VYRII_HOME / path,                               # files_index (relative path)
                _pl.Path(path),                                   # absolute path
                _VYRII_HOME / fname,                              # filename only fallback
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

        return {"results": results, "context": "\n\n".join(ctx_parts), "sources": sources}

    @app.post("/vyrii/rag/ask")
    def rag_ask(req: RagAskRequest):
        model = req.model or _default_model()
        if not req.context.strip():
            return {"error": "context is required"}
        m_name, m_url, m_bk = parse_model_spec(model)
        prompt = (
            f"Use the following sources to answer the question. "
            f"Be concise and cite sources by filename when relevant.\n\n"
            f"{req.context}\n\n---\n\nQuestion: {req.query}\n\nAnswer:"
        )
        try:
            answer = complete([{"role": "user", "content": prompt}],
                              m_name, m_url or base_url, backend=m_bk or backend,
                              num_ctx=8192, timeout=timeout * 2)
            return {"result": answer}
        except Exception as e:
            return {"error": str(e)}

    # ── /vyrii/files/* ────────────────────────────────────────────────────────

    from pathlib import Path as _FP
    import shutil as _shutil
    _FHOME = _FP.home() / ".vyrii"

    def _fsafe(rel: str) -> _FP:
        p = (_FHOME / rel.lstrip("/\\")).resolve()
        if not str(p).startswith(str(_FHOME.resolve())):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Path outside ~/.vyrii/")
        return p

    def _flist(root: _FP, depth: int = 0, max_depth: int = 6) -> dict:
        if not root.is_dir() or depth > max_depth:
            return {}
        result = {}
        for item in sorted(root.iterdir(), key=lambda x: (x.is_file(), x.name)):
            key = item.name + ("/" if item.is_dir() else "")
            result[key] = _flist(item, depth + 1, max_depth) if item.is_dir() else None
        return result

    # FileMkdirRequest / FileDeleteRequest / FileIndexRequest — defined at module level

    @app.get("/vyrii/files/read")
    def files_read(path: str = ""):
        if not path.strip():
            return {"error": "path is required"}
        try:
            p = _fsafe(path)
            if not p.is_file():
                return {"error": f"Not a file: {path}"}
            size = p.stat().st_size
            MAX  = 65536
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return {"error": f"Cannot read: {e}"}
            truncated = len(content) > MAX
            return {
                "name":      p.name,
                "path":      path,
                "size":      size,
                "content":   content[:MAX],
                "truncated": truncated,
            }
        except Exception as e:
            return {"error": str(e)}

    @app.get("/vyrii/files/list")
    def files_list(path: str = ""):
        root = _fsafe(path) if path.strip() else _FHOME
        if not root.is_dir():
            return {"error": f"Not a directory: {path}"}
        return {"path": str(root), "tree": _flist(root)}

    @app.post("/vyrii/files/mkdir")
    def files_mkdir(req: FileMkdirRequest):
        try:
            _fsafe(req.path).mkdir(parents=True, exist_ok=True)
            return {"ok": True, "path": req.path}
        except Exception as e:
            return {"error": str(e)}

    @app.post("/vyrii/files/upload")
    async def files_upload(dest: str = "",
                           files: list[UploadFile] = _File(default=[])):
        saved: list[str] = []
        errors: list[dict] = []
        for uf in files:
            try:
                rel = (dest.rstrip("/\\") + "/" + uf.filename) if dest.strip() else uf.filename
                target = _fsafe(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                content = await uf.read()
                target.write_bytes(content)
                saved.append(str(target.relative_to(_FHOME)))
            except Exception as e:
                errors.append({"file": getattr(uf, "filename", "?"), "error": str(e)})
        if errors and not saved:
            return {"error": errors}
        return {"ok": True, "saved": saved, **({"errors": errors} if errors else {})}

    @app.delete("/vyrii/files")
    def files_delete(req: FileDeleteRequest):
        try:
            p = _fsafe(req.path)
            if p.is_dir():
                _shutil.rmtree(p)
            elif p.is_file():
                p.unlink()
            else:
                return {"error": f"Not found: {req.path}"}
            return {"ok": True}
        except Exception as e:
            return {"error": str(e)}

    @app.post("/vyrii/files/index")
    def files_index(req: FileIndexRequest):
        import subprocess
        try:
            target = _fsafe(req.path)
            if not target.is_dir():
                return {"error": f"Not a directory: {req.path}"}
            project = target.name
            rel     = str(target.relative_to(_FHOME))
            result  = subprocess.run(
                ["simargl", "index", "files", rel,
                 "--project", project, "--store", ".simargl"],
                capture_output=True, text=True, timeout=300,
                cwd=str(_FHOME),
            )
            if result.returncode == 0:
                return {"ok": True, "project": project}
            return {"error": result.stderr[:500]}
        except Exception as e:
            return {"error": str(e)}

    # ── /vyrii/team/* ────────────────────────────────────────────────────────

    from . import parallel as _par
    _par.init(_VYRII_HOME)

    @app.get("/vyrii/team/profiles")
    def team_profiles():
        return {"profiles": _par.load_profiles()}

    @app.get("/vyrii/team/profile/{name}")
    def team_profile_get(name: str):
        p = _par.get_profile(name)
        if p is None:
            from fastapi import HTTPException
            raise HTTPException(404, f"Profile '{name}' not found")
        return p

    @app.post("/vyrii/team/profile")
    def team_profile_save(req: TeamProfileRequest):
        _par.upsert_profile(req.model_dump())
        return {"ok": True}

    @app.delete("/vyrii/team/profile/{name}")
    def team_profile_delete(name: str):
        _par.delete_profile(name)
        return {"ok": True}

    @app.post("/vyrii/team/run")
    def team_run(req: TeamRunRequest):
        import queue as _q, threading as _t

        profile = _par.get_profile(req.profile_name)
        if profile is None:
            def _e():
                yield f'data: {json.dumps({"type": "error", "text": f"Profile not found: {req.profile_name}"})}\n\n'
            return StreamingResponse(_e(), media_type="text/event-stream")

        workers = profile.get("workers", [])
        if not workers:
            def _e():
                yield f'data: {json.dumps({"type": "error", "text": "Profile has no workers"})}\n\n'
            return StreamingResponse(_e(), media_type="text/event-stream")

        model = req.model or _default_model()
        q = _q.Queue()

        def _run():
            try:
                results = _par.run_parallel(
                    workers, req.aspects, req.query,
                    req.messages, req.num_ctx, req.timeout,
                    lambda msg: q.put({"type": "progress", "text": msg}),
                )
                if req.combine == "compact":
                    final = _par.compact_results(
                        req.query, results, model,
                        base_url, backend, req.num_ctx, req.timeout,
                    )
                else:
                    final = _par.join_results(req.query, results)
                q.put({"type": "done", "text": final})
            except Exception as e:
                q.put({"type": "error", "text": str(e)})

        _t.Thread(target=_run, daemon=True).start()

        def _gen():
            while True:
                item = q.get()
                yield f"data: {json.dumps(item)}\n\n"
                if item["type"] in ("done", "error"):
                    break

        return StreamingResponse(_gen(), media_type="text/event-stream")

    # ── /vyrii/system/* ───────────────────────────────────────────────────────

    import platform as _platform, threading as _threading, os as _os4
    import pathlib as _plsys

    _ROOT = _plsys.Path(__file__).parent.parent   # C:\Project\vyrii\

    @app.post("/vyrii/system/restart")
    def system_restart():
        def _do():
            import time, subprocess as _sp
            time.sleep(1.5)
            devnull = open(_os4.devnull, "w")
            if _platform.system() == "Windows":
                script = str(_ROOT / "vyrii_auto_api.bat")
                DETACHED  = 0x00000008
                NEW_GROUP = 0x00000200
                _sp.Popen(script, creationflags=DETACHED | NEW_GROUP,
                          close_fds=True, stdin=_sp.DEVNULL,
                          stdout=devnull, stderr=devnull, shell=True)
            else:
                script = str(_ROOT / "vyrii_auto_api.sh")
                _sp.Popen(["bash", script], start_new_session=True,
                          stdin=_sp.DEVNULL, stdout=devnull, stderr=devnull)
            _os4._exit(0)
        _threading.Thread(target=_do, daemon=False).start()
        return {"ok": True, "message": "Restarting vyrii… reload the page in a few seconds."}

    def _ollama_env() -> dict:
        cfg = _read_cfg()
        env = _os4.environ.copy()
        kv = cfg.get("ollama_kv_cache", "").strip()
        if kv: env["OLLAMA_KV_CACHE_TYPE"] = kv
        if cfg.get("ollama_flash_attention"): env["OLLAMA_FLASH_ATTENTION"] = "1"
        ka = cfg.get("ollama_keep_alive", "").strip()
        if ka: env["OLLAMA_KEEP_ALIVE"] = ka
        ml = str(cfg.get("ollama_max_loaded_models", "")).strip()
        if ml: env["OLLAMA_MAX_LOADED_MODELS"] = ml
        oh = cfg.get("ollama_host", "").strip()
        if oh: env["OLLAMA_HOST"] = oh
        return env

    def _ollama_persist_windows(env: dict) -> None:
        import winreg as _wr
        _VARS = ["OLLAMA_KV_CACHE_TYPE", "OLLAMA_FLASH_ATTENTION",
                 "OLLAMA_KEEP_ALIVE", "OLLAMA_MAX_LOADED_MODELS", "OLLAMA_HOST"]
        key = _wr.OpenKey(_wr.HKEY_CURRENT_USER, "Environment", 0, _wr.KEY_SET_VALUE)
        for name in _VARS:
            val = env.get(name, "")
            if val:
                _wr.SetValueEx(key, name, 0, _wr.REG_SZ, val)
            else:
                try: _wr.DeleteValue(key, name)
                except FileNotFoundError: pass
        _wr.CloseKey(key)

    @app.post("/vyrii/system/ollama-restart")
    def ollama_restart():
        import subprocess as _sp, time as _t, socket as _sock
        env = _ollama_env()
        if _platform.system() == "Windows":
            try: _ollama_persist_windows(env)
            except Exception: pass
            _sp.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
            for _ in range(20):
                _t.sleep(0.5)
                try:
                    s = _sock.socket(); s.bind(("127.0.0.1", 11434)); s.close(); break
                except OSError:
                    _sp.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
            log_dir = _os4.path.join(_os4.environ.get("LOCALAPPDATA", ""), "Ollama")
            _os4.makedirs(log_dir, exist_ok=True)
            log_fh = open(_os4.path.join(log_dir, "server.log"), "a", encoding="utf-8")
            _sp.Popen(["ollama", "serve"], env=env,
                      start_new_session=True, stdout=log_fh, stderr=log_fh)
        else:
            _sp.run(["pkill", "-x", "ollama"], capture_output=True)
            _t.sleep(1)
            _sp.Popen(["ollama", "serve"], env=env,
                      start_new_session=True,
                      stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        return {"ok": True}

    @app.post("/vyrii/system/reboot")
    def system_reboot(req: SystemConfirmRequest):
        if not req.confirmed:
            return {"error": "Set confirmed=true to proceed."}
        import subprocess as _sp
        if _platform.system() == "Windows":
            _sp.Popen(["shutdown", "/r", "/t", "10"])
            return {"ok": True, "message": "Windows reboot in 10 s."}
        _sp.Popen(["systemctl", "reboot"])
        return {"ok": True, "message": "System reboot initiated."}

    @app.post("/vyrii/system/shutdown")
    def system_shutdown(req: SystemConfirmRequest):
        if not req.confirmed:
            return {"error": "Set confirmed=true to proceed."}
        import subprocess as _sp
        if _platform.system() == "Windows":
            _sp.Popen(["shutdown", "/s", "/t", "10"])
            return {"ok": True, "message": "Windows shutdown in 10 s."}
        _sp.Popen(["systemctl", "poweroff"])
        return {"ok": True, "message": "System shutdown initiated."}

    # ── /vyrii/projects — project registry ──────────────────────────────────
    import pathlib as _proj_pl
    _PROJECTS_FILE = _proj_pl.Path.home() / ".vyrii" / "projects.json"

    def _load_projects() -> list:
        try:
            if _PROJECTS_FILE.exists():
                return json.loads(_PROJECTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return []

    def _save_projects(projects: list):
        _PROJECTS_FILE.parent.mkdir(exist_ok=True)
        _PROJECTS_FILE.write_text(json.dumps(projects, indent=2, ensure_ascii=False), encoding="utf-8")

    def _resolve_project_cwd(project_name: str) -> str | None:
        for p in _load_projects():
            if p.get("name") == project_name:
                return p.get("path", "")
        return None

    @app.get("/vyrii/projects")
    def projects_list():
        return {"projects": _load_projects()}

    @app.post("/vyrii/projects")
    def projects_add(req: ProjectRequest):
        if not req.name.strip():
            return {"error": "name is required"}
        projects = _load_projects()
        projects = [p for p in projects if p.get("name") != req.name]
        projects.append({"name": req.name.strip(), "path": req.path.strip(), "description": req.description})
        _save_projects(projects)
        return {"ok": True, "projects": projects}

    @app.delete("/vyrii/projects/{name}")
    def projects_delete(name: str):
        projects = [p for p in _load_projects() if p.get("name") != name]
        _save_projects(projects)
        return {"ok": True, "projects": projects}

    # ── /vyrii/run — generic CLI runner ──────────────────────────────────────
    @app.post("/vyrii/run")
    def run_command(req: RunRequest):
        import subprocess as _run_sp
        import pathlib as _run_pl
        import time as _run_time

        cwd = req.cwd.strip() or None
        if req.project.strip():
            resolved = _resolve_project_cwd(req.project.strip())
            if resolved is None:
                return {"error": f"Project not found: {req.project}"}
            cwd = resolved

        if cwd and not _run_pl.Path(cwd).is_dir():
            return {"error": f"Directory not found: {cwd}"}

        t0 = _run_time.time()
        try:
            result = _run_sp.run(
                req.command, shell=True, cwd=cwd or None,
                capture_output=True, text=True, timeout=600,
                encoding="utf-8", errors="replace",
            )
            duration = round(_run_time.time() - t0, 2)
            return {
                "stdout":      result.stdout,
                "stderr":      result.stderr,
                "returncode":  result.returncode,
                "duration_s":  duration,
                "cwd":         cwd or "",
            }
        except _run_sp.TimeoutExpired:
            return {"error": "Command timed out (600s)", "stdout": "", "stderr": "", "returncode": -1}
        except Exception as e:
            return {"error": str(e), "stdout": "", "stderr": "", "returncode": -1}

    # ── /vyrii/scheduler — REST wrapper around scheduler.py ──────────────────
    from . import scheduler as _sch_api

    @app.get("/vyrii/scheduler/tasks")
    def sch_tasks_list():
        return {"tasks": _sch_api.load_tasks()}

    @app.post("/vyrii/scheduler/tasks")
    def sch_tasks_create(req: SchTaskRequest):
        if not req.name.strip() or not req.command.strip():
            return {"error": "name and command are required"}
        task = _sch_api.add_task(
            name=req.name.strip(), command=req.command.strip(),
            schedule_type=req.schedule_type, hour=req.hour,
            minute=req.minute, day_of_week=req.day_of_week,
            interval_value=req.interval_value,
        )
        return {"ok": True, "task": task}

    @app.delete("/vyrii/scheduler/tasks/{task_id}")
    def sch_tasks_delete(task_id: str):
        _sch_api.remove_task(task_id)
        return {"ok": True}

    @app.post("/vyrii/scheduler/tasks/{task_id}/toggle")
    def sch_tasks_toggle(task_id: str):
        enabled = _sch_api.toggle_task(task_id)
        return {"ok": True, "enabled": enabled}

    @app.post("/vyrii/scheduler/tasks/{task_id}/run")
    def sch_tasks_run_now(task_id: str):
        _sch_api.run_now(task_id)
        return {"ok": True}

    @app.get("/vyrii/scheduler/tasks/{task_id}/logs")
    def sch_task_logs(task_id: str):
        import pathlib as _sch_pl
        logs = _sch_api.get_task_logs(task_id)
        return {"logs": [{"filename": f.name, "size": f.stat().st_size,
                          "mtime": f.stat().st_mtime} for f in logs]}

    @app.get("/vyrii/scheduler/log")
    def sch_log_read(task_id: str = "", filename: str = ""):
        if not filename.strip():
            return {"error": "filename is required"}
        import pathlib as _sch_pl2
        log_dir = _sch_pl2.Path.home() / ".vyrii" / "scheduler_logs"
        p = (log_dir / filename).resolve()
        if not str(p).startswith(str(log_dir)):
            return {"error": "path outside log dir"}
        if not p.is_file():
            return {"error": "log not found"}
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"content": content, "filename": filename}

    # ── /vyrii/prompts — prompt library ──────────────────────────────────
    import pathlib as _prm_pl

    def _prm_path() -> "_prm_pl.Path":
        return _prm_pl.Path.home() / ".vyrii" / "prompts.json"

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
        _prm_path().write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    @app.get("/vyrii/prompts")
    def prompts_list():
        return {"prompts": _load_prompts()}

    @app.post("/vyrii/prompts")
    def prompts_save(req: PromptItem):
        if not req.name.strip() or not req.prompt.strip():
            return {"error": "name and prompt are required"}
        items = _load_prompts()
        pid = req.id.strip() or uuid.uuid4().hex[:12]
        items = [p for p in items if p.get("id") != pid]
        items.append({
            "id": pid, "name": req.name.strip(), "prompt": req.prompt,
            "description": req.description, "model": req.model.strip(), "area": req.area.strip(),
        })
        _save_prompts(items)
        return {"ok": True, "id": pid}

    @app.delete("/vyrii/prompts/{prompt_id}")
    def prompts_delete(prompt_id: str):
        items = [p for p in _load_prompts() if p.get("id") != prompt_id]
        _save_prompts(items)
        return {"ok": True}

    # ── static UI (served last so API routes take priority) ───────────────
    import os as _os
    _ui_dir = _os.path.join(_os.path.dirname(__file__), "ui")
    if _os.path.isdir(_ui_dir):
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import RedirectResponse

        @app.get("/")
        def _ui_root():
            return RedirectResponse("/ui/")

        app.mount("/ui", StaticFiles(directory=_ui_dir, html=True), name="ui")

    return app
