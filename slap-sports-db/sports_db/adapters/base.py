"""The contract every source adapter implements. No adapter is written yet.

One adapter = one (source, league) pair: `nflverse` for `nfl`, `espn` for
`nfl`, `retrosheet` for `mlb`. Adapters are the ONLY code that knows a
source's shape; everything past them speaks the schema's language (our
surrogate IDs, our stat keys, our season_type values). That is what lets a
new sport arrive as a new adapter plus reference rows, with no migration.

What an adapter must do, and why each point is here:

1. **Resolve identity through the xref tables, never by name.** Look up
   team_xref / player_xref / game_xref for (source_id, external_id); create
   the row and the xref together when it is new. Matching "Rams" by
   nickname is how LA/STL/LAR end up as three franchises.
2. **Stamp provenance.** Every fact row gets source_id, source_ref (the
   asset or endpoint it came from) and ingest_run_id.
3. **Declare what it expects before it writes.** Open an ingest_run with
   rows_expected when the source says how many there are. The database then
   refuses to record a short load as `succeeded` (constraint
   `succeeded_means_complete`); the adapter must record `partial` instead.
4. **Treat a missing upstream asset as a failure, not an empty season.** A
   404 on `stats_player_week_2025.parquet` is an alert (SLA-64), not zero
   rows.
5. **Map stats onto registered keys.** Write only keys present in
   stat_definition for the sport; `v_unknown_stat_keys` must be empty after
   every load, and the loader checks it rather than assuming its mapping.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Literal

Lane = Literal["backfill", "delta", "curated"]

# The fact tables an adapter may populate, in dependency order. A loader
# writes them in this order so every foreign key already exists.
FACT_TABLES = (
    "team_season",
    "roster_entry",
    "postseason_round",
    "postseason_series",
    "game",
    "game_period_score",
    "standing",
    "champion",
    "player_season_stat",
    "team_season_stat",
)


@dataclass(frozen=True)
class LoadResult:
    """What one adapter run reports back, mirrored into ingest_run."""
    table: str
    rows_expected: int | None
    rows_written: int
    missing_assets: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if self.missing_assets:
            return "partial" if self.rows_written else "failed"
        if self.rows_expected is not None and self.rows_written != self.rows_expected:
            return "partial" if self.rows_written else "failed"
        return "succeeded"


class SourceAdapter(ABC):
    """Base class for a (source, league) adapter."""

    source_id: str      # must exist in `source`
    league_id: str      # must exist in `league`
    lane: Lane

    @abstractmethod
    def seasons_available(self) -> range:
        """The season years this source can supply for this league,
        MEASURED against the source (probe the oldest asset), not read from
        its README. SLA-58 §4 question 2."""

    @abstractmethod
    def load(self, conn, seasons: Iterable[int], ingest_run_id: int) -> list[LoadResult]:
        """Write the given seasons into the schema, one LoadResult per fact
        table touched. Must be idempotent: re-running a season upserts."""
