# GIF subcategory taxonomy (SLA-132)

**Status:** draft for Abram's review. Nothing reads it yet. The machine-readable copy is
`prompts/gif_taxonomy.DRAFT.json`. Follow-ups: SLA-133 (vision tagging of the existing library,
selection in UAT), then SLA-134 (bulk expansion).

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
   (SLA-133 step 5).

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
   can be recovered from the paragraph above each GIF in the archived drafts, which is a cheap
   check worth adding to SLA-133.

6. **Pass 1's wording is formulaic.** "Smug vindication after being doubted all summer" appears
   almost verbatim most days. Some of the concentration may be habit rather than real variety in
   stories. Worth watching once selection uses subcategories. Not changed here (out of scope).

7. **Moods with no Pass 1 demand still ship.** `promiscuous_flirty`, `debate_takes`,
   `anger_outrage`, `denial_copium`, `financial_pain` and `mocking_sarcastic_praise` got zero
   seeds, but the writer used most of them directly (`denial_copium` 16, `financial_pain` 10).
   Nothing should be retired on this data.

## Fill order (top of the list)

`target_supply = max(3, 2 × weekly demand)`. With a 7-day cooldown a subcategory needs at least
a week's demand in stock, and double that so the pick isn't forced. Ranked by shortfall:

| # | Subcategory | Requests | Supply (by label) | Short |
|---|---|---|---|---|
| 1 | vindication_doubters_silenced | 34 | 5 | 7 |
| 2 | season_slipping_away | 7 | 0 | 3 |
| 3 | cruel_twist | 4 | 0 | 3 |
| 4 | relief: dodged_disaster *(new mood)* | 4 | 0 | 3 |
| 5 | relief: drought_over_or_star_back *(new mood)* | 4 | 0 | 3 |
| 6 | anticipation: big_game_buildup *(new mood)* | 4 | 0 | 3 |
| 7 | bad_news_accepted | 3 | 0 | 3 |
| 8 | comeback_euphoria | 3 | 0 | 3 |
| 9–11 | got_away_with_it, carrying_the_load, shell_shocked_loser | 2 each | 0 | 3 |

Closing every shortfall takes about 53 GIFs, and 7 of them are for the first row alone.
The full list of 35, with descriptions, use_when and every request classified into it, is in the JSON.

## Entity layer

Each GIF carries optional `players`, `teams`, `figures` (associated public figures) and
`league`.

- **Source:** Giphy metadata (channel, uploader, title, slug) plus what's visible in the frames
  (jersey number and colours, logos, on-screen text). **Never face recognition.** If neither
  metadata nor a visible cue supports an entity tag, it isn't written.
- **Use:** a secondary filter inside the chosen subcategory. An entity match never justifies
  picking a GIF from the wrong situation.

## For Abram to decide

- Approve the 35-subcategory shape over the ticket's 10–15 per mood (finding 3).
- Add `relief` and `anticipation` as real moods (finding 4).
- Whether SLA-133 should also recover the writer's unrecorded demand (finding 5).

## Accepted risk

Giphy's API Terms forbid creating "a database, directory, or index containing GIFs". The
library is already a list of Giphy IDs plus our labels; we link only and never copy, cache or
proxy media. This work makes that index richer. Accepted as low risk while SLAP is free, and
listed under the monetisation re-check in CLAUDE.md.
