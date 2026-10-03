"""
SLAP Newsletter — Claim Validator (Pass 3)

Validates factual claims in the newsletter draft against game_state.json.
Injects FACT FLAG and COHERENCE FLAG HTML comments for Pass 6 (Editor) to resolve.

This is a deterministic Python script — no LLM calls, no API cost.
Called from generate_newsletter.py after Pass 2 (Writer).

Can also be run standalone for testing:
  python claim_validator.py --input newsletter_draft.html
"""

import json
import re
from pathlib import Path

SCRIPT_DIR      = Path(__file__).resolve().parent
GAME_STATE_PATH = SCRIPT_DIR / "game_state.json"


# ---------------------------------------------------------------------------
# Regex patterns for claim extraction
# ---------------------------------------------------------------------------

# "Game 5", "Game Five", "G5" — catches game number references in prose
GAME_NUMBER_RE = re.compile(
    r'\bGame\s+(\d+|one|two|three|four|five|six|seven)\b',
    re.IGNORECASE,
)

WORD_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7,
}

# Series score: "3-2 lead", "leads 3-2", "up 2-1", "tied 2-2", "2-2 series"
SERIES_SCORE_RE = re.compile(
    r'(?:'
    r'\b([0-3])-([0-3])\s*(?:lead|series|advantage|deficit)\b'  # "3-2 lead"
    r'|'
    r'\b(?:leads?|trails?|up|down|tied|even)\s+([0-3])-([0-3])\b'  # "leads 3-2"
    r')',
    re.IGNORECASE,
)

# Elimination language — high-risk claim
ELIMINATION_RE = re.compile(
    r'\belimination\s+game\b'
    r'|\bwin.or.go.home\b'
    r'|\bmust.win\s+game\b'
    r'|\bseason\s+on\s+the\s+line\b'
    r'|\bback\s+against\s+the\s+wall\b',
    re.IGNORECASE,
)

# "Series over / ends / done" — dangerous when series is still live
SERIES_OVER_RE = re.compile(
    r'\bseries\s+(?:is\s+)?over\b'
    r'|\bseries\s+ends?\b'
    r'|\bseries\s+done\b',
    re.IGNORECASE,
)

