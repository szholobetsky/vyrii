"""Web fetch utilities — fetch_text and extract_links only.
DDG search lives in ChatAdapter._web_ddg_search (shared with 1bcoder flows).
"""
from __future__ import annotations

import re
import requests

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; vyrii/0.1; +https://github.com/szholobetsky)"}
_MAX_TEXT = 6000
_MAX_LINKS = 60


def fetch_text(url: str, timeout: int = 15) -> str:
    resp = requests.get(url, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    try:
        from lxml import html as lhtml
        tree = lhtml.fromstring(resp.content)
        for tag in tree.xpath("//script|//style|//nav|//footer|//header|//aside"):
            parent = tag.getparent()
            if parent is not None:
                parent.remove(tag)
        try:
            text = tree.text_content()
        except AttributeError:
            text = "".join(tree.itertext())
    except Exception:
        text = resp.text

    text = re.sub(r'\s+', ' ', text).strip()
    return text[:_MAX_TEXT]


def ddg_search(query: str, n: int = 5) -> list[tuple[str, str, str]]:
    """Return list of (title, url, snippet) from DuckDuckGo HTML — no external library."""
    from html.parser import HTMLParser
    from urllib.parse import parse_qs, urlparse, unquote

    class _DDG(HTMLParser):
        def __init__(self):
            super().__init__()
            self.results: list[tuple[str, str, str]] = []
            self._cur: dict = {}
            self._in: str | None = None

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            cls = d.get("class", "")
            if tag == "a" and "result__a" in cls:
                href = d.get("href", "")
                try:
                    params = parse_qs(urlparse(href).query)
                    url = params.get("uddg", [""])[0]
                    if not url:
                        url = unquote(href)
                except Exception:
                    url = href
                self._cur["url"] = url
                self._in = "title"
            elif tag == "a" and "result__snippet" in cls:
                self._in = "snippet"

        def handle_endtag(self, tag):
            if self._in and tag == "a":
                if "url" in self._cur and "title" in self._cur:
                    self.results.append((
                        self._cur.get("title", ""),
                        self._cur.get("url", ""),
                        self._cur.get("snippet", ""),
                    ))
                    self._cur = {}
                self._in = None

        def handle_data(self, data):
            if self._in == "title":
                self._cur["title"] = self._cur.get("title", "") + data
            elif self._in == "snippet":
                self._cur["snippet"] = self._cur.get("snippet", "") + data

    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers=_HEADERS,
        timeout=15,
    )
    parser = _DDG()
    parser.feed(resp.text)
    return parser.results[:n]


def extract_links(page_url: str, base_url: str) -> list[str]:
    from urllib.parse import urlparse
    try:
        resp = requests.get(page_url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        from lxml import html as lhtml
        tree = lhtml.fromstring(resp.content, base_url=page_url)
        tree.make_links_absolute()
    except Exception:
        return []

    base_parsed = urlparse(base_url)
    base_prefix = f"{base_parsed.scheme}://{base_parsed.netloc}"

    links: list[str] = []
    seen: set[str] = set()
    for el in tree.xpath("//a[@href]"):
        href = el.get("href", "").split("#")[0].rstrip("/")
        if not href or href in seen:
            continue
        seen.add(href)
        if href.startswith(base_prefix) and href != page_url.rstrip("/"):
            links.append(href)
        if len(links) >= _MAX_LINKS:
            break
    return links
