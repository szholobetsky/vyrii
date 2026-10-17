"""Chat attachments: text from documents, images for vision models, the upload/chat endpoints."""
import base64
import io
import json
import pathlib
import zipfile

import pytest

from vyrii import attachments as att
from vyrii import engine

# 1×1 red PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _zip(path, parts):
    with zipfile.ZipFile(path, "w") as z:
        for name, text in parts.items():
            z.writestr(name, text)
    return path


def _docx(path):
    return _zip(path, {"word/document.xml": f"""<?xml version="1.0"?>
<w:document {W}><w:body>
  <w:p><w:r><w:t>Invoice</w:t></w:r><w:r><w:tab/><w:t>2026</w:t></w:r></w:p>
  <w:tbl>
    <w:tr><w:tc><w:p><w:r><w:t>Item</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Price</w:t></w:r></w:p></w:tc></w:tr>
    <w:tr><w:tc><w:p><w:r><w:t>Tea</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>3</w:t></w:r></w:p></w:tc></w:tr>
  </w:tbl>
  <w:p><w:r><w:t>Total 3</w:t></w:r></w:p>
</w:body></w:document>"""})


def _xlsx(path):
    return _zip(path, {
        "xl/workbook.xml": f'<workbook {S} {R}><sheets><sheet name="Prices" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst {S}><si><t>Item</t></si><si><t>Tea</t></si></sst>',
        "xl/worksheets/sheet1.xml": f"""<worksheet {S}><sheetData>
  <row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="inlineStr"><is><t>Price</t></is></c></row>
  <row r="2"><c r="A2" t="s"><v>1</v></c><c r="C2"><v>3.5</v></c></row>
</sheetData></worksheet>"""})


def _pptx(path):
    slide = '<p:sld xmlns:p="x" {A}><a:p><a:r><a:t>{t}</a:t></a:r></a:p></p:sld>'
    return _zip(path, {"ppt/slides/slide2.xml": slide.format(A=A, t="Second"),
                       "ppt/slides/slide10.xml": slide.format(A=A, t="Tenth"),
                       "ppt/slides/slide1.xml": slide.format(A=A, t="First")})


