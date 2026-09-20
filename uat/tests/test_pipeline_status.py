"""
Run:  python -X utf8 uat/tests/test_pipeline_status.py

Locks SLA-52: the ONE daily status email, and the per-stage record it reads.

WHAT IS ACTUALLY AT RISK HERE
  1. The stage list going stale. PIPELINE_STAGES is what lets the email print
     "never ran" for a stage that died before it could report — and a stage that
     exists in the workflow but not in the list would silently lose its row, so
     the report would be wrong in exactly the situation it exists for. The
     workflow file is parsed here and compared, both ways.

  2. The verdict drifting. "PARTIAL" every morning is a headline nobody reads,
     so warnings must NOT downgrade it; a critical stage that never ran must.

  3. The email going quiet on a failed run. That is the whole ticket: the run
     Abram most needs to hear about is the one that died on step three, and
     that is precisely the run the old mid-workflow email step never reached.

  4. Noon staying silent. Abram's rule (2026-09-19) is that no email at 12:30
     means it published. That only holds if the innocent no-ops — he published
     it himself, he deleted the draft — send nothing, and the real ones do.

No API calls, no network, no SMTP: _smtp_send is stubbed and every send is
captured in memory.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import run_status  # noqa: E402

# Redirect before importing anything that writes: this suite must not leave a
# run_status.json in the working tree, and must never overwrite a real one.
_TMP = tempfile.TemporaryDirectory()
run_status.STATUS_PATH = Path(_TMP.name) / "run_status.json"

import pipeline_status as PS       # noqa: E402
import email_newsletter as EN      # noqa: E402

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
print("STAGE RECORDING — what ran, what broke, and the real error text")
print("=" * 72)

PS.start()
check("start() stamps the run", "run_started" in run_status.load(), True)
check("start() clears the stage list", run_status.load()["stages"], [])

PS.record_stage("Fetch content", 0, seconds=12.4)
PS.record_stage("Fetch sports data", 0, seconds=30)
check("a passing stage records ok", run_status.load()["stages"][0]["ok"], True)
check("and its duration", run_status.load()["stages"][0]["seconds"], 12.4)
check("a passing stage carries no log excerpt",
      "log_tail" in run_status.load()["stages"][0], False)

with tempfile.TemporaryDirectory() as tmp:
    log = Path(tmp) / "generate_newsletter.log"
    log.write_text(
        "\x1b[31mLoading content...\x1b[0m\n\n"
        "Traceback (most recent call last):\n"
        "  File \"generate_newsletter.py\", line 900, in run_pass2\n"
        "anthropic.RateLimitError: 429 rate_limit_error\n",
        encoding="utf-8")
    PS.record_stage("Generate newsletter", 1, seconds=41, log_path=log)

entry = [s for s in run_status.load()["stages"] if s["name"] == "Generate newsletter"][0]
check("a failing stage records the exit code", entry["exit_code"], 1)
check_true("and keeps the real error text",
           "anthropic.RateLimitError: 429" in entry["log_tail"])
check("and strips ANSI colour", "\x1b[" in entry["log_tail"], False)
check("and drops blank lines", "\n\n" in entry["log_tail"], False)

PS.record_stage("Generate newsletter", 0, seconds=50)
same = [s for s in run_status.load()["stages"] if s["name"] == "Generate newsletter"]
check("re-recording a stage REPLACES it (no contradictory rows)", len(same), 1)
check("with the newer outcome", same[0]["ok"], True)

long_log_dir = tempfile.TemporaryDirectory()
big = Path(long_log_dir.name) / "big.log"
big.write_text("\n".join(f"line {i}" for i in range(500)), encoding="utf-8")
PS.record_stage("Verify outputs", 3, log_path=big)
tail = [s for s in run_status.load()["stages"] if s["name"] == "Verify outputs"][0]["log_tail"]
check("a huge log is trimmed to the tail",
      len(tail.splitlines()) <= PS.LOG_TAIL_LINES, True)
check_true("keeping the LAST lines, where the error is", "line 499" in tail)

PS.record_stage("Fetch content", 0)
rows = PS.stage_rows(run_status.load())
check("stage_rows returns every declared stage", len(rows), len(PS.PIPELINE_STAGES))
check("in workflow order", [r["name"] for r in rows],
      [s.name for s in PS.PIPELINE_STAGES])
skipped = [r["name"] for r in rows if r["state"] == "skipped"]
check_true("stages that never reported are marked 'skipped', not 'ok'",
           "Create Substack draft" in skipped)


# ===========================================================================
print()
print("=" * 72)
print("VERDICT — the top line, which has to stay worth reading")
print("=" * 72)


def status_with(**over):
    """A status where every declared stage passed, then overridden."""
    base = {
        "date": "2026-09-20",
        "run_started": "2026-09-20T02:17:00-04:00",
        "stages": [{"name": s.name, "ok": True, "exit_code": 0,
                    "critical": s.critical, "seconds": 5}
                   for s in PS.PIPELINE_STAGES],
        "email_sent": True,
    }
    for name, exit_code in (over.pop("failed_stages", None) or {}).items():
        for s in base["stages"]:
            if s["name"] == name:
                s["ok"], s["exit_code"] = False, exit_code
    for name in over.pop("drop_stages", None) or []:
        base["stages"] = [s for s in base["stages"] if s["name"] != name]
    base.update(over)
    return base


check("a clean run is SUCCESS", PS.verdict(status_with())[0], "success")

lvl, head = PS.verdict(status_with(failed_stages={"Generate newsletter": 1}))
check("a failed CRITICAL stage is FAILED", lvl, "failed")
check_true("and the headline names it", "Generate newsletter" in head)

lvl, head = PS.verdict(status_with(failed_stages={"Commit and push outputs": 1}))
check("a failed NON-CRITICAL stage is PARTIAL", lvl, "partial")
check_true("and the headline says the newsletter still shipped",
           "shipped" in head and "Commit and push outputs" in head)

lvl, head = PS.verdict(status_with(
    drop_stages=["Render box score images", "Verify outputs", "Archive outputs",
                 "Commit and push outputs", "Create Substack draft",
                 "Commit Substack draft handoff",
                 "Publish late if past 12:30 PM ET", "Assess run quality"]))
check("a critical stage that NEVER RAN is FAILED", lvl, "failed")
check_true("and the headline says where it stopped",
           "stopped before" in head and "Render box score images" in head)
check_true("the headline is the DETAIL, not a second copy of the level",
           "FAILED" not in PS.verdict(status_with(
               failed_stages={"Generate newsletter": 1}))[1])

# The point of the whole ticket: warnings fire on most days.
warned = status_with(quality={"errors": [], "warnings": [
    "2 meme(s) rendered against a floor of 3", "14 tweets — thin for a normal day"]})
check("WARNINGS DO NOT downgrade the verdict", PS.verdict(warned)[0], "success")

broken = status_with(quality={"errors": ["zero GIFs AND zero memes"], "warnings": []})
check("run-quality ERRORS do", PS.verdict(broken)[0], "failed")
check_true("and are quoted in the headline",
           "zero GIFs" in PS.verdict(broken)[1])

check("a send failure is FAILED",
      PS.verdict(status_with(email_sent=False))[0], "failed")
check("an empty status is FAILED, not SUCCESS",
      PS.verdict({})[0], "failed")


# ===========================================================================
print()
print("=" * 72)
print("THE WORKFLOW AND THE STAGE LIST MUST AGREE")
print("=" * 72)

wf = (REPO / ".github" / "workflows" / "daily-newsletter.yml").read_text(encoding="utf-8")
wf_stages = re.findall(r'run_stage\.sh\s+"([^"]+)"', wf)
declared = [s.name for s in PS.PIPELINE_STAGES]

check("every workflow stage is declared in PIPELINE_STAGES",
      [n for n in wf_stages if n not in declared], [])
check("every declared stage exists in the workflow",
      [n for n in declared if n not in wf_stages], [])
check("and they are in the same order", wf_stages, declared)

# Split the job into steps by hand rather than importing PyYAML: it is not in
# requirements.txt, and tests.yml deliberately installs only that file.
def workflow_steps(text: str) -> list[dict]:
    blocks, current = [], None
    for line in text.splitlines():
        if line.startswith("      - name: "):
            if current:
                blocks.append(current)
            current = {"name": line[len("      - name: "):].strip(), "body": ""}
        elif current is not None:
            current["body"] += line + "\n"
    if current:
        blocks.append(current)
    return blocks


steps = workflow_steps(wf)
check("the step splitter found the whole job", len(steps) >= 15, True)

# critical <-> continue-on-error. A stage wired continue-on-error is one the
# issue can ship without, and that is exactly what critical=False means; if the
# two ever disagree, the email calls a blocking failure "non-blocking".
soft_in_wf = set()
for st in steps:
    m = re.search(r'run_stage\.sh\s+"([^"]+)"', st["body"])
    if m and "continue-on-error: true" in st["body"]:
        soft_in_wf.add(m.group(1))
soft_declared = {s.name for s in PS.PIPELINE_STAGES if not s.critical}
check("continue-on-error in the workflow == critical=False in the list",
      sorted(soft_in_wf), sorted(soft_declared))

# The email must be able to run at all on a run that failed earlier.
email_step = [st for st in steps if st["name"] == "Send the daily status email"][0]
check_true("the status email step runs even after a failure",
           "if: always()" in email_step["body"])
check_true("and does not itself halt the gate below",
           "continue-on-error: true" in email_step["body"])
check("the gate runs last", steps[-1]["name"], "Run-quality gate")
check_true("...and always", "if: always()" in steps[-1]["body"])

# The status email imports nothing outside the standard library, on purpose: a
# run that dies in `pip install` must still be able to say so.
email_src = (REPO / "email_newsletter.py").read_text(encoding="utf-8")
ps_src = (REPO / "pipeline_status.py").read_text(encoding="utf-8")
third_party = {"anthropic", "requests", "bs4", "feedparser", "dotenv", "PIL",
               "playwright", "substack", "curl_cffi", "yaml"}
for name, src in (("email_newsletter", email_src), ("pipeline_status", ps_src)):
    imported = set(re.findall(r'^(?:import|from)\s+([a-zA-Z_][\w]*)', src, re.M))
    check(f"{name}.py imports nothing third-party (survives a failed pip install)",
          sorted(imported & third_party), [])


# ===========================================================================
print()
print("=" * 72)
print("THE EMAIL — it always sends, and it says what broke")
print("=" * 72)

sent: list[tuple[str, str, list]] = []
EN._smtp_send = lambda subject, html, inline: sent.append((subject, html, inline))

work = tempfile.TemporaryDirectory()
WORK = Path(work.name)
EN.SUBSTACK_PATH = WORK / "newsletter_substack.html"
EN.BOX_SCORE_DIR = WORK / "box_score"
EN.COST_SUMMARY_PATH = WORK / "cost_summary.json"
EN.SUBSTACK_STATE_PATH = WORK / "substack_post_state.json"
EN.PUBLISH_RESULT_PATH = WORK / "publish_result.json"
EN.BOX_SCORE_DIR.mkdir()
EN.os.environ["GMAIL_ADDRESS"] = "abram@example.com"
EN.os.environ["GMAIL_PASSWORD"] = "app-password-not-real"
EN.os.environ.pop("GITHUB_RUN_ID", None)

# --- a run that died before there was a newsletter -------------------------
dead = status_with(failed_stages={"Fetch sports data": 1},
                   drop_stages=["Check sports data health", "Validate raw content",
                                "Generate newsletter", "Render box score images",
                                "Verify outputs", "Archive outputs",
                                "Commit and push outputs", "Create Substack draft",
                                "Commit Substack draft handoff",
                                "Publish late if past 12:30 PM ET",
                                "Assess run quality"])
for s in dead["stages"]:
    if s["name"] == "Fetch sports data":
        s["log_tail"] = "requests.HTTPError: 403 Client Error for espn.com"
run_status.record(**dead)

sent.clear()
check("a run with no newsletter STILL sends an email", EN.send_daily_email(), True)
subject, body, inline = sent[0]
check_true("the subject leads with the failure", subject.startswith("SLAP ❌"))
check_true("the subject names the broken stage", "Fetch sports data" in subject)
check_true("the body names the broken stage", "Fetch sports data" in body)
check_true("the body carries the ACTUAL error, not just 'failed'",
           "403 Client Error for espn.com" in body)
check_true("the body says there is nothing to copy",
           "NO NEWSLETTER WAS PRODUCED" in body)
check_true("stages that never ran are shown as such", "never ran" in body)
# The panel is full of ✅/❌/—, and a bare <body> renders as mojibake wherever
# the client guesses latin-1 instead of trusting the MIME charset.
check_true("a body we generated ourselves declares its charset",
           '<meta charset="utf-8">' in body)
check("a failed pipeline still counts as a successful SEND",
      run_status.load()["email_sent"], True)

# --- the normal, everything-worked run -------------------------------------
EN.SUBSTACK_PATH.write_text(
    "<html><body><h1>Ohtani Does It Again</h1><p>The lead.</p>"
    "<h2>Box Scores</h2></body></html>", encoding="utf-8")
EN.COST_SUMMARY_PATH.write_text(json.dumps({
    "date": "2026-09-20", "total": 1.2345,
    "passes": [{"label": "Pass 2 (Writer)", "model": "claude-opus-4-7",
                "cost": 1.2, "in_tokens": 40000, "out_tokens": 7000}]}),
    encoding="utf-8")
import datetime as _dt  # noqa: E402
_today_et = _dt.datetime.now(EN.ET).date().isoformat()
EN.SUBSTACK_STATE_PATH.write_text(json.dumps({
    "date": _today_et, "draft_id": 213696047, "title": "SLAP",
    "url": "https://slap.substack.com/publish/post/213696047"}), encoding="utf-8")

run_status.record(**status_with(quality={
    "errors": [], "warnings": ["2 meme(s) rendered against a floor of 3"],
    "counts": {"tweets": 21, "gifs": 7, "memes": 2, "highlights": 3, "words": 1400},
    "box_images": 5, "media_share": 30}))

sent.clear()
check("the good-day email sends", EN.send_daily_email(), True)
subject, body, inline = sent[0]
check_true("the subject is the plain SLAP subject, no alarm",
           subject.startswith("SLAP ") and "❌" not in subject and "⚠" not in subject)
check_true("the subject carries the headline", "Ohtani Does It Again" in subject)
check_true("the status panel says SUCCESS", "PIPELINE SUCCESS" in body)
check_true("the cost breakdown rides along", "SLAP DAILY COST" in body)
check_true("...with the total", "$1.2345" in body)
check_true("the Substack draft is confirmed", "213696047" in body)
check_true("and linked", "publish/post/213696047" in body)
check_true("noon silence is explained",
           "only hear from the noon publish job" in body)
check_true("the warning is shown without changing the verdict",
           "floor of 3" in body and "PIPELINE SUCCESS" in body)
check_true("the copy marker is present", "Copy newsletter below this line" in body)
check("everything above the marker is status, the issue is below",
      body.index("Copy newsletter below this line") < body.index("Ohtani Does It Again"),
      True)
check("the cost table sits above the marker too",
      body.index("SLAP DAILY COST") < body.index("Copy newsletter below this line"),
      True)

# --- the draft that was never created --------------------------------------
EN.SUBSTACK_STATE_PATH.write_text(json.dumps({
    "date": "2026-09-14", "draft_id": 1, "title": "old"}), encoding="utf-8")
sent.clear()
EN.send_daily_email()
check_true("a stale handoff is called out, not read as today's",
           "never created" in sent[0][1])

EN.SUBSTACK_STATE_PATH.unlink()
sent.clear()
EN.send_daily_email()
check_true("a missing handoff is called out too",
           "no draft handoff was written" in sent[0][1])

# --- a partial run ----------------------------------------------------------
run_status.record(**status_with(failed_stages={"Create Substack draft": 1}))
sent.clear()
EN.send_daily_email()
check_true("a non-blocking failure makes the subject ⚠, not ❌",
           sent[0][0].startswith("SLAP ⚠"))
check_true("and the panel explains the consequence in plain English",
           "no draft to publish at 12:30 PM ET" in sent[0][1])

# --- the send itself failing ------------------------------------------------
def _boom(subject, html, inline):
    raise OSError("[Errno 101] Network is unreachable")


EN._smtp_send = _boom
check("a failed send reports False", EN.send_daily_email(), False)
check("and records it for the gate", run_status.load()["email_sent"], False)
check_true("with the reason attached",
           "Network is unreachable" in run_status.load()["email_error"])
EN._smtp_send = lambda subject, html, inline: sent.append((subject, html, inline))


# ===========================================================================
print()
print("=" * 72)
print("THE NOON FOLLOW-UP — silence means it published")
print("=" * 72)


def alert(result: dict | None, step_outcome="success") -> bool:
    sent.clear()
    if result is None:
        if EN.PUBLISH_RESULT_PATH.exists():
            EN.PUBLISH_RESULT_PATH.unlink()
    else:
        EN.PUBLISH_RESULT_PATH.write_text(json.dumps(result), encoding="utf-8")
    return EN.send_publish_alert(str(EN.PUBLISH_RESULT_PATH), step_outcome)


check("a normal publish sends NOTHING",
      alert({"outcome": "published", "needs_attention": False,
             "reason": "Published 'SLAP'."}), False)
check("a draft Abram published himself sends nothing",
      alert({"outcome": "skipped_already_published", "needs_attention": False,
             "reason": "already published"}), False)
check("a draft Abram deleted sends nothing",
      alert({"outcome": "skipped_draft_deleted", "needs_attention": False,
             "reason": "deleted"}), False)
check("a draft he scheduled in Substack sends nothing",
      alert({"outcome": "skipped_already_scheduled", "needs_attention": False,
             "reason": "scheduled"}), False)

check("but a draft that was never created DOES email",
      alert({"outcome": "skipped_stale_handoff", "needs_attention": True,
             "reason": "The newest draft handoff is from 2026-09-14, not today."}), True)
check_true("and says the issue is not live", "not live" in sent[0][1])
check_true("and gives the actual reason", "2026-09-14" in sent[0][1])
check_true("with an unmistakable subject", sent[0][0].startswith("SLAP ❌"))

check("a crash that wrote no result file still emails",
      alert(None, step_outcome="failure"), True)
check_true("naming it as a crash", "before it could report" in sent[0][1])
check("no result file and a PASSING step stays quiet",
      alert(None, step_outcome="success"), False)


# ===========================================================================
print()
print("=" * 72)
print("verify_run.py --record / --gate")
print("=" * 72)

with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    for name in ("verify_run.py", "run_status.py", "pipeline_status.py"):
        shutil.copy(REPO / name, t / name)
    # An issue with no media at all: a hard error by verify_run's own rules.
    (t / "newsletter_draft.html").write_text(
        "<h1>x</h1>" + '<blockquote class="tweet">t</blockquote>' * 8, encoding="utf-8")
    shutil.copy(t / "newsletter_draft.html", t / "newsletter_substack.html")
    (t / "run_status.json").write_text(json.dumps({"date": "x"}), encoding="utf-8")

    def run(*flags):
        return subprocess.run([sys.executable, "-X", "utf8", "verify_run.py", *flags],
                              cwd=t, capture_output=True, text=True, encoding="utf-8")

    r = run("--record")
    check("--record exits 0 even on a broken issue, so the email still sends",
          r.returncode, 0)
    q = json.loads((t / "run_status.json").read_text(encoding="utf-8"))["quality"]
    check_true("and records the reason", any("zero GIFs" in e for e in q["errors"]))
    check("and the counts the email prints", q["counts"]["tweets"], 8)

    r = run("--gate")
    check("--gate fails the job on the same finding", r.returncode, 1)
    check_true("and names it on one line", "zero GIFs" in r.stdout)
    check("--gate does not repeat the whole table", "RUN QUALITY" in r.stdout, False)

    r = run()
    check("the plain invocation is unchanged (exit code + full report)",
          (r.returncode, "RUN QUALITY" in r.stdout), (1, True))


# ===========================================================================
print()
print("=" * 72)
print("ci/run_stage.sh — the wrapper must be invisible to pass/fail")
print("=" * 72)

if shutil.which("bash"):
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "ci").mkdir()
        shutil.copy(REPO / "ci" / "run_stage.sh", t / "ci" / "run_stage.sh")
        for name in ("run_status.py", "pipeline_status.py"):
            shutil.copy(REPO / name, t / name)

        def stage(name, script, use_stdin=False):
            if use_stdin:
                return subprocess.run(
                    ["bash", "ci/run_stage.sh", name], cwd=t, input=script,
                    capture_output=True, text=True, encoding="utf-8")
            return subprocess.run(
                ["bash", "ci/run_stage.sh", name, *script.split()], cwd=t,
                capture_output=True, text=True, encoding="utf-8")

        subprocess.run([sys.executable, "-X", "utf8", "pipeline_status.py", "start"],
                       cwd=t, capture_output=True)
        r = stage("Fetch content", "echo hello")
        check("a passing stage exits 0", r.returncode, 0)
        check_true("and its output still reaches the job log", "hello" in r.stdout)

        r = stage("Verify outputs", "echo 'about to fail'\nexit 7", use_stdin=True)
        check("a failing stage RE-RAISES its own exit code", r.returncode, 7)

        st = json.loads((t / "run_status.json").read_text(encoding="utf-8"))
        by = {s["name"]: s for s in st["stages"]}
        check("both stages were recorded", sorted(by), ["Fetch content", "Verify outputs"])
        check("the failure is recorded as such", by["Verify outputs"]["exit_code"], 7)
        check_true("with its output kept", "about to fail" in by["Verify outputs"]["log_tail"])
        check_true("and a duration", by["Fetch content"]["seconds"] is not None)
else:
    print("  [skip] bash not on PATH — wrapper checks run in CI (ubuntu)")


# ===========================================================================
print()
print("=" * 72)
print("ensure_started() must not erase stages recorded before the generator")
print("=" * 72)

PS.start()
PS.record_stage("Fetch content", 0, seconds=9)
PS.ensure_started()
check("today's stages survive a generator run mid-pipeline",
      [s["name"] for s in run_status.load()["stages"]], ["Fetch content"])

run_status.record(run_started="2026-01-01T02:17:00-05:00")
PS.ensure_started()
check("a status file left over from another day is cleared",
      run_status.load()["stages"], [])


print()
if failures:
    print("=" * 72)
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("=" * 72)
print("ALL CHECKS PASSED — 0 API calls, 0 emails sent")
print("=" * 72)
