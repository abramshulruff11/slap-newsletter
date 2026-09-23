"""Which adapter serves which league, per SLA-58's recommendation.

This is the plan as data, so a test can hold it against the seeded `source`
and `league` rows: a planned adapter naming a source nobody registered is
caught here, not on the first load. Nothing below is implemented yet; the
`ticket` column says where each one gets built.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlannedAdapter:
    league_id: str
    source_id: str
    lane: str            # the role it plays for THIS league
    covers: str          # what it supplies
    earliest: int | None # measured earliest season, where SLA-58 measured one
    ticket: str
    tier: str            # 'A' launch, 'B' fast follow


PLANNED: tuple[PlannedAdapter, ...] = (
    # NFL populates first.
    PlannedAdapter("nfl", "nflverse", "backfill", "games, playoffs, season stats, rosters", 1999, "SLA-6 / SLA-59 / SLA-60", "A"),
    PlannedAdapter("nfl", "curated",  "curated",  "pre-1999 NFL champions and postseason results", 1920, "SLA-6 (6b) / SLA-61", "A"),
    PlannedAdapter("afl", "curated",  "curated",  "AFL champions 1960-69", 1960, "SLA-6 (6b) / SLA-61", "A"),
    PlannedAdapter("nfl", "espn",     "delta",    "same-day scores and standings", None, "SLA-7", "A"),
    # The rest of Tier A.
    PlannedAdapter("mlb",    "retrosheet",    "backfill", "game results", 1871, "SLA-80", "A"),
    PlannedAdapter("mlb",    "lahman",        "backfill", "season stats, standings, postseason", 1871, "SLA-80", "A"),
    PlannedAdapter("mlb",    "mlb_stats_api", "delta",    "same-day scores (never bulk)", None, "SLA-7", "A"),
    PlannedAdapter("nhl",    "nhl_api",       "backfill", "results, standings, stats", 1917, "SLA-78", "A"),
    PlannedAdapter("nhl",    "nhl_api",       "delta",    "same-day scores", None, "SLA-7", "A"),
    PlannedAdapter("ncaaf",  "cfbd",          "backfill", "results, rankings, rosters, stats", 1869, "SLA-82", "A"),
    PlannedAdapter("ncaaf",  "cfbd",          "delta",    "same-day scores (shared quota)", None, "SLA-7", "A"),
    PlannedAdapter("ncaamb", "cbbd",          "backfill", "results, stats", 1949, "SLA-81", "A"),
    PlannedAdapter("ncaamb", "cbbd",          "delta",    "same-day scores (shared quota)", None, "SLA-7", "A"),
    # Tier B.
    PlannedAdapter("nba", "hoopr",   "backfill", "results, stats (no proxy needed)", 2002, "SLA-79", "B"),
    PlannedAdapter("nba", "nba_api", "delta",    "live and deeper history, via proxy", 1946, "SLA-79 / SLA-7", "B"),
)


def launch_leagues() -> set[str]:
    return {p.league_id for p in PLANNED if p.tier == "A"}
