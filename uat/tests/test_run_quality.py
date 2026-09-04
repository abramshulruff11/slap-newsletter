"""
Run:  python -X utf8 uat/tests/test_run_quality.py

Locks the two things added on 2026-09-05 to make a bad run visible:

  1. TRUNCATION DETECTION. Nothing checked stop_reason. Pass 2 measured 7,029
     output tokens on 2026-08-31 against a cap of 8,192 — 86% — so one busy
     Saturday truncates the draft mid-sentence. Passes 4 and 6 each have to
     reproduce the WHOLE draft, and the only guard was "does the output contain
     an <h1>", which truncation cannot trip because it removes the END.

  2. THE RUN-QUALITY GATE. Nothing could fail for a bad newsletter. The proof
     that it works is that it fails the real 2026-09-01 issue — the day seven
     GIFs shipped as invisible empty divs — and passes the issues around it.

No API calls, no network.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import run_status  # noqa: E402

# Redirect the status file before importing anything that writes to it: this
# suite exercises was_truncated(), which records to run_status.json, and a test
# must not leave a build artifact in the working tree. It also demonstrates the
# same redirect uat/generate_newsletter_uat.py uses to stay out of prod's files.
_STATUS_TMP = tempfile.TemporaryDirectory()
run_status.STATUS_PATH = Path(_STATUS_TMP.name) / "run_status.json"

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        failures.append(f"{label}: expected {want!r}, got {got!r}")


print("=" * 70)
print("RUN STATUS — the pipeline's own outcome, on disk")
print("=" * 70)

with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "run_status.json"
    check("missing file reads as empty", run_status.load(p), {})
    run_status.reset(p)
    check("reset stamps a date", "date" in run_status.load(p), True)
    run_status.record(p, email_sent=False, email_error="boom")
    check("record merges", run_status.load(p)["email_error"], "boom")
    run_status.record(p, email_sent=True, email_error="")
    check("record overwrites", run_status.load(p)["email_sent"], True)
    run_status.append("incomplete_passes", "PASS 2 (max_tokens)", p)
    run_status.append("incomplete_passes", "PASS 2 (max_tokens)", p)
    run_status.append("incomplete_passes", "PASS 4 (max_tokens)", p)
    check("append de-duplicates", run_status.load(p)["incomplete_passes"],
          ["PASS 2 (max_tokens)", "PASS 4 (max_tokens)"])
    # Yesterday's success must not survive into today's failed run.
    run_status.reset(p)
    check("reset clears the previous run", "email_sent" in run_status.load(p), False)
    p.write_text("{ not json", encoding="utf-8")
    check("corrupt file reads as empty, never raises", run_status.load(p), {})

print()
print("=" * 70)
print("TRUNCATION — detected, and acted on differently per pass")
print("=" * 70)

import runner_common as RC  # noqa: E402


def resp(stop_reason):
    r = types.SimpleNamespace()
    r.stop_reason = stop_reason
    r.content = [types.SimpleNamespace(type="text", text="<h1>Lead</h1><p>cut off")]
    r.usage = types.SimpleNamespace(input_tokens=0, output_tokens=0,
                                    cache_read_input_tokens=0,
                                    cache_creation_input_tokens=0)
    return r


check("end_turn is not truncation", RC.was_truncated(resp("end_turn"), "PASS X"), False)
check("tool_use is not truncation", RC.was_truncated(resp("tool_use"), "PASS X"), False)
check("max_tokens is truncation", RC.was_truncated(resp("max_tokens"), "PASS X"), True)
check("pause_turn counts as incomplete too",
      RC.was_truncated(resp("pause_turn"), "PASS X"), True)
check("a response with no stop_reason does not raise",
      RC.was_truncated(types.SimpleNamespace(), "PASS X"), False)

# Pass 4 and Pass 6 must return their INPUT when truncated. A half-rewritten
# draft still starts with <h1>, so the existing non-HTML gate cannot catch it.
DRAFT = "<h1>Lead</h1><p>full draft</p><h2>Around the League</h2>"


class FakeMessages:
    def __init__(self, stop): self.stop = stop
    def create(self, **kw): return resp(self.stop)


class FakeClient:
    def __init__(self, stop): self.messages = FakeMessages(stop)


RC.configure(prompts_dir=REPO / "prompts")
check("the sandbox redirect keeps prod's run_status.json untouched",
      (REPO / "run_status.json").exists(), False)

for pass_name, fn in (("PASS 4", RC.run_pass4), ("PASS 6", RC.run_pass6)):
    args = (DRAFT, FakeClient("max_tokens")) if pass_name == "PASS 4" \
        else (DRAFT, [], FakeClient("max_tokens"))
    check(f"{pass_name} returns its input unchanged when truncated",
          fn(*args), DRAFT)
    args = (DRAFT, FakeClient("end_turn")) if pass_name == "PASS 4" \
        else (DRAFT, [], FakeClient("end_turn"))
    check(f"{pass_name} returns the model's text when complete",
          fn(*args) != DRAFT, True)

# Pass 2's ceiling must stay under the SDK's non-streaming limit, or the run
# dies client-side before a single token is billed — the 2026-09-01 outage.
check("writer cap is above the observed 7,029-token peak",
      RC.MAX_TOKENS_WRITER > 7029, True)
try:
    import anthropic
    from anthropic._base_client import BaseClient
    c = anthropic.Anthropic(api_key="sk-not-real")
    BaseClient._calculate_nonstreaming_timeout(c, RC.MAX_TOKENS_WRITER, None)
    check("writer cap is legal WITHOUT streaming (Pass 2 does not stream)", True, True)
except ValueError:
    check("writer cap is legal WITHOUT streaming (Pass 2 does not stream)",
          False, True)

for runner in ("generate_newsletter.py", "uat/generate_newsletter_uat.py"):
    src = (REPO / runner).read_text(encoding="utf-8")
    check(f"{runner.split('/')[-1]}: Pass 2 uses the shared cap",
          "max_tokens=MAX_TOKENS_WRITER" in src, True)
    check(f"{runner.split('/')[-1]}: Pass 2 checks for truncation",
          'was_truncated(response, "PASS 2")' in src, True)
    check(f"{runner.split('/')[-1]}: the tool loop is bounded",
          "while True:" not in src.split("def run_pass2")[1].split("def ")[0], True)

print()
print("=" * 70)
print("RUN-QUALITY GATE — against the real archive")
print("=" * 70)


def gate(draft: Path, published: Path, status: dict) -> tuple[int, str]:
    """Run verify_run.py against one archived issue, in a scratch copy."""
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        for name in ("verify_run.py", "run_status.py"):
            shutil.copy(REPO / name, t / name)
        shutil.copy(draft, t / "newsletter_draft.html")
        shutil.copy(published, t / "newsletter_substack.html")
        (t / "run_status.json").write_text(json.dumps(status), encoding="utf-8")
        r = subprocess.run([sys.executable, "verify_run.py"], cwd=t,
                           capture_output=True, text=True)
        return r.returncode, r.stdout


SENT = {"date": "x", "email_sent": True}
archive = REPO / "archive"

# The canonical bad day: 7 GIF placeholders shipped un-rendered.
bad = archive / "2026-09-01"
if (bad / "newsletter_substack.html").exists():
    code, out = gate(bad / "newsletter_draft.html", bad / "newsletter_substack.html", SENT)
    check("2026-09-01 (the GIF-loss issue) FAILS the gate", code, 1)
    check("and names the reason", "un-rendered" in out, True)

# The issues either side of it are shippable.
for day in ("2026-09-02", "2026-09-03", "2026-09-04"):
    d = archive / day
    if (d / "newsletter_substack.html").exists():
        code, _ = gate(d / "newsletter_draft.html", d / "newsletter_substack.html", SENT)
        check(f"{day} passes the gate", code, 0)

# A failed email fails the run even when the newsletter itself is fine.
good = archive / "2026-09-04"
if (good / "newsletter_substack.html").exists():
    code, out = gate(good / "newsletter_draft.html", good / "newsletter_substack.html",
                     {"date": "x", "email_sent": False, "email_error": "5.7.9 app password"})
    check("a good issue that did not SEND still fails", code, 1)
    check("and names the SMTP reason", "5.7.9" in out, True)

    code, out = gate(good / "newsletter_draft.html", good / "newsletter_substack.html",
                     {"date": "x", "email_sent": True,
                      "incomplete_passes": ["PASS 2 (max_tokens)"]})
    check("a truncated pass fails the run", code, 1)
    check("and names the pass", "PASS 2 (max_tokens)" in out, True)

print()
if failures:
    print("=" * 70)
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("=" * 70)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 70)
