"""
Per-STAGE outcomes for the daily pipeline, on top of run_status.json.

WHY THIS EXISTS
    run_status.json already carried what the *newsletter generator* knew about
    itself (email_sent, incomplete_passes). It knew nothing about the run
    AROUND it. A fetch that 403'd, a box score render that crashed, a Substack
    draft that never got created — all of that lived in the GitHub Actions UI
    and nowhere else, which means Abram only saw it if he went looking, and the
    whole point of SLA-52 is that he shouldn't have to.

    So every stage of daily-newsletter.yml now runs through ci/run_stage.sh,
    which tees the stage's output to a log, records the exit code here, and
    re-raises. The consolidated email reads this back and says, in one place:
    what ran, what didn't, what broke, and the actual error text.

    This does NOT duplicate run_status.py — it writes into the same file,
    through the same functions, so uat/generate_newsletter_uat.py's redirect of
    run_status.STATUS_PATH keeps working and the sandbox still never touches a
    production file.

WHY A DECLARED STAGE LIST
    A stage that never ran is the most important row in the report and the only
    one nothing can record, because the process that would have recorded it was
    never started. PIPELINE_STAGES is what makes "not reached" printable:
    anything declared here but absent from the recorded list was skipped by a
    failure above it. uat/tests/test_pipeline_status.py fails if this list and
    the workflow disagree, so it cannot quietly go stale.

USAGE
    python pipeline_status.py start
    python pipeline_status.py stage --name "Fetch content" --exit-code 1 \
                                    --log ci_logs/fetch_content.log --seconds 12
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import run_status

ET = ZoneInfo("America/New_York")

# Only failures carry a log excerpt. A successful stage's log is noise, and
# thirteen of them would bloat a file that three separate processes rewrite.
LOG_TAIL_LINES = 24
LOG_TAIL_CHARS = 2400


@dataclass(frozen=True)
class Stage:
    """One step of daily-newsletter.yml.

    critical=True means the issue cannot ship without it, so a failure is a
    FAILED run. critical=False means the step is wired continue-on-error and a
    failure degrades the run rather than ending it — a push hiccup or a missed
    Substack draft is worth an email, but the newsletter still went out.
    """
    name: str
    critical: bool
    note: str = ""


PIPELINE_STAGES: tuple[Stage, ...] = (
    # The installs are stages too. A pip or Playwright failure is a plausible
    # way for the run to die, and if it is not declared here the email has
    # nothing to point at — it would report only "no pipeline stage reached",
    # which is true and useless. email_newsletter.py and this module import
    # nothing outside the standard library precisely so the report survives a
    # dependency install that did not.
    Stage("Install dependencies", True),
    Stage("Install Playwright Chromium", True),
    Stage("Fetch content", True),
    Stage("Fetch sports data", True),
    Stage("Check sports data health", False,
          "scores are missing or partial; the issue still ships without them"),
    Stage("Validate raw content", True),
    Stage("Generate newsletter", True),
    Stage("Render box score images", True),
    Stage("Verify outputs", True),
    Stage("Archive outputs", False, "today's snapshot was not written"),
    Stage("Commit and push outputs", False,
          "nothing was pushed; hosted box score images may 404 in this email"),
    Stage("Create Substack draft", False, "there is no draft to publish at 12:30 PM ET"),
    Stage("Commit Substack draft handoff", False,
          "the draft exists but the noon job cannot find it"),
    Stage("Publish late if past 12:30 PM ET", False),
    Stage("Assess run quality", False),
)

STAGE_BY_NAME = {s.name: s for s in PIPELINE_STAGES}

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

def start() -> dict:
    """Begin a run: clear yesterday's status and stamp the start time.

    Called as the FIRST step of the workflow, before anything can record into
    the file. run_status.reset() used to live in generate_newsletter.main(),
    which was fine when the generator was the only writer and is wrong now —
    two fetch stages run before it and their outcomes would be wiped."""
    run_status.reset()
    return run_status.record(
        run_started=datetime.now(ET).isoformat(timespec="seconds"),
        stages=[],
    )


def ensure_started() -> dict:
    """start() unless this run already did.

    The workflow calls start() explicitly. This exists for a local
    `python generate_newsletter.py` with no workflow around it: it resets a
    status file left over from a previous day, and leaves today's alone so a
    generator run in the middle of a pipeline does not erase the stages
    recorded before it."""
    data = run_status.load()
    started = str(data.get("run_started") or "")
    if started[:10] == date.today().isoformat():
        return data
    return start()


def _log_tail(log_path: str | Path | None) -> str:
    if not log_path:
        return ""
    p = Path(log_path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = _ANSI.sub("", text).rstrip()
    lines = [ln for ln in text.splitlines() if ln.strip()][-LOG_TAIL_LINES:]
    tail = "\n".join(lines)
    if len(tail) > LOG_TAIL_CHARS:
        tail = "…\n" + tail[-LOG_TAIL_CHARS:]
    return tail


def record_stage(name: str, exit_code: int, *, seconds: float | None = None,
                 log_path: str | Path | None = None) -> dict:
    """Record one stage's outcome. Re-recording the same name replaces it, so a
    re-run of a step inside one job does not produce two contradictory rows."""
    entry = {
        "name": name,
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "critical": STAGE_BY_NAME.get(name, Stage(name, True)).critical,
    }
    if seconds is not None:
        entry["seconds"] = round(float(seconds), 1)
    if exit_code != 0:
        tail = _log_tail(log_path)
        if tail:
            entry["log_tail"] = tail
    data = run_status.load()
    stages = [s for s in (data.get("stages") or [])
              if isinstance(s, dict) and s.get("name") != name]
    stages.append(entry)
    return run_status.record(stages=stages)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def stage_rows(status: dict) -> list[dict]:
    """Every declared stage, in workflow order, with the ones that never ran
    marked. Undeclared stages (someone added a step without adding it here)
    are appended rather than dropped — the test catches the omission, but the
    report must never silently lose a row."""
    recorded = {s.get("name"): s for s in (status.get("stages") or [])
                if isinstance(s, dict)}
    rows: list[dict] = []
    for stage in PIPELINE_STAGES:
        got = recorded.pop(stage.name, None)
        if got is None:
            rows.append({"name": stage.name, "state": "skipped",
                         "critical": stage.critical, "note": stage.note})
        else:
            rows.append({**got, "note": stage.note,
                         "state": "ok" if got.get("ok") else "failed"})
    for leftover in recorded.values():
        rows.append({**leftover, "note": "",
                     "state": "ok" if leftover.get("ok") else "failed"})
    return rows


def failures(status: dict) -> list[dict]:
    """Failed stages, worst first: critical before non-critical."""
    failed = [r for r in stage_rows(status) if r["state"] == "failed"]
    return sorted(failed, key=lambda r: not r.get("critical"))


def verdict(status: dict) -> tuple[str, str]:
    """(level, one-line headline). level is 'success' | 'partial' | 'failed'.

    WARNINGS DELIBERATELY DO NOT DOWNGRADE THE VERDICT. verify_run.py warns on
    a thin issue — below the meme floor, a quiet sports day — and those fire on
    most days. A top line that reads PARTIAL every morning is a top line nobody
    reads, which is the failure mode this whole ticket exists to fix. Warnings
    are printed in the body; only real breakage moves the headline.
    """
    rows = stage_rows(status)
    quality = status.get("quality") or {}
    q_errors = list(quality.get("errors") or [])

    critical_bad = [r for r in rows
                    if r.get("critical") and r["state"] in ("failed", "skipped")]
    soft_bad = [r for r in rows if not r.get("critical") and r["state"] == "failed"]

    if critical_bad:
        first = critical_bad[0]
        # The level is already the headline's neighbour everywhere it is shown
        # (the subject line carries ❌, the panel says PIPELINE FAILED), so this
        # string is the DETAIL, never a second copy of the word "failed".
        if first["state"] == "skipped":
            return "failed", f"stopped before “{first['name']}”"
        return "failed", f"stopped at “{first['name']}”"
    if q_errors:
        return "failed", f"the issue is not fit to send — {q_errors[0]}"
    if status.get("email_sent") is False:
        return "failed", "the newsletter email did not send"
    if soft_bad:
        names = ", ".join(f"“{r['name']}”" for r in soft_bad[:2])
        more = f" (+{len(soft_bad) - 2} more)" if len(soft_bad) > 2 else ""
        return "partial", f"newsletter shipped, but {names}{more} failed"
    if not rows or all(r["state"] == "skipped" for r in rows):
        return "failed", "no pipeline stage reported in — the run never started"
    return "success", "every stage completed"


# --------------------------------------------------------------------------
# CLI — called from ci/run_stage.sh and the workflow
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record pipeline stage outcomes.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start", help="reset the status file and stamp the start time")
    st = sub.add_parser("stage", help="record one stage's outcome")
    st.add_argument("--name", required=True)
    st.add_argument("--exit-code", type=int, required=True)
    st.add_argument("--seconds", type=float, default=None)
    st.add_argument("--log", default=None)
    sub.add_parser("show", help="print the current stage table (for the job log)")
    args = ap.parse_args(argv)

    if args.cmd == "start":
        start()
        print("  pipeline status reset — recording stage outcomes to run_status.json")
        return 0
    if args.cmd == "stage":
        record_stage(args.name, args.exit_code,
                     seconds=args.seconds, log_path=args.log)
        mark = "ok" if args.exit_code == 0 else f"FAILED (exit {args.exit_code})"
        print(f"  [stage] {args.name}: {mark}")
        return 0

    status = run_status.load()
    level, headline = verdict(status)
    print(f"\n── PIPELINE STATUS ── {level.upper()}: {headline}")
    for r in stage_rows(status):
        mark = {"ok": "✓", "failed": "✗", "skipped": "–"}[r["state"]]
        secs = f"{r['seconds']:.0f}s" if r.get("seconds") is not None else ""
        print(f"  {mark} {r['name']:<34} {secs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
