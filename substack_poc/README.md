# SLAP → Substack (proof of concept)

Automatically turn the `newsletter_substack.html` that SLAP produces into a
Substack post. Built on the unofficial [`python-substack`](https://pypi.org/project/python-substack/)
library (Substack has no official API).

The pipeline has two decoupled stages:

```
newsletter_substack.html
        │  convert.py        (stdlib only, no login)  ← the fragile part
        ▼
   clean blocks  ──►  build_post()  ──►  python-substack Post  ──►  Substack
                                                                  (publish.py)
```

## What's verified vs. what needs your login

- ✅ **Verified working** on real archive output (no credentials needed):
  parsing, junk-stripping, and building the exact Substack post payload.
  Run the dry-run below and you'll see it.
- ⏳ **Needs your Substack login** to actually run: creating a draft / publishing.
  The code is wired; you just add credentials to `.env`.

## Setup

```powershell
# from this folder
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 1. Dry run (no login, no risk) — start here

Builds and prints the full post payload without touching the network:

```powershell
.\.venv\Scripts\python.exe publish.py "..\Archive\2026-06-04\newsletter_substack.html"
```

Add `--out body_preview.json` to dump the exact ProseMirror body that would be
sent to Substack, so you can inspect it.

You can also run the converter alone to eyeball the blocks:

```powershell
.\.venv\Scripts\python.exe convert.py "..\Archive\2026-06-04\newsletter_substack.html"
```

## 2. Create a draft (needs login)

```powershell
copy .env.example .env   # then edit .env (see "Getting cookies" below)
.\.venv\Scripts\python.exe publish.py "..\Archive\2026-06-04\newsletter_substack.html" --draft
```

This creates a **draft** on Substack — nothing goes live. Open Substack and
review it. This is the safe way to validate formatting fidelity for real.

## 3. Publish live (needs login)

Only when you're happy with how drafts look:

```powershell
.\.venv\Scripts\python.exe publish.py "..\Archive\2026-06-04\newsletter_substack.html" --publish
```

## Getting cookies (recommended auth)

Substack often uses passwordless "magic link" login, so email+password may fail.
The reliable route:

1. Log into your Substack in a browser.
2. Open dev tools → Application/Storage → Cookies → `https://substack.com`.
3. Either copy the cookie string into `SUBSTACK_COOKIES_STRING` in `.env`, or
   save the cookies as a JSON object `{ "name": "value", ... }` to `cookies.json`
   and point `SUBSTACK_COOKIES_PATH` at it.

Cookies expire over time, so you'll need to refresh them periodically.

## Known limitations (PoC scope)

- **Tweets render as links, not embeds.** `python-substack` has no first-class
  tweet-embed node. Tweets currently become clickable link paragraphs. Real
  embeds are the #1 follow-up (Substack auto-embeds tweet URLs in its editor,
  but the API needs the embed node built explicitly).
- **Images are hot-linked** by URL (giphy/imgflip). Substack usually re-hosts on
  publish; if any fail, we'd switch to uploading via `api.get_image()`.
- **Box scores** (`box_score_*.png`) are local files in each archive folder and
  are not yet uploaded; the "Box Scores" heading currently has no images under it.
- Unofficial API — Substack can change it at any time. This is not ToS-blessed.

## Files

- `convert.py` — HTML → clean blocks (stdlib only; fully testable offline)
- `publish.py` — blocks → `python-substack` Post → dry-run / draft / publish
- `.env.example` — auth template (copy to `.env`)
