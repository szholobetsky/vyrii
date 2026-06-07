# MIRROR — 1bcoder as a Web Terminal

Concept: instead of maintaining vyrii as a separate tool with its own flows and adapters,
expose 1bcoder through a browser-based terminal interface. The browser becomes a "mirror"
of the CLI session running locally in the correct project cwd.

## Core Idea

```
1bcoder --web --port 4896 --cwd C:\Project\MyProject
    └── FastAPI/aiohttp server (runs in project cwd)
        ├── WebSocket /ws    ← stdin/stdout bridge
        ├── GET /            ← HTML page (xterm.js + command builder panel)
        └── POST /cmd        ← inject command into session
```

The browser shows:
- **xterm.js** pane — real terminal output, streaming LLM responses, ANSI colors
- **Command builder panel** — buttons and forms that construct and inject commands
  (deepagent_md, webindex, translate, script runner, etc.)

## Why This Is Better Than vyrii

- Single codebase: all flows, adapters, scripts live in 1bcoder only
- No duplicated logic: translate adapter, deepagent_md, webindex — one version
- CWD is correct by design: server starts in the project directory
- Scheduled tasks already work: `1bcoder --scriptapply` stays unchanged
- Multi-project: open browser tabs on different ports = different project sessions

## I/O Architecture (Windows-compatible, no PTY needed)

Replace `input()` / `print()` with asyncio queues when `--web` is active:

```python
input_queue  = asyncio.Queue()  # browser → 1bcoder (commands)
output_queue = asyncio.Queue()  # 1bcoder → browser (output stream)

# WebSocket handler:
async def ws_handler(websocket):
    async for message in websocket:
        await input_queue.put(message)       # user typed something
    # meanwhile: push output_queue → websocket continuously
```

1bcoder reads from `input_queue` instead of stdin.
1bcoder writes to `output_queue` instead of stdout.
No PTY, no platform-specific hacks, pure Python async.

## Key Challenge: pyreadline3

On Windows, 1bcoder's interactive `input()` goes through pyreadline3 which uses
Win32 console API directly. In `--web` mode this must be bypassed — `input()` should
read from `input_queue` instead. This requires a mode flag checked at the input loop.

## Command Builder Panel

The HTML page includes a sidebar with structured forms for common operations:

| Button / Form         | Injects command                                              |
|-----------------------|--------------------------------------------------------------|
| Deep Document         | `/flow deepagent_md "..." --rag X --maxdepth N`              |
| Web Index             | `/flow webindex <url> --project X --pages N`                 |
| Translate             | `/translate <text or file>`                                  |
| Web Analyse           | `/flow webanalys "..." -n N`                                 |
| Run Script            | `/script run <name>`                                         |
| Compose               | `/flow deepagent_md compose <plan>`                          |

Commands appear in the xterm.js pane as if the user typed them — full output visible.

## Session Model

- One `1bcoder --web` process = one session = one cwd
- Multiple projects = multiple ports (or session_id routing)
- Browser disconnect: session stays alive (reconnect resumes)
- `--web --port 4896` keeps running; `Ctrl+C` or `/exit` stops it

## Streaming

LLM output already streams via `print()` in 1bcoder. In `--web` mode, each printed
chunk goes to `output_queue` and is pushed over WebSocket to xterm.js in real time.
No changes to the LLM streaming logic itself.

## Relationship to vyrii

vyrii (Gradio-based) remains the simpler tool for quick use without installing 1bcoder.
MIRROR mode targets users who already have 1bcoder set up and want a richer UI without
leaving their local environment. The two can coexist — vyrii is the lightweight option,
MIRROR is the full-power option.

## Implementation Steps (when ready)

1. Add `--web [port]` argument to `chat.py` argparse
2. Replace `input()` with queue-aware wrapper (checked via `_WEB_MODE` flag)
3. Replace `print()` / streaming output with queue-aware wrapper
4. Write minimal `server.py`: FastAPI + WebSocket + static HTML
5. Build `mirror.html`: xterm.js + command builder sidebar (vanilla JS, no framework)
6. Test with `/flow deepagent_md` as the first real use case
