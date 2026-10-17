"""Ontology for every vyrii UI — Flask (flask_api.py), FastAPI (api.py) and
Gradio (app.py) call these functions, so the three stay identical.

The engine is vyrii/flows/ontology.py, an unchanged copy of 1bcoder's
/flow ontology (sync = copy). Like the glossary, vyrii keeps every project
under one fixed root (~/.vyrii/ontology/<project>/) instead of next to the
indexed folder. See concepts/ONTOLOGY.md in simrgl.

Errors are raised as OntoError(message, http_status); each web layer turns
them into its own {"error": ...} response.
"""
from __future__ import annotations

import contextlib
import io
import json
import types
import uuid

from .flows import ontology as _onto


class OntoError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def setup(home) -> None:
    """Store projects under <home>/ontology; source paths are shown relative to home."""
    _onto.ROOT = str(home / "ontology")
    _onto.SRC_BASE = str(home)


def project_name(name) -> str:
    name = (name or "").strip() or "default"
    if not all(ch.isalnum() or ch in "-_." for ch in name) or name.strip(".") == "":
        raise OntoError(f"bad project name: {name!r}")
    return name


def _capture(fn, *args) -> str:
    """Run a printing ontology command and return what it printed."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue().strip()


# ── index ────────────────────────────────────────────────────────────────────

def index_args(target, project, chunk=1200, strict=False, jev=True) -> str:
    args = f'"{target}" --project {project_name(project)} --chunk {int(chunk or 1200)}'
    if strict:
        args += " --strict"
    if not jev:
        args += " --no-jev"
    return args


# run_id -> {"stop": bool}: a web worker thread can't get Ctrl+C, so the Stop
# button sets this flag; ontology._llm checks chat._ontology_should_cancel
_cancel_flags: dict = {}


def run_index(adapter, args):
    """Index in a background thread -> yields ("started", run_id) once, then
    ("line", text) for every printed line."""
    from .adapter import stream_flow_lines
    flag = {"stop": False}
    run_id = str(uuid.uuid4())
    _cancel_flags[run_id] = flag
    adapter._ontology_should_cancel = lambda: flag["stop"]
    try:
        yield "started", run_id
        for line in stream_flow_lines(lambda: _onto.cmd_index(adapter, args)):
            yield "line", line
    finally:
        _cancel_flags.pop(run_id, None)


def index_sse(adapter, args):
    """run_index as the SSE stream app.js reads (Flask and FastAPI)."""
    for kind, value in run_index(adapter, args):
        if kind == "started":
            yield f'data: {json.dumps({"type": "started", "run_id": value})}\n\n'
        else:
            yield f'data: {json.dumps({"type": "line", "line": value})}\n\n'
    yield 'data: [DONE]\n\n'


def cancel_index(run_id) -> None:
    flag = _cancel_flags.get(run_id or "")
    if flag is not None:
        flag["stop"] = True


# ── browse / resolve ─────────────────────────────────────────────────────────

def projects() -> list:
    return _onto.list_projects()


def triples(project, q="") -> list:
    rows = [{"s": t.s, "p": t.p, "o": t.o, "src": t.src, "line": t.line()}
            for t in _onto.load_triples(project_name(project))]
    q = (q or "").strip().lower()
    return [r for r in rows if q in r["line"].lower()] if q else rows


def journal(project) -> list:
    rows = _onto.load_jsonl(project_name(project), _onto.JOURNAL)
    for r in rows:
        r["new_text"] = [_onto.Triple.from_list(x).text() for x in r["new"]]
        r["with_text"] = [_onto.Triple.from_list(x).line() for x in r["with"]]
    return rows


def resolve(project, row_id, choice, text="") -> dict:
    """-> {"status": new status or None (row stays open), "message": ...}"""
    project = project_name(project)
    text = (text or "").strip()
    row = next((r for r in _onto.load_jsonl(project, _onto.JOURNAL) if r["id"] == row_id), None)
    if row is None:
        raise OntoError("no such row", 404)
    if row["status"] != "open":
        raise OntoError(f"row is already {row['status']}", 409)
    if choice in ("disjoint", "accept") and row["level"] != "yellow":
        raise OntoError(f"{choice} is only for YELLOW rows")
    if choice == "condition" and not text:
        raise OntoError("condition is required")
    status, msg = _onto.resolve_row(project, row, choice, text)
    if status:
        note = text if choice in ("condition", "known") else ""
        _onto.journal_set(project, row["id"], status, note)
    return {"status": status, "message": msg}


_CMDS = {"add": _onto.cmd_add, "remove": _onto.cmd_remove, "check": _onto.cmd_check,
         "show": _onto.cmd_show, "ask": _onto.cmd_ask, "sparql": _onto.cmd_sparql,
         "export": _onto.cmd_export}


def command(cmd, project, arg="", strict=False) -> str:
    """add / remove / check / show / ask / sparql / export -> printed text.
    A failing command raises OntoError with status 200: the UI shows it as the output."""
    if cmd not in _CMDS:
        raise OntoError(f"unknown command: {cmd}")
    project = project_name(project)
    arg = (arg or "").strip().replace("\n", " ")
    if cmd == "show":
        rest = f"{arg} --project {project}"
    else:
        rest = (f'"{arg}" ' if arg else "") + f"--project {project}"
        if cmd == "add" and strict:
            rest += " --strict"
    chat = types.SimpleNamespace(messages=[], _last_output="")
    try:
        return _capture(_CMDS[cmd], chat, rest)
    except Exception as e:
        raise OntoError(f"{type(e).__name__}: {e}", 200)
