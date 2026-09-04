"""
Deterministic plan and draft audits for SLAP.

Shared by generate_newsletter.py (production) and
uat/generate_newsletter_uat.py. It lives at the repo root, next to
meme_library.py, for one reason: prompts drifted between prod and UAT for
months because promotion was manual copying, and duplicating THIS code would
reproduce that failure somewhere a prompt diff would never show it. One copy,
two callers — a threshold change lands in both or neither.

Everything here is arithmetic. No API calls, no network, no LLM judgement.
That is the point: each function replaced a rule that was either self-reported
by the model or self-graded by it, and that consequently was never enforced.

  Account caps (Pass 5, on draft HTML)
      count_headliner_accounts, effective_cap, audit_account_diversity
      Replaced editor_prompt.txt CHECK 3, which on 2026-08-27 missed
      @TomPelissero (4x) and @ESPN (3x) and flagged @SleeperHQ, which appeared
      exactly once. An LLM cannot count blockquotes across a long document.

  Redundancy / §2.2 (Pass 5, on draft HTML)
      audit_redundancy, audit_redundant_tweets
      Replaced a "filter" that only printed filter_stats.update_tweets_rejected
      — a number the model wrote about itself. It claimed 6 rejections while
      shipping a "BREAKING:" tweet.

  Plan trimming / §2.3-2.4 (between Pass 1 and Pass 2, on the plan JSON)
      enforce_tweet_budget, audit_media_seeds, backfill_gif_seeds
      Pass 1 had no tweet ceiling in its prompt and took 35 against a 20-24
      target, which held the GIF/meme share at 27%.

Thresholds here are calibrated against real issues, not guessed; see the
comment above each. Tests: uat/tests/test_account_audit.py.
"""

from __future__ import annotations

import re


def _normalize_tweet_url(url: str) -> str:
    """Normalize a tweet URL for reliable comparison across sources.

    Copied verbatim from the runners so this module has no import back into
    either caller. Must stay byte-identical to theirs — the tweet budget
    matches plan entries against beat media by this key, and a divergence
    would silently orphan beat media instead of pruning it.
    """
    url = url.strip()
    url = url.replace("nitter.net", "twitter.com")
    url = re.sub(r'#m$', '', url)
    url = re.sub(r'(/status)=(\d)', r'\1/\2', url)  # status= -> status/
    return url.lower()


# --- §2.2, as arithmetic ------------------------------------------------------
# pass1_story_selector.txt tells the model to reject tweets that only restate
# the news ("REJECT: 'BREAKING: The Jets have traded...' — that's the headline,
# not a take on it") and to report the count in
# filter_stats.update_tweets_rejected. That count is self-reported and nothing
# verifies it: on 2026-08-27 the model claimed 6 rejections while shipping a
# Schefter tweet whose text restated the lead's own prose almost verbatim.
# This measures the overlap instead of asking.

_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "have", "has", "had",
    "was", "were", "will", "would", "been", "being", "are", "but", "not", "you",
    "his", "her", "its", "their", "they", "them", "who", "what", "when", "after",
    "before", "into", "over", "than", "then", "there", "here", "about", "just",
    "more", "most", "some", "such", "only", "also", "very", "can", "could",
    "should", "did", "does", "done", "get", "got", "out", "off", "now", "one",
    "two", "all", "any", "how", "why", "our", "your", "him", "she", "had",
}

# A tweet whose text is mostly words the section already used is a restatement.
# 0.72 is deliberately conservative: measured against the 2026-08-27 issue the
# scores ran 0.05-0.67 for genuine content and 1.00 for the one pure-update
# tweet, so this catches that class with no false positives.
REDUNDANCY_THRESHOLD = 0.72
MIN_CONTENT_WORDS = 6

# Word overlap does NOT catch the other half of the problem. A wire report
# ("Commissioner Roger Goodell said the NFL is looking into whether...") scored
# only 0.42 on 2026-08-27 because it is full of proper nouns the prose never
# repeats — yet it is exactly the tweet the reader has already seen as a
# headline. pass1_story_selector.txt covers it with a rule word overlap cannot
# express: an insider breaking-news tweet "may anchor a story ONCE".
# These accounts break news rather than comment on it, so the cap is 1.
INSIDER_WIRE_ACCOUNTS = {
    "@adamschefter", "@shamscharania", "@rapsheet", "@tompelissero",
    "@albertbreer", "@espn", "@wojespn", "@jayglazer", "@mortreport",
    "@ianrapoport", "@espnnfl", "@espnnba", "@mlb", "@nfl",
}
INSIDER_HEADLINER_CAP = 1

