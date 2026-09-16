"""
library_studio_server.py — the local host for library_studio.html.

WHY THIS EXISTS
    The page was meant to be opened by double-clicking the .html file. It
    cannot be. The File System Access API — the only way a web page can write
    back to a file it opened — requires a SECURE CONTEXT, and file:// is not
    one. From file:// the page degrades to "pick a file, edit, download a copy,
    move the copy over the original", which is exactly the shuffling the page
    was supposed to remove.

    http://localhost IS a secure context. So this serves the repo on localhost
    and the page gets its capabilities back. It also exposes a tiny PUT
    endpoint, which is better still: the page writes straight to the file with
    no picker and no permission prompt, so Save is one click, every time.

HOW IT IS MEANT TO BE RUN
    Double-click library_studio.bat. That is the whole interaction — no
    terminal, no arguments. This file is the thing the .bat starts.

    Or, if a terminal is already open:
        python -X utf8 library_studio_server.py

SAFETY
    - Binds 127.0.0.1 only, so nothing outside this machine can reach it.
    - PUT is restricted to an explicit allowlist of the two library files.
      Any other path is refused. A static file server that accepts arbitrary
      writes is a remote shell with extra steps.
    - Every write is validated as JSON and checked against the same
      round-trip rule library_json.py enforces, then written through a
      temporary file and replaced atomically, so an interrupted save cannot
      truncate a library.
    - A .bak copy of the previous contents is kept next to each file.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent
PORT = int(os.environ.get("LIBRARY_STUDIO_PORT", "8777"))

CRLF = "\r\n"

# The only paths PUT will ever write. Relative to the repo root, forward slashes.
WRITABLE = {
    "prompts/meme_library.DRAFT.json",
    "prompts/gif_library.DRAFT.json",
}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(REPO), **kw)

    def log_message(self, fmt, *args):
        if self.command == "PUT" or "library_studio" in self.path:
            sys.stderr.write(f"  {self.command} {self.path} — {fmt % args}\n")

    def send_head(self):
        # Stamp every served library with the file's mtime, so the page can tell
        # on save whether the file changed underneath it. Two windows on the same
        # library -- or a session editing the file while a tab holds an older
        # copy in memory -- otherwise clobber each other silently.
        rel = self.path.lstrip("/").split("?")[0]
        self._version = None
        if rel in WRITABLE:
            try:
                self._version = str((REPO / rel).stat().st_mtime_ns)
            except OSError:
                pass
        return super().send_head()

    def end_headers(self):
        # The page is re-read every time; a cached copy of an edited library
        # would silently show stale data.
        self.send_header("Cache-Control", "no-store")
        version = getattr(self, "_version", None)
        if version:
            self.send_header("X-Library-Version", version)
        super().end_headers()

    def _refuse(self, code, msg):
        body = json.dumps({"ok": False, "error": msg}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        rel = self.path.lstrip("/").split("?")[0]
        if rel not in WRITABLE:
            return self._refuse(403, f"{rel} is not writable. Only the two library files are.")

        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8")
        except Exception as e:
            return self._refuse(400, f"could not read the body: {e}")

        # Refuse to overwrite a file that changed since the page loaded it.
        # Without this, a tab holding a pre-edit copy silently reverts whatever
        # happened on disk in between -- which is a real scenario here, because
        # a Claude session edits these files too.
        expected = self.headers.get("If-Match")
        if expected:
            try:
                current = str((REPO / rel).stat().st_mtime_ns)
            except OSError:
                current = None
            if current and current != expected:
                return self._refuse(
                    409, "STALE: this file changed on disk after the page loaded it. "
                         "Nothing was written. Reload to pick up the newer version "
                         "(your unsaved edits in this tab will be lost), or save a copy first.")

        try:
            obj = json.loads(raw)
        except Exception as e:
            return self._refuse(400, f"not valid JSON, nothing written: {e}")

        # The page promises its output round-trips through library_json.py.
        # Check that here too rather than trusting it: this is the last point
        # before the bytes land on disk, and a reflowed library destroys the
        # diff that is the only record of what a review decided.
        #
        # Compare with line endings NORMALISED. dumps_matching_style() always
        # emits LF -- on Windows it never had to care, because Path.write_text
        # translated to CRLF at write time. The page, writing bytes directly,
        # has to match the file's CRLF itself. So the two disagree on newlines
        # by construction and agree on everything that matters. Comparing raw
        # would reject every legitimate save.
        sys.path.insert(0, str(REPO))
        try:
            import library_json
            raw_lf = raw.replace(CRLF, "\n")
            if library_json.dumps_matching_style(obj, raw_lf) != raw_lf:
                return self._refuse(
                    409, "the payload does not match library_json.py's formatting — "
                         "refusing to write a reflowed library")
        except ImportError:
            pass  # library_json is the guard, not a hard dependency of serving

        dest = REPO / rel
        try:
            if dest.exists():
                shutil.copy2(dest, dest.with_suffix(dest.suffix + ".bak"))
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            # newline="" keeps the CRLF the page already produced from being
            # translated a second time into CRCRLF.
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(raw)
            os.replace(tmp, dest)
        except Exception as e:
            return self._refuse(500, f"write failed: {e}")

        try:
            version = str(dest.stat().st_mtime_ns)
        except OSError:
            version = None
        body = json.dumps({"ok": True, "bytes": len(raw.encode("utf-8")),
                           "version": version}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        print(f"  saved {rel}  ({len(raw.encode('utf-8')):,} bytes, .bak kept)")


def main() -> int:
    for rel in sorted(WRITABLE):
        if not (REPO / rel).exists():
            print(f"WARNING: {rel} is missing — the studio will have nothing to open.")

    url = f"http://127.0.0.1:{PORT}/library_studio.html"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"Could not bind port {PORT}: {e}")
        print("Something else is using it. Set LIBRARY_STUDIO_PORT to another number and retry.")
        return 1

    print("=" * 66)
    print("  SLAP Library Studio")
    print("=" * 66)
    print(f"  {url}")
    print("  Both libraries load automatically. Save writes straight to disk.")
    print("  Close this window when you are done.")
    print("=" * 66)

    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
