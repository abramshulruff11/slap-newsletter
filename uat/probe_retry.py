"""
Prove the API retry works, without waiting for a real rate limit.

    python -X utf8 uat/probe_retry.py

Every line it prints is plain English and says PASS or FAIL. It makes ZERO API
calls and sends nothing: it drives the real `runner_common.retry_api_call`
against a fake client that fails on cue, using the real classifier
(`is_transient`) and the real drill hook (`SLAP_SIMULATE_API_FAILURES`), and it
fast-forwards the backoff clock instead of actually sleeping — so the whole
thing finishes in under a second while still proving the waits are right.

WHY A PROBE AND NOT JUST THE TEST SUITE
    uat/tests/test_api_retry.py locks this behaviour for CI. This file exists so
    Abram can SEE it: one command, a readable verdict per behaviour, and the
    real log lines the pipeline would print at 3am printed underneath, so what
    he reads here is what he would read in the status email.

TO DRILL IT IN A REAL RUN INSTEAD
    Set the same variable the probe uses and run the pipeline for real:

        SLAP_SIMULATE_API_FAILURES=2      every pass hits two 429s, then recovers
        SLAP_SIMULATE_API_FAILURES=fatal  every pass hits a 400 — must NOT retry
        SLAP_SIMULATE_API_FAILURES=exhaust  never recovers — must give up

    Never set it on the daily schedule; it is a drill switch.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import run_status  # noqa: E402

_TMP = tempfile.TemporaryDirectory()
run_status.STATUS_PATH = Path(_TMP.name) / "run_status.json"
run_status.reset()

import runner_common as RC  # noqa: E402

results: list[tuple[bool, str]] = []
slept: list[float] = []


def fake_sleep(seconds: float) -> None:
    """Record the wait instead of taking it, so the probe is instant but the
    backoff is still checked."""
    slept.append(seconds)


def verdict(ok: bool, headline: str, detail: str = "") -> None:
    results.append((ok, headline))
    print(f"\n{'PASS' if ok else 'FAIL'} — {headline}")
    if detail:
        print(f"       {detail}")


def scenario(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)
    slept.clear()
    RC.reset_simulated_failures()
    os.environ.pop("SLAP_SIMULATE_API_FAILURES", None)


class Boom(Exception):
    def __init__(self, status):
        self.status_code = status
        super().__init__(f"HTTP {status}")


# ---------------------------------------------------------------------------
scenario("1. A rate limit clears on its own. The run should survive it.")
calls = {"n": 0}


def flaky():
    calls["n"] += 1
    if calls["n"] <= 2:
        raise Boom(429)
    return "the newsletter draft"


out = RC.retry_api_call("PASS 2", flaky, sleep=fake_sleep)
verdict(out == "the newsletter draft" and calls["n"] == 3,
        "Two rate limits in a row did not end the run.",
        f"It tried {calls['n']} times and got the draft on the last one, and "
        f"said so in the log above — which it never used to.")
verdict(slept == [4.0, 8.0],
        "It waited longer each time instead of hammering the API.",
        f"Waited {slept[0]:.0f}s, then {slept[1]:.0f}s. The Anthropic library "
        f"already retried twice on its own, but silently and only for about 8 "
        f"seconds total; a rate limit on a big call outlasts that.")

# ---------------------------------------------------------------------------
scenario("2. A broken request should fail instantly, not slowly.")
calls = {"n": 0}


def broken():
    calls["n"] += 1
    raise Boom(400)


try:
    RC.retry_api_call("PASS 4", broken, sleep=fake_sleep)
    failed_fast = False
except Exception:
    failed_fast = True

verdict(failed_fast and calls["n"] == 1 and not slept,
        "A bad request stopped immediately and waited zero seconds.",
        f"It tried {calls['n']} time and waited {len(slept)} times. Retrying a "
        f"broken request just turns a fast red run into a slow one that looks "
        f"like an outage.")

# ---------------------------------------------------------------------------
scenario("3. A real outage should still give up inside the job's time budget.")
calls = {"n": 0}


def always_down():
    calls["n"] += 1
    raise Boom(529)


try:
    RC.retry_api_call("PASS 1", always_down, sleep=fake_sleep)
    gave_up = False
except Exception:
    gave_up = True

verdict(gave_up and calls["n"] == RC.RETRY_ATTEMPTS,
        "It stopped trying rather than hanging until the job timed out.",
        f"It tried {calls['n']} times, waiting {sum(slept):.0f}s in total — far "
        f"inside the 30-minute job limit, so a genuine outage still fails today "
        f"rather than blocking tomorrow.")

# ---------------------------------------------------------------------------
scenario("4. The drill switch works, so this can be tested on a real run.")
os.environ["SLAP_SIMULATE_API_FAILURES"] = "529:1"
RC.reset_simulated_failures()
calls = {"n": 0}


def healthy():
    calls["n"] += 1
    return "ok"


out = RC.retry_api_call("PASS 6", healthy, sleep=fake_sleep)
verdict(out == "ok" and calls["n"] == 1 and len(slept) == 1,
        "A simulated outage was injected and recovered from.",
        "Setting SLAP_SIMULATE_API_FAILURES on a real pipeline run makes every "
        "pass fail on cue, so the retry can be watched end to end without "
        "waiting for Anthropic to have a bad day.")

os.environ["SLAP_SIMULATE_API_FAILURES"] = "fatal"
RC.reset_simulated_failures()
calls = {"n": 0}
try:
    RC.retry_api_call("PASS 6", healthy, sleep=fake_sleep)
    drill_fatal = False
except Exception:
    drill_fatal = True
verdict(drill_fatal and calls["n"] == 0,
        "The drill can also prove the no-retry side.",
        "SLAP_SIMULATE_API_FAILURES=fatal fails the run immediately, which is "
        "what should happen to a bad API key or a wrong model name.")

os.environ.pop("SLAP_SIMULATE_API_FAILURES", None)
RC.reset_simulated_failures()

# ---------------------------------------------------------------------------
scenario("5. Retries show up in the morning email, not only in the log.")
retries = run_status.load().get("api_retries") or []
verdict(any("PASS 2" in r and "recovered" in r for r in retries)
        and any("gave up" in r for r in retries),
        "Recoveries and give-ups were both recorded for the status email.",
        "Recorded: " + "; ".join(retries))

# ---------------------------------------------------------------------------
print("\n" + "=" * 74)
bad = [h for ok, h in results if not ok]
if bad:
    print(f"{len(bad)} of {len(results)} checks FAILED:")
    for h in bad:
        print(f"  - {h}")
    print("=" * 74)
    raise SystemExit(1)
print(f"All {len(results)} checks PASSED — 0 API calls, nothing sent, "
      f"no real waiting.")
print("=" * 74)
