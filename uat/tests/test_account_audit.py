"""
Run:  python -X utf8 uat/tests/test_account_audit.py

Locks the deterministic Pass 5 audits that replaced two self-graded checks:

  - account/insider caps, which the editor LLM got backwards on 2026-08-27
    (missed @TomPelissero 4x and @ESPN 3x, flagged @SleeperHQ which appeared
    once)
  - §2.2 pure-update rejection, which was a number the model wrote about
    itself and nothing verified

No API calls, no network.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "uat"))

import generate_newsletter_uat as G  # noqa: E402


def tweet(handle: str, text: str) -> str:
    return (f'<blockquote class="tweet">\n<strong>@{handle}</strong><br/>\n'
            f'{text}<br/>\n'
            f'<a href="https://twitter.com/{handle}/status/123">View tweet</a>\n'
            f'</blockquote>')


# @ESPN 3x and @TomPelissero 2x in headliners; @ESPN 2 more in ATL (uncapped).
# @ghetto_gronk 2x is within the normal cap and must stay clean.
DRAFT = f"""
<h1>Lead Story</h1>
<p>The Niners owner had a long night in Ohio and the league noticed.</p>
{tweet("ESPN", "Roger Goodell said the league is reviewing the matter closely today.")}
{tweet("ghetto_gronk", "bro really wore a Yankees fit to a sting operation")}
{tweet("TomPelissero", "The NFL sent a memo to clubs outlining the new discipline rules.")}
<h2>Second Story</h2>
<p>Two conferences closed the loophole in a single afternoon.</p>
{tweet("ESPN", "Big 12 athletic directors have unanimously agreed to bar returning pros.")}
{tweet("ghetto_gronk", "the transfer portal has entered its final form")}
{tweet("ShamsCharania", "BREAKING: Jonathan Kuminga has agreed to a two-year deal.")}
<h2>Third Story</h2>
<p>The fine system got rewritten and nobody argued.</p>
{tweet("ESPN", "The NFL and NFLPA agreed to overhaul the fines system on Wednesday.")}
{tweet("TomPelissero", "A joint statement from the league and the union adds detail.")}
<h2>Around the League</h2>
{tweet("ESPN", "A completely different wire item that must never be flagged.")}
{tweet("ESPN", "Another ATL wire item, also never flagged.")}
"""


def check(label, got, want):
    status = "ok " if got == want else "FAIL"
    print(f"  [{status}] {label}: {got!r}")
    if got != want:
        raise AssertionError(f"{label}: expected {want!r}, got {got!r}")


print("=" * 66)
print("HEADLINER COUNTING (ATL excluded)")
print("=" * 66)
counts = G.count_headliner_accounts(DRAFT)
check("@ESPN headliner count (2 more sit in ATL)", counts.get("@ESPN"), 3)
check("@TomPelissero headliner count", counts.get("@TomPelissero"), 2)
check("@ghetto_gronk headliner count", counts.get("@ghetto_gronk"), 2)
check("total headliner tweets", sum(counts.values()), 8)

print()
print("=" * 66)
print("EFFECTIVE CAPS")
print("=" * 66)
check("insider @ESPN cap", G.effective_cap("@ESPN"), 1)
check("insider @TomPelissero cap", G.effective_cap("@TomPelissero"), 1)
check("normal @ghetto_gronk cap", G.effective_cap("@ghetto_gronk"), 2)

print()
print("=" * 66)
print("FLAGGING")
print("=" * 66)
out = G._audit_account_diversity(DRAFT)
out = G._audit_redundant_tweets(out)

check("@ESPN flagged", "INSIDER CAP — @ESPN appears 3x" in out, True)
check("@TomPelissero flagged", "INSIDER CAP — @TomPelissero appears 2x" in out, True)
check("@ghetto_gronk NOT flagged (2x, within cap)",
      "@ghetto_gronk appears" in out, False)
check("BREAKING tweet flagged §2.2",
      "REDUNDANT TWEET (§2.2) — @ShamsCharania" in out, True)

atl = out.lower().index("around the league")
check("no flag anywhere in ATL",
      any(k in out[atl:] for k in ("ACCOUNT CAP", "INSIDER CAP", "REDUNDANT")), False)
check("no tweets destroyed", out.count('class="tweet"'), DRAFT.count('class="tweet"'))

print()
print("=" * 66)
print("REGRESSION: the 2026-08-27 editor failure")
print("=" * 66)
# Must sit BEFORE the ATL heading — counting stops there by design.
extra = ("\n<h2>Extra</h2>\n<p>A section with one lonely tweet in it.</p>\n"
         + tweet("SleeperHQ", "one single tweet here") + "\n")
solo = DRAFT.replace("<h2>Around the League</h2>", extra + "<h2>Around the League</h2>")
c = G.count_headliner_accounts(solo)
check("@SleeperHQ counted once, not 3x", c.get("@SleeperHQ"), 1)
check("@SleeperHQ not flagged",
      "@SleeperHQ appears" in G._audit_account_diversity(solo), False)



print()
print("=" * 66)
print("TWEET BUDGET (§2.3)")
print("=" * 66)


def tw(handle, text="a normal tweet with several distinct content words here"):
    return {"url": f"https://twitter.com/{handle}/status/{abs(hash(text+handle))%10**9}",
            "account": handle, "text": text}


def story(headline, tweets):
    return {"headline": headline, "tweets": list(tweets),
            "beats": [{"angle": "a", "landing": "b", "media": list(tweets)}]}


PLAN = {
    "lead_story": story("Lead", [tw("ESPN"), tw("ghetto_gronk", "a joke about the thing"),
                                 tw("ESPN", "second wire item"), tw("PFTCommenter", "bit"),
                                 tw("StatMuse", "a stat line worth reading"),
                                 tw("NBAMemes", "meme text"), tw("Ihartitz", "more")]),
    "supporting_stories": [
        # Both tweets from one insider — must NOT be emptied (2026-08-27 bug)
        story("Fines", [tw("TomPelissero", "memo to clubs"),
                        tw("TomPelissero", "joint statement adds detail")]),
        # First is pure-update; the second must survive (quota-refund bug)
        story("Kuminga", [tw("ShamsCharania", "BREAKING: Kuminga has agreed to a deal"),
                          tw("ShamsCharania", "Kuminga chose Minnesota over three other teams"),
                          tw("ClutchPoints", "reaction quote here")]),
    ],
    "around_the_league": {"tweets": [tw("A%d" % i, "atl item %d" % i) for i in range(12)]},
}

rep = G.enforce_tweet_budget(PLAN)   # mutates PLAN in place, by design

sup = PLAN["supporting_stories"]
check("total trimmed to ceiling", rep["after"] <= G.TWEET_CEILING, True)
check("no section emptied", all(len(s["tweets"]) >= 1 for s in sup), True)
check("insider-only story keeps one tweet", len(sup[0]["tweets"]), 1)
check("pure-update dropped, real scoop kept", len(sup[1]["tweets"]) >= 2, True)
check("BREAKING tweet gone",
      any("BREAKING" in (t["text"] or "") for t in sup[1]["tweets"]), False)
check("ATL capped", len(PLAN["around_the_league"]["tweets"]), G.ATL_MAX)

# beats must be pruned in lockstep or Pass 2 (locked to beats) still uses them
leftover = {t["url"] for s in [PLAN["lead_story"]] + sup for t in s["tweets"]}
beat_urls = {m["url"] for s in [PLAN["lead_story"]] + sup
             for b in s["beats"] for m in b["media"]}
check("beat media pruned in lockstep (no orphans)", beat_urls - leftover, set())

print()
print("=" * 66)
print("ALL CHECKS PASSED — 0 API calls")
print("=" * 66)
