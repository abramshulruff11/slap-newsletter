"""SLA-67 — Pass 1's candidate pool is partitioned, and the run says when a
headliner ships without a tweet.

Two things are locked here, both offline, 0 API calls:

1. `generate_newsletter.run_pass1` must hand Pass 1 TWO disjoint tweet lists —
   `tweets` (non-video, the headliner pool) and `around_the_league_only_tweets`
   (video). The old shape was one list carrying "has_video": true markers plus a
   prompt rule; Pass 1 broke that rule on 13 of 14 runs measured 2026-09-07 ->
   09-20, and because enforcement was a pure delete, 112 headliner tweets were
   lost and 21 sections emptied to zero.

2. `verify_run._placement` must count tweets per section, so an issue whose
   tweets all land in Around the League cannot score the same as a healthy one.
   On 2026-09-20 eight tweets shipped, seven in ATL, and the run went green.

The Anthropic client is never constructed — run_pass1 is exercised only up to
the point where it builds the payload, via a stub that captures the messages and
raises to stop the call.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import verify_run  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        FAILURES.append(label)


# --------------------------------------------------------------------------
# 1. The payload partition
# --------------------------------------------------------------------------
RAW = {
    "news_headlines": [
        {"title": "Something happened", "pubDate": "2026-09-20T11:00:00Z"},
    ],
    "tweets": [
        {"account": "StatMuse", "text": "a stat", "media_kind": "text",
         "link": "https://twitter.com/StatMuse/status/1", "pubDate": "2026-09-20T01:02:03Z"},
        {"account": "TalkinBaseball_", "text": "a clip", "media_kind": "video",
         "has_video": True,
         "link": "https://twitter.com/TalkinBaseball_/status/2", "pubDate": "2026-09-20T01:02:03Z"},
        {"account": "ESPN", "text": "a gif", "media_kind": "gif", "has_video": True,
         "link": "https://twitter.com/ESPN/status/3", "pubDate": "2026-09-20T01:02:03Z"},
        {"account": "HaterReport", "text": "a photo", "media_kind": "image",
         "link": "https://twitter.com/HaterReport/status/4", "pubDate": "2026-09-20T01:02:03Z"},
    ],
}


class _Stop(Exception):
    """Raised by the stub to end run_pass1 once the payload exists."""


class _StubClient:
    """Captures the user message Pass 1 would have sent, then stops the run."""

    def __init__(self) -> None:
        self.captured: str | None = None

        outer = self

        class _Messages:
            def stream(self, **kw):
                outer.captured = kw["messages"][0]["content"]
                raise _Stop()

            def create(self, **kw):
                outer.captured = kw["messages"][0]["content"]
                raise _Stop()

        self.messages = _Messages()


def _capture_payload(raw: dict = RAW) -> dict:
    """Run run_pass1 far enough to capture the payload it builds.

    run_pass1 wraps its API call in a 3-attempt retry that catches every
    exception, so the stub's _Stop is swallowed and the call ends in a
    RuntimeError. That is fine and expected here: the payload is built once,
    before the first attempt, and the stub captures it on the way past. We only
    care that `captured` was set.
    """
    import generate_newsletter as gn

    client = _StubClient()
    try:
        gn.run_pass1(raw, [], client, game_state={})
    except Exception:
        pass

    assert client.captured, "run_pass1 never reached the API call"
    m = re.search(r"## TODAY'S RAW CONTENT\n\n(\{.*?\})\n\n## ", client.captured, re.S)
    assert m, "could not find the raw-content JSON block in the Pass 1 user message"
    return json.loads(m.group(1))


print("run_pass1 payload partition")
payload = _capture_payload()

head = payload.get("tweets", [])
atl_only = payload.get("around_the_league_only_tweets")

check(atl_only is not None,
      "payload carries an 'around_the_league_only_tweets' list")
check(len(head) == 2, f"headliner pool holds the 2 non-video tweets (got {len(head)})")
check(len(atl_only or []) == 2, f"ATL-only pool holds the 2 video tweets (got {len(atl_only or [])})")

head_accounts = {t.get("account") for t in head}
atl_accounts = {t.get("account") for t in (atl_only or [])}
check(head_accounts == {"StatMuse", "HaterReport"},
      f"headliner pool is exactly the non-video accounts (got {sorted(head_accounts)})")
check(atl_accounts == {"TalkinBaseball_", "ESPN"},
      f"ATL-only pool is exactly the video accounts (got {sorted(atl_accounts)})")
check(not (head_accounts & atl_accounts), "the two pools are disjoint")

# A looping GIF counts as video today (fetch_content: kind in ("video","gif")).
# If that policy is ever revisited, this is the line that should fail first.
check("ESPN" in atl_accounts,
      "a media_kind='gif' tweet is treated as video and kept out of the headliner pool")

check(all("has_video" not in t for t in head + (atl_only or [])),
      "no 'has_video' marker survives — the pool a tweet is in carries that fact")
check(all("media_kind" not in t for t in head + (atl_only or [])),
      "media_kind is still stripped from the model payload")
check(all(len(t.get("pubDate", "")) <= 10 for t in head + (atl_only or [])),
      "pubDate is still truncated to date-only in both pools")

# The total must be conserved: partitioning may not silently drop a tweet.
check(len(head) + len(atl_only or []) == len(RAW["tweets"]),
      "every raw tweet lands in exactly one pool")


print("\nempty and degraded inputs")
for label, raw in (("no tweets at all", {"news_headlines": [], "tweets": []}),
                   ("tweets key missing", {"news_headlines": []})):
    body = _capture_payload(raw)
    check(body.get("tweets") == [] and body.get("around_the_league_only_tweets") == [],
          f"{label}: both pools are empty lists, not missing keys")


# --------------------------------------------------------------------------
# 2. The placement gate
# --------------------------------------------------------------------------
def _issue(lead_n: int, supporting: list, atl_n: int) -> str:
    tw = '<p class="tweet-url">https://twitter.com/a/status/9</p>'
    out = ["<h1>The Lead</h1>", tw * lead_n]
    for i, n in enumerate(supporting):
        out += [f"<h2>Supporting {i}</h2>", tw * n]
    out += ["<h2>Around the League</h2>", tw * atl_n]
    out += ["<h2>Box Scores</h2>"]
    return "\n".join(out)


print("\nverify_run._placement")
p = verify_run._placement(_issue(3, [2, 1], 8))
check(p["lead"] == 3, f"lead counted (got {p['lead']})")
check(p["supporting"] == [2, 1], f"supporting counted per story (got {p['supporting']})")
check(p["atl"] == 8, f"ATL counted apart (got {p['atl']})")
check(p["headliner"] == 6, f"headliner = lead + supporting (got {p['headliner']})")

# The shape that shipped on 2026-09-20: one body tweet, seven in ATL.
p = verify_run._placement(_issue(0, [0, 0, 0, 1], 7))
check(p["headliner"] == 1 and p["atl"] == 7,
      "the 2026-09-20 shape reads as 1 in the body / 7 in ATL")
check(p["lead"] == 0, "a tweet-free lead reads as zero, not as ATL's count")
check(0 < p["headliner"] < verify_run.WANT_HEADLINER_TWEETS,
      "that shape trips the body-too-thin warning band")

# Box Scores must never contribute, and must not swallow ATL's count.
p = verify_run._placement(_issue(1, [1], 4))
check(p["headliner"] == 2 and p["atl"] == 4, "Box Scores contributes nothing")

# The hard-failure shape: every tweet in ATL.
p = verify_run._placement(_issue(0, [0, 0], 9))
check(p["headliner"] == 0 and p["atl"] == 9,
      "all-tweets-in-ATL reads as headliner 0 (the hard failure)")

# A healthy issue must stay quiet.
p = verify_run._placement(_issue(5, [3, 2, 1], 8))
check(p["headliner"] >= verify_run.WANT_HEADLINER_TWEETS and p["lead"] > 0,
      "a healthy issue trips neither the warning nor the failure")

# Blockquote-shaped tweets (the published file) must count too.
bq = ('<h1>Lead</h1><blockquote class="tweet">x</blockquote>'
      '<h2>Around the League</h2><blockquote class="tweet">y</blockquote>')
p = verify_run._placement(bq)
check(p["lead"] == 1 and p["atl"] == 1, "blockquote-shaped tweets are counted")

# A file with no headings must not masquerade as "every tweet is in ATL" — that
# would fire the hard failure for entirely the wrong reason.
p = verify_run._placement('<p class="tweet-url">x</p>' * 9)
check(p["sections"] == 0,
      "a draft with no h1/h2 reports zero sections, so the hard failure is skipped")
check(verify_run._placement(_issue(2, [1], 8))["sections"] == 2,
      "sections counts the lead + each supporting story, not ATL or Box Scores")


print()
if FAILURES:
    print(f"FAILED ({len(FAILURES)}):")
    for f in FAILURES:
        print(f"  - {f}")
    raise SystemExit(1)
print("All video-pool partition checks passed.")
