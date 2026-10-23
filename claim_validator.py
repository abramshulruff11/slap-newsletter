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
    if _PLAYER.search(w):
        return set()
    # SLA-130: the football bundles' upset and playoff-win facts. Before
    # these, "first win over a top-10 team since 2017" read as a POLL claim
    # and was compared with "last ranked this high in 2021".
    if re.search(r"\b(playoff|postseason)\s+(win|victory)\b|\bwon\s+a\s+(playoff|postseason)\s+game\b", w):
        return {"playoff_win"}
    if re.search(r"\b(loss|losses|lost|lose|loses|fell)\b.{0,30}\bunranked\b", w):
        return {"upset_L"}
    if (re.search(r"\b(win|wins|won|victory|victories|beat|beats|beating|knock\w*\s+off|upset\w*)\b.{0,40}"
                  r"\b(ranked|top[- ](\d+|five|ten)|ap)\b", w) and not re.search(r"\bunranked\b", w)):
        return {"upset_W"}
    if _OTHER_POSTSEASON.search(w):
        return set()
    if re.search(r"\b(biggest|most|highest|lowest|fewest|largest|smallest)\b", w):
        # A superlative measures something ("biggest Finals court redesign in
        # 17 years", replay 2026-06-01): never a title, Finals or playoff
        # drought, which only a "first"/"since"/"drought" claim is.
        return set()
    if re.search(r"\bmiss(ed|es|ing)?\b", w):
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


_COLLEGE_SPORTS = ("ncaafb", "ncaamb")      # SLA-146: a college team goes by its school


def _alias_index(teams: list[dict]) -> dict[tuple, set[str]]:
    """Token sequence -> the teams it names. A pro team goes by its full name
    or its nickname ("Knicks", "Red Sox"); a college by its school
    ("Georgia"), never a nickname that a dozen schools share, and never a
    school that is the start of another ("Michigan" for Michigan State)."""
    idx: dict[tuple, set[str]] = {}

    def add(alias, name):
        idx.setdefault(tuple(alias), set()).add(name)

    for t in teams:
        for name, college in ((t["team"], t["sport"] in _COLLEGE_SPORTS),
                              (t.get("opponent") or "", t["sport"] in _COLLEGE_SPORTS)):
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
    if "bucket" in fact:
        # "a top-10 team" is a different fact from "a ranked team" (SLA-130):
        # the bundle holds only the bucket the beaten team fell in.
        m = re.search(r"\btop[- ](\d+|five|ten)\b", window, re.IGNORECASE)
        claimed = (int(m.group(1)) if m.group(1).isdigit() else {"five": 5, "ten": 10}[m.group(1).lower()]) if m \
            else 25
        return claimed == fact["bucket"]
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


# A period that ends an abbreviation does not end the sentence: "No. 4 Ole
# Miss" was being read as "... No." and "4 Ole Miss" (SLA-117 replay), which
# hid every rank claim and split history claims in two.
_SENTENCE_END = re.compile(r"(?<=[.!?])(?<!\bNo\.)(?<!\bno\.)(?<!\bSt\.)(?<!\bvs\.)(?<!\bJr\.)(?<!\bSr\.)"
                           r"(?<!\bMt\.)(?<!\bFt\.)(?<!\b[A-Z]\.)\s+|\n+")


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s.strip()]


def _quote(sentence: str) -> str:
    # Inside an HTML comment: no double quotes, and never "--".
    return re.sub(r"\s+", " ", sentence).strip()[:160].replace('"', "'").replace("--", "-")


_FOOTBALL_FACT_TEXT = {
    "upset_W": lambda f: (f"last regular-season win over {'an AP-ranked team' if f['bucket'] == 25 else 'an AP top-' + str(f['bucket']) + ' team'}"
                          + (f": {max(f['last'])}" if f["last"] else f": none since at least {f['floor']}")),
    "upset_L": lambda f: ("last regular-season loss to an unranked team"
                          + (f": {max(f['last'])}" if f["last"] else f": none since at least {f['floor']}")),
    "playoff_win": lambda f: ("last playoff win" + (f": the {min(f['last'])} season" if f["last"]
                                                    else f": none since at least {f['floor']}")),
}


def _with_football_facts(teams: list[dict], game_state: dict) -> list[dict]:
    """The history teams plus the football bundles' upset and playoff-win
    facts (SLA-130), in history_source.parse_facts()'s shape, so the same
    confirm / HIGH / LOW rules and the final re-check apply to them."""
    import football_bundle
    out = [dict(t, facts=list(t["facts"])) for t in teams]
    by_key = {(t["team"], t["sport"]): t for t in out}
    as_of = (game_state or {}).get("yesterday_date") or ""
    year = int(as_of[:4]) if as_of[:4].isdigit() else 0
    for f in football_bundle.claim_facts(game_state or {}):
        hf = [{"kind": x["kind"], "text": _FOOTBALL_FACT_TEXT[x["kind"]](x), "last": x["last"],
               "floor": x["floor"], "never": False, **({"bucket": x["bucket"]} if "bucket" in x else {})}
              for x in f.get("history_facts") or [] if x.get("kind") in _FOOTBALL_FACT_TEXT]
        if not hf:
            continue
        t = by_key.get((f["team"], f["sport"]))
        if t is None:
            t = {"team": f["team"], "opponent": f["opponent"], "sport": f["sport"], "season": year, "facts": []}
            out.append(t)
            by_key[(f["team"], f["sport"])] = t
        t["facts"].extend(hf)
    return out


def check_history_claims(own_text: str, game_state: dict, outcomes: list | None = None) -> list[str]:
    """`own_text`: one section's own prose (no tweets), one block per line.
    `outcomes`, if given, collects (verdict, sentence) for every claim,
    confirmed ones included: the archive replay counts them."""
    sentences = _sentences(own_text)
    if not any(True for s in sentences for _ in _history_anchors(s)):
        return []
    import history_source
    teams = _with_football_facts(history_source.team_facts(game_state), game_state)
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
                    outcomes.append(("confirmed", _quote(sentence), sentence))
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
                    outcomes.append(("HIGH", quoted, sentence, flags[-1]))
            else:
                flags.append(
                    f'\n<!-- FACT FLAG [LOW]: "{quoted}" makes a specific history claim the HISTORICAL '
                    f'CONTEXT block does not confirm. RULE 3: replace the specific year or count with '
                    f'relative framing, or cut the clause. -->')
                if outcomes is not None:
                    outcomes.append(("LOW", quoted, sentence))
    return flags


# ---------------------------------------------------------------------------
# Football rank and record claims (SLA-117): "No. 8 Florida", "the unranked
# ...", "improved to 4-1", "came in 4-0", "Missouri (4-1)", "still unbeaten",
# checked against football_bundle.claim_facts(): ESPN's ranks and records
# for yesterday's NFL and college games, plus the database's AP and CFP
# ranks from the bundles.
#
#   the data agrees                          -> confirmed, nothing added
#   the one team the claim is about plainly disagrees
#                                            -> FACT FLAG [HIGH] with the data
#   anything else about yesterday's teams    -> FACT FLAG [LOW]: cut the number
#
# Only teams that played yesterday are checked: a rank in a preview of next
# week's game is not something this data holds, and editor Check 8 already
# covers it. A rank belongs to the team written right after it, so it can be
# HIGH even in a two-team sentence; a record attaches to a team only through
# "Team (4-1)", so a verb-cued record ("improved to 4-1") is HIGH only when
# the sentence names a single team.
# ---------------------------------------------------------------------------

_RANK_RE = re.compile(r"(?:\bNo\.\s*|#)(\d{1,2})(?!\d)(?:-ranked)?\s+|\b(\d{1,2})(?:st|nd|rd|th)-ranked\s+|"
                      r"\b(unranked)\s+", re.IGNORECASE)
# "now No. 5 Georgia": next week's poll, which nobody has yet. NOT "to": a
# rank with a team after it is the team's ("down 20 to No. 1 Ohio State",
# replay 2026-09-13); "climbed to No. 5" has no team and is never checked.
_FORECAST_RE = re.compile(r"\bnow\s*$", re.IGNORECASE)
_RECORD_RE = re.compile(r"(?<![\d-])(\d{1,2})-(\d{1,2})(?:-(\d{1,2}))?(?![\d-])")
_AFTER_CUE = re.compile(r"\b(improv\w*|mov(?:e|es|ed|ing)|f[ae]ll(?:s|en|ing)?|drop\w*|slip\w*|ris(?:e|es|en|ing)|"
                        r"rose|climb\w*|push\w*|now|sits?|sitting|sat|stands?|standing|stood)\s+(?:to\s+|at\s+)?"
                        r"(?:a\s+)?$", re.IGNORECASE)
_BEFORE_CUE = re.compile(r"\b(?:(?:came|coming|come|went|going|walked)\s+in|enter(?:s|ed|ing)?)\s+(?:at\s+)?(?:a\s+)?$",
                         re.IGNORECASE)
_BEFORE_AFTER = re.compile(r"^\s+(?:going|coming)\s+in\b", re.IGNORECASE)
_SCORE_AFTER = re.compile(r"^\s+(?:win|victory|loss|defeat|lead|score|margin|halftime|final|blowout|rout)\b",
                          re.IGNORECASE)
_STATUS_RE = re.compile(r"\b(?:still|remains?|remained|stays?|stayed)\s+(unbeaten|undefeated|perfect|winless)\b",
                        re.IGNORECASE)


# Standings (SLA-130). NFL only: a division lead, a share of it, games back,
# last place. Each needs a division or "first" as its object, so "leads the
# series" or "led 21-7" is never one.
_DIV = r"(?:AFC|NFC)\s+(?:East|West|North|South)"
_LEAD_RE = re.compile(r"\b(?:lead(?:s|ing)?|atop|(?:on\s+)?top\s+of|first\s+(?:place\s+)?in)\s+(?:the\s+)?"
                      r"(?P<div>" + _DIV + r"|division)\b|\bdivision\s+lead\b|\bin\s+first\s+place\b", re.IGNORECASE)
_ALONE_RE = re.compile(r"\b(?:alone\s+in|sole\s+possession\s+of|outright)\s+(?:the\s+)?(?:first|division\s+lead)\b",
                       re.IGNORECASE)
# "tied with Kansas City atop the AFC West" (replay 2026-09-28) is a share.
_SHARE_RE = re.compile(r"\btied\s+for\s+(?:first|the\s+division\s+lead)\b|"
                       r"\btied\s+(?:with\s+[^,.;]{1,40}?\s+)?(?:atop|at\s+the\s+top\s+of|for\s+the\s+lead\s+in)\b|"
                       r"\bshare\s+of\s+(?:the\s+)?"
                       r"(?:division\s+lead|first(?:\s+place)?)\b|\bco-?leaders?\b", re.IGNORECASE)
_BACK_RE = re.compile(r"\b(?P<n>\d+(?:\.5)?|a|one|two|three|four|five|half\s+a|one\s+and\s+a\s+half)\s+games?\s+"
                      r"(?:back|behind|out)\b", re.IGNORECASE)
_LAST_RE = re.compile(r"\b(?:last|bottom)\s+(?:place\s+)?(?:in|of)\s+(?:the\s+)?(?P<div>" + _DIV + r"|division)\b|"
                      r"\blast\s+place\b|\bin\s+the\s+(?:division\s+)?cellar\b", re.IGNORECASE)
_BACK_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "half a": 0.5, "one and a half": 1.5}
# A college conference lead: what the bundle holds (each team's own
# conference record) can't confirm one, so it is LOW. A conference RECORD
# ("2-0 in SEC play") is checked against ESPN's.
_CONF = r"(?:SEC|ACC|Big\s+Ten|Big\s+12|Pac-12|AAC|American|Mountain\s+West|Sun\s+Belt|MAC|C-USA|Conference\s+USA)"
_CONF_LEAD_RE = re.compile(r"\b(?:lead(?:s|ing)?|atop|(?:on\s+)?top\s+of|first\s+(?:place\s+)?in)\s+(?:the\s+)?" + _CONF
                           + r"\b")
_CONF_RECORD_RE = re.compile(r"(?<![\d-])(\d{1,2})-(\d{1,2})(?![\d-])\s+in\s+(?:the\s+)?(?:" + _CONF +
                             r"|conference|league)(?:\s+play|\s+games)?\b")


_SPLIT_RECORD = re.compile(r"^\s+(?:in\s+(?:the\s+)?(?:" + _CONF + r"|conference|league|division|" + _DIV +
                           r"|AFC|NFC)\b|against\b|vs\.?\s)", re.IGNORECASE)


def _standing_verdict(f: dict, kind: str, m: re.Match) -> str:
    s = f.get("standing")
    if not s:
        return "unknown"
    div = (m.groupdict().get("div") or "") if m else ""
    if div and div.lower() != "division" and re.sub(r"\s+", " ", div).lower() != s["division"].lower():
        return "contradict"
    if kind == "lead":
        # A team tied at the top "leads" in plain English even when the
        # tiebreaker lists it second (the Raiders, 2026-09-27): only "alone in
        # first" / "outright" needs the lead untied.
        return "confirm" if s["place"] == 1 or s["shares_top"] else "contradict"
    if kind == "alone":
        return "confirm" if s["place"] == 1 and not s["shares_top"] else "contradict"
    if kind == "share":
        return "confirm" if s["shares_top"] else "contradict"
    if kind == "last":
        return "confirm" if s["place"] == s["size"] else "contradict"
    n = m.group("n").lower()
    gb = float(n) if n[0].isdigit() else _BACK_WORDS[re.sub(r"\s+", " ", n)]
    return "confirm" if abs(gb - s["games_back"]) < 0.01 else "contradict"


def _standings_claims(sentence: str, named: list[str], by_name: dict):
    """Standings and conference claims in one sentence, about a team the
    sentence itself names (never the section's: "the Giants are two games
    back" in a baseball section must not reach an NFL team)."""
    nfl = [by_name[n] for n in named if by_name[n]["sport"] == "nfl"]
    cfb = [by_name[n] for n in named if by_name[n]["sport"] == "ncaafb"]
    one = named[0] if len(named) == 1 else None
    if nfl:
        for rx, kind in ((_ALONE_RE, "alone"), (_SHARE_RE, "share"), (_LAST_RE, "last"), (_BACK_RE, "back"),
                         (_LEAD_RE, "lead")):
            for m in rx.finditer(sentence):
                if kind == "lead" and (_ALONE_RE.search(sentence) or _SHARE_RE.search(sentence)):
                    continue                           # the more specific claim covers it
                yield ("standings", m.group(0), re.escape(m.group(0)), nfl, one,
                       lambda f, k=kind, mm=m: _standing_verdict(f, k, mm))
    if cfb:
        for m in _CONF_LEAD_RE.finditer(sentence):
            yield ("standings", m.group(0), re.escape(m.group(0)), cfb, None, lambda f: "unknown")
        for m in _CONF_RECORD_RE.finditer(sentence):
            rec = (int(m.group(1)), int(m.group(2)))
            yield ("record", m.group(0), re.escape(m.group(0)), cfb, one,
                   lambda f, r=rec: "unknown" if not f.get("conf_after") else
                   "confirm" if tuple(f["conf_after"][:2]) == r else "contradict")


