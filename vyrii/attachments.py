"""Files attached to a chat message — text for any model, images for vision models.

The chat uploads a file with /vyrii/files/upload into ~/.vyrii/input/ and sends
its path (relative to ~/.vyrii) in the message's ``attachments`` list.
expand_messages() turns those paths into what a model API takes:

  - a document (text, code, csv, docx, xlsx, pptx, pdf) -> its text, put into
    the message content before the user's own words;
  - an image -> base64 in the message's ``images`` list (Ollama's field;
    engine.py converts it to image_url parts for OpenAI-compatible servers).

docx / xlsx / pptx are read with the standard library (zip + XML), so they need
no extra package. PDF needs pypdf (``pip install vyrii[docs]``). Pillow, when
installed, shrinks large images and converts formats a model may not take;
without it the image is sent as it is.
"""
from __future__ import annotations

import base64
import io
import pathlib
import re
import zipfile
import xml.etree.ElementTree as ET

INPUT_DIR = "input"            # ~/.vyrii/input/ — where the chat uploads files
MAX_TEXT_CHARS = 60_000        # per file; the rest is cut with a note
MAX_IMAGE_SIDE = 1280          # px; larger images are shrunk (Pillow only)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


class AttachmentError(Exception):
    """A file that cannot be turned into text or an image; the message says why."""


def kind(path: pathlib.Path) -> str:
    """'image', 'pdf', 'docx', 'xlsx', 'pptx', 'text' or 'binary'."""
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in (".pdf", ".docx", ".xlsx", ".pptx"):
        return ext[1:]
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return "binary"
    return "binary" if b"\x00" in head else "text"


# ── text extraction ───────────────────────────────────────────────────────────

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _docx_par(p) -> str:
    out = []
    for el in p.iter():
        if el.tag == _W + "t":
            out.append(el.text or "")
        elif el.tag == _W + "tab":
            out.append("\t")
        elif el.tag in (_W + "br", _W + "cr"):
            out.append("\n")
    return "".join(out)


def _docx(path: pathlib.Path) -> str:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(_W + "body")
    lines = []
    for child in (body if body is not None else []):
        if child.tag == _W + "p":
            lines.append(_docx_par(child))
        elif child.tag == _W + "tbl":
            for tr in child.iter(_W + "tr"):
                cells = [" ".join(_docx_par(p) for p in tc.iter(_W + "p")).strip()
                         for tc in tr.findall(_W + "tc")]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
    return "\n".join(lines).strip()


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return max(n - 1, 0)


def _xlsx(path: pathlib.Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(_S + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_S + "t")))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).findall(_PR + "Relationship"):
                target = r.get("Target", "").lstrip("/")
                rels[r.get("Id")] = target if target.startswith("xl/") else "xl/" + target
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        out = []
        for sheet in wb.iter(_S + "sheet"):
            part = rels.get(sheet.get(_R + "id"), "")
            if part not in names:
                continue
            out.append(f"## Sheet: {sheet.get('name', '')}")
            for row in ET.fromstring(z.read(part)).iter(_S + "row"):
                cells: list[str] = []
                for c in row.findall(_S + "c"):
                    t = c.get("t", "")
                    v = c.find(_S + "v")
                    if t == "s" and v is not None:
                        try:
                            val = shared[int(v.text)]
                        except (ValueError, IndexError):
                            val = v.text or ""
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(_S + "t"))
                    elif t == "b" and v is not None:
                        val = "TRUE" if v.text == "1" else "FALSE"
                    else:
                        val = (v.text or "") if v is not None else ""
                    i = _col_index(c.get("r", ""))
                    while len(cells) < i:
                        cells.append("")
                    cells.append(val)
                if any(x.strip() for x in cells):
                    out.append(" | ".join(cells))
            out.append("")
    return "\n".join(out).strip()


