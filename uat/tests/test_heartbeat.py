"""
Run:  python -X utf8 uat/tests/test_heartbeat.py

Locks SLA-56: the dead man's switch pings.

WHAT IS ACTUALLY AT RISK HERE
  1. The heartbeat becoming a new way for the newsletter to break. It is
     monitoring, not a stage: every network failure is swallowed, the exit code
     is always 0, and with no secret configured it does nothing at all.

  2. The finish signal meaning the wrong thing. SUCCESS must mean "Abram got
     today's email" — not "the run was green". A red run whose email went out
     is a success ping (the email already said so; a second alert is noise).
     A run whose email did NOT go out must be a FAIL ping, because then the
     heartbeat is the only channel left.

  3. The wiring. The start ping has to run BEFORE the installs (a pip failure
     must still count as "started"), the finish ping AFTER the email under
     always(), and neither may be a declared pipeline stage or sit below the
     run-quality gate.

  4. Imports. Like the status email, heartbeat.py must survive a failed
     `pip install`, so it imports nothing outside the standard library.

No network: urllib.request.urlopen and time.sleep are stubbed.
"""
from __future__ import annotations

import io
import json
import re
import sys
import tempfile
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import heartbeat as HB  # noqa: E402

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        failures.append(f"{label}: expected {want!r}, got {got!r}")


def check_true(label, got):
    check(label, bool(got), True)


# --- stubs -----------------------------------------------------------------
SENT: list[tuple[str, str]] = []
MODE = {"fail_times": 0}


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    if MODE["fail_times"] > 0:
        MODE["fail_times"] -= 1
        raise urllib.error.URLError("simulated outage")
    SENT.append((req.full_url, req.data.decode("utf-8")))
    return _Resp()


HB.urllib.request.urlopen = fake_urlopen
HB.time.sleep = lambda s: None

_TMP = tempfile.TemporaryDirectory()
HB.STATUS_PATH = Path(_TMP.name) / "run_status.json"

BASE = "https://hc-ping.com/00000000-test"


def run(cmd, env_url=BASE, status=None, fail_times=0):
    SENT.clear()
    MODE["fail_times"] = fail_times
    if status is None:
        HB.STATUS_PATH.unlink(missing_ok=True)
    else:
        HB.STATUS_PATH.write_text(json.dumps(status), encoding="utf-8")
    import os
    if env_url is None:
        os.environ.pop(HB.ENV_VAR, None)
    else:
        os.environ[HB.ENV_VAR] = env_url
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = HB.main(cmd)
    return code, list(SENT), buf.getvalue()


# ===========================================================================
print("=" * 72)
print("THE FINISH SIGNAL — did Abram get today's email?")
print("=" * 72)

sig, body = HB.finish_signal({"email_sent": True, "email_subject": "SLAP 9/23 — x"})
check("email sent -> success", sig, "success")
check_true("...and the body names the subject", "SLAP 9/23" in body)

sig, _ = HB.finish_signal({
    "email_sent": True,
    "stages": [{"name": "Generate newsletter", "exit_code": 1}],
})
check("a FAILED run whose email went out is still success (no double alert)", sig, "success")

sig, body = HB.finish_signal({"email_sent": False, "email_error": "SMTPAuthenticationError: 535"})
check("email failed -> fail", sig, "fail")
check_true("...and quotes the real error", "535" in body)

sig, body = HB.finish_signal({"stages": [{"name": "Install dependencies", "exit_code": 1}]})
check("email step never recorded anything -> fail", sig, "fail")
check_true("...and says so", "never ran" in body)
check_true("...and names the failed stage", "Install dependencies" in body)

check("no run_status.json at all -> fail", HB.finish_signal({})[0], "fail")


# ===========================================================================
print()
print("=" * 72)
print("THE PINGS — right URL, and never a failed step")
print("=" * 72)

code, sent, _ = run(["start"])
check("start pings /start", [u for u, _ in sent], [BASE + "/start"])
check("start exits 0", code, 0)

code, sent, _ = run(["finish"], status={"email_sent": True})
check("finish + email sent pings the bare URL (success)", [u for u, _ in sent], [BASE])

