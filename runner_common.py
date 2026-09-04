"""
Runner code shared by generate_newsletter.py and uat/generate_newsletter_uat.py.

These functions were byte-identical copies in both runners. That duplication is
not harmless: on 2026-09-01 two separate changes reached one runner and not the
other, and both shipped.

  - Pass 1's max_tokens went 16,384 -> 32,768 in production without the
    streaming call UAT already had. The run died client-side and shipped
    nothing.
  - The GIF library prompts were promoted, but production got no consumer for
    the data-library-category placeholders they taught Pass 2 to emit. Seven
    GIFs shipped as invisible empty divs.

promote.py diffs prompts only, and the test suites stub the Anthropic client,
so runner-code drift was invisible to every check in the repo. The standing
rule already covers plan_audit.py, meme_library.py, meme_box_check.py,
gif_library_select.py and gif_url_cache.py: one copy, imported by both. This
module extends it to the runner body itself.

uat/tests/test_runner_drift.py enforces what is left: any function still
defined in both runners must be identical unless declared, with a reason.

Per-runner configuration is injected, never assumed. PROMPTS_DIR is the one
value that genuinely differs — production reads prompts/, UAT reads
uat/prompts/, and that fork is the entire point of the sandbox. Each runner
calls configure() at import; load_prompt() reads what it set.
"""

import json
import re
import time
from pathlib import Path
from html import escape as escape_html
from urllib.parse import quote
from urllib.request import urlopen, Request

import anthropic

# ---------------------------------------------------------------------------
# Per-runner configuration
# ---------------------------------------------------------------------------
# Set by configure(). Deliberately None until then: reading production prompts
# from a UAT run would silently defeat the sandbox, so an unconfigured import
# must fail loudly rather than fall back to a default.
PROMPTS_DIR: Path | None = None


def configure(*, prompts_dir: Path) -> None:
    """
    Point the shared helpers at this runner's prompt tree.

    Refuses a conflicting reconfigure. PROMPTS_DIR is module state, so if both
    runners were ever imported into one process the second call would silently
    repoint the first — a UAT run reading production prompts, or worse. That is
    the exact failure mode this whole module exists to end, so it raises rather
    than resolving quietly in favour of whoever imported last.
    """
    global PROMPTS_DIR
    new_dir = Path(prompts_dir)
    if PROMPTS_DIR is not None and PROMPTS_DIR != new_dir:
        raise RuntimeError(
            f"runner_common already configured for {PROMPTS_DIR}; refusing to "
            f"repoint at {new_dir}. Both runners appear to be imported in one "
            f"process — they have forked prompt trees and cannot share one."
        )
    PROMPTS_DIR = new_dir


def _require_prompts_dir() -> Path:
    if PROMPTS_DIR is None:
        raise RuntimeError(
            "runner_common.configure(prompts_dir=...) was never called. The "
            "importing runner must set its own prompt tree; there is no safe "
            "default, because guessing wrong means UAT silently tests "
            "production's prompts."
        )
    return PROMPTS_DIR


# ---------------------------------------------------------------------------
# Model selection and pricing
# ---------------------------------------------------------------------------
# Pass 2 (Writer) uses Opus 4.7 for the prose quality lift — A/B trial week
# starting 2026-06-01. All other LLM passes use Sonnet 4.5, which is sufficient
# for selection/transformation tasks.
# NOTE on Opus 4.7: docs warn it follows instructions more literally than
# Sonnet — prompts tuned for Sonnet may need adjustment if output regresses.
# Also ships with a new tokenizer that can use 1.0-1.35x more tokens for the
# same input vs. 4.6; budget for that when reading the cost summary.
MODEL_DEFAULT = "claude-sonnet-4-5"   # Pass 1, 4, 6
MODEL_WRITER  = "claude-opus-4-7"     # Pass 2

# Backwards-compat alias — some downstream code still references MODEL.
MODEL = MODEL_DEFAULT

# Per-million-token prices used by cost_summary. Cache write is about 1.25x
# base input; cache read about 0.1x base input. Keep in sync with the Anthropic
# rate card. If pricing changes, update here — it is the single source of truth
# for the daily cost breakdown that lands at the top of the email.
PRICING = {
    "claude-sonnet-4-5": {"in": 3.0, "out": 15.0, "cw":  3.75, "cr": 0.30},
    "claude-opus-4-7":   {"in": 5.0, "out": 25.0, "cw":  6.25, "cr": 0.50},
}

