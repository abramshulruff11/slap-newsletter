"""
Dead man's switch for the daily pipeline (SLA-56).

WHY THIS EXISTS
    Every other reliability check — verify_run.py, check_game_state.py, the
    stage table, the one daily status email (SLA-52) — runs INSIDE the GitHub
    Actions job. If the scheduled workflow never starts, or the job is killed
    before the email step, none of them fire, because none of them ran. A dead
    man's switch cannot depend on the process it is watching, so the alert for
    "the pipeline didn't run today" has to be sent by something else.

    That something is an external heartbeat service (healthchecks.io). This
    module only PINGS it; the service decides a ping is late and emails Abram.
    Same inbox as the daily email, different sender.

THE PINGS
    start     first step of the job, before `pip install` — so a run that dies
              in the install still registers as "started".
    finish    after the daily status email step, under if: always(). Sends
              SUCCESS when run_status.json says the daily email went out (the
              pipeline reported on itself, whatever the verdict was), and FAIL
              when it did not — because then the heartbeat is the only thing
              left that can tell Abram anything.

    A run whose verdict is FAILED but whose email sent is a SUCCESS ping, on
    purpose: the email already said so, and a second alert for the same failure
    is the notification noise SLA-52 removed.

    What the service does with them (see docs/dead_mans_switch.md):
      * no ping at all by the cutoff             -> DOWN email ("never ran")
      * start ping, no finish ping by the cutoff -> DOWN email ("started, died")
      * fail ping                                -> DOWN email, immediately

NEVER FAILS THE STEP
    A monitoring ping must not be a new way for the newsletter to break: every
    network error is printed and swallowed, and the exit code is always 0. If
    SLAP_HEARTBEAT_URL is unset (a fork, a local run, the secret not yet added)
    this is a printed line and nothing else.

    Standard library only, like email_newsletter.py and pipeline_status.py: the
    start ping runs before `pip install`, and the finish ping must still work on
    a run where that install is what broke.

USAGE
    python heartbeat.py start
    python heartbeat.py finish
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENV_VAR = "SLAP_HEARTBEAT_URL"
STATUS_PATH = Path(__file__).resolve().parent / "run_status.json"

# Short and bounded: the ping is two lines of monitoring, not a stage. Worst
# case is ATTEMPTS x TIMEOUT + backoff ~= 35s, well inside any step budget.
TIMEOUT_SECONDS = 10
ATTEMPTS = 3
BACKOFF_SECONDS = (2, 4)

# healthchecks.io stores up to 100 KB of body per ping; keep it a summary.
MAX_BODY_CHARS = 2000


def _base_url() -> str:
    return os.getenv(ENV_VAR, "").strip().rstrip("/")


def _load_status(path: Path | None = None) -> dict:
    try:
        return json.loads((path or STATUS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def finish_signal(status: dict) -> tuple[str, str]:
    """('success' | 'fail', plain-text body) for the end-of-job ping.

    The one question that decides it: did Abram get today's email? If yes, the
    pipeline reported on itself and the heartbeat has nothing to add. If no —
    SMTP broke, credentials expired, the email step never ran — this ping is
    the only remaining way to reach him.
    """
    lines = []
    stages = status.get("stages") or []
    failed = [s.get("name", "?") for s in stages if s.get("exit_code") not in (0, None)]
    if stages:
        lines.append(f"stages recorded: {len(stages)}; failed: {', '.join(failed) or 'none'}")
    if status.get("email_sent") is True:
        lines.insert(0, "Daily status email sent: " + (status.get("email_subject") or "(no subject recorded)"))
        return "success", "\n".join(lines)

    err = status.get("email_error") or (
        "run_status.json has no record of the email step — it never ran, "
        "or the job died before it" if "email_sent" not in status else "unknown")
    lines.insert(0, f"The daily status email did NOT send: {err}")
    lines.append("Abram got no SLAP email from this run. Check the Actions run.")
    return "fail", "\n".join(lines)


def _ping(url: str, body: str = "") -> bool:
    data = body[:MAX_BODY_CHARS].encode("utf-8")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "text/plain; charset=utf-8",
                         "User-Agent": "slap-heartbeat"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                if 200 <= resp.status < 300:
                    return True
                print(f"  ⚠ heartbeat: HTTP {resp.status} (attempt {attempt}/{ATTEMPTS})")
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"  ⚠ heartbeat: {type(e).__name__}: {e} (attempt {attempt}/{ATTEMPTS})")
        if attempt < ATTEMPTS:
            time.sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
    return False


def _run_link() -> str:
    server = os.getenv("GITHUB_SERVER_URL")
    repo = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in ("start", "finish"):
        print("usage: python heartbeat.py start|finish")
        return 0  # still never fail the step — a typo must not stop the run

    base = _base_url()
    if not base:
        print(f"  heartbeat: {ENV_VAR} not set — dead man's switch is not armed, skipping.")
        return 0

    link = _run_link()
    if argv[0] == "start":
        url, body, what = base + "/start", link, "start"
    else:
        signal, body = finish_signal(_load_status())
        body = (body + ("\n" + link if link else "")).strip()
        url = base if signal == "success" else base + "/fail"
        what = signal

    if _ping(url, body):
        print(f"  ✓ heartbeat: sent '{what}'")
    else:
        print(f"  ⚠ heartbeat: could not send '{what}' after {ATTEMPTS} attempts — "
              "the service will treat this run as missing and alert at the cutoff.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
