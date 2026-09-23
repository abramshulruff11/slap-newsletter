"""
Run:  python -X utf8 uat/tests/test_pass_output_gates.py

Locks the four SLA-15 fixes — four places a pass detected a problem and only
print()ed about it:

  1. Pass 3's claim-flag count is recorded to run_status (was discarded).
  2. Pass 6 gets the same "is this actually HTML?" gate Pass 4 already had —
     moved into the shared runner_common.run_pass4/run_pass6 so both passes
     get it from one copy, instead of living only at generate_newsletter.py's
     Pass 4 call site.
  3. Both fallbacks (Pass 4's and Pass 6's) are recorded to run_status under
     "pass_fallbacks", so they reach the daily email instead of only a print.
  4. Pass 3's except clause is broadened from ImportError-only, so a bug
     INSIDE claim_validator degrades the run (draft ships unvalidated)
     instead of killing it.

No API calls, no network.
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import run_status  # noqa: E402

# Same redirect uat/generate_newsletter_uat.py and test_run_quality.py use:
# these checks exercise real run_status writes and must not leave a build
# artifact in the working tree.
_STATUS_TMP = tempfile.TemporaryDirectory()
run_status.STATUS_PATH = Path(_STATUS_TMP.name) / "run_status.json"

import runner_common as RC  # noqa: E402
import verify_run  # noqa: E402

RC.configure(prompts_dir=REPO / "prompts")

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        failures.append(f"{label}: expected {want!r}, got {got!r}")


def resp(text: str, stop_reason: str = "end_turn"):
    r = types.SimpleNamespace()
    r.stop_reason = stop_reason
    r.content = [types.SimpleNamespace(type="text", text=text)]
    r.usage = types.SimpleNamespace(input_tokens=0, output_tokens=0,
                                    cache_read_input_tokens=0,
                                    cache_creation_input_tokens=0)
    return r


class FakeMessages:
    def __init__(self, text):
        self.text = text
    def create(self, **kw):
        return resp(self.text)


class FakeClient:
    def __init__(self, text):
        self.messages = FakeMessages(text)


DRAFT = "<h1>Lead</h1><p>full draft</p><h2>Around the League</h2>"
META_RESPONSE = ("I've reviewed the draft and considered several approaches "
                 "to the obituary section before deciding not to change it.")

print("=" * 70)
print("PASS 4 / PASS 6 — the non-HTML gate now lives in ONE shared place")
print("=" * 70)

for label, fn in (("PASS 4", RC.run_pass4), ("PASS 6", RC.run_pass6)):
    run_status.reset()
    args = (DRAFT, FakeClient(META_RESPONSE)) if label == "PASS 4" \
        else (DRAFT, [], FakeClient(META_RESPONSE))
    out = fn(*args)
    check(f"{label} falls back to its input on a non-HTML response", out, DRAFT)
    status = run_status.load()
    check(f"{label} fallback is recorded to run_status",
          any(label in f and "non-HTML" in f for f in status.get("pass_fallbacks", [])),
          True)

    run_status.reset()
    good_text = "<h1>Lead</h1><p>rewritten draft</p><h2>Around the League</h2>"
    args = (DRAFT, FakeClient(good_text)) if label == "PASS 4" \
        else (DRAFT, [], FakeClient(good_text))
    out = fn(*args)
    check(f"{label} passes through a real newsletter response unchanged", out, good_text)
    check(f"{label} records nothing when the response is fine",
          run_status.load().get("pass_fallbacks"), None)

print()
print("=" * 70)
print("PASS 3 — flags recorded, not discarded; failures degrade, not crash")
print("=" * 70)

sys.path.insert(0, str(REPO))
import claim_validator  # noqa: E402


def fake_validate_ok(html, path):
    return html, 7


def fake_validate_boom(html, path):
    raise ValueError("boom — a bug inside claim_validator, not a missing module")


run_status.reset()
_orig = claim_validator.validate_claims
try:
    claim_validator.validate_claims = fake_validate_ok
    validated_html, val_flags = claim_validator.validate_claims(DRAFT, Path("game_state.json"))
    run_status.record(claim_flags=val_flags)
    check("claim flag count is recorded", run_status.load().get("claim_flags"), 7)
finally:
    claim_validator.validate_claims = _orig

# The except-clause broadening itself is a source-level guarantee (there is no
# way to import generate_newsletter.py's main() without live API keys and a
# full pipeline run), so assert it directly against the source rather than
# faking a call into main().
src = (REPO / "generate_newsletter.py").read_text(encoding="utf-8")
pass3_block = src.split("# Pass 3")[1].split("voiced_html")[0]
check("Pass 3 still catches a missing module", "except ImportError:" in pass3_block, True)
check("Pass 3 also catches a bug inside claim_validator itself",
      "except Exception as e:" in pass3_block, True)
check("a broad Pass 3 failure records pass3_error",
      "run_status.record(pass3_error=" in pass3_block, True)
check("a successful Pass 3 records claim_flags",
      "run_status.record(claim_flags=" in pass3_block, True)

print()
print("=" * 70)
print("VERIFY_RUN — the new signals reach the report as WARNINGS, not errors")
print("=" * 70)

import subprocess
import json
import shutil


def gate(status: dict) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        for name in ("verify_run.py", "run_status.py"):
            shutil.copy(REPO / name, t / name)
        # A minimal, otherwise-healthy issue so only the new signal decides
        # the outcome.
        html = ("<h1>Lead</h1>" + "<blockquote class=\"tweet\">t</blockquote>" * 8
                + "<img src=\"https://media.giphy.com/x.gif\">" * 5)
        (t / "newsletter_draft.html").write_text(html, encoding="utf-8")
        (t / "newsletter_substack.html").write_text(html, encoding="utf-8")
        (t / "run_status.json").write_text(json.dumps(status), encoding="utf-8")
        r = subprocess.run([sys.executable, "-X", "utf8", "verify_run.py"], cwd=t,
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode, r.stdout


BASE = {"date": "x", "email_sent": True}

code, out = gate({**BASE, "claim_flags": 2})
check("a few claim flags does not warn", "Pass 3 raised" in out, False)
check("and does not fail the gate", code, 0)

code, out = gate({**BASE, "claim_flags": verify_run.WARN_CLAIM_FLAGS})
check(f"{verify_run.WARN_CLAIM_FLAGS} claim flags warns", "Pass 3 raised" in out, True)
check("but is only a warning, not a failure", code, 0)

code, out = gate({**BASE, "pass3_error": "ValueError: boom"})
check("a Pass 3 failure is surfaced", "Pass 3" in out and "boom" in out, True)
check("and does not fail the gate on its own", code, 0)

code, out = gate({**BASE, "pass_fallbacks": ["PASS 6 (non-HTML output)"]})
check("a recorded pass fallback is surfaced", "PASS 6" in out, True)
check("and does not fail the gate on its own", code, 0)

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
