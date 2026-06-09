"""Gradio web UI for vyrii.

Launch:
  vyrii --web --port 4896

Tabs:
  Chat        — streaming chat + RAG from simargl index
  Translate   — two-pane LLM translation
  WebAsk      — fetch a page, answer a question about it
  WebCrawl    — BFS crawl (combine/pages/extract/mirror/llm modes)
  WebIndex    — crawl + simargl index for RAG
  DeepAgent   — structured markdown document (--web / --rag)
  Files       — file manager, viewer, downloader, simargl index
  Scheduler   — cron-like scheduler with APScheduler + log viewer
"""
from __future__ import annotations

from datetime import datetime

_DEFAULT_OLLAMA = "http://localhost:11434"
_DEFAULT_OPENAI = "http://localhost:8080"

_BACKEND_OLLAMA = "Ollama"
_BACKEND_OPENAI = "OpenAI-compatible"

def _backend_key(label: str) -> str:
    """Map UI label → engine constant."""
    from .engine import BACKEND_OPENAI, BACKEND_OLLAMA
    return BACKEND_OPENAI if label == _BACKEND_OPENAI else BACKEND_OLLAMA


# ── vyrii home directory ───────────────────────────────────────────────────────

import os as _os
import pathlib as _pathlib

VYRII_HOME = _pathlib.Path.home() / ".vyrii"
VYRII_HOME.mkdir(exist_ok=True)
(VYRII_HOME / "crawl").mkdir(exist_ok=True)
(VYRII_HOME / "files").mkdir(exist_ok=True)
_os.chdir(str(VYRII_HOME))   # deepagent_md uses getcwd() for plan dirs

from . import parallel as _parallel_mod
_parallel_mod.init(VYRII_HOME)
from . import i18n as _i18n_mod


# ── file tree helpers (used by Files tab) ─────────────────────────────────────

def _resolve_safe(rel: str) -> _pathlib.Path:
    p = (VYRII_HOME / rel.lstrip("/\\")).resolve()
    if not str(p).startswith(str(VYRII_HOME.resolve())):
        raise ValueError("Path outside VYRII_HOME")
    return p


