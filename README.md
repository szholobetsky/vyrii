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
Streaming conversation with a local LLM. Supports multiple saved sessions, context compaction, file loading, and export. A spinner shows when the model is generating — no silent waits.

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

To use a custom UI port:

```bash
vyrii --ui 8001
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

## Requirements

| Dependency | Version |
|---|---|
| Python | ≥ 3.10 |
| gradio | ≥ 6.0 |
| requests | ≥ 2.28 |
| [Ollama](https://ollama.com) | any recent version |

Optional:
- `fastapi` + `uvicorn` — for the REST API server (`pip install vyrii[api]`)
- `lxml_html_clean` — for cleaner HTML extraction in web tools (`pip install vyrii[html]`)
- `simargl` — for RAG and WebIndex (`pip install simargl`)
- `argostranslate` — for offline translation mini mode
- `ctranslate2` + `sentencepiece` — for NLLB-200 offline translation

---

## Running

```bash
vyrii
```

Or with options:

```bash
vyrii --host http://localhost:11434   # Ollama (default)
vyrii --host openai://localhost:1234  # LMStudio
vyrii --port 8080
vyrii --lang uk                       # Ukrainian UI
vyrii --theme GithubDark
```

The web UI opens at `http://localhost:4896` by default.

### With API server

```bash
vyrii --api
```

Starts both the Gradio UI and a FastAPI server on port 8002:

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
