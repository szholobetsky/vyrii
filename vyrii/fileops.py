"""Files → unzip an archive, git clone / pull / checkout a folder inside ~/.vyrii.

Each operation takes only values, never a command: a zip path; a repository URL
and a folder name; a branch, tag or commit. git runs with an argument list (no
shell), the URL and the ref after checks, and:

  - only network protocols (GIT_ALLOW_PROTOCOL: https, http, ssh, git) — no
    ext:: (it runs a command) and no local paths (another folder of this machine);
  - no hooks, no fsmonitor (both run programs named in .git), no password prompt
    (a headless machine would wait forever).

A .git folder is written by git alone: Files (upload, new folder) and unzip
refuse a path with a .git part — whoever writes .git/config or .git/hooks
decides what the next pull runs.

    unzip(zip_path)                     → {"dest", "files", "skipped"}
    clone(url, parent, name="")         → {"path", "output", "head"}
    pull(repo)                          → {"output", "head"}
    checkout(repo, ref)                 → {"output", "head"}
"""
from __future__ import annotations

import os
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

MAX_UNZIP_BYTES = 4 * 1024 ** 3     # a zip bomb stops here
MAX_UNZIP_FILES = 200_000
GIT_TIMEOUT     = 1800              # a big clone over a slow line

_URL = re.compile(r"^(https?|ssh|git)://[^\s]+$|^[\w.-]+@[\w.-]+:[^\s]+$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_NAME = re.compile(r"^[^\\/:*?\"<>|\s][^\\/:*?\"<>|]{0,199}$")


class FileOpError(Exception):
    pass


def has_git_part(rel: str) -> bool:
    return any(p.lower() == ".git" for p in re.split(r"[\\/]+", rel))


# ── unzip ─────────────────────────────────────────────────────────────────────

def _members(zf: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    res, total = [], 0
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or re.match(r"^[A-Za-z]:", name) or not p.parts:
            raise FileOpError(f"unsafe path in the archive: {info.filename}")
        total += info.file_size
        res.append((info, p))
    if len(res) > MAX_UNZIP_FILES:
        raise FileOpError(f"too many files in the archive ({len(res)})")
    if total > MAX_UNZIP_BYTES:
        raise FileOpError(f"the archive unpacks to {total // 1024 ** 2} MB — more than "
                          f"{MAX_UNZIP_BYTES // 1024 ** 2} MB")
    return res


def _free(path: Path) -> Path:
    n, cand = 1, path
    while cand.exists():
        cand = path.with_name(f"{path.name} ({n})")
        n += 1
    return cand


def unzip(zip_path: Path, writable=lambda p: p) -> dict:
    """Unpacks next to the zip: an archive of one top folder (a GitHub download,
    repo-main/…) gives that folder, any other a folder named after the zip.
    Nothing is overwritten. writable(path) raises for a path Files may not write."""
    if not zip_path.is_file():
        raise FileOpError(f"not a file: {zip_path.name}")
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise FileOpError(f"not a zip archive: {zip_path.name}")
    with zf:
        members = _members(zf)
        tops = {p.parts[0] for _, p in members}
        single = len(tops) == 1 and all(len(p.parts) > 1 or i.is_dir() for i, p in members)
        if single:
            dest, strip = _free(zip_path.parent / next(iter(tops))), 1
        else:
            dest, strip = _free(zip_path.parent / zip_path.stem), 0
        writable(dest)
        files, skipped = 0, []
        for info, p in members:
            parts = p.parts[strip:]
            if not parts:
                continue
            if any(x.lower() == ".git" for x in parts):
                skipped.append(info.filename)
                continue
            target = dest.joinpath(*parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(1024 * 1024):
                    out.write(chunk)
            files += 1
    return {"dest": dest, "files": files, "skipped": skipped}


# ── git ───────────────────────────────────────────────────────────────────────

def _env() -> dict:
    env = dict(os.environ)
    env.update({
        "GIT_ALLOW_PROTOCOL": "https:http:ssh:git",
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",          # Git Credential Manager: no window
        "GIT_SSH_COMMAND": env.get("GIT_SSH_COMMAND") or "ssh -o BatchMode=yes",
    })
    return env


def _git(args: list[str], cwd: Path | None = None) -> str:
    cmd = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
           "-c", "protocol.file.allow=never", *args]
    try:
        r = subprocess.run(cmd, cwd=cwd, env=_env(), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
                           stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        raise FileOpError("git is not installed on this machine")
    except subprocess.TimeoutExpired:
        raise FileOpError(f"git {args[0]} took longer than {GIT_TIMEOUT} s")
    out = (r.stdout + r.stderr).strip()
    if r.returncode:
        raise FileOpError(out or f"git {args[0]} failed ({r.returncode})")
    return out


def head(repo: Path) -> str:
    """branch (or 'detached') · short sha · subject of the last commit"""
    try:
        branch = _git(["-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"])
        last = _git(["-C", str(repo), "log", "-1", "--format=%h %s"])
    except FileOpError:
        return ""
    return f"{'detached' if branch == 'HEAD' else branch} · {last}"


def is_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


def check_url(url: str) -> str:
    url = (url or "").strip()
    if not _URL.match(url) or url.startswith("-"):
        raise FileOpError("a repository URL: https://…, ssh://…, git://… or user@host:path")
    return url


def check_ref(ref: str) -> str:
    ref = (ref or "").strip()
    if not _REF.match(ref) or ".." in ref or ref.endswith((".lock", "/")):
        raise FileOpError("a branch, tag or commit: letters, digits, . _ / -")
    return ref


def repo_name(url: str) -> str:
    name = re.split(r"[/:]", url.rstrip("/"))[-1]
    return name[:-4] if name.endswith(".git") else name


def clone(url: str, parent: Path, name: str = "", writable=lambda p: p) -> dict:
    url = check_url(url)
    name = (name or "").strip() or repo_name(url)
    if not _NAME.match(name) or name in (".", "..") or name.lower() == ".git":
        raise FileOpError(f"not a folder name: {name}")
    if not parent.is_dir():
        raise FileOpError("clone into a folder")
    target = parent / name
    writable(target)
    if target.exists() and any(target.iterdir()):
        raise FileOpError(f"{name} already exists and is not empty")
    out = _git(["clone", "--", url, str(target)])
    return {"path": target, "output": out, "head": head(target)}


def pull(repo: Path) -> dict:
    if not is_repo(repo):
        raise FileOpError("not a git repository (no .git in this folder)")
    out = _git(["-C", str(repo), "pull", "--ff-only"])
    return {"output": out, "head": head(repo)}


def checkout(repo: Path, ref: str) -> dict:
    """A local or remote branch, a tag, a commit; one the clone does not have yet
    is fetched from origin first (a commit lands as a detached HEAD)."""
    if not is_repo(repo):
        raise FileOpError("not a git repository (no .git in this folder)")
    ref = check_ref(ref)
    try:
        out = _git(["-C", str(repo), "checkout", ref, "--"])
    except FileOpError:
        fetched = _git(["-C", str(repo), "fetch", "origin", "--", ref])
        out = fetched + "\n" + _git(["-C", str(repo), "checkout", "--detach", "FETCH_HEAD", "--"])
    return {"output": out.strip(), "head": head(repo)}
