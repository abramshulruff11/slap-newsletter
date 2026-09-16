"""
library_json.py — style-matching JSON writer for the curated library files.

A review pass rewrites a whole library file to change a handful of status
fields. If the writer does not reproduce that file's own formatting, the diff
fills with reflow and the real edits become invisible — and for a reviewed
library the diff is the only record of what the pass decided. The first run of
the GIF applier expanded every compact tags[] and turned 19 status changes into
a 2,175-line diff.

The two libraries are NOT formatted the same way, which is why this matches the
file instead of imposing one convention:

    prompts/gif_library.DRAFT.json   compact leaf arrays: "tags": ["a", "b"]
    prompts/meme_library.DRAFT.json  expanded, i.e. plain json.dumps(indent=2)

Verified: plain json.dumps(indent=2) reproduces the meme library byte for byte,
and the compact renderer reproduces the GIF library byte for byte.

Both apply scripts import this. Per the repo-root rule in CLAUDE.md there is
exactly one copy; do not inline it into either caller.
"""

import json
import re

# A JSON array whose every element is a plain string, as json.dumps renders it
# across multiple lines. An array of objects opens with '{' and never matches.
_STRING_ARRAY = re.compile(r'\[\n((?:\s*"(?:[^"\\]|\\.)*",?\n)+)\s*\]')

# A complete one-line string array in an existing file, e.g. ["a", "b"] — the
# marker that the file uses the compact convention.
_INLINE_ARRAY = re.compile(r':\s*\["(?:[^"\\]|\\.)*"(?:,\s*"(?:[^"\\]|\\.)*")*\]')


def _compact(text: str) -> str:
    def collapse(match):
        items = [line.strip() for line in match.group(1).splitlines() if line.strip()]
        return "[" + " ".join(items) + "]"

    return _STRING_ARRAY.sub(collapse, text)


def uses_compact_arrays(original: str) -> bool:
    """True if this file keeps short string arrays on one line."""
    return bool(_INLINE_ARRAY.search(original))


def dumps_matching_style(obj, original: str) -> str:
    """Serialise obj the way `original` was already formatted.

    Pass the exact text the file was read from. Falls back to plain
    json.dumps(indent=2) when the file shows no compact arrays.
    """
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    return _compact(text) if uses_compact_arrays(original) else text
