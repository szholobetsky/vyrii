# vyrii — Multi-User Mode Concept

Activated with `vyrii --team`.
Goal: one vyrii instance serves a team (or public company deployment) with minimal
infrastructure — SQLite auth, no external services required.

---

## Roles

| Role      | Trigger                        | Access                                                         |
|-----------|--------------------------------|----------------------------------------------------------------|
| Anonymous | "Enter as Guest" button        | Chat (no history), Translate, WebAsk                          |
| User      | Login with username + password | + Chat history (own only), no backend/model switching          |
| Admin     | Login, role=admin in DB        | + WebCrawl, DeepAgent, host/model selector, active users list |

Anonymous sessions are ephemeral — no DB writes, no history.
Users see only their own chat history — never other users' conversations.

---

## Login / Register Page

When `--team` is active, the app opens at `/login` before Gradio loads.

```
┌──────────────────────────────────┐
│  vyrii — team login              │
│                                  │
│  Username: [___________________] │
│  Password: [___________________] │
│                                  │
│  [Login]  [Register]             │
│                                  │
│  ──────────────────────────────  │
│  [Enter as Guest]                │
└──────────────────────────────────┘
```

- Login / Register are standard form POSTs to FastAPI auth endpoints.
- "Enter as Guest" sets an anonymous session token (short TTL, e.g. 2h).
- After login the browser is redirected to the Gradio UI with a session token
  in a secure HTTP-only cookie.

---

## Session & Auth Architecture

### New file: `vyrii/auth.py`

```python
# SQLite table: users(id, username, password_hash, role, created_at)
# SQLite table: sessions(token, user_id, role, expires_at)

def register(username, password) -> bool: ...
def login(username, password) -> str | None:  # returns session token or None
def get_session(token: str) -> dict | None:   # {"user_id", "username", "role"}
def delete_session(token: str): ...
```

Password storage: `bcrypt` (or `hashlib.scrypt` — stdlib, no extra dep).
Session tokens: `secrets.token_hex(32)`, stored in SQLite with expiry (24h default).

### New file: `vyrii/login_page.py`

FastAPI router that serves:
- `GET  /login`          → HTML login/register form
- `POST /auth/login`     → validate, create session, set cookie, redirect
- `POST /auth/register`  → create user (role=user), then auto-login
- `POST /auth/logout`    → delete session, clear cookie
- `GET  /auth/me`        → return current session info (for JS frontend)

### Integration with Gradio

Gradio's built-in `app.launch(auth=fn)` only supports basic username/password —
no registration, no roles. Two options:

**Option A (simpler)**: FastAPI serves `/login` HTML, sets cookie, then Gradio
is mounted as a sub-app under `/app`. FastAPI middleware checks the cookie on
every request to `/app/*` and rejects unauthenticated requests with redirect to
`/login`. Gradio never knows about auth — FastAPI guards it.

```python
# in team mode:
fastapi_app.mount("/app", gradio_app)
# middleware: if no valid session cookie → redirect to /login
```

**Option B (richer)**: Gradio `auth=` receives a wrapper that checks the SQLite
session table. Role info is passed via a custom Gradio `State` injected at
startup. Harder to wire roles into Gradio tab visibility.

Recommended: **Option A** — clean separation, works for both Gradio and API.

---

## Role-Based UI in Gradio

Gradio doesn't support conditional tab visibility natively, but we can:

1. Build separate Gradio apps per role: `build_app_anon()`, `build_app_user()`,
   `build_app_admin()`.
2. The FastAPI middleware reads the session role and mounts the correct app:
   ```
   /app/anon/*   → anonymous Gradio
   /app/user/*   → user Gradio
   /app/admin/*  → admin Gradio
   ```
3. After login, redirect to the role-specific sub-path.

This avoids complex Gradio hacks and keeps each app minimal.

---

## Multi-Host / Time-Sharing

For a server with multiple GPUs or multiple LLM processes:

### New file: `vyrii/hosts.py`

Config loaded from `hosts.yaml` (or CLI `--hosts hosts.yaml`):

```yaml
hosts:
  - name: "GPU-0 (RTX 3090)"
    url: "http://localhost:11434"
    backend: "ollama"
  - name: "GPU-1 (RTX 3060)"
    url: "http://localhost:11435"
    backend: "ollama"
  - name: "LM Studio"
    url: "http://localhost:1234"
    backend: "openai"
```

Background task (runs every 30s):
```python
async def poll_hosts():
    for host in hosts:
        host.models   = list_models(host.url, host.backend)
        host.active   = count_active_requests(host.url)
        host.online   = ping(host.url)
```