# Accumulator for per-pass cost so email_newsletter.py can surface a price
# breakdown above the daily issue. Both runners bind this same list object, so
# it must be mutated in place (append), never rebound.
PASS_COSTS: list[dict] = []

# ---------------------------------------------------------------------------
# Output ceilings, and detecting when a pass hits one
# ---------------------------------------------------------------------------
# Pass 2 measured 7,029 output tokens on 2026-08-31 against a cap of 8,192 --
# 86% of the ceiling. One busy Saturday and the draft stops mid-sentence.
# Nothing checked stop_reason, so a truncated pass shipped as a finished one.
#
# 16,384 is deliberate: the SDK refuses a NON-streaming request above 21,333
# (bisected against anthropic==1.2.0), and Pass 2 runs a tool loop that
# streaming would complicate. This doubles the headroom while staying under
# that line. Anything above 21,333 must convert to streaming in the same
# commit -- see the Pass 1 note in CLAUDE.md.
MAX_TOKENS_WRITER = 16384    # Pass 2: writes the whole draft
MAX_TOKENS_EDITOR = 8192     # Passes 4 and 6: rewrite a draft of ~3.5-5K tokens

# stop_reasons that mean "this output is incomplete".
#   max_tokens -- hit the ceiling above.
#   pause_turn -- a server tool (web_search) paused a long turn. Resuming it
#                 correctly means continuing the assistant turn, which we do
#                 NOT do; so treat it as partial and say so rather than
#                 shipping the fragment as if it were the finished draft.
INCOMPLETE_STOP_REASONS = ("max_tokens", "pause_turn")


def was_truncated(response, label: str) -> bool:
    """True when a pass returned an incomplete response. Logs and records it.

    The caller decides what to do about it: an editing pass can fall back to
    its input, but Pass 2 has no earlier draft to fall back to, so there the
    only honest move is to make the run say so.
    """
    stop = getattr(response, "stop_reason", None)
    if stop not in INCOMPLETE_STOP_REASONS:
        return False
    why = ("hit its max_tokens ceiling" if stop == "max_tokens"
           else "was paused mid-turn by a server tool and not resumed")
    print(f"  \u2717 {label} {why} \u2014 the output is INCOMPLETE "
          f"(stop_reason={stop!r}).")
    try:
        import run_status
        run_status.append("incomplete_passes", f"{label} ({stop})")
    except Exception as e:  # noqa: BLE001 -- reporting must never break a run
        print(f"    (could not record run status: {type(e).__name__})")
    return True



def load_prompt(filename: str) -> str:
    path = _require_prompts_dir() / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def format_game_state_summary(game_state: dict) -> str:
    """
    Format game_state.json into a concise ground truth block for LLM prompts.
    Covers yesterday's completed games with series state for playoffs.
    Pass 1 and Pass 2 both receive this so they never rely on training data
    for scores, series state, or game numbers.
    """
    if not game_state or not game_state.get("sports"):
        return ""

    lines = [
        "## GROUND TRUTH: YESTERDAY'S GAME RESULTS",
        f"Source: ESPN API as of {game_state.get('as_of_date', 'unknown')}",
        "Treat as AUTHORITATIVE. Any claim about scores, series state, or game numbers",
        "must match this data exactly or be omitted. Do not invent or assume.",
        "",
    ]

    found_any = False
    for sport_key, sport_data in game_state.get("sports", {}).items():
        label = sport_data.get("label", sport_key.upper())
        completed = [
            g for g in sport_data.get("yesterday_games", [])
            if g.get("completed")
        ]
        if not completed:
            continue
        found_any = True
        lines.append(f"{label}:")
        for game in completed:
            home  = game.get("home_team", "?")
            away  = game.get("away_team", "?")
            hs    = game.get("home_score", 0)
            as_   = game.get("away_score", 0)
            ot    = " (OT)" if game.get("overtime") else ""
            winner = game.get("winner", "")
            lines.append(f"  {away} {as_}, {home} {hs}{ot}" + (f" — {winner} wins" if winner else ""))
            series = game.get("series")
            if series:
                if series.get("series_over"):
                    lines.append(f"    SERIES OVER. {winner} advances. Final: {series.get('home_wins',0)}-{series.get('away_wins',0)}.")
                else:
                    summary  = series.get("summary", "")
                    next_g   = series.get("next_game_number", "?")
                    elim_h   = series.get("elimination_game_for_home", False)
                    elim_a   = series.get("elimination_game_for_away", False)
                    elim_who = []
                    if elim_h: elim_who.append(home)
                    if elim_a: elim_who.append(away)
                    elim_str = f" {', '.join(elim_who)} eliminated." if elim_who else ""
                    lines.append(f"    PLAYOFFS — {summary}. Next: Game {next_g}.{elim_str}")
        lines.append("")

    if not found_any:
        return ""
    return "\n".join(lines)