def _pptx(path: pathlib.Path) -> str:
    with zipfile.ZipFile(path) as z:
        slides = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        slides.sort(key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        out = []
        for i, name in enumerate(slides, 1):
            out.append(f"## Slide {i}")
            for p in ET.fromstring(z.read(name)).iter(_A + "p"):
                line = "".join(t.text or "" for t in p.iter(_A + "t")).strip()
                if line:
                    out.append(line)
            out.append("")
    return "\n".join(out).strip()


def _pdf(path: pathlib.Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise AttachmentError("PDF needs pypdf: pip install vyrii[docs]")
    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"--- page {i} ---\n{text}")
    if not pages:
        raise AttachmentError("the PDF has no text layer (a scan?)")
    return "\n\n".join(pages)


_cache: dict[tuple, str] = {}


def extract_text(path: pathlib.Path) -> str:
    """The text of a document; AttachmentError when it has none or cannot be read."""
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key in _cache:
        return _cache[key]
    k = kind(path)
    try:
        if k == "text":
            text = path.read_text(encoding="utf-8", errors="replace")
        elif k == "docx":
            text = _docx(path)
        elif k == "xlsx":
            text = _xlsx(path)
        elif k == "pptx":
            text = _pptx(path)
        elif k == "pdf":
            text = _pdf(path)
        else:
            raise AttachmentError("not a text file (supported: text, docx, xlsx, pptx, pdf, images)")
    except AttachmentError:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise AttachmentError(f"cannot read the {k} file: {e}")
    if len(_cache) > 64:
        _cache.clear()
    _cache[key] = text
    return text


# ── images ────────────────────────────────────────────────────────────────────

def image_b64(path: pathlib.Path) -> str:
    """The image as base64 — shrunk to MAX_IMAGE_SIDE and made PNG/JPEG when Pillow is installed."""
    data = path.read_bytes()
    try:
        from PIL import Image
    except ImportError:
        return base64.b64encode(data).decode("ascii")
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        if fmt in ("PNG", "JPEG") and max(img.size) <= MAX_IMAGE_SIDE:
            return base64.b64encode(data).decode("ascii")
        img.seek(0)
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buf = io.BytesIO()
        if img.mode in ("RGBA", "LA", "P"):
            img.convert("RGBA").save(buf, "PNG")
        else:
            img.convert("RGB").save(buf, "JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return base64.b64encode(data).decode("ascii")


# ── messages ──────────────────────────────────────────────────────────────────

def resolve(home: pathlib.Path, rel: str) -> pathlib.Path:
    p = (home / str(rel).lstrip("/\\")).resolve()
    if not str(p).startswith(str(home.resolve())):
        raise AttachmentError("path outside ~/.vyrii/")
    if not p.is_file():
        raise AttachmentError("file not found")
    return p


def check(home: pathlib.Path, rel: str) -> dict:
    """Can this attachment be read? {"ok", "kind": "image"|"file", "chars"} or {"ok": False, "error"}.

    The chat asks right after the upload, so a file the model would never see
    (a PDF without pypdf, a scan, a binary file) is refused at once instead of
    turning silently into an error note inside the message.
    """
    try:
        p = resolve(home, rel)
        if kind(p) == "image":
            return {"ok": True, "kind": "image"}
        text = extract_text(p)
        if not text.strip():
            raise AttachmentError("the file has no text")
        return {"ok": True, "kind": "file", "chars": len(text)}
    except (AttachmentError, OSError) as e:
        return {"ok": False, "error": str(e)}


def list_input(home: pathlib.Path) -> list[dict]:
    """Files uploaded earlier to ~/.vyrii/input/, newest first."""
    root = home / INPUT_DIR
    items = []
    if root.is_dir():
        for p in root.iterdir():
            if p.is_file():
                st = p.stat()
                items.append({"path": f"{INPUT_DIR}/{p.name}", "name": p.name,
                              "size": st.st_size, "mtime": st.st_mtime,
                              "kind": "image" if kind(p) == "image" else "file"})
    items.sort(key=lambda x: -x["mtime"])
    return items


def expand_messages(messages: list[dict], home: pathlib.Path) -> list[dict]:
    """Messages with ``attachments`` -> text in the content, images in ``images``.

    Messages without attachments are returned as they are.
    """
    out = []
    for m in messages:
        atts = m.get("attachments") if isinstance(m, dict) else None
        if not atts:
            out.append(m)
            continue
        blocks: list[str] = []
        images: list[str] = list(m.get("images") or [])
        for rel in atts:
            name = pathlib.PurePath(str(rel)).name
            try:
                p = resolve(home, rel)
                if kind(p) == "image":
                    images.append(image_b64(p))
                    blocks.append(f"[Image: {name}]")
                    continue
                text = extract_text(p)
                if len(text) > MAX_TEXT_CHARS:
                    text = (text[:MAX_TEXT_CHARS]
                            + f"\n[… cut: the file has {len(text):,} characters, "
                              f"the first {MAX_TEXT_CHARS:,} are shown]")
                blocks.append(f"[File: {name}]\n{text}\n[End of file: {name}]")
            except (AttachmentError, OSError) as e:
                blocks.append(f"[File: {name} — could not be read: {e}]")
        content = m.get("content") or ""
        if isinstance(content, str):
            content = "\n\n".join(blocks + ([content] if content.strip() else []))
        elif isinstance(content, list) and blocks:
            content = [{"type": "text", "text": "\n\n".join(blocks)}] + content
        new = {k: v for k, v in m.items() if k != "attachments"}
        new["content"] = content
        if images:
            new["images"] = images
        out.append(new)
    return out