_PURE_UPDATE_RE = re.compile(
    r'^\s*(breaking|official|update|report|confirmed|just in|final)\b\s*:?',
    re.IGNORECASE,
)
_SCORELINE_RE = re.compile(
    r'^\s*(final|f/\d+)?\s*:?\s*[A-Z][\w .\'-]{2,25}\s+\d{1,3}\s*[,-]\s*'
    r'[A-Z][\w .\'-]{2,25}\s+\d{1,3}\s*$',
    re.IGNORECASE,
)


def _words(text: str) -> set:
    return {
        w for w in re.findall(r"[a-z][a-z'-]{2,}", text.lower())
        if len(w) > 3 and w not in _STOPWORDS
    }


def _tweet_body(block: str) -> str:
    """The tweet's own text: handle, links and the 'View tweet' anchor removed."""
    body = re.sub(r'<strong>.*?</strong>', ' ', block, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r'<a\b.*?</a>', ' ', body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r'<[^>]+>', ' ', body)
    body = re.sub(r'https?://\S+', ' ', body)
    return re.sub(r'\s+', ' ', body).strip()


def audit_redundancy(html: str) -> list:
    """
    Per headliner tweet, how much of it the section's own prose already said.

    Returns [{account, ratio, reason, shared, total, text}], most redundant
    first. Pure read-only — used by the flagger and by tests.
    """
    findings = []
    for part in re.split(r'(?=<h[12][\s>])', html, flags=re.IGNORECASE):
        heading = re.match(r'<h[12][^>]*>(.*?)</h[12]>', part, re.IGNORECASE | re.DOTALL)
        if heading:
            htext = re.sub(r'<[^>]+>', '', heading.group(1)).lower()
            if "around the league" in htext:
                break                      # ATL carries no prose to duplicate
        else:
            continue

        prose = " ".join(re.findall(r'<p[^>]*>(.*?)</p>', part, re.DOTALL | re.IGNORECASE))
        context = _words(re.sub(r'<[^>]+>', ' ', prose) + " " + heading.group(1))

        for block in re.findall(
            r'<blockquote[^>]*class="tweet"[^>]*>.*?</blockquote>',
            part, re.DOTALL | re.IGNORECASE
        ):
            text = _tweet_body(block)
            tw = _words(text)
            handle = _tweet_handle(block) or "@?"
            if _PURE_UPDATE_RE.match(text) or _SCORELINE_RE.match(text):
                findings.append({"account": handle, "ratio": 1.0,
                                 "reason": "pure-update shape", "shared": len(tw & context),
                                 "total": len(tw), "text": text[:90]})
                continue
            if len(tw) < MIN_CONTENT_WORDS or not context:
                continue
            ratio = len(tw & context) / len(tw)
            if ratio >= REDUNDANCY_THRESHOLD:
                findings.append({"account": handle, "ratio": round(ratio, 2),
                                 "reason": "restates section prose", "shared": len(tw & context),
                                 "total": len(tw), "text": text[:90]})
    return sorted(findings, key=lambda f: -f["ratio"])


def audit_redundant_tweets(html: str) -> str:
    """Flag headliner tweets the surrounding prose already said."""
    findings = audit_redundancy(html)
    if not findings:
        print("  ✓ No redundant headliner tweets detected (§2.2)")
        return html

    for f in findings:
        print(f"  ⚠ §2.2 {f['account']} {f['reason']} "
              f"({f['shared']}/{f['total']} words already in prose): {f['text'][:60]}…")

    flagged = {f["account"]: f for f in findings}
    marked: set = set()

    def mark(m: re.Match) -> str:
        block = m.group(0)
        handle = _tweet_handle(block)
        f = flagged.get(handle)
        if not f or handle in marked:
            return block
        if _tweet_body(block)[:90] != f["text"]:
            return block
        marked.add(handle)
        return (
            block
            + f'\n<!-- EDITOR FLAG: REDUNDANT TWEET (§2.2) — {handle} '
            f'{f["reason"]}; {f["shared"]} of {f["total"]} content words already '
            f'appear in this section. Cut the tweet or cut the prose that '
            f'duplicates it. -->'
        )

    split = re.search(r'<h2[^>]*>\s*(?:<[^>]+>\s*)*around the league', html, re.IGNORECASE)
    cut = split.start() if split else len(html)
    head, tail = html[:cut], html[cut:]
    head = re.sub(r'<blockquote[^>]*class="tweet"[^>]*>.*?</blockquote>',
                  mark, head, flags=re.DOTALL | re.IGNORECASE)
    print(f"  ⚠ {len(marked)} redundancy flag(s) inserted")
    return head + tail