def _team_after(text: str, idx: dict[tuple, set[str]]) -> str | None:
    """The team written at the very start of `text`: "Florida beat ..." ->
    Florida. Longest alias wins; "Florida State" never reads as Florida, and
    nor does any name followed by another capitalised word ("Florida
    Atlantic"), which may be a school the data doesn't hold."""
    words, raw = _tokens(text), text.split()
    best = None
    for alias, names in idx.items():
        n = len(alias)
        if tuple(words[:n]) == alias and (best is None or n > len(best[0])):
            best = (alias, names)
    if best is None or len(best[1]) != 1:
        return None
    n = len(best[0])
    if n < len(words) and words[n] in _NAME_CONTINUES:
        return None
    nxt = raw[n] if n < len(raw) else ""
    if nxt[:1].isupper() and len(_tokens(" ".join(raw[:n]))) == n:
        return None
    return next(iter(best[1]))


def _team_before(text: str, idx: dict[tuple, set[str]]) -> str | None:
    """The team written at the very end of `text`: "... Missouri (" -> Missouri."""
    words = _tokens(text)
    best = None
    for alias, names in idx.items():
        n = len(alias)
        if n <= len(words) and tuple(words[-n:]) == alias and (best is None or n > len(best[0])):
            best = (alias, names)
    return next(iter(best[1])) if best and len(best[1]) == 1 else None


def _rec(t) -> str:
    return "?" if t is None else f"{t[0]}-{t[1]}" + (f"-{t[2]}" if t[2] else "")


def _rank_verdict(f: dict, rank: int | None) -> str:
    """rank None means the claim is "unranked"."""
    if rank is None:
        if f["unranked"]:
            return "confirm"
        return "contradict" if f["ranks"] and f["unranked"] is False else "unknown"
    if rank in f["ranks"]:
        return "confirm"
    if f["ranks"] or f["unranked"]:
        return "contradict"
    return "unknown"


def _record_verdict(f: dict, rec: tuple, when: str) -> str:
    want = [f["before"]] if when == "before" else [f["after"]] if when == "after" else [f["before"], f["after"]]
    if any(w is not None and tuple(w) == rec for w in want):
        return "confirm"
    if f["post"] or any(w is None for w in want):
        return "unknown"
    return "contradict"


def _status_verdict(f: dict, word: str) -> str:
    a = f["after"]
    if a is None or f["post"]:
        return "unknown"
    ok = a[0] == 0 if word == "winless" else a[1] == 0 and a[2] == 0
    return "confirm" if ok else "contradict"


def _football_claims(sentence: str, idx: dict, by_name: dict, section_teams: set[str]):
    """Every rank or record claim in one sentence: (kind, token, figure regex,
    candidate facts, the one team it attaches to or None, verdict fn)."""
    named = [n for n in _named(sentence, idx) if n in by_name]
    for m in _RANK_RE.finditer(sentence):
        team = _team_after(sentence[m.end():], idx)
        if team is None or team not in by_name or by_name[team]["sport"] != "ncaafb":
            continue                                   # a preview, a player, a pick, an NFL seed
        n = m.group(1) or m.group(2)
        rank = int(n) if n else None
        forecast = bool(_FORECAST_RE.search(sentence[:m.start()]))
        fig = (r"(?:No\.\s*|#)" + n + r"(?!\d)|\b" + n + r"(?:st|nd|rd|th)-ranked") if n else r"\bunranked\b"
        yield ("rank", m.group(0).strip(), fig, [by_name[team]], None if forecast else team,
               (lambda f, r=rank: "unknown") if forecast else (lambda f, r=rank: _rank_verdict(f, r)))
    for m in _RECORD_RE.finditer(sentence):
        rec = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
        if sum(rec) > 20:
            continue                                   # a score, not a season
        pre, post = sentence[:m.start()], sentence[m.end():]
        if _SCORE_AFTER.search(post) or _SPLIT_RECORD.search(post):
            continue                                   # a score; or a conference/division split, not the record
        token = m.group(0)
        fig = r"(?<![\d-])" + re.escape(token) + r"(?![\d-])"
        if pre.rstrip().endswith("(") and post.lstrip().startswith(")"):
            team = _team_before(pre.rstrip()[:-1], idx)
            if team in by_name:
                yield ("record", token, fig, [by_name[team]], team,
                       lambda f, r=rec: _record_verdict(f, r, "either"))
            continue                                   # "Jones (4-1)": a pitcher, not a team
        when = ("after" if _AFTER_CUE.search(pre) else
                "before" if _BEFORE_CUE.search(pre) or _BEFORE_AFTER.search(post) else None)
        if when is None:
            continue
        cands = [by_name[n] for n in named] or [by_name[n] for n in section_teams]
        if cands:
            yield ("record", token, fig, cands, named[0] if len(named) == 1 else None,
                   lambda f, r=rec, w=when: _record_verdict(f, r, w))
    yield from _standings_claims(sentence, named, by_name)
    for m in _STATUS_RE.finditer(sentence):
        cands = [by_name[n] for n in named] or [by_name[n] for n in section_teams]
        if cands:
            word = m.group(1).lower()
            yield ("record", m.group(0), r"\b" + word + r"\b", cands, named[0] if len(named) == 1 else None,
                   lambda f, w=word: _status_verdict(f, w))


def check_football_claims(own_text: str, game_state: dict, outcomes: list | None = None) -> list[str]:
    """`own_text`: one section's own prose (no tweets), one block per line.
    `outcomes`, if given, collects (level, quoted, sentence, kind, figure)
    for every claim, confirmed ones included."""
    import football_bundle
    facts = football_bundle.claim_facts(game_state or {})
    if not facts:
        return []
    by_name = {f["team"]: f for f in facts}
    # A college team the database knows goes by its full name or its school
    # as the database spells it; "Florida" is then the Gators only, not also
    # the start of "Florida Atlantic Owls". Without a school, the prefix rule.
    idx = _alias_index([{"team": f["team"], "sport": f["sport"], "opponent": ""} for f in facts
                        if not f.get("school")])
    owned: dict[tuple, set[str]] = {}
    for f in facts:
        if f.get("school"):
            for alias in (_tokens(f["team"]), _tokens(f["school"])):
                owned.setdefault(tuple(alias), set()).add(f["team"])
    idx.update(owned)          # a school's own name beats another team's prefix ("Florida A&M")
    # "Giants", "Cardinals", "Panthers", "Jets": a nickname another league
    # shares is never, alone, the NFL team (SLA-130).
    for nick in football_bundle.other_league_nicknames(game_state):
        names = idx.get((nick,), set())
        if any(by_name.get(n, {}).get("sport") == "nfl" for n in names):
            idx.pop((nick,), None)
    as_of = (game_state or {}).get("yesterday_date") or (game_state or {}).get("as_of_date") or ""
    sentences = _sentences(own_text)
    section_teams = {n for n in _named(own_text, idx) if n in by_name}
    flags, seen = [], set()
    for sentence in sentences:
        for kind, token, fig, cands, team, verdict in _football_claims(sentence, idx, by_name, section_teams):
            results = [(f, verdict(f)) for f in cands]
            quoted = _quote(sentence)
            if any(v == "confirm" for _, v in results):
                if outcomes is not None:
                    outcomes.append(("confirmed", quoted, sentence, kind, fig))
                continue
            if (quoted, token) in seen:
                continue
            seen.add((quoted, token))
            if team is not None and len(results) == 1 and results[0][1] == "contradict":
                f = results[0][0]
                data = (f"went into the game {'ranked No. ' + ', '.join(str(r) for r in sorted(f['ranks'])) if f['ranks'] else 'unranked'}"
                        if kind == "rank" else f"{_rec(f['before'])} going in, {_rec(f['after'])} after this game")
                flags.append(
                    f'\n<!-- FACT FLAG [HIGH]: "{quoted}" gives {f["team"]} a {kind} ("{token}") the '
                    f'game data contradicts: {f["team"]} {data} (ESPN and the SLAP sports database, as of '
                    f'{as_of}). Correct it to the data, or cut it. -->')
                level = "HIGH"
            else:
                flags.append(
                    f'\n<!-- FACT FLAG [LOW]: "{quoted}" states a {kind} ("{token}") the game data cannot '
                    f'confirm. Cut the number and keep the sentence, unless a tweet in this section '
                    f'carries it. -->')
                level = "LOW"
            if outcomes is not None:
                outcomes.append((level, quoted, sentence, kind, fig))
    return flags


# ---------------------------------------------------------------------------
# Check 3D: MLB game-flow claims vs the line score (SLA-142)
#
# The 2026-10-19 ALCS recap had Hicks's single "in the 5th to put the Rays up
# 2-0" (it was the 3rd, and made it 3-0) and "a Ben Williamson single to make
# it 3-0" (Williamson scored on Aranda's single; Hicks's made it 3-0). Each
# claim below is checked against mlb_bundle.claim_facts():
#
#   score in an inning  "up 2-0 in the 5th": a score a play in that inning
#                       PRODUCED ("make it", "put ... up", "took a 3-0 lead"),
#                       or one the game stood at during it ("led 3-0 in the 5th")
#   go-ahead / for good "took the lead in the 7th", "go-ahead homer in the 8th"
#   tie                 "tied it in the 9th"
#   runs in an inning   "a four-run 6th"
#   a scoring play      "Hicks's RBI single in the 5th": that batter scored a
#                       run in that inning; "two-RBI" no more than it scored
#   who made a score    "a Williamson single to make it 3-0": the play that
#                       made it 3-0 was that batter's
#   no runs at all      a run-scoring claim in an inning nobody scored in
#   walk-off, wire to wire, never trailed, came back from N down
#
# HIGH only when the sentence ties to exactly one of yesterday's MLB games and
# the facts contradict it. A claim the facts can't settle (several candidate
# games, no reconciled plays, a past game) is left alone: before this, nothing
# checked game flow at all, and a LOW asks the editor to blur a claim that may
# be right. Only sections that read as baseball are checked, so "took the
# lead in the third" about the Cardinals' football game is not.
# ---------------------------------------------------------------------------

_ORD_WORDS = {w: i + 1 for i, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth "
    "fourteenth fifteenth".split())}
_NOT_INNING = (r"(?!\s*-?\s*(?:place|seed|round|pick|quarter|period|half|straight|consecutive|time|times|"
               r"game|games|win|wins|loss|year|years|season|career|ranked|overall|start|title|series|team|"
               r"of\s+the\s+(?:season|year|night|postseason|series|playoffs)|homer|home\s+run|hit|batter|pitch|"
               r"save|appearance|meeting|matchup)\b)")
_INNING_RE = re.compile(
    r"\b(?:(?P<half>top|bottom)\s+(?:half\s+)?(?:of\s+)?(?:the\s+)?|(?P<pre>in|of|the|an?|during|by)\s+(?:the\s+)?)?"
    r"(?P<n>\d{1,2}(?:st|nd|rd|th)|" + "|".join(_ORD_WORDS) + r")\b(?P<inning>\s+inning)?" + _NOT_INNING,
    re.IGNORECASE)
_BASEBALL_RE = re.compile(r"\b(innings?|homer\w*|home\s+runs?|RBIs?|bullpen|pitch\w*|strikeouts?|struck\s+out|"
                          r"walk-?\s?off|ALCS|NLCS|ALDS|NLDS|World\s+Series|MLB|starter|reliever|"
                          r"singled|doubled|tripled|bases\s+loaded)\b", re.IGNORECASE)
_SCORE_PAIR_RE = re.compile(r"(?<![\d-])(\d{1,2})-(\d{1,2})(?![\d-])")
_NOT_A_SCORE_AFTER = re.compile(r"^\s*(?:series|in\s+the\s+series|lead\s+in\s+the\s+series|record|win|victory|"
                                r"loss|defeat|final|count|edge\s+in\s+the\s+series)\b", re.IGNORECASE)
_NOT_A_SCORE_BEFORE = re.compile(r"\b(?:series|ALCS|NLCS|ALDS|NLDS|World\s+Series|won|beat|beats|beating|topped|"
                                 r"edged|over)\s+(?:the\s+)?(?:[A-Z][\w.'-]*\s+){0,3}$")
# A score that isn't the score at that inning: one already overcome, or a count.
_EARLIER_SCORE_BEFORE = re.compile(r"\b(?:erase[sd]?|erasing|overc[ao]me|overcoming|blew|blown|blowing|squander\w*|"
                                   r"from|after)\s+(?:a\s+|an\s+|the\s+|their\s+|his\s+)?$", re.IGNORECASE)
_EARLIER_SCORE_AFTER = re.compile(r"^\s*(?:deficit|hole|count|slider|fastball|sinker|cutter|curveball|curve|"
                                  r"changeup|splitter|sweeper|heater|pitch|knuckle\w*)\b", re.IGNORECASE)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MADE_CUE = re.compile(r"\b(?:make|made|makes|making|put|puts|putting|gave|give|gives|giving|cut|cuts|cutting|"
                       r"pulled\s+within|stretched|extended|extends|pushed|took|takes|take|taking|grabbed|opened|"
                       r"tied\s+it\s+at|knotted\s+it\s+at)\b[^.;]*$", re.IGNORECASE)
_LEAD_CUE = re.compile(r"\b(?:up|ahead|lead|led|leading|in\s+front)\b", re.IGNORECASE)
_TRAIL_CUE = re.compile(r"\b(?:down|behind|trailed|trailing|trail)\b", re.IGNORECASE)
_GO_AHEAD_RE = re.compile(
    r"\b(?:took|take|takes|taking|grabbed|grabs|seized|seizes|regained|retook|reclaimed)\s+(?:the|a)\s+lead\b|"
    r"\bwent\s+ahead\b|\bgo-ahead\b|\bpulled\s+ahead\b|\bmoved\s+ahead\b", re.IGNORECASE)
_TIE_RE = re.compile(r"\b(?:tied|ties|knotted|evened|leveled|levelled)\s+(?:it|things|the\s+game|the\s+score)\b|"
                     r"\btying\s+(?:homer|home\s+run|hit|single|double|triple|sac(?:rifice)?\s+fly)\b", re.IGNORECASE)
_N_RUN_INNING_RE = re.compile(r"\b" + _N + r"-run\s+(?:top\s+of\s+the\s+|bottom\s+of\s+the\s+)?(\d{1,2}(?:st|nd|rd|th)|"
                              + "|".join(_ORD_WORDS) + r")\b(?!\s+(?:lead|win|deficit))", re.IGNORECASE)
_HIT = (r"single|singled|singles|double|doubled|doubles|triple|tripled|triples|homer|homered|homers|home\s+run|"
        r"blast|shot|grand\s+slam|walk|walked|sac(?:rifice)?\s+fly|groundout|grounder|grounded\s+out|bunt")
