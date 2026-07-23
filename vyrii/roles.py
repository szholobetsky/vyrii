"""Role storage — named system prompts selected once at the start of a new chat.

Distinct from `prompts.json` (the Prompts library, a one-shot "Add to chat"
snippet inserted manually at any point): a Role is chosen once, becomes an
invisible system-level message prepended to every request for the rest of
that chat, and is protected by ctxwindow's autocut `first` budget.

One JSON file per role in `<vyrii_home>/role/` (not a single flat list) —
mirrors `_bcoder_data/flows/glossary.py`'s per-term-file directory convention.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROLES_DIR: Path | None = None


def init(vyrii_home: Path) -> None:
    global ROLES_DIR
    ROLES_DIR = vyrii_home / "role"
    ROLES_DIR.mkdir(parents=True, exist_ok=True)


def _dir() -> Path:
    if ROLES_DIR is None:
        raise RuntimeError("roles.init() not called")
    return ROLES_DIR


def _kebab(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return re.sub(r'-+', '-', s).strip('-')


def _role_path(name: str) -> Path:
    return _dir() / (_kebab(name) + ".json")


def list_roles() -> list[dict]:
    roles = []
    for f in _dir().glob("*.json"):
        try:
            roles.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return sorted(roles, key=lambda r: r.get("name", "").lower())


def get_role(name: str) -> dict | None:
    p = _role_path(name)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_role(name: str, prompt: str, size: int = 0) -> None:
    name = name.strip()
    if not name:
        raise ValueError("role name is required")
    _role_path(name).write_text(
        json.dumps({"name": name, "prompt": prompt, "size": int(size or 0)},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def delete_role(name: str) -> None:
    p = _role_path(name)
    if p.is_file():
        p.unlink()
