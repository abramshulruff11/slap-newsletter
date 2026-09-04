"""
Per-run status, written by the pipeline and read by verify_run.py.

WHY THIS EXISTS
    The run's own outcome was invisible. The email has failed since at least
    2026-08-14 — send_email() caught the SMTP exception and print()ed it — and
    every CI run was green. A print is not a signal: nothing downstream could
    ask "did the newsletter actually go out?"

    Same shape as the ESPN outage: the failure was in the log the whole time
    and no step could act on it.

    generate_newsletter.py and email_newsletter.py run as SEPARATE processes,
    so this state has to live on disk. It is a build artifact, regenerated
    every run, and gitignored.

USAGE
    run_status.reset()                      # once, at the top of the pipeline
    run_status.record(email_sent=False, email_error="...")
    run_status.append("truncated_passes", "PASS 2")
    run_status.load()                       # verify_run.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

STATUS_PATH = Path(__file__).resolve().parent / "run_status.json"


def load(path: Path | None = None) -> dict:
    """Current status. {} when absent or unreadable — never raises."""
    p = path or STATUS_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict, path: Path | None = None) -> None:
    (path or STATUS_PATH).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def reset(path: Path | None = None) -> None:
    """Start a fresh run. Without this, yesterday's 'email_sent: true' would
    still be sitting there when today's send fails."""
    _save({"date": date.today().isoformat()}, path)


def record(path: Path | None = None, **fields) -> dict:
    """Merge fields into the status file. Tolerates a missing file."""
    data = load(path)
    data.setdefault("date", date.today().isoformat())
    data.update(fields)
    _save(data, path)
    return data


def append(key: str, value, path: Path | None = None) -> dict:
    """Append to a list field, creating it if needed, without duplicates."""
    data = load(path)
    data.setdefault("date", date.today().isoformat())
    items = data.get(key)
    if not isinstance(items, list):
        items = []
    if value not in items:
        items.append(value)
    data[key] = items
    _save(data, path)
    return data