_RBI_CUE = re.compile(r"\bRBIs?\b|\bdr(?:ove|ives|iving)\s+in\b|\bknock(?:ed|s)\s+in\b|\bplated\b|"
                      r"\brun-scoring\b|\bscored\s+on\b|\bgrand\s+slam\b|\bhomer\w*\b|\bhome\s+run\b|\bsac(?:rifice)?\s+fly\b|"
                      r"\b" + _N + r"-run\s+(?:" + _HIT + r")\b", re.IGNORECASE)
_RBI_COUNT_RE = re.compile(r"\b" + _N + r"-(?:RBI|run)\s+(?:" + _HIT + r")\b|\b" + _N + r"\s+RBIs?\b", re.IGNORECASE)
_WALKOFF_RE = re.compile(r"\bwalk-?\s?off\b", re.IGNORECASE)
_WIRE_RE = re.compile(r"\bwire[- ]to[- ]wire\b|\bnever\s+trailed\b|\bled\s+(?:the\s+)?(?:whole|entire)\s+(?:way|game)\b",
                      re.IGNORECASE)
_COMEBACK_RE = re.compile(r"\b(?:came|comes|coming|rallied|rallies|rallying|stormed|fought|climbed)\s+back\s+from\s+"
                          r"(?:a\s+)?" + _N + r"-run\s+(?:deficit|hole)|\berased?\s+(?:a\s+)?" + _N +
                          r"-run\s+(?:deficit|lead)|\bfrom\s+" + _N + r"\s+(?:runs\s+)?down\b", re.IGNORECASE)
_ELSEWHERE_RE = re.compile(r"\bGame\s+\d\b|\b(?:19|20)\d{2}\b|\blast\s+(?:night'?s?\s+)?(?:week|year|season|time)\b|"
                           r"\bearlier\s+in\s+the\s+series\b", re.IGNORECASE)


_SHARED_MLB_CITIES = {"new york", "chicago", "los angeles"}
_SCORED_RE = re.compile(r"\bscor(?:ed|es|ing)\b|\bcame\s+(?:around|home)\s+to\s+score\b|\bcrossed\s+the\s+plate\b",
                        re.IGNORECASE)


