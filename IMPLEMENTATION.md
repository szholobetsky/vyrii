# Implementation Notes

## Project structure

```
vyrii/
├── pyproject.toml          package metadata and dependencies
└── vyrii/
    ├── __init__.py         version
    ├── __main__.py         CLI entry point  (vyrii --web --port N)
    ├── engine.py           Ollama API client + smart context
    ├── history.py          SQLite chat history
    ├── tools.py            web fetch utilities (fetch_text, extract_links, ddg_search)
    └── app.py              Gradio UI — all tabs
```

Chat history database: `~/.vyrii/history.db` (SQLite, created on first run).

---

## engine.py

### Ollama API

Uses the `/api/chat` endpoint with `stream: true`. Each streamed line is a JSON object with `message.content`. The last line has `done: true`.

```python
stream_chat(messages, model, base_url, num_ctx)  # → generator of str chunks
complete(messages, model, base_url, num_ctx)      # → str  (joins all chunks)
```

### Message sanitization

Gradio 6.x passes `MessageDict` objects from the chatbot component back to Python handlers. These objects carry Gradio-internal fields (`metadata`, `id`, timestamps) that Ollama rejects with HTTP 400. `_clean_messages()` strips every field except `role` and `content` before the request is sent.

### Smart context

```
smart_ctx(messages, current=2048) → int
```

Estimates total tokens (heuristic: `len(text) // 4`). If the total exceeds 80 % of the current window, bumps the window by 2048. Repeats until the messages fit. The resulting value is passed to Ollama as `options.num_ctx`.

Starting size: **2048**. Step: **2048**. Threshold: **80 %**.

This keeps small models (4 K context) from silently truncating long conversations, while avoiding unnecessary large allocations on short ones.

---

## history.py

Two SQLite tables:

```sql
chats    (id, title, created_at)
messages (id, chat_id, role, content, created_at)
```

`auto_title(content)` takes the first 50 characters of the first user message as the conversation title.

Database path is `~/.vyrii/history.db`. The parent directory is created on first access.

---

## tools.py

### fetch_text(url)

1. `requests.get` with a browser-like User-Agent
2. `lxml.html.fromstring` to parse the DOM
3. Remove `<script>`, `<style>`, `<nav>`, `<footer>`, `<header>`, `<aside>` tags
4. Extract text via `text_content()` (fallback: `itertext()`)
5. Collapse whitespace with `re.sub(r'\s+', ' ', ...)`
6. Truncate to 6000 characters

### ddg_search(query, n)

Thin wrapper around `duckduckgo_search.DDGS().text()`. Returns a list of `(title, url, snippet)` tuples. Used by WebAsk when no URL is provided.

### extract_links(page_url, base_url)

Fetches the page, parses all `<a href>` elements, makes links absolute, and returns only those that share the same scheme+host as `base_url`. Used by WebCrawl for BFS link discovery. Capped at 60 links per page.

---

## app.py

Built with `gradio.Blocks`. All five tabs share a single model dropdown and Ollama URL at the top of the page — one setting applies everywhere.

### Component layout

```
gr.Blocks
  gr.Row           ← shared: Model dropdown | Ollama URL | Refresh
  gr.Tabs
    gr.Tab "Chat"
    gr.Tab "Translate"
    gr.Tab "WebAsk"
    gr.Tab "WebCrawl"
    gr.Tab "DeepAgent MD"
```

### Chat tab — state model

The chatbot component (`gr.Chatbot`) serves as both the display and the message history. Its value is a list of `{"role": ..., "content": ...}` dicts in Gradio 6.x message format.

Two additional `gr.State` components:
- `s_cid` — current chat ID (int or None; None means unsaved)
- `s_ctx` — current context window size (int, starts at 2048)

**Send flow:**
1. Append user message dict to chatbot value
2. Call `smart_ctx` to potentially expand the window
3. Yield the updated chatbot (with empty assistant placeholder) to show the user message immediately
4. Create a DB record for the chat on the first message of a new session
5. Stream from Ollama, updating the assistant message in-place on each chunk
6. Persist the completed assistant message to SQLite

**Load flow:** Fetch all messages for the selected chat from SQLite and set them as the chatbot value directly — no conversion needed since the DB stores plain `role`/`content` dicts.

### "Add to chat" button

Each tool tab has an "Add to chat" button that writes the tool's result textbox into `msg_in` — the chat input on the Chat tab. The user then switches to Chat and sends it. This works because all components in a `gr.Blocks` are accessible across tabs.

### WebAsk — dual mode

```python
if url.strip():
    # single-page mode: fetch URL → build context
else:
    # search mode: DDG search → fetch top N pages → build context
    # if a page fetch fails, fall back to DDG snippet
```

The LLM prompt in both modes ends with "cite source URLs where relevant" and "if not in the content, say so" — this reduces hallucination on small models.

### DeepAgent MD — two-pass generation

1. **Outline pass**: ask LLM to produce N numbered `## X. Title` headers
2. **Section pass**: for each extracted title, ask LLM to write that section independently

Each section call is a separate `complete()` call with no shared state between sections. This keeps individual prompts short, which is critical for 4 K context models.

Reference URL (optional): its text is appended to every prompt in both passes.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `gradio>=4.0` | Web UI framework |
| `requests` | HTTP client for Ollama API and web fetching |
| `lxml` | HTML parsing and text extraction |
| `duckduckgo-search` | DDG web search in WebAsk |

No GPU, no vector database, no embedding models. The only external service is a local Ollama instance.

---

## Known limitations

- **Context accuracy**: token estimation is `len(text) // 4` — fast but approximate. For models with non-Latin scripts (Ukrainian, Chinese) the actual token count is higher.
- **Web fetch reliability**: JavaScript-rendered pages return empty or minimal content since there is no headless browser.
- **DeepAgent MD section coherence**: sections are generated independently, so cross-references between sections are not guaranteed to be consistent.
- **WebCrawl on large sites**: BFS with `extract_links` stays within the same domain but does not respect `robots.txt`.