def strip_code_fences(text: str) -> str:
    text = re.sub(r'^```(?:html|json)?\s*\n?', '', text.strip())
    text = re.sub(r'\n?```\s*$', '', text.strip())
    return text.strip()


def extract_text(response) -> str:
    return "".join(
        block.text for block in response.content if hasattr(block, "text")
    )


def cost_summary(label: str, model: str, in_tokens: int, out_tokens: int,
                 cache_read: int = 0, cache_write: int = 0) -> float:
    p = PRICING.get(model, PRICING[MODEL_DEFAULT])
    est = (cache_write * p["cw"] + cache_read * p["cr"]
           + in_tokens * p["in"] + out_tokens * p["out"]) / 1_000_000
    cache_note = ""
    if cache_read or cache_write:
        cache_note = f" (cache read: {cache_read:,} | cache write: {cache_write:,})"
    short_model = model.replace("claude-", "")
    print(f"  [{label}] {short_model} — {in_tokens:,} in / {out_tokens:,} out — ~${est:.4f}{cache_note}")
    PASS_COSTS.append({
        "label":       label,
        "model":       model,
        "in_tokens":   in_tokens,
        "out_tokens":  out_tokens,
        "cache_read":  cache_read,
        "cache_write": cache_write,
        "cost":        round(est, 6),
    })
    return est


def load_gif_history(repo_root: Path) -> list:
    """Load gif_history.json to check for recently used GIFs."""
    history_path = repo_root / "gif_history.json"
    if history_path.exists():
        try:
            return json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def is_recently_used(gif_url: str, history: list, days: int = 7) -> bool:
    """Return True if this exact GIF URL was used in the past N days."""
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=days)
    for entry in history:
        try:
            entry_date = date.fromisoformat(entry["date"])
            if entry_date >= cutoff and entry.get("url") == gif_url:
                return True
        except Exception:
            continue
    return False


def normalize_gif_concept(search_term: str) -> str:
    """
    Extract the core meme reference from a search term for concept-level dedup.
    Strips context notes after " — " or " - ", then takes the first 4 words.
    This catches the same GIF being used with slightly different search terms.

    Examples:
      "Monkey Puppet Side-Eye — extremely awkward situation" → "monkey puppet side-eye"
      "Leonardo DiCaprio raising glass Django — pure respect" → "leonardo dicaprio raising glass"
    """
    concept = search_term.split(" — ")[0].split(" - ")[0]
    concept = concept.strip('"\' ').lower()
    words = concept.split()[:4]
    return " ".join(words)


def is_concept_recently_used(search_term: str, history: list, days: int = 7) -> bool:
    """Return True if a GIF with the same core concept was used in the past N days."""
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=days)
    concept = normalize_gif_concept(search_term)
    if not concept:
        return False
    for entry in history:
        try:
            entry_date = date.fromisoformat(entry["date"])
            if entry_date >= cutoff:
                entry_concept = normalize_gif_concept(entry.get("search_term", ""))
                if concept == entry_concept:
                    return True
        except Exception:
            continue
    return False


def save_gif_history(repo_root: Path, new_entries: list, history: list):
    """Append new GIF uses to gif_history.json, keep last 60 entries."""
    history_path = repo_root / "gif_history.json"
    combined = new_entries + history
    combined = combined[:60]
    history_path.write_text(json.dumps(combined, indent=2), encoding="utf-8")


