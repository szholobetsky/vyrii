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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .engine import (
    BACKEND_OLLAMA, DEFAULT_OLLAMA,
    list_models, stream_chat, complete,
)


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
    url: str
    task: str = ""
    max_pages: int = 5
    model: str = ""

class DeepAgentRequest(BaseModel):
    task: str
    ref_url: str = ""
    sections: int = 3
    model: str = ""

class WebAnalysRequest(BaseModel):
    query: str
    n: int = 5
    model: str = ""


def create_app(base_url: str = DEFAULT_OLLAMA, backend: str = BACKEND_OLLAMA) -> FastAPI:
    app = FastAPI(title="vyrii API", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _default_model() -> str:
        models = list_models(base_url, backend)
        return models[0] if models else ""

    # ── /v1/models ────────────────────────────────────────────────────────────

    @app.get("/v1/models")
    def get_models():
        models = list_models(base_url, backend)
        return {
            "object": "list",
            "data": [
                {"id": m, "object": "model", "created": 0, "owned_by": "local"}
                for m in models
            ],
        }

    # ── /v1/chat/completions ──────────────────────────────────────────────────

    @app.post("/v1/chat/completions")
    def chat_completions(req: ChatRequest):
        model = req.model or _default_model()
        messages = [m.model_dump() for m in req.messages]
        cid = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created = int(time.time())

        if req.stream:
            def _generate():
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

            return StreamingResponse(_generate(), media_type="text/event-stream")

        full = complete(messages, model, base_url, backend=backend)
        return {
            "id": cid, "object": "chat.completion",
            "created": created, "model": model,
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
                return {"result": r.json()["choices"][0]["message"]["content"].strip()}
            except Exception as e:
                return {"error": str(e)}
        from_clause = f" from {req.from_lang}" if req.from_lang != "Auto" else ""
        prompt = (
            f"Translate the following text{from_clause} to {req.to_lang}. "
            f"Output ONLY the translation — no introduction, no explanation.\n\n{text}"
        )
        return {"result": complete([{"role": "user", "content": prompt}], model, base_url, backend=backend)}

    # ── /vyrii/webask ─────────────────────────────────────────────────────────

    @app.post("/vyrii/webask")
    def webask(req: WebAskRequest):
        from .adapter import ChatAdapter
        from .flows import webask as _wf
        model = req.model or _default_model()
        question = req.question.strip()
        if not question:
            return {"error": "question is required"}
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
                                        model, base_url, backend=backend)}
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend)
        _wf.run(adapter, f"{question} -d {req.top_n}")
        return {"result": adapter.last_reply or "No results."}

    # ── /vyrii/webcrawl ───────────────────────────────────────────────────────

    @app.post("/vyrii/webcrawl")
    def webcrawl(req: WebCrawlRequest):
        from .adapter import ChatAdapter
        from .flows import webcrawl as _wcf
        model = req.model or _default_model()
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend)
        task_str = req.task.strip() or "Summarize the main content."
        _wcf.run(adapter, f'{req.url} --mode llm --task "{task_str}" -N {req.max_pages}')
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
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend)
        args = f'"{task}" --maxdepth {req.sections}'
        if req.ref_url.strip():
            args += f' --ref {req.ref_url.strip()}'
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
        adapter = ChatAdapter(model=model, base_url=base_url, backend=backend)
        _waf.run(adapter, f'"{query}" -n {req.n}')
        return {"result": adapter.last_reply or "No results."}

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

    class FileMkdirRequest(BaseModel):
        path: str

    class FileDeleteRequest(BaseModel):
        path: str

    class FileIndexRequest(BaseModel):
        path: str

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
    async def files_upload(dest: str = "", files=None):
        from fastapi import UploadFile, File
        return {"error": "Use multipart/form-data with files field"}

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

    return app