# Editor CHECK 3 asks the model to count @handles across the whole draft and
# flag any that appear 3+ times in the headliners. On 2026-08-27 it missed
# @TomPelissero (4x) and @ESPN (3x) and instead flagged @SleeperHQ, which
# appeared ONCE. An LLM cannot reliably count blockquotes across a long
# document, so the count moves here — same policy, same headliner-only scope
# as CHECK 3, but arithmetic instead of judgement.
HEADLINER_ACCOUNT_CAP = 2

_TWEET_HANDLE_RE = re.compile(
    r'(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})/status', re.IGNORECASE
)


def _tweet_handle(block: str) -> str | None:
    """The @handle a tweet blockquote belongs to, from its status URL."""
    m = _TWEET_HANDLE_RE.search(block)
    return f"@{m.group(1)}" if m else None


def count_headliner_accounts(html: str) -> dict:
    """
    Count tweets per @handle in the HEADLINER sections only.

    Around the League is a highlight reel with no per-account cap (see
    editor_prompt.txt CHECK 3 and the unified account-cap policy), so every
    section from <h2>Around the League</h2> onward is excluded.
    """
    counts: dict = {}
    for part in re.split(r'(?=<h[12][\s>])', html, flags=re.IGNORECASE):
        heading = re.match(r'<h[12][^>]*>(.*?)</h[12]>', part, re.IGNORECASE | re.DOTALL)
        if heading:
            text = re.sub(r'<[^>]+>', '', heading.group(1)).lower()
            if "around the league" in text:
                break          # ATL and everything after it is uncapped
        for block in re.findall(
            r'<blockquote[^>]*class="tweet"[^>]*>.*?</blockquote>',
            part, re.DOTALL | re.IGNORECASE
        ):
            handle = _tweet_handle(block)
            if handle:
                counts[handle] = counts.get(handle, 0) + 1
    return counts


# --- §2.1 Video tweets: Around the League only --------------------------------
# A video embed renders as a dead grey box in email and stops the read, so a
# video tweet has no place inside a written story. In Around the League it is
# fine: that section is quick hitters with no prose to interrupt, and a reader
# who wants the play can tap it.
#
# fetch_content.py tags every tweet with has_video from the Nitter RSS
# description. Pass 1's prompt states the rule; this enforces it, because a
# prompt line with no check is not a rule (see CLAUDE.md). Both places a tweet
# can hide are pruned: story["tweets"] and every beat's media[] — Pass 2 is
# locked to the beats, so pruning one without the other just moves the problem.

_STATUS_ID_RE = re.compile(r'/status/(\d+)')


def status_id(url: str) -> str:
    """The numeric status ID in a tweet URL — the only invariant across
    nitter/twitter/x hosts, #m suffixes and query strings. "" when absent."""
    m = _STATUS_ID_RE.search(url or "")
    return m.group(1) if m else ""


def video_status_ids(raw: dict) -> set:
    """Status IDs of every video-carrying tweet in today's raw content."""
    out = set()
    for t in (raw or {}).get("tweets", []) or []:
        if isinstance(t, dict) and t.get("has_video"):
            sid = status_id(t.get("link") or t.get("url") or "")
            if sid:
                out.add(sid)
    return out


