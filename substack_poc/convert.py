"""
SLAP newsletter HTML  ->  clean, structured "blocks" ready for Substack.

This module does the *hard, fragile* part of the pipeline: taking the
messy `newsletter_substack.html` that SLAP produces and turning it into a
clean, JSON-serializable list of blocks. It has ZERO dependencies beyond
the Python standard library and needs NO Substack login, so you can run it
and see exactly what would be published before any credentials are involved.

Block shapes produced (all JSON-serializable):

    {"type": "heading",   "level": 1|2, "text": "..."}
    {"type": "paragraph", "tokens": [{"content": "..", "marks": [...]}, ...]}
    {"type": "tweet",     "url": "https://twitter.com/.../status/..."}
    {"type": "image",     "src": "https://...", "alt": "..."}
    {"type": "hr"}

`marks` follow python-substack's format, e.g.
    [{"type": "strong"}]
    [{"type": "link", "href": "https://example.com"}]

Run it directly to see a summary for a given file:

    python convert.py "../Archive/2026-06-04/newsletter_substack.html"
"""

from __future__ import annotations

import copy
import json
import sys
from html.parser import HTMLParser
from typing import Dict, List, Optional


# Tags whose textual content we capture as inline runs.
_INLINE_MARK_TAGS = {
    "strong": {"type": "strong"},
    "b": {"type": "strong"},
    "em": {"type": "em"},
    "i": {"type": "em"},
}


class _SlapHTMLParser(HTMLParser):
    """Streaming parser that emits a flat list of content blocks.

    Design notes:
      * We only capture text that lives *inside* a recognized block element
        (h1/h2/p). Any stray text at the <body> level -- e.g. the LLM
        preamble ("I'll edit this newsletter...") or stray ``` code fences
        that SLAP sometimes leaks -- is therefore dropped automatically.
      * HTML comments (the <!-- EDITOR FLAG ... --> notes) are ignored.
      * Malformed <img> tags (SLAP occasionally emits broken attributes) are
        tolerated: we just read whatever `src`/`alt` we can find.
    """

    def __init__(self) -> None:
        # convert_charrefs=True -> entities like &amp; arrive already decoded.
        super().__init__(convert_charrefs=True)
        self.blocks: List[Dict] = []

        # Current open block we are accumulating text into (heading/paragraph),
        # or None when we are between blocks.
        self._block: Optional[Dict] = None
        # Inline formatting marks currently in effect (stack).
        self._marks: List[Dict] = []
        # When inside a <p class="tweet-url"> we buffer the URL text here.
        self._tweet_buf: Optional[List[str]] = None

    # -- block lifecycle ---------------------------------------------------
    def _open_paragraph(self) -> None:
        self._block = {"type": "paragraph", "tokens": []}

    def _open_heading(self, level: int) -> None:
        self._block = {"type": "heading", "level": level, "text": ""}

    def _close_block(self) -> None:
        block = self._block
        self._block = None
        self._marks = []
        if block is None:
            return
        if block["type"] == "heading":
            block["text"] = block["text"].strip()
            if block["text"]:
                self.blocks.append(block)
        elif block["type"] == "paragraph":
            # Drop whitespace-only tokens, then drop empty paragraphs.
            tokens = [t for t in block["tokens"] if t.get("content", "").strip()]
            if tokens:
                # Trim leading/trailing whitespace of the run as a whole.
                tokens[0]["content"] = tokens[0]["content"].lstrip()
                tokens[-1]["content"] = tokens[-1]["content"].rstrip()
                block["tokens"] = tokens
                self.blocks.append(block)

    # -- HTMLParser hooks --------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_d = {k: (v or "") for k, v in attrs}

        if tag in ("h1", "h2"):
            self._close_block()
            self._open_heading(1 if tag == "h1" else 2)
            return

        if tag == "p":
            self._close_block()
            classes = attrs_d.get("class", "")
            if "tweet-url" in classes:
                self._tweet_buf = []  # capture the URL text, emit on </p>
            else:
                self._open_paragraph()
            return

        if tag == "img":
            src = attrs_d.get("src", "").strip()
            if src:
                self.blocks.append(
                    {"type": "image", "src": src, "alt": attrs_d.get("alt", "").strip()}
                )
            return

        if tag == "hr":
            self._close_block()
            self.blocks.append({"type": "hr"})
            return

        if tag == "br" and self._block and self._block["type"] == "paragraph":
            self._block["tokens"].append({"content": " "})
            return

        if tag in _INLINE_MARK_TAGS:
            self._marks.append(copy.deepcopy(_INLINE_MARK_TAGS[tag]))
            return

        if tag == "a":
            href = attrs_d.get("href", "").strip()
            if href:
                self._marks.append({"type": "link", "href": href})
            else:
                # Push a no-op so the matching </a> pop stays balanced.
                self._marks.append({"type": "_noop"})

    def handle_startendtag(self, tag: str, attrs) -> None:
        # Self-closing tags like <img ... />. Route img/hr/br through starttag
        # logic; ignore the (non-existent) end.
        if tag in ("img", "hr", "br"):
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h1", "h2", "p"):
            if self._tweet_buf is not None:
                url = "".join(self._tweet_buf).strip()
                self._tweet_buf = None
                if url:
                    self.blocks.append({"type": "tweet", "url": url})
            else:
                self._close_block()
            return

        if tag in _INLINE_MARK_TAGS or tag == "a":
            if self._marks:
                self._marks.pop()

    def handle_data(self, data: str) -> None:
        if self._tweet_buf is not None:
            self._tweet_buf.append(data)
            return
        if self._block is None:
            return  # stray body-level text (preamble / code fences) -> dropped
        if self._block["type"] == "heading":
            self._block["text"] += data
        else:  # paragraph
            token: Dict = {"content": data}
            real_marks = [m for m in self._marks if m.get("type") != "_noop"]
            if real_marks:
                token["marks"] = copy.deepcopy(real_marks)
            self._block["tokens"].append(token)


def html_to_blocks(html: str) -> List[Dict]:
    """Parse SLAP newsletter HTML into a list of clean content blocks."""
    parser = _SlapHTMLParser()
    parser.feed(html)
    parser.close()
    parser._close_block()  # flush any trailing open block
    return parser.blocks


def block_summary(blocks: List[Dict]) -> str:
    """Human-readable one-line-per-block summary (for eyeballing output)."""
    counts: Dict[str, int] = {}
    lines: List[str] = []
    for b in blocks:
        counts[b["type"]] = counts.get(b["type"], 0) + 1
        if b["type"] == "heading":
            lines.append(f'  H{b["level"]}  {b["text"]}')
        elif b["type"] == "paragraph":
            text = "".join(t["content"] for t in b["tokens"])
            preview = (text[:90] + "...") if len(text) > 90 else text
            lines.append(f"  P    {preview}")
        elif b["type"] == "tweet":
            lines.append(f'  TWT  {b["url"]}')
        elif b["type"] == "image":
            lines.append(f'  IMG  [{b["alt"]}] {b["src"][:70]}')
        elif b["type"] == "hr":
            lines.append("  ---")
    header = "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    return f"{len(blocks)} blocks  ({header})\n" + "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python convert.py <path-to-newsletter_substack.html> [--json]")
        raise SystemExit(2)
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        blocks = html_to_blocks(f.read())
    if "--json" in sys.argv:
        print(json.dumps(blocks, indent=2, ensure_ascii=False))
    else:
        print(block_summary(blocks))
