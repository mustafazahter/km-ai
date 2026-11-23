"""Readable text from HTML with the standard library only."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "head", "nav", "footer", "form"}
BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header", "main",
              "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr", "dd", "dt", "figcaption"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in SKIP_TAGS and tag != "head":
            self._skip += 1
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in SKIP_TAGS and tag != "head" and self._skip:
            self._skip -= 1
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._link_text).split())))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._link_text.append(data)


def html_to_text(markup: str) -> tuple[str, str]:
    """Return ``(title, text)``."""
    p = _Extractor()
    try:
        p.feed(markup)
        p.close()
    except Exception:  # malformed markup: fall back to tag stripping
        text = re.sub(r"<[^>]+>", " ", markup)
        return "", normalize(html.unescape(text))
    return " ".join(p.title.split()), normalize("".join(p.parts))


def normalize(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def extract_links(markup: str) -> list[tuple[str, str]]:
    p = _Extractor()
    try:
        p.feed(markup)
        p.close()
    except Exception:
        return []
    return p.links