def enforce_video_policy(plan: dict, video_ids: set) -> dict:
    """Strip video tweets from the headliners. ATL keeps them. Mutates `plan`.

    Returns {"dropped": [(section, @account)], "atl_kept": n, "sections": {}}.
    A story emptied by this is not a failure: an empty media[] is exactly the
    case the beats system covers with a GIF, a meme, or prose.
    """
    report: dict = {"dropped": [], "atl_kept": 0, "sections": {}}
    if not video_ids:
        return report

    for label, story, _floor in _plan_sections(plan):
        tweets = story.get("tweets", []) or []
        kept = []
        for t in tweets:
            sid = status_id(t.get("url", "")) if isinstance(t, dict) else ""
            if sid and sid in video_ids:
                report["dropped"].append(
                    (label, "@" + str(t.get("account", "?")).lstrip("@"))
                )
            else:
                kept.append(t)
        if len(kept) != len(tweets):
            report["sections"][label] = (len(tweets), len(kept))
        story["tweets"] = kept

        for beat in story.get("beats", []) or []:
            if isinstance(beat, dict) and isinstance(beat.get("media"), list):
                beat["media"] = [
                    m for m in beat["media"]
                    if not (isinstance(m, dict)
                            and status_id(m.get("url", "")) in video_ids)
                ]

    atl = plan.get("around_the_league", {})
    atl_tweets = (atl.get("tweets", []) if isinstance(atl, dict) else atl) or []
    report["atl_kept"] = sum(
        1 for t in atl_tweets
        if isinstance(t, dict) and status_id(t.get("url", "")) in video_ids
    )
    return report


# --- Media seed floor ---------------------------------------------------------
# Pass 1 seeded 4 memes on one run and 2 on the next from the SAME input, which
# is what left the media share at 33%. The prompt now states a floor; this
# verifies it, because a prompt rule with no check is how the account cap and
# §2.2 both ended up unenforced.
#
# GIFs can be backfilled safely: a Tier 1 concept IS an emotional beat, and the
# plan already carries one per beat in `landing`. Memes cannot — they need a
# named subject, and inventing one to satisfy a count produces exactly the dead
# meme the subject gate exists to prevent. Meme shortfalls are reported, never
# fabricated.
MIN_GIF_SEEDS = 3
MIN_MEME_SEEDS = 3


def _seeded_stories(plan: dict) -> list:
    out = [plan.get("lead_story", {}) or {}]
    out += [s or {} for s in (plan.get("supporting_stories") or [])]
    return out


def audit_media_seeds(plan: dict) -> dict:
    """Count gif/meme seeds against the floor. Read-only."""
    stories = _seeded_stories(plan)
    gifs = [s for s in stories if (s.get("gif_concept") or "").strip()]
    memes = [s for s in stories
             if (s.get("meme_concept") or "").strip()
             and (s.get("meme_template") or "").strip()]
    return {
        "stories": len(stories),
        "gif": len(gifs), "meme": len(memes),
        "gif_short": max(0, MIN_GIF_SEEDS - len(gifs)),
        "meme_short": max(0, MIN_MEME_SEEDS - len(memes)),
        "both": sum(1 for s in stories
                    if (s.get("gif_concept") or "").strip()
                    and (s.get("meme_template") or "").strip()),
    }


def backfill_gif_seeds(plan: dict) -> list:
    """
    Fill empty gif_concepts from the story's own beat landings, up to the floor.

    Not fabrication: `landing` is Pass 1's own statement of what the reader
    should feel at that beat, which is precisely a Tier 1 GIF concept.
    """
    filled = []
    for story in _seeded_stories(plan):
        rep = audit_media_seeds(plan)
        if rep["gif_short"] <= 0:
            break
        if (story.get("gif_concept") or "").strip():
            continue
        landing = ""
        for beat in (story.get("beats") or []):
            if isinstance(beat, dict) and (beat.get("landing") or "").strip():
                landing = beat["landing"].strip()
                break
        if not landing:
            continue
        story["gif_concept"] = landing
        story["gif_tier"] = 1          # derived beats are always library-generic
        filled.append((story.get("headline", "?")[:40], landing[:60]))
    return filled


# --- Tweet budget -------------------------------------------------------------
# Nothing capped the total tweet count. pass1_story_selector.txt never states a
# ceiling, so Pass 1 took 35 on 2026-08-27 against a 20-24 target — which is
# what dragged the GIF/meme share down to 27% against a 50% target. Every tweet
# Pass 1 hands over is one Pass 2 can spend instead of a GIF.
#
# This trims the PLAN, before Pass 2 writes, so the excess never reaches the
# draft. Tweets live in both story["tweets"] and story["beats"][i]["media"] and
# Pass 2 is locked to the beats, so both have to be pruned together.
TWEET_CEILING = 24          # top of run_uat.TWEET_TARGET
ATL_MAX = 8                 # ATL is a highlight reel, but it is not a dumping ground
MIN_TWEETS_LEAD = 3
MIN_TWEETS_SUPPORTING = 1   # a supporting story can carry a single tweet


