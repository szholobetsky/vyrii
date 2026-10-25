"""Tool tabs print the answer as it is generated: {"stream": true} → SSE start/token/log/done."""
import json
import pathlib

import pytest

from vyrii import engine, live


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".vyrii").mkdir()
    monkeypatch.setattr(engine, "stream_chat", lambda *a, **k: iter(["Пере", "клад ", "готовий"]))
    from vyrii.flask_api import create_app
    return create_app().test_client()


def _events(resp):
    return [json.loads(line[6:]) for line in resp.get_data(as_text=True).split("\n\n") if line.startswith("data: ")]


def test_without_stream_the_answer_is_one_json(client):
    r = client.post("/vyrii/translate", json={"text": "hi", "to_lang": "uk", "model": "m"})
    assert r.is_json and r.get_json()["result"] == "Переклад готовий"


def test_stream_sends_tokens_then_the_same_json(client):
    r = client.post("/vyrii/translate", json={"text": "hi", "to_lang": "uk", "model": "m", "stream": True})
    assert r.mimetype == "text/event-stream"
    ev = _events(r)
    assert ev[0] == {"type": "start"}
    assert [e["text"] for e in ev if e["type"] == "token"] == ["Пере", "клад ", "готовий"]
    assert ev[-1] == {"type": "done", "data": {"result": "Переклад готовий"}}


def test_capture_routes_only_its_own_thread(capsys):
    import threading
    got = []
    with live.capture(on_log=got.append):
        print("mine")
        t = threading.Thread(target=lambda: print("other thread"))
        t.start(); t.join()
    assert "".join(got).strip() == "mine"
    assert "other thread" in capsys.readouterr().out