def _ascii(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _num(s: str) -> int:
    s = s.lower()
    return _NUM_WORDS[s] if s in _NUM_WORDS else int(s)


def _ord(n: int) -> str:
    return f"{n}{'th' if n % 100 in (11, 12, 13) else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _inning_no(token: str) -> int:
    t = token.lower()
    return _ORD_WORDS[t] if t in _ORD_WORDS else int(re.match(r"\d+", t).group(0))


def _innings_in(sentence: str) -> list[tuple[int, int]]:
    """(inning, position) for every inning a sentence names. A word ordinal
    ("the fifth") counts only with "inning" after it, a half before it, or
    "in the" before it and the clause ending after it; "the third straight
    win" and "for the second time" are not innings."""
    out = []
    for m in _INNING_RE.finditer(sentence):
        word = m.group("n").lower() in _ORD_WORDS
        if not (m.group("half") or m.group("pre") or m.group("inning")):
            continue                                    # a bare "5th": only "5th inning" or "in the 5th"
        if re.search(r"\b(?:before|until|till|through|after|since)\s+(?:the\s+)?$", sentence[:m.start()],
                     re.IGNORECASE):
            continue                                    # "Six RBI before the second inning started": a boundary
        if word and not (m.group("inning") or m.group("half") or
                         (m.group("pre") or "").lower() == "in" and
                         re.match(r"\s*(?:$|[,.;:!?—–-]|(?:to|on|with|when|and|off|after|before|as)\b)",
                                  sentence[m.end():])):
            continue
        n = _inning_no(m.group("n"))
        if 1 <= n <= 25:
            out.append((n, m.start("n")))
    return out


def _nearest(innings: list[tuple[int, int]], pos: int) -> int | None:
    return min(innings, key=lambda x: abs(x[1] - pos))[0] if innings else None


def _hn(play: dict) -> str:
    return f"{play['half']} {_ord(play['inning'])}"


def _batter_hits(sentence: str, roster: set[str]) -> list[tuple[str, int]]:
    """(surname, position) for each player in the game credited with a hit or
    play in the sentence: "Hicks dropped a two-RBI single", "a Ben Williamson
    single", "Hicks: 2-RBI single". A runner who scored is not a batter."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", sentence).encode("ascii", "ignore").decode()
    out = []
    for name in roster:
        for m in re.finditer(r"\b" + re.escape(name) + r"(?:['’]s)?\b\W{0,2}((?:\s+\S+){0,3}?)\s+(?:" + _HIT + r")\b",
                             plain, re.IGNORECASE):
            if re.search(r"\b(?:scored|came|on|after|ahead|behind|for|off|against)\b", m.group(1), re.IGNORECASE):
                continue                                  # "Simpson came around on a ... single"
            out.append((name, m.start()))
    return out


def _flow_claims(sentence: str, f: dict, side: str | None) -> list[tuple[str, str, str]]:
    """(verdict, what the claim said, the fact) for every game-flow claim in
    one sentence about game `f`. `side` is the one team the sentence names,
    or None."""
    out: list[tuple[str, str, str]] = []

    def abbr(s):
        return f["teams"][s]["abbr"] or f["teams"][s]["name"]

    def sc(a, h):
        if a == h:
            return f"tied {a}-{h}"
        return f"{abbr('away' if a > h else 'home')} {max(a, h)}-{min(a, h)}"

    innings = _innings_in(sentence)
    plays = f["plays"]

    # A score in an inning.
    for m in _SCORE_PAIR_RE.finditer(sentence):
        x, y = int(m.group(1)), int(m.group(2))
        if x + y > 40 or _NOT_A_SCORE_AFTER.search(sentence[m.end():]) or \
                _NOT_A_SCORE_BEFORE.search(sentence[:m.start()]):
            continue
        inn = _nearest(innings, m.start())
        if inn is None:
            continue
        pre = sentence[max(0, m.start() - 60):m.start()]
        if _EARLIER_SCORE_BEFORE.search(pre) or _EARLIER_SCORE_AFTER.search(sentence[m.end():]):
            continue                                    # "erased a 4-0 deficit", "a 2-2 slider": not then
        made = bool(_MADE_CUE.search(pre))

        # Unordered on purpose: the team a sentence names is often not the
        # subject ("Down 7-5 heading into the ninth against the Phillies" is
        # the Marlins down), so who led is not read off it.
        def fits(st, x=x, y=y):
            return sorted(st) == sorted((x, y))
        pool = f["made"].get(inn, set()) if made else f["moments"].get(inn, [])
        if any(fits(st) for st in pool):
            out.append(("confirm", m.group(0), ""))
            continue
        if made and not plays and any(fits(st) for st in f["moments"].get(inn, [])):
            continue                                    # no plays: half-level only, can't tell
        where = sorted(i for i, sts in f["made"].items() if any(fits(st) for st in sts))
        fact = (f"the score became {sc(*next(st for st in f['made'][where[0]] if fits(st)))} in the "
                + " and ".join(_ord(w) for w in where)) if where else f"the score was never {x}-{y}"
        out.append(("contradict", f"{m.group(0)} in the {_ord(inn)}", fact))
    # Go-ahead, for good.
    for m in _GO_AHEAD_RE.finditer(sentence):
        inn = _nearest(innings, m.start())
        if inn is None or re.match(r"\s+(?:a|the)\s+\d", sentence[m.end():]):
            continue                                    # "took a 3-0 lead": the score check has it
        good = bool(re.search(r"\bfor\s+good\b", sentence[m.start():m.end() + 40], re.IGNORECASE))
        if good:
            fg = f["for_good"]
            ok = fg is not None and fg[1] == inn and (side is None or fg[0] == side)
            fact = f"{abbr(fg[0])} took the lead for good in the {_ord(fg[1])}" if fg else "no lead held"
        else:
            ok = any(i == inn and (side is None or s == side) for s, i in f["go_ahead"])
            ga = [f"{abbr(s)} in the {_ord(i)}" for s, i in f["go_ahead"] if side is None or s == side]
            fact = ("the lead was taken only " + ", ".join(ga)) if ga else \
                   f"{abbr(side) if side else 'nobody'} never took the lead"
        out.append(("confirm" if ok else "contradict", f"{m.group(0)} in the {_ord(inn)}", fact))
    # Tie.
    for m in _TIE_RE.finditer(sentence):
        inn = _nearest(innings, m.start())
        if inn is None:
            continue
        if any(st[0] == st[1] for st in f["made"].get(inn, set())):
            out.append(("confirm", m.group(0), ""))
        elif plays:
            ties = sorted(i for i, sts in f["made"].items() if any(st[0] == st[1] for st in sts))
            out.append(("contradict", f"{m.group(0)} in the {_ord(inn)}",
                        "a play tied it only in the " + " and ".join(_ord(i) for i in ties)
                        if ties else "the game was never tied after the first run"))
    # Runs in an inning.
    for m in _N_RUN_INNING_RE.finditer(sentence):
        n, inn = _num(m.group(1)), _inning_no(m.group(2))
        got = {s: f["runs"].get((s, inn)) for s in ("away", "home")}
        ok = any(v == n for s, v in got.items() if side is None or s == side)
        fact = "; ".join(f"{abbr(s)} scored {v} in the {_ord(inn)}" for s, v in got.items() if v is not None)
        out.append(("confirm" if ok else "contradict", m.group(0), fact or f"there was no {_ord(inn)}"))
    # A batter's scoring play in an inning, and who made a score.
    hits = _batter_hits(sentence, f["roster"])
    rbi = bool(_RBI_CUE.search(sentence))
    for name, pos in hits:
        mine = [p for p in plays if p["surname"] == name]
        inn = _nearest(innings, pos)
        if inn is None or not rbi or not plays:
            continue
        here = [p for p in mine if p["inning"] == inn]
        if here:
            cm = _RBI_COUNT_RE.search(sentence[pos:])
            claimed = _num(next(g for g in cm.groups() if g)) if cm else None
            most = max(p["runs"] for p in here)
            if claimed and claimed > most:
                out.append(("contradict", cm.group(0), f"{name.title()}'s play in the {_ord(inn)} scored {most} run(s)"))
            else:
                out.append(("confirm", name, ""))
        elif mine:
            out.append(("contradict", f"{name.title()} in the {_ord(inn)}",
                        f"{name.title()}'s run-scoring play(s) came in the " +
                        ", ".join(f"{_hn(p)} ({sc(p['away'], p['home'])})" for p in mine)))
        elif not any(f["runs"].get((s, inn)) for s in ("away", "home")):
            out.append(("contradict", f"{name.title()} in the {_ord(inn)}", f"nobody scored in the {_ord(inn)}"))
    if hits and plays:
        for m in _SCORE_PAIR_RE.finditer(sentence):
            x, y = int(m.group(1)), int(m.group(2))
            if not _MADE_CUE.search(sentence[max(0, m.start() - 60):m.start()]):
                continue
            makers = [p for p in plays if sorted((p["away"], p["home"])) == sorted((x, y))]
            if len(makers) != 1 or makers[0]["surname"] is None:
                continue
            p = makers[0]
            if any(n == p["surname"] for n, _ in hits):
                out.append(("confirm", m.group(0), ""))
            else:
                out.append(("contradict", f"{', '.join(n.title() for n, _ in hits)} making it {x}-{y}",
                            f"{sc(p['away'], p['home'])} came on {p['surname'].title()}'s play in the {_hn(p)}"))
    # A run-scoring claim in an inning nobody scored in.
    if rbi and not hits:
        for inn, _ in innings:
            if inn <= f["innings"] and not any(f["runs"].get((s, inn)) for s in ("away", "home")):
                out.append(("contradict", f"a run in the {_ord(inn)}", f"nobody scored in the {_ord(inn)}"))
    # Who scored: "Mangum walked, scored on a wild pitch" (a real Pass 2 replay
    # of ALCS G5; it was DeLuca's bases-loaded walk, Díaz scoring). The player
    # named nearest before the verb must have crossed the plate in this game.
    if plays and f.get("scorers") is not None and \
            not re.search(r"\b(?:didn'?t|never|without|not|no)\b[^.;]*\bscor", sentence, re.IGNORECASE):
        plain = _ascii(sentence)
        for m in _SCORED_RE.finditer(plain):
            after = plain[m.end():]
            if re.match(r"\s+(?:[A-Z]|a\b|an\b|the\b|two\b|three\b|four\b|\d|runs?\b|him\b|both\b)", after):
                continue                                # transitive: "Hicks's single scored Simpson"
            near = None
            for w in reversed(re.findall(r"[A-Za-z'’]+", plain[max(0, m.start() - 60):m.start()])[-6:]):
                if re.search(r"['’]s$", w):
                    break                               # "Hicks's single ... scored": not the runner
                if w.lower() in f["roster"]:
                    near = w.lower()
                    break
            if near is None:
                # A full name as the subject, with only lowercase words between it
                # and the verb ("Jake Mangum walked, scored"), of someone not in
                # this game's box score at all.
                pm = re.search(r"\b([A-Z][a-z]+)\s+([A-Z][a-zA-Z'-]+)((?:,?\s+[a-z]+){0,3},?\s*)$",
                               plain[max(0, m.start() - 60):m.start()])
                team_words = {w for s in ("away", "home") for w in _tokens(f["teams"][s]["name"])}
                if pm and pm.group(1).lower() not in team_words | _NOT_A_TEAM and \
                        pm.group(2).lower() not in team_words and pm.group(1) not in ("Game", "World"):
                    out.append(("contradict", f"{pm.group(1)} {pm.group(2)} scored",
                                f"{pm.group(2)} is not in this game's box score"))
                continue
            if near in f["scorers"]:
                out.append(("confirm", f"{near.title()} scored", ""))
            else:
                who = ", ".join(f"{s.title()} ({_hn(p)})" for p in plays for s in p["scorers"])
                out.append(("contradict", f"{near.title()} scored", f"{near.title()} did not score; the runs "
                            f"were scored by {who}"))
    # Walk-off, wire to wire, comebacks.
    win = f["winner"]
    if _WALKOFF_RE.search(sentence):
        # Whether there WAS one, not whose: "Waldschmidt Walk-Off Sinks
        # Dodgers" names the team that lost it.
        fact = (f"no walk-off: {abbr(win)}, the road team, won it in the top of the {_ord(f['innings'])}"
                if win == "away" else
                f"no walk-off: {abbr(win)} already led going into the bottom of the {_ord(f['innings'])}")
        out.append(("confirm" if f["walk_off"] else "contradict", "walk-off", fact))
    m = _WIRE_RE.search(sentence)
    if m:
        who = side or win
        never_trailed = f["max_deficit"][who] == 0
        ok = (f["wire_to_wire"] and who == win) if "wire" in m.group(0).lower() else never_trailed
        out.append(("confirm" if ok else "contradict", m.group(0),
                    f"{abbr(who)} trailed by as many as {f['max_deficit'][who]}" if not never_trailed
                    else f"{abbr(who)} did not lead from the first run on"))
    for m in _COMEBACK_RE.finditer(sentence):
        n = _num(next(g for g in m.groups() if g))
        who = side or win
        ok = f["max_deficit"][who] >= n
        out.append(("confirm" if ok else "contradict", m.group(0),
                    f"{abbr(who)} trailed by at most {f['max_deficit'][who]}"))
    return out


def check_mlb_flow_claims(own_text: str, game_state: dict, outcomes: list | None = None) -> list[str]:
    """`own_text`: one section's own prose (no tweets), one block per line.
    `outcomes`, if given, collects (level, quoted, sentence, kind, figure)
    for every sentence with a game-flow claim, confirmed ones included."""
    import mlb_bundle
    facts = mlb_bundle.claim_facts(game_state or {})
    if not facts or not _BASEBALL_RE.search(own_text):
        return []
    by_name, twice = {}, set()
    for f in facts:
        for s in ("away", "home"):
            n = f["teams"][s]["name"]
            if n in by_name:
                twice.add(n)                            # a doubleheader: which game is unknowable
            by_name[n] = (f, s)
    for n in twice:
        by_name.pop(n)
    facts = [f for f in facts if not {f["teams"][s]["name"] for s in ("away", "home")} & twice]
    idx = _alias_index([{"team": n, "sport": "mlb", "opponent": ""} for n in by_name])
    strong_idx = dict(idx)
    # Recaps name teams by city ("Tampa Bay 3, Cleveland 1"); not a city two
    # MLB teams share.
    for f in facts:
        for s in ("away", "home"):
            t = f["teams"][s]
            city = _tokens(t["name"])[:-len(_tokens(t["nick"]))] if t["nick"] else []
            if city and " ".join(city) not in _SHARED_MLB_CITIES:
                idx.setdefault(tuple(city), set()).add(t["name"])

    def games_of(text):
        return {by_name[n][0]["game_id"]: by_name[n][0] for n in _named(text, idx) if n in by_name}

    yday = (game_state or {}).get("yesterday_date") or ""
    try:
        from datetime import date as _date
        yday_name = _WEEKDAYS[_date.fromisoformat(yday).weekday()]
    except ValueError:
        yday_name = None
    section_games = games_of(own_text)
    flags = []
    for sentence in _sentences(own_text):
        if _ELSEWHERE_RE.search(sentence):
            continue                                    # another game, another year
        days = {d for d in _WEEKDAYS if re.search(r"\b" + d + r"\b", sentence, re.IGNORECASE)}
        if days and (yday_name is None or days != {yday_name}):
            continue                                    # "late Tuesday night": another day's game
        # Unless the sentence names an MLB team by name or nickname, it must
        # itself read as baseball: "Wemby went 1-5 in the fourth" in a
        # section that mentions Cleveland is a quarter, not an inning.
        if not (_named(sentence, strong_idx) & set(by_name)) and not _BASEBALL_RE.search(sentence) and \
                not re.search(r"\binnings?\b|\b(?:top|bottom)\s+(?:half\s+)?of\b|\b\d{1,2}(?:st|nd|rd|th)\b|"
                              r"\b(?:single|double|triple|sac(?:rifice)?\s+fly)s?\b(?!-)", sentence, re.IGNORECASE):
            continue
        named = [n for n in _named(sentence, idx) if n in by_name]
        cands = games_of(sentence) or section_games
        if not cands:
            # A sentence naming no team: a batter only one game has.
            cands = {f["game_id"]: f for f in facts if _batter_hits(sentence, f["roster"])}
        if len(cands) != 1:
            continue
        f = next(iter(cands.values()))
        sides = {by_name[n][1] for n in named if by_name[n][0] is f}
        side = next(iter(sides)) if len(sides) == 1 else None
        results = _flow_claims(sentence, f, side)
        if not results:
            continue
        quoted = _quote(sentence)
        bad = [(said, fact) for v, said, fact in results if v == "contradict"]
        # What a tweet would have to carry to source the sentence: one of its
        # scores, or an inning it names.
        figs = [r"(?<![\d-])" + re.escape(m.group(0)) + r"(?![\d-])" for m in _SCORE_PAIR_RE.finditer(sentence)]
        figs += [r"\b" + _ord(n) + r"\b" for n, _ in _innings_in(sentence)]
        fig = "|".join(figs) or r"(?!x)x"
        if not bad:
            if outcomes is not None:
                outcomes.append(("confirmed", quoted, sentence, "game flow", fig))
            continue
        t = f["teams"]
        why = "; ".join(f"{s} -> but {x}" for s, x in bad).replace('"', "'").replace("--", "-")
        flags.append(
            f'\n<!-- FACT FLAG [HIGH]: "{quoted}" contradicts the line score of {t["away"]["name"]} @ '
            f'{t["home"]["name"]}: {why} (ESPN; the MLB GAME FLOW block has the full sequence). Correct it '
            f'to the block, or cut it. -->')
        if outcomes is not None:
            outcomes.append(("HIGH", quoted, sentence, "game flow", fig))
    return flags

# ---------------------------------------------------------------------------
# SLA-112: after the editor. Pass 3 flags a history claim and the editor is
# ASKED to fix it; nothing checked that it did. This re-runs the same check
# on the final draft and reports what is still standing. It never rewrites
# prose: a claim the editor left in is flagged in the archived draft, the run
# log and the morning email. Cutting the sentence instead is a switch
# (SLAP_HISTORY_AUTOCUT=1), off by default, for days nobody reviews before
# the noon publish.
# ---------------------------------------------------------------------------

AUTOCUT_ENV = "SLAP_HISTORY_AUTOCUT"


def _figures(sentence: str) -> set[str]:
    """Patterns for the numbers a history claim stakes itself on: years, and
    counts of years ("53-year", "in 27 years"), a count only as a count of
    years, so a tweet's "53 points" never sources a "53-year drought"."""
    figs = {r"(?<!\d)" + y + r"(?!\d)" for y in re.findall(r"\b(?:18|19|20)\d{2}\b", sentence)}
    figs |= {r"(?<!\d)" + n + r"[- ](?:years?|seasons?)\b"
             for n in re.findall(r"\b(\d{1,3})[- ](?:years?|seasons?)\b", sentence, re.IGNORECASE)}
    return figs


def _tweet_text(section_html: str) -> str:
    return " ".join(strip_tags(b) for b in re.findall(r"<blockquote\b.*?</blockquote>", section_html,
                                                       flags=re.IGNORECASE | re.DOTALL))


def _cut_sentence(section_html: str, sentence: str) -> tuple[str, bool]:
    """Remove one sentence from the paragraph that holds it. Only where it
    can be done cleanly: the sentence sits in the HTML as plain text, or is
    the whole paragraph. Anything else is left and reported as not cut."""
    s = sentence.strip()
    for m in re.finditer(r"<p\b[^>]*>(.*?)</p>", section_html, flags=re.IGNORECASE | re.DOTALL):
        inner = m.group(1)
        if strip_tags(inner).strip() == s:
            return section_html[:m.start()] + section_html[m.end():], True
        if s in inner and s in strip_tags(inner):
            new_inner = re.sub(r"\s{2,}", " ", inner.replace(s, "", 1)).strip()
            return section_html[:m.start(1)] + new_inner + section_html[m.end(1):], True
    return section_html, False


def final_history_check(html: str, game_state: dict, autocut: bool | None = None) -> tuple[str, list[dict]]:
    """Re-check the FINAL draft's history claims. Returns the draft (with a
    note after each section heading, or the sentence cut when `autocut`) and
    one entry per claim still standing: {level, section, sentence, cut}.

    A claim whose year or count a tweet in the same section carries is
    sourced (editor Check 8, rule 1) and is not reported."""
    if autocut is None:
        import os
        autocut = os.environ.get(AUTOCUT_ENV, "").strip().lower() in ("1", "true", "yes")
    report: list[dict] = []
    parts = []
    for heading, sec in split_into_sections(html):
        if heading == "__preamble__":
            parts.append(sec)
            continue
        outcomes: list = []
        check_history_claims(own_text(sec), game_state, outcomes)
        tweets = _tweet_text(sec)
        for level, quoted, sentence, *_ in outcomes:
            if level == "confirmed":
                continue
            if any(re.search(f, tweets, re.IGNORECASE) for f in _figures(sentence)):
                continue                                # a tweet in this section carries it
            cut = False
            if autocut:
                sec, cut = _cut_sentence(sec, sentence)
            if not cut:
                sec = inject_flag_after_heading(
                    sec, f'\n<!-- ⚠ HISTORY CLAIM LEFT IN BY THE EDITOR [{level}]: "{quoted}" -->')
            report.append({"level": level, "section": heading, "sentence": quoted, "cut": cut})
        # SLA-148: NHL game-flow claims, the same way. A tweet in the section
        # carrying one of the sentence's scores sources it.
        nhl: list = []
        check_nhl_flow_claims(own_text(sec), game_state, nhl)
        for level, quoted, sentence, kind, fig in nhl:
            if level == "confirmed" or re.search(fig, tweets, re.IGNORECASE):
                continue
            cut = False
            if autocut:
                sec, cut = _cut_sentence(sec, sentence)
            if not cut:
                sec = inject_flag_after_heading(
                    sec, f'\n<!-- ⚠ NHL GAME FLOW CLAIM LEFT IN BY THE EDITOR [{level}]: "{quoted}" -->')
            report.append({"level": level, "section": heading, "sentence": quoted, "cut": cut, "kind": kind})
        # SLA-146: college basketball game-flow claims, the same way.
        ncaamb: list = []
        check_ncaamb_flow_claims(own_text(sec), game_state, ncaamb)
        for level, quoted, sentence, kind, fig in ncaamb:
            if level == "confirmed" or re.search(fig, tweets, re.IGNORECASE):
                continue
            cut = False
            if autocut:
                sec, cut = _cut_sentence(sec, sentence)
            if not cut:
                sec = inject_flag_after_heading(
                    sec, f'\n<!-- ⚠ COLLEGE BASKETBALL GAME FLOW CLAIM LEFT IN BY THE EDITOR [{level}]: "{quoted}" -->')
            report.append({"level": level, "section": heading, "sentence": quoted, "cut": cut, "kind": kind})
        # SLA-147: NBA game-flow claims, the same way. A tweet in the section
        # carrying one of the sentence's scores, runs or streaks sources it.
        nba: list = []
        check_nba_flow_claims(own_text(sec), game_state, nba)
        for level, quoted, sentence, kind, fig in nba:
            if level == "confirmed" or re.search(fig, tweets, re.IGNORECASE):
                continue
            cut = False
            if autocut:
                sec, cut = _cut_sentence(sec, sentence)
            if not cut:
                sec = inject_flag_after_heading(
                    sec, f'\n<!-- ⚠ NBA GAME FLOW CLAIM LEFT IN BY THE EDITOR [{level}]: "{quoted}" -->')
            report.append({"level": level, "section": heading, "sentence": quoted, "cut": cut, "kind": kind})
        # SLA-117: football rank and record claims, the same way. A tweet in
        # the section carrying the same rank or record sources it.
        football: list = []
        check_football_claims(own_text(sec), game_state, football)
        for level, quoted, sentence, kind, fig in football:
            if level == "confirmed" or re.search(fig, tweets, re.IGNORECASE):
                continue
            cut = False
            if autocut:
                sec, cut = _cut_sentence(sec, sentence)
            if not cut:
                sec = inject_flag_after_heading(
                    sec, f'\n<!-- ⚠ FOOTBALL {kind.upper()} CLAIM LEFT IN BY THE EDITOR [{level}]: "{quoted}" -->')
            report.append({"level": level, "section": heading, "sentence": quoted, "cut": cut, "kind": kind})
        # SLA-142: MLB game-flow claims, the same way. A tweet in the section
        # carrying one of the sentence's scores or innings sources it.
        mlb: list = []
        check_mlb_flow_claims(own_text(sec), game_state, mlb)
        for level, quoted, sentence, kind, fig in mlb:
            if level == "confirmed" or re.search(fig, tweets, re.IGNORECASE):
                continue
            cut = False
            if autocut:
                sec, cut = _cut_sentence(sec, sentence)
            if not cut:
                sec = inject_flag_after_heading(
                    sec, f'\n<!-- ⚠ MLB GAME FLOW CLAIM LEFT IN BY THE EDITOR [{level}]: "{quoted}" -->')
            report.append({"level": level, "section": heading, "sentence": quoted, "cut": cut, "kind": kind})
        parts.append(sec)
    return "".join(parts), report


# ---------------------------------------------------------------------------
# Check 3E: NHL game-flow claims vs the goal log (SLA-148)
#
# Each claim below is checked against nhl_bundle.claim_facts(), the same goal
# log the NHL GAME FLOW block shows the writer:
#
#   a score in a period  "up 2-0 in the second", "tied 2-2 after two",
#                        "led 3-1 entering the third"
#   go-ahead / for good  "took the lead in the third", "went ahead for good in OT"
#   tie                  "tied it in the second"
#   a scorer's goal      "Stankoven scored in the second": that player scored in
#                        that period
#   wire to wire         "never trailed", "led wire to wire"
#   comebacks            "came back from a two-goal deficit"
#
# HIGH only when the sentence ties to exactly one of yesterday's NHL games and
# the facts contradict it; a claim the facts can't settle is left alone (the
# SLA-142 rule). Only sections that read as hockey are checked, so "took the
# lead in the third" about a football game is not.
# ---------------------------------------------------------------------------

_NHL_GO_AHEAD_RE = re.compile(
    r"\b(?:took|take|takes|taking|grabbed|grabs|seized|seizes|regained|retook|reclaimed)\s+(?:the|a)\s+lead\b|"
    r"\bwent\s+ahead\b|\bgo-ahead\b|\bpulled\s+ahead\b|\bmoved\s+ahead\b", re.IGNORECASE)
_NHL_TIE_RE = re.compile(r"\b(?:tied|ties|knotted|evened|leveled|levelled)\s+(?:it|things|the\s+game|the\s+score)\b|"
                         r"\btying\s+goal\b", re.IGNORECASE)
_NHL_WIRE_RE = re.compile(r"\bwire[- ]to[- ]wire\b|\bnever\s+trailed\b|\bled\s+(?:the\s+)?(?:whole|entire)\s+(?:way|game)\b",
                          re.IGNORECASE)
_NHL_LEAD_CUE = re.compile(r"\b(?:up|ahead|lead|led|leading|in\s+front)\b", re.IGNORECASE)
_NHL_TRAIL_CUE = re.compile(r"\b(?:down|behind|trailed|trailing|trail)\b", re.IGNORECASE)
_NHL_SCORE_PAIR_RE = re.compile(r"(?<![\d-])(\d{1,2})-(\d{1,2})(?![\d-])")
_NHL_SCORE_CUE = re.compile(r"(?:up|ahead|lead|led|leading|down|behind|trail\w*|tied|tie|ties|make|made|makes|put|"
                            r"cut|extend\w*|stretch\w*|pull\w*|knot\w*|grab\w*|took|take|opened|jumped|"
                            r"the\s+score|it\s+was)\b", re.IGNORECASE)
_NHL_NOT_A_SCORE_AFTER = re.compile(r"^\s*(?:series|in\s+the\s+series|lead\s+in\s+the\s+series|record|win|victory|"
                                    r"loss|defeat|final|count|edge\s+in\s+the\s+series)\b", re.IGNORECASE)
_NHL_ELSEWHERE_RE = re.compile(r"\bGame\s+\d\b|\b(?:19|20)\d{2}\b|\blast\s+(?:night'?s?\s+)?(?:week|year|season|time)\b|"
                               r"\bearlier\s+in\s+the\s+series\b", re.IGNORECASE)


def _nhl_ascii(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _nhl_num(s: str) -> int:
    s = s.lower()
    return _NUM_WORDS[s] if s in _NUM_WORDS else int(s)


_NHL_TWO_WORD_NICKS = {"golden knights", "blue jackets", "maple leafs", "red wings"}
_NHL_SHARED_CITIES = {"new york", "los angeles"}
_HOCKEY_RE = re.compile(r"\b(?:NHL|Stanley\s+Cup|power[- ]play|shorthanded|empty[- ]net|puck|goalie|goaltender|"
                        r"shootout|hat\s+trick|shots?\s+on\s+goal|slap\s?shot|wrist\s?shot|backhand|"
                        r"overtime|periods?|goals?|saves?)\b", re.IGNORECASE)
_PERIOD_RE = re.compile(
    r"\b(?:(?:in|during|of|after|through|entering|into|by)\s+(?:the\s+)?(?P<d>[123])(?:st|nd|rd)\b"
    r"(?!\s*[- ]?(?:place|seed|round|straight|consecutive|time|game|win|loss))"
    r"|(?P<w>first|second|third)\s+period"
    r"|(?P<ot>(?:(?P<k>double|triple)[- ]?)?(?:overtime|OT)\b)"
    r"|(?P<n>[23])OT\b)", re.IGNORECASE)
_PERIOD_WORDS = {"first": 1, "second": 2, "third": 3}
_COMEBACK_NHL_RE = re.compile(r"\b(?:came|comes|coming|rallied|rallies|rallying|clawed|fought|climbed)\s+back\s+from\s+"
                              r"(?:a\s+)?" + _N + r"-goal\s+(?:deficit|hole)|"
                              r"\berased?\s+(?:a\s+)?" + _N + r"-goal\s+(?:deficit|lead)|"
                              r"\bfrom\s+" + _N + r"\s+goals\s+down\b", re.IGNORECASE)


def _periods_in(sentence: str) -> list[tuple[int, int, str]]:
    """(period, position, cue) for each period a sentence names. cue is "end"
    ("after the second"), "enter" ("entering the third") or "in". An overtime
    mention is the first overtime (4), or the one named (2OT = 5)."""
    out = []
    for m in _PERIOD_RE.finditer(sentence):
        if m.group("d"):
            p = int(m.group("d"))
        elif m.group("w"):
            p = _PERIOD_WORDS[m.group("w").lower()]
        elif m.group("ot"):
            k = (m.group("k") or "").lower()
            p = 3 + {"double": 2, "triple": 3}.get(k, 1)
        else:
            p = 3 + int(m.group("n"))
        lead_word = re.match(r"\s*(\w+)", sentence[max(0, m.start() - 0):]).group(1).lower() if m.group("d") else ""
        before = sentence[max(0, m.start() - 22):m.start()].lower()
        if lead_word == "after" or re.search(r"\b(?:after|by\s+the\s+end\s+of)\s+(?:the\s+)?$", before):
            cue = "end"
        elif lead_word in ("entering", "into") or re.search(r"\b(?:entering|into)\s+(?:the\s+)?$", before):
            cue = "enter"
        else:
            cue = "in"
        out.append((p, m.start(), cue))
    return out


def _nearest_period(periods: list[tuple[int, int, str]], pos: int):
    return min(periods, key=lambda x: abs(x[1] - pos)) if periods else None


def _pname(p: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd", 4: "OT"}.get(p, f"{p - 3}OT")


def _nhl_claims(sentence: str, f: dict, side: str | None) -> list[tuple[str, str, str]]:
    """(verdict, what the claim said, the fact) for every game-flow claim in
    one sentence about NHL game `f`. `side` is the one team the sentence names,
    or None."""
    out: list[tuple[str, str, str]] = []

    def abbr(s):
        return f["teams"][s]["abbr"] or f["teams"][s]["name"]

    def sc(a, h):
        return f"tied {a}-{h}" if a == h else f"{abbr('away' if a > h else 'home')} {max(a, h)}-{min(a, h)}"

    periods = _periods_in(sentence)
    end_score: dict[int, tuple[int, int]] = {}
    cur = (0, 0)
    for p in range(1, f["periods"] + 1):
        for g in f["goals"]:
            if g["period"] == p:
                cur = (g["away"], g["home"])
        end_score[p] = cur

    # A score in a period.
    for m in _NHL_SCORE_PAIR_RE.finditer(sentence):
        x, y = int(m.group(1)), int(m.group(2))
        if x + y > 25 or _NHL_NOT_A_SCORE_AFTER.search(sentence[m.end():]) or \
                re.search(r"\b(?:series|won|beat|beats|final)\s+(?:the\s+\w+\s+)?$", sentence[:m.start()], re.IGNORECASE):
            continue
        near = _nearest_period(periods, m.start())
        if near is None or near[0] > f["periods"]:
            continue
        p, _, cue = near
        pre = sentence[max(0, m.start() - 60):m.start()]
        if tuple(sorted((x, y))) in f["series_pairs"]:
            continue                                    # "take a 2-1 lead": the series, not the game
        if cue == "in" and (tuple(sorted((x, y))) == f["final"] or not _NHL_SCORE_CUE.search(pre)):
            continue                                    # "beat Colorado 5-3", "1-3 in overtime": not a period score
        want = None
        if side and _NHL_LEAD_CUE.search(pre):
            want = (max(x, y), min(x, y))
        elif side and _NHL_TRAIL_CUE.search(pre):
            want = (min(x, y), max(x, y))

        def fits(st, want=want, x=x, y=y):
            if want is not None:
                return (st if side == "away" else (st[1], st[0])) == want
            return sorted(st) == sorted((x, y))
        if cue == "end":
            pool = [end_score[p]]
        elif cue == "enter":
            pool = [end_score[p - 1]] if p > 1 else [(0, 0)]
        else:
            pool = sorted(f["scores"][p])
        label = {"end": f"after the {_pname(p)}", "enter": f"entering the {_pname(p)}",
                 "in": f"in the {_pname(p)}"}[cue]
        if any(fits(st) for st in pool):
            out.append(("confirm", m.group(0), ""))
        else:
            out.append(("contradict", f"{m.group(0)} {label}",
                        f"the score {label} was " + " / ".join(sc(*st) for st in pool)))
    # Go-ahead, for good.
    for m in _NHL_GO_AHEAD_RE.finditer(sentence):
        near = _nearest_period(periods, m.start())
        if near is None or re.match(r"\s+(?:a|the)\s+\d", sentence[m.end():]):
            continue
        p = near[0]
        good = bool(re.search(r"\bfor\s+good\b", sentence[m.start():m.end() + 40], re.IGNORECASE))
        if good:
            fg = f["for_good"]
            ok = fg is not None and fg[1] == p and (side is None or fg[0] == side)
            fact = f"{abbr(fg[0])} took the lead for good in the {_pname(fg[1])}" if fg else "no lead held (shootout)"
        else:
            ok = any(i == p and (side is None or s == side) for s, i in f["go_ahead"])
            ga = [f"{abbr(s)} in the {_pname(i)}" for s, i in f["go_ahead"] if side is None or s == side]
            fact = ("the lead was taken only " + ", ".join(ga)) if ga else \
                   f"{abbr(side) if side else 'nobody'} never took the lead"
        out.append(("confirm" if ok else "contradict", f"{m.group(0)} in the {_pname(p)}", fact))
    # Tie.
    for m in _NHL_TIE_RE.finditer(sentence):
        near = _nearest_period(periods, m.start())
        if near is None:
            continue
        p = near[0]
        if any(i == p and (side is None or s == side) for s, i in f["ties"]):
            out.append(("confirm", m.group(0), ""))
        else:
            ties = [f"{abbr(s)} in the {_pname(i)}" for s, i in f["ties"] if side is None or s == side]
            out.append(("contradict", f"{m.group(0)} in the {_pname(p)}",
                        ("it was tied only by " + ", ".join(ties)) if ties else "the game was never tied by a goal"))
    # A scorer's goal in a period.
    if f["roster"] and periods:
        plain = _nhl_ascii(sentence)
        for name in f["roster"]:
            for m in re.finditer(r"\b" + re.escape(name) + r"(?:['’]s)?\b\W{0,2}((?:\s+\S+){0,3}?)\s+"
                                 r"(?:goal|scored|scores|netted|tallied|buried)\b", plain, re.IGNORECASE):
                if re.search(r"\b(?:assist\w*|helped|set\s+up|on|after|for|off|against|from|to|by)\b",
                             m.group(1), re.IGNORECASE):
                    continue                          # "Hall assisted on Blake's goal"
                # The period must follow the goal in the same clause ("scored in
                # the second"), not be named for another reason ("after
                # trailing 3-1 in the first").
                after = [x for x in periods if m.end() <= x[1] <= m.end() + 40]
                if not after:
                    continue
                gap = sentence[m.end():after[0][1]]
                if re.search(r"\b(?:after|trailing|trailed|down|when|while|as|but|and\s+\w+\s+(?:scored|added))\b|\d-\d|[.;]",
                             gap, re.IGNORECASE):
                    continue
                p = after[0][0]
                mine = [g for g in f["goals"] if g["scorer"] == name]
                if any(g["period"] == p for g in mine):
                    out.append(("confirm", name, ""))
                elif mine:
                    out.append(("contradict", f"{name.title()} scoring in the {_pname(p)}",
                                f"{name.title()}'s goal(s) came in the " + ", ".join(
                                    f"{_pname(g['period'])} ({sc(g['away'], g['home'])})" for g in mine)))
    # Wire to wire, never trailed.
    win = f["winner"]
    m = _NHL_WIRE_RE.search(sentence)
    if m and not f["shootout"]:
        who = side or win
        never_trailed = f["max_deficit"][who] == 0
        ok = (f["wire_to_wire"] and who == win) if "wire" in m.group(0).lower() else never_trailed
        out.append(("confirm" if ok else "contradict", m.group(0),
                    f"{abbr(who)} trailed by as many as {f['max_deficit'][who]}" if not never_trailed
                    else f"{abbr(who)} did not lead from the first goal on"))
    for m in _COMEBACK_NHL_RE.finditer(sentence):
        got = next((g for g in m.groups() if g), None)
        if got is None:
            continue
        n = _nhl_num(got)
        who = side or win
        out.append(("confirm" if f["max_deficit"][who] >= n else "contradict", m.group(0),
                    f"{abbr(who)} trailed by at most {f['max_deficit'][who]}"))
    return out


def check_nhl_flow_claims(own_text: str, game_state: dict, outcomes: list | None = None) -> list[str]:
    """`own_text`: one section's own prose (no tweets), one block per line.
    `outcomes`, if given, collects (level, quoted, sentence, kind, figure)
    for every sentence with a game-flow claim, confirmed ones included."""
    import nhl_bundle
    facts = nhl_bundle.claim_facts(game_state or {})
    if not facts or not _HOCKEY_RE.search(own_text):
        return []
    by_name, dup = {}, set()
    for f in facts:
        for s in ("away", "home"):
            n = f["teams"][s]["name"]
            if n in by_name:
                dup.add(n)                              # a team can't play twice in a night: ambiguous
            by_name[n] = (f, s)
    for n in dup:
        del by_name[n]
    if not by_name:
        return []
    idx = _alias_index([{"team": n, "sport": "nhl", "opponent": ""} for n in by_name])
    # Recaps name teams by city ("Nashville tied it ..."); not a city that
    # two teams or two sports' teams share.
    for name in by_name:
        toks = _tokens(name)
        k = 2 if " ".join(toks[-2:]) in _NHL_TWO_WORD_NICKS else 1
        city = toks[:-k]
        if city and " ".join(city) not in _NHL_SHARED_CITIES:
            idx.setdefault(tuple(city), set()).add(name)

    def games_of(text):
        return {by_name[n][0]["game_id"]: by_name[n][0] for n in _named(text, idx) if n in by_name}

    section_games = games_of(own_text)
    flags = []
    for sentence in _sentences(own_text):
        if _NHL_ELSEWHERE_RE.search(sentence):
            continue                                    # another game, another year
        named = [n for n in _named(sentence, idx) if n in by_name]
        cands = games_of(sentence) or section_games
        if len(cands) != 1:
            continue
        f = next(iter(cands.values()))
        sides = {by_name[n][1] for n in named if by_name[n][0] is f}
        side = next(iter(sides)) if len(sides) == 1 else None
        results = _nhl_claims(sentence, f, side)
        if not results:
            continue
        quoted = _quote(sentence)
        bad = [(said, fact) for v, said, fact in results if v == "contradict"]
        figs = [r"(?<![\d-])" + re.escape(m.group(0)) + r"(?![\d-])" for m in _NHL_SCORE_PAIR_RE.finditer(sentence)]
        fig = "|".join(figs) or r"(?!x)x"
        if not bad:
            if outcomes is not None:
                outcomes.append(("confirmed", quoted, sentence, "game flow", fig))
            continue
        t = f["teams"]
        why = "; ".join(f"{s} -> but {x}" for s, x in bad).replace('"', "'").replace("--", "-")
        flags.append(
            f'\n<!-- FACT FLAG [HIGH]: "{quoted}" contradicts the goal log of {t["away"]["name"]} @ '
            f'{t["home"]["name"]}: {why} (ESPN; the NHL GAME FLOW block has the full sequence). Correct it '
            f'to the block, or cut it. -->')
        if outcomes is not None:
            outcomes.append(("HIGH", quoted, sentence, "game flow", fig))
    return flags


# ---------------------------------------------------------------------------
# Check 3F: men's college basketball game-flow claims vs the play log (SLA-146)
#
# Each claim is checked against ncaamb_bundle.claim_facts(), the same play log
# the COLLEGE BASKETBALL GAME FLOW block shows the writer:
#
#   a score at a point   "led 38-36 at halftime", "tied 60-60 in the second half"
#   go-ahead / for good  "took the lead for good in the second half"
#   tie                  "tied it in the second half"
#   wire to wire         "never trailed", "led wire to wire"
#   comebacks            "came back from a 15-point deficit", "erased a 12-point hole"
#   overtime             "went to overtime" (it didn't), "double overtime" (it was one)
#
# HIGH only when the sentence ties to exactly one of yesterday's games that has
# a play log and the facts contradict it; anything the log can't settle is left
# alone, with no LOW (the SLA-142 rule). Only sections that read as basketball
# are checked, so "took the lead in the second half" about football is not.
# Not checked: who scored, and clock claims ("with 4 seconds left").
# ---------------------------------------------------------------------------

_HOOPS_RE = re.compile(r"\b(?:basketball|hoops|NCAA|March\s+Madness|halftime|free\s+throws?|three-pointers?|"
                       r"buzzer|Elite\s+Eight|Sweet\s+(?:16|Sixteen)|Final\s+Four|rebounds?|dunks?|"
                       r"overtime|double[- ]overtime)\b", re.IGNORECASE)
_HOOPS_HALF_END_RE = re.compile(
    r"\b(?:at|by|into)\s+(?:the\s+)?(?:half|halftime|break|intermission)\b|"
    r"\bhalftime\s+(?:lead|score|deficit)\b|\bafter\s+(?:the\s+)?first\s+half\b|"
    r"\b(?:entering|into|coming\s+out\s+of)\s+(?:the\s+)?(?:second\s+half|break|locker)", re.IGNORECASE)
_HOOPS_IN_RE = re.compile(
    r"\b(?:in|during|of)\s+(?:the\s+)?(?P<h>first|second)\s+half\b|"
    r"\b(?:in|during)\s+(?P<ot>(?:(?P<k>double|triple)[- ]?)?(?:overtime|OT)|(?P<n>[23])OT)\b", re.IGNORECASE)
_HOOPS_OT_RE = re.compile(
    r"\b(?:went|goes|going|headed|heads|forced|forces|forcing|needed|needs|required|requires|"
    r"sent\s+(?:it|the\s+game)\s+to)\s+(?:to\s+|into\s+)?(?:an?\s+)?"
    r"(?:(?P<k>double|triple)[- ]?)?(?:extra\s+session|overtimes?|OT)\b|"
    r"\b(?P<d>double|triple)[- ]overtime\b|\b(?P<n>[23])OT\b", re.IGNORECASE)
_COMEBACK_HOOPS_RE = re.compile(
    r"\b(?:came|comes|coming|rallied|rallies|rallying|clawed|fought|climbed)\s+back\s+from\s+(?:a\s+)?" + _N +
    r"(?:-point\s+(?:deficit|hole)|\s+(?:points?\s+)?down)|"
    r"\berased?\s+(?:a\s+)?" + _N + r"-point\s+(?:deficit|hole|lead)|"
    r"\bfrom\s+" + _N + r"\s+(?:points?\s+)?down\b", re.IGNORECASE)


def _hoops_periods(sentence: str) -> list[tuple[int, int, str]]:
    """(period, position, cue) for each point in the game a sentence names.
    "end" cues are the half ("at halftime", "entering the second half" = the
    score after the first half); "in" cues are a half or an overtime."""
    out = []
    for m in _HOOPS_HALF_END_RE.finditer(sentence):
        out.append((1, m.start(), "end"))
    for m in _HOOPS_IN_RE.finditer(sentence):
        if m.group("h"):
            out.append((1 if m.group("h").lower() == "first" else 2, m.start(), "in"))
        else:
            k = (m.group("k") or "").lower()
            out.append((2 + ({"double": 2, "triple": 3}.get(k) or (int(m.group("n")) if m.group("n") else 1)),
                        m.start(), "in"))
    return out


def _hname(p: int) -> str:
    return {1: "first half", 2: "second half", 3: "overtime"}.get(p, f"{p - 2}OT")


def _hoops_claims(sentence: str, f: dict, side: str | None) -> list[tuple[str, str, str]]:
    """(verdict, what the claim said, the fact) for every game-flow claim in
    one sentence about college basketball game `f`. `side` is the one team
    the sentence names, or None."""
    out: list[tuple[str, str, str]] = []

    def abbr(s):
        return f["teams"][s]["abbr"] or f["teams"][s]["name"]

    def sc(a, h):
        return f"tied {a}-{h}" if a == h else f"{abbr('away' if a > h else 'home')} {max(a, h)}-{min(a, h)}"

    periods = _hoops_periods(sentence)
    # A score at a point in the game.
    for m in _NHL_SCORE_PAIR_RE.finditer(sentence):
        x, y = int(m.group(1)), int(m.group(2))
        if x + y > 260 or _NHL_NOT_A_SCORE_AFTER.search(sentence[m.end():]) or \
                re.search(r"\b(?:series|won|beat|beats|final|record|improved?\s+to|moved?\s+to)\s+(?:the\s+\w+\s+)?$",
                          sentence[:m.start()], re.IGNORECASE):
            continue
        near = min(periods, key=lambda q: abs(q[1] - m.start()), default=None)
        if near is None or abs(near[1] - m.start()) > 70 or near[0] > f["periods"]:
            continue
        if sentence[max(0, m.start() - 1):m.start()] == "(" or sentence[m.end():m.end() + 1] == ")":
            continue                                    # "Iowa (27-6)": a record
        p, _, cue = near
        pre = sentence[max(0, m.start() - 60):m.start()]
        if cue == "in" and (tuple(sorted((x, y))) == f["final"] or not _NHL_SCORE_CUE.search(pre)):
            continue                                    # "beat Duke 74-70 in the second half"
        want = None
        if side and _NHL_LEAD_CUE.search(pre):
            want = (max(x, y), min(x, y))
        elif side and _NHL_TRAIL_CUE.search(pre):
            want = (min(x, y), max(x, y))

        def fits(st, want=want, x=x, y=y):
            if want is not None:
                return (st if side == "away" else (st[1], st[0])) == want
            return sorted(st) == sorted((x, y))
        pool = [f["half"]] if cue == "end" else sorted(f["scores"][p])
        label = "at halftime" if cue == "end" else f"in the {_hname(p)}"
        if any(fits(st) for st in pool):
            out.append(("confirm", m.group(0), ""))
        else:
            out.append(("contradict", f"{m.group(0)} {label}",
                        f"the score {label} was " + (sc(*pool[0]) if cue == "end" else
                                                     "one of " + " / ".join(sc(*st) for st in pool[:6]))))
    # Go-ahead, for good.
    for m in _NHL_GO_AHEAD_RE.finditer(sentence):
        near = min([q for q in periods if q[2] == "in"], key=lambda q: abs(q[1] - m.start()), default=None)
        if near is None or abs(near[1] - m.start()) > 70 or re.match(r"\s+(?:a|the)\s+\d", sentence[m.end():]):
            continue
        p = near[0]
        good = bool(re.search(r"\bfor\s+good\b", sentence[m.start():m.end() + 40], re.IGNORECASE))
        if good:
            fg = f["for_good"]
            ok = fg is not None and fg[1] == p and (side is None or fg[0] == side)
            fact = f"{abbr(fg[0])} took the lead for good in the {_hname(fg[1])}" if fg else "no lead held"
        else:
            ok = any(i == p and (side is None or s == side) for s, i in f["go_ahead"])
            ga = [f"{abbr(s)} in the {_hname(i)}" for s, i in f["go_ahead"] if side is None or s == side]
            fact = ("the lead was taken only " + ", ".join(ga)) if ga else \
                   f"{abbr(side) if side else 'nobody'} never took the lead"
        out.append(("confirm" if ok else "contradict", f"{m.group(0)} in the {_hname(p)}", fact))
    # Tie.
    for m in _NHL_TIE_RE.finditer(sentence):
        near = min([q for q in periods if q[2] == "in"], key=lambda q: abs(q[1] - m.start()), default=None)
        if near is None or abs(near[1] - m.start()) > 70:
            continue
        p = near[0]
        if any(i == p and (side is None or s == side) for s, i in f["ties"]):
            out.append(("confirm", m.group(0), ""))
        else:
            ties = [f"{abbr(s)} in the {_hname(i)}" for s, i in f["ties"] if side is None or s == side]
            out.append(("contradict", f"{m.group(0)} in the {_hname(p)}",
                        ("it was tied only by " + ", ".join(ties)) if ties else "the game was never tied"))
    # Wire to wire, never trailed.
    win = f["winner"]
    m = _NHL_WIRE_RE.search(sentence)
    if m:
        who = side or win
        never_trailed = f["max_deficit"][who] == 0
        ok = (f["wire_to_wire"] and who == win) if "wire" in m.group(0).lower() else never_trailed
        out.append(("confirm" if ok else "contradict", m.group(0),
                    f"{abbr(who)} trailed by as many as {f['max_deficit'][who]}" if not never_trailed
                    else f"{abbr(who)} did not lead from the first basket on"))
    for m in _COMEBACK_HOOPS_RE.finditer(sentence):
        got = next((g for g in m.groups() if g), None)
        if got is None:
            continue
        n = _nhl_num(got)
        who = side or win
        out.append(("confirm" if f["max_deficit"][who] >= n else "contradict", m.group(0),
                    f"{abbr(who)} trailed by at most {f['max_deficit'][who]}"))
    # Overtime.
    ot = f["periods"] - 2
    for m in _HOOPS_OT_RE.finditer(sentence):
        k = (m.group("k") or m.group("d") or "").lower()
        need = {"double": 2, "triple": 3}.get(k) or (int(m.group("n")) if m.group("n") else 1)
        out.append(("confirm" if ot >= need else "contradict", m.group(0),
                    "it ended in regulation" if ot == 0 else f"it went to {ot} overtime(s)"))
    return out


def check_ncaamb_flow_claims(own_text: str, game_state: dict, outcomes: list | None = None) -> list[str]:
    """`own_text`: one section's own prose (no tweets), one block per line.
    `outcomes`, if given, collects (level, quoted, sentence, kind, figure)
    for every sentence with a game-flow claim, confirmed ones included."""
    import ncaamb_bundle
    facts = ncaamb_bundle.claim_facts(game_state or {})
    if not facts or not _HOOPS_RE.search(own_text):
        return []
    by_name, dup = {}, set()
    for f in facts:
        for s in ("away", "home"):
            n = f["teams"][s]["name"]
            if n in by_name:
                dup.add(n)                              # a team can't play twice in a night: ambiguous
            by_name[n] = (f, s)
    for n in dup:
        del by_name[n]
    if not by_name:
        return []
    idx = _alias_index([{"team": n, "sport": "ncaamb", "opponent": ""} for n in by_name])

    def games_of(text):
        return {by_name[n][0]["game_id"]: by_name[n][0] for n in _named(text, idx) if n in by_name}

    section_games = games_of(own_text)
    flags = []
    for sentence in _sentences(own_text):
        if _NHL_ELSEWHERE_RE.search(sentence):
            continue                                    # another game, another year
        named = [n for n in _named(sentence, idx) if n in by_name]
        cands = games_of(sentence) or section_games
        if len(cands) != 1:
            continue
        f = next(iter(cands.values()))
        sides = {by_name[n][1] for n in named if by_name[n][0] is f}
        side = next(iter(sides)) if len(sides) == 1 else None
        results = _hoops_claims(sentence, f, side)
        if not results:
            continue
        quoted = _quote(sentence)
        bad = [(said, fact) for v, said, fact in results if v == "contradict"]
        figs = [r"(?<![\d-])" + re.escape(m.group(0)) + r"(?![\d-])" for m in _NHL_SCORE_PAIR_RE.finditer(sentence)]
        fig = "|".join(figs) or r"(?!x)x"
        if not bad:
            if outcomes is not None:
                outcomes.append(("confirmed", quoted, sentence, "game flow", fig))
            continue
        t = f["teams"]
        why = "; ".join(f"{a} -> but {b}" for a, b in bad).replace('"', "'").replace("--", "-")
        flags.append(
            f'\n<!-- FACT FLAG [HIGH]: "{quoted}" contradicts the play log of {t["away"]["name"]} @ '
            f'{t["home"]["name"]}: {why} (ESPN; the COLLEGE BASKETBALL GAME FLOW block has the full flow). '
            f'Correct it to the block, or cut it. -->')
        if outcomes is not None:
            outcomes.append(("HIGH", quoted, sentence, "game flow", fig))
    return flags


# ---------------------------------------------------------------------------
# Check 3G: NBA game-flow claims vs the play log (SLA-147)
#
# Each claim below is checked against nba_bundle.claim_facts(), the same
# baskets the NBA GAME FLOW block shows the writer:
#
#   a score at a point    "led 52-48 at the half", "up 12 entering the fourth"
#   go-ahead / for good   "took the lead in the third", "went ahead for good"
#   tie                   "tied it in the fourth"
#   a shooter's basket    "Brunson hit the go-ahead three", "Haliburton's
#                         game-winner", "Doncic's game-tying free throws"
#   runs and streaks      "a 14-0 run", "an 11-2 run", "scored 12 straight points"
#   a scorer's points     "Edwards had 21 in the first half"
#   a quarter's score     "outscored them 28-12 in the third"
#   leads and deficits    "led by as many as 20", "came back from 17 down"
#   wire to wire          "never trailed", "led wire to wire"
#
# HIGH only when the sentence ties to exactly one of yesterday's NBA games and
# the facts contradict it; a claim the facts can't settle is left alone (the
# SLA-142 rule). A shooter is only ever named when the play log's points for
# every player matched the box score (nba_bundle `named`). Only sections that
# read as basketball are checked.
# ---------------------------------------------------------------------------

_NBA_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_NBA_NOT_PERIOD_AFTER = r"(?!\s*[- ]?(?:place|seed|round|straight|consecutive|time|game|win|loss|inning|half|options?))"
_NBA_PERIOD_RES = [
    # "in the third quarter", "entering the fourth quarter", "Q3", "3rd quarter"
    re.compile(r"\b(?P<pre>(?:in|during|of|after|through|entering|into|by|to\s+(?:open|start)|midway\s+through|"
               r"heading\s+into|going\s+into|late\s+in|early\s+in)\s+(?:the\s+)?)?"
               r"(?P<q>first|second|third|fourth|[1-4](?:st|nd|rd|th)|q[1-4])(?:\s+quarter)\b", re.IGNORECASE),
    # "entering the fourth", "in the third" (no "quarter")
    re.compile(r"\b(?P<pre>entering|into|heading\s+into|going\s+into|in|after|through)\s+the\s+"
               r"(?P<q>first|second|third|fourth)\b" + _NBA_NOT_PERIOD_AFTER + r"(?!\s+quarter)", re.IGNORECASE),
    # "after three quarters", "through two quarters"
    re.compile(r"\b(?P<pre>after|through)\s+(?P<n>one|two|three)\s+quarters?\b", re.IGNORECASE),
    # halftime
    re.compile(r"\b(?P<pre>at|by|before|after|into|heading\s+into|going\s+into|entering)\s+(?:the\s+)?"
               r"(?P<h>half(?:time)?|break|intermission)\b", re.IGNORECASE),
    # overtime
    re.compile(r"\b(?P<pre>in|during|into|to|after|forcing|forced|forces|force|sending|sent|send|pushing|pushed|"
               r"pushes|push)?\s*(?:(?P<k>double|triple)[- ]?)?"
               r"(?P<ot>overtime|OT)\b|\b(?P<m>[2-4])OT\b", re.IGNORECASE),
]
_NBA_GO_AHEAD_RE = re.compile(
    r"\b(?:took|take|takes|taking|grabbed|grabs|seized|seizes|regained|retook|reclaimed)\s+(?:the|a)\s+lead\b|"
    r"\bwent\s+ahead\b|\bpulled\s+ahead\b|\bmoved\s+ahead\b|"
    r"\b(?:ahead|in\s+front|up)\s+for\s+good\b|\bthe\s+lead\s+for\s+good\b", re.IGNORECASE)
_NBA_TIE_RE = re.compile(r"\b(?:tied|ties|knotted|evened|leveled|levelled)\s+(?:it|things|the\s+game|the\s+score)\b|"
                         r"\btying\s+(?:basket|bucket|three|shot)\b", re.IGNORECASE)
_NBA_WIRE_RE = re.compile(r"\bwire[- ]to[- ]wire\b|\bnever\s+trailed\b|\bled\s+(?:the\s+)?(?:whole|entire)\s+(?:way|game)\b",
                          re.IGNORECASE)
_NBA_PAIR_RE = re.compile(r"(?<![\d-])(\d{2,3})-(\d{2,3})(?![\d-])")
_NBA_SCORE_CUE = re.compile(r"(?:up|ahead|lead|led|leading|down|behind|trail\w*|tied|tie|ties|make|made|makes|put|"
                            r"cut|extend\w*|stretch\w*|pull\w*|knot\w*|grab\w*|took|take|opened|jumped|"
                            r"the\s+score|it\s+was|scored)\b", re.IGNORECASE)
_NBA_NOT_SCORE_BEFORE = re.compile(r"(?:improved?|fell|moved|dropped|record|to|at|from|series|won|beat|beats|final|"
                                   r"went|are|is|now)\s+(?:to\s+|the\s+\w+\s+)?$", re.IGNORECASE)
_NBA_NOT_A_SCORE_BEFORE = re.compile(
    r"outscor\w*|won\s+the\s+\w+\s+(?:quarter|half)|won\s+the\s+(?:quarter|half)|closed|ran\s+off|went\s+on|"
    r"rattled\s+off|\bover\s+the\s+(?:final|last|next)|\bin\s+the\s+(?:final|last)\s+\d", re.IGNORECASE)
_NBA_NOT_SCORE_AFTER = re.compile(r"^\s*\)?\s*(?:series|record|on\s+the\s+season|this\s+season|in\s+conference|"
                                  r"win|victory|loss|defeat|final|mark|clip|shooting)\b", re.IGNORECASE)
_NBA_ELSEWHERE_RE = re.compile(r"\bGame\s+\d\b|\b(?:19|20)\d{2}\b|\blast\s+(?:night'?s?\s+)?(?:week|year|season|time)\b|"
                               r"\bearlier\s+in\s+the\s+series\b|\bin\s+Game\b", re.IGNORECASE)
_NBA_HALF_RE = re.compile(r"\b(?:first|second)[- ]half\b", re.IGNORECASE)
_NBA_NICKS2 = {"trail blazers"}
_NBA_SHARED_CITIES = {"new york", "los angeles"}
_NBA_HOOPS_RE = re.compile(r"\b(?:NBA|basketball|quarter|halftime|at\s+the\s+half|first\s+half|second\s+half|three-pointers?|"
                       r"3-pointers?|dunks?|free\s+throws?|buzzer|layups?|rebounds?|point\s+guard|buckets?|hoops|"
                       r"jumpers?|and-one|triple-double|go-ahead\s+three|fourth\s+quarter|Q[1-4])\b", re.IGNORECASE)
_NBA_COMEBACK_RE = re.compile(
    r"\b(?:came|comes|coming|rallied|rallies|rallying|clawed|fought|climbed|bounced|erased|erases|overcame|overcomes)\s+"
    r"(?:back\s+)?(?:from\s+)?(?:a\s+|an\s+)?(?:(?P<n>\d{1,2})-point|(?P<d>double-digit))\s+(?:deficit|hole)|"
    r"\bfrom\s+(?P<n2>\d{1,2})\s+down\b", re.IGNORECASE)
_NBA_AS_MANY_RE = re.compile(
    r"\b(?P<dir>led|lead(?:ing)?|up|ahead|trailed|trailing|trail|down|behind)\s+(?:by\s+)?as\s+many\s+as\s+(?P<n>\d{1,2})\b|"
    r"\bbiggest\s+(?P<dir2>lead|deficit)\s+(?:was\s+|of\s+)(?P<n2>\d{1,2})\b", re.IGNORECASE)
_NBA_MARGIN_RE = re.compile(
    r"\b(?P<dir>up|ahead|led|leading|lead|down|behind|trailed|trailing|trails)\s+(?:by\s+)?(?P<n>\d{1,2})\b"
    r"(?!\s*(?:-|–|games?|wins?|losses|seed|percent|%|years?|minutes?|seconds?|\d))", re.IGNORECASE)
_NBA_RUN_RE = re.compile(r"\b(?P<a>\d{1,2})-(?P<b>\d{1,2})\s+(?:run|burst|spurt|stretch|surge|blitz|tear)\b", re.IGNORECASE)
_NBA_OUTSCORED_RE = re.compile(
    r"\b(?:outscor\w+|won\s+the\s+(?:first|second|third|fourth)\s+quarter|won\s+the\s+(?:quarter|period))\b"
    r"[^.;]{0,50}?(?P<a>\d{1,3})-(?P<b>\d{1,3})(?=\s+(?:in|during|over)\s+(?:the\s+)?"
    r"(?:first|second|third|fourth|overtime|OT)\b)", re.IGNORECASE)
_NBA_STRAIGHT_RE = re.compile(r"\b(?P<n>\d{1,2})\s+(?:straight|consecutive|unanswered)\s+points?\b", re.IGNORECASE)
_NBA_SHOT_CUE_RE = re.compile(
    r"\b(?P<kind>go-ahead|game-winning|game-winner|game-tying|game-tier|tying)\s*"
    r"(?P<what>three(?:-pointers?)?|3-pointers?|triples?|baskets?|buckets?|jumpers?|layups?|dunks?|free\s+throws?|"
    r"shots?|tip-in|floaters?|scores?)?", re.IGNORECASE)
_NBA_SHOT_VERB = re.compile(r"\b(?:hit|hits|made|makes|sank|sinks|drained|drains|knocked|knocks|buried|buries|"
                            r"converted|banked|nailed|splashed|swished|delivered|delivers|connected|had|with)\b",
                            re.IGNORECASE)
_NBA_PTS_VERB = re.compile(r"\b(?:scored|scores|had|has|poured|dropped|drops|put\s+up|added|adds|tallied|netted|"
                           r"erupted|finished|piled|totaled|managed|dished)\b", re.IGNORECASE)


def _nba_period_from(m: re.Match) -> int | None:
    g = m.groupdict()
    if g.get("q"):
        q = g["q"].lower()
        return _NBA_WORDS.get(q) or int(re.sub(r"\D", "", q))
    if g.get("n"):
        return {"one": 1, "two": 2, "three": 3}[g["n"].lower()]
    if g.get("h"):
        return 2
    if g.get("ot") or g.get("m"):
        return 4 + {"double": 2, "triple": 3}.get((g.get("k") or "").lower(), 1) if g.get("ot") else 4 + int(g["m"])
    return None


def _nba_periods_in(sentence: str) -> list[tuple[int, int, str, int]]:
    """(period, start, cue, end) for each period a sentence names. cue is
    "end" ("after the third quarter", "at the half"), "enter" ("entering the
    fourth") or "in". Overtime is the first one (5), or the one named."""
    out, seen = [], set()
    for rx in _NBA_PERIOD_RES:
        for m in rx.finditer(sentence):
            p = _nba_period_from(m)
            if p is None:
                continue
            pre = re.sub(r"\s+the$", "", (m.groupdict().get("pre") or "").strip().lower())
            h = m.groupdict().get("h")
            if h or m.groupdict().get("n"):
                cue = "enter" if pre in ("into", "heading into", "going into", "entering") else "end"
            elif pre in ("after", "through", "by"):
                cue = "end"
            elif pre in ("entering", "into", "heading into", "going into", "to", "forcing", "forced", "forces", "force",
                         "sending", "sent", "send", "pushing", "pushed", "pushes", "push"):
                cue = "enter"
            else:
                cue = "in"
            key = (p, m.start() // 4)
            if key in seen:
                continue
            seen.add(key)
            out.append((p, m.start(), cue, m.end()))
    return sorted(out, key=lambda x: x[1])


def _nba_pname(p: int) -> str:
    return {1: "first quarter", 2: "second quarter", 3: "third quarter", 4: "fourth quarter", 5: "overtime"}.get(
        p, f"{p - 4}OT")


def _nba_period_points(f: dict, p: int) -> tuple[int, int]:
    """(away, home) points scored IN period p."""
    start = f["end_score"][p - 1] if p > 1 else (0, 0)
    end = f["end_score"][p]
    return end[0] - start[0], end[1] - start[1]


def _nba_claims(sentence: str, f: dict, side: str | None) -> list[tuple[str, str, str]]:
    """(verdict, what the claim said, the fact) for every game-flow claim in
    one sentence about NBA game `f`. `side` is the one team the sentence names,
    or None."""
    out: list[tuple[str, str, str]] = []

    def abbr(s):
        return f["teams"][s]["abbr"] or f["teams"][s]["name"]

    def sc(a, h):
        return f"tied {a}-{h}" if a == h else f"{abbr('away' if a > h else 'home')} {max(a, h)}-{min(a, h)}"

    def surname_of(k):
        return k.title()

    periods = [x for x in _nba_periods_in(sentence) if x[0] <= f["periods"]]
    winner = f["winner"]

    # A score at a point in the game ("led 52-48 at the half").
    for m in _NBA_PAIR_RE.finditer(sentence):
        x, y = int(m.group(1)), int(m.group(2))
        pre = sentence[max(0, m.start() - 60):m.start()]
        if _NBA_NOT_SCORE_BEFORE.search(pre) or _NBA_NOT_A_SCORE_BEFORE.search(pre) or \
                _NBA_NOT_SCORE_AFTER.search(sentence[m.end():]) or \
                re.match(r"\s+(?:over|in)\s+the\s+(?:final|last|next)\b", sentence[m.end():], re.IGNORECASE) or \
                tuple(sorted((x, y))) == f["final"] and not periods:
            continue
        if tuple(sorted((x, y))) in f["series_pairs"]:
            continue
        near = min(periods, key=lambda z: abs(z[1] - m.start())) if periods else None
        if near is None or abs(near[1] - m.start()) > 70:
            continue
        halves = [h.start() for h in _NBA_HALF_RE.finditer(sentence)]
        if any(abs(h - m.start()) < abs(near[1] - m.start()) for h in halves):
            continue                                    # "the second half 48-46": a half, not this period
        p, _, cue, _e = near
        if cue in ("in", "enter") and tuple(sorted((x, y))) == f["final"]:
            continue                                    # the final score, named near a period
        if cue == "in" and not _NBA_SCORE_CUE.search(pre):
            continue
        want = None
        if side and re.search(r"\b(?:up|ahead|lead|led|leading|in\s+front)\b", pre, re.IGNORECASE):
            want = (max(x, y), min(x, y))
        elif side and re.search(r"\b(?:down|behind|trailed|trailing|trail)\b", pre, re.IGNORECASE):
            want = (min(x, y), max(x, y))

        def fits(st, want=want, x=x, y=y):
            if want is not None:
                return (st if side == "away" else (st[1], st[0])) == want
            return sorted(st) == sorted((x, y))
        if cue == "end":
            pool = [f["end_score"][p]]
        elif cue == "enter":
            pool = [f["end_score"][p - 1]] if p > 1 else [(0, 0)]
        else:
            pool = sorted(f["scores"][p])
        label = {"end": f"after the {_nba_pname(p)}", "enter": f"entering the {_nba_pname(p)}",
                 "in": f"in the {_nba_pname(p)}"}[cue]
        if any(fits(st) for st in pool):
            out.append(("confirm", m.group(0), ""))
        else:
            out.append(("contradict", f"{m.group(0)} {label}",
                        f"the score {label} was " + " / ".join(sc(*st) for st in pool)))
    # A margin at a point in the game ("up 12 at the half").
    for m in _NBA_MARGIN_RE.finditer(sentence):
        n = int(m.group("n"))
        cands = [x for x in periods if x[2] in ("end", "enter") and 0 <= x[1] - m.end() <= 45]
        if not cands:
            continue
        p, _, cue, _e = cands[0]
        if re.search(r"[.;]", sentence[m.end():cands[0][1]]):
            continue
        st = f["end_score"][p] if cue == "end" else (f["end_score"][p - 1] if p > 1 else (0, 0))
        lead_side = "away" if st[0] > st[1] else "home" if st[1] > st[0] else None
        margin = abs(st[0] - st[1])
        d = m.group("dir").lower()
        leading = d in ("up", "ahead", "led", "leading", "lead")
        label = f"{'after' if cue == 'end' else 'entering'} the {_nba_pname(p)}"
        if side:
            ok = margin == n and lead_side == (side if leading else ("home" if side == "away" else "away"))
        else:
            ok = margin == n
        if ok:
            out.append(("confirm", m.group(0), ""))
        else:
            out.append(("contradict", f"{m.group(0)} {label}", f"the score {label} was {sc(*st)}"))
    # Go-ahead, for good.
    in_periods = [x for x in periods if x[2] == "in"]
    for m in _NBA_GO_AHEAD_RE.finditer(sentence):
        near = min(in_periods, key=lambda z: abs(z[1] - m.start())) if in_periods else None
        if near is None or abs(near[1] - m.start()) > 60 or re.match(r"\s+(?:a|the)\s+\d", sentence[m.end():]):
            continue
        p = near[0]
        good = bool(re.search(r"\bfor\s+good\b", sentence[m.start():m.end() + 40], re.IGNORECASE))
        if good:
            fg = f["for_good"]
            ok = fg is not None and fg["period"] == p and (side is None or fg["side"] == side)
            fact = f"{abbr(fg['side'])} took the lead for good in the {_nba_pname(fg['period'])}" if fg else "no lead held"
        else:
            ok = any(g["period"] == p and (side is None or g["side"] == side) for g in f["go_ahead"])
            ga = sorted({f"{abbr(g['side'])} in the {_nba_pname(g['period'])}" for g in f["go_ahead"]
                         if side is None or g["side"] == side})
            fact = ("the lead was taken only " + ", ".join(ga)) if ga else \
                   f"{abbr(side) if side else 'nobody'} never took the lead"
        out.append(("confirm" if ok else "contradict", f"{m.group(0)} in the {_nba_pname(p)}", fact))
    # Tie.
    for m in _NBA_TIE_RE.finditer(sentence):
        near = min(in_periods, key=lambda z: abs(z[1] - m.start())) if in_periods else None
        if near is None or abs(near[1] - m.start()) > 60:
            continue
        p = near[0]
        if any(i == p and (side is None or s == side) for s, i in f["ties"]):
            out.append(("confirm", m.group(0), ""))
        else:
            ties = sorted({f"{abbr(s)} in the {_nba_pname(i)}" for s, i in f["ties"] if side is None or s == side})
            out.append(("contradict", f"{m.group(0)} in the {_nba_pname(p)}",
                        ("it was tied only by " + ", ".join(ties)) if ties else "the game was never tied by a basket"))
    # Wire to wire, never trailed.
    m = _NBA_WIRE_RE.search(sentence)
    if m:
        who = side or winner
        never_trailed = f["max_deficit"][who] == 0
        ok = (f["wire_to_wire"] and who == winner) if "wire" in m.group(0).lower() else never_trailed
        out.append(("confirm" if ok else "contradict", m.group(0),
                    f"{abbr(who)} trailed by as many as {f['max_deficit'][who]}" if not never_trailed
                    else f"{abbr(who)} did not lead from the first basket on"))
    # Comebacks.
    for m in _NBA_COMEBACK_RE.finditer(sentence):
        n = m.group("n") or m.group("n2")
        n = int(n) if n else 10                          # "double-digit"
        who = side or winner
        out.append(("confirm" if f["max_deficit"][who] >= n else "contradict", m.group(0),
                    f"{abbr(who)} trailed by at most {f['max_deficit'][who]}"))
    # "led by as many as N", "trailed by as many as N".
    for m in _NBA_AS_MANY_RE.finditer(sentence):
        d = (m.group("dir") or m.group("dir2") or "").lower()
        n = int(m.group("n") or m.group("n2"))
        lead = d in ("led", "lead", "leading", "up", "ahead")
        who = side
        if who is None:
            continue                                    # which team? the sentence doesn't say
        key = "max_lead" if lead else "max_deficit"
        if f[key][who] < n:
            out.append(("contradict", m.group(0), f"{abbr(who)}'s biggest {'lead' if lead else 'deficit'} was {f[key][who]}"))
        else:
            out.append(("confirm", m.group(0), ""))
    # Runs: "a 14-0 run", "an 11-2 run".
    for m in _NBA_RUN_RE.finditer(sentence):
        a, b = int(m.group("a")), int(m.group("b"))
        if a < b or a < 6:
            continue
        sides = [side] if side else ["away", "home"]
        import nba_bundle
        ok = any(nba_bundle.best_window(f["_events"], s, a, b) for s in sides)
        if ok:
            out.append(("confirm", m.group(0), ""))
        elif side:
            best = max((r["pts"] for r in f["runs"] if r["side"] == side), default=0)
            out.append(("contradict", m.group(0), f"{abbr(side)}'s best unanswered run was {best}-0"))
    # A period's points: "outscored them 28-12 in the third".
    for m in _NBA_OUTSCORED_RE.finditer(sentence):
        near = [x for x in periods if x[1] >= m.end() - 1 and x[1] - m.end() <= 25]
        if not near:
            continue
        p = near[0][0]
        a, h = _nba_period_points(f, p)
        said = (int(m.group("a")), int(m.group("b")))
        if sorted(said) == sorted((a, h)):
            out.append(("confirm", m.group(0), ""))
        else:
            out.append(("contradict", f"{m.group(0)} in the {_nba_pname(p)}",
                        f"the {_nba_pname(p)} was {abbr('away')} {a}, {abbr('home')} {h}"))
    # Straight points: a player's or a team's.
    for m in _NBA_STRAIGHT_RE.finditer(sentence):
        n = int(m.group("n"))
        lead_in = sentence[max(0, m.start() - 70):m.start()]
        plain = _nba_ascii(lead_in)
        who = None
        for name in f["points"]:
            for nm in re.finditer(r"\b" + _nba_name_pat(name) + r"(?:['’]s)?\b", plain, re.IGNORECASE):
                gap = plain[nm.end():]
                if re.search(r"[.;]", gap):
                    continue
                if who is None or nm.end() > who[1]:
                    who = (name, nm.end())
        if who is not None:
            name = who[0]
            best = max((s["pts"] for s in f["streaks"] if s["shooter"] == name), default=0)
            out.append(("confirm" if best >= n else "contradict", f"{name.title()} {m.group(0)}",
                        f"{name.title()}'s longest streak of his team's points was {best}"))
        elif side:
            import nba_bundle
            ok = nba_bundle.best_window(f["_events"], side, n, 0)
            best = max((r["pts"] for r in f["runs"] if r["side"] == side), default=0)
            out.append(("confirm" if ok else "contradict", m.group(0), f"{abbr(side)}'s best unanswered run was {best}"))
    return out + _nba_shooter_claims(sentence, f, side, periods, abbr, sc)


def _nba_name_pat(name: str) -> str:
    """A normalized surname ("gilgeousalexander", "oneale") as a pattern that
    also reads "Gilgeous-Alexander" and "O'Neale"."""
    return "['’-]?".join(re.escape(ch) for ch in name)


def _nba_ascii(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _nba_shooter_claims(sentence: str, f: dict, side, periods, abbr, sc) -> list[tuple[str, str, str]]:
    """Claims that name a player: his go-ahead / game-winning / game-tying
    basket, and the points he scored in a period or half. Only when the play
    log's shooters matched the box score (f["named"])."""
    out: list[tuple[str, str, str]] = []
    if not f["named"] or not f["points"]:
        return out
    plain = _nba_ascii(sentence)
    roster = list(f["points"])

    def owner(pos_start: int, pos_end: int, window: int = 70):
        """The roster surname a phrase belongs to: the nearest name before it
        with no sentence break between (and possessive or a verb to link), or
        one right after it ("a go-ahead three from Haliburton")."""
        best = None
        for name in roster:
            for nm in re.finditer(r"\b" + _nba_name_pat(name) + r"(?:['’]s)?\b", plain, re.IGNORECASE):
                if nm.end() <= pos_start and pos_start - nm.end() <= window:
                    gap = plain[nm.end():pos_start]
                    if re.search(r"[.;!?]", gap) or any(o != name and re.search(r"\b" + _nba_name_pat(o) + r"\b", gap, re.IGNORECASE)
                                                       for o in roster):
                        continue
                    if plain[nm.end() - 2:nm.end()] in ("'s", "’s") or _NBA_SHOT_VERB.search(gap) or \
                            _NBA_PTS_VERB.search(gap):
                        if best is None or nm.end() > best[1]:
                            best = (name, nm.end())
                elif nm.start() >= pos_end and nm.start() - pos_end <= 25:
                    gap = plain[pos_end:nm.start()]
                    if re.search(r"\b(?:from|by|off|courtesy\s+of)\b\s*(?:\w+\s+){0,2}$", gap, re.IGNORECASE):
                        if best is None:
                            best = (name, nm.start())
        return best[0] if best else None

    for m in _NBA_SHOT_CUE_RE.finditer(plain):
        kind = m.group("kind").lower().replace("game-tier", "game-tying")
        what = (m.group("what") or "").lower()
        name = owner(m.start(), m.end())
        if not name:
            continue
        side_of = f["sides"].get(name)
        if kind == "go-ahead":
            evs = [g for g in f["go_ahead"] if g["shooter"] == name]
        elif kind in ("game-winning", "game-winner"):
            fg = f["for_good"]
            evs = [fg] if fg and fg["shooter"] == name else []
        else:                                            # game-tying, tying
            evs = [g for g in f["tie_baskets"] if g["shooter"] == name]
        near_p = next((x for x in periods if 0 <= x[1] - m.end() <= 45 and x[2] == "in"), None)
        wrong_p = None
        if near_p is not None and evs:
            right = [g for g in evs if g["period"] == near_p[0]]
            if not right:
                wrong_p = ", ".join(sorted({_nba_pname(g["period"]) for g in evs}))
            evs = right
        want_pts = 3 if re.match(r"(three|3|triple)", what) else 1 if what.startswith("free") else None
        had_kind = bool(evs)                             # he made one of this kind, whatever its value
        if want_pts:
            evs = [g for g in evs if g["pts"] == want_pts]
        quoted = f"{name.title()} {m.group(0)}".strip()
        if evs:
            out.append(("confirm", quoted, ""))
            continue
        if wrong_p:
            fact = f"{name.title()}'s {kind} basket came in the {wrong_p}"
        elif kind == "go-ahead":
            who = sorted({f"{(g['shooter'] or '?').title()} ({g['pts']}-pt, {_nba_pname(g['period'])})"
                          for g in f["go_ahead"] if g["side"] == side_of})[:6]
            fact = f"{name.title()} made no such basket; {abbr(side_of)}'s go-ahead baskets: " + (", ".join(who) or "none")
        elif kind in ("game-winning", "game-winner"):
            fg = f["for_good"]
            fact = (f"the basket that put {abbr(fg['side'])} ahead for good was {(fg['shooter'] or '?').title()}'s "
                    f"{'three' if fg['pts'] == 3 else 'free throw' if fg['pts'] == 1 else 'basket'} in the "
                    f"{_nba_pname(fg['period'])}") if fg else "no lead was held"
        else:
            who = sorted({(g['shooter'] or '?').title() for g in f["tie_baskets"]})
            fact = f"{name.title()} never tied it; the game was tied by " + (", ".join(who) or "no basket")
        if want_pts and had_kind and not wrong_p:
            fact = f"{name.title()}'s {kind} basket was not a {'three' if want_pts == 3 else 'free throw'}"
        out.append(("contradict", quoted, fact))
    # Points by period or half: "Edwards had 21 in the first half".
    pts_res = [
        re.compile(r"\b(?P<n>\d{1,2})\s+(?:points?\s+)?(?:in|during|over)\s+(?:the\s+)?(?P<w>first|second|third|fourth)\s+"
                   r"(?P<u>half|quarter)\b", re.IGNORECASE),
        re.compile(r"\b(?P<n>\d{1,2})\s+(?P<w>first|second|third|fourth)-(?P<u>half|quarter)\s+points\b", re.IGNORECASE),
    ]
    for rx in pts_res:
        for m in rx.finditer(plain):
            name = owner(m.start(), m.end(), 90)
            if not name:
                continue
            gap_names = [o for o in roster if o != name and re.search(r"\b" + _nba_name_pat(o) + r"\b", plain, re.IGNORECASE)]
            if gap_names:
                continue                                 # two players in the sentence: whose points?
            n, w, u = int(m.group("n")), m.group("w").lower(), m.group("u").lower()
            if u == "half":
                if w not in ("first", "second"):
                    continue
                ps = (1, 2) if w == "first" else (3, 4)
            else:
                ps = (_NBA_WORDS[w],)
            got = sum(f["points"][name].get(p, 0) for p in ps)
            said = f"{name.title()} {n} in the {w} {u}"
            out.append(("confirm" if got == n else "contradict", said,
                        "" if got == n else f"{name.title()} scored {got} in the {w} {u}"))
    return out


def check_nba_flow_claims(own_text: str, game_state: dict, outcomes: list | None = None) -> list[str]:
    """`own_text`: one section's own prose (no tweets), one block per line.
    `outcomes`, if given, collects (level, quoted, sentence, kind, figure)
    for every sentence with a game-flow claim, confirmed ones included."""
    import nba_bundle
    facts = nba_bundle.claim_facts(game_state or {})
    if not facts or not _NBA_HOOPS_RE.search(own_text):
        return []
    by_name, dup = {}, set()
    for f in facts:
        f["_events"] = f["events"]
        for s in ("away", "home"):
            n = f["teams"][s]["name"]
            if n in by_name:
                dup.add(n)                              # a team can't play twice in a night: ambiguous
            by_name[n] = (f, s)
    for n in dup:
        del by_name[n]
    if not by_name:
        return []
    idx = _alias_index([{"team": n, "sport": "nba", "opponent": ""} for n in by_name])
    # Recaps name teams by city ("Portland pulled ahead ..."), and "Blazers"
    # alone is the Trail Blazers; not a city two teams share.
    for name in by_name:
        toks = _tokens(name)
        k = 2 if " ".join(toks[-2:]) in _NBA_NICKS2 else 1
        city = toks[:-k]
        if city and " ".join(city) not in _NBA_SHARED_CITIES:
            idx.setdefault(tuple(city), set()).add(name)
        if k == 2:
            idx.setdefault((toks[-1],), set()).add(name)

    def games_of(text):
        return {by_name[n][0]["game_id"]: by_name[n][0] for n in _named(text, idx) if n in by_name}

    section_games = games_of(own_text)
    flags = []
    for sentence in _sentences(own_text):
        if _NBA_ELSEWHERE_RE.search(sentence):
            continue                                    # another game, another year
        named = [n for n in _named(sentence, idx) if n in by_name]
        cands = games_of(sentence) or section_games
        if len(cands) != 1:
            continue
        f = next(iter(cands.values()))
        sides = {by_name[n][1] for n in named if by_name[n][0] is f}
        side = next(iter(sides)) if len(sides) == 1 else None
        results = _nba_claims(sentence, f, side)
        if not results:
            continue
        quoted = _quote(sentence)
        bad = [(said, fact) for v, said, fact in results if v == "contradict"]
        figs = [r"(?<![\d-])" + re.escape(m.group(0)) + r"(?![\d-])" for m in _NBA_PAIR_RE.finditer(sentence)]
        figs += [r"\b" + re.escape(m.group(0)) for m in _NBA_RUN_RE.finditer(sentence)]
        figs += [r"\b" + re.escape(m.group("n")) + r"\s+(?:straight|consecutive|unanswered)"
                 for m in _NBA_STRAIGHT_RE.finditer(sentence)]
        fig = "|".join(figs) or r"(?!x)x"
        if not bad:
            if outcomes is not None:
                outcomes.append(("confirmed", quoted, sentence, "game flow", fig))
            continue
        t = f["teams"]
        why = "; ".join(f"{s} -> but {x}" for s, x in bad).replace('"', "'").replace("--", "-")
        flags.append(
            f'\n<!-- FACT FLAG [HIGH]: "{quoted}" contradicts the play-by-play of {t["away"]["name"]} @ '
            f'{t["home"]["name"]}: {why} (ESPN; the NBA GAME FLOW block has the full sequence). Correct it '
            f'to the block, or cut it. -->')
        if outcomes is not None:
            outcomes.append(("HIGH", quoted, sentence, "game flow", fig))
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

    # ── CHECK 3E: NHL game-flow claims vs the goal log (SLA-148) ─────────────
    flags.extend(check_nhl_flow_claims(own_text(section_html), game_state))

    # ── CHECK 3F: college basketball game-flow claims vs the play log (SLA-146)
    flags.extend(check_ncaamb_flow_claims(own_text(section_html), game_state))

    # ── CHECK 3G: NBA game-flow claims vs the play log (SLA-147) ─────────────
    flags.extend(check_nba_flow_claims(own_text(section_html), game_state))

    # ── CHECK 3C: Football rank and record claims (SLA-117) ──────────────────
    flags.extend(check_football_claims(own_text(section_html), game_state))

    # ── CHECK 3D: MLB game-flow claims vs the line score (SLA-142) ───────────
    flags.extend(check_mlb_flow_claims(own_text(section_html), game_state))

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
