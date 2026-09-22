"""
Run:  python -X utf8 uat/tests/test_substack_uploads.py

Locks SLA-68: a box score image that never reaches Substack must be counted,
must be visible, and must not be allowed to eat the job's time budget.

THE BUG THIS EXISTS FOR
    2026-09-21: three of thirteen images failed to upload. The run reported
    SUCCESS. `upload_box_scores` returned only its successes, so a partial
    upload was literally indistinguishable from a complete one — there was no
    number anywhere to compare against. `verify_run.py` counted the PNGs on
    disk, saw thirteen, and was satisfied.

    Meanwhile 4 attempts x a 30s timeout plus backoff is ~132s per hard failure,
    so those three burned ~11 minutes and the run finished at 24m48s against a
    30-minute cap. Two more failures would have killed the job — and a killed
    job never reaches the step that emails Abram, which is the whole thing
    SLA-52 exists to prevent.

WHAT IS AT RISK IF THIS DRIFTS
  1. The count going back to "successes only". Then nothing downstream can tell.
  2. The time box being removed or raised without noticing that worst case is
     multiplied by the size of the slate — CFB Saturdays have the most images
     AND the longest runs.
  3. The verdict quietly demoting this to a warning. It is PARTIAL on purpose:
     warnings fire most days and must not move the headline, but this is a
     DELIVERY failure and it is rare.

No network, no Substack: the API is a stub and the clock is stubbed too, so the
time-box checks run instantly.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "substack_poc"))

import run_status  # noqa: E402

_TMP = tempfile.TemporaryDirectory()
run_status.STATUS_PATH = Path(_TMP.name) / "run_status.json"
run_status.reset()

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


# --------------------------------------------------------------------------
# A stub Substack API and a stub clock.
# --------------------------------------------------------------------------
class FakeApi:
    """fail_for: basenames that always fail.
       recover_after: basenames that fail once then succeed.

    Costs are modelled the way the real thing behaves and the way the
    2026-09-21 log shows: a SUCCESS returns in a couple of seconds, a FAILURE
    burns the full 30s curl timeout. Charging both the same was the first
    version of this stub, and it is what exposed the first-attempt bug -- so
    `ok_cost` is still settable for that case."""

    def __init__(self, fail_for=(), recover_after=(), ok_cost=2.0, fail_cost=30.0):
        self.fail_for = set(fail_for)
        self.recover_after = dict.fromkeys(recover_after, 0)
        self.ok_cost = ok_cost
        self.fail_cost = fail_cost
        self.calls = []

    def get_image(self, path):
        name = Path(path).name
        self.calls.append(name)
        if name in self.fail_for:
            CLOCK["t"] += self.fail_cost
            raise TimeoutError("Failed to perform, curl: (28) Operation timed out")
        if name in self.recover_after:
            self.recover_after[name] += 1
            if self.recover_after[name] == 1:
                CLOCK["t"] += self.fail_cost
                raise TimeoutError("Failed to perform, curl: (28) Operation timed out")
        CLOCK["t"] += self.ok_cost
        return {"url": f"https://substackcdn.com/{name}_800x600.png"}


CLOCK = {"t": 0.0}
SLEPT: list[float] = []


def fake_monotonic():
    return CLOCK["t"]


def fake_sleep(sec):
    SLEPT.append(sec)
    CLOCK["t"] += sec


# publish.py does `import time` INSIDE the functions, so patching the module's
# attributes reaches it.
time.monotonic = fake_monotonic
time.sleep = fake_sleep


def make_images(n: int) -> str:
    d = tempfile.mkdtemp()
    for i in range(1, n + 1):
        (Path(d) / f"box_score_sport_{i:02d}_mlb.png").write_bytes(b"\x89PNG fake")
    return d


def upload(n, fail_for=(), recover_after=(), ok_cost=2.0, fail_cost=30.0):
    CLOCK["t"] = 0.0
    SLEPT.clear()
    run_status.reset()
    d = make_images(n)
    api = FakeApi(fail_for=fail_for, recover_after=recover_after,
                  ok_cost=ok_cost, fail_cost=fail_cost)
    items, report = P.upload_box_scores(api, d)
    return items, report, api


print("=" * 72)
print("THE COUNT — a partial upload must be distinguishable from a full one")
print("=" * 72)

items, report, api = upload(13)
check("a clean run uploads everything", (len(items), report["uploaded"]), (13, 13))
check("and reports nothing failed", report["failed"], [])
check("expected is recorded even on success", report["expected"], 13)

items, report, api = upload(13, fail_for=["box_score_sport_02_mlb.png",
                                          "box_score_sport_05_mlb.png",
                                          "box_score_sport_09_mlb.png"])
check("the 2026-09-21 shape: 10 of 13 uploaded", report["uploaded"], 10)
check("and the 3 failures are NAMED, not just counted",
      sorted(report["failed"]),
      ["box_score_sport_02_mlb.png", "box_score_sport_05_mlb.png",
       "box_score_sport_09_mlb.png"])
check("expected vs uploaded is the comparison that was missing",
      (report["expected"], report["uploaded"]), (13, 10))
check("the successes still come back for the draft", len(items), 10)

recorded = run_status.load().get("substack_images") or {}
check("it reaches run_status, where every reporter can see it",
      (recorded.get("expected"), recorded.get("uploaded")), (13, 10))
check_true("with the names", len(recorded.get("failed") or []) == 3)

items, report, api = upload(0)
check("no images at all is not a failure", (report["expected"], report["failed"]),
      (0, []))

items, report, api = upload(4, recover_after=["box_score_sport_02_mlb.png"])
check("a retry that works still counts as uploaded", report["uploaded"], 4)
check("and reports no failure", report["failed"], [])
check_true("the retry really happened", api.calls.count("box_score_sport_02_mlb.png") == 2)


print()
print("=" * 72)
print("THE TIME BOX — a bad day must not run the job into its 30-minute cap")
print("=" * 72)

# Every image fails, each attempt costing a full 30s timeout. This is the
# unbounded case: before SLA-68 it was attempts x timeout x images, with nothing
# stopping it.
n = 13
items, report, api = upload(n, fail_for=[f"box_score_sport_{i:02d}_mlb.png"
                                         for i in range(1, n + 1)])
elapsed = report["seconds"]
check("every image failed", report["uploaded"], 0)
check_true(f"the phase is bounded ({elapsed:.0f}s), not attempts x images x timeout",
           elapsed <= P.MAX_UPLOAD_SECONDS + 60)

# The number that matters: what the OLD code would have done on this input.
old_worst = n * (4 * 30.0 + 2 + 4 + 6)
check_true(f"and far under the old worst case of {old_worst:.0f}s",
           elapsed < old_worst / 2)

# Worst case must leave room for the rest of the pipeline inside
# timeout-minutes: 30, given a base run of roughly 13.5 minutes.
check_true("the bound leaves headroom under the 30-minute job cap",
           P.MAX_UPLOAD_SECONDS + 60 < 25 * 60)
check_true("the retry budget is the tighter of the two",
           P.MAX_RETRY_SECONDS < P.MAX_UPLOAD_SECONDS)

# EVERY image gets its first attempt while the hard cap is untouched. This is
# the bug the first version of this file caught: the original implementation
# skipped first attempts too, so a slow-but-working big slate silently lost its
# last images.
attempted = {c for c in api.calls}
check("every image still got a first attempt", len(attempted), n)

# The same, stated as the case that would have broken: 13 images that ALL
# succeed but are slow enough that the naive budget would have cut them off.
items, report, api = upload(13, ok_cost=30.0)
check("a slow but entirely healthy run still uploads all 13",
      report["uploaded"], 13)
check("and reports no failures", report["failed"], [])

# The catastrophe stop: first attempts alone blow the hard cap. Here images ARE
# dropped, and that is correct -- but they are dropped loudly, and the job lives.
n = 40
items, report, api = upload(n, fail_for=[f"box_score_sport_{i:02d}_mlb.png"
                                         for i in range(1, n + 1)])
check_true(f"a total outage stops at the hard cap ({report['seconds']:.0f}s)",
           report["seconds"] <= P.MAX_UPLOAD_SECONDS + 60)
check("and every dropped image is still reported as failed",
      len(report["failed"]), n)
check_true("without trying all 40 (that is the point of the cap)",
           len(set(api.calls)) < n)

# Retries stop at the deadline rather than pressing on.
CLOCK["t"] = 0.0
api = FakeApi(fail_for=["x.png"])
got = P._upload_one_image(api, "x.png", attempts=3, deadline=10.0)
check("a retry past the deadline is abandoned", got, None)
check("after a single attempt", len(api.calls), 1)

CLOCK["t"] = 0.0
api = FakeApi(fail_for=["x.png"])
got = P._upload_one_image(api, "x.png", attempts=3, deadline=10_000.0)
check("with budget, it uses all its attempts", len(api.calls), 3)


print()
print("=" * 72)
print("THE VERDICT AND THE EMAIL — it must not read as SUCCESS")
print("=" * 72)

clean_stages = [{"name": s.name, "ok": True, "exit_code": 0, "critical": s.critical}
                for s in PS.PIPELINE_STAGES]

ok_status = {"stages": clean_stages, "email_sent": True,
             "substack_images": {"expected": 13, "uploaded": 13, "failed": []}}
check("a full upload is SUCCESS", PS.verdict(ok_status)[0], "success")

short = {"stages": clean_stages, "email_sent": True,
         "substack_images": {"expected": 13, "uploaded": 10,
                             "failed": ["a.png", "b.png", "c.png"]}}
level, head = PS.verdict(short)
check("a shortfall is PARTIAL, not SUCCESS", level, "partial")
check_true("and the headline says so in numbers", "3 of 13" in head)
check_true("while making clear the newsletter still went out", "shipped" in head)

# A quality warning must still NOT move the headline -- that distinction is the
# whole reason the top line is worth reading.
warned = dict(ok_status)
warned["quality"] = {"errors": [], "warnings": ["2 meme(s) rendered against a floor of 3"]}
check("a quality warning still does not downgrade anything",
      PS.verdict(warned)[0], "success")

# A failed STAGE outranks an image shortfall in the headline.
both = dict(short)
both["stages"] = [dict(s) for s in clean_stages]
for s in both["stages"]:
    if s["name"] == "Commit and push outputs":
        s["ok"], s["exit_code"] = False, 1
check_true("a failed stage outranks the image shortfall in the headline",
           "Commit and push outputs" in PS.verdict(both)[1])

panel = EN._substack_images_html(short["substack_images"] and short)
check_true("the email names the section", "SUBSTACK IMAGES" in panel)
check_true("gives the count", "3 of 13" in panel)
check_true("lists the files", "a.png" in panel and "c.png" in panel)
check_true("and says the emailed copy is fine",
           "This email is unaffected" in panel)
check("no shortfall means no section", EN._substack_images_html(ok_status), "")

many = {"substack_images": {"expected": 30, "uploaded": 0,
                            "failed": [f"f{i}.png" for i in range(20)]}}
panel = EN._substack_images_html(many)
check_true("a long failure list is truncated", "and 12 more" in panel)


print()
print("=" * 72)
print("verify_run.py warns, but does not fail the run")
print("=" * 72)

import subprocess, json, shutil  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    for name in ("verify_run.py", "run_status.py", "pipeline_status.py"):
        shutil.copy(REPO / name, t / name)
    good = ("<h1>x</h1>" + '<blockquote class="tweet">t</blockquote>' * 12
            + '<img src="https://giphy.com/a.gif">' * 6
            + '<img src="https://imgflip.com/b.jpg">' * 3)
    (t / "newsletter_draft.html").write_text(good, encoding="utf-8")
    shutil.copy(t / "newsletter_draft.html", t / "newsletter_substack.html")
    (t / "run_status.json").write_text(json.dumps({
        "date": "x", "email_sent": True,
        "substack_images": {"expected": 13, "uploaded": 10,
                            "failed": ["a.png", "b.png", "c.png"]}}), encoding="utf-8")
    r = subprocess.run([sys.executable, "-X", "utf8", "verify_run.py"], cwd=t,
                       capture_output=True, text=True, encoding="utf-8")
    check("a shippable issue with missing images still passes the gate",
          r.returncode, 0)
    check_true("but the gate says so", "never reached Substack" in r.stdout)
    check_true("and points out the emailed copy is fine",
               "emailed copy is not" in r.stdout)


print()
if failures:
    print("=" * 72)
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("=" * 72)
print("ALL CHECKS PASSED — no network, no Substack, no real waiting")
print("=" * 72)