def format_recent_media_block(repo_root: Path, days: int = 7) -> str:
    """
    Build a 'do not repeat' block of GIF concepts and meme templates used in the
    last N days, for injection into Pass 2's user message.

    Without this, Pass 2 cannot honor the rotation rules in gif_reference.txt /
    meme_reference.txt — it never sees what was recently used. History files log
    each item multiple times per day (embed runs over multiple output files), so
    concepts/slugs are de-duplicated here. Returns "" if there is no history.
    """
    from datetime import date, timedelta

    def _parse_date(s):
        try:
            return date.fromisoformat(s)
        except Exception:
            return None

    cutoff = date.today() - timedelta(days=days)

    # GIF concepts (most recent first, de-duplicated by normalized concept)
    gif_concepts, seen_concepts = [], set()
    for e in load_gif_history(repo_root):
        d = _parse_date(e.get("date", ""))
        if not d or d < cutoff:
            continue
        raw = e.get("search_term", "")
        concept = normalize_gif_concept(raw)
        if concept and concept not in seen_concepts:
            seen_concepts.add(concept)
            gif_concepts.append(raw.split(" — ")[0].strip())

    # Meme templates (de-duplicated by slug)
    meme_slugs, seen_slugs = [], set()
    meme_path = repo_root / "meme_history.json"
    if meme_path.exists():
        try:
            meme_hist = json.loads(meme_path.read_text(encoding="utf-8"))
        except Exception:
            meme_hist = []
        for e in meme_hist:
            d = _parse_date(e.get("date", ""))
            if not d or d < cutoff:
                continue
            slug = e.get("slug", "")
            if slug and slug not in seen_slugs:
                seen_slugs.add(slug)
                meme_slugs.append(slug)

    if not gif_concepts and not meme_slugs:
        return ""

    lines = [
        "## RECENTLY USED MEDIA — DO NOT REPEAT (last 7 days)",
        "These GIFs and meme templates ran in recent issues. Pick different ones "
        "from the same emotional category in the GIF / MEME REFERENCE. The only "
        "exception is a deliberate escalation callback within a single story.",
    ]
    if gif_concepts:
        lines.append("GIF concepts used recently: " + "; ".join(gif_concepts))
    if meme_slugs:
        lines.append("Meme templates used recently: " + ", ".join(meme_slugs))
    return "\n".join(lines) + "\n\n"


def clean_giphy_search(term: str) -> list[str]:
    clean = term.strip().strip('"').strip("'").strip("[]")
    for filler in ["search Giphy for ", "search for ", "from ", "meme", "gif", "reaction"]:
        clean = clean.replace(filler, "").strip()
    queries = [clean]
    words = clean.split()
    if len(words) > 4:
        queries.append(" ".join(words[:4]))
    if len(words) > 6:
        queries.append(" ".join(words[:3]))
    return queries


def fetch_giphy_candidates(search_term: str, api_key: str, limit: int = 5) -> list[str]:
    """Search Giphy and return up to `limit` candidate URLs (best quality per result)."""
    try:
        encoded_term = quote(search_term)
        api_url = f"https://api.giphy.com/v1/gifs/search?api_key={api_key}&q={encoded_term}&limit={limit}&rating=pg-13"
        req = Request(api_url, headers={"User-Agent": "SLAP-Newsletter/1.0"})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("data", [])
            urls = []
            for result in results:
                images = result.get("images", {})
                for size_key in ("downsized_medium", "downsized", "original"):
                    if size_key in images and images[size_key].get("url"):
                        urls.append(images[size_key]["url"])
                        break
            return urls
    except Exception:
        return []