code, sent, _ = run(["finish"], status={"email_sent": False, "email_error": "boom"})
check("finish + email NOT sent pings /fail", [u for u, _ in sent], [BASE + "/fail"])

code, sent, _ = run(["finish"], env_url=BASE + "/", status={"email_sent": True})
check("a trailing slash on the secret does not double up", [u for u, _ in sent], [BASE])

code, sent, out = run(["start"], env_url=None)
check("secret unset -> no request", sent, [])
check("secret unset -> exit 0", code, 0)
check_true("secret unset -> says it is not armed", "not armed" in out)

code, sent, out = run(["start"], env_url="  ")
check("blank secret is treated as unset", sent, [])

code, sent, out = run(["start"], fail_times=1)
check("a transient failure is retried and then lands", len(sent), 1)

code, sent, out = run(["finish"], status={"email_sent": True}, fail_times=99)
check("a permanent outage exits 0 anyway", code, 0)
check_true("...and says the service will alert at the cutoff", "cutoff" in out)

code, sent, _ = run(["bogus"])
check("an unknown command exits 0 and sends nothing", (code, sent), (0, []))

code, sent, _ = run(["finish"], status={"email_sent": False, "email_error": "x" * 50_000})
check("the ping body is capped", len(sent[0][1]) <= HB.MAX_BODY_CHARS, True)


# ===========================================================================
print()
print("=" * 72)
print("THE WIRING — before the installs, after the email, above the gate")
print("=" * 72)

wf = (REPO / ".github" / "workflows" / "daily-newsletter.yml").read_text(encoding="utf-8")


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
names = [s["name"] for s in steps]
by_name = {s["name"]: s for s in steps}
start = by_name.get("Heartbeat — started")
finish = by_name.get("Heartbeat — finished")
check_true("a start-heartbeat step exists", start)
check_true("a finish-heartbeat step exists", finish)
if start and finish:
    check_true("start runs heartbeat.py start", "python heartbeat.py start" in start["body"])
    check_true("finish runs heartbeat.py finish", "python heartbeat.py finish" in finish["body"])
    check_true("start is before the dependency install",
               names.index("Heartbeat — started") < names.index("Install dependencies"))
    check_true("finish is after the daily status email",
               names.index("Heartbeat — finished") > names.index("Send the daily status email"))
    check("the run-quality gate is still last", names[-1], "Run-quality gate")
    check_true("finish runs even after a failure", "if: always()" in finish["body"])
    for label, st in (("start", start), ("finish", finish)):
        check_true(f"{label} cannot fail the job", "continue-on-error: true" in st["body"])
        check_true(f"{label} gets the secret",
                   "SLAP_HEARTBEAT_URL: ${{ secrets.SLAP_HEARTBEAT_URL }}" in st["body"])
        check(f"{label} is not a pipeline stage", "run_stage.sh" in st["body"], False)

# Only the daily workflow pings: a manual connectivity test or the noon publish
# job must not be able to satisfy the daily check.
for other in (REPO / ".github" / "workflows").glob("*.yml"):
    if other.name != "daily-newsletter.yml":
        check(f"{other.name} does not ping the heartbeat",
              "heartbeat.py" in other.read_text(encoding="utf-8"), False)

src = (REPO / "heartbeat.py").read_text(encoding="utf-8")
imported = set(re.findall(r'^(?:import|from)\s+([a-zA-Z_][\w]*)', src, re.M))
stdlib = set(sys.stdlib_module_names) | {"__future__"}
check("heartbeat.py imports only the standard library", sorted(imported - stdlib), [])

doc = REPO / "docs" / "dead_mans_switch.md"
check_true("the setup + pause doc exists", doc.exists())
if doc.exists():
    d = doc.read_text(encoding="utf-8")
    check_true("the doc gives the schedule the service must mirror", "17 6 * * *" in d)
    check_true("the doc says how to pause without dismantling", "Pause" in d)


print()
if failures:
    print("=" * 72)
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("=" * 72)
print("ALL CHECKS PASSED — 0 network requests")
print("=" * 72)