def _norm_key(t: dict) -> str:
    return _normalize_tweet_url(t.get("url", "")) or (t.get("url") or "")


def _plan_sections(plan: dict) -> list:
    """[(label, story_dict, floor)] for headliner sections, in order."""
    out = [("lead", plan.get("lead_story", {}) or {}, MIN_TWEETS_LEAD)]
    for i, s in enumerate(plan.get("supporting_stories", []) or []):
        out.append((f"supporting{i}", s or {}, MIN_TWEETS_SUPPORTING))
    return out


def _drop_from_story(story: dict, keys: set) -> int:
    """Remove tweets by normalized URL from both tweets[] and every beat's media[]."""
    removed = 0
    kept = []
    for t in story.get("tweets", []) or []:
        if isinstance(t, dict) and _norm_key(t) in keys:
            removed += 1
        else:
            kept.append(t)
    story["tweets"] = kept
    for beat in story.get("beats", []) or []:
        if isinstance(beat, dict) and isinstance(beat.get("media"), list):
            beat["media"] = [m for m in beat["media"]
                             if not (isinstance(m, dict) and _norm_key(m) in keys)]
    return removed


def enforce_tweet_budget(plan: dict, ceiling: int = TWEET_CEILING) -> dict:
    """
    Trim the story plan to the tweet budget, dropping the least valuable first.

    Order:
      1. insider/wire tweets past their cap of 1 (keep the anchor, drop the rest)
      2. any account past the normal cap of 2
      3. pure-update shapes ("BREAKING: ...", scorelines)
      4. if still over, the trailing tweet of the biggest section, respecting
         per-section floors

    Returns a report dict; mutates `plan` in place.
    """
    sections = _plan_sections(plan)
    atl = plan.get("around_the_league", {})
    atl_tweets = (atl.get("tweets", []) if isinstance(atl, dict) else atl) or []

    before_head = sum(len(s.get("tweets", []) or []) for _, s, _ in sections)
    before_atl = len(atl_tweets)
    report = {"before": before_head + before_atl, "dropped": [], "sections": {}}

    # --- ATL first: it is uncapped per-account but not unlimited in size ---
    if len(atl_tweets) > ATL_MAX:
        for t in atl_tweets[ATL_MAX:]:
            report["dropped"].append(("atl-overflow", "@" + str(t.get("account", "?")).lstrip("@")))
        atl_tweets = atl_tweets[:ATL_MAX]
        if isinstance(atl, dict):
            atl["tweets"] = atl_tweets
        else:
            plan["around_the_league"] = atl_tweets

    # --- Rules 1-3, headliners only ---
    # Insider caps are PER STORY, not per issue: pass1_story_selector.txt says an
    # insider tweet "may anchor A STORY once". Applying it per issue emptied the
    # NFL-fines story entirely on 2026-08-27 (both its tweets were Pelissero's),
    # which is worse than the problem being solved. A section is never taken
    # below its floor by any rule.
    seen_issue: dict = {}
    for label, story, floor in sections:
        tweets = [t for t in (story.get("tweets", []) or []) if isinstance(t, dict)]
        seen_section: dict = {}
        drop: set = set()

        for t in tweets:
            if len(tweets) - len(drop) <= floor:
                break                      # floor reached — stop cutting this section
            acct = "@" + str(t.get("account", "?")).lstrip("@")
            low = acct.lower()
            seen_section[acct] = seen_section.get(acct, 0) + 1
            seen_issue[acct] = seen_issue.get(acct, 0) + 1
            text = (t.get("text") or "").strip()

            if low in INSIDER_WIRE_ACCOUNTS and seen_section[acct] > INSIDER_HEADLINER_CAP:
                drop.add(_norm_key(t))
                report["dropped"].append(("insider-cap/story", acct))
            elif seen_issue[acct] > HEADLINER_ACCOUNT_CAP:
                drop.add(_norm_key(t))
                report["dropped"].append(("account-cap/issue", acct))
            elif _PURE_UPDATE_RE.match(text) or _SCORELINE_RE.match(text):
                drop.add(_norm_key(t))
                report["dropped"].append(("pure-update", acct))
            else:
                continue
            # A dropped tweet consumes no quota — otherwise cutting the first
            # tweet of an account makes the next one look "over cap" and takes
            # that too, which zeroed @ShamsCharania out of its own scoop.
            seen_issue[acct] -= 1
            seen_section[acct] -= 1

        if drop:
            _drop_from_story(story, drop)

    # --- Rule 4: still over budget, shave the biggest sections ---
    def total() -> int:
        return (sum(len(s.get("tweets", []) or []) for _, s, _ in sections)
                + len(atl_tweets))

    guard = 0
    while total() > ceiling and guard < 200:
        guard += 1
        candidates = [(len(s.get("tweets", []) or []), label, s, floor)
                      for label, s, floor in sections
                      if len(s.get("tweets", []) or []) > floor]
        if not candidates:
            break
        candidates.sort(key=lambda c: -c[0])
        _, label, story, _floor = candidates[0]
        victim = (story.get("tweets") or [])[-1]
        acct = "@" + str(victim.get("account", "?")).lstrip("@")
        _drop_from_story(story, {_norm_key(victim)})
        report["dropped"].append((f"over-budget:{label}", acct))

    for label, story, _f in sections:
        report["sections"][label] = len(story.get("tweets", []) or [])
    report["sections"]["atl"] = len(atl_tweets)
    report["after"] = total()
    return report