def embed_gifs_in_html(html: str, api_key: str, repo_root: Path = None) -> tuple[str, list]:
    gif_pattern = re.compile(
        r'<div class="gif-placeholder">\s*GIF:\s*(.+?)\s*</div>',
        re.DOTALL
    )
    matches = gif_pattern.findall(html)
    if not matches:
        print("  [GIPHY] No GIF placeholders found")
        return html, []

    # `history` gains this run's entries below so same-run dedup can see
    # them; `stored_history` is what was on disk. Saving new_entries +
    # `history` wrote every entry TWICE — 60-row files held 30 real rows,
    # and gif_history.json covered only 6 days against a 7-day lookback,
    # so the rotation rule was silently under-enforced. (2026-09-02)
    history = load_gif_history(repo_root) if repo_root else []
    stored_history = list(history)
    embed_count = 0
    used_gifs = []

    for search_term in matches:
        queries = clean_giphy_search(search_term)
        gif_url = None
        chosen_query = None

        for q in queries:
            print(f"  [GIPHY] Searching: {q}")
            candidates = fetch_giphy_candidates(q, api_key, limit=5)

            # Pick first candidate not used in the last 7 days
            for candidate_url in candidates:
                if not is_recently_used(candidate_url, history) and not is_concept_recently_used(q, history):
                    gif_url = candidate_url
                    chosen_query = q
                    break
                else:
                    reason = "URL" if is_recently_used(candidate_url, history) else "concept"
                    print(f"           -> Skipping recently used ({reason}): {candidate_url[:60]}...")

            if gif_url:
                break

            # All candidates were recently used — fall back to first result anyway
            if candidates:
                print(f"           -> All candidates recently used, using freshest anyway")
                gif_url = candidates[0]
                chosen_query = q
                break

            print(f"           -> No results, trying shorter...")
            time.sleep(0.2)

        if gif_url and chosen_query:
            placeholder_pattern = re.compile(
                r'<div class="gif-placeholder">\s*GIF:\s*'
                + re.escape(search_term)
                + r'\s*</div>',
                re.DOTALL
            )
            img_html = (
                f'<div style="margin: 16px 0; text-align: center;">'
                f'<img src="{escape_html(gif_url, quote=True)}" alt="{escape_html(chosen_query, quote=True)}" '
                f'style="max-width: 100%; border-radius: 8px;" />'
                f'</div>'
            )
            html, count = placeholder_pattern.subn(img_html, html, count=1)
            if count > 0:
                embed_count += 1
                print(f"           -> Embedded: {gif_url[:80]}...")
                entry = {
                    "date": __import__('datetime').date.today().isoformat(),
                    "url": gif_url,
                    "search_term": chosen_query,
                }
                used_gifs.append(entry)
                history.insert(0, entry)  # update in-memory history for same-run dedup
        else:
            print(f"           -> All queries failed, keeping placeholder")
        time.sleep(0.3)

    print(f"  [GIPHY] {embed_count}/{len(matches)} GIFs embedded")

    # Save updated history
    if repo_root and used_gifs:
        save_gif_history(repo_root, used_gifs, stored_history)

    return html, used_gifs


def blockquotes_to_substack_urls(html: str) -> str:
    """
    Replace styled tweet blockquotes with bare tweet URLs on their own line.
    Substack auto-embeds a bare twitter.com or x.com URL when pasted.
    """
    def replace(match):
        block = match.group(0)
        # Prefer twitter.com, fall back to x.com, strip Nitter fragments
        url_match = re.search(
            r'href="(https?://(?:twitter\.com|x\.com|nitter\.net)/[^"]+)"',
            block
        )
        if url_match:
            url = url_match.group(1)
            # Normalise Nitter → Twitter
            url = url.replace("nitter.net", "twitter.com")
            # Strip Nitter fragment (#m)
            url = re.sub(r'#m$', '', url)
            return f'\n<p class="tweet-url">{url}</p>\n'
        return block

    return re.sub(
        r'<blockquote[^>]*class="tweet"[^>]*>.*?</blockquote>',
        replace,
        html,
        flags=re.DOTALL
    )


def drop_fabricated_tweets(html: str) -> tuple[str, int]:
    """
    Remove any tweet blockquote whose URL lacks a real numeric status ID.

    The model sometimes invents a reaction-tweet slot for a headliner beat and
    emits a made-up URL like ".../status/placeholder" when no real tweet exists
    in the feed for that beat. A fabricated URL never embeds in Substack, so it
    is strictly worse than no tweet. This deterministic guard strips them before
    the draft/substack/email files are written, regardless of whether Pass 1 or
    Pass 2 introduced them.
    """
    pattern = re.compile(
        r'<blockquote[^>]*class="tweet"[^>]*>.*?</blockquote>',
        re.DOTALL | re.IGNORECASE,
    )
    dropped = 0

    def _check(m: re.Match) -> str:
        nonlocal dropped
        block = m.group(0)
        href = re.search(r'href="([^"]+)"', block)
        if href and re.search(r'/status/\d+', href.group(1)):
            return block
        dropped += 1
        return ""

    return pattern.sub(_check, html), dropped