`count_active_requests`: tracked internally — increment on request start,
decrement on completion. Stored in a `Counter` dict keyed by host URL.

### UI: Host Selector (Admin only in full, Users see read-only)

```
┌─ Available hosts ──────────────────────────────────┐
│  GPU-0 (RTX 3090)  │ qwen3:7b    │ ●● 2 active    │
│  GPU-1 (RTX 3060)  │ gemma3:4b   │ ● 1 active     │  ← least loaded
│  LM Studio         │ phi-4       │ ○  0 active     │
└────────────────────────────────────────────────────┘
```

Anonymous and regular Users see this table as read-only info.
They can click a row to "prefer" a host — stored in their session.
Admin can change the default host for all users.

---

## Request Queue

### Problem

A single LLM processes one token stream at a time. With N users sending
requests simultaneously, responses degrade or timeout.

### Design: `vyrii/queue.py`

```python
# Per-host FIFO queue
# Each entry: {request_id, user_id, host_url, payload, enqueued_at, future}

class RequestQueue:
    def __init__(self, host_url):
        self.queue = asyncio.Queue()
        self.processing = False
        self.recent_durations = deque(maxlen=20)  # rolling average

    async def submit(self, payload) -> asyncio.Future:
        # returns Future that resolves when LLM responds
        ...

    def estimated_wait(self) -> float:
        # position_in_queue * avg_duration_seconds
        avg = mean(self.recent_durations) if self.recent_durations else 30.0
        return self.queue.qsize() * avg

    async def worker(self):
        while True:
            entry = await self.queue.get()
            t0 = time.monotonic()
            await entry.future.set_result(await _call_llm(entry.payload))
            self.recent_durations.append(time.monotonic() - t0)
```

### User notification

When a request is queued:
- Gradio: show a banner above the chat: `"Queue position: 3 — estimated wait: ~12 min"`
  Updated via Gradio streaming / polling every 5s.
- API: return `202 Accepted` with body `{"queue_position": 3, "estimated_wait_sec": 720,
  "request_id": "abc123"}`. Client polls `GET /v1/queue/{request_id}` for status.

### Wait time estimation

```
avg_duration = rolling mean of last 20 completed requests (per host)
position     = current position in queue (1 = next)
estimated    = position * avg_duration
```

Display: `"~{estimated // 60} min"` if > 60s, else `"~{estimated}s"`.

---

## Implementation Sequence (when ready)

### Phase 1 — Auth (prerequisite for everything)
1. `auth.py` — SQLite users + sessions, bcrypt passwords
2. `login_page.py` — FastAPI router, login/register/logout HTML
3. `__main__.py` — `--team` flag, mounts login page + guards Gradio
4. Three Gradio app variants (anon / user / admin) in `app.py`

### Phase 2 — Multi-host
5. `hosts.py` — config loader, background poller, active-request counter
6. Host selector table in admin Gradio tab
7. Host preference stored in session; engine routes requests to preferred host

### Phase 3 — Queue
8. `queue.py` — per-host asyncio queue + rolling average estimator
9. Wire into `stream_chat` / `complete` — all calls go through queue
10. Gradio: queue position banner with 5s poll
11. API: `202 Accepted` + `GET /v1/queue/{id}` status endpoint

### Phase 4 — Polish
12. Admin dashboard: active users, queue depth per host, avg wait time
13. Session expiry / idle timeout config
14. Rate limiting per user (max N requests/hour for anonymous)

---

## New files summary

| File                    | Purpose                                              |
|-------------------------|------------------------------------------------------|
| `vyrii/auth.py`         | SQLite user store, session tokens, bcrypt            |
| `vyrii/login_page.py`   | FastAPI router: /login HTML + /auth/* endpoints      |
| `vyrii/hosts.py`        | Multi-host config, health poller, active-req counter |
| `vyrii/queue.py`        | Per-host FIFO queue, wait time estimator             |
| `hosts.yaml`            | User-edited host list                                |

Modified files: `app.py` (role variants), `api.py` (auth middleware, queue),
`__main__.py` (`--team` flag, host config path).

---

## Dependencies to add

```
bcrypt>=4.0          # password hashing (or use stdlib hashlib.scrypt)
pyyaml>=6.0          # hosts.yaml config
aiofiles>=23.0       # async file ops (optional)
```

No Redis, no Celery — SQLite + asyncio queues are sufficient for a team of
~50 concurrent users.
