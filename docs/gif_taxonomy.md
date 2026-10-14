# GIF subcategory taxonomy (SLA-132)

**Status:** 39 situations; targets rebased on what actually ships (SLA-133, 2026-10-14). Nothing
reads it yet. The machine-readable copy is `prompts/gif_taxonomy.DRAFT.json`. SLA-132's three open
decisions went with the recommendations (35+ situations rather than 10–15 per mood; `relief` and
`anticipation` added; writer demand recovered, below). Follow-ups: SLA-133 (vision tagger),
SLA-137 (selection in UAT), SLA-134 (pool to ~500), SLA-135, SLA-136.

## Update (SLA-133): what the writer actually uses

Pass 1's requests turned out to be the wrong demand signal. All **345 library GIFs that shipped**
over the same 43 days were each classified by the paragraph directly above them, which is what
the GIF was reacting to. That is real consumption, and it now drives every target.

- **The taxonomy holds up.** 279 of 345 placements (81%) fit an existing situation, and 34 fit a
  mood but no situation. Moods are tagged as well, so those still get matched.
- **Four situations recur often enough to add.** 21 placements:
  - `buyers_remorse` (7): a bad contract, a lopsided trade, a wasted pick.
  - `long_drought` (5): decades without a title, still going.
  - `dud_performance` (5): a star or lineup that showed up empty.
  - `controversial_call` (4): a disputed call decides it.

  Ten more candidates appeared 1–3 times each and stay out until they recur.
- **Real demand is far less lopsided than Pass 1's.** The writer's top situations:
  - `calm_dominance` 23
  - `vindication_doubters_silenced` 19 (Pass 1 asked for it 34 times)
  - `big_game_buildup` 17
  - `saw_it_coming`, `cruel_twist`, `season_slipping_away` 16 each
  - `what_just_happened` 15

  `season_over`, Pass 1's second favourite at 17 requests, shipped only 6 times. Nothing takes
  more than 7% of placements.
- **Targets now size the SLA-134 pool.** `freshness_target = placement share × 500` (about two
  months between repeats). The old cooldown floor stays as `cooldown_floor`.

## What was measured

- **Demand:** all 155 `gif_concept` values Pass 1 wrote in `archive/*/story_plan.json`,
  2026-09-02 → 2026-10-14 (43 issues). 152 classified by hand. 3 left out: 2 empty and 1 Tier 3
  clip that only works as itself ("Tua in the 2018 title game").
- **Use:** every Giphy ID in the shipped `newsletter_substack.html` over the same 43 days:
  352 GIFs, 345 from the library, 7 from live search.
- **Supply:** the 175 verified library entries, assigned to subcategories **by label only**.
  The `supply_ids_by_label` column is a judgment from text, not from looking at the GIF.
  SLA-133 replaces it with real tags.

## Findings

1. **The situation is thrown away today.** Pass 1 writes a situation ("quiet resignation after
   the error costs you the game"). Pass 2 turns it into a bare `data-library-category`, and
   `gif_library_select.pick_entry()` then picks a **random** verified GIF from that mood. So a
   "told you so" request can ship the cocky strut, and a "season slipping away" request can ship
   the Michael Jordan crying face. The taxonomy only helps once selection reads the subcategory
   (SLA-137).

2. **Demand is very concentrated.** Two situations account for a third of all requests:
   - `vindication_doubters_silenced`: 34 (22%), about 5.5 a week. Only ~5 library GIFs fit it.
   - `season_over`: 17 (11%). ~10 fit, so this one is covered.

   Add `season_slipping_away`, `here_we_go_again` and `calm_dominance` and five situations make
   up about half of demand (73 of 152).

3. **The ticket's target of 10–15 subcategories per mood is not supported by the data.** 152
   requests spread across **35 situations**, and most moods hold 1–4. Padding each of 20 moods
   to 10–15 would mean 200–300 subcategories, almost all with no demand behind them. The draft
   keeps 35 and lets demand decide where depth goes. More can be added as requests show up.

4. **Three requested situations have no library mood at all:**
   - **relief**: dodged a disaster, a drought ended or a star came back, an emotional release. 9 requests.
   - **anticipation**: build-up before a big game, scoreboard-watching. 4 requests.
   - **shell-shocked loser**: the losing side of a rout. Filed under `domination` here.

   Today these land on the wrong mood (relief → celebratory or sadness) or fall through.

5. **Pass 1 seeds only 44% of the GIFs that ship.** 352 GIFs shipped against 155 seeds; the writer
   adds the rest with only a mood. `respect_awe` shipped 66 times from 11 GIFs, so each one
   repeated about every 6 days against a 7-day cooldown. That demand is real but unrecorded. It
   can be recovered from the paragraph above each GIF in the archived drafts. That was done in
   SLA-133; see the update above.

6. **Pass 1's wording is formulaic.** "Smug vindication after being doubted all summer" appears
   almost verbatim most days. Some of the concentration may be habit rather than real variety in
   stories. Worth watching once selection uses subcategories. Not changed here (out of scope).

7. **Moods with no Pass 1 demand still ship.** `promiscuous_flirty`, `debate_takes`,
   `anger_outrage`, `denial_copium`, `financial_pain` and `mocking_sarcastic_praise` got zero
   seeds, but the writer used most of them directly (`denial_copium` 16, `financial_pain` 10).
   Nothing should be retired on this data.

## Fill order (top of the list)

Ranked by `shortfall = freshness_target − supply_estimate`. Supply is still estimated from labels
until the SLA-133 tagger has run.

| # | Situation | Shipped | Pass 1 asked | Supply (by label) | Target | Short |
|---|---|---|---|---|---|---|
| 1 | calm_dominance | 23 | 8 | 3 | 34 | 31 |
| 2 | big_game_buildup *(anticipation)* | 17 | 4 | 0 | 24 | 24 |
| 3 | vindication_doubters_silenced | 19 | 34 | 5 | 28 | 23 |
| 4 | season_slipping_away | 16 | 7 | 0 | 23 | 23 |
| 5 | cruel_twist | 16 | 4 | 0 | 23 | 23 |
| 6 | saw_it_coming | 16 | 3 | 4 | 23 | 19 |
| 7 | comeback_euphoria | 12 | 3 | 0 | 18 | 18 |
| 8 | here_we_go_again | 13 | 7 | 3 | 19 | 16 |
| 9 | historic_company | 13 | 1 | 4 | 19 | 15 |
| 10 | drought_over_or_star_back *(relief)* | 10 | 4 | 0 | 14 | 14 |

The full list of 39 is in the JSON. Each situation carries every Pass 1 request and every shipped
placement classified into it.

## Entity layer

Each GIF carries optional `players`, `teams`, `figures` (associated public figures) and
`league`.

- **Source:** Giphy metadata (channel, uploader, title, slug) plus what's visible in the frames
  (jersey number and colours, logos, on-screen text). **Never face recognition.** If neither
  metadata nor a visible cue supports an entity tag, it isn't written.
- **Use:** a secondary filter inside the chosen subcategory. An entity match never justifies
  picking a GIF from the wrong situation.

## Accepted risk

Giphy's API Terms forbid creating "a database, directory, or index containing GIFs". The
library is already a list of Giphy IDs plus our labels; we link only and never copy, cache or
proxy media. This work makes that index richer. Accepted as low risk while SLAP is free, and
listed under the monetisation re-check in CLAUDE.md.