def normalize_topic_key(key: str) -> str:
    """Collapse per-game / per-day topic keys to a stable series-level key.

    The model routinely appends a game number ('-game3', '-g3') or a date to an
    otherwise-stable key, which defeats continuing-story matching and makes a
    multi-game playoff series look brand new every single day. Stripping those
    segments means 'knights-avalanche-game2-wcf-2026' and
    'knights-avalanche-game3-wcf-2026' both collapse to
    'knights-avalanche-wcf-2026', so the series is recognized as one ongoing
    story. The 4-digit season year (e.g. '-2026') is preserved.
    """
    if not isinstance(key, str):
        return ""
    k = key.lower().strip()
    k = re.sub(r'-?\bgame[-_]?\d+\b', '', k)   # -game3, game_3
    k = re.sub(r'-?\bg\d+\b', '', k)           # -g3
    k = re.sub(r'-?\bgm\d+\b', '', k)          # -gm3
    k = re.sub(r'-?\bday[-_]?\d+\b', '', k)    # -day2
    k = re.sub(r'-\d{4}-\d{2}-\d{2}\b', '', k) # -2026-05-25
    k = re.sub(r'-\d{1,2}-\d{1,2}\b', '', k)   # -05-25
    k = re.sub(r'-{2,}', '-', k).strip('-')    # collapse/trim stray hyphens
    return k


def format_story_history(recent_output: list) -> str:
    """
    Format the last 14 days of story_log entries into a readable block
    for Pass 1 to use in continuing story detection.
    Handles both new format (story_log) and old format (all_headlines).
    """
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=14)
    lines = []

    for entry in recent_output:
        try:
            entry_date = date.fromisoformat(entry.get("date", ""))
        except Exception:
            continue
        if entry_date < cutoff:
            continue

        lines.append(f"\n--- {entry['date']} ---")

        if "story_log" in entry:
            for story in entry["story_log"]:
                resolved = " [RESOLVED]" if story.get("resolved") else ""
                _tk = normalize_topic_key(story.get('topic_key', '')) or story.get('topic_key', '?')
                lines.append(
                    f"  [{story.get('section', '?').upper()}] "
                    f"{_tk} — "
                    f"{story.get('title', '?')}{resolved}"
                )
                lines.append(f"    Development: {story.get('development', '?')}")
        elif "all_headlines" in entry:
            # Legacy format — provide headline list without topic keys
            lines.append(f"  Lead: {entry.get('lead_story', '?')}")
            for h in entry.get("all_headlines", [])[1:]:
                lines.append(f"  Supporting: {h}")

    if not lines:
        return "No recent story history available."
    return "\n".join(lines)