def _tree_text(root: _pathlib.Path, prefix: str = "", depth: int = 0,
               max_depth: int = 4) -> str:
    if depth > max_depth or not root.is_dir():
        return ""
    parts = []
    try:
        items = sorted(root.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except PermissionError:
        return ""
    for i, item in enumerate(items):
        connector = "└── " if i == len(items) - 1 else "├── "
        parts.append(f"{prefix}{connector}{item.name}{'/' if item.is_dir() else ''}")
        if item.is_dir():
            ext = "    " if i == len(items) - 1 else "│   "
            child = _tree_text(item, prefix + ext, depth + 1, max_depth)
            if child:
                parts.append(child)
    return "\n".join(parts)





def _list_rag_projects() -> list:
    p = VYRII_HOME / ".simargl_web"
    if not p.exists():
        return []
    return sorted(d.name for d in p.iterdir() if d.is_dir())


def _rag_search(query: str, project: str, top_k: int = 3) -> tuple[list, str]:
    """Query simargl index. Returns (source_filenames, formatted_context_text)."""
    try:
        from simargl.searcher import search as _sim_search
        result = _sim_search(
            query, mode="file", top_n=top_k,
            project_id=project,
            store_dir=str(VYRII_HOME / ".simargl"),
        )
        parts: list = []
        sources: list = []
        for f in result.get("files", [])[:top_k]:
            fname = _pathlib.Path(f.get("path", "")).name
            score = f.get("score", 0)
            for candidate in [
                VYRII_HOME / ".simargl_web" / project / fname,
                _pathlib.Path(f.get("path", "")),
            ]:
                if candidate.is_file():
                    text = candidate.read_text(encoding="utf-8", errors="replace")[:3000]
                    parts.append(f"[{fname}  score:{score:.2f}]\n{text}")
                    sources.append(fname)
                    break
        return sources, "\n\n".join(parts)
    except Exception as e:
        return [], f"[RAG error: {e}]"


def _rag_tab_search(project: str, query: str, top_k: int):
    """Search simargl index; yield (out_md, llm_md, ask_btn, full_ctx, src_only, hidden_ctx_reset)."""
    import gradio as _gr
    query = query.strip()
    if not query or not (project or "").strip():
        yield "_Provide a project and a query._", "", _gr.update(visible=False), "", "", ""
        return
    yield "_Searching..._", "", _gr.update(visible=False), "", "", ""
    try:
        from simargl.searcher import search as _sim_search
        result = _sim_search(query, mode="file", top_n=int(top_k),
                             project_id=project,
                             store_dir=str(VYRII_HOME / ".simargl"))
        files = result.get("files", [])[:int(top_k)]
    except Exception as e:
        yield f"[RAG error: {e}]", "", _gr.update(visible=False), "", "", ""
        return
    if not files:
        yield "_No results found._", "", _gr.update(visible=False), "", "", ""
        return

    out_lines, src_lines, full_lines = [], [], []
    src_lines.append(f"**Query:** {query}\n\n**Sources:**")
    full_lines.append(f"**Query:** {query}\n")
    for i, f in enumerate(files, 1):
        fname = _pathlib.Path(f.get("path", "")).name
        score = f.get("score", 0)
        text = ""
        for cand in [VYRII_HOME / ".simargl_web" / project / fname,
                     _pathlib.Path(f.get("path", ""))]:
            if cand.is_file():
                text = cand.read_text(encoding="utf-8", errors="replace")[:3000]
                break
        snippet = text[:200].replace("\n", " ")
        out_lines.append(f"**{i}. {fname}** (score: {score:.2f})\n```\n{text[:1500]}\n```")
        src_lines.append(f"{i}. `{fname}` ({score:.2f})\n   _{snippet}_")
        full_lines.append(f"[Source {i}: {fname}  score:{score:.2f}]\n{text}")

    yield ("\n\n".join(out_lines), "",
           _gr.update(visible=True),
           "\n\n".join(full_lines),
           "\n".join(src_lines),
           "")


def _rag_tab_ask(query: str, rag_full: str, model: str, url: str,
                 backend_label: str, timeout):
    """Synthesise LLM answer from already-retrieved chunks in rag_full_ctx."""
    if not (rag_full or "").strip():
        yield "_Run a search first._", rag_full or ""
        return
    yield "_Thinking..._", rag_full
    from .engine import complete as _cmp
    answer = _cmp(
        [{"role": "user",
          "content": f"{rag_full}\n\n---\n\nQuestion: {query}\n\nAnswer based on the sources above."}],
        model, url, backend=_backend_key(backend_label), timeout=int(timeout),
    )
    updated = rag_full + f"\n\n---\n\n**LLM Answer:**\n\n{answer}"
    yield answer, updated


def _add_to_chat(content, sources, is_new, mode, n_tokens, display_mode,
                 messages, cid, ctx, hidden_ctx,
                 model, url, backend_label, timeout):
    """Handler for the 'Add to chat' panel confirm button."""
    import gradio as _gr
    from . import history as _hist_atc
    from .engine import complete as _complete_atc, smart_ctx as _sctx_atc
    content = (content or "").strip()
    if not content:
        return (messages, cid, ctx, f"ctx: {ctx}", hidden_ctx,
                _gr.update(), _gr.update(visible=False))

    if mode == "summary":
        processed = _complete_atc(
            [{"role": "user",
              "content": f"Summarize this concisely in 2-3 paragraphs:\n\n{content}"}],
            model, url, backend=_backend_key(backend_label), timeout=int(timeout),
        )
    elif mode == "last_n":
        chars = int(n_tokens) * 3
        processed = content[-chars:]
    else:
        processed = content

    if is_new or not cid:
        cid = _hist_atc.create_chat(_hist_atc.auto_title(processed))
        messages = []

    # full content always goes to LLM via hidden_ctx
    new_hidden = processed

    if display_mode == "transparent":
        visible_content = "_[Context loaded silently — not shown]_"
    elif display_mode == "sources" and (sources or "").strip():
        visible_content = (sources or "").strip()
    else:
        visible_content = processed

    ctx_msg = {"role": "user",      "content": visible_content}
    ack_msg = {"role": "assistant", "content": "Context received. What would you like to know?"}
    messages = list(messages) + [ctx_msg, ack_msg]
    _hist_atc.add_message(cid, "user",      visible_content)
    _hist_atc.add_message(cid, "assistant", ack_msg["content"])

    new_ctx = _sctx_atc(messages, ctx)
    new_hist = _gr.Dropdown(choices=_chat_choices(_hist_atc.list_chats()))
    return (messages, cid, new_ctx, f"ctx: {new_ctx}", new_hidden,
            new_hist, _gr.update(visible=False))


# ── config helpers ────────────────────────────────────────────────────────────

def _load_config() -> dict:
    p = VYRII_HOME / "config.json"
    if p.is_file():
        try:
            import json as _json
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_config(updates: dict):
    import json as _json
    cfg = _load_config()
    cfg.update(updates)
    (VYRII_HOME / "config.json").write_text(
        _json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


_THEME_MAP = {
    "Monochrome": None,   # resolved lazily inside build_app to avoid importing gradio at module level
    "Soft":       None,
    "Glass":      None,
    "Ocean":      None,
    "Default":    None,
    "Base":       None,
    "Citrus":     None,
    "GithubDark": None,
    "Dracula":    None,
    "Solarized":  None,
}
_THEME_NAMES = list(_THEME_MAP)


def _dark_theme(primary_hue: str, bg: str, bg2: str, border: str,
                text: str, text_sub: str, input_bg: str, btn2_bg: str) -> "gr.themes.Base":
    """Build a dark Gradio theme by forcing both light and dark CSS variables to the same dark values."""
    import gradio as _gr
    from gradio.themes.utils import colors as _gc
    # c50 of primary_hue is used as chatbot user bubble background — override it to dark
    _base_color = getattr(_gc, primary_hue.lower(), _gc.blue)
    _primary = _gc.Color(
        c50=btn2_bg, c100=_base_color.c100, c200=_base_color.c200,
        c300=_base_color.c300, c400=_base_color.c400, c500=_base_color.c500,
        c600=_base_color.c600, c700=_base_color.c700, c800=_base_color.c800,
        c900=_base_color.c900, c950=_base_color.c950,
        name=f"dark-{primary_hue}",
    )
    return _gr.themes.Base(primary_hue=_primary, neutral_hue="slate").set(
        body_background_fill=bg,               body_background_fill_dark=bg,
        background_fill_primary=bg,            background_fill_primary_dark=bg,
        background_fill_secondary=bg2,         background_fill_secondary_dark=bg2,
        block_background_fill=bg2,             block_background_fill_dark=bg2,
        block_label_background_fill=bg2,       block_label_background_fill_dark=bg2,
        block_title_background_fill=bg2,
        panel_background_fill=bg2,             panel_background_fill_dark=bg2,
        block_border_color=border,             block_border_color_dark=border,
        block_label_border_color=border,       block_label_border_color_dark=border,
        border_color_primary=border,           border_color_primary_dark=border,
        body_text_color=text,                  body_text_color_dark=text,
        body_text_color_subdued=text_sub,      body_text_color_subdued_dark=text_sub,
        block_label_text_color=text_sub,       block_label_text_color_dark=text_sub,
        block_title_text_color=text,           block_title_text_color_dark=text,
        block_info_text_color=text_sub,        block_info_text_color_dark=text_sub,
        input_background_fill=bg,             input_background_fill_dark=bg,
        input_background_fill_hover=bg2,      input_background_fill_hover_dark=bg2,
        input_border_color=border,             input_border_color_dark=border,
        input_border_color_hover=text_sub,     input_border_color_hover_dark=text_sub,
        input_placeholder_color=text_sub,      input_placeholder_color_dark=text_sub,
        code_background_fill=bg,               code_background_fill_dark=bg,
        button_secondary_background_fill=btn2_bg,      button_secondary_background_fill_dark=btn2_bg,
        button_secondary_background_fill_hover=border,  button_secondary_background_fill_hover_dark=border,
        button_secondary_text_color=text,      button_secondary_text_color_dark=text,
        button_secondary_border_color=border,  button_secondary_border_color_dark=border,
        button_cancel_background_fill=bg2,     button_cancel_background_fill_dark=bg2,
        table_even_background_fill=bg,         table_even_background_fill_dark=bg,
        table_odd_background_fill=bg2,         table_odd_background_fill_dark=bg2,
        table_border_color=border,             table_border_color_dark=border,
        table_text_color=text,                 table_text_color_dark=text,
        checkbox_background_color=bg2,         checkbox_background_color_dark=bg2,
        checkbox_border_color=border,          checkbox_border_color_dark=border,
        checkbox_label_background_fill=bg2,    checkbox_label_background_fill_dark=bg2,
        checkbox_label_text_color=text,        checkbox_label_text_color_dark=text,
        accordion_text_color=text,             accordion_text_color_dark=text,
        error_background_fill=bg2,             error_background_fill_dark=bg2,
        stat_background_fill=bg2,              stat_background_fill_dark=bg2,
    )

# ── MCP config helpers ─────────────────────────────────────────────────────────

_MCP_CONFIG_FILE = VYRII_HOME / "mcp_servers.json"
_mcp_clients: dict = {}       # name → MCPClient (live connections)
_mcp_tools_cache: dict = {}   # name → {tool_name: tool_dict}


def _mcp_load_configs() -> list:
    if _MCP_CONFIG_FILE.exists():
        try:
            import json as _j
            return _j.loads(_MCP_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _mcp_saved_names() -> list:
    return [c["name"] for c in _mcp_load_configs()]


def _mcp_save_config(name: str, command: str, cwd: str):
    import json as _j
    configs = _mcp_load_configs()
    for c in configs:
        if c["name"] == name:
            c["command"] = command
            c["cwd"] = cwd
            break
    else:
        configs.append({"name": name, "command": command, "cwd": cwd})
    _MCP_CONFIG_FILE.write_text(
        _j.dumps(configs, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _mcp_delete_config(name: str):
    import json as _j
    configs = [c for c in _mcp_load_configs() if c["name"] != name]
    _MCP_CONFIG_FILE.write_text(
        _j.dumps(configs, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _mcp_coerce(val: str, schema: dict):
    t = schema.get("type", "string")
    try:
        if t == "integer":
            return int(val)
        if t == "number":
            return float(val)
        if t == "boolean":
            return val.strip().lower() in ("true", "1", "yes")
    except Exception:
        pass
    return val


# ── system control ─────────────────────────────────────────────────────────────

def _sys_restart(delay: int = 8) -> str:
    import threading, subprocess, os, platform
    import pathlib as _pl
    _ROOT = _pl.Path(__file__).parent.parent
    def _do():
        import time; time.sleep(1.5)
        devnull = open(os.devnull, "w")
        if platform.system() == "Windows":
            script = str(_ROOT / "vyrii_auto.bat")
            DETACHED = 0x00000008
            NEW_GROUP = 0x00000200
            subprocess.Popen(script, creationflags=DETACHED | NEW_GROUP,
                             close_fds=True, stdin=subprocess.DEVNULL,
                             stdout=devnull, stderr=devnull,
                             shell=True)
        else:
            script = str(_ROOT / "vyrii_auto.sh")
            subprocess.Popen(["bash", script], start_new_session=True,
                             stdin=subprocess.DEVNULL,
                             stdout=devnull, stderr=devnull)
        os._exit(0)
    threading.Thread(target=_do, daemon=False).start()
    return f"Restarting vyrii… page will reload automatically in {int(delay)} s."


def _sys_reboot(confirmed: bool) -> str:
    if not confirmed:
        return "Check the confirmation box first."
    import platform, subprocess as _sp
    if platform.system() == "Windows":
        _sp.Popen(["shutdown", "/r", "/t", "10"])
        return "Windows reboot scheduled in 10 s."
    else:
        _sp.Popen(["systemctl", "reboot"])
        return "System reboot initiated."


def _sys_shutdown(confirmed: bool) -> str:
    if not confirmed:
        return "Check the confirmation box first."
    import platform, subprocess as _sp
    if platform.system() == "Windows":
        _sp.Popen(["shutdown", "/s", "/t", "10"])
        return "Windows shutdown scheduled in 10 s."
    else:
        _sp.Popen(["systemctl", "poweroff"])
        return "System shutdown initiated."


# ── history helpers ────────────────────────────────────────────────────────────

def _chat_choices(chats: list) -> list[str]:
    result = []
    for cid, title, ts in chats:
        dt = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        result.append(f"{cid}| {title[:40]}  [{dt}]")
    return result


def _parse_id(choice: str | None) -> int | None:
    if not choice:
        return None
    try:
        return int(choice.split("|")[0].strip())
    except (ValueError, AttributeError):
        return None


# ── app ────────────────────────────────────────────────────────────────────────

_HEAD_HTML = """
<script>
(function () {
  var ADDED = 'data-vyrii-save';
  var ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>';

  function save(text) {
    var blob = new Blob([text], { type: 'text/markdown' });
    if (window.showSaveFilePicker) {
      window.showSaveFilePicker({
        suggestedName: 'response.md',
        types: [{ description: 'Markdown', accept: { 'text/markdown': ['.md'] } }]
      }).then(function(fh) {
        return fh.createWritable();
      }).then(function(w) {
        return w.write(blob).then(function() { return w.close(); });
      }).catch(function() {});
    } else {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'response.md';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(function() { URL.revokeObjectURL(a.href); }, 1000);
    }
  }

  function inject() {
    var rows = document.querySelectorAll('.message.bot, .message-row .bot');
    rows.forEach(function(row) {
      if (row.getAttribute(ADDED)) return;
      var btnBox = row.querySelector('.message-buttons, .copy-btn-container, .options, [class*="actions"]');
      if (!btnBox) return;
      row.setAttribute(ADDED, '1');

      var btn = document.createElement('button');
      btn.innerHTML = ICON;
      btn.title = 'Save as .md';
      btn.style.cssText = 'background:none;border:none;cursor:pointer;padding:2px 4px;opacity:0.6;color:inherit;vertical-align:middle;';
      btn.onmouseover = function() { btn.style.opacity = '1'; };
      btn.onmouseout  = function() { btn.style.opacity = '0.6'; };
      btn.onclick = function() {
        var el = row.querySelector('.prose, .md, [class*="chat"], [class*="content"], [class*="message"]');
        save(el ? (el.innerText || '') : '');
      };
      btnBox.appendChild(btn);
    });
  }

  var obs = new MutationObserver(inject);
  obs.observe(document.body, { childList: true, subtree: true });
  setTimeout(inject, 1500);
</script>
"""


def _stream_flow(run_fn, lines: list):
    """Run run_fn in a background thread, capture its stdout, yield progress.
    Captured lines are also written into the `lines` list for the caller to inspect."""
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
            lines.append(text)
            yield '```\n' + '\n'.join(lines[-60:]) + '\n```'


_JS_SCROLL_TO_PANEL = (
    "() => { setTimeout(() => {"
    " const el = document.getElementById('add_ctx_panel');"
    " if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});"
    " }, 100); }"
)
_JS_SWITCH_TO_CHAT = (
    "() => { setTimeout(() => {"
    " const btns = document.querySelectorAll('[role=\"tab\"]');"
    " for (const b of btns) { if (b.textContent.trim() === 'Chat') { b.click(); break; } }"
    " window.scrollTo({top: 0, behavior: 'smooth'});"
    " }, 200); }"
)
_JS_SWITCH_TO_SCAN = (
    "() => { setTimeout(() => {"
    " const btns = document.querySelectorAll('[role=\"tab\"]');"
    " for (const b of btns) {"
    "  const txt = b.textContent.trim();"
    "  if (txt === 'Scan' || txt === 'Скан') { b.click(); break; }"
    " }"
    " }, 200); }"
)
_JS_SCROLL_TO_INDEX = (
    "() => { setTimeout(() => {"
    " const el = document.getElementById('fi_index_section');"
    " if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});"
    " }, 150); }"
)
_JS_SCROLL_TO_VIEW = (
    "() => { setTimeout(() => {"
    " const el = document.getElementById('fi_view_section');"
    " if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});"
    " }, 150); }"
)


def build_app(ollama_url: str = _DEFAULT_OLLAMA, openai_url: str = _DEFAULT_OPENAI,
              lang: str = "en", startup_model: str | None = None):
    try:
        import gradio as gr
        t = _i18n_mod.get(lang)
    except ImportError:
        raise ImportError("Gradio not installed.  pip install gradio")

    from .engine import list_models, stream_chat, smart_ctx, complete, CTX_START
    from . import history as _hist

    _cfg_now        = _load_config()
    _theme_name     = _cfg_now.get("theme", "Monochrome")
    _saved_url      = _cfg_now.get("saved_url", ollama_url)
    _saved_model    = _cfg_now.get("saved_model", None)
    _saved_backend  = _cfg_now.get("saved_backend", _BACKEND_OLLAMA)
    _restart_delay  = int(_cfg_now.get("restart_delay", 8))

    _models = list_models(_saved_url, _backend_key(_saved_backend)) or ["gemma3:1b", "qwen3:1.7b"]
    _default_model = _models[0]
    _live_themes  = {
        "Monochrome": gr.themes.Monochrome(),
        "Soft":       gr.themes.Soft(),
        "Glass":      gr.themes.Glass(),
        "Ocean":      gr.themes.Ocean(),
        "Default":    gr.themes.Default(),
        "Base":       gr.themes.Base(),
        "Citrus":     gr.themes.Citrus(),
        "GithubDark": _dark_theme(
            primary_hue="blue",
            bg="#0d1117", bg2="#161b22", border="#30363d",
            text="#c9d1d9", text_sub="#8b949e",
            input_bg="#0d1117", btn2_bg="#21262d",
        ),
        "Dracula": _dark_theme(
            primary_hue="purple",
            bg="#282a36", bg2="#1e1f29", border="#44475a",
            text="#f8f8f2", text_sub="#6272a4",
            input_bg="#282a36", btn2_bg="#44475a",
        ),
        "Solarized": _dark_theme(
            primary_hue="cyan",
            bg="#002b36", bg2="#073642", border="#586e75",
            text="#839496", text_sub="#657b83",
            input_bg="#002b36", btn2_bg="#073642",
        ),
    }
    _active_theme = _live_themes.get(_theme_name, gr.themes.Monochrome())

    with gr.Blocks(title="Vyrii") as app:
        gr.Markdown(t["app_title"])

        # ── shared settings bar ────────────────────────────────────────────────
        g_saved_timeout = gr.BrowserState(180)

        with gr.Row():
            g_backend = gr.Radio(
                choices=[_BACKEND_OLLAMA, _BACKEND_OPENAI],
                value=_saved_backend,
                label=t["backend_label"],
                scale=2,
            )
            g_url    = gr.Textbox(
                value=_saved_url,
                label=t["url_label"],
                scale=3,
            )
            _cli_or_saved = startup_model or _saved_model
            _init_model = _cli_or_saved if _cli_or_saved in _models else _default_model
            g_model  = gr.Dropdown(choices=_models, value=_init_model,
                                   label=t["model_label"], scale=3)
            with gr.Column(scale=1, min_width=160):
                g_refresh  = gr.Button(t["refresh_models_btn"], size="sm")
                g_thinking = gr.Checkbox(label=t["show_thinking_label"], value=False)

        def _do_refresh_models(url: str, backend_label: str):
            mods = list_models(url, _backend_key(backend_label)) or ["gemma3:1b"]
            return gr.Dropdown(choices=mods, value=mods[0])

        def _on_backend_change(backend_label: str, current_url: str):
            if backend_label == _BACKEND_OPENAI and current_url == ollama_url:
                new_url = openai_url
            elif backend_label == _BACKEND_OLLAMA and current_url == openai_url:
                new_url = ollama_url
            else:
                new_url = current_url
            mods = list_models(new_url, _backend_key(backend_label)) or ["gemma3:1b"]
            return gr.Textbox(value=new_url), gr.Dropdown(choices=mods, value=mods[0])

        g_backend.change(
            _on_backend_change,
            inputs=[g_backend, g_url],
            outputs=[g_url, g_model],
        )
        g_refresh.click(_do_refresh_models, inputs=[g_url, g_backend], outputs=[g_model])


        # defined here so Chat/other tabs can reference it before Settings tab
        s_timeout = gr.Number(
            value=180, minimum=10, maximum=7200, precision=0,
            label="Request timeout (seconds)",
            info="Increase for slow/large models. 180 = default, 600 = 10 min, 3600 = 1 hour.",
            scale=1,
            render=False,
        )

        # ── "Add to chat" context panel (before tabs, visible on demand) ─────
        ctx_buffer  = gr.State("")   # full content (always sent to LLM)
        ctx_sources = gr.State("")   # sources-only text (WebAsk URLs; empty for other tabs)
        s_hidden_ctx = gr.State("")  # hidden LLM context not shown in chatbot

        with gr.Group(visible=False, elem_id="add_ctx_panel") as add_ctx_panel:
            with gr.Row():
                atc_is_new  = gr.Checkbox(label=t["settings_new_chat_label"], value=False,
                                          scale=1, min_width=90)
                atc_display = gr.Radio(
                    choices=list(zip(t["atc_display_choices"],
                                     ["transparent", "sources", "text"])),
                    value="text", label=t["atc_display_label"], scale=2,
                )
                atc_mode    = gr.Radio(
                    choices=list(zip(t["atc_content_choices"],
                                     ["whole", "summary", "last_n"])),
                    value="whole", label=t["atc_content_label"], scale=2,
                )
                atc_n       = gr.Number(value=1000, label=t["atc_n_label"],
                                        minimum=100, precision=0,
                                        visible=False, scale=1, min_width=80)
                atc_cancel  = gr.Button(t["cancel_btn"], scale=1, min_width=80)
                atc_add     = gr.Button(t["add_confirm_btn"], variant="primary",
                                        scale=2, min_width=120)

        atc_mode.change(
            lambda m: gr.update(visible=m == "last_n"),
            inputs=[atc_mode], outputs=[atc_n],
        )

        # ── tabs ──────────────────────────────────────────────────────────────
        with gr.Tabs():

            # ══════════════════════════════════════════════════════════════════
            # Chat
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["chat_tab"]) as tab_chat:
                with gr.Row():
                    # sidebar
                    with gr.Column(scale=1, min_width=220):
                        gr.Markdown(t["conversations_header"])
                        new_btn  = gr.Button(t["new_chat_btn"], variant="primary", size="sm")
                        export_btn = gr.DownloadButton(
                            t["export_chat_btn"], size="sm",
                        )
                        hist_dd  = gr.Dropdown(
                            choices=_chat_choices(_hist.list_chats()),
                            label=t["saved_chats_label"],
                            allow_custom_value=False,
                            interactive=True,
                        )
                        with gr.Row():
                            load_btn = gr.Button(t["load_btn"], size="sm", scale=1)
                            del_btn  = gr.Button(t["delete_btn"], variant="stop",
                                                 size="sm", scale=1)
                        refr_btn = gr.Button(t["refresh_list_btn"], size="sm")
                        with gr.Row():
                            hist_search_in  = gr.Textbox(
                                placeholder=t["hist_search_placeholder"],
                                show_label=False, container=False,
                                scale=4, lines=1,
                            )
                            hist_search_btn = gr.Button(t["search_btn"], size="sm", scale=1)
                        ctx_lbl  = gr.Textbox(value="ctx: 2048", label="Context",
                                              interactive=False)

                    # main area
                    with gr.Column(scale=4):
                        chatbot = gr.Chatbot(height=460, label="", buttons=["copy"])
                        with gr.Row():
                            msg_in = gr.Textbox(
                                placeholder=t["msg_in_placeholder"],
                                scale=5, show_label=False, container=False,
                                lines=2,
                            )
                            send_btn = gr.Button(t["send_btn"], variant="primary",
                                                 scale=1, min_width=80)
                            stop_btn = gr.Button(t["stop_btn"], variant="stop",
                                                 scale=1, min_width=80)
                        with gr.Row():
                            save_last_btn = gr.DownloadButton(
                                t["save_last_btn"], variant="secondary", size="sm", scale=1,
                            )
                            compact_btn = gr.Button(
                                t["compact_btn"], variant="secondary", size="sm", scale=1,
                            )
                            load_text_btn = gr.UploadButton(
                                t["load_text_btn"], size="sm", scale=1,
                                file_types=[".txt", ".md", ".py", ".java", ".rb",
                                            ".js", ".ts", ".go", ".rs", ".cpp",
                                            ".c", ".h", ".json", ".yaml", ".yml",
                                            ".toml", ".sh", ".bat", ".sql", ".xml",
                                            ".html", ".css", ".kt", ".swift"],
                            )

                # state: chatbot holds the full messages list;
                # s_cid and s_ctx are extras
                s_cid = gr.State(None)      # current chat_id or None
                s_ctx = gr.State(CTX_START) # current context window size

                # ── handlers ──────────────────────────────────────────────────

                # ── file export helpers ───────────────────────────────────────

                def _save_last_response(messages):
                    import tempfile, os
                    if not messages:
                        return None
                    for m in reversed(messages):
                        if m.get("role") == "assistant" and m.get("content"):
                            content = m["content"]
                            break
                    else:
                        return None
                    tmp = tempfile.NamedTemporaryFile(
                        mode="w", suffix=".md", delete=False, encoding="utf-8"
                    )
                    tmp.write(content)
                    tmp.close()
                    return tmp.name

                def _export_chat(messages):
                    import tempfile
                    if not messages:
                        return None
                    tmp = tempfile.NamedTemporaryFile(
                        mode="w", suffix=".txt", delete=False, encoding="utf-8"
                    )
                    for m in messages:
                        role = m.get("role", "")
                        content = m.get("content", "")
                        tmp.write(f"=== {role} ===\n{content}\n\n")
                    tmp.close()
                    return tmp.name

                def _compact_chat(messages, ctx, model, url, backend_label, timeout):
                    if not messages:
                        return messages, ctx, f"ctx: {ctx}"
                    history_text = "\n\n".join(
                        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m.get('content', '')}"
                        for m in messages if m.get("content")
                    )
                    summary = complete(
                        [{"role": "user", "content":
                          f"Summarize this conversation concisely, preserving all key "
                          f"information, decisions, and context:\n\n{history_text}"}],
                        model, url, backend=_backend_key(backend_label),
                        timeout=int(timeout),
                    )
                    new_messages = [
                        {"role": "user",
                         "content": f"[Compacted conversation summary]\n\n{summary}"},
                        {"role": "assistant",
                         "content": t["compacted_context"]},
                    ]
                    new_ctx = max(512, int(ctx) // 4)
                    return new_messages, new_ctx, f"ctx: {new_ctx}"

                def _load_text_file(file_obj, messages):
                    if file_obj is None:
                        return messages
                    import pathlib
                    p = pathlib.Path(file_obj if isinstance(file_obj, str) else file_obj.name)
                    try:
                        content = p.read_text(encoding="utf-8", errors="replace")
                    except Exception as e:
                        return list(messages) + [
                            {"role": "user", "content": t["load_error"].format(e=e)},
                        ]
                    fname, n = p.name, len(content)
                    if n > 1000:
                        display = (f"<details><summary>📄 {fname} ({n:,} chars)</summary>"
                                   f"\n\n```\n{content}\n```\n</details>")
                    else:
                        display = f"📄 **{fname}**\n```\n{content}\n```"
                    return list(messages) + [
                        {"role": "user",      "content": display},
                        {"role": "assistant", "content": t["loaded_file"].format(fname=fname, n=n)},
                    ]

                save_last_btn.click(
                    _save_last_response, inputs=[chatbot], outputs=[save_last_btn]
                )
                export_btn.click(
                    _export_chat, inputs=[chatbot], outputs=[export_btn]
                )
                compact_btn.click(
                    _compact_chat,
                    inputs=[chatbot, s_ctx, g_model, g_url, g_backend, s_timeout],
                    outputs=[chatbot, s_ctx, ctx_lbl],
                )
                load_text_btn.upload(
                    _load_text_file,
                    inputs=[load_text_btn, chatbot],
                    outputs=[chatbot],
                )

                # ─────────────────────────────────────────────────────────────

                def _refresh_hist():
                    return gr.Dropdown(
                        choices=_chat_choices(_hist.list_chats()), value=None
                    )

                def _new_chat():
                    return (
                        [],          # chatbot
                        None,        # s_cid
                        2048,        # s_ctx
                        "ctx: 2048", # ctx_lbl
                        gr.Dropdown(choices=_chat_choices(_hist.list_chats()), value=None),
                    )

                def _load_chat(choice):
                    cid = _parse_id(choice)
                    if cid is None:
                        return [], None, CTX_START, f"ctx: {CTX_START}"
                    msgs = _hist.get_messages(cid)
                    ctx = smart_ctx(msgs)
                    return msgs, cid, ctx, f"ctx: {ctx}"

                def _delete_chat(choice):
                    cid = _parse_id(choice)
                    if cid is not None:
                        _hist.delete_chat(cid)
                    return gr.Dropdown(
                        choices=_chat_choices(_hist.list_chats()), value=None
                    )

                def _fmt(text: str, show: bool) -> str:
                    import re
                    think_re = re.compile(r'<think>(.*?)</think>', re.DOTALL)
                    open_re  = re.compile(r'<think>(.*?)$', re.DOTALL)
                    if not show:
                        text = think_re.sub('', text)
                        text = open_re.sub('', text)
                        return text.strip()
                    def _wrap(m):
                        lines = m.group(1).strip().splitlines()
                        quoted = '\n'.join(f'> {l}' for l in lines)
                        return f'\n> 💭 **Thinking**\n{quoted}\n\n'
                    # closed blocks
                    text = think_re.sub(_wrap, text)
                    # unclosed block (still streaming) — show as active thinking
                    def _wrap_open(m):
                        lines = m.group(1).strip().splitlines()
                        quoted = '\n'.join(f'> {l}' for l in lines)
                        return f'\n> 💭 **Thinking...**\n{quoted}\n'
                    text = open_re.sub(_wrap_open, text)
                    return text.strip()

                import threading as _threading
                _stop_gen = _threading.Event()

                def _do_stop():
                    _stop_gen.set()

                def _send(user_msg, messages, cid, ctx, hidden_ctx, model, url, backend_label,
                          show_thinking, timeout):
                    _stop_gen.clear()
                    user_msg = user_msg.strip()
                    if not user_msg:
                        yield messages, cid, ctx, f"ctx: {ctx}", ""
                        return

                    # ── hidden context (from Add-to-chat transparent/show-sources) ──
                    base_msgs = list(messages)
                    if (hidden_ctx or "").strip():
                        base_msgs = base_msgs + [{
                            "role": "user",
                            "content": f"[Background context — use to answer the question below]\n\n{hidden_ctx}",
                        }, {"role": "assistant", "content": "Understood."}]

                    # show user message + thinking indicator immediately
                    messages = list(messages) + [{"role": "user", "content": user_msg}]
                    new_ctx = smart_ctx(messages, ctx)
                    display = messages + [{"role": "assistant", "content": "..."}]
                    yield display, cid, new_ctx, f"ctx: {new_ctx}", ""

                    rag_display = ""
                    send_msgs = base_msgs + [{"role": "user", "content": user_msg}]

                    if cid is None:
                        cid = _hist.create_chat(_hist.auto_title(user_msg))
                    _hist.add_message(cid, "user", user_msg)

                    import threading as _thr, queue as _q
                    _chunk_q = _q.Queue()
                    def _gen():
                        try:
                            for _c in stream_chat(send_msgs, model, url, new_ctx,
                                                  _backend_key(backend_label),
                                                  thinking=show_thinking,
                                                  timeout=int(timeout)):
                                _chunk_q.put(("chunk", _c))
                        except Exception as _e:
                            _chunk_q.put(("error", str(_e)))
                        _chunk_q.put(("done", None))
                    _thr.Thread(target=_gen, daemon=True).start()

                    _spin = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
                    _si, full = 0, ""
                    while True:
                        if _stop_gen.is_set():
                            break
                        try:
                            kind, val = _chunk_q.get(timeout=0.25)
                        except _q.Empty:
                            display = messages + [{"role": "assistant",
                                                   "content": f"{_spin[_si % 10]} _Thinking..._"}]
                            _si += 1
                            yield display, cid, new_ctx, f"ctx: {new_ctx}", ""
                            continue
                        if kind == "done":
                            break
                        if kind == "error":
                            display = messages + [{"role": "assistant", "content": f"Error: {val}"}]
                            yield display, cid, new_ctx, f"ctx: {new_ctx}", ""
                            return
                        full += val
                        display = messages + [{"role": "assistant",
                                               "content": _fmt(full, show_thinking)}]
                        yield display, cid, new_ctx, f"ctx: {new_ctx}", ""

                    if full:
                        messages = messages + [{"role": "assistant", "content": full}]
                        _hist.add_message(cid, "assistant", full)
                    display = messages[:]
                    display[-1] = {"role": "assistant",
                                   "content": _fmt(full, show_thinking) + rag_display}
                    yield display, cid, new_ctx, f"ctx: {new_ctx}", ""

                _send_out = [chatbot, s_cid, s_ctx, ctx_lbl, msg_in]
                _send_in  = [msg_in, chatbot, s_cid, s_ctx, s_hidden_ctx,
                              g_model, g_url, g_backend,
                              g_thinking, s_timeout]

                send_btn.click(_send,   inputs=_send_in, outputs=_send_out)
                msg_in.submit(_send,    inputs=_send_in, outputs=_send_out)
                stop_btn.click(_do_stop, outputs=[])

                new_btn.click(
                    _new_chat,
                    outputs=[chatbot, s_cid, s_ctx, ctx_lbl, hist_dd],
                )
                refr_btn.click(_refresh_hist, outputs=[hist_dd])
                load_btn.click(
                    _load_chat,
                    inputs=[hist_dd],
                    outputs=[chatbot, s_cid, s_ctx, ctx_lbl],
                )
                del_btn.click(_delete_chat, inputs=[hist_dd], outputs=[hist_dd])

                def _search_hist(query):
                    results = _hist.search_chats(query)
                    return gr.update(choices=_chat_choices(results))

                hist_search_btn.click(
                    _search_hist, inputs=[hist_search_in], outputs=[hist_dd]
                )
                hist_search_in.submit(
                    _search_hist, inputs=[hist_search_in], outputs=[hist_dd]
                )

            # ══════════════════════════════════════════════════════════════════
            # Translate
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["translate_tab"]):
                _LANGS = [
                    "English", "Ukrainian", "German", "French", "Spanish",
                    "Polish", "Italian", "Portuguese", "Chinese", "Japanese", "Arabic",
                ]
                _LANG_CODES = {
                    "English": "en", "Ukrainian": "uk", "German": "de",
                    "French": "fr", "Spanish": "es", "Polish": "pl",
                    "Italian": "it", "Portuguese": "pt", "Chinese": "zh",
                    "Japanese": "ja", "Arabic": "ar", "Auto": "auto",
                }

                with gr.Row():
                    tr_src_lang = gr.Dropdown(choices=["Auto"] + _LANGS, value="Auto",
                                              label=t["tr_from_label"], scale=2)
                    tr_swap_btn = gr.Button("⇄", size="sm", scale=1, min_width=40)
                    tr_tgt_lang = gr.Dropdown(choices=_LANGS, value="Ukrainian",
                                              label=t["tr_to_label"], scale=2)
                    tr_mode = gr.Radio(
                        choices=["LLM", "Argos", "NLLB"],
                        value="LLM",
                        label=t["tr_mode_label"],
                        scale=2,
                    )

                with gr.Row():
                    tr_src = gr.Textbox(label=t["tr_src_label"], lines=12, scale=1)
                    tr_tgt = gr.Textbox(label=t["tr_tgt_label"], lines=12, scale=1,
                                        interactive=False)

                with gr.Row():
                    tr_btn      = gr.Button(t["translate_btn"], variant="primary", scale=2)
                    tr_add_btn  = gr.Button(t["add_to_chat_btn"], scale=1)
                    tr_copy_btn = gr.Button(t["copy_btn"], scale=1)

                def _translate_argos(text: str, from_code: str, to_code: str) -> str:
                    import re as _re
                    import argostranslate.translate as _at
                    _blocks: list = []
                    def _stash(m):
                        _blocks.append(m.group(0))
                        return f"[CODEBLK_{len(_blocks)-1}]"
                    text = _re.sub(r"```[\s\S]*?```", _stash, text)
                    def _restore(s):
                        for i, b in enumerate(_blocks): s = s.replace(f"[CODEBLK_{i}]", b)
                        return s
                    _ph_re = _re.compile(r"(\[CODEBLK_\d+\])")
                    parts = _ph_re.split(text)
                    out_parts = []
                    for part in parts:
                        if _ph_re.fullmatch(part):
                            out_parts.append(part)
                        elif part.strip():
                            out_parts.append(_at.translate(part, from_code, to_code))
                        else:
                            out_parts.append(part)
                    return _restore("".join(out_parts))

                def _translate_nllb(text: str, from_code: str, to_code: str) -> str:
                    import re as _re, os as _os
                    import ctranslate2, sentencepiece as _spm
                    _NLLB_DIR = _os.path.join(_os.path.expanduser("~"), ".1bcoder", "nllb-200")
                    _SP_PATH  = _os.path.join(_NLLB_DIR, "sentencepiece.bpe.model")
                    if not _os.path.isdir(_NLLB_DIR):
                        return f"**Error:** NLLB model not found at `{_NLLB_DIR}`\n\nInstall: `pip install ctranslate2 sentencepiece` then download the model."
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
                    translator = ctranslate2.Translator(_NLLB_DIR, device="cpu")
                    def _chunk(line):
                        toks = sp.encode(line, out_type=str)
                        src  = [src_f] + toks + ["</s>"]
                        res  = translator.translate_batch(
                            [src], target_prefix=[[tgt_f]],
                            max_decoding_length=512, repetition_penalty=1.3, beam_size=4,
                        )
                        return sp.decode(res[0].hypotheses[0][1:])
                    _blocks: list = []
                    def _stash(m):
                        _blocks.append(m.group(0))
                        return f"[CODEBLK_{len(_blocks)-1}]"
                    text = _re.sub(r"```[\s\S]*?```", _stash, text)
                    def _restore(s):
                        for i, b in enumerate(_blocks): s = s.replace(f"[CODEBLK_{i}]", b)
                        return s
                    _ph_re = _re.compile(r"^\[CODEBLK_\d+\]$")
                    out_lines = []
                    for line in text.split("\n"):
                        stripped = line.strip()
                        if not stripped or _ph_re.match(stripped):
                            out_lines.append(line)
                        else:
                            out_lines.append(_chunk(stripped))
                    return _restore("\n".join(out_lines))

                def _translate(src, src_lang, tgt_lang, mode, model, url, backend_label, timeout):
                    if not src.strip():
                        yield ""
                        return
                    from_code = _LANG_CODES.get(src_lang, src_lang.lower()[:2])
                    to_code   = _LANG_CODES.get(tgt_lang, tgt_lang.lower()[:2])
                    if mode == "Argos":
                        try:
                            yield _translate_argos(src.strip(), from_code, to_code)
                        except Exception as e:
                            yield f"**Error (Argos):** {e}"
                        return
                    if mode == "NLLB":
                        try:
                            yield _translate_nllb(src.strip(), from_code, to_code)
                        except Exception as e:
                            yield f"**Error (NLLB):** {e}"
                        return
                    # LLM mode
                    if "translategemma" in model.lower():
                        import requests as _req
                        payload = {
                            "model": model,
                            "messages": [{"role": "user", "content": [
                                {"type": "text", "source_lang_code": from_code,
                                 "target_lang_code": to_code, "text": src.strip()}
                            ]}],
                            "stream": False, "temperature": 0.1,
                        }
                        try:
                            r = _req.post(
                                f"{url}/v1/chat/completions", json=payload,
                                headers={"Authorization": "Bearer lm-studio"},
                                timeout=int(timeout),
                            )
                            r.raise_for_status()
                            yield r.json()["choices"][0]["message"]["content"].strip()
                        except Exception as e:
                            yield f"**Error:** {e}"
                        return
                    if src_lang != "Auto":
                        prompt = (
                            f"You are a professional {src_lang} ({from_code}) to {tgt_lang} ({to_code}) translator. "
                            f"Your goal is to accurately convey the meaning and nuances of the original {src_lang} text "
                            f"while adhering to {tgt_lang} grammar, vocabulary, and cultural sensitivities.\n"
                            f"Produce only the {tgt_lang} translation, without any additional explanations or commentary. "
                            f"Please translate the following {src_lang} text into {tgt_lang}:\n\n\n{src.strip()}"
                        )
                    else:
                        prompt = (
                            f"You are a professional translator to {tgt_lang} ({to_code}). "
                            f"Your goal is to accurately convey the meaning and nuances of the original text "
                            f"while adhering to {tgt_lang} grammar, vocabulary, and cultural sensitivities.\n"
                            f"Produce only the {tgt_lang} translation, without any additional explanations or commentary. "
                            f"Please translate the following text into {tgt_lang}:\n\n\n{src.strip()}"
                        )
                    full = ""
                    for chunk in stream_chat(
                        [{"role": "user", "content": prompt}], model, url,
                        backend=_backend_key(backend_label), timeout=int(timeout),
                    ):
                        full += chunk
                        yield full

                _tr_inputs = [tr_src, tr_src_lang, tr_tgt_lang, tr_mode, g_model, g_url, g_backend, s_timeout]
                tr_btn.click(_translate, inputs=_tr_inputs, outputs=[tr_tgt])
                tr_src.submit(_translate, inputs=_tr_inputs, outputs=[tr_tgt])
                tr_add_btn.click(lambda c: (c, gr.update(visible=True)), inputs=[tr_tgt], outputs=[ctx_buffer, add_ctx_panel], js=_JS_SCROLL_TO_PANEL)
                tr_copy_btn.click(None, inputs=[tr_tgt], outputs=[], js="async (text) => { await navigator.clipboard.writeText(text || ''); }")
                tr_swap_btn.click(
                    lambda s, t: (
                        gr.update(value=t if t in (["Auto"] + _LANGS) else "Auto"),
                        gr.update(value=s if s in _LANGS else _LANGS[0]),
                    ),
                    inputs=[tr_src_lang, tr_tgt_lang],
                    outputs=[tr_src_lang, tr_tgt_lang],
                )

            # ══════════════════════════════════════════════════════════════════
            # Obfuscate / Deobfuscate
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["obfuscate_tab"]):
                gr.Markdown(t["obfuscate_desc"])
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown(t["obf_glossary_header"])
                        obf_glossary = gr.Textbox(
                            value="# real term: code word\nCompany Name: ACME Corp\nJohn Smith: User1\n",
                            label="",
                            lines=14,
                            show_label=False,
                        )
                        with gr.Row():
                            obf_load = gr.UploadButton(
                                t["obf_load_btn"], file_types=[".yaml", ".yml"], size="sm"
                            )
                            obf_save = gr.DownloadButton(
                                t["obf_save_btn"], size="sm"
                            )
                        obf_llm = gr.Checkbox(
                            label=t["obf_llm_label"],
                            value=True,
                        )
                    with gr.Column(scale=2):
                        with gr.Row():
                            obf_src = gr.Textbox(label=t["obf_src_label"], lines=12, scale=1)
                            obf_dst = gr.Textbox(label=t["obf_dst_label"],  lines=12, scale=1,
                                                  interactive=False)
                        with gr.Row():
                            obf_btn      = gr.Button(t["obfuscate_btn"],   variant="primary", scale=1)
                            deobf_btn    = gr.Button(t["deobfuscate_btn"], scale=1)
                            obf_add_btn  = gr.Button(t["add_to_chat_btn"], scale=1)
                            obf_copy_btn = gr.Button(t["copy_btn"],        scale=1)

                def _parse_yaml_glossary(text: str) -> dict:
                    result = {}
                    for line in text.splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if ":" in line:
                            k, _, v = line.partition(":")
                            k = k.strip().strip("\"'")
                            v = v.strip().strip("\"'")
                            if k and v:
                                result[k] = v
                    return result

                def _load_obf_glossary(file):
                    if file is None:
                        return gr.update()
                    try:
                        path = file if isinstance(file, str) else file.name
                        with open(path, "r", encoding="utf-8") as f:
                            return f.read()
                    except Exception as e:
                        return t["error_loading"].format(e=e)

                def _obfuscate(text, glossary_yaml, use_llm, model, url, backend_label):
                    if not text.strip():
                        return ""
                    glossary = _parse_yaml_glossary(glossary_yaml)
                    if not glossary:
                        return t["no_glossary"]
                    from .flows.obfuscate import _force_replace, _obfuscate_prompt
                    if not use_llm:
                        return _force_replace(text, glossary)
                    from .adapter import ChatAdapter
                    adapter = ChatAdapter(model=model, base_url=url,
                                          backend=_backend_key(backend_label))
                    prompt = _obfuscate_prompt(text, glossary)
                    return adapter._stream_chat([
                        {"role": "system", "content": "You are a precise text rewriter. Follow glossary instructions exactly."},
                        {"role": "user",   "content": prompt},
                    ]) or ""

                def _deobfuscate(text, glossary_yaml, use_llm, model, url, backend_label):
                    if not text.strip():
                        return ""
                    glossary = _parse_yaml_glossary(glossary_yaml)
                    if not glossary:
                        return t["no_glossary"]
                    from .flows.deobfuscate import _force_replace, _deobfuscate_prompt
                    if not use_llm:
                        rev = {v: k for k, v in glossary.items()}
                        return _force_replace(text, rev)
                    from .adapter import ChatAdapter
                    adapter = ChatAdapter(model=model, base_url=url,
                                          backend=_backend_key(backend_label))
                    prompt = _deobfuscate_prompt(text, glossary)
                    return adapter._stream_chat([
                        {"role": "system", "content": "You are a precise text rewriter. Follow glossary instructions exactly."},
                        {"role": "user",   "content": prompt},
                    ]) or ""

                def _save_obf_glossary(text):
                    import tempfile
                    tmp = tempfile.NamedTemporaryFile(
                        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
                    )
                    tmp.write(text)
                    tmp.close()
                    return tmp.name

                obf_load.upload(_load_obf_glossary, inputs=[obf_load], outputs=[obf_glossary])
                obf_save.click(_save_obf_glossary, inputs=[obf_glossary], outputs=[obf_save])
                obf_btn.click(
                    _obfuscate,
                    inputs=[obf_src, obf_glossary, obf_llm, g_model, g_url, g_backend],
                    outputs=[obf_dst],
                )
                deobf_btn.click(
                    _deobfuscate,
                    inputs=[obf_dst, obf_glossary, obf_llm, g_model, g_url, g_backend],
                    outputs=[obf_src],
                )
                obf_add_btn.click(lambda c: (c, gr.update(visible=True)), inputs=[obf_dst], outputs=[ctx_buffer, add_ctx_panel], js=_JS_SCROLL_TO_PANEL)
                obf_copy_btn.click(None, inputs=[obf_dst], outputs=[], js="async (text) => { await navigator.clipboard.writeText(text || ''); }")

            # ══════════════════════════════════════════════════════════════════
            # RAG
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["rag_tab"]) as tab_rag:
                gr.Markdown(t["rag_desc"])
                with gr.Row():
                    rag_tab_project = gr.Dropdown(
                        choices=[""] + _list_rag_projects(), value="",
                        label=t["rag_project_label"], scale=3, allow_custom_value=True,
                    )
                    rag_tab_refresh = gr.Button(t["rag_reload_btn"], size="sm", scale=1)
                rag_tab_query = gr.Textbox(label=t["rag_query_label"], lines=2,
                                           placeholder=t["rag_query_placeholder"])
                rag_tab_topk = gr.Slider(1, 10, value=3, step=1, label=t["rag_top_k_label"])
                with gr.Row():
                    rag_tab_search_btn = gr.Button(t["rag_search_btn"], variant="primary", scale=2)
                    rag_tab_add_btn    = gr.Button(t["add_to_chat_btn"], scale=1)
                    rag_tab_copy_btn   = gr.Button(t["copy_btn"], scale=1)

                rag_out     = gr.Markdown(label=t["rag_results_label"])
                rag_ask_btn = gr.Button(t["rag_ask_llm_btn"], variant="secondary", visible=False)
                rag_llm_out = gr.Markdown(label=t["rag_llm_answer_label"])

                rag_full_ctx = gr.State("")
                rag_src_only = gr.State("")

                rag_tab_search_btn.click(
                    _rag_tab_search,
                    inputs=[rag_tab_project, rag_tab_query, rag_tab_topk],
                    outputs=[rag_out, rag_llm_out, rag_ask_btn,
                             rag_full_ctx, rag_src_only, s_hidden_ctx],
                )
                rag_ask_btn.click(
                    _rag_tab_ask,
                    inputs=[rag_tab_query, rag_full_ctx,
                            g_model, g_url, g_backend, s_timeout],
                    outputs=[rag_llm_out, rag_full_ctx],
                )
                rag_tab_add_btn.click(
                    lambda full, src: (full, src, gr.update(visible=True)),
                    inputs=[rag_full_ctx, rag_src_only],
                    outputs=[ctx_buffer, ctx_sources, add_ctx_panel],
                    js=_JS_SCROLL_TO_PANEL,
                )
                rag_tab_copy_btn.click(None, inputs=[rag_out], outputs=[], js="async (text) => { await navigator.clipboard.writeText(text || ''); }")
                rag_tab_refresh.click(
                    lambda: gr.update(choices=[""] + _list_rag_projects()),
                    outputs=[rag_tab_project],
                )
                tab_rag.select(
                    lambda: gr.update(choices=[""] + _list_rag_projects()),
                    outputs=[rag_tab_project],
                )

            # MCP
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["mcp_tab"]):
                gr.Markdown(t["mcp_desc"])

                # ── Connect section ───────────────────────────────────────────
                gr.Markdown(t["mcp_connect_section"])
                with gr.Row():
                    mcp_saved_dd  = gr.Dropdown(
                        choices=_mcp_saved_names(), label=t["mcp_saved_label"],
                        allow_custom_value=False, scale=3,
                    )
                    mcp_load_btn   = gr.Button(t["mcp_load_btn"],         size="sm", scale=1)
                    mcp_del_saved  = gr.Button(t["mcp_del_saved_btn"], size="sm", scale=1,
                                               variant="stop")
                mcp_name_in    = gr.Textbox(label=t["mcp_name_label"],    placeholder=t["mcp_name_placeholder"])
                mcp_command_in = gr.Textbox(
                    label=t["mcp_command_label"],
                    placeholder=t["mcp_command_placeholder"],
                )
                mcp_cwd_in     = gr.Textbox(label=t["mcp_cwd_label"])
                with gr.Row():
                    mcp_connect_btn    = gr.Button(t["mcp_connect_btn"], variant="primary", scale=2)
                    mcp_disconnect_btn = gr.Button(t["mcp_disconnect_btn"], variant="stop", scale=1)
                mcp_status = gr.Markdown("")

                # ── Call a tool section ───────────────────────────────────────
                gr.Markdown(t["mcp_call_section"])
                with gr.Row():
                    mcp_server_dd = gr.Dropdown(
                        choices=[], label=t["mcp_server_label"], scale=2, allow_custom_value=False,
                    )
                    mcp_refresh_btn = gr.Button(t["mcp_refresh_tools_btn"], size="sm", scale=1)
                mcp_tool_dd   = gr.Dropdown(choices=[], label=t["mcp_tool_label"],
                                            allow_custom_value=False)
                mcp_tool_desc = gr.Markdown("")

                # 8 pre-defined parameter rows (shown/hidden dynamically)
                mcp_param_rows   = []
                mcp_param_labels = []
                mcp_param_inputs = []
                for _pi in range(8):
                    with gr.Row(visible=False) as _pr:
                        _pl = gr.Markdown("")
                        _pv = gr.Textbox(show_label=False, container=False,
                                         placeholder="value", scale=3)
                    mcp_param_rows.append(_pr)
                    mcp_param_labels.append(_pl)
                    mcp_param_inputs.append(_pv)

                with gr.Row():
                    mcp_call_btn  = gr.Button(t["mcp_call_btn"], variant="primary", scale=2)
                    mcp_send_btn  = gr.Button(t["add_to_chat_btn"], scale=1)
                    mcp_copy_btn  = gr.Button(t["copy_btn"], scale=1)

                mcp_result_out = gr.Markdown("")
                mcp_result_ctx = gr.State("")

                # ── handlers ─────────────────────────────────────────────────

                def _mcp_load_preset(name):
                    for c in _mcp_load_configs():
                        if c["name"] == name:
                            return c["name"], c["command"], c.get("cwd", "")
                    return gr.update(), gr.update(), gr.update()

                def _mcp_connect(name, command, cwd):
                    import gradio as _gr
                    from .mcp_client import MCPClient
                    name = (name or "").strip()
                    if not name or not (command or "").strip():
                        return (t["provide_name_command"],
                                _gr.update(), _gr.update(), _gr.update())
                    try:
                        client = MCPClient(name, command.strip(), cwd.strip() or None)
                    except Exception as e:
                        return (t["connection_failed"].format(e=e),
                                _gr.update(), _gr.update(), _gr.update())
                    _mcp_clients[name] = client
                    tools = client.list_tools()
                    _mcp_tools_cache[name] = {_t["name"]: _t for _t in tools}
                    _mcp_save_config(name, command.strip(), cwd.strip())
                    return (
                        t["connected_to_server"].format(name=name, n=len(tools)),
                        _gr.update(choices=list(_mcp_clients.keys()), value=name),
                        _gr.update(choices=[_t["name"] for _t in tools], value=None),
                        _gr.update(choices=_mcp_saved_names()),
                    )

                def _mcp_disconnect(server_name):
                    import gradio as _gr
                    if server_name and server_name in _mcp_clients:
                        _mcp_clients.pop(server_name).close()
                        _mcp_tools_cache.pop(server_name, None)
                    return (
                        t["disconnected"].format(name=server_name or ""),
                        _gr.update(choices=list(_mcp_clients.keys()), value=None),
                        _gr.update(choices=[]),
                    )

                def _mcp_refresh_tools(server_name):
                    import gradio as _gr
                    client = _mcp_clients.get(server_name)
                    if not client:
                        return _gr.update(choices=[])
                    tools = client.list_tools()
                    _mcp_tools_cache[server_name] = {_t["name"]: _t for _t in tools}
                    return _gr.update(choices=[_t["name"] for _t in tools], value=None)

                def _mcp_tool_select(server_name, tool_name):
                    import gradio as _gr
                    tool = _mcp_tools_cache.get(server_name or "", {}).get(tool_name or "", {})
                    desc = tool.get("description", "")
                    props = tool.get("inputSchema", {}).get("properties", {})
                    required = tool.get("inputSchema", {}).get("required", [])
                    params = sorted(props.items(),
                                    key=lambda x: (x[0] not in required, x[0]))
                    updates = [desc]
                    for i in range(8):
                        if i < len(params):
                            pname, pschema = params[i]
                            req_mark = "  *(required)*" if pname in required else ""
                            pdesc = pschema.get("description", "")
                            ptype = pschema.get("type", "string")
                            label = f"**{pname}** `{ptype}`{req_mark}" + (f" — {pdesc}" if pdesc else "")
                            default = str(pschema.get("default", ""))
                            updates += [_gr.update(visible=True), label, default]
                        else:
                            updates += [_gr.update(visible=False), "", ""]
                    return updates  # 1 + 24 = 25 values

                def _mcp_call(server_name, tool_name, *param_values):
                    client = _mcp_clients.get(server_name or "")
                    if not client:
                        return t["no_server_connected"], ""
                    if not tool_name:
                        return t["select_tool"], ""
                    tool = _mcp_tools_cache.get(server_name, {}).get(tool_name, {})
                    props = tool.get("inputSchema", {}).get("properties", {})
                    required = tool.get("inputSchema", {}).get("required", [])
                    params = sorted(props.items(),
                                    key=lambda x: (x[0] not in required, x[0]))
                    args: dict = {}
                    for i, (pname, pschema) in enumerate(params[:8]):
                        val = param_values[i] if i < len(param_values) else ""
                        if (val or "").strip():
                            args[pname] = _mcp_coerce(val.strip(), pschema)
                    try:
                        result = client.call_tool(tool_name, args)
                    except Exception as e:
                        return f"Error: {e}", ""
                    ctx = (f"**MCP [{server_name}/{tool_name}]**\n"
                           f"Args: {args}\n\n```\n{result}\n```")
                    return result, ctx

                def _mcp_del_saved(name):
                    import gradio as _gr
                    if name:
                        _mcp_delete_config(name)
                    return _gr.update(choices=_mcp_saved_names(), value=None)

                # wire up
                mcp_load_btn.click(
                    _mcp_load_preset,
                    inputs=[mcp_saved_dd],
                    outputs=[mcp_name_in, mcp_command_in, mcp_cwd_in],
                )
                mcp_del_saved.click(
                    _mcp_del_saved, inputs=[mcp_saved_dd], outputs=[mcp_saved_dd],
                )
                mcp_connect_btn.click(
                    _mcp_connect,
                    inputs=[mcp_name_in, mcp_command_in, mcp_cwd_in],
                    outputs=[mcp_status, mcp_server_dd, mcp_tool_dd, mcp_saved_dd],
                )
                mcp_disconnect_btn.click(
                    _mcp_disconnect,
                    inputs=[mcp_server_dd],
                    outputs=[mcp_status, mcp_server_dd, mcp_tool_dd],
                )
                mcp_refresh_btn.click(
                    _mcp_refresh_tools,
                    inputs=[mcp_server_dd],
                    outputs=[mcp_tool_dd],
                )
                mcp_server_dd.change(
                    _mcp_refresh_tools,
                    inputs=[mcp_server_dd],
                    outputs=[mcp_tool_dd],
                )
                mcp_tool_dd.change(
                    _mcp_tool_select,
                    inputs=[mcp_server_dd, mcp_tool_dd],
                    outputs=[mcp_tool_desc]
                            + [o for i in range(8)
                               for o in (mcp_param_rows[i], mcp_param_labels[i],
                                         mcp_param_inputs[i])],
                )
                mcp_call_btn.click(
                    _mcp_call,
                    inputs=[mcp_server_dd, mcp_tool_dd] + mcp_param_inputs,
                    outputs=[mcp_result_out, mcp_result_ctx],
                )
                mcp_send_btn.click(
                    lambda ctx: (ctx, gr.update(visible=True)),
                    inputs=[mcp_result_ctx],
                    outputs=[ctx_buffer, add_ctx_panel],
                    js=_JS_SCROLL_TO_PANEL,
                )
                mcp_copy_btn.click(None, inputs=[mcp_result_out], outputs=[], js="async (text) => { await navigator.clipboard.writeText(text || ''); }")

            # Team
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["team_tab"]):
                gr.Markdown(t["team_desc"])

                # ── Profile & query ───────────────────────────────────────────
                with gr.Row():
                    team_profile_dd = gr.Dropdown(
                        choices=_parallel_mod.profile_names(), value=None,
                        label=t["team_profile_label"],
                        allow_custom_value=False, scale=3,
                    )
                    team_profile_refresh = gr.Button(t["team_refresh_btn"], size="sm", scale=1)

                team_query = gr.Textbox(label=t["team_query_label"], lines=3,
                                        placeholder=t["team_query_placeholder"])

                with gr.Row():
                    team_ctx_mode = gr.Dropdown(
                        choices=list(zip(t["team_ctx_mode_choices"], ["none", "last", "full"])),
                        value="none",
                        label=t["team_ctx_mode_label"], scale=2,
                    )
                    team_combine = gr.Radio(
                        choices=list(zip(t["team_combine_choices"], ["join", "compact"])),
                        value="join",
                        label=t["team_combine_label"], scale=3,
                    )

                # ── Worker / aspect rows (8 pre-defined, shown/hidden) ────────
                gr.Markdown(t["team_workers_header"])
                team_worker_rows   = []
                team_worker_labels = []
                team_aspect_inputs = []
                for _ti in range(8):
                    with gr.Row(visible=False) as _tr:
                        with gr.Column(scale=2):
                            _tl = gr.Markdown("")
                        _ta = gr.Textbox(show_label=False, container=False,
                                         placeholder=t["team_aspect_placeholder"], scale=3)
                    team_worker_rows.append(_tr)
                    team_worker_labels.append(_tl)
                    team_aspect_inputs.append(_ta)

                with gr.Row():
                    team_clear_btn = gr.Button(t["team_clear_btn"], scale=1)
                    team_run_btn   = gr.Button(t["team_run_btn"], variant="primary", scale=2)
                    team_add_btn   = gr.Button(t["add_to_chat_btn"], scale=1)
                    team_copy_btn  = gr.Button(t["copy_btn"], scale=1)

                team_progress  = gr.Markdown("")
                team_result_md = gr.Markdown("")
                team_result_ctx = gr.State("")

                # ── handlers ─────────────────────────────────────────────────

                def _team_profile_select(name):
                    import gradio as _gr
                    profile = _parallel_mod.get_profile(name or "")
                    workers = profile["workers"] if profile else []
                    updates = []
                    for i in range(8):
                        visible = i < len(workers)
                        if visible:
                            w = workers[i]
                            label = f"`{w['model']}` @ `{w['host']}`"
                            updates += [_gr.update(visible=True),          # row
                                        _gr.update(value=label),           # label
                                        _gr.update(value="")]              # aspect
                        else:
                            updates += [_gr.update(visible=False),         # row
                                        _gr.update(value=""),              # label
                                        _gr.update(value="")]              # aspect
                    return updates  # 24 values

                def _team_run(profile_name, main_query, ctx_mode, combine,
                              *rest):
                    import gradio as _gr
                    aspects    = list(rest[:8])
                    messages   = rest[8]
                    num_ctx    = rest[9]
                    model      = rest[10]
                    url        = rest[11]
                    backend_lbl = rest[12]
                    timeout    = rest[13]

                    profile = _parallel_mod.get_profile(profile_name or "")
                    if not profile or not main_query.strip():
                        yield t["select_profile_query"], "", ""
                        return

                    workers = profile["workers"][:8]
                    if ctx_mode == "none":
                        base = []
                    elif ctx_mode == "last":
                        base = messages[-2:] if len(messages) >= 2 else list(messages)
                    else:
                        base = list(messages)

                    progress_log: list[str] = []
                    lock = __import__("threading").Lock()

                    def on_progress(msg: str):
                        with lock:
                            progress_log.append(msg)

                    results_box: dict = {}

                    def worker_thread():
                        results_box["results"] = _parallel_mod.run_parallel(
                            workers, aspects, main_query.strip(), base,
                            int(num_ctx), int(timeout), on_progress,
                        )

                    _thread = __import__("threading").Thread(target=worker_thread, daemon=True)
                    _thread.start()

                    while _thread.is_alive():
                        with lock:
                            log = "\n\n".join(progress_log)
                        yield log, "", _gr.update()
                        __import__("time").sleep(0.5)
                    _thread.join()

                    with lock:
                        log = "\n\n".join(progress_log) + t["team_done"]

                    results = results_box.get("results", [])
                    backend = _backend_key(backend_lbl)

                    if combine == "compact":
                        final = _parallel_mod.compact_results(
                            main_query.strip(), results,
                            model, url, backend, int(num_ctx), int(timeout),
                        )
                    else:
                        final = _parallel_mod.join_results(main_query.strip(), results)

                    yield log, final, final

                def _team_clear():
                    import gradio as _gr
                    return ("", "", "", _gr.update(value=None)) + ("",) * 8

                team_profile_refresh.click(
                    lambda: gr.update(choices=_parallel_mod.profile_names()),
                    outputs=[team_profile_dd],
                )
                team_profile_dd.change(
                    _team_profile_select,
                    inputs=[team_profile_dd],
                    outputs=[o for i in range(8)
                             for o in (team_worker_rows[i], team_worker_labels[i],
                                       team_aspect_inputs[i])],
                )
                team_run_btn.click(
                    _team_run,
                    inputs=[team_profile_dd, team_query, team_ctx_mode, team_combine]
                           + team_aspect_inputs
                           + [chatbot, s_ctx, g_model, g_url, g_backend, s_timeout],
                    outputs=[team_progress, team_result_md, team_result_ctx],
                )
                team_clear_btn.click(
                    _team_clear,
                    outputs=[team_query, team_progress, team_result_md, team_profile_dd]
                             + team_aspect_inputs,
                )
                team_add_btn.click(
                    lambda ctx: (ctx, gr.update(visible=True)),
                    inputs=[team_result_ctx],
                    outputs=[ctx_buffer, add_ctx_panel],
                    js=_JS_SCROLL_TO_PANEL,
                )
                team_copy_btn.click(None, inputs=[team_result_md], outputs=[], js="async (text) => { await navigator.clipboard.writeText(text || ''); }")

            # ══════════════════════════════════════════════════════════════════
            # WebAsk
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["webask_tab"]):
                gr.Markdown(t["webask_desc"])

                wa_url = gr.Textbox(
                    label=t["wa_url_label"],
                    placeholder=t["wa_url_placeholder"],
                )
                wa_q = gr.Textbox(
                    label=t["wa_q_label"],
                    placeholder=t["wa_q_placeholder"],
                    lines=2,
                )
                wa_n = gr.Slider(1, 10, value=3, step=1,
                                 label=t["wa_n_label"])
                with gr.Row():
                    wa_btn      = gr.Button(t["webask_btn"], variant="primary", scale=2)
                    wa_add_btn  = gr.Button(t["add_to_chat_btn"], scale=1)
                    wa_copy_btn = gr.Button(t["copy_btn"], scale=1)
                wa_out      = gr.Markdown(label=t["wa_answer_label"])
                wa_full_ctx  = gr.State("")   # question + sources + page text + answer
                wa_src_only  = gr.State("")   # question + URL list only

                def _webask(url, question, top_n, model, ollama_u, backend_label, timeout):
                    question = question.strip()
                    if not question:
                        yield "_Provide a question._", "", "", ""
                        return
                    from .adapter import ChatAdapter
                    import requests as _req
                    adapter = ChatAdapter(model=model, base_url=ollama_u,
                                          backend=_backend_key(backend_label),
                                          timeout=int(timeout))

                    # ── single URL mode ────────────────────────────────────────
                    if url.strip():
                        yield f"_Fetching `{url.strip()}`..._", "", "", ""
                        from .tools import fetch_text
                        try:
                            page = fetch_text(url.strip())
                        except Exception as e:
                            yield f"**Fetch error:** {e}", "", "", ""
                            return
                        chars = len(page)
                        yield (f"_Fetched {chars:,} chars. Asking LLM..._\n\n"
                               f"> {url.strip()}"), "", "", ""
                        prompt = (
                            f"Source: {url.strip()}\n\n{page}\n\n---\n\n"
                            f"Question: {question}\n\n"
                            f"Answer based ONLY on the content above. "
                            f"Cite source URLs where relevant."
                        )
                        answer = complete([{"role": "user", "content": prompt}],
                                          model, ollama_u,
                                          backend=_backend_key(backend_label),
                                          timeout=int(timeout))
                        full = f"**Question:** {question}\n\n**Source:** {url.strip()}\n\n---\n\n{answer}"
                        src  = f"**Question:** {question}\n\n**Source:** {url.strip()}"
                        yield answer, full, src, ""
                        return

                    # ── DDG search mode ────────────────────────────────────────
                    yield "_Searching the web..._", "", "", ""
                    results = adapter._web_ddg_search(question, int(top_n))
                    if not results:
                        yield "_No search results found._", "", "", ""
                        return

                    sources_md = [f"{i}. [{title}]({u})"
                                  for i, (title, u, _) in enumerate(results, 1)]
                    src_list = "\n".join(sources_md)
                    src = f"**Question:** {question}\n\n**Sources:**\n{src_list}"
                    yield (f"_Found {len(results)} pages. Fetching content..._\n\n"
                           + src_list), "", src, ""

                    pages = []
                    fetch_log = []
                    for i, (title, u, snippet) in enumerate(results, 1):
                        try:
                            r = _req.get(u, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                            page = adapter._web_strip_html(r.content)[:3000]
                            fetch_log.append(f"{i}. [{title}]({u}) — {len(page):,} chars")
                        except Exception:
                            page = snippet
                            fetch_log.append(f"{i}. [{title}]({u}) — snippet only")
                        pages.append(f"[Source {i}: {u}]\n{page}")
                        yield (f"_Fetching {i}/{len(results)}..._\n\n"
                               + "\n".join(fetch_log)), "", src, ""

                    total_chars = sum(len(p) for p in pages)
                    yield (f"_Fetched {total_chars:,} chars total. Asking LLM..._\n\n"
                           + "\n".join(fetch_log)), "", src, ""

                    prompt = (
                        "Sources:\n\n" + "\n\n".join(pages) +
                        f"\n\n---\n\nQuestion: {question}\n\n"
                        "Answer based on the sources above. Cite source numbers."
                    )
                    answer = complete([{"role": "user", "content": prompt}],
                                      model, ollama_u,
                                      backend=_backend_key(backend_label),
                                      timeout=int(timeout))
                    full = (f"**Question:** {question}\n\n"
                            f"**Sources:**\n{src_list}\n\n---\n\n**Answer:**\n\n{answer}")
                    yield answer, full, src, ""

                wa_btn.click(
                    _webask,
                    inputs=[wa_url, wa_q, wa_n, g_model, g_url, g_backend, s_timeout],
                    outputs=[wa_out, wa_full_ctx, wa_src_only, s_hidden_ctx],
                )
                wa_q.submit(
                    _webask,
                    inputs=[wa_url, wa_q, wa_n, g_model, g_url, g_backend, s_timeout],
                    outputs=[wa_out, wa_full_ctx, wa_src_only, s_hidden_ctx],
                )
                wa_add_btn.click(
                    lambda full, src: (full, src, gr.update(visible=True)),
                    inputs=[wa_full_ctx, wa_src_only],
                    outputs=[ctx_buffer, ctx_sources, add_ctx_panel],
                    js=_JS_SCROLL_TO_PANEL,
                )
                wa_copy_btn.click(None, inputs=[wa_out], outputs=[], js="async (text) => { await navigator.clipboard.writeText(text || ''); }")

            # ══════════════════════════════════════════════════════════════════
            # WebAnalys
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["webanalys_tab"]):
                gr.Markdown(t["webanalys_desc"])
                wan_q = gr.Textbox(
                    label=t["wan_q_label"],
                    placeholder=t["wan_q_placeholder"],
                    lines=2,
                )
                wan_n = gr.Slider(1, 300, value=5, step=1,
                                  label=t["wan_n_label"])
                with gr.Row():
                    wan_btn      = gr.Button(t["webanalys_btn"], variant="primary", scale=2)
                    wan_add_btn  = gr.Button(t["add_to_chat_btn"], scale=1)
                    wan_copy_btn = gr.Button(t["copy_btn"], scale=1)
                wan_out = gr.Markdown(label=t["wan_results_label"])

                def _webanalys(query, n, model, ollama_u, backend_label, timeout):
                    import re as _re
                    query = query.strip()
                    if not query:
                        yield "_Provide a query._"
                        return
                    from .adapter import ChatAdapter
                    from .flows.webanalys import _RATING_PROMPT, _stars
                    from .flows.deepagent_md import _apply_extract, _fetch_page

                    adapter = ChatAdapter(model=model, base_url=ollama_u,
                                          backend=_backend_key(backend_label),
                                          timeout=int(timeout))
                    n = int(n)

                    yield f"Searching DuckDuckGo: **{query}**...\n\n"

                    try:
                        results = adapter._web_ddg_search(query, n=n + 3)
                    except Exception as e:
                        yield f"**Search failed:** {e}"
                        return

                    if not results:
                        yield "_No results from DuckDuckGo._"
                        return

                    def _render(rated, status=""):
                        lines = [f"# webanalys: {query}\n"]
                        for i, (sc, t, u, w) in enumerate(rated, 1):
                            lines.append(f"{i}. [{sc}/5] {_stars(sc)}  {t}")
                            lines.append(f"   {u}")
                            lines.append(f"   {w}")
                            lines.append("")
                        if status:
                            lines.append(f"_{status}_")
                        return "\n".join(lines)

                    ratings = []
                    fetched = 0

                    for title, url, snippet in results:
                        if fetched >= n:
                            break
                        if not url.startswith("http"):
                            continue

                        yield _render(ratings,
                                      f"[{fetched+1}/{n}] fetching {url[:70]}...")

                        page_bytes = _fetch_page(url)
                        source_label = "full page"
                        if page_bytes:
                            try:
                                raw = adapter._web_strip_html(page_bytes)
                                content = _apply_extract(adapter, raw, None, None)
                            except Exception:
                                content = snippet or ""
                                source_label = "DDG snippet (parse error)"
                        else:
                            content = snippet or ""
                            source_label = "DDG snippet (site blocked)"

                        if not content.strip():
                            ratings.append((0, title, url,
                                            f"(no content — {source_label})"))
                            fetched += 1
                            continue

                        yield _render(ratings,
                                      f"[{fetched+1}/{n}] rating {url[:70]}...")

                        source_note = (f"[Source: {source_label}]\n\n"
                                       if source_label != "full page" else "")
                        prompt = _RATING_PROMPT.format(
                            query=query,
                            content=source_note + content[:3000],
                        )
                        reply = adapter._stream_chat(
                            [{"role": "user", "content": prompt}]
                        ) or ""

                        score = 0
                        why = "—"
                        rm = _re.search(r'[Rr]ating:\s*(\d)', reply)
                        wm = _re.search(r'[Ww]hy:\s*(.+)', reply)
                        if rm:
                            score = max(0, min(5, int(rm.group(1))))
                        if wm:
                            why = wm.group(1).strip()

                        why_full = (why if source_label == "full page"
                                    else f"{why}  [{source_label}]")
                        ratings.append((score, title, url, why_full))
                        fetched += 1

                    if not ratings:
                        yield "_No pages could be evaluated._"
                        return

                    ratings.sort(key=lambda x: x[0], reverse=True)
                    output = _render(ratings)
                    yield output

                wan_btn.click(
                    _webanalys,
                    inputs=[wan_q, wan_n, g_model, g_url, g_backend, s_timeout],
                    outputs=[wan_out],
                )
                wan_q.submit(
                    _webanalys,
                    inputs=[wan_q, wan_n, g_model, g_url, g_backend, s_timeout],
                    outputs=[wan_out],
                )
                wan_add_btn.click(lambda c: (c, gr.update(visible=True)), inputs=[wan_out], outputs=[ctx_buffer, add_ctx_panel], js=_JS_SCROLL_TO_PANEL)
                wan_copy_btn.click(None, inputs=[wan_out], outputs=[], js="async (text) => { await navigator.clipboard.writeText(text || ''); }")

            # ══════════════════════════════════════════════════════════════════
            # WebCrawl
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["webcrawl_tab"]):
                gr.Markdown(t["webcrawl_desc"])
                wc_url = gr.Textbox(label=t["wc_url_label"],
                                    placeholder=t["wc_url_placeholder"])
                with gr.Row():
                    wc_mode = gr.Dropdown(
                        choices=["combine", "pages", "extract", "mirror", "llm"],
                        value="combine",
                        label=t["wc_mode_label"],
                        info="combine=one file | pages=one file/page | extract=CSV(XPath) | mirror=HTML | llm=filter+extract per page",
                    )
                    wc_filter = gr.Dropdown(
                        choices=["none", "url-prefix", "llm"],
                        value="none",
                        label=t["wc_filter_label"],
                        info="url-prefix=stay under start path | llm=LLM decides relevance (works with any mode)",
                    )
                with gr.Row():
                    wc_depth = gr.Slider(1, 10, value=2, step=1, label=t["wc_depth_label"])
                    wc_pages = gr.Number(value=20, minimum=0, precision=0,
                                         label=t["wc_pages_label"])
                wc_task = gr.Textbox(
                    label=t["wc_task_label"],
                    placeholder=t["wc_task_placeholder"],
                    lines=2,
                    visible=False,
                )
                with gr.Row():
                    wc_format = gr.Radio(
                        choices=["log", "structured"],
                        value="log",
                        label=t["wc_format_label"],
                        visible=False,
                    )
                    wc_ask = gr.Checkbox(label=t["wc_ask_label"],
                                         value=False)

                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown(t["wc_columns_header"])
                        wc_columns = gr.Textbox(
                            value='# name: "//xpath"\n# title:   "//h1/text()"\n# content: "//div[@class=\'body\']/text()"\n',
                            label="",
                            lines=6,
                            show_label=False,
                            visible=False,
                        )
                        with gr.Row():
                            wc_cols_load = gr.UploadButton("Load (.yaml)",
                                                           file_types=[".yaml", ".yml"],
                                                           size="sm", visible=False)
                            wc_cols_save = gr.DownloadButton("Save (.yaml)",
                                                             size="sm", visible=False)
                    with gr.Column(scale=2):
                        with gr.Row():
                            wc_btn      = gr.Button(t["webcrawl_btn"], variant="primary", scale=2)
                            wc_add_btn  = gr.Button(t["add_to_chat_btn"], scale=1)
                            wc_copy_btn = gr.Button(t["copy_btn"], scale=1)
                        wc_out = gr.Markdown(label=t["wc_results_label"])

                def _wc_mode_change(mode, filter_val):
                    show_task    = mode == "llm" or filter_val == "llm"
                    show_format  = mode == "llm"
                    show_columns = mode in ("extract", "llm")
                    return (
                        gr.update(visible=show_task),
                        gr.update(visible=show_format),
                        gr.update(visible=show_columns),
                        gr.update(visible=show_columns),
                        gr.update(visible=show_columns),
                    )

                wc_mode.change(
                    _wc_mode_change,
                    inputs=[wc_mode, wc_filter],
                    outputs=[wc_task, wc_format, wc_columns, wc_cols_load, wc_cols_save],
                )
                wc_filter.change(
                    _wc_mode_change,
                    inputs=[wc_mode, wc_filter],
                    outputs=[wc_task, wc_format, wc_columns, wc_cols_load, wc_cols_save],
                )

                def _load_wc_yaml(file):
                    if file is None:
                        return gr.update()
                    try:
                        path = file if isinstance(file, str) else file.name
                        return open(path, "r", encoding="utf-8").read()
                    except Exception as e:
                        return f"# Error: {e}"

                def _save_wc_yaml(text):
                    import tempfile
                    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                                     delete=False, encoding="utf-8")
                    tmp.write(text); tmp.close()
                    return tmp.name

                wc_cols_load.upload(_load_wc_yaml, inputs=[wc_cols_load], outputs=[wc_columns])
                wc_cols_save.click(_save_wc_yaml, inputs=[wc_columns], outputs=[wc_cols_save])

                def _webcrawl(start_url, mode, filter_mode, task, fmt, use_ask,
                              columns_yaml, depth, pages,
                              model, ollama_u, backend_label, timeout):
                    import os, time as _time, tempfile
                    from urllib.parse import urlparse as _up
                    start_url = start_url.strip()
                    if not start_url:
                        yield "_Provide a start URL._"
                        return
                    from .adapter import ChatAdapter
                    from .flows import webcrawl as _wcf

                    adapter = ChatAdapter(model=model, base_url=ollama_u,
                                          backend=_backend_key(backend_label),
                                          timeout=int(timeout))

                    crawl_dir = VYRII_HOME / "crawl"
                    crawl_dir.mkdir(exist_ok=True)
                    ts = _time.strftime("%Y%m%d_%H%M%S")

                    if mode in ("pages", "mirror"):
                        out_path = str(crawl_dir / f"crawl_{ts}")
                        os.makedirs(out_path, exist_ok=True)
                    elif mode == "extract" or (mode == "llm" and fmt == "structured"):
                        out_path = str(crawl_dir / f"crawl_{ts}.csv")
                    else:
                        out_path = str(crawl_dir / f"crawl_{ts}.txt")

                    args = (f"{start_url} --mode {mode}"
                            f" --depth {int(depth)} -N {int(pages)}"
                            f" --out {out_path}")

                    if filter_mode == "url-prefix":
                        p = _up(start_url)
                        prefix = f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"
                        args += f" --filter {prefix}"
                    elif filter_mode == "llm":
                        args += " --filter llm"

                    task_str = (task or "").strip()
                    if task_str and (mode == "llm" or filter_mode == "llm"):
                        args += f' --task "{task_str}"'

                    if mode == "llm":
                        args += f" --format {fmt}"

                    col_lines = [l for l in columns_yaml.splitlines()
                                 if l.strip() and not l.strip().startswith("#")]
                    if col_lines and mode in ("extract", "llm"):
                        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                                          delete=False, encoding="utf-8")
                        tmp.write(columns_yaml); tmp.close()
                        args += f" --columns {tmp.name}"

                    if use_ask:
                        args += " --ask"

                    lines = []
                    for progress in _stream_flow(lambda: _wcf.run(adapter, args), lines):
                        yield progress

                    summary = adapter.last_reply.strip() if adapter.last_reply else ""
                    if os.path.isfile(out_path):
                        content = open(out_path, encoding="utf-8").read(8000).strip()
                        header  = f"**Output:** `{out_path}`\n\n"
                        yield header + (summary or content or
                                        "_No relevant content found — try a different mode or filter._")
                    elif os.path.isdir(out_path):
                        files = sorted(os.listdir(out_path))
                        yield (f"**Output directory:** `{out_path}` ({len(files)} files)\n\n" +
                               "\n".join(f"- {f}" for f in files[:30]))
                    elif summary:
                        yield summary
                    else:
                        yield ('```\n' + '\n'.join(lines) + '\n```\n\n'
                               '_No output written._')

                wc_btn.click(
                    _webcrawl,
                    inputs=[wc_url, wc_mode, wc_filter, wc_task, wc_format,
                            wc_ask, wc_columns, wc_depth, wc_pages,
                            g_model, g_url, g_backend, s_timeout],
                    outputs=[wc_out],
                )
                wc_add_btn.click(lambda c: (c, gr.update(visible=True)), inputs=[wc_out], outputs=[ctx_buffer, add_ctx_panel], js=_JS_SCROLL_TO_PANEL)
                wc_copy_btn.click(None, inputs=[wc_out], outputs=[], js="async (text) => { await navigator.clipboard.writeText(text || ''); }")

            # ══════════════════════════════════════════════════════════════════
            # WebIndex
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["webindex_tab"]):
                gr.Markdown(t["webindex_desc"])
                wi_url     = gr.Textbox(label=t["wi_url_label"],
                                        placeholder=t["wi_url_placeholder"])
                wi_project = gr.Textbox(label=t["wi_project_label"],
                                        placeholder=t["wi_project_placeholder"])
                with gr.Row():
                    wi_depth = gr.Slider(1, 5, value=2, step=1, label=t["wi_depth_label"])
                    wi_pages = gr.Number(value=20, minimum=1, precision=0,
                                         label=t["wi_pages_label"])
                with gr.Row():
                    wi_btn = gr.Button(t["webindex_btn"], variant="primary", scale=2)
                wi_out = gr.Textbox(label=t["wi_progress_label"], lines=14, interactive=False)

                def _webindex(url, project, depth, pages, model, ollama_u, backend_label, timeout):
                    url = url.strip()
                    if not url:
                        yield "_Provide a URL._"
                        return
                    from .adapter import ChatAdapter
                    from .flows import webindex as _wi

                    adapter = ChatAdapter(model=model, base_url=ollama_u,
                                          backend=_backend_key(backend_label),
                                          timeout=int(timeout))
                    proj = project.strip()
                    proj_arg = f"--project {proj}" if proj else ""
                    vyrii_path = str(VYRII_HOME)
                    args = (f'{url} {proj_arg} --path "{vyrii_path}"'
                            f' --depth {int(depth)} --pages {int(pages)}')
                    lines = []
                    for prog in _stream_flow(lambda: _wi.run(adapter, args), lines):
                        yield prog
                    yield ('```\n' + '\n'.join(lines) + '\n```\n\n'
                           '_Index complete. Refresh RAG projects in DeepAgent MD tab._')

                wi_btn.click(
                    _webindex,
                    inputs=[wi_url, wi_project, wi_depth, wi_pages,
                            g_model, g_url, g_backend, s_timeout],
                    outputs=[wi_out],
                )

            # ══════════════════════════════════════════════════════════════════
            # DeepAgent MD
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["deepagent_tab"]):
                gr.Markdown(t["deepagent_desc"])
                dam_task = gr.Textbox(
                    label=t["dam_task_label"],
                    placeholder=t["dam_task_placeholder"],
                    lines=2,
                )
                with gr.Row():
                    dam_sections = gr.Slider(2, 6, value=3, step=1,
                                             label=t["dam_sections_label"], scale=2)
                    dam_preset = gr.Dropdown(
                        choices=list(zip(t["dam_preset_choices"], ["", "quick", "balanced", "deep"])),
                        value="", label=t["dam_preset_label"], scale=1,
                    )
                with gr.Row():
                    dam_ctx = gr.Number(value=6, minimum=2, maximum=64, precision=0,
                                        label=t["dam_ctx_label"], scale=1)
                    dam_max_parent_ctx = gr.Number(value=500, minimum=0, maximum=5000, precision=0,
                                                   label=t["dam_max_parent_ctx_label"], scale=2)
                dam_plan = gr.Textbox(label=t["dam_plan_label"],
                                      placeholder=t["dam_plan_placeholder"])
                dam_list = gr.Textbox(label=t["dam_list_label"],
                                      placeholder=t["dam_list_placeholder"])
                with gr.Row():
                    dam_use_web = gr.Checkbox(label=t["dam_use_web_label"], value=False)
                    dam_web_n   = gr.Number(label=t["dam_web_n_label"], value=3,
                                            minimum=1, maximum=10, precision=0,
                                            visible=False)
                    dam_prescan = gr.Checkbox(label=t["dam_prescan_label"], value=False)
                    dam_ref     = gr.Checkbox(label=t["dam_ref_label"], value=False)
                dam_rw = gr.Slider(0, 10, value=5, step=1, label=t["dam_rw_label"],
                                   visible=False)
                with gr.Row():
                    _rag_choices = _list_rag_projects()
                    dam_rag = gr.Dropdown(
                        choices=[""] + _rag_choices,
                        value="",
                        label=t["dam_rag_label"],
                        allow_custom_value=True,
                        scale=3,
                    )
                    dam_rag_refresh = gr.Button(t["files_refresh_btn"], size="sm", scale=1)

                gr.Markdown(t["dam_extract_header"])
                gr.Markdown(t["dam_extract_note"])
                dam_extract_mode = gr.Radio(
                    choices=list(zip(t["dam_extract_mode_choices"], ["none", "fix", "scan"])),
                    value="none", label=t["dam_extract_mode_label"],
                )
                with gr.Row():
                    dam_fix_top  = gr.Number(value=2000, minimum=0, precision=0,
                                             label=t["dam_fix_top_label"], visible=False)
                    dam_fix_mid  = gr.Number(value=0,    minimum=0, precision=0,
                                             label=t["dam_fix_mid_label"], visible=False)
                    dam_fix_last = gr.Number(value=0,    minimum=0, precision=0,
                                             label=t["dam_fix_last_label"], visible=False)
                dam_scan_n = gr.Number(value=200, minimum=50, precision=0,
                                       label=t["dam_scan_n_label"], visible=False)

                with gr.Row():
                    dam_profile = gr.Dropdown(
                        choices=[""] + _parallel_mod.profile_names(),
                        value="",
                        label=t["dam_profile_label"],
                        allow_custom_value=False,
                        scale=3,
                    )
                    dam_profile_refresh = gr.Button(t["files_refresh_btn"], size="sm", scale=1)

                with gr.Row():
                    dam_btn      = gr.Button(t["deepagent_generate_btn"], variant="primary", scale=2)
                    dam_add_btn  = gr.Button(t["add_to_chat_btn"], scale=1)
                    dam_copy_btn = gr.Button(t["copy_btn"], scale=1)
                dam_out = gr.Markdown(label=t["dam_document_label"])

                dam_use_web.change(
                    lambda v: gr.update(visible=v),
                    inputs=[dam_use_web], outputs=[dam_web_n],
                )

                def _dam_extract_change(mode):
                    show_fix  = mode == "fix"
                    show_scan = mode == "scan"
                    return (gr.update(visible=show_fix),
                            gr.update(visible=show_fix),
                            gr.update(visible=show_fix),
                            gr.update(visible=show_scan))

                dam_extract_mode.change(
                    _dam_extract_change,
                    inputs=[dam_extract_mode],
                    outputs=[dam_fix_top, dam_fix_mid, dam_fix_last, dam_scan_n],
                )

                def _dam_rw_visible(web, rag):
                    return gr.update(visible=bool(web and rag and rag.strip()))

                dam_use_web.change(_dam_rw_visible, inputs=[dam_use_web, dam_rag], outputs=[dam_rw])
                dam_rag.change(_dam_rw_visible, inputs=[dam_use_web, dam_rag], outputs=[dam_rw])

                dam_rag_refresh.click(
                    lambda: gr.update(choices=[""] + _list_rag_projects()),
                    outputs=[dam_rag],
                )
                dam_profile_refresh.click(
                    lambda: gr.update(choices=[""] + _parallel_mod.profile_names()),
                    outputs=[dam_profile],
                )

                def _deepagent_md(task, n_sections, preset,
                                  plan_str, list_str,
                                  use_web, web_n, prescan, use_ref, rw,
                                  rag_project, profile_name,
                                  extract_mode, fix_top, fix_mid, fix_last, scan_n,
                                  ctx_n, max_parent_ctx,
                                  model, ollama_u, backend_label, timeout):
                    import os, re as _re
                    task = task.strip()
                    if not task:
                        yield t["provide_topic"]
                        return
                    from .adapter import ChatAdapter
                    from .flows import deepagent_md as _df

                    adapter = ChatAdapter(model=model, base_url=ollama_u,
                                          backend=_backend_key(backend_label),
                                          timeout=int(timeout))
                    _w_timeout = int(_load_config().get("worker_timeout", 300))
                    args = f'"{task}" --maxdepth {int(n_sections)} --worker-timeout {_w_timeout}'

                    if preset:
                        args += f' --preset {preset}'
                    if use_web:
                        args += f' --web {int(web_n)}'
                    if prescan:
                        args += ' --prescan'
                    if use_ref:
                        args += ' --ref'
                    if rag_project and rag_project.strip():
                        args += f' --rag {rag_project.strip()} --rag-store "{VYRII_HOME}"'
                    if use_web and rag_project and rag_project.strip():
                        args += f' --rw {int(rw)}'
                    if extract_mode == "fix":
                        parts = []
                        if fix_top  > 0: parts.append(f"top:{int(fix_top)}")
                        if fix_mid  > 0: parts.append(f"mid:{int(fix_mid)}")
                        if fix_last > 0: parts.append(f"last:{int(fix_last)}")
                        if parts:        args += f' --fix {",".join(parts)}'
                    elif extract_mode == "scan" and scan_n > 0:
                        args += f' --scan {int(scan_n)}'
                    if int(ctx_n) != 6:
                        args += f' --ctx {int(ctx_n)}'
                    if int(max_parent_ctx) != 500:
                        args += f' --max_parent_ctx {int(max_parent_ctx)}'
                    if plan_str.strip():
                        args += f' plan: {plan_str.strip()}'
                    if list_str.strip():
                        args += f' list: {list_str.strip()}'

                    workers = None
                    if profile_name and profile_name.strip():
                        profile = _parallel_mod.get_profile(profile_name.strip())
                        if profile:
                            workers = [(w["host"], w["model"], w.get("provider", "ollama"))
                                       for w in profile.get("workers", [])]

                    lines = []
                    for progress in _stream_flow(lambda: _df.run(adapter, args, workers=workers), lines):
                        yield progress

                    plan_dir = None
                    for ln in lines:
                        m = _re.search(r'\[deepagent_md\] dir\s*:\s*(.+)', ln)
                        if m:
                            plan_dir = m.group(1).strip()
                            break

                    if not plan_dir or not os.path.isdir(plan_dir):
                        yield ('```\n' + '\n'.join(lines) + '\n```\n\n'
                               + t["no_output_dir"])
                        return

                    yield ('```\n' + '\n'.join(lines) + '\n```\n\n'
                           + t["composing_output"].format(plan_dir=plan_dir))

                    compose_lines = []
                    for progress in _stream_flow(
                        lambda: _df.run(adapter, f"compose {plan_dir} --plan"),
                        compose_lines,
                    ):
                        yield ('```\n' + '\n'.join(lines[-20:])
                               + '\n--- compose ---\n'
                               + '\n'.join(compose_lines[-10:]) + '\n```')

                    item_files = [f for f in os.listdir(plan_dir)
                                  if _re.match(r'^item_', f)]
                    header = f"**Output:** `{plan_dir}`\n\n"

                    if not item_files:
                        # expansion produced nothing — show index.md + full log
                        idx_path = os.path.join(plan_dir, "index.md")
                        idx_raw  = (open(idx_path, encoding="utf-8").read().strip()
                                    if os.path.isfile(idx_path) else "")
                        gen_log  = '\n'.join(ln for ln in lines if ln.strip())
                        skip_lines = [ln for ln in lines if "skip" in ln.lower() or "error" in ln.lower()]
                        diag = ("\n\n**Issues detected:**\n```\n" + "\n".join(skip_lines) + "\n```"
                                if skip_lines else "")
                        yield (header
                               + f"> No section files generated. Showing index only.{diag}\n\n"
                               + "---\n\n" + idx_raw
                               + "\n\n---\n\n**Generation log:**\n```\n" + gen_log + "\n```")
                        return

                    composed = os.path.join(plan_dir, "composed.md")
                    plan_md  = os.path.join(plan_dir, "PLAN.md")
                    if os.path.isfile(composed):
                        composed_content = open(composed, encoding="utf-8").read().strip()
                        index_content    = (open(plan_md, encoding="utf-8").read().strip()
                                            if os.path.isfile(plan_md) else "")
                        if index_content:
                            yield (header +
                                   f"## Index\n\n```\n{index_content}\n```\n\n---\n\n"
                                   + composed_content)
                        else:
                            yield header + "---\n\n" + composed_content
                    else:
                        yield (header +
                               '```\n' + '\n'.join(lines + compose_lines) + '\n```')

                dam_btn.click(
                    _deepagent_md,
                    inputs=[dam_task, dam_sections, dam_preset,
                            dam_plan, dam_list,
                            dam_use_web, dam_web_n, dam_prescan, dam_ref, dam_rw,
                            dam_rag, dam_profile,
                            dam_extract_mode, dam_fix_top, dam_fix_mid, dam_fix_last, dam_scan_n,
                            dam_ctx, dam_max_parent_ctx,
                            g_model, g_url, g_backend, s_timeout],
                    outputs=[dam_out],
                )
                dam_add_btn.click(lambda c: (c, gr.update(visible=True)), inputs=[dam_out], outputs=[ctx_buffer, add_ctx_panel], js=_JS_SCROLL_TO_PANEL)
                dam_copy_btn.click(None, inputs=[dam_out], outputs=[], js="async (text) => { await navigator.clipboard.writeText(text || ''); }")

            # ══════════════════════════════════════════════════════════════════
            # Scan (compact)
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["scan_tab"]):
                gr.Markdown(t["scan_desc"])
                sc_path  = gr.Textbox(label=t["sc_path_label"],
                                      placeholder=t["sc_path_placeholder"])
                sc_query = gr.Textbox(label=t["sc_query_label"],
                                      placeholder=t["sc_query_placeholder"])
                with gr.Row():
                    sc_chunk   = gr.Number(value=4000, minimum=500,  precision=0,
                                           label=t["sc_chunk_label"],   scale=1)
                    sc_summary = gr.Number(value=400,  minimum=50,   precision=0,
                                           label=t["sc_summary_label"], scale=1)
                with gr.Row():
                    sc_filter = gr.Checkbox(value=True, label=t["sc_filter_label"], scale=1)
                    sc_ext    = gr.Textbox(
                        value="txt,md,py,rst,js,ts,java,cpp,c,h,log,csv,json,yaml,yml,html,css",
                        label=t["sc_ext_label"], scale=4,
                    )
                with gr.Row():
                    sc_recursive = gr.Checkbox(value=False, label=t["sc_recursive_label"], scale=1)
                    sc_rounds    = gr.Number(value=3, minimum=2, maximum=10, precision=0,
                                            label=t["sc_rounds_label"], visible=False, scale=1)
                    sc_target    = gr.Number(value=8000, minimum=1000, precision=0,
                                            label=t["sc_target_label"], visible=False, scale=2)
                sc_btn = gr.Button(t["sc_run_btn"], variant="primary")
                sc_out = gr.Markdown(label=t["sc_out_label"])

                sc_filter.change(
                    lambda v: gr.update(visible=v),
                    inputs=[sc_filter], outputs=[sc_ext],
                )
                sc_recursive.change(
                    lambda v: (gr.update(visible=v), gr.update(visible=v)),
                    inputs=[sc_recursive], outputs=[sc_rounds, sc_target],
                )

                def _scan_compact(path, query, chunk, summary,
                                  use_filter, ext, use_recursive, rounds, target,
                                  model, ollama_u, backend_label, timeout):
                    from .flows import scan as _sc
                    from .adapter import ChatAdapter
                    path = path.strip()
                    if not path:
                        yield t.get("provide_topic", "Provide a file or directory path.")
                        return
                    adapter = ChatAdapter(model=model, base_url=ollama_u,
                                          backend=_backend_key(backend_label),
                                          timeout=int(timeout))
                    args = f'"{path}"'
                    if query.strip():
                        args += f' --query "{query.strip()}"'
                    args += f' --chunk {int(chunk)} --summary {int(summary)}'
                    if not use_filter:
                        args += ' --all-ext'
                    elif ext.strip():
                        args += f' --ext {ext.strip()}'
                    if use_recursive:
                        args += f' --rounds {int(rounds)} --target {int(target)}'
                    lines = []
                    for progress in _stream_flow(lambda: _sc.run(adapter, args), lines):
                        yield progress
                    out_line = next(
                        (l for l in reversed(lines) if l.startswith("[scan] output:")), ""
                    )
                    log_block = '```\n' + '\n'.join(lines) + '\n```'
                    if out_line:
                        filepath = out_line.replace("[scan] output:", "").strip()
                        try:
                            import os as _os2
                            content = open(filepath, encoding="utf-8").read().strip()
                            yield (log_block + f'\n\n**{out_line}**\n\n---\n\n' + content)
                        except Exception:
                            yield log_block + f'\n\n**{out_line}**'
                    else:
                        yield log_block

                sc_btn.click(
                    _scan_compact,
                    inputs=[sc_path, sc_query, sc_chunk, sc_summary,
                            sc_filter, sc_ext, sc_recursive, sc_rounds, sc_target,
                            g_model, g_url, g_backend, s_timeout],
                    outputs=[sc_out],
                )

            # ══════════════════════════════════════════════════════════════════
            # Files
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["files_tab"]):
                gr.Markdown(t["files_desc"])

                fi_browser = gr.FileExplorer(
                    root_dir=str(VYRII_HOME),
                    file_count="single",
                    label=t["files_tree_label"],
                    height=380,
                )
                fi_sel_state = gr.State("")   # source of truth for buttons
                with gr.Row():
                    fi_selected  = gr.Textbox(
                        label=t["files_selected_label"], interactive=True, scale=4,
                    )
                    fi_to_scan   = gr.Button(t["files_to_scan_btn"],  size="sm", scale=1)
                    fi_to_index  = gr.Button(t["files_to_index_btn"], size="sm", scale=1)
                    fi_to_view   = gr.Button(t["files_to_view_btn"],  size="sm", scale=1)

                with gr.Row():
                    fi_mkdir_path = gr.Textbox(label=t["files_mkdir_label"],
                                               placeholder=t["files_mkdir_placeholder"], scale=3)
                    fi_mkdir_btn  = gr.Button(t["files_mkdir_btn"], scale=1)

                with gr.Row():
                    fi_upload      = gr.File(label=t["files_upload_label"], file_count="multiple",
                                             scale=2)
                    fi_upload_dest = gr.Textbox(label=t["files_upload_dest_label"],
                                                value="files", scale=2)
                    fi_upload_btn  = gr.Button(t["files_upload_btn"], scale=1)

                with gr.Row():
                    fi_delete_path = gr.Textbox(label=t["files_delete_label"],
                                                placeholder=t["files_delete_placeholder"], scale=3)
                    fi_delete_btn  = gr.Button(t["files_delete_btn"], variant="stop", scale=1)

                gr.Markdown(t["files_index_header"], elem_id="fi_index_section")
                with gr.Row():
                    fi_index_path  = gr.Textbox(label=t["files_index_path_label"],
                                                placeholder=t["files_index_placeholder"], scale=3)
                    fi_index_btn   = gr.Button(t["files_index_btn"], scale=1)
                fi_index_log = gr.Textbox(label=t["files_index_log_label"], lines=5, interactive=False)

                fi_status = gr.Markdown(value="")

                def _fi_mkdir(rel_path):
                    if not rel_path.strip():
                        return t["provide_folder_path"]
                    try:
                        _resolve_safe(rel_path).mkdir(parents=True, exist_ok=True)
                        return t["created_folder"].format(rel_path=rel_path)
                    except Exception as e:
                        return f"Error: {e}"

                def _fi_upload(files, dest):
                    import shutil
                    if not files:
                        return t["no_files_selected"]
                    try:
                        d = _resolve_safe(dest or "files")
                        d.mkdir(parents=True, exist_ok=True)
                        names = []
                        for f in files:
                            src = f if isinstance(f, str) else f.name
                            dst = d / _pathlib.Path(src).name
                            shutil.copy2(src, dst)
                            names.append(dst.name)
                        return t["uploaded_files"].format(names=', '.join(names))
                    except Exception as e:
                        return f"Error: {e}"

                def _fi_delete(rel_path):
                    import shutil
                    if not rel_path.strip():
                        return t["provide_folder_path"]
                    try:
                        p = _resolve_safe(rel_path)
                        if p.is_dir():
                            shutil.rmtree(p)
                        elif p.is_file():
                            p.unlink()
                        else:
                            return t["not_found"].format(rel_path=rel_path)
                        return t["deleted_item"].format(rel_path=rel_path)
                    except Exception as e:
                        return f"Error: {e}"

                def _fi_index(rel_dir):
                    import subprocess
                    if not rel_dir.strip():
                        yield t["provide_directory"]
                        return
                    try:
                        target = _resolve_safe(rel_dir)
                        if not target.is_dir():
                            yield t["not_directory"].format(rel_dir=rel_dir)
                            return
                        project = target.name
                        rel     = str(target.relative_to(VYRII_HOME))
                        yield f"Running: simargl index files {rel} --project {project} ..."
                        result = subprocess.run(
                            ["simargl", "index", "files", rel,
                             "--project", project, "--store", ".simargl"],
                            capture_output=True, text=True, timeout=300,
                            cwd=str(VYRII_HOME),
                        )
                        if result.returncode == 0:
                            yield t["index_ok"].format(project=project)
                        else:
                            yield f"Error:\n{result.stderr[:500]}"
                    except Exception as e:
                        yield f"Error: {e}"

                def _fi_sel(path):
                    if not path:
                        return gr.update(), gr.update()
                    if isinstance(path, list):
                        path = path[0] if path else None
                        if not path:
                            return gr.update(), gr.update()
                    p = _pathlib.Path(str(path))
                    try:
                        result = str(p.relative_to(VYRII_HOME)).replace("\\", "/")
                    except ValueError:
                        result = str(p).replace("\\", "/")
                    return result, result  # textbox display + state

                fi_browser.change(_fi_sel, inputs=[fi_browser],
                                  outputs=[fi_selected, fi_sel_state])
                fi_selected.change(lambda p: p or "", inputs=[fi_selected],
                                   outputs=[fi_sel_state])

                fi_mkdir_btn.click(_fi_mkdir, inputs=[fi_mkdir_path], outputs=[fi_status])
                fi_upload_btn.click(_fi_upload, inputs=[fi_upload, fi_upload_dest],
                                    outputs=[fi_status])
                fi_delete_btn.click(_fi_delete, inputs=[fi_delete_path], outputs=[fi_status])
                fi_index_btn.click(_fi_index, inputs=[fi_index_path], outputs=[fi_index_log])

                # ── viewer / download ─────────────────────────────────────────
                gr.Markdown(t["files_view_header"], elem_id="fi_view_section")
                with gr.Row():
                    fi_view_path = gr.Textbox(
                        label=t["files_view_path_label"],
                        placeholder=t["files_view_placeholder"],
                        scale=4,
                    )
                    fi_view_btn = gr.Button(t["files_view_btn"], scale=1)

                with gr.Tabs():
                    with gr.Tab(t["files_code_tab"]):
                        fi_view_code = gr.Code(language=None, lines=35,
                                               interactive=False)
                    with gr.Tab(t["files_rendered_tab"]):
                        fi_view_render = gr.Markdown(value="")
                        fi_view_html   = gr.HTML(value="", visible=False)

                fi_download    = gr.DownloadButton("Download file", visible=False)
                fi_view_status = gr.Markdown("")

                def _fi_view(path):
                    path = path.strip()
                    if not path:
                        return (gr.Code(value="", language=None),
                                gr.Markdown(""), gr.HTML("", visible=False),
                                gr.update(visible=False), "")
                    try:
                        p = _resolve_safe(path)
                        if not p.is_file():
                            return (gr.Code(value=f"Not a file: {path}"),
                                    gr.Markdown(""), gr.HTML("", visible=False),
                                    gr.update(visible=False),
                                    f"Error: `{path}` is not a file")
                        content = p.read_text(encoding="utf-8", errors="replace")
                        ext  = p.suffix.lower()
                        _LMAP = {
                            ".py":"python", ".js":"javascript", ".ts":"typescript",
                            ".java":"java", ".rs":"rust", ".go":"go",
                            ".yaml":"yaml", ".yml":"yaml", ".json":"json",
                            ".sh":"bash", ".css":"css", ".sql":"sql",
                            ".xml":"xml", ".html":"html", ".htm":"html",
                            ".toml":"toml", ".md":"markdown",
                        }
                        lang = _LMAP.get(ext, "")
                        code_upd = gr.Code(value=content, language=lang or None)
                        if ext in (".md", ".markdown"):
                            rmd  = gr.Markdown(value=content)
                            rhtm = gr.HTML("", visible=False)
                        elif ext in (".html", ".htm"):
                            rmd  = gr.Markdown("", visible=False)
                            rhtm = gr.HTML(value=content, visible=True)
                        else:
                            rmd  = gr.Markdown(f"_No rendered preview for `{ext}` files._")
                            rhtm = gr.HTML("", visible=False)
                        dl = gr.DownloadButton(value=str(p), visible=True)
                        return code_upd, rmd, rhtm, dl, \
                               t["viewing_file"].format(name=p.name, n=len(content))
                    except Exception as e:
                        return (gr.Code(value=f"Error: {e}"),
                                gr.Markdown(""), gr.HTML("", visible=False),
                                gr.update(visible=False), f"Error: {e}")

                fi_view_btn.click(
                    _fi_view,
                    inputs=[fi_view_path],
                    outputs=[fi_view_code, fi_view_render, fi_view_html,
                             fi_download, fi_view_status],
                )

                def _fi_quick_view(p):
                    p = (p or "").strip()
                    if not p:
                        return (gr.update(), gr.update(), gr.update(),
                                gr.update(), gr.update(), gr.update())
                    return (p,) + _fi_view(p)

                fi_to_scan.click(
                    lambda p: str(VYRII_HOME / p) if (p or "").strip() else "",
                    inputs=[fi_sel_state], outputs=[sc_path],
                    js=_JS_SWITCH_TO_SCAN,
                )
                fi_to_index.click(
                    lambda p: p or "",
                    inputs=[fi_sel_state], outputs=[fi_index_path],
                    js=_JS_SCROLL_TO_INDEX,
                )
                fi_to_view.click(
                    _fi_quick_view,
                    inputs=[fi_sel_state],
                    outputs=[fi_view_path, fi_view_code, fi_view_render,
                             fi_view_html, fi_download, fi_view_status],
                    js=_JS_SCROLL_TO_VIEW,
                )

            # ══════════════════════════════════════════════════════════════════
            # Scheduler
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["scheduler_tab"]):
                gr.Markdown(t["scheduler_desc"])
                from . import scheduler as _sch

                sch_table   = gr.Markdown(value=_sch.tasks_as_table(_sch.load_tasks()))
                sch_refresh = gr.Button("Refresh table", size="sm")

                with gr.Row():
                    sch_sel_id  = gr.Textbox(label=t["sch_task_id_label"], scale=3)
                    sch_run_now = gr.Button(t["sch_run_now_btn"], scale=1)
                    sch_toggle  = gr.Button(t["sch_toggle_btn"], scale=1)
                    sch_delete  = gr.Button(t["sch_delete_btn"], variant="stop", scale=1)
                sch_action_status = gr.Markdown("")

                with gr.Accordion(t["scheduler_add_section"], open=False):
                    sch_name    = gr.Textbox(label=t["sch_name_label"],
                                             placeholder=t["sch_name_placeholder"])
                    sch_command = gr.Textbox(
                        label=t["sch_command_label"],
                        placeholder=t["sch_command_placeholder"],
                        lines=2,
                    )
                    sch_stype   = gr.Dropdown(
                        choices=["daily", "weekly", "monthly",
                                 "interval_minutes", "interval_hours"],
                        value="daily",
                        label=t["sch_stype_label"],
                    )
                    with gr.Row():
                        sch_time     = gr.Textbox(label=t["sch_time_label"], value="08:00",
                                                  placeholder="08:00", scale=2)
                        sch_dow      = gr.Dropdown(
                            choices=["mon","tue","wed","thu","fri","sat","sun"],
                            value="mon", label=t["sch_dow_label"], visible=False, scale=2,
                        )
                        sch_interval = gr.Number(label=t["sch_interval_label"], value=60,
                                                 visible=False, precision=0, scale=2)
                    sch_create = gr.Button(t["sch_create_btn"], variant="primary")
                    sch_status = gr.Markdown("")

                with gr.Accordion(t["scheduler_logs_section"], open=False):
                    sch_log_id  = gr.Textbox(label=t["sch_task_id_label"])
                    sch_log_btn = gr.Button(t["sch_load_logs_btn"], size="sm")
                    sch_log_sel = gr.Dropdown(choices=[], label=t["sch_log_sel_label"],
                                              allow_custom_value=False)
                    sch_log_out = gr.Code(language=None, lines=25, interactive=False)

                def _sch_toggle_fields(stype):
                    show_time     = stype in ("daily", "weekly", "monthly")
                    show_dow      = stype == "weekly"
                    show_interval = stype in ("interval_minutes", "interval_hours")
                    return (gr.update(visible=show_time),
                            gr.update(visible=show_dow),
                            gr.update(visible=show_interval))

                sch_stype.change(
                    _sch_toggle_fields, inputs=[sch_stype],
                    outputs=[sch_time, sch_dow, sch_interval],
                )
                sch_refresh.click(
                    lambda: _sch.tasks_as_table(_sch.load_tasks()),
                    outputs=[sch_table],
                )

                def _sch_create(name, command, stype, time_str, dow, interval):
                    if not name.strip() or not command.strip():
                        return gr.update(), "Name and command are required."
                    try:
                        parts = time_str.strip().split(":")
                        h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
                    except Exception:
                        h, m = 8, 0
                    _sch.add_task(name.strip(), command.strip(), stype,
                                  h, m, dow, int(interval))
                    return (_sch.tasks_as_table(_sch.load_tasks()),
                            t["task_created"].format(name=name))

                sch_create.click(
                    _sch_create,
                    inputs=[sch_name, sch_command, sch_stype,
                            sch_time, sch_dow, sch_interval],
                    outputs=[sch_table, sch_status],
                )

                def _sch_find_full_id(prefix):
                    prefix = prefix.strip()
                    for _task in _sch.load_tasks():
                        if _task["id"].startswith(prefix):
                            return _task["id"]
                    return None

                def _sch_delete(prefix):
                    tid = _sch_find_full_id(prefix)
                    if not tid:
                        return gr.update(), t["task_not_found"].format(prefix=prefix)
                    _sch.remove_task(tid)
                    return _sch.tasks_as_table(_sch.load_tasks()), t["task_deleted"]

                def _sch_toggle(prefix):
                    tid = _sch_find_full_id(prefix)
                    if not tid:
                        return gr.update(), t["task_not_found"].format(prefix=prefix)
                    enabled = _sch.toggle_task(tid)
                    return _sch.tasks_as_table(_sch.load_tasks()), t["task_enabled"] if enabled else t["task_disabled"]

                def _sch_run_now(prefix):
                    tid = _sch_find_full_id(prefix)
                    if not tid:
                        return t["task_not_found"].format(prefix=prefix)
                    _sch.run_now(tid)
                    return t["running_background"]

                sch_delete.click(_sch_delete, inputs=[sch_sel_id],
                                 outputs=[sch_table, sch_action_status])
                sch_toggle.click(_sch_toggle, inputs=[sch_sel_id],
                                 outputs=[sch_table, sch_action_status])
                sch_run_now.click(_sch_run_now, inputs=[sch_sel_id],
                                  outputs=[sch_action_status])

                def _sch_load_logs(prefix):
                    tid = _sch_find_full_id(prefix)
                    if not tid:
                        return gr.Dropdown(choices=[], value=None)
                    logs = _sch.get_task_logs(tid)
                    choices = [str(p) for p in logs]
                    return gr.Dropdown(choices=choices,
                                       value=choices[0] if choices else None)

                def _sch_show_log(log_path):
                    if not log_path:
                        return ""
                    try:
                        return _pathlib.Path(log_path).read_text(encoding="utf-8",
                                                                  errors="replace")
                    except Exception as e:
                        return t["error_reading_log"].format(e=e)

                sch_log_btn.click(_sch_load_logs, inputs=[sch_log_id],
                                  outputs=[sch_log_sel])
                sch_log_sel.change(_sch_show_log, inputs=[sch_log_sel],
                                   outputs=[sch_log_out])

            # Profile
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["profile_tab"]):
                gr.Markdown(t["profile_desc"])
                with gr.Row():
                    sp_saved_dd  = gr.Dropdown(
                        choices=_parallel_mod.profile_names(), label=t["profile_saved_label"],
                        allow_custom_value=False, scale=3,
                    )
                    sp_load_btn   = gr.Button(t["profile_load_btn"],   size="sm", scale=1)
                    sp_delete_btn = gr.Button(t["profile_delete_btn"], size="sm", scale=1, variant="stop")

                sp_name    = gr.Textbox(label=t["profile_name_label"],    placeholder=t["profile_name_placeholder"])
                sp_comment = gr.Textbox(label=t["profile_comment_label"], placeholder=t["profile_comment_placeholder"])

                gr.Markdown(t["profile_workers_header"])
                sp_worker_rows     = []
                sp_worker_hosts    = []
                sp_worker_models   = []
                sp_worker_providers = []
                for _spi in range(8):
                    with gr.Row(visible=(_spi == 0)) as _spr:
                        _sph = gr.Textbox(label=f"Host {_spi+1}",
                                          placeholder="localhost:11434", scale=3)
                        _spm = gr.Textbox(label=f"Model {_spi+1}",
                                          placeholder="qwen2.5:7b", scale=3)
                        _spp = gr.Dropdown(choices=["ollama", "openai"], value="ollama",
                                           label="Provider", scale=1)
                    sp_worker_rows.append(_spr)
                    sp_worker_hosts.append(_sph)
                    sp_worker_models.append(_spm)
                    sp_worker_providers.append(_spp)

                sp_worker_count = gr.State(1)

                with gr.Row():
                    sp_add_worker = gr.Button(t["profile_add_worker_btn"],    size="sm", scale=1)
                    sp_rem_worker = gr.Button(t["profile_rem_worker_btn"], size="sm", scale=1)
                    sp_export_btn = gr.DownloadButton(t["profile_export_btn"],
                                                      size="sm", scale=1)
                with gr.Row():
                    sp_save_btn = gr.Button(t["profile_save_btn"], variant="primary")
                sp_status = gr.Markdown("")

                # ── handlers ─────────────────────────────────────────────────

                def _sp_add_worker(count):
                    import gradio as _gr
                    new_count = min(count + 1, 8)
                    return [new_count] + [_gr.update(visible=(i < new_count)) for i in range(8)]

                def _sp_rem_worker(count):
                    import gradio as _gr
                    new_count = max(count - 1, 1)
                    return [new_count] + [_gr.update(visible=(i < new_count)) for i in range(8)]

                def _sp_load(name):
                    import gradio as _gr
                    profile = _parallel_mod.get_profile(name or "")
                    if not profile:
                        return [_gr.update()] * (2 + 8 * 3 + 8 + 1)
                    workers = profile.get("workers", [])
                    n = len(workers)
                    hosts, models, providers = [], [], []
                    for i in range(8):
                        if i < n:
                            w = workers[i]
                            hosts.append(w.get("host", ""))
                            models.append(w.get("model", ""))
                            providers.append(w.get("provider", "ollama"))
                        else:
                            hosts.append("")
                            models.append("")
                            providers.append("ollama")
                    out = [profile["name"], profile.get("comment", "")] + hosts + models + providers
                    for i in range(8):
                        out.append(_gr.update(visible=(i < max(n, 1))))
                    out.append(max(n, 1))
                    return out

                def _sp_save(name, comment, count, *worker_vals):
                    hosts     = list(worker_vals[0:8])
                    models    = list(worker_vals[8:16])
                    providers = list(worker_vals[16:24])
                    name = (name or "").strip()
                    if not name:
                        return t["name_required"], gr.update()
                    workers = []
                    for i in range(int(count)):
                        h = (hosts[i] or "").strip()
                        m = (models[i] or "").strip()
                        if h and m:
                            workers.append({"host": h, "model": m,
                                            "provider": providers[i] or "ollama"})
                    if not workers:
                        return t["worker_required"], gr.update()
                    _parallel_mod.upsert_profile({
                        "name": name, "comment": comment or "", "workers": workers
                    })
                    return (t["profile_saved"].format(name=name, n=len(workers)),
                            gr.update(choices=_parallel_mod.profile_names()))

                def _sp_delete(name):
                    import gradio as _gr
                    if name:
                        _parallel_mod.delete_profile(name)
                    return (t["deleted_profile"].format(name=name) if name else "",
                            _gr.update(choices=_parallel_mod.profile_names(), value=None))

                def _sp_export():
                    content = _parallel_mod.export_1bcoder(_parallel_mod.load_profiles())
                    export_path = VYRII_HOME / "exports" / "profiles.txt"
                    export_path.write_text(content, encoding="utf-8")
                    return str(export_path)

                sp_add_worker.click(
                    _sp_add_worker, inputs=[sp_worker_count],
                    outputs=[sp_worker_count] + sp_worker_rows,
                )
                sp_rem_worker.click(
                    _sp_rem_worker, inputs=[sp_worker_count],
                    outputs=[sp_worker_count] + sp_worker_rows,
                )
                sp_load_btn.click(
                    _sp_load, inputs=[sp_saved_dd],
                    outputs=[sp_name, sp_comment]
                             + sp_worker_hosts + sp_worker_models + sp_worker_providers
                             + sp_worker_rows + [sp_worker_count],
                )
                sp_save_btn.click(
                    _sp_save,
                    inputs=[sp_name, sp_comment, sp_worker_count]
                           + sp_worker_hosts + sp_worker_models + sp_worker_providers,
                    outputs=[sp_status, sp_saved_dd],
                )
                sp_delete_btn.click(
                    _sp_delete, inputs=[sp_saved_dd],
                    outputs=[sp_status, sp_saved_dd],
                )
                sp_export_btn.click(_sp_export, outputs=[sp_export_btn])

            # ══════════════════════════════════════════════════════════════════
            # Settings
            # ══════════════════════════════════════════════════════════════════
            with gr.Tab(t["settings_tab"]):
                gr.Markdown(t["settings_request_header"])
                with gr.Row():
                    s_timeout.render()
                    with gr.Column(scale=2):
                        gr.Markdown(t["settings_timeout_desc"])

                gr.Markdown(t["settings_theme_header"])
                with gr.Row():
                    s_theme = gr.Dropdown(
                        choices=_THEME_NAMES,
                        value=_theme_name,
                        label=t["settings_theme_label"],
                        scale=2,
                    )
                    s_theme_save = gr.Button(t["settings_theme_save_btn"], scale=1)
                s_theme_hint = gr.Markdown("")

                def _do_save_theme(name):
                    _save_config({"theme": name})
                    return t["theme_saved"].format(name=name)

                s_theme_save.click(
                    _do_save_theme, inputs=[s_theme], outputs=[s_theme_hint]
                )

                gr.Markdown(t["settings_conn_header"])
                _conn_saved_text = (
                    f"`{_saved_model}` @ `{_saved_url}` ({_saved_backend})"
                    if _saved_model
                    else t["settings_conn_none"]
                )
                s_conn_current = gr.Markdown(t["settings_conn_current"].format(val=_conn_saved_text))
                with gr.Row():
                    s_save_conn_btn = gr.Button(t["settings_conn_save_btn"], scale=2)
                s_conn_hint = gr.Markdown("")

                def _do_save_conn(url, model, backend):
                    _save_config({"saved_url": url, "saved_model": model,
                                  "saved_backend": backend})
                    saved_text = f"`{model}` @ `{url}` ({backend})"
                    return (t["settings_conn_current"].format(val=saved_text),
                            t["settings_conn_saved"].format(val=saved_text))

                s_save_conn_btn.click(
                    _do_save_conn,
                    inputs=[g_url, g_model, g_backend],
                    outputs=[s_conn_current, s_conn_hint],
                )

                gr.Markdown(t["settings_lang_header"])
                with gr.Row():
                    s_lang = gr.Dropdown(
                        choices=_i18n_mod.LANGS, value=lang,
                        label=t["settings_lang_label"], scale=2,
                    )
                    s_lang_save = gr.Button(t["settings_lang_save_btn"], scale=1)
                s_lang_hint = gr.Markdown("")

                def _do_save_lang(name):
                    _save_config({"lang": name})
                    return t["lang_saved"].format(lang=name)

                s_lang_save.click(_do_save_lang, inputs=[s_lang], outputs=[s_lang_hint])

                gr.Markdown(t["settings_auth_header"])
                with gr.Row():
                    s_auth_user = gr.Textbox(
                        value=_cfg_now.get("auth_user", "admin"),
                        label=t["settings_auth_user_label"], scale=2,
                    )
                    s_auth_pass = gr.Textbox(
                        value="", label=t["settings_auth_pass_label"],
                        type="password", scale=2,
                    )
                    s_auth_save   = gr.Button(t["settings_auth_save_btn"], scale=1)
                    s_auth_logout = gr.Button(t["settings_auth_logout_btn"], scale=1, variant="stop")
                s_auth_hint = gr.Markdown("")

                def _do_save_auth(user, pwd):
                    user = (user or "").strip()
                    if not user or not pwd:
                        return "Username and password are required."
                    _save_config({"auth_user": user, "auth_pass": pwd})
                    return t["settings_auth_saved"]

                s_auth_save.click(_do_save_auth, inputs=[s_auth_user, s_auth_pass],
                                  outputs=[s_auth_hint])
                s_auth_logout.click(None, js="() => { window.location.href = '/logout'; }")

                gr.Markdown(t["settings_control_header"])
                with gr.Row():
                    s_restart_btn  = gr.Button(t["settings_restart_btn"], variant="secondary", size="sm", scale=1)
                    s_reboot_btn   = gr.Button(t["settings_reboot_btn"],     variant="stop",      size="sm", scale=1)
                    s_shutdown_btn = gr.Button(t["settings_shutdown_btn"],   variant="stop",      size="sm", scale=1)
                with gr.Row():
                    s_restart_delay = gr.Number(
                        value=_restart_delay, minimum=2, maximum=60, step=1,
                        label=t["settings_reload_delay_label"], precision=0, scale=3,
                    )
                    _worker_timeout_saved = int(_cfg_now.get("worker_timeout", 300))
                    s_worker_timeout = gr.Number(
                        value=_worker_timeout_saved, minimum=60, maximum=3600, step=60,
                        label=t["dam_worker_timeout_label"], precision=0, scale=3,
                    )
                    s_delay_save = gr.Button("Save", size="sm", scale=1)
                s_delay_hint = gr.Markdown("")

                def _do_save_delay(d, wt):
                    _save_config({"restart_delay": int(d), "worker_timeout": int(wt)})
                    return f"Saved: restart {int(d)} s  |  worker timeout {int(wt)} s"

                s_delay_save.click(_do_save_delay, inputs=[s_restart_delay, s_worker_timeout],
                                   outputs=[s_delay_hint])

                s_sys_confirm = gr.Checkbox(
                    label=t["settings_confirm_label"],
                    value=False,
                )
                s_sys_status = gr.Markdown("")

                s_restart_btn.click(
                    lambda d: _sys_restart(d if d else 8),
                    inputs=[s_restart_delay],
                    outputs=[s_sys_status],
                    js="""(delay) => {
  const ms = Math.max(2, parseInt(delay) || 8) * 1000;
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;z-index:99999;background:#1e293b;padding:10px 16px;box-shadow:0 2px 8px rgba(0,0,0,.5)';
  const lbl = document.createElement('div');
  lbl.style.cssText = 'color:#e2e8f0;font-size:13px;margin-bottom:6px;font-family:monospace';
  const track = document.createElement('div');
  track.style.cssText = 'width:100%;height:8px;background:#334155;border-radius:4px;overflow:hidden';
  const fill = document.createElement('div');
  fill.style.cssText = 'height:100%;width:0%;background:#3b82f6;border-radius:4px';
  track.appendChild(fill);
  overlay.appendChild(lbl);
  overlay.appendChild(track);
  document.body.appendChild(overlay);
  const start = Date.now();
  const tick = setInterval(() => {
    const elapsed = Date.now() - start;
    const pct = Math.min(100, (elapsed / ms) * 100);
    const secs = Math.max(0, Math.ceil((ms - elapsed) / 1000));
    fill.style.width = pct + '%';
    lbl.textContent = 'Restarting vyrii… reloading in ' + secs + ' s';
    if (elapsed >= ms) { clearInterval(tick); window.location.reload(); }
  }, 100);
}""",
                )
                s_reboot_btn.click(
                    _sys_reboot, inputs=[s_sys_confirm], outputs=[s_sys_status]
                )
                s_shutdown_btn.click(
                    _sys_shutdown, inputs=[s_sys_confirm], outputs=[s_sys_status]
                )

        # ── "Add to chat" panel handlers (registered after all components) ──
        atc_cancel.click(lambda: gr.update(visible=False), outputs=[add_ctx_panel])
        atc_add.click(
            _add_to_chat,
            inputs=[ctx_buffer, ctx_sources, atc_is_new, atc_mode, atc_n, atc_display,
                    chatbot, s_cid, s_ctx, s_hidden_ctx,
                    g_model, g_url, g_backend, s_timeout],
            outputs=[chatbot, s_cid, s_ctx, ctx_lbl, s_hidden_ctx, hist_dd, add_ctx_panel],
            js=_JS_SWITCH_TO_CHAT,
        )

        app.load(
            lambda t: gr.Number(value=int(t)) if t else gr.update(),
            inputs=[g_saved_timeout], outputs=[s_timeout],
        )
        s_timeout.change(lambda t: int(t), inputs=[s_timeout], outputs=[g_saved_timeout])

        # ── start background scheduler ────────────────────────────────────────
        try:
            from . import scheduler as _sch_startup
            _sch_startup.start_scheduler()
        except Exception:
            pass

    app.queue()
    app._vyrii_theme = _active_theme
    return app


def _vyrii_auth(username: str, password: str) -> bool:
    """Callable for Gradio auth= — reads credentials from ~/.vyrii/config.json."""
    import json as _j
    try:
        cfg = _j.loads((_pathlib.Path.home() / ".vyrii" / "config.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    return username == cfg.get("auth_user", "admin") and password == cfg.get("auth_pass", "admin")


def main(
    port: int = 4896,
    host: str = "0.0.0.0",
    ollama_url: str = _DEFAULT_OLLAMA,
    openai_url: str = _DEFAULT_OPENAI,
    lang: str = "en",
    startup_model: str | None = None,
    auth: bool = False,
):
    import gradio as gr
    app = build_app(ollama_url=ollama_url, openai_url=openai_url, lang=lang,
                    startup_model=startup_model)
    print(f"vyrii — open: http://localhost:{port}")
    app.launch(server_name=host, server_port=port, head=_HEAD_HTML,
               auth=_vyrii_auth if auth else None,
               theme=getattr(app, "_vyrii_theme", None))