# Defending champion language — training data may be stale
DEFENDING_CHAMP_RE = re.compile(
    r'\bdefending\s+champ(?:ion)?s?\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def strip_tags(html: str) -> str:
    """Remove all HTML tags, return plain text."""
    return re.sub(r'<[^>]+>', '', html)


def own_text(section_html: str) -> str:
    """A section's own prose: no embedded tweets, no HTML comments (a flag
    an earlier pass left), one paragraph or heading per line."""
    html = re.sub(r"<blockquote\b.*?</blockquote>|<!--.*?-->", " ", section_html,
                  flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"</(p|h[1-6]|li|div)>|<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    return strip_tags(html)


def split_into_sections(html: str) -> list[tuple[str, str]]:
    """
    Split HTML at every <h1>/<h2> boundary.
    Returns list of (heading_text, section_html) tuples.
    Content before the first heading is labeled '__preamble__'.
    """
    parts = re.split(r'(?=<h[12][\s>])', html, flags=re.IGNORECASE)
    sections: list[tuple[str, str]] = []
    for part in parts:
        if not part.strip():
            continue
        match = re.match(r'<h[12][^>]*>(.*?)</h[12]>', part, re.IGNORECASE | re.DOTALL)
        if match:
            heading = strip_tags(match.group(1)).strip()
        else:
            heading = "__preamble__"
        sections.append((heading, part))
    return sections


def inject_flag_after_heading(section_html: str, comment: str) -> str:
    """Insert a flag HTML comment immediately after the section's heading tag."""
    heading_end = re.search(r'</h[12]>', section_html, re.IGNORECASE)
    if heading_end:
        pos = heading_end.end()
        return section_html[:pos] + comment + section_html[pos:]
    # No heading found — prepend
    return comment + section_html


# ---------------------------------------------------------------------------
# Game state helpers
# ---------------------------------------------------------------------------

def load_game_state(path: Path = GAME_STATE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_yesterday_playoff_games(game_state: dict) -> list[dict]:
    """Return all completed playoff games from yesterday across all sports."""
    results = []
    for sport_data in game_state.get("sports", {}).values():
        for game in sport_data.get("yesterday_games", []):
            if game.get("completed") and game.get("playoffs") and game.get("series"):
                results.append(game)
    return results


def extract_game_number(text: str) -> int | None:
    """Pull the first game number mentioned in text. Returns int or None."""
    match = GAME_NUMBER_RE.search(text)
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    return WORD_TO_NUM.get(raw.lower())


# ---------------------------------------------------------------------------
# Check 3: "defending champion", resolved (SLA-65)
# ---------------------------------------------------------------------------
#
# game_state.json carries a "champions" block (champions_source.py, read from
# slap-sports-db): each league's most recent champion, or `stale`/`missing`
# when the database is behind. For every "defending champion" in a section:
#   - the team it names IS a current champion      -> confirmed, nothing added
#   - it names a team that is NOT one              -> FACT FLAG [HIGH] naming
#     the real champions, which editor Check 9 uses to correct the sentence
#   - it names no team we can recognise, or a league's champion is unknown
#                                                   -> FACT FLAG [LOW]: the
#     claim can't be confirmed, so the editor cuts the title phrase
# With no champions block at all (no database) every claim is the LOW case,
# which is what this check always did.

# A team name as written: capitalised words, "of" allowed inside
# ("Mighty Ducks of Anaheim"). Case-sensitive on purpose: it is the capital
# letters that separate "the Knicks" from "the rest of the league".
_NAME = r"[A-Z][\w.'&-]*(?:\s+(?:of\s+)?[A-Z][\w.'&-]*){0,3}"
_PHRASE = r"(?i:defending)\s+(?:[\w.'-]+\s+){0,2}?(?i:champ(?:ion)?s?)\b"
# "the defending champion Thunder", "defending NBA champs, the Knicks"
_AFTER_RE = re.compile(_PHRASE + r"[,:]?\s+(?:the\s+)?(" + _NAME + r")")
# "the Knicks, the defending champions", "Indiana are the defending champs"
_BEFORE_RE = re.compile(
    r"(" + _NAME + r"),?\s+(?:(?:who\s+)?(?:are|were|is|was|as|remain)\s+)?(?:the\s+)?"
    r"(?:reigning\s+(?:and\s+)?)?" + _PHRASE)
# Capitalised only because they start a sentence ("Against the defending
# champions."): never a team.
_NOT_A_TEAM = {"the", "a", "an", "and", "but", "now", "so", "with", "when", "as", "then",
               "even", "yes", "no", "meanwhile", "also", "still", "if", "because", "who",
               "against", "over", "for", "from", "to", "at", "by", "vs", "versus", "beat",
               "beating", "beats", "facing", "face", "faces", "after", "before", "into",
               "like", "of", "on", "in", "than", "past", "without", "toward", "towards",
               "they", "we", "you", "he", "she", "it", "this", "that", "these", "those",
               "here", "there", "just", "only", "every", "all", "both", "sweeping",
               "eliminating", "eliminated", "stunning", "upsetting", "ousting"}
# Words that turn one school into another: "Michigan State" is not Michigan.
_NAME_CONTINUES = {"state", "st", "tech", "a&m", "southern", "northern", "eastern",
                   "western", "central", "christian", "international"}
_SENTENCE_RE = re.compile(r"[^.!?]*\bdefending\s+champ[^.!?]*[.!?]?", re.IGNORECASE)


def _tokens(name: str) -> list[str]:
    return re.findall(r"[a-z0-9&]+", name.lower())


def _is_champion(mention: str, champions: dict) -> str | None:
    """The league whose current champion `mention` names, or None.

    The mention may run on past the name ("Knicks Tuesday"), so its leading
    words are tried against the END of each alias ("New York Knicks" is
    named by "Knicks" and by "New York Knicks"). A match that would leave
    a school-name word behind ("Michigan State") is not a match."""
    m = _tokens(mention)
    for league, entry in champions.items():
        for alias in entry.get("aliases", []):
            a = _tokens(alias)
            for k in range(min(len(m), len(a)), 0, -1):
                if m[:k] == a[-k:] and (k == len(m) or m[k] not in _NAME_CONTINUES):
                    return league
    return None


def _mention(sentence: str) -> str | None:
    """The team a "defending champion" sentence attaches the title to."""
    for pattern in (_AFTER_RE, _BEFORE_RE):
        m = pattern.search(sentence)
        if m:
            words = m.group(1).split()
            while words and words[0].lower().strip(",.:;'") in _NOT_A_TEAM:
                words.pop(0)
            if words:
                return " ".join(words)
    return None


def _names_a_champion(sentence: str, champions: dict) -> bool:
    words = _tokens(sentence)
    for entry in champions.values():
        for alias in entry.get("aliases", []):
            a = _tokens(alias)
            if any(words[i:i + len(a)] == a for i in range(len(words) - len(a) + 1)):
                return True
    return False


def _champion_list(champions: dict) -> str:
    return "; ".join(f"{e['label']}: {e['team']} ({e['season_label']})"
                     for e in champions.values())


def check_defending_champion(plain_text: str, game_state: dict) -> list[str]:
    if not DEFENDING_CHAMP_RE.search(plain_text):
        return []
    import champions_source
    champions = champions_source.known_champions(game_state)
    unknown = champions_source.unknown_leagues(game_state)
    if not champions:
        return ['\n<!-- FACT FLAG [LOW]: "Defending champion" language, and the champions '
                'data is unavailable today, so it cannot be confirmed. Cut the title phrase '
                'and keep the team (e.g. "the defending champion Thunder" -> "the Thunder"). -->']
    flags = []
    cities = {tuple(_tokens(e["city"])) for e in champions.values() if e.get("city")}
    for sentence in _SENTENCE_RE.findall(plain_text):
        mention = _mention(sentence)
        if mention and _is_champion(mention, champions):
            continue                                   # confirmed: nothing to fix
        # No team attached to the phrase ("they stole homecourt from the
        # defending champs"): the story names who it means. If the sentence,
        # or failing that the section, names a current champion, that is who.
        if not mention and (_names_a_champion(sentence, champions)
                            or _names_a_champion(plain_text, champions)):
            continue
        # Inside an HTML comment: no double quotes, and never "--", which would
        # end the comment early and print the rest into the newsletter.
        quoted = re.sub(r"\s+", " ", sentence).strip()[:160].replace('"', "'").replace("--", "-")
        # A bare city ("the defending champion Carolina") could be any of its
        # clubs: not provably wrong, so it is the can't-confirm case.
        ambiguous = mention and tuple(_tokens(mention)) in cities
        if mention and not unknown and not ambiguous:
            flags.append(
                f'\n<!-- FACT FLAG [HIGH]: "{quoted}" calls {mention} the defending champion. '
                f'They are not. Current defending champions: {_champion_list(champions)}. '
                f'Correct the team, or cut the title phrase. -->')
        else:
            gap = (f" The {', '.join(champions_source.LEAGUES[k][0] for k in unknown)} "
                   f"champion is not known today." if unknown else "")
            who = f"calls {mention} the defending champion" if mention else "names no team we can match"
            flags.append(
                f'\n<!-- FACT FLAG [LOW]: "{quoted}" {who}, and that cannot be confirmed.{gap} '
                f'Known defending champions: {_champion_list(champions)}. If the sentence '
                f'means one of them, name it; otherwise cut the title phrase. -->')
    return flags


# ---------------------------------------------------------------------------
# History claims (SLA-109): "first since YEAR", "N-year drought", "longest
# ... since", checked against the HISTORICAL CONTEXT block (history_source).
#
#   a listed fact agrees          -> confirmed, nothing added
#   the one team the sentence names has a fact of that kind, and it disagrees
#                                 -> FACT FLAG [HIGH] quoting the fact
#   anything else                 -> FACT FLAG [LOW]: RULE 3 still applies
#
# HIGH is deliberately narrow. A fact is only listed when it is notable, so
# "no fact" never means "false"; and a sentence naming two teams, or none,
# could mean either. Every doubt resolves to LOW, which asks for exactly what
# RULE 3 asked for before this existed.
# ---------------------------------------------------------------------------

_NUM_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}
_N = r"(\d{1,3}|" + "|".join(_NUM_WORDS) + r")"

# "since 1999", "since at least 1999", "since the 2020 season", "since '99",
# "since 1998-99". Not a decade ("since the '90s"): that is the relative
# framing RULE 3 asks for.
_SINCE_RE = re.compile(r"\bsince\s+(?:at\s+least\s+)?(?:the\s+)?(?:(\d{4})(?:-(\d{2}))?|['’](\d{2}))(?![\ds'’])",
                       re.IGNORECASE)
_DATING_RE = re.compile(r"\bdating\s+(?:back\s+)?to\s+(?:the\s+)?(\d{4})(?:-(\d{2}))?(?![\ds])", re.IGNORECASE)
_DROUGHT_RE = re.compile(_N + r"[- ](?:year|season)s?[- ](?:title\s+|finals\s+|playoff\s+|postseason\s+)?"
                         r"(?:drought|wait|absence|skid|gap)\b", re.IGNORECASE)
_IN_N_YEARS_RE = re.compile(r"\b(?:in|for)\s+(?:the\s+first\s+time\s+in\s+)?" + _N + r"\s+(?:years|seasons)\b",
                            re.IGNORECASE)
# A "since YEAR" is a history claim only next to one of these: "the league
# has existed since 1997" is not one.
_CLAIM_TRIGGER = re.compile(
    r"\b(first|longest|most|best|worst|highest|lowest|biggest|fewest|never|without|drought|"
    r"streak|straight|last\s+time|neither|nobody|none|hasn['’]?t|haven['’]?t|hadn['’]?t|didn['’]?t|not|no)\b", re.IGNORECASE)

_PLAYER = re.compile(r"\b(he|she|his|her|he['’]s|she['’]s|him)\b", re.IGNORECASE)
# Postseason milestones the database does not hold (series wins, conference
# titles, rounds): never matched to a title or playoff fact. "First conference
# finals since 2000" is not a Finals claim.
_OTHER_POSTSEASON = re.compile(
    r"\b(afc|nfc|conference|division(al)?|wild[- ]?card|alcs|nlcs|ecf|wcf|semi-?finals?|"
    r"(first|second|third)\s+round|round|series\s+(win|wins|victory|victories)|playoff\s+(win|wins|victory|"
    r"victories|series|game|games)|postseason\s+(win|wins|victory|series|game|games)|closeout|elimination|"
    r"bowl\s+(game|win|victory)|cfp|college\s+football\s+playoff|mcws|llws|ncaa\s+tournament|final\s+four|"
    r"sweet\s+16|elite\s+eight|all-star|heisman)\b", re.IGNORECASE)
_APPEAR = re.compile(r"\b(reach|reached|reaches|reaching|return|returns|returned|back\s+(in|to)|appearance|"
                     r"trip|berth|advance|advanced|advances|make|made|makes|clinch|clinched|in\s+the)\b",
                     re.IGNORECASE)
_WIN = re.compile(r"\b(won|win|wins|winning|victory|victories|beat|beats|beating|topped|defeated?|swept)\b",
                  re.IGNORECASE)
_LOSE = re.compile(r"\b(lost|lose|loses|losing|loss|losses|skid|defeats|dropped)\b", re.IGNORECASE)
# A pennant IS a World Series appearance: the database's runner-up record
# is exactly the other pennant winner (SLA-110).
_FINALS = re.compile(r"\b(nba\s+finals|cup\s+final|finals|world\s+series|super\s+bowl|title\s+game|pennants?|"
                     r"championship\s+game)\b", re.IGNORECASE)
# "Cup" alone is the Stanley Cup in our prose; the World Cup is never a team
# in the history block.
_TITLE = re.compile(r"\b(title|titles|championship|championships|champions?|ring|crown|stanley\s+cup|"
                    r"(?<!world\s)cup|won\s+it\s+all|parade)\b", re.IGNORECASE)


def _clause(window: str) -> str:
    """The clause a claim sits in. "The Hurricanes, who reached the Eastern
    Conference finals for the third time in four years, are looking for
    their first Stanley Cup since 2006" is a title claim: the conference
    finals are an aside (replay, 2026-05-10). A last clause too short to say
    anything ("..., the first time since 1999") falls back to the whole."""
    last = re.split(r"[,;:—–]|\s-\s", window)[-1]
    return last if _claim_kinds(last) or _PLAYER.search(last) else window


def _claim_kinds(window: str) -> set[str]:
    """What a history claim is about, from the words just before it. Empty
    when it isn't a team fact the database holds (a player's, a series
    win's): those can only ever be LOW."""
    w = window.lower()
    if _PLAYER.search(w) or _OTHER_POSTSEASON.search(w):
        return set()
    if re.search(r"miss(ed|es|ing)?", w):
        return set()                                   # "first missed playoffs since": the inverse fact
    if re.search(r"\b(straight|in\s+a\s+row|consecutive)\b", w) and re.search(r"\bseasons\b", w):
        return set()                                   # "five straight winning seasons"
    if _FINALS.search(w) or (_TITLE.search(w) and "stanley cup" in w):
        if re.search(r"\bpennants?\b", w):
            return {"title_game"}                      # "won the pennant" is reaching the World Series
        if re.search(r"\b(title|champion|championship|ring|parade)s?\b", w) and not re.search(
                r"\b(title|championship)\s+game\b", w):
            return {"title"}                           # "World Series title", "Super Bowl champions"
        if _APPEAR.search(w):
            return {"title_game"}                      # "first Finals trip", "reach the World Series"
        if _WIN.search(w) and not re.search(r"\b(final|finals)\b", w):
            return {"title"}                           # "won the Super Bowl", "won the Stanley Cup"
        if "stanley cup" in w and not re.search(r"\bfinals?\b", w):
            return {"title"}                           # "first Stanley Cup since 1994"
        return {"title_game"}
    if _TITLE.search(w):
        return {"title"}
    if re.search(r"\b(playoffs?|postseason)\b", w):
        return {"playoff"}
    if re.search(r"\bwinning\s+(season|record)|(above|over)\s+\.500\b", w):
        return {"winning"}
    if re.search(r"\b(straight|in\s+a\s+row|consecutive|streak|skid)\b", w):
        if _LOSE.search(w):
            return {"streak_L"}
        if _WIN.search(w):
            return {"streak_W"}
        return set()                                   # "scoreless streak"
    if re.search(r"\bstart\b|\b\d{1,2}-0\b|\b0-\d{1,2}\b", w):
        return {"start_best", "start_worst"}
    if re.search(r"\b(ranked|ranking|top[- ]\d+|poll)\b|\bno\.\s*\d", w):
        return {"ranked_high"} if re.search(r"\b(high|highest|top|no\.)", w) else {"ranked"}
    return set()


def _alias_index(teams: list[dict]) -> dict[tuple, set[str]]:
    """Token sequence -> the teams it names. A pro team goes by its full name
    or its nickname ("Knicks", "Red Sox"); a college by its school
    ("Georgia"), never a nickname that a dozen schools share, and never a
    school that is the start of another ("Michigan" for Michigan State)."""
    idx: dict[tuple, set[str]] = {}

    def add(alias, name):
        idx.setdefault(tuple(alias), set()).add(name)

    for t in teams:
        for name, college in ((t["team"], t["sport"] == "ncaafb"), (t.get("opponent") or "", t["sport"] == "ncaafb")):
            toks = _tokens(name)
            if not toks:
                continue
            add(toks, name)
            for k in range(1, len(toks)):
                if college:
                    if toks[k] not in _NAME_CONTINUES:
                        add(toks[:k], name)
                elif not (k == len(toks) - 1 and toks[-1] in {"sox", "jays", "leafs", "knights", "blazers", "wings"}):
                    add(toks[k:], name)
    return idx


def _named(text: str, idx: dict[tuple, set[str]]) -> set[str]:
    words = _tokens(text)
    found = set()
    for alias, names in idx.items():
        n = len(alias)
        if any(tuple(words[i:i + n]) == alias for i in range(len(words) - n + 1)):
            found |= names
    return found


def _claim_years(m: re.Match, issue_year: int, nows: set[int]) -> tuple[set[int], str]:
    """The year(s) a claim says the thing last happened, and how it said it."""
    if m.re in (_SINCE_RE, _DATING_RE):
        if m.re is _SINCE_RE and m.group(3):
            yy = int(m.group(3))
            y = 1900 + yy if yy > issue_year % 100 else 2000 + yy
            return {y}, "year"
        y = int(m.group(1))
        return ({y, y + 1} if m.group(2) else {y}), "year"
    raw = m.group(1).lower()
    n = int(raw) if raw.isdigit() else _NUM_WORDS[raw]
    return {now - n for now in nows}, "count"


def _verdict(fact: dict, claim: set[int], sport: str, window: str, season: int = 0) -> str:
    """confirm / contradict / unknown for one fact against one claim."""
    if fact["never"]:
        return "contradict"
    if fact["floor"] is not None:
        # "None since at least 1999": a claim of 2005 says there was one, so
        # it's wrong; a claim of 1987 is past what the data can see.
        return "contradict" if min(claim) >= fact["floor"] else "unknown"
    last = set(fact["last"])
    # Football seasons end in the next calendar year: the 2019 season's Super
    # Bowl and playoffs were played in 2020, and either is how people say it.
    if sport in ("nfl", "ncaafb") and fact["kind"] in ("title", "title_game", "playoff"):
        last |= {y + 1 for y in last}
        if fact["kind"] in ("title", "title_game") and "super bowl" in window.lower() and max(last) < 1967:
            return "unknown"                           # a pre-Super Bowl NFL title
    if last & claim:
        return "confirm"
    # A fact from the last season or so may BE the event the sentence
    # recounts: "Gausman sent the Blue Jays to their first World Series since
    # 1993" (2026-08-03, about last October) is true, and "last World Series
    # appearance: 2025" is that very trip. The one before it isn't listed, so
    # an older claim can't be convicted.
    if max(claim) < min(last) and max(fact["last"]) >= season - 1:
        return "unknown"
    return "contradict"


def _numbers_agree(fact: dict, window: str) -> bool:
    """A streak or start claim about a different number ("first 5-game
    streak since...", against a 7-game one) is about something else."""
    if "length" in fact:
        nums = re.findall(r"\b" + _N + r"(?:[- ]game)?\s+(?:straight|in\s+a\s+row|consecutive|"
                          r"(?:winning|losing|win|game)?\s*streak)", window, re.IGNORECASE)
        return all((int(n) if n.isdigit() else _NUM_WORDS[n.lower()]) == fact["length"] for n in nums)
    if "record" in fact:
        recs = re.findall(r"\b\d{1,2}-\d{1,2}(?:-\d)?\b", window)
        return all(r == fact["record"] for r in recs)
    return True


# How prose shortens the cities the database spells out.
_CITY_SHORT = {"los angeles": ["la", "l.a."], "new york": ["ny"], "kansas city": ["kc"],
               "san francisco": ["sf"], "tampa bay": ["tampa"], "philadelphia": ["philly"],
               "las vegas": ["vegas"], "washington": ["dc", "d.c."]}


def _at_city(window: str, city: str) -> bool:
    """'won in LA', 'at Buffalo': a win AT the city the fact names."""
    names = [city] + _CITY_SHORT.get(city.lower(), [])
    return any(re.search(r"\b(?:at|in)\s+(?:the\s+)?" + re.escape(n) + r"(?![\w.])", window, re.IGNORECASE)
               for n in names)


def _history_anchors(sentence: str):
    for rx in (_SINCE_RE, _DATING_RE, _DROUGHT_RE, _IN_N_YEARS_RE):
        for m in rx.finditer(sentence):
            if rx is _DROUGHT_RE or _CLAIM_TRIGGER.search(sentence[:m.end()]):
                yield m


def _quote(sentence: str) -> str:
    # Inside an HTML comment: no double quotes, and never "--".
    return re.sub(r"\s+", " ", sentence).strip()[:160].replace('"', "'").replace("--", "-")


def check_history_claims(own_text: str, game_state: dict, outcomes: list | None = None) -> list[str]:
    """`own_text`: one section's own prose (no tweets), one block per line.
    `outcomes`, if given, collects (verdict, sentence) for every claim,
    confirmed ones included: the archive replay counts them."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", own_text) if s.strip()]
    if not any(True for s in sentences for _ in _history_anchors(s)):
        return []
    import history_source
    teams = history_source.team_facts(game_state)
    by_name = {t["team"]: t for t in teams}
    idx = _alias_index(teams)
    as_of = ((game_state or {}).get("history") or {}).get("as_of") or game_state.get("yesterday_date") or ""
    issue_year = int(as_of[:4]) if as_of[:4].isdigit() else 0
    section_teams = {n for n in _named(own_text, idx) if n in by_name}
    flags, seen = [], set()
    for sentence in sentences:
        start = 0
        for m in sorted(_history_anchors(sentence), key=lambda m: m.start()):
            window = sentence[start:m.end()]
            start = m.end()
            kinds = _claim_kinds(_clause(window))
            named = {n for n in _named(sentence, idx) if n in by_name}
            candidates = named or section_teams
            results = []                               # (team, fact, verdict)
            for name in candidates:
                t = by_name[name]
                nows = {issue_year, t["season"] or issue_year}
                if t["sport"] in ("nba", "nhl") and t["season"]:
                    nows.add(t["season"] + 1)
                claim, _ = _claim_years(m, issue_year, nows)
                facts = [f for f in t["facts"] if f["kind"] in kinds]
                # Head-to-head: a win over the team the sentence names, or at
                # its city. Regular season only, and a team's, not a player's.
                if _WIN.search(window) and not (_PLAYER.search(window) or _OTHER_POSTSEASON.search(window)):
                    facts += [f for f in t["facts"] if f["kind"] == "h2h" and f["opponent"] in _named(window, idx)]
                    facts += [f for f in t["facts"] if f["kind"] == "h2h_at" and _at_city(window, f["city"])]
                for f in facts:
                    v = _verdict(f, claim, t["sport"], window, t["season"] or issue_year) if _numbers_agree(f, window) else "unknown"
                    results.append((t, f, v))
            if any(v == "confirm" for _, _, v in results):
                if outcomes is not None:
                    outcomes.append(("confirmed", _quote(sentence)))
                continue
            quoted = _quote(sentence)
            if quoted in seen:
                continue
            seen.add(quoted)
            contradicting = {t["team"] for t, _, v in results if v == "contradict"}
            # HIGH only when the sentence itself names exactly one team with a
            # fact of this kind, and every such fact disagrees: no guessing
            # whose history a two-team or no-team sentence meant.
            if (named and len({t["team"] for t, _, _ in results}) == 1 and contradicting
                    and all(v == "contradict" for _, _, v in results)):
                t = results[0][0]
                facts = "; ".join(f["text"] for _, f, _ in results)
                ago = sorted({issue_year - max(f["last"]) for _, f, _ in results if f["last"]})
                ago_s = f" ({', '.join(str(a) for a in ago)} years before this issue)" if ago else ""
                flags.append(
                    f'\n<!-- FACT FLAG [HIGH]: "{quoted}" makes a history claim the SLAP sports '
                    f'database contradicts. {t["team"]}: {facts}{ago_s}, as of {as_of}. Correct the '
                    f'year or count to match, keeping its kind (a title is not a Finals appearance), '
                    f'or cut the claim. -->')
                if outcomes is not None:
                    outcomes.append(("HIGH", flags[-1]))
            else:
                flags.append(
                    f'\n<!-- FACT FLAG [LOW]: "{quoted}" makes a specific history claim the HISTORICAL '
                    f'CONTEXT block does not confirm. RULE 3: replace the specific year or count with '
                    f'relative framing, or cut the clause. -->')
                if outcomes is not None:
                    outcomes.append(("LOW", quoted))
    return flags


# ---------------------------------------------------------------------------
# Section validator
# ---------------------------------------------------------------------------

def validate_section(heading: str, section_html: str, game_state: dict) -> tuple[str, int]:
    """
    Run all checks against a single newsletter section.
    Returns (annotated_html, flag_count).
    """
    if heading == "__preamble__":
        return section_html, 0

    plain_text     = strip_tags(section_html)
    playoff_games  = get_yesterday_playoff_games(game_state)
    flags: list[str] = []

    # ── CHECK 1: "Series over" + future game number in same section ──────────
    # This is the exact pattern that produced the 5/14 bug.
    series_over_match = SERIES_OVER_RE.search(plain_text)
    game_number_match = GAME_NUMBER_RE.search(plain_text)

    if series_over_match and game_number_match:
        # Find a playoff game where series is still live
        live_game = next(
            (g for g in playoff_games if not g["series"].get("series_over", True)),
            None,
        )
        if live_game:
            s = live_game["series"]
            claimed_game = extract_game_number(plain_text)
            flags.append(
                f'\n<!-- COHERENCE FLAG: Section contains "series over" language but also references '
                f'Game {claimed_game}. game_state shows series is ONGOING: {s.get("summary", "see game_state.json")}. '
                f'The series is NOT over — remove "series over" language or correct it. '
                f'Next game: Game {s.get("next_game_number", "?")}. -->'
            )

    # ── CHECK 2: "Elimination game" claim validation ──────────────────────────
    if ELIMINATION_RE.search(plain_text):
        # Was yesterday's game actually an elimination game for any team?
        any_true_elim = any(
            g["series"].get("elimination_game_for_home") or
            g["series"].get("elimination_game_for_away")
            for g in playoff_games
        )

        if playoff_games and not any_true_elim:
            # There are playoff games but none were elimination games
            game = playoff_games[0]
            s    = game["series"]
            flags.append(
                f'\n<!-- FACT FLAG [HIGH]: "Elimination game" language detected '
                f'but game_state shows this was NOT an elimination game. '
                f'Series state: {s.get("summary", "unknown")} '
                f'(home wins: {s.get("home_wins", "?")}, away wins: {s.get("away_wins", "?")}, '
                f'series over: {s.get("series_over", "?")}). '
                f'Remove or rewrite this claim. -->'
            )
        elif not playoff_games and ELIMINATION_RE.search(plain_text):
            # Elimination claim but no playoff games in game_state at all
            flags.append(
                f'\n<!-- FACT FLAG [MEDIUM]: "Elimination game" language detected '
                f'but game_state has no playoff game data to verify against. '
                f'Manually verify before publishing. -->'
            )

    # ── CHECK 3: Defending champion language ─────────────────────────────────
    # Resolved against the champions block (SLA-65), not flagged for a human.
    # Only our own prose: an embedded tweet is someone else's words, which the
    # editor may not touch (a 2026-07 tweet shouting "THE DEFENDING CHAMPIONS"
    # about Argentina is not a claim SLAP made).
    own_prose = strip_tags(re.sub(r"<blockquote\b.*?</blockquote>", " ", section_html,
                                  flags=re.IGNORECASE | re.DOTALL))
    flags.extend(check_defending_champion(own_prose, game_state))

    # ── CHECK 3B: History claims vs HISTORICAL CONTEXT (SLA-109) ─────────────
    # Same rule: our prose only. Paragraph and heading breaks become line
    # breaks so a heading never runs into the sentence after it.
    flags.extend(check_history_claims(own_text(section_html), game_state))

    # ── CHECK 4: Series score claim vs game_state ─────────────────────────────
    score_matches = SERIES_SCORE_RE.findall(plain_text)
    for match in score_matches:
        # match groups: (g1, g2, g3, g4) from the two alternation branches
        if match[0] and match[1]:
            a, b = int(match[0]), int(match[1])
        elif match[2] and match[3]:
            a, b = int(match[2]), int(match[3])
        else:
            continue

        claimed = tuple(sorted([a, b], reverse=True))  # (higher, lower)

        for game in playoff_games:
            s = game["series"]
            actual = tuple(sorted([s.get("home_wins", 0), s.get("away_wins", 0)], reverse=True))
            if claimed != actual:
                flags.append(
                    f'\n<!-- FACT FLAG [HIGH]: Series score "{a}-{b}" in draft may not match '
                    f'game_state: {s.get("summary", f"{actual[0]}-{actual[1]}")}. '
                    f'Verify and correct. -->'
                )
                break  # one flag per section is enough for score mismatches

    # ── Inject all flags ──────────────────────────────────────────────────────
    if not flags:
        return section_html, 0

    annotated = section_html
    for flag in flags:
        annotated = inject_flag_after_heading(annotated, flag)

    return annotated, len(flags)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate_claims(
    html: str,
    game_state_path: Path = GAME_STATE_PATH,
    game_state: dict | None = None,
) -> tuple[str, int]:
    """
    Validate factual claims in newsletter HTML against game_state.json.

    Args:
        html: Full newsletter HTML string (from Pass 2 output).
        game_state_path: Path to game_state.json produced by fetch_sports_data.py.
        game_state: The runner's in-memory copy, when it has one. Since SLA-111
            the runner adds history for the teams the day's stories name
            after Pass 1, so its copy knows more than the file does.

    Returns:
        (annotated_html, total_flag_count)
        annotated_html has FACT FLAG / COHERENCE FLAG HTML comments injected.
        Pass 6 (Editor) resolves all flags before publish.
    """
    print("\n── PASS 3: Claim Validator ─────────────────────────")

    game_state = game_state if game_state else load_game_state(game_state_path)

    if not game_state:
        print("  ⚠ game_state.json not found — run fetch_sports_data.py first")
        print("    Skipping claim validation (no ground truth data available)")
        return html, 0

    sports_count = len(game_state.get("sports", {}))
    playoff_games = get_yesterday_playoff_games(game_state)
    print(f"  Loaded game_state: {sports_count} sport(s), {len(playoff_games)} playoff game(s) from yesterday")

    sections   = split_into_sections(html)
    parts      = []
    total_flags = 0

    for heading, section_html in sections:
        annotated, count = validate_section(heading, section_html, game_state)
        parts.append(annotated)
        total_flags += count

    if total_flags:
        print(f"  ⚠ {total_flags} flag(s) inserted — Pass 6 (Editor) will resolve")
    else:
        print(f"  ✓ No claim flags raised")

    return "".join(parts), total_flags


# ---------------------------------------------------------------------------
# Standalone runner (for testing)
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="SLAP Claim Validator — validates newsletter claims against game_state.json"
    )
    parser.add_argument(
        "--input", default="newsletter_draft.html",
        help="Input HTML file (default: newsletter_draft.html)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output file path (default: overwrites input)",
    )
    args = parser.parse_args()

    input_path  = SCRIPT_DIR / args.input
    output_path = SCRIPT_DIR / (args.output or args.input)

    if not input_path.exists():
        print(f"✗ Input file not found: {input_path}")
        return

    html = input_path.read_text(encoding="utf-8")
    annotated, flag_count = validate_claims(html)
    output_path.write_text(annotated, encoding="utf-8")

    if flag_count:
        print(f"\n⚠ {flag_count} flag(s) written to {output_path.name}")
        print(f"  Search 'FLAG' in the file to find them.")
    else:
        print(f"\n✓ Clean — no flags. Output: {output_path.name}")


if __name__ == "__main__":
    main()
