"""
Run:  python -X utf8 uat/tests/test_rerun_safe.py

Locks SLA-55: a manual re-dispatch of daily-newsletter.yml (e.g. to recover
from a mid-run failure) must not duplicate the day's deliverables.

THE PROBLEM
    Per CLAUDE.md Known Issues, `workflow_dispatch` always does a full run:
    a second daily email, and a second Substack draft that orphans the first
    (substack_post_state.json just gets overwritten). Both come from the same
    root cause -- neither script can tell it already ran once today, because
    the only state that survives BETWEEN separate GitHub Actions runners is
    what got committed to git. run_status.json is gitignored and reset at the
    top of every run for exactly this reason (see run_status.py), so it can
    answer "did THIS run already send" but never "did an EARLIER run today".

WHAT THIS LOCKS
  1. email_newsletter.py: a committed email_sent_state.json dated today makes
     --rerun-safe skip the send, and is otherwise ignored (a fresh day, a
     missing file, no flag at all -- all send normally).
  2. substack_poc/publish.py: --rerun-safe reuses today's still-open draft
     (PUT, not POST) instead of creating a second one, but only when the
     handoff is genuinely from today AND the draft is still an open draft --
     a stale handoff, a deleted draft, or one already published all fall
     through to the ordinary fresh-draft path.
  3. The workflow: the new input threads into both steps, and the new
     "Commit email-sent marker" step is wired the same way every other
     continue-on-error stage is (declared in PIPELINE_STAGES, in order).

No network, no Substack, no SMTP: the Substack API is a stub and email
sending is captured in memory.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "substack_poc"))

import run_status  # noqa: E402

_TMP = tempfile.TemporaryDirectory()
run_status.STATUS_PATH = Path(_TMP.name) / "run_status.json"

import publish as P            # noqa: E402
import pipeline_status as PS   # noqa: E402
import email_newsletter as EN  # noqa: E402

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        failures.append(f"{label}: expected {want!r}, got {got!r}")


def check_true(label, got):
    check(label, bool(got), True)


# ===========================================================================
print("=" * 72)
print("SUBSTACK DRAFT REUSE — _find_reusable_draft")
print("=" * 72)


class FakeApi:
    """drafts: {id: {"is_published": bool}}. get_draft raises for an unknown id
    (a 404, the way the real Api behaves on a deleted draft)."""

    def __init__(self, drafts=None):
        self.drafts = dict(drafts or {})
        self.put_calls = []
        self.post_calls = []

    def get_draft(self, draft_id):
        if draft_id not in self.drafts:
            raise Exception("404 Client Error: Not Found")
        return {"id": draft_id, **self.drafts[draft_id]}

    def put_draft(self, draft_id, **kwargs):
        self.put_calls.append((draft_id, kwargs))
        return {"id": draft_id}

    def post_draft(self, body):
        self.post_calls.append(body)
        return {"id": 999}


def state_file(payload) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                    encoding="utf-8")
    json.dump(payload, f)
    f.close()
    return f.name


today = P.today_et()

api = FakeApi({111: {"is_published": False}})
check("a valid, unpublished, TODAY draft is reused",
      P._find_reusable_draft(api, state_file({"date": today, "draft_id": 111})),
      111)

api = FakeApi({111: {"is_published": False}})
check("a STALE (yesterday's) handoff is never reused",
      P._find_reusable_draft(api, state_file({"date": "2020-01-01", "draft_id": 111})),
      None)

api = FakeApi({})
check("a draft that 404s (deleted) falls through to a fresh one",
      P._find_reusable_draft(api, state_file({"date": today, "draft_id": 111})),
      None)

api = FakeApi({111: {"is_published": True}})
check("an ALREADY-PUBLISHED draft is never touched -- fresh draft instead",
      P._find_reusable_draft(api, state_file({"date": today, "draft_id": 111})),
      None)

api = FakeApi({111: {"is_published": False}})
check("no draft_id in the handoff falls through",
      P._find_reusable_draft(api, state_file({"date": today})), None)

check("a missing handoff file falls through",
      P._find_reusable_draft(FakeApi(), "/no/such/file.json"), None)


def _write_garbage() -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                    encoding="utf-8")
    f.write("{not valid json")
    f.close()
    return f.name


check("an unreadable (corrupt) handoff file falls through",
      P._find_reusable_draft(FakeApi(), _write_garbage()), None)


# ===========================================================================
print()
print("=" * 72)
print("EMAIL SKIP — email_sent_state.json across separate runs")
print("=" * 72)

work = tempfile.TemporaryDirectory()
WORK = Path(work.name)
EN.EMAIL_SENT_MARKER = WORK / "email_sent_state.json"

check("no marker at all -- nothing to report", EN.already_sent_today(), None)

EN.write_email_sent_marker("SLAP - 9/22/2026")
prior = EN.already_sent_today()
check_true("a marker written just now is found", prior is not None)
check("with the subject preserved", prior.get("subject"), "SLAP - 9/22/2026")

stale = {"date": "2020-01-01", "subject": "old"}
EN.EMAIL_SENT_MARKER.write_text(json.dumps(stale), encoding="utf-8")
check("a marker from a DIFFERENT (older) day is not today's",
      EN.already_sent_today(), None)

EN.EMAIL_SENT_MARKER.write_text("{not valid json", encoding="utf-8")
check("a corrupt marker file is treated as absent", EN.already_sent_today(), None)

EN.EMAIL_SENT_MARKER.unlink(missing_ok=True)


# --- main()'s --rerun-safe skip path, end to end ---------------------------
sent: list[tuple] = []
EN._smtp_send = lambda subject, html, inline: sent.append((subject, html, inline))
EN.SUBSTACK_PATH = WORK / "newsletter_substack.html"
EN.BOX_SCORE_DIR = WORK / "box_score"
EN.COST_SUMMARY_PATH = WORK / "cost_summary.json"
EN.SUBSTACK_STATE_PATH = WORK / "substack_post_state.json"
EN.PUBLISH_RESULT_PATH = WORK / "publish_result.json"
EN.BOX_SCORE_DIR.mkdir(exist_ok=True)
EN.os.environ["GMAIL_ADDRESS"] = "abram@example.com"
EN.os.environ["GMAIL_PASSWORD"] = "app-password-not-real"

clean_stages = [{"name": s.name, "ok": True, "exit_code": 0, "critical": s.critical}
               for s in PS.PIPELINE_STAGES]
run_status.record(stages=clean_stages)

# No marker yet: --rerun-safe must send normally, same as a plain run.
def run_main(argv):
    old = sys.argv
    sys.argv = ["email_newsletter.py", *argv]
    try:
        return EN.main()
    finally:
        sys.argv = old


rc = run_main(["--rerun-safe"])
check("no marker yet -- --rerun-safe still sends (same as a normal run)",
      len(sent), 1)
check("and exits 0", rc, 0)
check("and records email_sent for the gate", run_status.load()["email_sent"], True)
check_true("and writes today's marker for the NEXT rerun to find",
           EN.already_sent_today() is not None)

# Now a "rerun": the marker from the send above is still today's.
sent.clear()
rc = run_main(["--rerun-safe"])
check("a marker from EARLIER TODAY -- --rerun-safe skips the send",
      len(sent), 0)
check("and still exits 0 (not a failure)", rc, 0)
check("and STILL records email_sent=True for the gate (it did go out today)",
      run_status.load()["email_sent"], True)

# Without the flag, a marker is ignored -- a deliberate full run still sends.
sent.clear()
rc = run_main([])
check("WITHOUT --rerun-safe, a same-day marker is ignored -- sends anyway",
      len(sent), 1)


# ===========================================================================
print()
print("=" * 72)
print("THE WORKFLOW — the input, the two flags, and the new stage")
print("=" * 72)

wf = (REPO / ".github" / "workflows" / "daily-newsletter.yml").read_text(encoding="utf-8")

check_true("workflow_dispatch declares rerun_of_failed_run",
           "rerun_of_failed_run:" in wf)
check_true("...as a boolean, default false",
           bool(re.search(r"rerun_of_failed_run:\s*\n(?:.*\n)*?\s*type:\s*boolean\s*\n\s*default:\s*false",
                          wf)))

draft_step_m = re.search(
    r'- name: Create Substack draft.*?(?=\n      - name:|\Z)', wf, re.S)
check_true("the Substack draft step found", bool(draft_step_m))
check_true("...threads the input into --rerun-safe",
           "inputs.rerun_of_failed_run" in draft_step_m.group(0)
           and "--rerun-safe" in draft_step_m.group(0))

email_step_m = re.search(
    r'- name: Send the daily status email.*?(?=\n      - name:|\Z)', wf, re.S)
check_true("the email step found", bool(email_step_m))
check_true("...threads the input into --rerun-safe",
           "inputs.rerun_of_failed_run" in email_step_m.group(0)
           and "--rerun-safe" in email_step_m.group(0))

marker_step_m = re.search(
    r'- name: Commit email-sent marker.*?(?=\n      - name:|\Z)', wf, re.S)
check_true("the marker-commit step exists", bool(marker_step_m))
check_true("...runs always() and continue-on-error",
           "if: always()" in marker_step_m.group(0)
           and "continue-on-error: true" in marker_step_m.group(0))

wf_stages = re.findall(r'run_stage\.sh\s+"([^"]+)"', wf)
declared = [s.name for s in PS.PIPELINE_STAGES]
check("the new stage is declared in PIPELINE_STAGES, in order",
      wf_stages, declared)

soft_in_wf = set()
steps_bodies = re.findall(r'- name: ([^\n]+)\n((?:(?!\n      - name:).)*)', wf, re.S)
for name, body in steps_bodies:
    m = re.search(r'run_stage\.sh\s+"([^"]+)"', body)
    if m and "continue-on-error: true" in body:
        soft_in_wf.add(m.group(1))
soft_declared = {s.name for s in PS.PIPELINE_STAGES if not s.critical}
check("continue-on-error in the workflow == critical=False in PIPELINE_STAGES",
      sorted(soft_in_wf), sorted(soft_declared))


print()
if failures:
    print("=" * 72)
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("=" * 72)
print("ALL CHECKS PASSED — no network, no Substack, no SMTP")
print("=" * 72)