def normalize_plan(plan: dict) -> dict:
    """
    Sanitize every field in the Pass 1 plan dict to its expected type.

    The model occasionally returns wrong types for nested fields — a string
    where a list is expected, a string where a dict is expected, a list of
    strings where a list of tweet-dicts is expected. This has caused crashes
    in three different places (missing-text loop, pre_edit, and would have
    hit others).

    Running this once, immediately after Pass 1 returns, means every
    downstream consumer (pre_edit, save_story_log, run_pass2, etc.) receives
    a guaranteed-clean structure and needs no individual type guards.
    """
    def safe_dict(val) -> dict:
        return val if isinstance(val, dict) else {}

    def safe_list(val) -> list:
        return val if isinstance(val, list) else []

    def safe_tweet_list(val) -> list:
        """Return a list containing only well-formed tweet dicts with a REAL
        numeric tweet URL. Drops fabricated tweets (e.g. .../status/placeholder)
        that the model invents when it wants a reaction tweet not in the feed."""
        return [
            t for t in safe_list(val)
            if isinstance(t, dict)
            and t.get("url")
            and re.search(r'/status/\d+', str(t.get("url", "")))
        ]

    def safe_str(val) -> str:
        return val if isinstance(val, str) else ""

    def normalize_beat_list(val) -> list:
        """Same guarantee as safe_tweet_list, one level down: each beat's
        media[] gets the fabricated-URL filter too, and a beat missing its
        required text fields is dropped rather than crashing a downstream
        consumer that assumes angle/landing are present strings."""
        out = []
        for b in safe_list(val):
            if not isinstance(b, dict):
                continue
            angle   = safe_str(b.get("angle"))
            landing = safe_str(b.get("landing"))
            if not angle or not landing:
                continue
            out.append({
                "angle": angle,
                "landing": landing,
                "media": safe_tweet_list(b.get("media")),
            })
        return out

    def normalize_story(val) -> dict:
        s = safe_dict(val)
        s["tweets"] = safe_tweet_list(s.get("tweets"))
        s["gif_concept"]  = safe_str(s.get("gif_concept"))
        # Default to Tier 1: a missing tier must never silently authorize live
        # search, which is the expensive/uncurated path.
        try:
            tier = int(s.get("gif_tier") or 1)
        except (TypeError, ValueError):
            tier = 1
        s["gif_tier"]     = tier if tier in (1, 3) else 1
        s["meme_concept"] = safe_str(s.get("meme_concept"))
        s["beats"]        = normalize_beat_list(s.get("beats"))
        return s

    plan["lead_story"]         = normalize_story(plan.get("lead_story"))
    plan["supporting_stories"] = [
        normalize_story(s)
        for s in safe_list(plan.get("supporting_stories"))
        if isinstance(s, dict)
    ]
    atl = safe_dict(plan.get("around_the_league"))
    atl["tweets"]              = safe_tweet_list(atl.get("tweets"))
    plan["around_the_league"]  = atl
    plan["story_log"]          = [
        s for s in safe_list(plan.get("story_log"))
        if isinstance(s, dict)
    ]

    return plan


def save_story_log(story_plan_raw: str, recent_output: list, path: Path) -> list:
    """
    Extract today's story_log from Pass 1's JSON output, prepend it to
    recent_output, trim to 30 entries, and save to disk.
    Returns the updated recent_output list.
    """
    try:
        plan = json.loads(story_plan_raw)
        story_log = plan.get("story_log", [])
        if not story_log:
            print("  ⚠ No story_log in Pass 1 output — recent_output not updated")
            return recent_output
        # Normalize topic keys before persisting so a multi-game series stores
        # one stable key (strips -game3/-g3/date suffixes the model appends).
        for _s in story_log:
            if isinstance(_s, dict) and _s.get("topic_key"):
                _norm = normalize_topic_key(_s["topic_key"])
                if _norm:
                    _s["topic_key"] = _norm
    except json.JSONDecodeError:
        print("  ⚠ Pass 1 output not valid JSON — recent_output not updated")
        return recent_output

    from datetime import date
    new_entry = {
        "date": date.today().isoformat(),
        "story_log": story_log,
    }

    # Ensure recent_output is a list (handles old dict format)
    if not isinstance(recent_output, list):
        recent_output = [recent_output] if recent_output else []

    updated = [new_entry] + recent_output
    updated = updated[:30]  # keep last 30 days

    path.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ story_log saved — {len(story_log)} stories logged")
    return updated


def run_pass4(draft_html: str, client: anthropic.Anthropic) -> str:
    print("\n── PASS 4: Voice Editor ────────────────────────────")

    voice_examples = load_prompt("voice_examples.txt")
    voice_prompt   = load_prompt("pass4_voice.txt")

    if not voice_prompt:
        print("  ⚠ prompts/pass4_voice.txt not found — skipping voice pass")
        return draft_html

    # Voice examples lead the system prompt so they are the first thing
    # the model reads — imitation before instruction.
    system_blocks = []
    if voice_examples:
        system_blocks.append({
            "type": "text",
            "text": voice_examples,
            "cache_control": {"type": "ephemeral"},
        })
    system_blocks.append({
        "type": "text",
        "text": voice_prompt,
        "cache_control": {"type": "ephemeral"},
    })

    response = client.messages.create(
        model=MODEL_DEFAULT,
        max_tokens=MAX_TOKENS_EDITOR,
        system=system_blocks,
        messages=[{
            "role": "user",
            "content": (
                "Rewrite any <p> tag prose that fails the Sportswriter Test. "
                "Leave everything else unchanged. Return the full HTML.\n\n"
                + draft_html
            )
        }]
    )

    cache_read  = getattr(response.usage, "cache_read_input_tokens", 0)
    cache_write = getattr(response.usage, "cache_creation_input_tokens", 0)
    cost_summary("PASS 4", MODEL_DEFAULT, response.usage.input_tokens, response.usage.output_tokens, cache_read, cache_write)

    # A truncated rewrite is a draft with its ending missing. The existing
    # "did it come back as HTML?" gate cannot catch it -- truncation removes
    # the END, so the <h1> it looks for is still there. Keep the input.
    if was_truncated(response, "PASS 4"):
        print("    Keeping the pre-voice draft rather than a half-rewritten one.")
        return draft_html

    return strip_code_fences(extract_text(response))