def _pdf(path, text="Hello PDF"):
    """A one-page PDF with a text layer, xref offsets computed."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))
    return path


# ── extraction ────────────────────────────────────────────────────────────────

def test_kind(tmp_path):
    (tmp_path / "a.py").write_text("print(1)")
    (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02")
    assert att.kind(tmp_path / "a.py") == "text"
    assert att.kind(tmp_path / "b.bin") == "binary"
    assert att.kind(tmp_path / "c.JPG") == "image"
    assert att.kind(tmp_path / "d.docx") == "docx"


def test_docx_paragraphs_and_tables(tmp_path):
    text = att.extract_text(_docx(tmp_path / "x.docx"))
    assert text.splitlines()[0] == "Invoice\t2026"
    assert "| Item | Price |" in text and "| Tea | 3 |" in text
    assert text.endswith("Total 3")


def test_xlsx_shared_inline_and_gaps(tmp_path):
    text = att.extract_text(_xlsx(tmp_path / "x.xlsx"))
    assert "## Sheet: Prices" in text
    assert "Item |  | Price" in text      # column B is empty
    assert "Tea |  | 3.5" in text


def test_pptx_slides_in_number_order(tmp_path):
    text = att.extract_text(_pptx(tmp_path / "x.pptx"))
    assert text.index("First") < text.index("Second") < text.index("Tenth")


def test_pdf(tmp_path):
    pytest.importorskip("pypdf")
    text = att.extract_text(_pdf(tmp_path / "x.pdf"))
    assert "--- page 1 ---" in text and "Hello PDF" in text


def test_pdf_without_pypdf_says_how_to_install(tmp_path, monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "pypdf":
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(att.AttachmentError, match=r"vyrii\[docs\]"):
        att.extract_text(_pdf(tmp_path / "y.pdf"))


def test_broken_docx_and_binary(tmp_path):
    (tmp_path / "bad.docx").write_bytes(b"not a zip")
    with pytest.raises(att.AttachmentError):
        att.extract_text(tmp_path / "bad.docx")
    (tmp_path / "x.bin").write_bytes(b"\x00" * 10)
    with pytest.raises(att.AttachmentError):
        att.extract_text(tmp_path / "x.bin")


def test_image_b64_small_png_unchanged(tmp_path):
    (tmp_path / "r.png").write_bytes(PNG)
    assert base64.b64decode(att.image_b64(tmp_path / "r.png")) == PNG


def test_image_b64_large_is_shrunk(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", (3000, 1500), "blue").save(tmp_path / "big.webp", "WEBP")
    out = Image.open(io.BytesIO(base64.b64decode(att.image_b64(tmp_path / "big.webp"))))
    assert out.format == "JPEG" and max(out.size) == att.MAX_IMAGE_SIDE


# ── expand_messages ───────────────────────────────────────────────────────────

def test_expand_text_and_image(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "notes.md").write_text("# Notes\nbuy milk", encoding="utf-8")
    (tmp_path / "input" / "r.png").write_bytes(PNG)
    msgs = [{"role": "system", "content": "be brief"},
            {"role": "user", "content": "what is here?",
             "attachments": ["input/notes.md", "input/r.png"]}]
    out = att.expand_messages(msgs, tmp_path)
    assert out[0] is msgs[0]
    u = out[1]
    assert "attachments" not in u
    assert u["content"].startswith("[File: notes.md]\n# Notes\nbuy milk\n[End of file: notes.md]")
    assert "[Image: r.png]" in u["content"]
    assert u["content"].endswith("what is here?")
    assert base64.b64decode(u["images"][0]) == PNG


def test_expand_errors_are_text_not_exceptions(tmp_path):
    out = att.expand_messages([{"role": "user", "content": "q",
                                "attachments": ["input/gone.txt", "../../etc/passwd"]}], tmp_path)
    c = out[0]["content"]
    assert "[File: gone.txt — could not be read: file not found]" in c
    assert "path outside" in c
    assert "images" not in out[0]


def test_expand_cuts_long_text(tmp_path, monkeypatch):
    monkeypatch.setattr(att, "MAX_TEXT_CHARS", 10)
    (tmp_path / "long.txt").write_text("x" * 50)
    c = att.expand_messages([{"role": "user", "content": "",
                              "attachments": ["long.txt"]}], tmp_path)[0]["content"]
    assert "x" * 10 + "\n[… cut: the file has 50 characters" in c
    assert "x" * 11 not in c


# ── engine ────────────────────────────────────────────────────────────────────

def test_clean_messages_keeps_images_and_reads_data_urls():
    b64 = base64.b64encode(PNG).decode()
    clean = engine._clean_messages([
        {"role": "user", "content": "", "images": [b64]},                 # image only
        {"role": "user", "content": [{"type": "text", "text": "look"},
                                     {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                                     {"type": "image_url", "image_url": {"url": "http://x/y.png"}}]},
        {"role": "user", "content": "plain"},
        {"role": "user", "content": ""},                                   # dropped, as before
    ])
    assert clean == [{"role": "user", "content": "", "images": [b64]},
                     {"role": "user", "content": "look", "images": [b64]},
                     {"role": "user", "content": "plain"}]


def test_openai_messages_turn_images_into_parts():
    b64 = base64.b64encode(PNG).decode()
    out = engine._openai_messages([{"role": "user", "content": "what?", "images": [b64]},
                                   {"role": "assistant", "content": "a"}])
    assert out[0]["content"] == [
        {"type": "text", "text": "what?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]
    assert out[1] == {"role": "assistant", "content": "a"}


def test_smart_ctx_counts_images():
    assert engine.smart_ctx([{"role": "user", "content": "hi", "images": ["x"] * 4}]) > engine.CTX_START


def test_ollama_payload_has_images(monkeypatch):
    sent = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_lines(self):
            yield json.dumps({"message": {"content": "a red dot"}}).encode()
            yield json.dumps({"done": True}).encode()

    def post(url, json=None, **kw):
        sent.update(url=url, payload=json)
        return Resp()
    monkeypatch.setattr(engine.requests, "post", post)
    out = "".join(engine.stream_chat([{"role": "user", "content": "?", "images": ["AAA"]}], "m"))
    assert out == "a red dot"
    assert sent["url"].endswith("/api/chat")
    assert sent["payload"]["messages"] == [{"role": "user", "content": "?", "images": ["AAA"]}]


def test_model_capabilities(monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self): return {"capabilities": ["completion", "vision"]}
    monkeypatch.setattr(engine.requests, "post", lambda *a, **k: R())
    assert engine.model_capabilities("gemma3:4b") == ["completion", "vision"]
    assert engine.model_capabilities("x", backend=engine.BACKEND_OPENAI) is None


# ── endpoints ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert pathlib.Path.home() == tmp_path
    from vyrii import flask_api
    seen = {}

    def fake_stream(messages, model, *a, **k):
        seen["messages"] = messages
        yield "ok"
    monkeypatch.setattr(flask_api, "stream_chat", fake_stream)
    monkeypatch.setattr(flask_api, "complete", lambda m, *a, **k: "".join(fake_stream(m, *a)))
    monkeypatch.setattr(flask_api, "smart_ctx", lambda m: 4096)
    app = flask_api.create_app()
    app.testing = True
    c = app.test_client()
    c.seen = seen
    c.home = tmp_path / ".vyrii"
    return c


def _upload(client, name, data, unique=True):
    q = "dest=input" + ("&unique=1" if unique else "")
    return client.post("/vyrii/files/upload?" + q,
                       data={"files": (io.BytesIO(data), name)},
                       content_type="multipart/form-data").get_json()


def test_upload_unique_never_overwrites(client):
    a = _upload(client, "r.png", PNG)
    b = _upload(client, "r.png", b"second")
    assert a["saved"] == [str(pathlib.Path("input/r.png"))]
    assert b["saved"] == [str(pathlib.Path("input/r (1).png"))]
    assert (client.home / "input" / "r.png").read_bytes() == PNG
    c = _upload(client, "r.png", b"third", unique=False)           # the Files tab: overwrite, as before
    assert c["saved"] == [str(pathlib.Path("input/r.png"))]
    assert (client.home / "input" / "r.png").read_bytes() == b"third"


def test_raw_serves_file_and_refuses_outside(client):
    _upload(client, "r.png", PNG)
    assert client.get("/vyrii/files/raw?path=input/r.png").data == PNG
    assert client.get("/vyrii/files/raw?path=../x").status_code == 400
    assert client.get("/vyrii/files/raw?path=input/none.png").status_code == 404


def test_chat_expands_attachments(client):
    _upload(client, "r.png", PNG)
    (client.home / "input" / "t.txt").write_text("the text", encoding="utf-8")
    r = client.post("/v1/chat/completions", json={
        "model": "m", "stream": False,
        "messages": [{"role": "user", "content": "describe",
                      "attachments": ["input/t.txt", "input/r.png"]}]})
    assert r.status_code == 200
    m = client.seen["messages"][0]
    assert "[File: t.txt]\nthe text" in m["content"] and m["content"].endswith("describe")
    assert base64.b64decode(m["images"][0]) == PNG


# ── check / list (the chat refuses a file the model would never see) ─────────

def test_check(tmp_path, monkeypatch):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "r.png").write_bytes(PNG)
    (tmp_path / "input" / "t.txt").write_text("hello")
    (tmp_path / "input" / "empty.txt").write_text("  \n")
    (tmp_path / "input" / "x.bin").write_bytes(b"\x00\x01")
    assert att.check(tmp_path, "input/r.png") == {"ok": True, "kind": "image"}
    assert att.check(tmp_path, "input/t.txt") == {"ok": True, "kind": "file", "chars": 5}
    assert att.check(tmp_path, "input/empty.txt") == {"ok": False, "error": "the file has no text"}
    assert not att.check(tmp_path, "input/x.bin")["ok"]
    assert not att.check(tmp_path, "input/none.txt")["ok"]


def test_check_pdf_without_pypdf(tmp_path, monkeypatch):
    import builtins
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name)) if name == "pypdf"
                        else real(name, *a, **k))
    _pdf(tmp_path / "doc.pdf")
    r = att.check(tmp_path, "doc.pdf")
    assert r["ok"] is False and "pypdf" in r["error"]


def test_list_input_newest_first(tmp_path):
    import os, time
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "old.txt").write_text("a")
    (tmp_path / "input" / "new.png").write_bytes(PNG)
    t = time.time()
    os.utime(tmp_path / "input" / "old.txt", (t - 100, t - 100))
    files = att.list_input(tmp_path)
    assert [f["name"] for f in files] == ["new.png", "old.txt"]
    assert files[0]["kind"] == "image" and files[0]["path"] == "input/new.png"
    assert att.list_input(tmp_path / "nowhere") == []


def test_attach_endpoints(client):
    _upload(client, "r.png", PNG)
    files = client.get("/vyrii/attach/list").get_json()["files"]
    assert [f["path"] for f in files] == ["input/r.png"]
    assert client.get("/vyrii/attach/check?path=input/r.png").get_json() == {"ok": True, "kind": "image"}
    assert client.get("/vyrii/attach/check?path=input/no.pdf").get_json()["ok"] is False
