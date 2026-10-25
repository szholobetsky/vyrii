"""Files → unzip, git clone / pull / checkout: values only, no hooks, .git written by git alone."""
import io
import json
import pathlib
import subprocess
import zipfile

import pytest

from vyrii import fileops


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".vyrii").mkdir()
    from vyrii.flask_api import create_app
    return create_app().test_client(), tmp_path / ".vyrii"


def _zip(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


def _repo(path):
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    (path / "a.txt").write_text("one", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "first")
    _git(path, "tag", "v1")
    (path / "a.txt").write_text("two", encoding="utf-8")
    _git(path, "commit", "-q", "-am", "second")
    return path


# ── unzip ─────────────────────────────────────────────────────────────────────

def test_unzip_one_top_folder_gives_that_folder(tmp_path):
    z = _zip(tmp_path / "repo-main.zip", {"repo-main/a.txt": "a", "repo-main/s/b.txt": "b"})
    r = fileops.unzip(z)
    assert r["dest"] == tmp_path / "repo-main" and r["files"] == 2
    assert (tmp_path / "repo-main" / "s" / "b.txt").read_text() == "b"


def test_unzip_loose_files_go_into_a_folder_named_after_the_zip(tmp_path):
    z = _zip(tmp_path / "docs.zip", {"a.txt": "a", "b/c.txt": "c"})
    (tmp_path / "docs").mkdir()                     # never overwritten
    r = fileops.unzip(z)
    assert r["dest"] == tmp_path / "docs (1)" and (tmp_path / "docs (1)" / "b" / "c.txt").exists()


@pytest.mark.parametrize("bad", ["../evil.txt", "/abs.txt", "C:/win.txt", "a/../../evil.txt"])
def test_unzip_refuses_paths_outside(tmp_path, bad):
    z = _zip(tmp_path / "x.zip", {"ok.txt": "a", bad: "b"})
    with pytest.raises(fileops.FileOpError):
        fileops.unzip(z)
    assert not (tmp_path.parent / "evil.txt").exists()


def test_unzip_skips_git(tmp_path):
    z = _zip(tmp_path / "x.zip", {"p/a.txt": "a", "p/.git/hooks/post-merge": "evil", "p/.GIT/config": "x"})
    r = fileops.unzip(z)
    assert r["files"] == 1 and len(r["skipped"]) == 2
    assert not (tmp_path / "p" / ".git").exists()


# ── git ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", ["ext::sh -c touch% /tmp/x", "-uhttps://x", "C:/Projects/vyrii",
                                 "/home/u/repo", "file:///etc", "https://x y"])
def test_bad_urls_refused(url, tmp_path):
    with pytest.raises(fileops.FileOpError):
        fileops.clone(url, tmp_path)


@pytest.mark.parametrize("ref", ["--orphan", "-b", "a..b", "x y", "x;rm", "main.lock", ""])
def test_bad_refs_refused(ref):
    with pytest.raises(fileops.FileOpError):
        fileops.check_ref(ref)


def test_repo_name_from_url():
    assert fileops.repo_name("https://github.com/u/vyrii.git") == "vyrii"
    assert fileops.repo_name("git@github.com:u/simargl") == "simargl"


def test_checkout_tag_and_back_without_hooks(tmp_path):
    repo = _repo(tmp_path / "r")
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\necho ran > hook_ran.txt\n", encoding="utf-8")
    hook.chmod(0o755)
    r = fileops.checkout(repo, "v1")
    assert (repo / "a.txt").read_text() == "one" and "first" in r["head"]
    r = fileops.checkout(repo, "main")
    assert (repo / "a.txt").read_text() == "two" and r["head"].startswith("main")
    assert not (repo / "hook_ran.txt").exists()


def test_pull_needs_a_repository(tmp_path):
    with pytest.raises(fileops.FileOpError, match="not a git repository"):
        fileops.pull(tmp_path)


# ── through Flask ─────────────────────────────────────────────────────────────

def test_upload_and_mkdir_never_write_git(client):
    c, home = client
    (home / "r").mkdir()
    r = c.post("/vyrii/files/upload?dest=r/.git/hooks",
               data={"files": (io.BytesIO(b"evil"), "post-merge")},
               content_type="multipart/form-data").get_json()
    assert "git only" in json.dumps(r)
    r = c.post("/vyrii/files/mkdir", json={"path": "r/.Git/hooks"}).get_json()
    assert "git only" in json.dumps(r)
    assert not (home / "r" / ".git").exists()


def test_unzip_route(client):
    c, home = client
    (home / "in").mkdir()
    _zip(home / "in" / "x.zip", {"x/a.txt": "a"})
    r = c.post("/vyrii/files/unzip", json={"path": "in/x.zip"}).get_json()
    assert r["ok"] and r["dest"] == "in/x/" and (home / "in" / "x" / "a.txt").exists()


def test_git_route_checkout_and_list_hides_objects(client):
    c, home = client
    _repo(home / "r")
    r = c.post("/vyrii/files/git", json={"op": "checkout", "path": "r", "ref": "v1"}).get_json()
    assert r["ok"] and "first" in r["head"]
    tree = c.get("/vyrii/files/list").get_json()["tree"]
    assert tree["r/"][".git/"] == {}
    r = c.post("/vyrii/files/git", json={"op": "clone", "path": "", "url": "ext::sh"}).get_json()
    assert "error" in r


def test_delete_a_cloned_repo(client):
    c, home = client
    _repo(home / "r")
    r = c.delete("/vyrii/files", json={"path": "r"}).get_json()
    assert r.get("ok"), r
    assert not (home / "r").exists()