def _normalize_tweet_url(url: str) -> str:
    """Normalize a tweet URL for reliable comparison across sources."""
    url = url.strip()
    url = url.replace("nitter.net", "twitter.com")
    url = re.sub(r'#m$', '', url)
    url = re.sub(r'(/status)=(\d)', r'\1/\2', url)  # status= → status/
    return url.lower()


def run_pass6(draft_html: str, recent_output: dict, client: anthropic.Anthropic,
              game_state: dict | None = None) -> str:
    """
    The editor pass. `game_state` is what makes CHECK 8 real rather than a
    guess: that check leaves a claim alone when an embedded tweet OR the
    ground-truth block sources it, and until 2026-09-04 the editor was never
    shown the ground truth at all — it could only compare a number against the
    tweets next to it. Optional so an older caller still works, but every
    caller in this repo passes it.
    """
    print("\n── PASS 6: Editor ──────────────────────────────────")

    editor_prompt = load_prompt("editor_prompt.txt")
    if not editor_prompt:
        print("  ⚠ prompts/editor_prompt.txt not found — skipping editor pass")
        return draft_html

    # Build system as cached blocks.
    # The static editor prompt is cached. The dynamic media note (changes daily)
    # is appended as a separate uncached block so the static cache still hits.
    system_blocks = [
        {
            "type": "text",
            "text": editor_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    latest = recent_output[0] if isinstance(recent_output, list) and recent_output else (recent_output or {})
    recent_gifs  = latest.get("gifs_used", [])
    recent_memes = latest.get("memes_used", [])
    if recent_gifs or recent_memes:
        media_note = (
            "## PREVIOUS ISSUE MEDIA — DO NOT REUSE\n"
            f"GIFs used: {json.dumps(recent_gifs)}\n"
            f"Memes used: {json.dumps(recent_memes)}"
        )
        system_blocks.append({"type": "text", "text": media_note})

    # Ground truth rides in the USER message, not the cached system block: it
    # changes daily and would thrash the cache. CHECK 8 names it as one of the
    # two things that can source a claim, so it has to be here — a check that
    # cites evidence it was never given is worse than no check.
    ground_truth = format_game_state_summary(game_state or {})
    if ground_truth:
        print(f"  ground truth supplied to CHECK 8 ({len(ground_truth):,} chars)")
    else:
        print("  ⚠ no ground truth available — CHECK 8 can only source claims "
              "from the tweets in each section")

    response = client.messages.create(
        model=MODEL_DEFAULT,
        max_tokens=MAX_TOKENS_EDITOR,
        system=system_blocks,
        messages=[{
            "role": "user",
            "content": (
                (ground_truth + "\n\n") if ground_truth else ""
            ) + f"Edit this newsletter draft and return the corrected HTML:\n\n{draft_html}"
        }]
    )

    cache_read  = getattr(response.usage, "cache_read_input_tokens", 0)
    cache_write = getattr(response.usage, "cache_creation_input_tokens", 0)
    cost_summary("PASS 6", MODEL_DEFAULT, response.usage.input_tokens, response.usage.output_tokens, cache_read, cache_write)

    # As in Pass 4: a truncated edit silently drops the end of the newsletter,
    # Around the League included. The unedited draft is the better outcome.
    if was_truncated(response, "PASS 6"):
        print("    Keeping the pre-editor draft rather than a truncated edit.")
        return draft_html

    edited = strip_code_fences(extract_text(response))

    # Count editor flags for the operator log
    flags = re.findall(r'<!-- EDITOR FLAG:', edited)
    if flags:
        print(f"  ⚠ {len(flags)} editor flag(s) inserted — review before publishing")
    else:
        print(f"  ✓ No flags raised")

    return edited
