![vyrii](images/vyrii2.png)

# vyrii

Local AI web UI — chat, translate, web research, deep document generation, file management, and RAG — powered by Ollama or any OpenAI-compatible backend.

The name comes from the Ukrainian word *вирій* — the mythical warm land where birds migrate in winter. A quiet, self-contained place where intelligence lives locally, without the cloud.

---

## What it is

**vyrii** is a browser-based interface that runs entirely on your hardware. It connects to a local LLM (Ollama, LMStudio, or any `/v1/chat/completions` endpoint) and provides a suite of AI tools through a clean Gradio UI — without sending your data to any cloud service.

It also exposes an OpenAI-compatible API (`/v1/chat/completions`) and a set of vyrii-specific endpoints (`/vyrii/*`), so it can serve as a backend for other tools in the SIMARGL toolkit.

vyrii continues the same philosophy as **1bcoder**: give users with modest hardware a way to work effectively with ultra-small models — from 0.5B to 7B parameters, within a 4 000-token context window. There are many reasons a person may be forced to rely on small local models: no internet connection, a company or institutional policy that prohibits cloud AI, running on battery in the field, working from a shelter or restricted environment. vyrii has no autonomous agents — but every tool works reliably with `gemma3:1b` or `qwen3:1.7b`. The premise is the same: the smallest model available right now is enough to be useful.

---

## Features

### Chat
Streaming conversation with a local LLM. Supports multiple saved sessions, context compaction, file loading, and export. A spinner shows when the model is generating — no silent waits. A **Stop** button interrupts generation at any time; whatever was already generated is kept in the conversation.

Chat history is saved automatically to `~/.vyrii/history.db` and exported as markdown to `~/.vyrii/ctx/`. This applies to both the Gradio UI and the lightweight HTML UI.

### Translate
On-the-fly translation in four modes:

| Mode | Backend | Privacy |
|---|---|---|
| `llm` | Local model via Ollama | Full — nothing leaves your machine |
| `mini` | argostranslate (~100 MB per pair) | Full |
| `offline` | NLLB-200 (ctranslate2, ~600 MB) | Full |
| `online` | Google Translate | ⚠ Sends text to Google |

### Obfuscate / Deobfuscate
Replace sensitive terms before sending to a cloud AI, then restore the response. Uses a YAML glossary in two modes: smart (LLM-based, handles grammar) and force (instant direct substitution).

### RAG
Search a simargl semantic index by project. Ask the LLM to synthesise an answer from retrieved chunks. Works with indexes built by the simargl tool or the WebIndex tab.

### MCP
Connect external tool servers via the Model Context Protocol (filesystem, web, git, database, browser…). Call tools directly and pipe results into the chat.

### Team
Parallel LLM workers with different aspects or profiles. Each worker answers a facet of the same question; results are combined (join or compact) into a single context block.

### WebAsk
Ask a question about a URL or let vyrii search the web (DuckDuckGo), fetch the top N pages, and synthesise an answer. Shows live per-page fetch progress.

### WebAnalys
Deep multi-page web research: gather N pages on a topic, rank by relevance, summarise collectively.

### WebCrawl
Crawl a website up to N pages deep. Modes: collect links, LLM summarise, extract structured data (table columns), export as markdown. Optionally indexes crawled content for RAG.

### WebIndex
Crawl a URL and index the content with simargl for later RAG retrieval.

### DeepAgent MD
Multi-section document generation from a task description. Supports web augmentation, RAG context, custom presets (quick/balanced/deep), plan and list injection, and sliding-window compaction (`--fix` / `--scan`) for large outputs.

### Scan
Compact a large file or entire directory into a single summary markdown. Works chunk by chunk — optionally filters chunks by a query (filter mode) or summarises everything (general mode). Supports recursive compaction until the output fits a target size.

### Files
File manager for `~/.vyrii/`. Browse the directory tree, select files, copy paths to Scan or Index, upload, create folders, delete, view with syntax highlighting, and index directories with simargl.

### Scheduler
Background task scheduler. Create recurring or one-off tasks (daily, weekly, interval), toggle, run immediately, and tail logs.

### Profile
Manage parallel worker profiles for the Team tab. Each profile defines a set of models/hosts with aspect roles.

### Settings
Switch theme (including GithubDark, Dracula, Solarized), language (EN/UK), timeout, and backend connection. Changes apply on the next session start.

---

## Quick install

### Option 1 — PyPI

