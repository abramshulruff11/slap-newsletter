# slap-sports-db

The shared sports database behind **SLAP Newsletter** and **SLAP Fantasy**:
results, standings, playoffs and champions, rosters and season stats, in one
Postgres schema that both products (and anything later) read from.

> **Staging note.** This folder is being staged inside `slap-newsletter` on
> branch `claude/sla-5-execution-grikyj` until the standalone
> `slap-sports-db` repository exists. Everything here is self-contained
> (its own requirements, tests and CI workflow) and moves over as-is; the
> workflow only activates once it sits at a repository root.

## Status

| | |
|---|---|
| Schema | ✅ v1 (SLA-5): teams, franchises, rosters, games, standings, postseason, champions, season stats |
| Reference data | ✅ sports, the six launch leagues + AFL, all SLA-58 sources with licences |
| Adapters | Contract and plan only (`sports_db/adapters/`). None implemented |
| Data | **None loaded.** NFL populates first: SLA-6 (results/playoffs/champions), SLA-59 (season stats), SLA-60 (rosters), then SLA-7 (the daily feed) |

## Why it exists

The newsletter writes lines like "their first title since 1995" and "7-0 for
the first time since…". Today the only fact-check on those is Pass 3's
cross-check against *today's* ESPN scoreboard, and RULE 3 downgrades
anything historical to vague framing because nothing can source it. This
database is the source.

## How it's built

- **Postgres 16**, plain SQL migrations, no ORM.
- **Two lanes** (SLA-58 §5.2): *backfill* from static files (nflverse,
  Retrosheet, Lahman) that survive a hostile network, and *delta* from
  live APIs (ESPN, NHL, CFBD, CBBD) that are proxy-aware from day one.
- **One adapter per (source, league)** (`sports_db/adapters/registry.py`
  holds the plan), all writing into one source-neutral schema.

The design and its reasoning are in **[docs/schema.md](docs/schema.md)**.
Start there before changing a table.

## Launch scope

| Tier | Leagues | Primary sources |
|---|---|---|
| A (launch) | NFL, MLB, NHL, NCAAF, NCAAMB | nflverse + ESPN · Retrosheet + Lahman + MLB Stats API · NHL API · CFBD · CBBD |
| B (fast follow) | NBA | hoopR, then nba_api via proxy |
| Out | Tennis, golf | Not team sports (SLA-58 §5.4) |

## Running it

```bash
pip install -r requirements.txt

# Apply migrations (idempotent; refuses an edited, already-applied file)
DATABASE_URL=postgresql://user:pass@host:5432/db python -m sports_db.migrate
python -m sports_db.migrate --status

# Tests: need a Postgres they may CREATE DATABASE on. Each run builds and
# drops its own throwaway database. Without DATABASE_URL they SKIP, and CI
# treats any skip as a failure.
DATABASE_URL=postgresql://postgres@localhost:5432/postgres python -m pytest
```

## Licensing

Every source's licence and required attribution line is in the `source`
table. SLAP is currently free (no paid tier, ads or sponsorship), and every
launch source is fine on that basis. **If that changes, re-check Lahman
(CC BY-SA, which binds if this database is ever distributed) and the MLB
Stats API (non-commercial, non-bulk)**, per slap-newsletter's CLAUDE.md.
Never load Sports-Reference data: its terms forbid exactly this use.
