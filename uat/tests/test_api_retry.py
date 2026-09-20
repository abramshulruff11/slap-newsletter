"""
Run:  python -X utf8 uat/tests/test_api_retry.py

Locks SLA-54: retry with backoff around the Anthropic calls.

WHAT IS ACTUALLY AT RISK
  1. Retrying the wrong thing. A 400, a bad model name or the SDK's own
     client-side streaming ValueError never get better by waiting, and
     retrying them turns a five-second red run into a several-minute one that
     looks like an outage. The classifier is the load-bearing part.

  2. Not retrying the right thing, or not retrying it for long enough. The SDK
     already retries 429/5xx twice, capped around 8 seconds; the outer loop is
     what covers a rate limit that outlasts that.

  3. A call site losing its wrapper. There are seven across three files and two
     of them live in functions that are DECLARED divergent between the runners,
     which is exactly where a half-port goes unnoticed — so the call sites are
     checked by reading the source, not only by exercising the helper.

  4. The retry never being visible. The 2026-09-01 lesson is that a failure
     nothing acts on is not a signal. Recoveries and give-ups are recorded into
     run_status.json so the morning email shows them.

  5. An unbounded loop. A real outage must still fail inside the job's
     30-minute cap rather than hang until GitHub kills it with no email.

No API calls, no network, no sleeping: time.sleep is replaced by a recorder.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import run_status  # noqa: E402

_TMP = tempfile.TemporaryDirectory()
run_status.STATUS_PATH = Path(_TMP.name) / "run_status.json"
run_status.reset()

import anthropic       # noqa: E402
import runner_common as RC  # noqa: E402

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        failures.append(f"{label}: expected {want!r}, got {got!r}")


def check_true(label, got):
    check(label, bool(got), True)


class Status(Exception):
    """Stands in for an SDK error carrying an HTTP status."""

    def __init__(self, status):
        self.status_code = status
        super().__init__(f"HTTP {status}")


print("=" * 72)
print("CLASSIFIER — what is worth another try, and what never will be")
print("=" * 72)

for code in (408, 409, 425, 429, 500, 502, 503, 504, 529):
    check(f"HTTP {code} is transient", RC.is_transient(Status(code)), True)
for code in (400, 401, 403, 404, 413, 422):
    check(f"HTTP {code} is NOT transient", RC.is_transient(Status(code)), False)

check_true("a socket timeout is transient", RC.is_transient(TimeoutError("timed out")))
check_true("a connection error is transient", RC.is_transient(ConnectionError("reset")))

# The 2026-09-01 outage was a client-side ValueError from the SDK ("Streaming is
# required..."). Retrying it three times would have burned minutes to reach the
# same red run, and the log would have implied an API problem.
check("the SDK's client-side ValueError is NOT transient",
      RC.is_transient(ValueError("Streaming is required for operations that "
                                 "may take longer than 10 minutes")), False)
check("a plain bug is NOT transient",
      RC.is_transient(KeyError("lead_story")), False)
check("neither is a RuntimeError from our own code",
      RC.is_transient(RuntimeError("Pass 1 produced invalid output")), False)

# Public SDK classes, since a version bump could change the internals.
for name in ("APITimeoutError", "APIConnectionError", "RateLimitError",
             "InternalServerError", "BadRequestError", "AuthenticationError"):
    check_true(f"anthropic.{name} still exists (the classifier names it)",
               hasattr(anthropic, name))


print()
print("=" * 72)
print("BACKOFF — bounded, growing, and finished well inside the job timeout")
print("=" * 72)

slept: list[float] = []


def rec(sec):
    slept.append(sec)


def run(fail_times, status=429, attempts=None):
    """Fail `fail_times` times, then succeed. Returns (result, call_count)."""
    slept.clear()
    RC.reset_simulated_failures()
    n = {"c": 0}

    def call():
        n["c"] += 1
        if n["c"] <= fail_times:
            raise Status(status)
        return "ok"

    kw = {"sleep": rec}
    if attempts is not None:
        kw["attempts"] = attempts
    return RC.retry_api_call("PASS T", call, **kw), n["c"]


out, n = run(0)
check("a call that works is called exactly once", (out, n), ("ok", 1))
check("and never sleeps", slept, [])

out, n = run(2)
check("two transient failures are ridden out", (out, n), ("ok", 3))
check("with exponential backoff", slept, [4.0, 8.0])

out, n = run(3)
check("three are still inside the budget", (out, n), ("ok", 4))
check("and the waits keep doubling", slept, [4.0, 8.0, 16.0])

try:
    run(99)
    gave_up = False
except Status:
    gave_up = True
check("a persistent outage RAISES rather than looping forever", gave_up, True)
check("after exactly RETRY_ATTEMPTS tries", len(slept), RC.RETRY_ATTEMPTS - 1)
total = sum(slept)
check_true(f"and the total wait ({total:.0f}s) is far inside the 30-minute cap",
           total < 300)
check_true("the per-wait cap is honoured",
           all(w <= RC.RETRY_MAX_DELAY for w in slept))

# Worst case across the whole pipeline, including the SDK's own retries.
worst = RC.RETRY_ATTEMPTS * (1 + RC.SDK_MAX_RETRIES)
check_true(f"worst case is {worst} HTTP attempts per call, which is bounded",
           worst <= 20)

n = {"c": 0}
slept.clear()


def fatal():
    n["c"] += 1
    raise Status(400)


try:
    RC.retry_api_call("PASS T", fatal, sleep=rec)
except Status:
    pass
check("a non-transient failure is tried ONCE", n["c"], 1)
check("and waits zero seconds", slept, [])


print()
print("=" * 72)
print("THE RUN SAYS SO — recoveries and give-ups reach the morning email")
print("=" * 72)

run_status.reset()
run(1)
recorded = run_status.load().get("api_retries") or []
check_true("a recovery is recorded", any("recovered" in r for r in recorded))
check_true("naming the pass", any(r.startswith("PASS T") for r in recorded))

try:
    run(99)
except Status:
    pass
recorded = run_status.load().get("api_retries") or []
check_true("so is a give-up", any("gave up" in r for r in recorded))

import email_newsletter as EN  # noqa: E402

panel = EN._retries_html({"api_retries": ["PASS 2: recovered on attempt 3/4",
                                          "PASS 1: gave up after 4 attempts"]})
check_true("the email renders them", "API RETRIES" in panel)
check_true("with the recovery", "recovered on attempt 3/4" in panel)
check_true("and the give-up marked red", "#b42318" in panel)
check("no retries means no section", EN._retries_html({"api_retries": []}), "")

# A retry is not a failure: the run survived it. If it moved the verdict, a
# day that recovered would read the same as a day that broke.
import pipeline_status as PS  # noqa: E402

clean = {"stages": [{"name": s.name, "ok": True, "exit_code": 0,
                     "critical": s.critical} for s in PS.PIPELINE_STAGES],
         "email_sent": True, "api_retries": ["PASS 2: recovered on attempt 3/4"]}
check("a recovered retry does NOT downgrade the verdict",
      PS.verdict(clean)[0], "success")


print()
print("=" * 72)
print("THE DRILL SWITCH — testable on demand, off by default")
print("=" * 72)

os.environ.pop("SLAP_SIMULATE_API_FAILURES", None)
RC.reset_simulated_failures()
out, n = run(0)
check("unset, the drill does nothing", (out, n), ("ok", 1))

for spec, want_calls, want_sleeps in (("2", 1, 2), ("429:1", 1, 1),
                                      ("529:2", 1, 2), ("timeout:1", 1, 1)):
    os.environ["SLAP_SIMULATE_API_FAILURES"] = spec
    RC.reset_simulated_failures()
    out, n = run(0)
    check(f"{spec!r} injects {want_sleeps} failure(s) then recovers",
          (out, n, len(slept)), ("ok", want_calls, want_sleeps))

os.environ["SLAP_SIMULATE_API_FAILURES"] = "fatal"
RC.reset_simulated_failures()
try:
    run(0)
    drilled_fatal = False
except Exception:
    drilled_fatal = True
check("'fatal' proves the no-retry side too", drilled_fatal, True)

os.environ["SLAP_SIMULATE_API_FAILURES"] = "exhaust"
RC.reset_simulated_failures()
try:
    run(0)
    drilled_exhaust = False
except Exception:
    drilled_exhaust = True
check("'exhaust' proves the give-up path", drilled_exhaust, True)

os.environ["SLAP_SIMULATE_API_FAILURES"] = "nonsense"
RC.reset_simulated_failures()
out, n = run(0)
check("an unreadable spec is ignored, never fatal", (out, n), ("ok", 1))
os.environ.pop("SLAP_SIMULATE_API_FAILURES", None)
RC.reset_simulated_failures()


print()
print("=" * 72)
print("EVERY CALL SITE IS WRAPPED — including the ones in divergent functions")
print("=" * 72)

# Two of these live in run_pass1/run_pass2, which test_runner_drift.py declares
# divergent between the runners. That is precisely where a change reaches one
# copy and not the other, so count them in the source rather than trusting that
# exercising the helper proves the pipeline uses it.
EXPECTED = {
    "runner_common.py": {"PASS 4", "PASS 6"},
    "generate_newsletter.py": {"PASS 1", "PASS 2"},
    "uat/generate_newsletter_uat.py": {"PASS 1", "PASS 2", "PASS 1B"},
}
for rel, labels in EXPECTED.items():
    src = (REPO / rel).read_text(encoding="utf-8")
    wrapped = set(re.findall(r'retry_api_call\(\s*"([^"]+)"', src))
    # Pass 1 hands a named function rather than a lambda; same call, same label.
    check(f"{rel}: every pass is wrapped", sorted(wrapped), sorted(labels))

    # Nothing may reach the API around the outside of the wrapper. Comments
    # are stripped first: uat/generate_newsletter_uat.py explains in prose that
    # get_final_message() returns what client.messages.create() used to, and a
    # scanner that counts that sentence as a call site is a scanner nobody will
    # trust the next time it goes red.
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    calls = re.findall(r'client\.messages\.(?:create|stream)\(', code)
    guarded = (re.findall(r'retry_api_call\("[^"]+", lambda: client\.messages\.create\(',
                          code)
               # Pass 1 streams, so it hands over a named function instead; the
               # `with` sits inside _call_story_selector, which is what the
               # wrapper is given.
               + re.findall(r'with client\.messages\.stream\(', code))
    check(f"{rel}: every client.messages call is inside the wrapper",
          len(calls), len(guarded))

# The client must be built with an explicit retry count, not the SDK default.
for rel in ("generate_newsletter.py", "uat/run_uat.py"):
    src = (REPO / rel).read_text(encoding="utf-8")
    check_true(f"{rel}: the client sets max_retries explicitly",
               "max_retries=SDK_MAX_RETRIES" in src)

# Pass 1's old corrective message asserted the cause was unescaped quotes. For a
# 429 that was false, and it cost a validation attempt to say it.
prod = (REPO / "generate_newsletter.py").read_text(encoding="utf-8")
check("Pass 1 no longer asserts a cause it cannot know",
      "This is usually caused by special characters" in prod, False)


print()
print("=" * 72)
print("COST — a retried call must not be counted twice, or lost")
print("=" * 72)

# The ticket's worry, checked behaviourally rather than by reading regexes: the
# wrapper itself must never touch the cost ledger. It cannot, because
# cost_summary() is only ever handed the usage of a response that arrived --
# and a failed attempt returns no response at all.
import inspect  # noqa: E402

check("the wrapper contains no cost accounting",
      "cost_summary" in inspect.getsource(RC.retry_api_call), False)

RC.PASS_COSTS.clear()
run(2)
check("three HTTP attempts add ZERO rows to the cost ledger",
      len(RC.PASS_COSTS), 0)
RC.cost_summary("PASS T", RC.MODEL_DEFAULT, 1000, 100)
check("the caller's single post-success call adds exactly one",
      len(RC.PASS_COSTS), 1)
check("counted once, not once per attempt", RC.PASS_COSTS[0]["in_tokens"], 1000)
RC.PASS_COSTS.clear()


# The drill must never be able to fire on the daily schedule. A pipeline that
# quietly injects failures every morning would be a self-inflicted outage, so
# the ONLY thing allowed to set it is a manual dispatch input.
wf = (REPO / ".github" / "workflows" / "daily-newsletter.yml").read_text(encoding="utf-8")
setters = [ln.strip() for ln in wf.splitlines()
           if "SLAP_SIMULATE_API_FAILURES:" in ln]
check("exactly one place in the workflow sets the drill", len(setters), 1)
check_true("and it is a manual dispatch input, not a literal",
           setters and "inputs.simulate_api_failures" in setters[0])
check_true("declared as a dispatch input", "simulate_api_failures:" in wf)
for other in (".github/workflows/publish-substack.yml", ".github/workflows/tests.yml"):
    src = (REPO / other).read_text(encoding="utf-8")
    check(f"{other} never sets it", "SLAP_SIMULATE_API_FAILURES" in src, False)


print()
if failures:
    print("=" * 72)
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("=" * 72)
print("ALL CHECKS PASSED — 0 API calls, 0 seconds slept")
print("=" * 72)