```bash
pip install vyrii
```

### Option 2 — Clone

```bash
git clone https://github.com/szholobetsky/vyrii.git
cd vyrii
pip install -e .
```

---

## Quick start

```bash
# 1. Install Ollama and pull a small model
ollama pull qwen3:1.7b

# 2. Install vyrii
pip install vyrii

# 3. Launch
vyrii
```

Open `http://localhost:4896` — the Chat tab is ready. Pick `qwen3:1.7b` in the model selector and start talking.

Custom port:

```bash
vyrii --ui 8001
```

No Gradio (HTML UI only, e.g. on Termux or a headless server):

```bash
vyrii --api 8000
# open http://localhost:8000/ui/
```

For Ukrainian UI:

```bash
vyrii --lang uk
```

For a dark theme:

```bash
vyrii --theme GithubDark
```

---

## Two UI modes

vyrii offers two independent interfaces — use one or both at the same time:

### Gradio UI (`--ui`)

The full-featured Gradio interface. Good for desktop use and interactive workflows.

```bash
vyrii               # Gradio UI on http://localhost:4896
vyrii --ui 8001     # custom port
```

> **Note:** Gradio has a heavy Python dependency stack. On constrained environments — Android Termux, low-RAM systems, single-board computers — Gradio may fail to start or render poorly. Use the HTML UI in those cases.

### Lightweight HTML UI (`--api` + `/ui/`)

A self-contained single-page app served at `/ui/` by the FastAPI server. No Gradio required — runs in any browser including mobile Chrome. Ideal for headless servers, Termux, and LAN access from a phone.

```bash
vyrii --api 8000
# open http://<your-ip>:8000/ui/
```

All tabs (Chat, Translate, RAG, WebAsk, WebCrawl, Team, etc.) are available in the HTML UI.

### Both at the same time

```bash
vyrii --ui 8001 --api 8000
# Gradio: http://localhost:8001
# HTML UI: http://localhost:8000/ui/
```

---

## Requirements