def effective_cap(account: str) -> int:
    """Insider/wire accounts may anchor ONCE; everyone else gets the normal cap."""
    return (INSIDER_HEADLINER_CAP if account.lower() in INSIDER_WIRE_ACCOUNTS
            else HEADLINER_ACCOUNT_CAP)


def audit_account_diversity(html: str) -> str:
    """Flag over-cap headliner accounts, deterministically, after their last use."""
    counts = count_headliner_accounts(html)
    if not counts:
        print("  ⚠ No headliner tweets found — skipping account audit")
        return html

    over = {a: n for a, n in counts.items() if n > effective_cap(a)}
    total = sum(counts.values())
    if not over:
        print(f"  ✓ Account diversity OK — {total} headliner tweet(s), "
              f"{len(counts)} account(s), all within cap")
        return html

    for account, n in sorted(over.items(), key=lambda kv: -kv[1]):
        kind = "insider/wire" if account.lower() in INSIDER_WIRE_ACCOUNTS else "account"
        print(f"  ⚠ {account} appears {n}x in headliners "
              f"({kind} cap {effective_cap(account)})")

    # Flag each offender after its LAST headliner occurrence, so the editor sees
    # the full run before being asked to cut one.
    seen: dict = {}
    flagged: set = set()

    def mark(m: re.Match) -> str:
        block = m.group(0)
        handle = _tweet_handle(block)
        if handle not in over:
            return block
        seen[handle] = seen.get(handle, 0) + 1
        if seen[handle] == over[handle] and handle not in flagged:
            flagged.add(handle)
            cap = effective_cap(handle)
            kind = ("INSIDER CAP" if handle.lower() in INSIDER_WIRE_ACCOUNTS
                    else "ACCOUNT CAP")
            why = (" An insider/wire account may anchor ONE story per issue; "
                   "the rest restate news the reader already saw."
                   if kind == "INSIDER CAP" else "")
            return (
                block
                + f'\n<!-- EDITOR FLAG: {kind} — {handle} appears '
                f'{over[handle]}x in headliners (cap {cap}).{why} '
                f'Remove or replace {over[handle] - cap} of them. -->'
            )
        return block

    # Only rewrite the headliner region; ATL must stay untouched.
    split = re.search(r'<h2[^>]*>\s*(?:<[^>]+>\s*)*around the league',
                      html, re.IGNORECASE)
    cut = split.start() if split else len(html)
    head, tail = html[:cut], html[cut:]
    head = re.sub(r'<blockquote[^>]*class="tweet"[^>]*>.*?</blockquote>',
                  mark, head, flags=re.DOTALL | re.IGNORECASE)
    print(f"  ⚠ {len(flagged)} account-cap flag(s) inserted")
    return head + tail