| Dependency | Version |
|---|---|
| Python | ≥ 3.10 |
| gradio | ≥ 6.0 |
| requests | ≥ 2.28 |
| [Ollama](https://ollama.com) | any recent version |

Optional:
- `lxml` — for web crawl and HTML extraction (`pip install 'vyrii[web]'`)
- `lxml_html_clean` — for cleaner HTML extraction (`pip install 'vyrii[html]'`)
- `fastapi` + `uvicorn` — for the REST API server (`pip install 'vyrii[api]'`)
- `simargl` — for RAG and WebIndex (`pip install simargl`)
- `argostranslate` — for offline translation mini mode
- `ctranslate2` + `sentencepiece` — for NLLB-200 offline translation

### Termux (Android)

```bash
pkg update
pkg install python

# core install — chat, translate, file manager, RAG (no web crawl)
pip install vyrii

# optional: add web crawl support
pkg install python-lxml
```

Chat, translate, web search, and file management work without lxml. Web crawl requires lxml — install it via `pkg install python-lxml` (pre-compiled for ARM, avoids source build).

**Gradio is hard to run on Termux.** Use the lightweight HTML UI instead:

```bash
# in Termux:
OLLAMA_HOST=0.0.0.0:11434 ollama serve &
vyrii --api 8000 --bind 0.0.0.0

# open in any browser:
# http://127.0.0.1:8000/ui/          ← same device
# http://192.168.x.x:8000/ui/        ← from any device on the same LAN
```

---

## Running

```bash
vyrii
```

Common options:

```bash
vyrii --host http://localhost:11434    # Ollama (default)
vyrii --host openai://localhost:1234   # LMStudio or any OpenAI-compatible server
vyrii --lang uk                        # Ukrainian UI
vyrii --theme GithubDark
vyrii --model qwen3:1.7b               # default model on startup
```

### Network binding

By default vyrii listens on `0.0.0.0` (all interfaces). To restrict to localhost only:

```bash
vyrii --bind 127.0.0.1 --ui 4896
vyrii --bind 127.0.0.1 --api 8000
```

To expose on the LAN (e.g., access from a phone):

```bash
vyrii --bind 0.0.0.0 --api 8000
```

### Authentication (`--auth`)

Authentication is **opt-in**. By default vyrii runs without any login — suitable for home use or LAN cable connections where access control is handled at the network level.

Add `--auth` to enable HTTP Basic Auth on all API endpoints:

```bash
# home / LAN cable — no login needed
vyrii --api 8000

# office / phone over Wi-Fi — protect with a password
vyrii --api 8000 --ui 8001 --auth
```

Default credentials are `admin` / `admin`. Change them via the **Settings → Authentication** section in either the Gradio UI or the HTML UI — no display or terminal access required. Credentials are stored in `~/.vyrii/config.json`.

Static UI files at `/ui/` are always served without auth so the login page loads even before credentials are entered.

### API endpoints

When running with `--api`, the following endpoints are available:

- `GET  /v1/models` — list available models
- `POST /v1/chat/completions` — OpenAI-compatible chat (streaming + non-streaming)
- `POST /vyrii/translate`
- `POST /vyrii/webask`
- `POST /vyrii/webcrawl`
- `POST /vyrii/deepagent`
- `POST /vyrii/webanalys`
- `GET  /vyrii/files/list`
- `POST /vyrii/files/mkdir`
- `POST /vyrii/files/upload`
- `DELETE /vyrii/files`
- `POST /vyrii/files/index`

Interactive API docs: `http://localhost:<port>/docs`

---

## Project layout

```
vyrii/
├── vyrii/
│   ├── app.py           # Gradio UI — all tabs and handlers
│   ├── api.py           # FastAPI REST server
│   ├── engine.py        # LLM streaming, model listing, smart context
│   ├── adapter.py       # ChatAdapter — unified interface for flows
│   ├── tools.py         # fetch_text, HTML stripping
│   ├── history.py       # SQLite chat history
│   ├── scheduler.py     # background task scheduler
│   ├── parallel.py      # parallel profile management
│   ├── mcp_client.py    # MCP server subprocess management
│   ├── i18n.py          # EN / UK string tables
│   ├── __main__.py      # CLI entry point
│   └── flows/
│       ├── webask.py    # web search + answer flow
│       ├── webcrawl.py  # site crawler flow
│       ├── webanalys.py # multi-page web research flow
│       ├── webindex.py  # crawl + simargl index flow
│       ├── deepagent_md.py  # multi-section document generation
│       ├── scan.py      # large-file compaction flow
│       ├── obfuscate.py
│       └── deobfuscate.py
├── images/
│   └── vyrii2.png
├── pyproject.toml
├── README.md
└── .gitignore

~/.vyrii/                # user data (created on first run)
    ├── crawl/           # WebCrawl output
    ├── files/           # user files (for RAG indexing)
    ├── .simargl/        # simargl RAG index
    ├── .1bcoder/scan/   # Scan flow output (compact_N.md)
    ├── config.json      # saved settings (theme, lang, timeout)
    ├── scheduler.json   # scheduler tasks
    └── history.db       # chat history (SQLite)
```

---

## Part of the SIMARGL toolkit

vyrii is one of five tools that together form an **intellectual development support system**:

| Tool | Role |
|---|---|
| **[simargl](https://github.com/szholobetsky/simargl)** | Task-to-code retrieval — given a task description, finds which files and modules are likely affected, using semantic similarity over git history |
| **[svitovyd](https://github.com/szholobetsky/svitovyd)** | Project map — scans any codebase and produces a structural map of definitions and cross-file dependencies; exposes it as an MCP server |
| **[1bcoder](https://github.com/szholobetsky/1bcoder)** | AI coding assistant for small local models — surgical context management, agents, parallel inference, proc scripts |
| **[yasna](https://github.com/szholobetsky/yasna)** | Session memory — indexes conversations from all AI agents so you can find what was discussed, when, and where |
| **[radogast](https://github.com/szholobetsky/radogast)** | Context drift monitor — measures how far an AI agent's conversation has drifted from the original task |

- **simargl** answers: *what code is related to this task?*
- **svitovyd** answers: *how is the code structured and what depends on what?*
- **1bcoder** answers: *how do I work with local models efficiently?*
- **vyrii** answers: *how do I access all of this through a browser?*
- **yasna** answers: *where did I already discuss this?*
- **radogast** answers: *is the AI agent still on track toward the goal?*

Together they cover the full development loop: understand the codebase, find relevant history, work with AI locally, access everything through a web UI, remember what was decided, and verify the context stays on target.

---

**(c) 2026 Stanislav Zholobetskyi, Oleh Andriichuk**
Institute for Information Recording, National Academy of Sciences of Ukraine, Kyiv
*PhD research: «Intelligent Technology for Software Development and Maintenance Support»*
